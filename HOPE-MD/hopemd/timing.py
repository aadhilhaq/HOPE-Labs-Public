"""What ran where, for how long, at what speed: the run's wall-time record.

Slurm's accounting (``sacct``) has, for every job of a run, the node, partition, GPU type,
start, end and elapsed time; the job logs name the host's CPU and GPU; the spec says how many
steps each stage took; the finished production parts have their ns/day. A run's jobs are the
graph's in ``jobs/ids.json``, those a resubmission replaced, the video, snapshot and
dense-window jobs, and every other job whose log is in ``logs/`` (an earlier tidy, the Setup
screen's Prepare). This module joins them into one table, keeps it in ``jobs/timing.json`` once
every job has finished (and makes it again when a job is added), and appends one row per
finished job to ``timings.tsv`` beside ``benchmarks.tsv`` in the run's own runs folder, so the
next run's estimate can say how long each stage will take on this cluster, not just the
production. The tidy job records the rows before it packs, so a run nobody opens is timed too.
Nothing here submits anything; ``sacct`` is a read of the accounting database.

    python3 -m hopemd.timing <rundir> [<rundir> ...]      # record the rows of finished runs by hand
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .spec import Spec, derive, mmgbsa_frames

SACCT_FIELDS = ["JobID", "JobName", "Partition", "NodeList", "Elapsed", "ElapsedRaw", "State",
                "AllocTRES", "Start", "End", "ReqCPUS"]
FINAL = ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED",
         "DEADLINE", "BOOT_FAIL")
STAGE_OF = {"build": "build", "equil": "equil", "analysis": "analysis", "mmgbsa": "mmgbsa"}


def parse_sacct(text: str) -> list[dict]:
    """Rows of ``sacct -n -P -X`` with SACCT_FIELDS, as dicts."""
    out = []
    for line in text.splitlines():
        parts = line.rstrip("\n").split("|")
        if len(parts) < len(SACCT_FIELDS):
            continue
        r = dict(zip(SACCT_FIELDS, parts))
        try:
            r["ElapsedRaw"] = int(r["ElapsedRaw"] or 0)
        except ValueError:
            r["ElapsedRaw"] = 0
        r["State"] = (r["State"] or "").split()[0] if r["State"] else ""
        out.append(r)
    return out


def sacct(ids: list[str]) -> list[dict]:
    """One call for every job of the run; [] without sacct (a dry run, a laptop)."""
    real = [i for i in ids if i and not str(i).startswith("dry")]
    if not real or shutil.which("sacct") is None:
        return []
    try:
        r = subprocess.run(["sacct", "-n", "-P", "-X", "-j", ",".join(real), "-o", ",".join(SACCT_FIELDS)],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_sacct(r.stdout) if r.returncode == 0 else []


def gpu_type(tres: str) -> str:
    """'a100' from 'billing=4,cpu=4,gres/gpu:a100=1,gres/gpu=1,mem=16G'; '' for a CPU job."""
    m = re.search(r"gres/gpu:([A-Za-z0-9_.-]+)=", tres or "")
    if m:
        return m.group(1)
    return "gpu" if "gres/gpu=" in (tres or "") else ""


def _log(rundir: Path, name: str, key: str, jobid: str) -> Path:
    """A job's Slurm log: logs/<run>_<key>.<job>[_<task>].out, the Prepare job's logs/prepare.<job>.out."""
    return rundir / "logs" / (f"prepare.{jobid}.out" if key == "prepare" else f"{name}_{key}.{jobid}.out")


def log_facts(rundir: Path, name: str, key: str, jobid: str) -> dict:
    """host, cpu and gpu names from the '[hopemd] ... host=... cpu="..." gpu="..."' line the
    preamble prints; older logs have only the host."""
    try:
        head = _log(rundir, name, key, jobid).read_text(errors="replace")[:4000]
    except OSError:
        return {}
    m = re.search(r"\[hopemd\] .*?host=(\S+)", head)
    out = {"host": m.group(1)} if m else {}
    for k in ("cpu", "gpu"):
        m = re.search(k + r'="([^"]*)"', head)
        if m and m.group(1).strip() and not m.group(1).startswith("NVIDIA-SMI has failed"):   # a CPU node with nvidia-smi and no driver (ACES)
            out[k] = m.group(1).strip()
    return out


def steps_for(spec: Spec, key: str, nseg: int) -> tuple[int | None, int | None]:
    """(steps, frames) a stage works through: the ladder for equil, one segment's share of
    production, the saved frames for the analysis, the MM-GBSA window's frames. None where steps
    mean nothing."""
    pro = spec.protocol
    if key == "equil":
        return int(pro.minimise_steps + (pro.heat_ps + pro.equil_ps) * 1000 / 2.0), None   # the ladder runs at 2 fs
    if key.startswith("prod_s"):
        return int(round(pro.length_ns / max(nseg, 1) * 1e6 / pro.timestep_fs)), None
    if key == "analysis":
        return None, int(round(pro.length_ns * 1000.0 / pro.save_ps))
    if key == "mmgbsa":
        return None, mmgbsa_frames(spec)
    return None, None


def _mmgbsa_frames_run(rundir: Path, name: str, jobid: str) -> int | None:
    """The frames an MM-GBSA job scored, from MMPBSA.py's own line in its log ("80 frames were
    processed by cpptraj"): the window of a job a rerun replaced is no longer in the spec."""
    try:
        m = re.search(r"(\d+) frames were processed", _log(rundir, name, "mmgbsa", jobid).read_text(errors="replace"))
    except OSError:
        return None
    return int(m.group(1)) if m else None


