"""What a folding run has done so far, what it wrote, and what Slurm makes of its job.

By Aadhil Haq

A run folder is the one the page wrote: submission.json and entities.json at the top, the input
the tool reads in inputs/, the alignments where phase one left them, the structures where phase
two wrote them, and job.sbatch with the Slurm log beside it.

Everything here that does not depend on which tool wrote the run is the design page's, imported
rather than written again: reading a record, finding the runs under a folder, asking squeue and
sacct what became of a job, tailing a log, cancelling and queueing again. What is here is the
part that is about folding: where each tool puts its structures, what the confidence files beside
them say, and the ranking, which is the thing somebody opens a finished run to see.

    python -m hope_labs.folding.runs boltz2                  the runs you can see, newest first
    python -m hope_labs.folding.runs boltz2 <run folder>      one run in detail
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
import time

from . import SERVES
from . import job as jobmod
#: The generic half of reading a run folder. One implementation, used by all four pages: a run
#: queued from either kind of page is listed, polled and cancelled by the same code.
from ..pages.runs import (OVER, cancel, finished_state, find_runs, job_states, owner_of,
                          read_json, tail)

__all__ = ["OVER", "cancel", "finished_state", "find_runs", "job_states", "owner_of",
           "read_json", "tail", "runs_root", "structures", "summarise", "detail", "resubmit"]

#: Where each tool's runs go when nobody says otherwise, taken from the driver's own table.
RUNS_OF = dict(jobmod.RUNS)

#: The rank in a file ColabFold wrote. It names them rank_001 upwards, best first.
CF_RANK = re.compile(r"_rank_(\d+)_")
#: The rank in a file Boltz-2 wrote. It numbers them from zero, best first.
BOLTZ_RANK = re.compile(r"_model_(\d+)\.(cif|pdb)$")

#: The columns of the structures table, in the order they are shown.
SHOWN = ("rank", "name", "confidence", "plddt", "ptm", "iptm", "chains")


def runs_root(tool):
    return RUNS_OF[tool]


def job_script(run):
    """The job script, which the page offers and which Queue again runs. In the run folder
    itself rather than a folder below it: a folding run is one job."""
    return os.path.join(run, "job.sbatch")


def log_path(run):
    """The newest Slurm log. One job writes one, but a run queued again writes another beside
    it, and the newest is the one being watched."""
    logs = sorted(glob.glob(os.path.join(run, "slurm_*.out")), key=os.path.getmtime)
    return logs[-1] if logs else ""


def _mean(values):
    good = [v for v in values if isinstance(v, (int, float))]
    return sum(good) / len(good) if good else None


def _colabfold_scores(path):
    """The scores ColabFold wrote beside this structure, as the table shows them.

    Its pLDDT is an array, one value per residue, so the headline is the mean of it, which is
    the number ColabFold itself ranks by for a single chain.
    """
    scores = path.replace("_unrelaxed_rank_", "_scores_rank_")
    got = read_json(os.path.splitext(scores)[0] + ".json", {}) or {}
    plddt = _mean(got.get("plddt") or [])
    return {"plddt": round(plddt, 1) if plddt is not None else None,
            "ptm": got.get("ptm"), "iptm": got.get("iptm"),
            # ColabFold has no single combined score of its own, so the one it ranks a complex by
            # is shown as the confidence and a single chain shows its mean pLDDT.
            "confidence": got.get("iptm") if got.get("iptm") is not None else (
                round(plddt / 100.0, 3) if plddt is not None else None)}


def _boltz_scores(path):
    """The confidence file Boltz-2 wrote beside this structure."""
    folder, name = os.path.dirname(path), os.path.basename(path)
    got = read_json(os.path.join(folder, "confidence_%s.json" % os.path.splitext(name)[0]),
                    {}) or {}
    plddt = got.get("complex_plddt")
    return {"confidence": got.get("confidence_score"), "ptm": got.get("ptm"),
            "iptm": got.get("iptm"),
            # Boltz-2's pLDDT is already a fraction of one, and ColabFold's is a percentage.
            # Shown the same way, as a percentage, so the two pages read alike.
            "plddt": round(plddt * 100, 1) if isinstance(plddt, (int, float)) else None}


def structures(run):
    """Every structure this run has written, best first.

    Found by looking rather than by reading the record, so a run whose submission.json was lost,
    or one copied in from somewhere else, still shows what it holds.
    """
    found = []
    for path in glob.glob(os.path.join(run, "fold", "*", "*rank_*.pdb")):
        rank = CF_RANK.search(os.path.basename(path))
        found.append(dict(_colabfold_scores(path), path=path, kind="colabfold",
                          rank=int(rank.group(1)) if rank else 999,
                          name=os.path.basename(path)))
    for suffix in ("cif", "pdb"):
        for path in glob.glob(os.path.join(run, "out", "boltz_results_*", "predictions", "*",
                                           "*_model_*.%s" % suffix)):
            rank = BOLTZ_RANK.search(os.path.basename(path))
            found.append(dict(_boltz_scores(path), path=path, kind="boltz2",
                              # Boltz-2 numbers its models from zero, best first. Shown from one,
                              # because every other ranking on these pages starts at one and a
                              # table with a rank 0 in it reads as a missing value.
                              rank=(int(rank.group(1)) + 1) if rank else 999,
                              name=os.path.basename(path)))
    return sorted(found, key=lambda one: (one["rank"], one["name"]))


def alignments(run):
    """The alignments phase one left, which is how the page shows that phase one happened."""
    return (sorted(glob.glob(os.path.join(run, "msa", "*.csv")))
            + sorted(glob.glob(os.path.join(run, "fold", "*", "*.a3m"))))


def counts(run):
    """How far the run has got, read off the files rather than the log.

    A log line can be printed before the work it describes has finished, and a structure that is
    on disk is a structure.
    """
    return {"structures": len(structures(run)), "alignments": len(alignments(run))}


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
    got = counts(run)
    return {
        "path": run,
        "name": record.get("name") or os.path.basename(run),
        "tool": record.get("tool") or "",
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
        "rows": record.get("rows") or [],
        "alignments": record.get("alignments") or "",
        "plan": record.get("plan") or {},
        "counts": got,
        "structures": got["structures"] or None,
    }


def detail(run, log_lines=240, rows=20):
    """One run in full: its summary, the structures it has written, the files, and the log."""
    info = summarise(run)
    table = []
    for one in structures(run)[:rows]:
        table.append({"rank": one["rank"], "name": one["name"],
                      "confidence": one.get("confidence"), "plddt": one.get("plddt"),
                      "ptm": one.get("ptm"), "iptm": one.get("iptm"),
                      "chains": "",
                      "file": os.path.relpath(one["path"], run)})
    info["table"] = {"columns": list(SHOWN), "rows": table, "total": len(structures(run))}
    info["told"] = _told(run)
    # The rows as they were entered, sequences and modifications included, for Reuse: the record
    # beside the run keeps what the page showed, and entities.json keeps what was typed.
    kept = (read_json(os.path.join(run, "entities.json"), {}) or {}).get("rows") or []
    info["entities"] = [{key: row.get(key) for key in ("kind", "name", "sequence", "copies",
                                                      "modifications")}
                        for row in kept if isinstance(row, dict)]
    log = log_path(run)
    info["log_name"] = os.path.relpath(log, run) if log else ""
    info["log"] = tail(log, log_lines) if log else ""
    info["files"] = sorted(
        os.path.relpath(os.path.join(folder, name), run)
        for folder in (run, os.path.join(run, "inputs"))
        for name in (os.listdir(folder) if os.path.isdir(folder) else [])
        if os.path.isfile(os.path.join(folder, name)))
    return info


def _told(run):
    """What the job was told, read back off the run rather than rebuilt.

    The same box the form shows before submitting, shown afterwards against what was actually
    written: that is the only way to settle an argument about what a finished run folded.
    """
    found = sorted(glob.glob(os.path.join(run, "inputs", "*.yaml"))
                   + glob.glob(os.path.join(run, "inputs", "*.fasta")))
    out = {"what": "", "text": "", "command": ""}
    if found:
        out["what"] = os.path.relpath(found[0], run)
        try:
            with open(found[0], encoding="utf-8") as handle:
                out["text"] = handle.read()[:20000]
        except OSError:
            pass
    script = job_script(run)
    if os.path.isfile(script):
        try:
            with open(script, encoding="utf-8") as handle:
                # The one line that does the folding, out of the script that surrounds it.
                for line in handle:
                    if "/bin/boltz" in line or "colabfold_batch" in line:
                        out["command"] = line.strip()
                        break
        except OSError:
            pass
    return out


def resubmit(run):
    """Queue the job script again, for a run that ran out of walltime or died on a bad node.

    The alignments are already on disk, so this needs no network either: the script it queues is
    phase two and nothing else, which is exactly why the phases are split.
    """
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
    if not argv or argv[0] not in SERVES:
        print("usage: python -m hope_labs.folding.runs {%s} [run folder]" % "|".join(SERVES),
              file=sys.stderr)
        return 2
    tool, rest = argv[0], argv[1:]
    if rest:
        info = detail(os.path.abspath(rest[0]))
        print("%s  %s  %s" % (info["name"], info["state"] or "not queued", info["path"]))
        print("  asked for  " + ", ".join("%s %s" % (k, v)
                                          for k, v in sorted(info["asked"].items())))
        print("  so far     " + ", ".join("%d %s" % (n, k)
                                          for k, n in sorted(info["counts"].items())))
        for row in info["table"]["rows"]:
            print("  %2d  %-52s  %s" % (row["rank"], row["name"][:52], row["confidence"]))
        return 0
    found = find_runs([runs_root(tool)])
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
