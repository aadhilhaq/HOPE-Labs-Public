"""What a design run has done so far, and what Slurm makes of its job.

By Aadhil Haq

A run folder is the one the page wrote: submission.json beside flow.json, the structure in
inputs/, and the design job's own folder inside it holding plan.json, the job script, the Slurm
log and, at the end, designs.json. That is the same shape a flow leaves, because the page queues
through the flow's own driver; a run started from the canvas and read by this module therefore
reads correctly, which is how somebody who drew a flow last week can still find it here.

Nothing here writes to a run. cancel() and resubmit() are the two exceptions and both go through
Slurm, which decides for itself whether the person asking owns the job.

    python -m hope_labs.pages.runs rfdiffusion                 the runs you can see, newest first
    python -m hope_labs.pages.runs rfdiffusion <run folder>    one run in detail
"""
from __future__ import annotations

import glob
import json
import os
import pwd
import subprocess
import sys
import time

from hope_flow import drivers

#: Slurm states that mean the job is over, whatever it achieved. A run in one of these will not
#: change again on its own, so the page stops polling it and offers Queue again instead of Cancel.
OVER = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED",
        "BOOT_FAIL", "DEADLINE", "SPECIAL_EXIT"}

#: What to count while a run is still going, per tool: (what it is called, where to look, the
#: suffix). designs.json is the only thing either tool leaves that the rest of the lab reads, and
#: it is written at the very end, so before then the only honest progress is the files on disk.
PROGRESS = {
    "rfdiffusion": (("backbones", "rfdiffusion", ".pdb"),
                    ("sequences", "proteinmpnn", "")),
    "boltzgen": (("designs", os.path.join("final_ranked_designs", "final_30_designs"), ".cif"),
                 ("considered", os.path.join("final_ranked_designs",
                                             "intermediate_ranked_10_designs"), ".cif")),
}

#: The columns of the designs table, in the order they are shown. designs.json carries the same
#: keys for both tools by construction (hope_flow/adapters.py documents the shape), so one table
#: serves both.
SHOWN = ("name", "score", "residues", "binder", "sequence")


#: Where each tool's runs go when nobody says otherwise. Taken from the flow's own table rather
#: than written again here: a run queued from the page and a run queued from the canvas land in
#: one place, and a person looking for either looks in one folder.
RUNS_OF = {tool: drivers.RUNS[tool] for tool in PROGRESS}


def runs_root(tool):
    return RUNS_OF[tool]


def owner_of(path):
    try:
        return pwd.getpwuid(os.stat(path).st_uid).pw_name
    except (OSError, KeyError):
        return ""


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def design_dir(run):
    """The design job's own folder inside a run folder, or the run folder itself.

    The driver names it after the flow and the card, so it is found by the job script it holds
    rather than by rebuilding that name here: a run folder written by a flow uses the flow's name
    and would not match anything the page would guess.
    """
    found = sorted(glob.glob(os.path.join(run, "*", "job.sbatch")))
    return os.path.dirname(found[0]) if found else run


def log_path(run):
    """The newest Slurm log in the design folder. One job writes one, but a run queued again
    writes another beside it, and the newest is the one being watched."""
    logs = sorted(glob.glob(os.path.join(design_dir(run), "slurm_*.out")), key=os.path.getmtime)
    return logs[-1] if logs else ""


def tail(path, lines=200, cap=400_000):
    """The last lines of a file, without reading a long log from the beginning."""
    try:
        size = os.path.getsize(path)
        with open(path, errors="replace") as handle:
            handle.seek(max(0, size - cap))
            text = handle.read()
    except OSError:
        return ""
    if size > cap:
        text = text.split("\n", 1)[-1]          # the first line read is half a line
    return "\n".join(text.splitlines()[-lines:])


def counts(run, tool):
    """How far the run has got, as {what: how many}.

    Read off the files rather than the log: a log line can be printed before the work it
    describes has finished, and a design that is on disk is a design.
    """
    where = design_dir(run)
    out = {}
    for name, folder, suffix in PROGRESS.get(tool, ()):
        path = os.path.join(where, folder)
        try:
            found = os.listdir(path)
        except OSError:
            out[name] = 0
            continue
        out[name] = len([f for f in found if not suffix or f.endswith(suffix)])
    return out


