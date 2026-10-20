"""The command line. The launcher calls these over the connection it already holds.

    hope-flow check   flow.json
    hope-flow submit  flow.json [--runs ROOT] [--account A] [--partition P]
    hope-flow status  FLOWDIR [--json]
    hope-flow step    FLOWDIR CARD        (what a step job runs; not for typing)
    hope-flow stop    FLOWDIR

Nothing here needs a token or a page: a flow is a file, and this queues it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__, report, run, state as st
from .flow import Flow

#: Where a person's flows go when they do not say. Personal scratch, never a home folder.
DEFAULT_RUNS = os.path.join(os.environ.get("SCRATCH", "/tmp"), "hope_flows")

#: The Python and the checkout a step job starts from. A flow queued from one install runs on
#: that install from start to end, so an update halfway through a flow cannot change it.
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path):
    """A flow file, read with the cards that already have a state when it is a flow's own folder:
    a running flow is read as it was launched (see run._flow_in)."""
    with open(path, encoding="utf-8") as fh:
        return Flow.from_json(fh.read(), started=st.with_state(os.path.dirname(
            os.path.abspath(path))))


def cmd_check(a):
    bad = _read(a.flow).check()
    for line in bad:
        print(line)
    print("this flow can be queued" if not bad else "%d problem%s" % (len(bad), "" if len(bad) == 1 else "s"))
    return 1 if bad else 0


def cmd_submit(a):
    flow = _read(a.flow)
    where, queued = run.submit(flow, a.runs or DEFAULT_RUNS, a.python or sys.executable,
                               a.package_root or HERE, a.account, a.partition,
                               say=lambda *m: print(" ", *m))
    print(where)
    for nid, jid in queued.items():
        print("  %s waits as job %s" % (nid, jid))
    return 0


def cmd_plan(a):
    """What the flow would do, before anything is queued."""
    flow = _read(a.flow)
    got = run.plan(flow, a.runs or DEFAULT_RUNS, a.account, a.partition)
    if a.json:
        print(json.dumps(got, indent=2))
        return 1 if got["problems"] else 0
    print("%s would run as:\n" % got["name"])
    for row in got["cards"]:
        after = (" after " + ", ".join(row["waits_for"])) if row["waits_for"] else ""
        print("  %s%s" % (row["name"], after))
        print("     queues  %s" % row["queues"])
        if row["scale"]:
            print("     scale   %s" % row["scale"])
        print("     lands   %s" % row["lands_in"])
    for note in got["notes"]:
        print("\n  note: %s" % note)
    if got["problems"]:
        print("\nthis flow cannot be queued yet:")
        for line in got["problems"]:
            print("  - %s" % line)
    return 1 if got["problems"] else 0


def cmd_catalogue(a):
    """The cards this install offers, as JSON. A launcher reads this so that a card added here
    reaches it without anybody downloading the launcher again."""
    from . import cards
    if a.write:
        print("written: %s" % cards.write_catalogue())
        return 0
    print(json.dumps(cards.catalogue(), indent=2 if not a.compact else None))
    return 0


def cmd_tools(a):
    """The tools this install offers, as JSON, so a launcher can list one added after it was built."""
    try:
        from hope_labs import tools
    except ImportError as why:
        print("the tools catalogue is not here: %s" % why, file=sys.stderr)
        return 2
    print(json.dumps(tools.catalogue(), indent=2 if not a.compact else None))
    return 0


def cmd_site(a):
    """What the launcher's bar says here, as JSON, so a launcher built before it can say the same."""
    try:
        from hope_labs import docs
    except ImportError as why:
        print("the launcher's own words are not here: %s" % why, file=sys.stderr)
        return 2
    print(json.dumps(docs.site(), indent=2 if not a.compact else None))
    return 0


def cmd_status(a):
    flow = _read(os.path.join(a.flowdir, "flow.json"))
    record = st.State.read(a.flowdir)
    run.refresh(flow, record)
    record.write()
    got = record.summary(flow)
    # A flow with nothing left to happen gets its page written here, because this is the command
    # the seal job runs at the end of every flow - so the page exists without anyone asking for
    # it. Never worth failing a status for: the record is the thing, and the page is a reading of
    # it that can be made again at any time.
    if got["state"] in ("done", "failed", "stopped"):
        try:
            report.write(a.flowdir)
        except Exception as why:                                    # noqa: BLE001
            print("  the flow's page could not be written: %s" % why, file=sys.stderr)
    if a.json:
        print(json.dumps(got, indent=2))
        return 0
    print("%s: %s, %d of %d cards done" % (got["name"], got["state"], got["done"], got["of"]))
    for row in got["cards"]:
        note = ("  " + row["note"]) if row["note"] else ""
        print("  %-10s %-9s %s%s" % (row["id"], row["state"], row["rundir"] or "", note))
    return 0


def cmd_report(a):
    """Write the flow's own page: the chain, what each card left, and where its files are."""
    print(report.write(a.flowdir))
    return 0


def cmd_step(a):
    return 0 if run.step(a.flowdir, a.card) not in ("unknown",) else 2


def cmd_resume(a):
    """Carry on a flow that stopped, leaving what finished alone."""
    where, queued = run.resume(a.flowdir, say=lambda *m: print(" ", *m))
    print(where)
    for nid, jid in queued.items():
        print("  %s waits as job %s" % (nid, jid))
    return 0


