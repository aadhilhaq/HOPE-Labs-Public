"""What has happened to a flow, as the jobs write it down.

A flow's record is a folder, `state/`, holding one small file per card, beside flow.json. Each is
written whole and moved into place, so a reader sees a card before a write or after it and never
half of one.

One file per card rather than one for the flow, and that is not tidiness. Several step jobs are
awake at once, and a single document meant every one of them read it, changed its own card and
wrote the whole thing back - so two cards finishing within a few seconds of each other lost one of
the two writes. It happened on the first real run: a card queued its tool, said so in its log, and
the record still showed it as waiting, because the card beside it wrote afterwards from a copy
taken before. Cards are now written one at a time and only by the jobs that have something to say
about them, and where two jobs do write the same card they write the same thing.

What does not change while a flow runs - where it came from, which Python, which account - stays
in flow.json, written once.
"""
from __future__ import annotations

import json
import os
import time

#: What a card can be. A card is "waiting" until everything arriving at it has finished.
WAITING, READY, QUEUED, RUNNING, DONE, FAILED, STOPPED = (
    "waiting", "ready", "queued", "running", "done", "failed", "stopped")

#: The states a flow does not come back from.
ENDED = (DONE, FAILED, STOPPED)


def _now():
    return int(time.time())


class State:
    """The record of one flow's run: a folder of small files, one for each card."""

    def __init__(self, folder, data=None):
        self.folder = str(folder)
        self.where = os.path.join(self.folder, "state")
        self.data = data if data is not None else {"version": 1, "cards": {}, "started": _now()}
        self._changed = set()                  # the cards this process has something to say about

    # ---- reading and writing ---------------------------------------------
    @classmethod
    def read(cls, folder):
        it = cls(folder)
        it.data = {"version": 1, "cards": {}, "started": _now()}
        try:
            names = sorted(os.listdir(it.where))
        except OSError:
            names = []
        for name in names:
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(it.where, name), encoding="utf-8") as fh:
                    it.data["cards"][name[:-5]] = json.load(fh)
            except (ValueError, OSError):
                # A half-written file should never exist, since every write is a rename, and one
                # card that cannot be read is not worth losing the rest of the flow to.
                it.data["cards"][name[:-5]] = {
                    "state": WAITING, "jobs": [], "rundir": "", "changed": _now(),
                    "note": "this card's record could not be read"}
        # Anything settled when the flow was queued and not changed since.
        try:
            with open(os.path.join(it.folder, "flow.json"), encoding="utf-8") as fh:
                it.data["settled"] = json.load(fh).get("settled") or {}
        except (ValueError, OSError):
            it.data["settled"] = {}
        it.data.update(it.data.get("settled") or {})
        return it

    def write(self):
        """Write the cards this process changed, each in one step, and nothing else.

        Writing only what this job has something to say about is the whole point: a job that
        rewrote every card would undo whatever another job wrote while it was working.
        """
        os.makedirs(self.where, exist_ok=True)
        for nid in sorted(self._changed):
            path = os.path.join(self.where, "%s.json" % nid)
            tmp = "%s.%d.tmp" % (path, os.getpid())
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data["cards"][nid], fh, indent=2, sort_keys=False)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        self._changed.clear()

    # ---- one card --------------------------------------------------------
    def card(self, nid):
        """One card's record. Read it freely; change it only through set(), which is what marks
        it to be written. Reaching in here and assigning is a change this process keeps and
        nothing else ever sees."""
        return self.data.setdefault("cards", {}).setdefault(
            nid, {"state": WAITING, "jobs": [], "rundir": "", "note": "", "changed": _now()})

    def set(self, nid, state=None, rundir=None, jobs=None, note=None, **more):
        """Record what became of a card. Only what is given is changed."""
        it = self.card(nid)
        self._changed.add(nid)
        if state is not None:
            it["state"] = state
        if rundir is not None:
            it["rundir"] = str(rundir)
        if jobs is not None:
            it["jobs"] = [str(j) for j in jobs]
        if note is not None:
            it["note"] = note
        it.update(more)
        it["changed"] = _now()
        return it

    def state_of(self, nid):
        return self.card(nid)["state"]

    def finished(self, nid):
        return self.state_of(nid) == DONE

    def ended(self):
        """True once nothing more will happen without somebody asking for it."""
        cards = self.data.get("cards", {})
        return bool(cards) and all(c["state"] in ENDED for c in cards.values())

    def summary(self, flow):
        """What the launcher shows: a line per card, in the order they run."""
        rows = []
        for node in flow.order():
            it = self.card(node.id)
            # `step` is the job that started this card, so the page can read that job's own log
            # rather than the newest file with the card's name on it - which, after a Carry on or
            # a wake-up that found nothing to do, is a log saying nothing at all.
            rows.append({"id": node.id, "card": node.card,
                         "name": node.kind.name if node.kind else node.card,
                         "state": it["state"], "jobs": it["jobs"], "rundir": it["rundir"],
                         "note": it["note"], "changed": it["changed"],
                         "step": str(it.get("step") or "")})
        done = sum(1 for r in rows if r["state"] == DONE)
        # A flow somebody stopped has not failed, and telling them it did would be wrong twice
        # over: it says something went wrong, and it hides the one that actually did when a flow
        # holds both.
        broken = [r for r in rows if r["state"] == FAILED]
        halted = [r for r in rows if r["state"] == STOPPED]
        # A card below one that failed or was stopped is not still to happen: its step job waits
        # on jobs that will never finish well, and a track that ended with nothing to carry
        # queues nothing after it at all. Counted as to come, they made a flow whose one track
        # had ended read as running, and then, with nothing in the queue, as stalled - beside a
        # banner saying, truthfully, that a card had stopped.
        cut = set()
        for node in flow.order():
            if self.card(node.id)["state"] in (WAITING, READY) and any(
                    self.card(up)["state"] in (FAILED, STOPPED) or up in cut
                    for up in flow.upstream(node.id)):
                cut.add(node.id)
        to_come = [r for r in rows
                   if r["state"] in (QUEUED, RUNNING, READY, WAITING) and r["id"] not in cut]
        if broken:
            state = FAILED
        elif done == len(rows):
            state = DONE
        elif halted and not to_come:
            state = STOPPED
        else:
            state = RUNNING
        first = (broken or halted or [{"note": ""}])[0]
        # Still running, or merely still unfinished? They are not the same, and the page used to
        # show one for the other: a flow whose next card never queued had cards at waiting, no
        # card running, and said "running" for ever. refresh() is the one that can tell, because
        # it is the one that reads the queue, and it leaves the answer here.
        stalled = bool(state == RUNNING and not self.data.get("live", True))
        return {"name": flow.name, "cards": rows, "done": done, "of": len(rows),
                "state": state, "note": first["note"], "stalled": stalled}