def _json(p: Path) -> dict:
    try:
        v = json.loads(p.read_text())
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def run_jobs(rundir: Path) -> list[dict]:
    """Every job the run has had, in the order they were submitted, without asking the queue:
    {key, job, current, replaced}. current: the graph's job as jobs/ids.json has it now;
    replaced: a later job of the same key took its place (a resubmission, a second tidy or video)."""
    rundir = Path(rundir)
    ids = _json(rundir / "jobs" / "ids.json")
    found: dict[str, str] = {}                  # job id -> key, the first name met for it
    for key, jid in (ids.get("jobs") or {}).items():
        found.setdefault(str(jid), key)
    for h in ids.get("history") or []:
        for key, jid in (h.get("replaced") or {}).items():
            found.setdefault(str(jid), key)
    for rel, prefix in (("export/video_jobs.json", "video_"), ("export/snapshot_jobs.json", "snap_")):
        for k, j in _json(rundir / rel).items():
            if isinstance(j, dict) and j.get("job"):
                found.setdefault(str(j["job"]), prefix + k)
    for j in _json(rundir / "analysis" / "dense_jobs.json").values():
        if isinstance(j, dict) and j.get("job"):
            tag = f"{float(j.get('centre_ns') or 0):g}".replace(".", "p")
            found.setdefault(str(j["job"]), f"dense_r{j.get('rep')}_{tag}")
    from .queue import run_logs
    for lg in run_logs(rundir):
        found.setdefault(lg["job"], lg["stage"])
    current = {str(v) for v in (ids.get("jobs") or {}).values()}
    num = lambda jid: int(jid) if jid.isdigit() else float("inf")      # noqa: E731 (a dry run's ids last)
    newest: dict[str, str] = {}
    for jid, key in found.items():
        if key not in newest or num(jid) > num(newest[key]):
            newest[key] = jid
    return [{"key": key, "job": jid, "current": jid in current, "replaced": newest[key] != jid}
            for jid, key in sorted(found.items(), key=lambda kv: num(kv[0]))]


def _nsday(rundir: Path, key: str, task: str) -> float | None:
    if not key.startswith("prod_s") or not task:
        return None
    vals = []
    for f in (rundir / f"r{task}").glob(f"{key}_p*.nsday"):
        try:
            vals.append(float(f.read_text().strip()))
        except (OSError, ValueError):
            pass
    return round(sum(vals) / len(vals), 1) if vals else None


