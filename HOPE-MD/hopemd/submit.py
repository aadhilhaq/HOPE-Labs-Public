"""Queue a run from a spec file, with no page open.

    python -m hopemd.submit spec.json [--runs DIR] [--name NAME] [--dry-run]
    python -m hopemd.submit spec.json --selections picks.json      # one run per pose

The spec is what the Setup screen holds: `hopemd.spec.Spec` as JSON. The simplest one names a
source and an engine and leaves the rest to the defaults the page would have shown:

    {"system": {"source": {"kind": "rcsb", "pdb_id": "1BHL"}},
     "compute": {"engine": "amber"}}

This takes the same two steps the page takes - import the source into a staging folder, then
render the run and hand it to Slurm - by calling the same functions, so a run queued here and a
run queued from the page are the same run rather than two that resemble each other.

What it prints on success is JSON: the run folder and the job id of every job queued, so whatever
called it can wait on them. Nothing is printed to standard output but that, and a refusal goes to
standard error with a non-zero status, so this can be read by a program as well as by a person.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _merge_import(spec_dict, got):
    """Apply what the import detected to the spec, as the page does when a structure loads.

    The fields the person chose are put back afterwards: an import reports what it found in the
    file, and must not quietly overrule the pH, the preparation or the environment that were
    asked for.
    """
    system = spec_dict.setdefault("system", {})
    keep = {k: system[k] for k in ("prep", "ph", "source", "environment") if k in system}
    membrane = dict(system.get("membrane") or {})
    membrane.update((got["system"] or {}).get("membrane") or {})
    system.update(got["system"] or {})
    system.update(keep)
    if membrane:
        system["membrane"] = membrane
    return (got["system"] or {}).get("staging", "")


def run(spec_dict, runs="", name="", dry_run=False, selections=None, say=print):
    """Import, render and queue. Returns what api_render returned, with its job ids."""
    from . import server as sv

    if runs:
        sv.S.runs = Path(os.path.abspath(os.path.expandvars(os.path.expanduser(runs))))
        sv.S.runs.mkdir(parents=True, exist_ok=True)
    sv.S.dry_run = bool(dry_run)
    if name:
        spec_dict.setdefault("name", "")
        spec_dict["name"] = name

    system = spec_dict.setdefault("system", {})
    source = system.get("source") or {}
    if not source.get("kind"):
        raise ValueError("the spec names no source: system.source.kind is empty")

    if selections:
        # The batch imports each selection into a staging folder of its own and names each run
        # itself, so the one import this function would do would be thrown away. It reports every
        # selection, refusals among them, and queues the ones that passed.
        out = sv.api_submit_batch({"spec": spec_dict, "selections": list(selections)})
        if out.get("error"):
            raise ValueError(out["error"])
        for line in out.get("log", []):
            say(line)
        return out

    say("importing the %s source" % source["kind"])
    got = sv.api_import({"source": source,
                         "environment": system.get("environment"),
                         "membrane": system.get("membrane"),
                         "ph": system.get("ph"),
                         "chains": system.get("chains"),
                         "keep_waters": system.get("keep_waters"),
                         "keep_metals": system.get("keep_metals")})
    if got.get("error"):
        raise ValueError("the source could not be imported: %s" % got["error"])
    staging = _merge_import(spec_dict, got)
    if got.get("note"):
        say("the import says: %s" % got["note"])

    out = sv.api_render({"spec": spec_dict, "staging": staging}, submit=True)
    if out.get("error"):
        raise ValueError(out["error"])
    if out.get("errors"):
        raise ValueError("; ".join("%s: %s" % (e.get("field", "spec"), e.get("message", ""))
                                   for e in out["errors"]))
    return out


def _jobs_of(out):
    """{run folder: {job key: id}} for one run or a batch, so both print the same shape."""
    if "runs" in out:                                 # a batch: each selection queued its own run
        return {r["rundir"]: r.get("jobs", {})
                for r in out.get("runs", []) if r.get("rundir")}
    return {out.get("rundir", ""): (out.get("submitted") or {}).get("jobs", {})}


def _refused(out):
    """The selections of a batch that were refused, as {what was asked for: why}."""
    return {r.get("selection", "?"): r["error"]
            for r in out.get("runs", []) if r.get("error")}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m hopemd.submit",
                                 description=__doc__.splitlines()[0])
    ap.add_argument("spec", help="the run's spec, as JSON")
    ap.add_argument("--runs", default="", help="where run folders go (default: the usual root)")
    ap.add_argument("--name", default="", help="the run's name; the tool names it when empty")
    ap.add_argument("--selections", default="",
                    help='JSON: [{"peptide": "<sequence>", "pose": 1}, ...], one run queued for each')
    ap.add_argument("--dry-run", action="store_true",
                    help="write the run folder and the job scripts but queue nothing")
    ap.add_argument("--quiet", action="store_true", help="print only the JSON")
    a = ap.parse_args(argv)

    with open(a.spec, encoding="utf-8") as fh:
        spec_dict = json.load(fh)
    picks = None
    if a.selections:
        with open(a.selections, encoding="utf-8") as fh:
            picks = json.load(fh)

    say = (lambda *m: None) if a.quiet else (lambda *m: print(*m, file=sys.stderr))
    try:
        out = run(spec_dict, a.runs, a.name, a.dry_run, picks, say=say)
    except ValueError as why:
        print(str(why), file=sys.stderr)
        return 2
    queued, refused = _jobs_of(out), _refused(out)
    print(json.dumps({"runs": queued, "refused": refused, "dry_run": bool(a.dry_run)}, indent=2))
    # A batch where every selection was refused has queued nothing, and saying so with a status
    # of zero would have whatever called this wait for jobs that do not exist.
    return 0 if queued else 3


if __name__ == "__main__":
    sys.exit(main())