def with_state(folder):
    """The cards of the flow in this folder that already have a record, which is every card of a
    flow that has been queued. Empty for a folder that holds no flow, or one never queued.

    What the flow is read with when it is read out of its own folder (Flow.from_json's
    `started`): a card is never put in front of one of these, because nothing would queue it.
    """
    try:
        names = os.listdir(os.path.join(str(folder), "state"))
    except OSError:
        return set()
    return {name[:-5] for name in names if name.endswith(".json")}


def claim(folder, nid):
    """Take the right to run this card, once. True for the one caller that gets it.

    A card with two tracks arriving at it is woken by both of them, and on a good day both wake
    at the same moment. A directory is made in one step or not at all, on every file system the
    cluster has, so the one that makes it does the work and the other goes back to sleep.

    Anything other than "it is already there" is raised rather than answered. A claims folder that
    cannot be made at all - a full quota, a folder left read-only - is not another job holding the
    card, and answering as though it were left the card at ready with a log saying the opposite of
    what had happened, and the step job ending without a word about it anywhere. The caller marks
    the card instead, with the reason on it.
    """
    where = os.path.join(str(folder), "claims")
    try:
        os.makedirs(where, exist_ok=True)
        os.mkdir(os.path.join(where, nid))
    except FileExistsError:
        return False
    return True


def unclaim(folder, nid):
    """Give the claim back, so the card can be tried again after a failure."""
    try:
        os.rmdir(os.path.join(str(folder), "claims", nid))
    except OSError:
        pass
