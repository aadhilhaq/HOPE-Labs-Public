"""What an ensemble run has done so far, what it wrote, and what Slurm makes of its job.

By Aadhil Haq

A run folder is the one the page wrote: submission.json at the top, the alignment phase one
fetched in inputs/, the samples where the tool wrote them in out/, what reading them found in
analysis/, and job.sbatch with the Slurm log beside it.

Everything here that does not depend on this being an ensemble is the design page's, imported
rather than written again: reading a record, finding the runs under a folder, asking squeue and
sacct what became of a job, tailing a log, canceling and queueing again. What is here is the
part that is about an ensemble: the samples, the shapes reading them found, and the one sentence
somebody opens a finished run to read.

    python -m hope_labs.ensemble.runs                       the runs you can see, newest first
    python -m hope_labs.ensemble.runs <run folder>          one run in detail
"""
from __future__ import annotations

import glob
import os
import sys
import time

from . import job as jobmod
#: The generic half of reading a run folder, which every page of ours shares.
from ..pages.runs import (OVER, cancel, finished_state, find_runs, job_states, owner_of,
                          read_json, tail)

__all__ = ["OVER", "cancel", "finished_state", "find_runs", "job_states", "owner_of",
           "read_json", "tail", "runs_root", "samples", "summarise", "detail", "resubmit",
           "analysis", "ensemble_file"]

#: The columns of the shapes table, in the order they are shown.
SHOWN = ("rank", "share", "samples", "spread", "sample")


def runs_root(tool="bioemu"):
    return jobmod.RUNS[tool]


def job_script(run):
    return os.path.join(run, "job.sbatch")


def log_path(run):
    """The newest Slurm log. One job writes one, but a run queued again writes another beside
    it, and the newest is the one being watched."""
    logs = sorted(glob.glob(os.path.join(run, "slurm_*.out")), key=os.path.getmtime)
    return logs[-1] if logs else ""


def analysis(run):
    """What reading the samples found, or {} for a run that has not got there yet."""
    got = read_json(os.path.join(run, "analysis", "analysis.json"), {}) or {}
    if got and "rmsf" not in got:
        # The per residue numbers live beside the document rather than in it, because a thousand
        # samples is a thousand numbers three times over. The page draws them, so they are read
        # back here once rather than fetched as a second request.
        got["rmsf"] = _column(os.path.join(run, "analysis", "rmsf.dat"), 1)
    return got


def _column(path, which):
    """One column of a two-column data file, as a list of numbers."""
    out = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.split()
                if len(parts) > which:
                    out.append(float(parts[which]))
    except (OSError, ValueError):
        return []
    return out


def set_of(run):
    """The other molecules submitted with this one, or {}.

    A variant and its wild type are two runs, and the only thing that makes them a pair is that
    each was told about the other when they were queued (submit.queue). Without this a person
    would have to remember which folder the wild type went in, which is exactly the kind of thing
    nobody remembers a fortnight later.
    """
    return read_json(os.path.join(run, "set.json"), {}) or {}


def ensemble_file(run):
    """The one structure the viewer opens: a model per shape, most populated first."""
    path = os.path.join(run, "analysis", "ensemble.pdb")
    return path if os.path.isfile(path) else ""


def numbering(run, count, record=None):
    """One residue number per sampled residue, and where the numbers came from.

    The structure's own numbers when the run was given one and the chain it named is the chain
    that was sampled. BioEmu numbers its samples from zero and the page counted from one, so a run
    of MDM2 read from 1YCR showed residues 1 to 85 where the protein's are 25 to 109, and Leu54
    sat at 30, where nobody who knows the protein would look for it. Counted from one otherwise,
    and also when the chain's length differs from what was sampled or a number repeats (an
    insertion code), rather than labeling a residue with another's number.
    """
    from .. import structure as structuremod
    record = record if record is not None else (read_json(os.path.join(run, "submission.json"),
                                                          {}) or {})
    count = int(count or 0)
    counted = {"numbers": list(range(1, count + 1)), "from": "the sequence, counted from one"}
    given = record.get("structure") or ""
    if not count or not given or not os.path.isfile(given):
        return counted
    try:
        chain = structuremod.chain_named(structuremod.read(given), record.get("chain") or "")
    except structuremod.Unreadable:
        return counted
    numbers = list((chain or {}).get("numbers") or [])
    if len(numbers) != count or len(set(numbers)) != count:
        return counted
    return {"numbers": numbers,
            "from": "chain %s of the structure given, in its own numbering" % chain["id"]}


