"""Queueing a flow, and the few minutes of work that happen at each card.

A flow is submitted once and then runs itself. Submitting queues a step job for every card that
nothing arrives at. Each step job, when the cards before it have finished, turns what they left
into what this card needs, starts the card's tool, and queues the next cards' step jobs behind
the tool it just started. The chain unrolls as it goes, so a dependency is always on a job id
that already exists.

A card with two tracks arriving at it is woken by both. The first to arrive finds the other
unfinished and goes back to sleep; the last does the work. If both arrive at once, `claim()`
settles it. Waking a card twice is therefore ordinary rather than exceptional, and costs a
minute of a short queue.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

from . import adapters, cards, drivers, queue, state as st
from .flow import Flow

#: The checkout this runner was imported from, which is what a flow queued from here names as the
#: one its step jobs import (cli.HERE, the same folder).
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def folder_for(flow, runs_root):
    """Where this flow's own folder goes: its name under the root, never over an existing run."""
    root = os.path.abspath(os.path.expandvars(os.path.expanduser(str(runs_root))))
    where = os.path.join(root, flow.name)
    if os.path.exists(where):
        where = "%s_%s" % (where, time.strftime("%Y%m%d-%H%M%S"))
    return where


def write_out(flow, where, settled=None):
    """Lay the flow's folder out. Nothing of a card's own run is put here: the tools do that."""
    os.makedirs(where, exist_ok=True)
    for part in ("logs", "inputs", "claims", "state"):
        os.makedirs(os.path.join(where, part), exist_ok=True)
    doc = flow.as_json()
    if settled:
        doc["settled"] = dict(settled)
    with open(os.path.join(where, "flow.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    return where


def submit(flow, runs_root, python, package_root, account="", partition="", say=print):
    """Queue the flow. Returns (its folder, {card id: the step job watching it}).

    Only the cards nothing arrives at are queued here. The rest are queued by the cards before
    them, when there is something for them to wait on.
    """
    bad = flow.check()
    if bad:
        raise ValueError("this flow cannot be queued:\n  " + "\n  ".join(bad))
    if not queue.available():
        raise RuntimeError("there is no sbatch here: a flow is queued from a login node")

    where = folder_for(flow, runs_root)
    # What a step job needs and nobody changes afterwards goes in with the drawing, written once.
    # Keeping it out of the record is what lets the record be one small file per card, which is
    # what stops two step jobs writing over each other.
    settled = {"python": str(python), "package_root": str(package_root),
               "account": account, "partition": partition}
    write_out(flow, where, settled)
    record = st.State.read(where)
    for node in flow.nodes:
        record.set(node.id, state=st.WAITING)
    record.write()

    # The cards that cannot be started from a step job at all are started here, on the login
    # node, before anything is queued. What they have in common is the network: a compute node on
    # this cluster resolves names and then reaches nothing, so an RCSB id could not be fetched
    # from one and a folding run's alignments could not either. Doing it now also means a wrong
    # PDB id is refused while the person is still looking at the page, instead of quietly, in a
    # queue, several minutes later.
    #
    # Only the ones with nothing to wait for, which is the whole of what keeps a target card fed
    # by a fold out of this loop: its structure does not exist yet, so it takes its turn like any
    # other card, and what it does when its turn comes is copy a file.
    for node in flow.nodes:
        if node.card not in drivers.AT_LAUNCH or flow.into(node.id):
            continue
        try:
            rundir, jobs = drivers.submit(node, flow, {}, where, record, say=say)
        except Exception as why:                                # noqa: BLE001
            # The refusal reaches the person at the keyboard, which is the whole reason these are
            # started here - but the folder is written by now and will be listed with the rest, so
            # it has to say for itself why nothing in it ever started. Left unrecorded, a flow
            # refused half way through read as one whose cards were all still to come.
            record.set(node.id, state=st.FAILED, note=str(why)[:400])
            record.write()
            raise
        # A fold leaves a job behind it and the target card does not. One is queued and the other
        # is finished, and the cards after each wait accordingly.
        record.set(node.id, state=st.QUEUED if jobs else st.DONE, rundir=rundir, jobs=jobs,
                   note="")
        if jobs and not flow.out_of(node.id):
            _queue_seal(where, node.id, record, "afterok:" + ":".join(jobs), say)
    record.write()

    queued = _wake_the_front(where, flow, record)
    record.write()
    return where, queued


def _wake_the_front(where, flow, record):
    """Queue a step job for every card whose turn has already come. Returns {card id: job}.

    A card's step job is normally written by the card before it, at the end of that card's own
    turn, behind the tool it has just started. The cards at the front of a flow have nobody to do
    that for them, so they are queued here: the ones with nothing left to wait for, and the ones
    behind a card that was started on the login node and is still in the queue, which wait on the
    jobs it left.
    """
    queued = {}
    for node in flow.nodes:
        if record.state_of(node.id) != st.WAITING:
            continue
        waits = flow.upstream(node.id)
        # Started, not finished. A card behind a fold is queued now, waiting on the fold's job,
        # because the fold has no step job of its own that could do it later. A card behind
        # anything that has not been started yet is left alone: that card will queue this one.
        if not all(record.state_of(u) in (st.DONE, st.QUEUED, st.RUNNING) for u in waits):
            continue
        # Only what is still to finish. A card that has finished has nothing left for this one to
        # wait on, and Slurm forgets a job a few minutes after it ends: a dependency on one of
        # those is refused outright ("Job dependency problem"), so carrying on a flow whose cards
        # before the break had finished hours ago could never queue the card at the break.
        jobs = [str(j) for u in waits if record.state_of(u) != st.DONE
                for j in (record.card(u).get("jobs") or []) if str(j)]
        queued[node.id] = _queue_step(
            where, node.id, record,
            dependency=("afterok:" + ":".join(jobs)) if jobs else "")
    return queued


def _step_script(where, node_id, record):
    """A step job for this card, started the way the flow was settled to be started.

    A flow says in flow.json which Python and which checkout its step jobs use, and they are used
    for as long as it runs, so an update halfway through cannot change it. One that no longer
    says - a flow corrected before the correcting kept them has lost them - is carried on by the
    install doing the carrying on, which is what would have been written down had it been queued
    from here. The old fallback, "python3" with nothing on its path, is the system Python on a
    compute node, which has no runner in it: the step job stopped in its first second, and the
    flow with it.
    """
    return queue.step_script(where, node_id,
                             record.data.get("python") or sys.executable,
                             record.data.get("package_root") or HERE,
                             record.data.get("account") or "",
                             record.data.get("partition") or "")


def _queue_step(where, node_id, record, dependency=""):
    """Write and queue one card's step job."""
    script = os.path.join(where, "logs", "step-%s.sbatch" % node_id)
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(_step_script(where, node_id, record))
    os.chmod(script, 0o755)
    jid = queue.submit(script, where, dependency=dependency, name="flow-%s" % node_id)
    # Through set(), not by reaching into the card: only what set() is told about is written, so a
    # change made by hand would be kept in this process and lost everywhere else.
    was = record.state_of(node_id)
    record.set(node_id, step=jid, state=st.READY if was == st.WAITING else None,
               note="" if was == st.WAITING else None)
    return jid


def _queue_seal(where, node_id, record, after, say=print):
    """A job that reads the flow's own state once the last card's work has ended, and writes it.

    It runs `status`, which is the one thing that brings a record up to date from the queue. Not
    worth failing the card for if it cannot be queued: the record is then as it always was, true
    the moment anybody asks.
    """
    script = os.path.join(where, "logs", "seal-%s.sbatch" % node_id)
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(_step_script(where, node_id, record)
                 .replace("hope_flow.cli step", "hope_flow.cli status")
                 .replace(" %s\n" % _q(node_id), "\n")
                 .replace("--job-name=flow-%s" % node_id, "--job-name=flow-%s-seal" % node_id))
    os.chmod(script, 0o755)
    try:
        jid = queue.submit(script, where, dependency=after, name="flow-%s-seal" % node_id)
        # Written down so that anything waiting for the flow to be finished with can see it. A job
        # nobody records is a job nobody can wait for.
        record.set(node_id, seal=jid)
        record.write()
        say("%s will write down how it ended" % jid)
    except RuntimeError as why:
        say("the sealing job was refused (%s); the record is true whenever it is next read" % why)


def _q(text):
    import shlex
    return shlex.quote(str(text))


def lost_ports(flow, node_id, ended):
    """The input ports of a card that every edge into them comes from a card in `ended`.

    A port fed by two tracks, one of which ended with nothing, still has the other: the card runs
    on what arrived. Only a port with nothing left to come is lost, and a card with a lost port
    cannot run at all.
    """
    node = flow.node(node_id)
    out = []
    for port in (node.kind.inputs if node is not None and node.kind else []):
        sources = [e.src for e in flow.into(node_id) if e.dst_port == port.key]
        if sources and all(src in ended for src in sources):
            out.append(port.key)
    return out


def _ended_cards(flow, record):
    return {n.id for n in flow.nodes if record.state_of(n.id) in (st.FAILED, st.STOPPED)}


def _stop_below(flow, record, node_id, why):
    """Say so on the cards under one that has ended, and give back the ones marked.

    A card's state is brought up to date by the card after it, and the cards after this one are
    never going to run: this card's turn ended before it could queue their step jobs, so there is
    nothing in the queue for Slurm to cancel and nothing that will ever wake them. Left at
    waiting they read as still to come - a flow whose one track had ended said it was running,
    and the page, finding nothing of it in the queue, called it stalled - and the person was left
    to work out from one note on one card why nothing was happening.

    It is the rule step() already applies when it is woken and finds the card before it did not
    finish. This applies it where nothing is coming to wake them.
    """
    marked, left = [], [e.dst for e in flow.out_of(node_id)]
    seen = set()
    while left:
        nid = left.pop(0)
        if nid in seen:
            continue
        seen.add(nid)
        # A card already running has its own work in the queue and its own ending to come, and a
        # card that has ended has already said how. Neither is this card's to speak for.
        if record.state_of(nid) in st.ENDED or record.state_of(nid) in (st.QUEUED, st.RUNNING):
            continue
        # A card another track still feeds runs on what that track brings, and it is that
        # track's turn that will wake it.
        if not lost_ports(flow, nid, _ended_cards(flow, record)):
            continue
        record.set(nid, state=st.STOPPED, note=why)
        marked.append(nid)
        left.extend(e.dst for e in flow.out_of(nid))
    return marked


def _unstop_below(flow, record, node_id):
    """Give back the cards stopped because this one did not finish, now that it has started.

    A card that failed stopped the cards under it; tried again and started, it would otherwise
    leave them stopped, and a step job that woke one would find it stopped and do nothing.
    """
    said = "%s did not finish" % node_id
    left, seen = [e.dst for e in flow.out_of(node_id)], set()
    while left:
        nid = left.pop(0)
        if nid in seen:
            continue
        seen.add(nid)
        card = record.card(nid)
        if card.get("state") == st.STOPPED and card.get("note") == said:
            record.set(nid, state=st.WAITING, note="")
            left.extend(e.dst for e in flow.out_of(nid))


def refresh(flow, record):
    """Bring every card's state up to date from Slurm. The only thing that reads the queue.

    It also records whether anything of this flow is still on its way, which is not the same
    question as whether every card has ended. A card can sit at waiting with nothing behind it:
    its step job ran and did not advance it, or never queued at all because sbatch answered
    nothing. The flow then has no card running and no card finished, and reads as though it were
    still going, for ever. `live` is what says otherwise.

    Every job of every card is asked about together, in one pair of calls. A reading of a flow is
    what the page asks for every twenty seconds while anything is left to happen, and card by card
    that was a pair of calls each, on a login node, for as long as the flow ran.
    """
    changed = False
    live = False
    want = []
    for node in flow.nodes:
        card = record.card(node.id)
        if card["state"] in st.ENDED:
            continue
        if card.get("step"):
            want.append(str(card["step"]))
        want.extend(str(j) for j in (card.get("jobs") or []))
    seen = queue.states(want)

    for node in flow.nodes:
        card = record.card(node.id)
        if card["state"] in (st.QUEUED, st.RUNNING):
            live = True
        # A step job still in the queue is the flow's next move, whether or not its card has one
        # of its own yet. One that has ended without moving its card is the stalled case.
        step = str(card.get("step") or "")
        if step and card["state"] not in st.ENDED:
            done, _bad = queue.ended(seen, [step])
            if not done:
                live = True
        if card["state"] in st.ENDED or not card.get("jobs"):
            continue
        finished, bad = queue.ended(seen, card["jobs"])
        if bad:
            record.set(node.id, state=st.FAILED,
                       note="%s ended as %s" % (bad[0][0], bad[0][1].lower()))
            changed = True
        elif finished:
            record.set(node.id, state=st.DONE, note="")
            changed = True
        else:
            # Queued until one of its jobs has a node. Every card used to be called running from
            # the first time it was read, so a job that waited a day for a GPU was "running for a
            # day", with the page offering its log as it goes over a job that had not begun.
            # Running once anything of it has started, and staying so while the tool's later
            # jobs wait their turn: the card is under way. Never back from running to queued,
            # either: Slurm answering nothing for a moment looks exactly like a job that has not
            # started, and a card that has started has.
            now = st.RUNNING if queue.started(card["jobs"], seen) else st.QUEUED
            if card["state"] != now and card["state"] != st.RUNNING:
                record.set(node.id, state=now)
                changed = True
        live = True
    if record.data.get("live") != live:
        record.data["live"] = live
        changed = True
    return changed


def step(where, node_id, say=print):
    """One card's turn: wait, adapt, submit, and wake the cards after it.

    Returns a short word saying what it did, which is what the job's log is for.
    """
    where = os.path.abspath(str(where))
    flow = _flow_in(where)
    record = st.State.read(where)
    node = flow.node(node_id)
    if node is None:
        say("there is no card called %s in this flow" % node_id)
        return "unknown"

    refresh(flow, record)

    # This card has already had its turn. A step job can run twice: Slurm requeues one when a
    # node fails under it, and a card with two tracks arriving is woken by both. Starting the
    # tool again would be a second run of the same work, charged a second time, so the state
    # this card is already in is the first thing read.
    already = record.state_of(node_id)
    if already in (st.QUEUED, st.RUNNING, st.DONE):
        say("this card is already %s" % already)
        return already
    if already == st.STOPPED:
        say("this card was stopped")
        return "stopped"
    if already == st.FAILED:
        # Woken again after it failed - by a second track, or a step job Slurm requeued - a card
        # is tried once more by itself, for the failures that were the node's rather than the
        # card's. Once: a card that fails twice waits for Carry on, so nothing is charged a third
        # time without somebody asking.
        tries = int(record.card(node_id).get("retried") or 0)
        if tries >= 1:
            say("this card failed again after one more try; Carry on starts it again")
            return "failed"
        # The one more try is itself claimed, once, so two wake-ups at the same moment cannot
        # both take it. The card's own claim is then given back: a card whose tool failed in the
        # queue still holds the claim its first turn took.
        if not st.claim(where, node_id + ".retry"):
            say("another job is already trying this card again")
            return "claimed"
        st.unclaim(where, node_id)
        record.set(node_id, retried=tries + 1)
        say("this card failed before; trying it once more")

    # Something before this card went wrong. Where a port still has another track coming, the
    # card runs on what that track brings; where a port has nothing left to come, there is
    # nothing to carry forward. Slurm will have cancelled this job's own dependency in most
    # cases; this is the case where it did not.
    ended = {u for u in flow.upstream(node_id) if record.state_of(u) in (st.FAILED, st.STOPPED)}
    if lost_ports(flow, node_id, ended):
        up = sorted(u for u in ended
                    if any(e.src == u for e in flow.into(node_id)))[0]
        record.set(node_id, state=st.STOPPED, note="%s did not finish" % up)
        _stop_below(flow, record, node_id, "%s did not finish" % up)
        record.write()
        say("%s did not finish, so nothing is queued here" % up)
        return "stopped"
    waiting = [u for u in flow.upstream(node_id) if not record.finished(u) and u not in ended]
    if waiting:
        record.write()
        say("still waiting for %s" % ", ".join(waiting))
        return "waiting"

    try:
        mine = st.claim(where, node_id)
    except OSError as why:
        # The flow's own folder is not writable - a quota filled overnight is how this happens -
        # so neither the claim nor anything after it can be written. Said on the card, because
        # the one place a person looks is the card.
        record.set(node_id, state=st.FAILED, note="this card could not be claimed: %s" % why)
        _stop_below(flow, record, node_id, "%s did not finish" % node_id)
        record.write()
        say("this card could not be claimed: %s" % why)
        raise
    if not mine:
        say("another job is already starting this card")
        return "claimed"

    try:
        carried = {}
        for edge in flow.into(node_id):
            if edge.src in ended:
                continue                                # a track that ended brings nothing
            port = flow.node(edge.src)
            got = adapters.carry(flow, record, edge, where)
            carried.setdefault(edge.dst_port, []).append(got)
            say("from %s: %s" % (port.kind.name if port.kind else edge.src, got.get("what", "")))

        empty = [p for p, got in carried.items()
                 if all(not g.get("count", 1) for g in got)]
        if empty:
            # Nothing came out of the track before this one. That is a result, not a fault: a
            # design run can pass nothing, and the flow should say so rather than queue a card
            # with an empty input and let the tool refuse it a minute later.
            record.set(node_id, state=st.STOPPED,
                       note="nothing arrived at %s" % ", ".join(sorted(empty)))
            under = _stop_below(flow, record, node_id, "nothing came out of %s" % node_id)
            record.write()
            say("nothing arrived: this track ends here%s"
                % (", and at %s" % ", ".join(under) if under else ""))
            return "empty"

        rundir, jobs = drivers.submit(node, flow, carried, where, record, say=say)
        # A card that queued nothing has already done the whole of its work, here, in this job: a
        # target card fed by a fold copies one file and is finished. Left as "queued" it would be
        # waiting for jobs that do not exist, and every card after it would wait for it for ever.
        partly = ("ran on what arrived; %s ended with nothing to pass on"
                  % ", ".join(sorted(ended))) if ended else ""
        record.set(node_id, state=st.QUEUED if jobs else st.DONE, rundir=rundir, jobs=jobs,
                   note=partly)
        _unstop_below(flow, record, node_id)
        record.write()
        say("%s %s" % (node.kind.name,
                       ("queued as " + ", ".join(jobs)) if jobs else "finished here")
            + (" (%s)" % partly if partly else ""))
    except Exception as why:                                    # noqa: BLE001
        record.set(node_id, state=st.FAILED, note=str(why)[:400])
        # The cards under it would have been queued at the end of this turn, behind the tool this
        # card never started, so there is nothing of them in the queue and nothing to cancel.
        _stop_below(flow, record, node_id, "%s did not finish" % node_id)
        record.write()
        st.unclaim(where, node_id)
        say("this card failed: %s" % why)
        raise

    after = "afterok:" + ":".join(jobs) if jobs else ""
    # Nothing comes after this card, so nothing will ever look at it again and mark it finished:
    # a card's state is brought up to date by the card after it. Left alone, a flow that ended
    # while nobody was watching reads as still queued for ever. One small job behind its tool
    # settles it, which is what somebody opening the launcher a week later will read.
    if jobs and not flow.out_of(node_id):
        finishing = [str(j) for j in (record.card(node_id).get("finishing") or []) if str(j)]
        _queue_seal(where, node_id, record,
                    after + (",afterany:" + ":".join(finishing) if finishing else ""), say)

    for edge in flow.out_of(node_id):
        if record.state_of(edge.dst) in (st.QUEUED, st.RUNNING, st.DONE):
            continue
        try:
            _queue_step(where, edge.dst, record, dependency=after)
            say("%s will wake when this finishes" % edge.dst)
        except RuntimeError as why:
            record.set(edge.dst, state=st.FAILED, note=str(why)[:400])
            _stop_below(flow, record, edge.dst, "%s did not finish" % edge.dst)
            say("could not queue %s: %s" % (edge.dst, why))
    record.write()
    return "queued"


def plan(flow, runs_root, account="", partition=""):
    """What this flow would do, worked out without doing any of it.

    A person pressing Launch is committing a cluster allocation, sometimes for days, and until now
    they were doing it blind. This says what will be queued, in what order, where it will land and
    what drives the cost, so the press is a decision rather than a leap.

    The numbers here are counts and arithmetic, not predictions. An hour figure would have to come
    from a built system, which does not exist until the flow has run; inventing one would be worse
    than giving none, so what is given is what is known: how many of what.
    """
    where = folder_for(flow, runs_root)
    rows, notes = [], []
    target = next((n for n in flow.nodes if n.card == "target"), None)
    settings = target.settings if target else {}

    for node in flow.order():
        card = node.kind
        waits = [flow.node(u).kind.name for u in flow.upstream(node.id)]
        row = {"id": node.id, "card": node.card, "name": card.name,
               "waits_for": waits, "queues": "", "lands_in": "", "scale": ""}
        if node.card == "target":
            # Two shapes, and the difference is the whole of when this card does its work. One
            # fetches its structure while Launch is being pressed; the other waits for a fold and
            # copies what it leaves. Somebody reading this before committing an allocation should
            # be told which.
            folds = [flow.node(e.src) for e in flow.into(node.id)]
            folds = [n for n in folds if n is not None and n.kind is not None]
            row["queues"] = (("nothing: it waits for %s and copies the structure it leaves"
                              % ", ".join(n.kind.name for n in folds)) if folds
                             else "nothing: fetched here, before anything is queued")
            row["lands_in"] = os.path.join(where, "inputs")
            row["scale"] = _target_line(settings, folds)
        elif node.card in (cards.PREPARE_KEY, cards.PREPARE_RECEPTOR_KEY):
            # Not a tool, so it has no runs folder of its own: what it leaves belongs to the flow,
            # beside the inputs the flow fetched.
            row["lands_in"] = os.path.join(where, "prepare", node.id)
            row["queues"], row["scale"] = _card_scale(node, settings)
        else:
            row["lands_in"] = os.path.join(
                os.path.abspath(os.path.expandvars(os.path.expanduser(
                    node.settings.get("runs") or drivers.RUNS.get(node.card, "")))),
                "%s_%s" % (flow.name, node.id))
            row["queues"], row["scale"] = _card_scale(node, settings)
        rows.append(row)
        notes.extend(_card_notes(node, settings, flow))

    return {"name": flow.name, "folder": where, "cards": rows, "notes": notes,
            "account": account, "partition": partition,
            "problems": flow.check()}


def _target_line(s, folds=()):
    source = (s.get("source") or "rcsb").strip()
    if source == "folded":
        where = "folded by " + (", ".join(n.kind.name for n in folds) or "a fold")
    elif source == "rcsb":
        where = "PDB " + (s.get("pdb_id") or "?")
    else:
        where = os.path.basename(s.get("path") or "?")
    return "%s, chains %s, hotspots %s" % (where, s.get("chains") or "all",
                                           s.get("hotspots") or "none")


def _card_scale(node, target_settings):
    """(what this card queues, the numbers that decide what it costs)."""
    s = node.settings
    if node.card in ("boltz2", "colabfold"):
        single = str(s.get("alignments") or "fetch") == "single"
        chains, residues = 0, 0
        for row in cards.fold_entities(node.card, s):
            copies = max(1, int(_int(row.get("copies"), 1)))
            chains += copies
            residues += copies * len(re.sub(r"\s+", "", str(row.get("sequence") or "")))
        samples = max(1, int(_int(s.get("diffusion_samples"), 1)))
        return (("one GPU job" if single else
                 "the alignments here, while Launch is pressed, then one GPU job"),
                "%d residue%s over %d chain%s%s, up to %s on %s"
                % (residues, "" if residues == 1 else "s", chains,
                   "" if chains == 1 else "s",
                   (", %d samples" % samples) if samples > 1 else "",
                   s.get("walltime") or "04:00:00", s.get("gpu") or "auto"))
    if node.card == "bindcraft":
        return ("one campaign on %s, up to %s" % (s.get("gpu") or "auto",
                                                  s.get("walltime") or "24:00:00"),
                "until %s designs pass, binders of %s-%s residues"
                % (s.get("designs") or "?", target_settings.get("binder_min"),
                   target_settings.get("binder_max")))
    if node.card == "adcp":
        return ("three jobs: prepare, dock as an array over the peptides, then gather",
                "%s replicas a peptide, %s poses kept%s"
                % (s.get("replicas") or 50, s.get("poses") or 10,
                   ", then MM-GBSA" if s.get("mmgbsa", True) else ""))
    if node.card == cards.PREPARE_RECEPTOR_KEY:
        return ("one short CPU job: the target's chains are prepared once, for every docking card "
                "it feeds, and the hotspots are checked against what comes out",
                "pH %s, %s, gaps up to %s residues built" % (
                    s.get("ph") or 7.4,
                    {"propka": "protonation by propka",
                     "pdbfixer": "protonation by PDBFixer's rules",
                     "none": "the hydrogens as they arrive"}.get(
                         str(s.get("protonation") or "propka"), "propka"),
                    s.get("model_loops_max") if s.get("model_loops_max") not in (None, "")
                    else 12))
    if node.card == "prepare":
        return ("one short CPU job: every molecule of everything that arrives is prepared on its "
                "own and the complexes are put back together",
                "pH %s, %s" % (s.get("ph") or 7.4,
                               {"propka": "protonation by propka",
                                "pdbfixer": "protonation by PDBFixer's rules",
                                "none": "the hydrogens as they arrive"}.get(
                                    str(s.get("protonation") or "propka"), "propka")))
    if node.card == "hopemd":
        runs = int(_int(s.get("top_n"), 10))
        reps = int(_int(s.get("replicates"), 3))
        ns = _int(s.get("length_ns"), 100)
        return ("up to %d run%s per track, each of about five jobs"
                % (runs, "" if runs == 1 else "s"),
                "%d x %d replicate%s x %g ns = %g ns a track, on %s"
                % (runs, reps, "" if reps == 1 else "s", ns, runs * reps * ns,
                   s.get("engine") or "amber"))
    return ("one job", "")


def _int(value, fallback):
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _card_notes(node, target_settings, flow):
    """What a person should know about this card before pressing Launch."""
    out = []
    if node.card == "hopemd":
        runs = int(_int(node.settings.get("top_n"), 10))
        reps = int(_int(node.settings.get("replicates"), 3))
        # The best of each track, not the best overall: two tracks arriving means twice the runs,
        # which is the single easiest way for a flow to cost twice what was intended.
        tracks = max(1, len(flow.into(node.id)))
        total = runs * tracks * reps * _int(node.settings.get("length_ns"), 100)
        if tracks > 1:
            out.append("%d tracks arrive at the simulation and the best %d of each is taken, so up "
                       "to %d runs rather than %d." % (tracks, runs, runs * tracks, runs))
        if total >= 3000:
            out.append("That is %g ns in all. A nanosecond of a middling system is minutes on one "
                       "card, so this is a long commitment: check the number before starting it."
                       % total)
    if node.card in ("boltz2", "colabfold") and \
            str(node.settings.get("alignments") or "fetch") != "single":
        # Worth saying before the press rather than after it: this is the one card whose work
        # starts while somebody is still waiting at the keyboard, because the alignment server is
        # the only thing in a flow that a compute node cannot reach.
        out.append("The alignments for %s are fetched while the flow is being queued, on the "
                   "login node. Launching will sit there for a minute or two a chain."
                   % node.kind.name)
    if node.card == "bindcraft":
        longest = _int(target_settings.get("binder_max"), 0)
        if longest and longest > cards.DOCKABLE_MAX_LENGTH and \
                any(e.dst_port == "sequences" for e in flow.out_of(node.id)):
            out.append("Designs of up to %g residues cannot be docked; that link is refused."
                       % longest)
    return out


def _step_pending(record, node_id):
    """The step job watching this card, if one is still in the queue."""
    jid = record.card(node_id).get("step")
    if not jid:
        return ""
    state = queue.states([jid]).get(str(jid), "")
    return str(jid) if state.startswith(queue.LIVE) else ""


def _flow_in(where):
    """The flow in this folder, or a sentence saying why there is not one.

    A path that is not a flow is the ordinary mistake - a run folder of one of the tools, or a
    folder that has been tidied away - and it deserves an answer rather than a traceback, because
    the answer reaches a person through the launcher's page.
    """
    path = os.path.join(str(where), "flow.json")
    try:
        with open(path, encoding="utf-8") as fh:
            # With the cards that already have a state, so a flow launched before a preparation
            # card existed is read as it was launched, and nothing is put in front of a card whose
            # step job is already queued behind the card before it.
            return Flow.from_json(fh.read(), started=st.with_state(where))
    except FileNotFoundError:
        raise ValueError("there is no flow in %s: it holds no flow.json" % where)
    except (ValueError, OSError) as why:
        raise ValueError("the flow in %s could not be read: %s" % (where, why))


def _meaning(node, key, value):
    """What a setting says, whatever it is spelt as, so a finished card is called changed only when
    what it was given has changed.

    A setting can arrive spelt three ways that all mean "not given" - missing, None and empty - and
    on a field whose default is empty, which is a field where empty means the tool's own, the flows
    saved before 6 October said the same thing with 0. A number is a number whether the page sent
    it as 10 or "10". Compared as written, a flow opened to be corrected was refused for changes
    nobody had made: the page had only respelt them.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    field = None
    if node.kind is not None:
        field = next((f for f in node.kind.settings if f.key == key), None)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value).strip()
    if field is not None and field.default is None and number == 0:
        return None
    return number


def edit(where, asked, say=print):
    """Replace the drawing of a flow that has stopped, so Carry on uses the corrected one.

    Carry on queues what has not finished, with the settings the flow was launched with. That is
    right for a flow that stopped because a node died or a queue was full, and useless for one
    that stopped because a number on a card was wrong: there was nothing to change and no way to
    change it. resume() reads flow.json out of the flow's own folder, so changing that file is
    all this has to do - and all it has to be careful about.

    Three things are refused, because each would make the record and the folders disagree:

    * anything still in the queue. A card that is running was given its settings when it was
      queued, and rewriting them now would leave a record saying one thing and a job doing
      another. Stop it first, or wait.
    * a card that has finished being changed, removed or rewired. Its run is on disk, was made
      from what it was given, and is not run again. Letting the drawing say otherwise would
      make the flow's own record a lie about what produced its results.
    * a card that has finished losing an input it had, or gaining one. It ran with what arrived
      along its links. One taken away leaves the drawing silent about where its results came
      from; one added runs a card - a fold, a day of design - for a card that is not run again.

    Everything else is allowed: the settings of any card that has not finished, and cards and
    links added or removed below the line that has run - including the links that leave a
    finished card, which are the inputs of cards that have not run, and the card that stopped
    itself.
    """
    where = os.path.abspath(str(where))
    was = _flow_in(where)
    record = st.State.read(where)
    refresh(was, record)
    # Read as the canvas reads it while correcting: nothing is put in front of a card that has
    # already run, whose links in are what it ran with and cannot change.
    ran = {n.id for n in was.nodes if record.state_of(n.id) == st.DONE}
    now = asked if isinstance(asked, Flow) else Flow.from_json(asked, started=ran)

    busy = [n.id for n in was.nodes if record.state_of(n.id) in (st.QUEUED, st.RUNNING)]
    if busy:
        raise ValueError("%s is still in the queue; stop the flow or wait for it before changing "
                         "what it was given" % ", ".join(sorted(busy)))

    finished = [n for n in was.nodes if record.state_of(n.id) == st.DONE]
    here = {n.id: n for n in now.nodes}
    for node in finished:
        got = here.get(node.id)
        if got is None:
            raise ValueError("%s has already run; its results are on disk and it cannot be taken "
                             "out of the flow" % node.id)
        if got.card != node.card:
            raise ValueError("%s has already run as a %s card and cannot become a %s one"
                             % (node.id, node.card, got.card))
        changed = sorted(k for k in set(got.settings) | set(node.settings)
                         if _meaning(node, k, got.settings.get(k))
                         != _meaning(node, k, node.settings.get(k)))
        if changed:
            raise ValueError("%s has already run, so %s cannot be changed now: what it produced "
                             "was made with what it was given, and it is not run again"
                             % (node.id, " and ".join(changed)))

    done_ids = {n.id for n in finished}
    def links(flow):
        return {(e.src, e.src_port, e.dst, e.dst_port) for e in flow.edges}
    # The links arriving at a finished card are what it ran with, so they are held both ways. The
    # links leaving one are not held: they are the inputs of cards that have not run, which are
    # theirs to change, and check() below says whether those cards are still fed. Holding them as
    # well meant the card that stopped could never be taken out of its flow, because the finished
    # card before it led to it - and the refusal blamed a link "to a card that has already run"
    # that led to one that had not.
    then, drawn = links(was), links(now)
    lost = sorted(e for e in then - drawn if e[2] in done_ids)
    if lost:
        raise ValueError("%s has already run, so the link into it from %s cannot be taken out: "
                         "it ran with what arrived along it" % (lost[0][2], lost[0][0]))
    gained = sorted(e for e in drawn - then if e[2] in done_ids)
    if gained:
        raise ValueError("%s has already run, so nothing new can be linked into it from %s: it is "
                         "not run again, and what arrived would go nowhere"
                         % (gained[0][2], gained[0][0]))

    # Judged as the canvas judges it while correcting: a problem on a card that has already run
    # is not a reason to refuse, because that card is not run again. Its settings are the ones it
    # ran with, and a rule added since - a bound on a number, say - should not stop a flow from
    # carrying on over work that is already on disk.
    bad = [p for p in now.check() if getattr(p, "node", "") not in done_ids]
    if bad:
        raise ValueError("that flow would not start: %s" % bad[0])

    # What the flow was settled with when it was queued - which Python its step jobs start, which
    # checkout they import, which account they are charged to - is not part of the drawing, and
    # the page never sees it, so it is carried over from the file being replaced. Written without
    # it, every step job Carry on queued began "PYTHONPATH='' exec python3": a corrected flow was
    # carried on and stopped again within the minute, and so was every Carry on after it. Moved
    # into place whole, as the record is, because a step job may read this file at any moment.
    doc = now.as_json()
    if record.data.get("settled"):
        doc["settled"] = dict(record.data["settled"])
    path = os.path.join(where, "flow.json")
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    say("the drawing in %s was replaced; Carry on queues what has not finished" % path)
    return {"folder": where, "changed": True,
            "untouched": sorted(done_ids),
            "note": "%d card(s) have already run and were left as they were" % len(done_ids)}


def resume(where, say=print):
    """Carry on a flow that stopped, from the first card that has not finished.

    A flow stops for reasons that have nothing to do with the flow: a node failed under a job, a
    queue was full, an allocation ran out overnight. What has finished is left alone - its runs
    are on disk and cost what they cost - and everything from the first unfinished card is queued
    again. A card that failed has its claim given back, so it is tried rather than skipped.
    """
    where = os.path.abspath(str(where))
    flow = _flow_in(where)
    record = st.State.read(where)
    refresh(flow, record)

    again = []
    for node in flow.nodes:
        state = record.state_of(node.id)
        if state == st.DONE:
            continue
        if state in (st.QUEUED, st.RUNNING):
            # Still in the queue. Starting it again would be a second run of the same work.
            say("%s is still %s; leaving it" % (node.id, state))
            continue
        # A card that has not had its turn yet may already have a step job waiting for the card
        # before it; pressing Carry on twice is an ordinary thing to do and should not leave two
        # behind. A card that failed or was stopped is different: its step job has already run, so
        # it needs a new one however the old one ended.
        if state in (st.WAITING, st.READY):
            waiting_already = _step_pending(record, node.id)
            if waiting_already:
                say("%s is already waiting as job %s" % (node.id, waiting_already))
                continue
        st.unclaim(where, node.id)
        st.unclaim(where, node.id + ".retry")       # and its one more try, for after this
        # The old attempt's jobs and folder go with it. Left on the card, the next status read
        # found those jobs ended - badly, which is why it was being carried on - and marked it
        # failed again before its new step job had even started: Carry on looked as though it had
        # done nothing, and the page stopped reading a flow that was about to move.
        record.set(node.id, state=st.WAITING, note="", jobs=[], rundir="", retried=0)
        again.append(node.id)
    if not again:
        record.write()
        say("nothing to carry on: every card has finished or is already queued")
        return where, {}

    # The cards that need the network are done here again, for the reason they were done here the
    # first time, and only where there is nothing left for them to wait for.
    for node in flow.nodes:
        if node.id not in again or node.card not in drivers.AT_LAUNCH or flow.into(node.id):
            continue
        rundir, jobs = drivers.submit(node, flow, {}, where, record, say=say)
        record.set(node.id, state=st.QUEUED if jobs else st.DONE, rundir=rundir, jobs=jobs,
                   note="")
        if jobs and not flow.out_of(node.id):
            _queue_seal(where, node.id, record, "afterok:" + ":".join(jobs), say)
    record.write()

    queued = _wake_the_front(where, flow, record)
    for nid, jid in queued.items():
        say("%s queued again as job %s" % (nid, jid))
    record.write()
    return where, queued