def cmd_edit(a):
    """Replace the drawing of a flow that has stopped, so Carry on uses the corrected one."""
    with open(a.flow, encoding="utf-8") as fh:
        asked = fh.read()
    got = run.edit(a.flowdir, asked, say=lambda *m: print(" ", *m))
    if a.json:
        print(json.dumps(got))
    else:
        print(got["note"])
    return 0


def cmd_stop(a):
    """Cancel what this flow still has in the queue. What has finished is left alone."""
    from . import queue
    import subprocess
    flow = _read(os.path.join(a.flowdir, "flow.json"))
    record = st.State.read(a.flowdir)
    ids = []
    for node in flow.nodes:
        card = record.card(node.id)
        ids.extend(str(j) for j in (card.get("jobs") or []))
        # The step job watching this card, and the sealing job behind the last one. The seal was
        # missed: it is queued on afterok of work that is about to be cancelled, so Slurm sees to
        # it in the end, but a flow somebody stopped should not leave a job of its own in the
        # queue to be noticed later and wondered about.
        for key in ("step", "seal"):
            if card.get(key):
                ids.append(str(card[key]))
        # The jobs writing each run's report and videos, which are held apart from the card's own.
        ids.extend(str(j) for j in (card.get("finishing") or []) if str(j))
    # Only the ids this flow holds. An array job is answered about by task as well, and cancelling
    # a task of a job that is already being cancelled would be counted as a second job stopped.
    seen = queue.states(ids)
    alive = [j for j in dict.fromkeys(ids) if seen.get(j, "").startswith(queue.LIVE)]
    if alive:
        subprocess.run(["scancel"] + alive, check=False, timeout=120)
    for node in flow.nodes:
        if record.state_of(node.id) not in st.ENDED:
            record.set(node.id, state=st.STOPPED, note="stopped by hand")
    record.write()
    print("stopped %d job%s" % (len(alive), "" if len(alive) == 1 else "s"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="hope-flow", description=__doc__.splitlines()[0])
    ap.add_argument("--version", action="version", version="HOPE-flow " + __version__)
    sub = ap.add_subparsers(dest="cmd")

    c = sub.add_parser("check", help="what is wrong with a flow, if anything")
    c.add_argument("flow")
    c.set_defaults(fn=cmd_check)

    s = sub.add_parser("submit", help="queue a flow")
    s.add_argument("flow")
    s.add_argument("--runs", default="", help="where the flow's folder goes (default %s)" % DEFAULT_RUNS)
    s.add_argument("--account", default="", help="the allocation to charge")
    s.add_argument("--partition", default="", help="the partition the step jobs run in")
    s.add_argument("--python", default="", help="the Python the step jobs use")
    s.add_argument("--package-root", default="", help="the HOPE-flow checkout they import from")
    s.set_defaults(fn=cmd_submit)

    n = sub.add_parser("plan", help="what a flow would do, queueing nothing")
    n.add_argument("flow")
    n.add_argument("--runs", default="")
    n.add_argument("--account", default="")
    n.add_argument("--partition", default="")
    n.add_argument("--json", action="store_true")
    n.set_defaults(fn=cmd_plan)

    b = sub.add_parser("site", help="what the launcher's bar says here, as JSON")
    b.add_argument("--compact", action="store_true")
    b.set_defaults(fn=cmd_site)

    w = sub.add_parser("tools", help="the tools this install offers, as JSON")
    w.add_argument("--compact", action="store_true")
    w.set_defaults(fn=cmd_tools)

    g = sub.add_parser("catalogue", help="the cards this install offers, as JSON")
    g.add_argument("--compact", action="store_true")
    g.add_argument("--write", action="store_true",
                   help="write it to hope_flow/catalogue.json, which the canvas page reads")
    g.set_defaults(fn=cmd_catalogue)

    t = sub.add_parser("status", help="how far a flow has got")
    t.add_argument("flowdir")
    t.add_argument("--json", action="store_true")
    t.set_defaults(fn=cmd_status)

    p = sub.add_parser("step", help="run one card (what a step job calls)")
    p.add_argument("flowdir")
    p.add_argument("card")
    p.set_defaults(fn=cmd_step)

    r = sub.add_parser("resume", help="carry on a flow that stopped")
    r.add_argument("flowdir")
    r.set_defaults(fn=cmd_resume)

    rp = sub.add_parser("report", help="write the flow's results on one page")
    rp.add_argument("flowdir")
    rp.set_defaults(fn=cmd_report)

    e = sub.add_parser("edit", help="replace the drawing of a flow that has stopped")
    e.add_argument("flowdir")
    e.add_argument("flow", help="the corrected flow.json")
    e.add_argument("--json", action="store_true")
    e.set_defaults(fn=cmd_edit)

    k = sub.add_parser("stop", help="cancel what is still queued")
    k.add_argument("flowdir")
    k.set_defaults(fn=cmd_stop)

    a = ap.parse_args(argv)
    if not getattr(a, "fn", None):
        ap.print_help()
        return 2
    try:
        return a.fn(a)
    except (ValueError, RuntimeError) as why:
        # These carry a sentence meant for a person, and they reach one: the launcher shows what
        # this prints. A traceback here would be shown instead, and says nothing anybody can act on.
        print(str(why), file=sys.stderr)
        return 2
    except (FileNotFoundError, PermissionError, NotADirectoryError, IsADirectoryError) as why:
        # A path that is not there, and one the person may not read, are the same kind of answer.
        # The second used to reach the launcher as the first lines of a traceback, which is all
        # the room it gives an answer. Anything else the system refuses is left to raise, since
        # where it happened is then the useful part.
        print("%s: %s" % (why.strerror or "could not be read", why.filename or ""),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