def samples(run):
    """The files the sampling left, as the page lists them for download.

    Found by looking rather than read out of the record, so a run whose submission.json was lost,
    or one copied in from somewhere else, still shows what it holds.
    """
    out = os.path.join(run, "out")
    found = []
    for path in (sorted(glob.glob(os.path.join(out, "*.xtc")))
                 + sorted(glob.glob(os.path.join(out, "*.pdb")))
                 + ([ensemble_file(run)] if ensemble_file(run) else [])):
        found.append({"name": os.path.relpath(path, run),
                      "bytes": os.path.getsize(path),
                      "kind": ("the samples" if path.endswith(".xtc") else
                               "the shapes, one model each" if "analysis" in path else
                               "the topology the samples are read with")})
    return found


def counts(run):
    """How far the run has got, read off the files rather than the log: a sample that is on disk
    is a sample, and a log line can be printed before the work it describes has finished."""
    got = analysis(run)
    drawn = int(got.get("samples") or 0)
    if not drawn:
        # Before the analysis has run, the batches the tool has written so far are the only count
        # there is, and each one names the samples in it: batch_<first>_<last>.npz.
        for path in glob.glob(os.path.join(run, "out", "batch_*.npz")):
            parts = os.path.splitext(os.path.basename(path))[0].split("_")
            try:
                drawn = max(drawn, int(parts[-1]))
            except (ValueError, IndexError):
                drawn = drawn or 0
    return {"samples": drawn,
            "shapes": len((got.get("states") or {}).get("states") or []),
            "alignments": len(glob.glob(os.path.join(run, "inputs", "*.a3m")))}


def headline(run, got=None):
    """The one sentence a finished run is read by.

    An ensemble's answer is not a number but a shape of an answer: one shape or several, and how
    much of the spread is in each. Said here rather than in the page's script so that the page,
    the report and the flow all say the same thing.
    """
    got = analysis(run) if got is None else got
    shapes = (got.get("states") or {}).get("states") or []
    if not shapes:
        return ""
    spread = got.get("spread") or {}
    if len(shapes) == 1:
        return ("One shape, in %d samples: the chain holds it, give or take %.2f nm."
                % (got.get("samples") or 0, spread.get("from_mean_sd") or 0))
    biggest = shapes[0]
    return ("%d shapes in %d samples. The most populated holds %.0f%% of them, and the samples "
            "lie %.2f nm from the mean shape on average."
            % (len(shapes), got.get("samples") or 0, 100 * (biggest.get("share") or 0),
               spread.get("from_mean_mean") or 0))


def summarise(run, states=None):
    """One run as the list shows it: what was asked for, where the job is, what has arrived."""
    record = read_json(os.path.join(run, "submission.json"), {}) or {}
    job_id = str(record.get("job_id") or "")
    state, elapsed, left, reason = "", "", "", ""
    if job_id:
        if states is None:
            states = job_states([job_id])
        if job_id in states:
            state, elapsed, left, reason = states[job_id]
        else:
            state, elapsed = finished_state(job_id)
    elif record.get("dry_run"):
        state = "DRY RUN"
    got = analysis(run)
    counted = counts(run)
    return {
        "path": run,
        "name": record.get("name") or os.path.basename(run),
        "tool": record.get("tool") or "bioemu",
        "owner": record.get("user") or owner_of(run),
        "submitted": record.get("submitted", ""),
        "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(run))),
        "job_id": job_id,
        "state": state or ("UNKNOWN" if job_id else ""),
        "over": state in OVER,
        "elapsed": elapsed,
        "left": left,
        "reason": reason if reason not in ("None", "") else "",
        "asked": record.get("asked") or {},
        "sequence": record.get("sequence") or got.get("sequence") or "",
        "residues": record.get("residues") or got.get("residues") or 0,
        "alignments": record.get("alignments") or "",
        "mutations": record.get("mutations") or [],
        "structure": record.get("structure") or "",
        "from": record.get("from") or "",
        "set": set_of(run),
        "counts": counted,
        "samples": counted["samples"] or None,
        "shapes": counted["shapes"] or None,
        "headline": headline(run, got),
    }