def job_states(job_ids):
    """{job id: (state, elapsed, left, reason)} from squeue, for the jobs it still knows."""
    live = sorted({str(job) for job in job_ids if job})
    if not live:
        return {}
    try:
        done = subprocess.run(["squeue", "-h", "-j", ",".join(live), "-o", "%i|%T|%M|%L|%R"],
                              capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return {}
    states = {}
    for line in done.stdout.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 5:
            # An array task is 123_4 in squeue and 123 in the record, so the id is cut back to
            # the job it belongs to. Neither tool asks for an array, but a job queued again by
            # hand can arrive as one.
            states[parts[0].split("_")[0]] = tuple(parts[1:])
    return states


def finished_state(job_id):
    """What became of a job squeue has forgotten. sacct remembers for a few months."""
    try:
        done = subprocess.run(["sacct", "-n", "-X", "-P", "-j", str(job_id), "-o", "State,Elapsed"],
                              capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return "", ""
    for line in done.stdout.splitlines():
        parts = line.strip().split("|")
        if parts and parts[0]:
            # "CANCELLED by 12345" is one state with a tail on it.
            return parts[0].split()[0], (parts[1] if len(parts) > 1 else "")
    return "", ""


def find_runs(roots, user=None):
    """Every run folder under these roots: <root>/<run> and <root>/<user>/<run>.

    Both depths, because the page writes the first and somebody's own --runs folder may hold the
    second. A folder is a run when it has a submission.json, which the page writes before it
    queues anything, so a run whose submission failed is still listed and can still be read.
    """
    found = []
    for root in roots:
        root = os.path.abspath(os.path.expanduser(root))
        for depth in ("*", "*/*"):
            for marker in glob.glob(os.path.join(root, depth, "submission.json")):
                found.append(os.path.dirname(marker))
    out = sorted(set(found), key=lambda path: os.path.getmtime(path), reverse=True)
    if user:
        out = [run for run in out
               if (read_json(os.path.join(run, "submission.json"), {}) or {}).get("user") == user
               or owner_of(run) == user]
    return out


def summarise(run, states=None):
    """One run as the list shows it: what was asked for, where the job is, what has arrived."""
    record = read_json(os.path.join(run, "submission.json"), {}) or {}
    tool = record.get("tool") or ""
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
    got = counts(run, tool)
    designs = read_json(os.path.join(design_dir(run), "designs.json"), {}) or {}
    return {
        "path": run,
        "name": record.get("name") or os.path.basename(run),
        "tool": tool,
        "owner": record.get("user") or owner_of(run),
        "submitted": record.get("submitted", ""),
        "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(run))),
        "job_id": job_id,
        "state": state or ("UNKNOWN" if job_id else ""),
        "over": state in OVER,
        "elapsed": elapsed,
        "left": left,
        # squeue says "None" for a job with nothing holding it up, which is not news.
        "reason": reason if reason not in ("None", "") else "",
        "asked": record.get("asked") or {},
        "target": record.get("target") or {},
        "results": design_dir(run),
        "counts": got,
        # Only once designs.json is there: until then "0 designs" would be read as none found
        # rather than none written yet, which are different things on a run still going.
        "designs": len(designs.get("designs") or []) if designs else None,
    }


def detail(run, log_lines=240, rows=30):
    """One run in full: its summary, the designs it has written, the files, and the log."""
    info = summarise(run)
    where = info["results"]
    designs = read_json(os.path.join(where, "designs.json"), {}) or {}
    table = []
    for one in (designs.get("designs") or [])[:rows]:
        sequence = str(one.get("sequence") or "")
        # The structure's path is given relative to the run, so the page can offer it for
        # download: /api/download only serves what is inside a run folder, which is the point.
        at = str(one.get("path") or "")
        table.append({"name": one.get("name") or "", "score": one.get("score"),
                      "residues": len(sequence),
                      "binder": ",".join(one.get("binder_chains") or []),
                      "sequence": sequence,
                      "file": os.path.relpath(at, run) if at.startswith(run + os.sep) else ""})
    info["table"] = {"columns": list(SHOWN), "rows": table,
                     "total": len(designs.get("designs") or [])}
    info["plan"] = read_json(os.path.join(where, "plan.json"), {}) or {}
    log = log_path(run)
    info["log_name"] = os.path.relpath(log, run) if log else ""
    info["log"] = tail(log, log_lines) if log else ""
    info["files"] = sorted(
        os.path.relpath(os.path.join(folder, name), run)
        for folder in (run, where)
        for name in (os.listdir(folder) if os.path.isdir(folder) else [])
        if os.path.isfile(os.path.join(folder, name)))
    return info


def cancel(run):
    """scancel this run's job. Slurm decides whether the person may: it is their job, or not."""
    record = read_json(os.path.join(run, "submission.json"), {}) or {}
    job_id = str(record.get("job_id") or "")
    if not job_id:
        return False, "this run has no job to cancel"
    done = subprocess.run(["scancel", job_id], capture_output=True, text=True)
    if done.returncode != 0:
        return False, (done.stderr or done.stdout).strip() or "scancel refused"
    return True, "job %s cancelled" % job_id


def resubmit(run):
    """Queue the job script again, for a run that ran out of walltime or died on a bad node.

    The script is the one the driver wrote, so this queues exactly what was queued before. Both
    tools start again from the beginning: neither writes a checkpoint a second job could pick up,
    which is said plainly on the page rather than left to be discovered.
    """
    script = os.path.join(design_dir(run), "job.sbatch")
    if not os.path.isfile(script):
        return False, "this run has no job script to queue"
    info = summarise(run)
    if info["job_id"] and not info["over"]:
        return False, "job %s is still %s; cancel it first" % (info["job_id"],
                                                               info["state"].lower())
    done = subprocess.run(["sbatch", "--parsable", script], capture_output=True, text=True,
                          cwd=os.path.dirname(script))
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
    if not argv or argv[0] not in RUNS_OF:
        print("usage: python -m hope_labs.pages.runs {%s} [run folder]"
              % "|".join(sorted(RUNS_OF)), file=sys.stderr)
        return 2
    tool, rest = argv[0], argv[1:]
    if rest:
        info = detail(os.path.abspath(rest[0]))
        print("%s  %s  %s" % (info["name"], info["state"] or "not queued", info["path"]))
        asked = info["asked"]
        print("  asked for  " + ", ".join("%s %s" % (k, v) for k, v in sorted(asked.items())))
        print("  so far     " + (", ".join("%d %s" % (n, k)
                                           for k, n in sorted(info["counts"].items())) or "nothing"))
        print("  designs    %s" % ("not written yet" if info["designs"] is None
                                   else "%d in designs.json" % info["designs"]))
        return 0
    found = find_runs([runs_root(tool)])
    states = job_states([(read_json(os.path.join(run, "submission.json"), {}) or {}).get("job_id")
                         for run in found])
    for run in found:
        info = summarise(run, states)
        print("%s  %-10s %-12s %-28s %s  %s"
              % (info["modified"], info["state"] or "-", info["owner"], info["name"],
                 ", ".join("%d %s" % (n, k) for k, n in sorted(info["counts"].items())) or "-",
                 info["path"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