def _hms(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def timing(rundir: str | Path, refresh: bool = False) -> dict:
    """The table. From jobs/timing.json when every job had finished and none has been added since
    (an MM-GBSA rerun, a video), else from sacct now.

    {"jobs": [{key, stage, job, task, state, node, partition, gpu, cpu, cpus, steps, frames,
               elapsed_s, elapsed, ns_per_day, start, end, current, replaced}, ...],
     "ids": the job ids it covers, "complete": bool,
     "wall_s": int (the graph's first start to its last end), "busy_s": int (sum of elapsed),
     "estimate": {ns_per_day, note, hours_per_replicate, walltime, stage_hours}}"""
    rundir = Path(rundir)
    cache = rundir / "jobs" / "timing.json"
    listed = run_jobs(rundir)
    if cache.exists() and not refresh:
        try:
            kept = json.loads(cache.read_text())
            if kept.get("ids") == [j["job"] for j in listed]:     # no job added since it was written
                # MM-GBSA's frames as a table made now counts them: one cached before the window was counted gave
                # the run's length over the stride (100 frames where 80 were scored)
                spec = Spec.from_json((rundir / "spec.json").read_text())
                for j in kept.get("jobs", []):
                    if j.get("key") == "mmgbsa" and j.get("state"):
                        j["frames"] = _mmgbsa_frames_run(rundir, spec.name, j["job"]) or (None if j.get("replaced") else mmgbsa_frames(spec))
                return kept
        except ValueError:
            pass
    spec = Spec.from_json((rundir / "spec.json").read_text())
    graph = []
    try:
        graph = json.loads((rundir / "jobs" / "graph.json").read_text())
    except (OSError, ValueError):
        pass
    in_graph = {j.get("key") for j in graph}
    # a stage the graph declares NOT an array has no replicate: "build r7" read as replicate 7 of
    # this run, when 7 was its task number in a build array shared with the other systems. A graph
    # that does not record `array` at all says nothing either way, and the task sacct gives stands.
    not_array = {j.get("key") for j in graph if "array" in j and not j.get("array")}
    nseg = sum(1 for j in graph if j.get("key", "").startswith("prod_s")) or 1
    rows = {r["JobID"]: r for r in sacct([j["job"] for j in listed])}
    jobs = []
    for lj in listed:
        key, jid = lj["key"], lj["job"]
        flags = {"current": lj["current"], "replaced": lj["replaced"]}
        # an id that names one task of an array job matches that task and nothing else; only a
        # whole array job's id takes all of its tasks
        mine = [r for r in rows.values() if r["JobID"] == jid] or \
               [r for r in rows.values() if r["JobID"].startswith(jid + "_")]
        if not mine:
            jobs.append({"key": key, "stage": key, "job": jid, "task": "", "state": "" if jid.startswith("dry") else "unknown", **flags})
            continue
        for r in sorted(mine, key=lambda x: x["JobID"]):
            task = r["JobID"].split("_", 1)[1] if "_" in r["JobID"] else ""
            facts = log_facts(rundir, spec.name, key, r["JobID"])
            steps, frames = steps_for(spec, key, nseg)
            if key == "mmgbsa":           # what MMPBSA.py says it scored; a replaced job's window is not the spec's
                frames = _mmgbsa_frames_run(rundir, spec.name, r["JobID"]) or (None if lj["replaced"] else frames)
            jobs.append({"key": key, "stage": key, "job": r["JobID"],
                         "task": "" if key in not_array else task, "state": r["State"],
                         "node": r["NodeList"] or facts.get("host", ""), "partition": r["Partition"],
                         "gpu": facts.get("gpu") or gpu_type(r["AllocTRES"]), "cpu": facts.get("cpu", ""),
                         "cpus": r["ReqCPUS"], "steps": steps, "frames": frames,
                         "elapsed_s": r["ElapsedRaw"], "elapsed": _hms(r["ElapsedRaw"]),
                         "ns_per_day": None if lj["replaced"] else _nsday(rundir, key, task),
                         "start": r["Start"], "end": r["End"], **flags})
    done = [j for j in jobs if j.get("state") in FINAL]
    complete = bool(jobs) and all(j.get("state") in FINAL for j in jobs)
    # the whole graph's span: its jobs as they stand, not a video queued a week later
    span = [j for j in done if j["current"] and j["key"] in in_graph] or done
    starts = [j["start"] for j in span if j.get("start") and j["start"] not in ("Unknown", "None")]
    ends = [j["end"] for j in span if j.get("end") and j["end"] not in ("Unknown", "None")]
    wall = 0
    if starts and ends:
        try:
            t0 = time.mktime(time.strptime(min(starts), "%Y-%m-%dT%H:%M:%S"))
            t1 = time.mktime(time.strptime(max(ends), "%Y-%m-%dT%H:%M:%S"))
            wall = max(0, int(t1 - t0))
        except ValueError:
            wall = 0
    d = derive(spec)
    out = {"jobs": jobs, "ids": [j["job"] for j in listed], "complete": complete, "wall_s": wall, "wall": _hms(wall),
           "busy_s": sum(j.get("elapsed_s", 0) for j in done), "busy": _hms(sum(j.get("elapsed_s", 0) for j in done)),
           "estimate": {"ns_per_day": d["ns_per_day"], "note": d["ns_per_day_note"], "hours_per_replicate": d["hours_per_replicate"],
                        "walltime": spec.compute.walltime or d["walltime"], "segments": d["segments"],
                        "stage_hours": d.get("stage_hours", {}), "stage_note": d.get("stage_note", "")}}
    if complete:
        try:
            cache.parent.mkdir(exist_ok=True)
            cache.write_text(json.dumps(out, indent=1))
        except OSError:
            pass                     # someone else's run folder, read-only: the table is still returned
    return out


#: the stage a job's row is filed under: production segments as one, media jobs by kind
STAGE_OF_KEY = (("prod_s", "prod"), ("video_", "video"), ("snap_", "snapshot"), ("dense_", "dense"))


def record_timings(rundir: str | Path, t: dict | None = None) -> list[str]:
    """One row per finished job in the timings.tsv of the run's runs folder, once (keyed run/job),
    when this account may write there. Returns the keys added. Needs the built system's atom count,
    so nothing is written before the build has run."""
    from .estimate import read_timings, append_timing, repo_benchmarks_path, run_tables
    rundir = Path(rundir)
    t = t or timing(rundir)
    try:
        atoms = json.loads((rundir / "build" / "system.json").read_text()).get("atoms")
    except (OSError, ValueError):
        atoms = None
    if not atoms:
        return []
    spec = Spec.from_json((rundir / "spec.json").read_text())
    site = spec.site
    device = f"cpu{site.cpu_cores_per_node}" if spec.on_cpu else spec.gpu
    table = run_tables(rundir)[1]
    have = {r.get("run") for r in read_timings(repo_benchmarks_path().with_name("timings.tsv")) + read_timings(table)}
    added = []
    for j in t.get("jobs", []):
        if j.get("state") != "COMPLETED" or not j.get("elapsed_s"):
            continue
        key = f"{rundir.name}/{j['job']}"
        if key in have:
            continue
        stage = next((st for prefix, st in STAGE_OF_KEY if j["key"].startswith(prefix)), j["key"])
        try:
            append_timing(site.key, spec.engine, device, stage, int(atoms), j.get("steps"), j.get("frames"),
                          int(j["elapsed_s"]), j.get("node", ""), j.get("partition", ""),
                          (j.get("end") or "")[:10] or time.strftime("%Y-%m-%d"), key, path=table)
        except OSError:
            break                  # a runs folder this account cannot write to: its owner's page or tidy job adds the rows
        have.add(key); added.append(key)
    return added


def record(rundir: str | Path) -> dict:
    """A run's rows in the tables of its runs folder, each once: one per finished job in timings.tsv,
    one per finished production part in benchmarks.tsv. The tidy job runs this before it packs, and
    the page when it opens a run. {"timings": keys added, "benchmarks": keys added, "folder"}."""
    from .queue import record_benchmarks
    rundir = Path(rundir).resolve()
    spec = Spec.from_json((rundir / "spec.json").read_text())
    return {"timings": record_timings(rundir), "benchmarks": record_benchmarks(rundir, spec), "folder": str(rundir.parent)}


def main(argv: list[str] | None = None) -> int:
    dirs = list(sys.argv[1:] if argv is None else argv)
    if not dirs:
        print(__doc__)
        return 2
    for d in dirs:
        try:
            got = record(d)
        except (OSError, ValueError) as e:
            print(f"[hopemd] timing {d}: not recorded ({e})")
            continue
        print(f"[hopemd] timing {d}: {len(got['timings'])} job row(s) and {len(got['benchmarks'])} speed row(s) added in {got['folder']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