def detail(run, log_lines=240):
    """One run in full: its summary, the shapes reading it found, the files, and the log."""
    info = summarise(run)
    got = analysis(run)
    shapes = (got.get("states") or {}).get("states") or []
    shown = set(got.get("shown") or [])
    info["table"] = {
        "columns": list(SHOWN),
        "rows": [{"rank": one.get("rank"), "share": one.get("share"),
                  "samples": one.get("samples"), "spread": one.get("spread_nm"),
                  "sample": one.get("frame"), "in_viewer": one.get("rank") in shown}
                 for one in shapes],
        "total": len(shapes)}
    info["analysis"] = got
    info["numbering"] = numbering(run, info["residues"])
    info["ensemble"] = (os.path.relpath(ensemble_file(run), run) if ensemble_file(run) else "")
    info["files"] = samples(run)
    info["told"] = _told(run)
    log = log_path(run)
    info["log_name"] = os.path.relpath(log, run) if log else ""
    info["log"] = tail(log, log_lines) if log else ""
    return info


def _told(run):
    """What the job was told, read back off the run rather than rebuilt: the alignment it was
    given and the one line that did the sampling, out of the script that surrounds it."""
    out = {"what": "", "text": "", "command": ""}
    found = sorted(glob.glob(os.path.join(run, "inputs", "*.a3m")))
    if found:
        out["what"] = os.path.relpath(found[0], run)
        try:
            with open(found[0], encoding="utf-8") as handle:
                # The first lines only: an alignment is thousands of sequences, and what is worth
                # showing is that it is there and what it starts with.
                out["text"] = "".join(handle.readlines()[:40])
        except OSError:
            pass
    script = job_script(run)
    if os.path.isfile(script):
        try:
            with open(script, encoding="utf-8") as handle:
                for line in handle:
                    if "bioemu.sample" in line:
                        out["command"] = line.strip()
                        break
        except OSError:
            pass
    return out


def resubmit(run):
    """Queue the job script again, for a run that ran out of walltime or died on a bad node.

    The alignment is already in the run folder, so this needs no network either: the script it
    queues is phase two and nothing else, which is exactly why the phases are split.
    """
    import json
    import subprocess
    script = job_script(run)
    if not os.path.isfile(script):
        return False, "this run has no job script to queue"
    info = summarise(run)
    if info["job_id"] and not info["over"]:
        return False, "job %s is still %s; cancel it first" % (info["job_id"],
                                                               info["state"].lower())
    done = subprocess.run(["sbatch", "--parsable", script], capture_output=True, text=True,
                          cwd=run)
    if done.returncode != 0:
        return False, (done.stderr or done.stdout).strip() or "sbatch refused the job"
    job_id = done.stdout.strip().split(";")[0]
    marker = os.path.join(run, "submission.json")
    record = read_json(marker, {}) or {}
    record["job_id"] = job_id
    record["resubmitted"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(marker, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2)
            handle.write("\n")
    except OSError:
        pass            # the job is queued either way, and the state is read from Slurm, not here
    return True, "queued again as job %s" % job_id


def main(argv=None):
    argv = (argv if argv is not None else sys.argv)[1:]
    if argv:
        info = detail(os.path.abspath(argv[0]))
        print("%s  %s  %s" % (info["name"], info["state"] or "not queued", info["path"]))
        print("  asked for  " + ", ".join("%s %s" % (k, v)
                                          for k, v in sorted(info["asked"].items())))
        print("  so far     " + ", ".join("%d %s" % (n, k)
                                          for k, n in sorted(info["counts"].items())))
        if info["headline"]:
            print("  " + info["headline"])
        for row in info["table"]["rows"]:
            print("  shape %-3s %5.1f%%  %6s samples  spread %s nm"
                  % (row["rank"], 100 * (row["share"] or 0), row["samples"], row["spread"]))
        return 0
    found = find_runs([runs_root()])
    states = job_states([(read_json(os.path.join(run, "submission.json"), {}) or {}).get("job_id")
                         for run in found])
    for run in found:
        info = summarise(run, states)
        print("%s  %-10s %-12s %-28s %s  %s"
              % (info["modified"], info["state"] or "-", info["owner"], info["name"],
                 ", ".join("%d %s" % (n, k) for k, n in sorted(info["counts"].items())),
                 info["path"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
