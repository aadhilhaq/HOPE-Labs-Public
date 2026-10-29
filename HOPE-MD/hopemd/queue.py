"""Write a render to disk, submit its job graph, and read progress back.

Submission is `sbatch --parsable` with `--dependency` built from the graph, in order.
Progress comes from the engines' own files: Amber's .mdinfo (Completed / ns/day), OpenMM's
.progress.json and each engine's part_progress hook (the GROMACS, NAMD and Desmond logs), plus
squeue/sacct for the queue's view.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .engines.base import Render, cache_lines
from .spec import Spec

SLURM_STATES_DONE = {"COMPLETED"}
SLURM_STATES_BAD = {"FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED", "DEADLINE"}


def runs_root() -> Path:
    root = os.environ.get("HOPEMD_RUNS") or os.path.join(os.environ.get("SCRATCH", os.path.expanduser("~")), "hopemd_runs")
    return Path(root).expanduser()


def write_render(rundir: Path, spec: Spec, render: Render) -> list[str]:
    from .provenance import stamp
    rundir.mkdir(parents=True, exist_ok=True)
    stamp(spec, "submit", render)
    (rundir / "spec.json").write_text(spec.to_json())
    written = ["spec.json"]
    for f in render.files:
        p = rundir / f.path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f.text)
        if f.executable:
            p.chmod(0o755)
        written.append(f.path)
    (rundir / "logs").mkdir(exist_ok=True)
    (rundir / "jobs" / "graph.json").write_text(json.dumps(
        [{"key": j.key, "script": j.script, "depends": j.depends, "array": j.array, "gpu": j.gpu,
          "description": j.description} for j in render.jobs], indent=2))
    return written


def sbatch_available() -> bool:
    return shutil.which("sbatch") is not None


def prepare_job_script(spec: Spec, stage: Path, cpus: int = 8, mem_gb: int = 16, minutes: int = 60, chain: bool = False) -> str:
    """The Prepare stage as a short CPU job. Compute runs on compute nodes, never on a login
    node: the server submits this and the page polls. The interpreter is the server's own (it
    has OpenMM, PDBFixer and pdb2pqr); no engine module is loaded, so its PYTHONPATH stays its
    own. chain=True is the job that opens a run's graph (Submit without a Prepare first): it
    ends by re-rendering the run's files from the prepared structure (hopemd.rerender) so the
    build that follows has the right masks."""
    import sys
    site = spec.site
    home = str(Path(__file__).resolve().parent.parent)
    return (
        "#!/bin/bash\n"
        f"#SBATCH --job-name=hopemd_prepare_{stage.name}\n"
        # the same rule as every other job (site.partition_to_name): where the scheduler routes
        # on --time, name no queue rather than one that may be shorter than this job asks for
        + (lambda p: f"#SBATCH --partition={p}\n" if p else "")(
            site.partition_to_name(site.prep_partition, minutes / 60.0, gpu=False,
                                   pinned=bool(spec.compute.partition)))
        # the allocation the run is charged to, when one was named: this job as well as the rest
        + (f"#SBATCH --account={spec.compute.account}\n" if spec.compute.account else "")
        + "#SBATCH --nodes=1\n"
        "#SBATCH --ntasks=1\n"
        f"#SBATCH --cpus-per-task={cpus}\n"
        f"#SBATCH --mem={mem_gb}G\n"          # required on Grace, harmless elsewhere
        # an hour: the loops Prepare builds are relaxed in up to five attempts from each of three seeds, the attempts
        # side by side on the job's CPUs, one thread each (prepare.relax_built); 7A3R's five took five minutes
        f"#SBATCH --time={minutes // 60:02d}:{minutes % 60:02d}:00\n"
        "#SBATCH --output=logs/prepare.%j.out\n"
        "set -euo pipefail\n"
        "cd \"$SLURM_SUBMIT_DIR\"\n"
        "mkdir -p logs\n"
        "type module >/dev/null 2>&1 || source /etc/profile   # Lmod in a batch shell\n"
        "module purge\n"
        # nothing in $HOME, as in the run's own jobs (engines/base.py, cache_lines)
        + cache_lines("$SLURM_SUBMIT_DIR")
        + f"export PYTHONPATH=\"${{HOPEMD_HOME:-{home}}}:${{PYTHONPATH:-}}\"\n"
        "export OPENMM_CPU_THREADS=$SLURM_CPUS_PER_TASK\n"
        "echo \"[hopemd] $(date) host=$(hostname) job=${SLURM_JOB_ID:-none} prepare in $SLURM_SUBMIT_DIR\"\n"
        f"\"${{HOPEMD_PYTHON:-{sys.executable}}}\" -m hopemd.prepare .\n"
        + (f"\"${{HOPEMD_PYTHON:-{sys.executable}}}\" -m hopemd.rerender .\n" if chain else "")
        + "echo \"[hopemd] prepare done\"\n"
    )


SLURM_STATES_LIVE = {"PENDING", "RUNNING", "REQUEUED", "SUSPENDED", "COMPLETING", "CONFIGURING", "RESIZING"}


_GPU_CACHE: dict = {"t": 0.0, "key": "", "value": {}}


def parse_sinfo_gres(text: str) -> dict:
    """sinfo -h -N -O Partition,NodeList,Gres,GresUsed,StateCompact -> {type: {free, total, nodes,
    partition}}. A card is free when its node is idle or mixed and has fewer in use than it
    carries; a drained, down or fully allocated node offers none."""
    out: dict[str, dict] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        partition, gres, used, state = parts[0], parts[2], parts[3], parts[4]
        m = re.search(r"gpu:([A-Za-z0-9_.-]+):(\d+)", gres)
        if not m:
            continue
        kind, total = m.group(1), int(m.group(2))
        mu = re.search(r"gpu:" + re.escape(kind) + r":(\d+)", used)
        busy = int(mu.group(1)) if mu else 0
        d = out.setdefault(kind, {"free": 0, "total": 0, "nodes": 0, "partition": partition.rstrip("*")})
        d["total"] += total
        if state.startswith(("idle", "mix")) and total > busy:
            d["free"] += total - busy
            d["nodes"] += 1
    return out


def gpu_availability(site, max_age_s: float = 20.0) -> dict:
    """Free GPUs by type on the site's GPU partitions, right now, from sinfo (cached a few
    seconds: the page asks on every keystroke); {} without Slurm."""
    parts = [p.name for p in site.partitions.values() if p.gpu]
    if not parts or shutil.which("sinfo") is None:
        return {}
    key = f"{site.key}:{','.join(parts)}"
    now = time.time()
    if _GPU_CACHE["key"] == key and now - _GPU_CACHE["t"] < max_age_s:
        return _GPU_CACHE["value"]
    try:
        r = subprocess.run(["sinfo", "-h", "-N", "-p", ",".join(parts),
                            "-O", "Partition:24,NodeList:16,Gres:40,GresUsed:60,StateCompact:14"],
                           capture_output=True, text=True, timeout=20)
        value = parse_sinfo_gres(r.stdout) if r.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired):
        value = {}
    _GPU_CACHE.update(t=now, key=key, value=value)
    return value


def job_state(jid: str) -> str:
    """One Slurm job's state, from squeue while it is live and sacct after; "" when unknown."""
    if not jid or jid.startswith("dry-"):
        return ""
    try:
        if shutil.which("squeue"):
            r = subprocess.run(["squeue", "-h", "-j", jid, "-o", "%T"], capture_output=True, text=True, timeout=30)
            st = r.stdout.strip().split("\n")[0].strip() if r.returncode == 0 else ""
            if st:
                return st
        if shutil.which("sacct"):
            r = subprocess.run(["sacct", "-n", "-P", "-X", "-j", jid, "-o", "State"], capture_output=True, text=True, timeout=30)
            st = r.stdout.strip().split("\n")[0].strip() if r.returncode == 0 else ""
            return st.split()[0] if st else ""
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def submit(rundir: Path, render: Render, dry_run: bool = False) -> dict:
    """Submit in graph order. Returns {"jobs": {key: id}, "dry_run": bool, "log": [...]}."""
    ids: dict[str, str] = {}
    log: list[str] = []
    for j in render.jobs:
        deps = ",".join(f"{mode}:{ids[key]}" for key, mode in j.depends if key in ids)
        cmd = ["sbatch", "--parsable"] + ([f"--dependency={deps}"] if deps else []) + [j.script]
        if dry_run or not sbatch_available():
            ids[j.key] = f"dry-{len(ids) + 1}"
            log.append("(dry) " + " ".join(cmd))
            continue
        try:
            out = subprocess.run(cmd, cwd=rundir, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.append(f"{j.key}: sbatch failed: {e}")
            break
        jid = out.stdout.strip().split(";")[0]
        if out.returncode != 0 or not jid:       # sbatch prints some refusals on stderr and exits 0
            log.append(f"{j.key}: sbatch refused: {out.stderr.strip() or 'no job id printed'}")
            break
        ids[j.key] = jid
        log.append(f"{j.key} -> {ids[j.key]}")
    (rundir / "jobs" / "ids.json").write_text(json.dumps({"jobs": ids, "submitted": time.strftime("%Y-%m-%d %H:%M:%S"),
                                                          "dry_run": dry_run or not sbatch_available()}, indent=2))
    return {"jobs": ids, "dry_run": dry_run or not sbatch_available(), "log": log}


def resubmit(rundir: Path, from_key: str, dry_run: bool = False) -> dict:
    """Submit `from_key` again and every job downstream of it, dependencies rebuilt: upstream
    jobs keep their ids (a dependency on a finished job is satisfied at once), the resubmitted
    ones get new ids. A production segment that was killed picks up from its last checkpoint
    (the script's own resume logic); the analysis and MM-GBSA follow the new segment id.
    jobs/ids.json keeps the replaced ids under "history"."""
    graph = json.loads((rundir / "jobs" / "graph.json").read_text())
    keys = [j["key"] for j in graph]
    if from_key not in keys:
        raise KeyError(f"no stage called {from_key!r}; this run has {', '.join(keys)}")
    old = _ids(rundir)
    redo = {from_key}
    changed = True
    while changed:                                   # the downstream closure
        changed = False
        for j in graph:
            if j["key"] not in redo and any(k in redo for k, _ in j["depends"]):
                redo.add(j["key"]); changed = True
    ids = dict(old)
    log: list[str] = []
    dry = dry_run or not sbatch_available()
    fresh: set[str] = set()
    for j in graph:
        if j["key"] not in redo:
            continue
        # a dependency on a job Slurm has already forgotten is refused ("Job dependency
        # problem"): keep the ones on jobs still in the queue and on the ids made here, drop
        # the rest, their work is done
        deps = ",".join(f"{mode}:{ids[key]}" for key, mode in j["depends"]
                        if key in ids and ids[key] and (key in fresh or job_state(ids[key]) in SLURM_STATES_LIVE))
        cmd = ["sbatch", "--parsable"] + ([f"--dependency={deps}"] if deps else []) + [j["script"]]
        if dry:
            ids[j["key"]] = f"dry-r{len(log) + 1}"; fresh.add(j["key"])
            log.append("(dry) " + " ".join(cmd))
            continue
        try:
            out = subprocess.run(cmd, cwd=rundir, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.append(f"{j['key']}: sbatch failed: {e}")
            break
        jid = out.stdout.strip().split(";")[0]
        if out.returncode != 0 or not jid:
            log.append(f"{j['key']}: sbatch refused: {out.stderr.strip() or 'no job id printed'}")
            break
        ids[j["key"]] = jid; fresh.add(j["key"])
        log.append(f"{j['key']} -> {ids[j['key']]}")
    p = rundir / "jobs" / "ids.json"
    try:
        prev = json.loads(p.read_text())
    except (OSError, ValueError):
        prev = {}
    history = prev.get("history", []) + [{"when": time.strftime("%Y-%m-%d %H:%M:%S"), "from": from_key,
                                          "replaced": {k: old[k] for k in sorted(redo, key=keys.index) if k in old}}]
    p.write_text(json.dumps({"jobs": ids, "submitted": prev.get("submitted", ""), "resubmitted": time.strftime("%Y-%m-%d %H:%M:%S"),
                             "dry_run": dry, "history": history}, indent=2))
    return {"jobs": ids, "resubmitted": sorted(redo, key=keys.index), "dry_run": dry, "log": log}


def cancel(rundir: Path) -> list[str]:
    ids = _ids(rundir)
    out = []
    for k, v in ids.items():
        if v.startswith("dry-"):
            continue
        r = subprocess.run(["scancel", v], capture_output=True, text=True)
        out.append(f"{k} {v}: {'canceled' if r.returncode == 0 else r.stderr.strip()}")
    return out


def _ids(rundir: Path) -> dict[str, str]:
    p = rundir / "jobs" / "ids.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text()).get("jobs", {})
    except ValueError:
        return {}


def queue_states(ids: dict[str, str]) -> dict[str, dict[str, int]]:
    """{job key: {STATE: count}} from sacct (covers finished jobs and array tasks)."""
    out: dict[str, dict[str, int]] = {}
    for k, tasks in task_states(ids).items():
        out[k] = {}
        for st in tasks.values():
            out[k][st] = out[k].get(st, 0) + 1
    return out


def _task_numbers(suffix: str) -> list[str]:
    """The tasks an sacct JobID's suffix stands for: "" for a job that is not an array, "3" for
    task 3, and each task of a pending array's "[1-3,5%2]"."""
    if not suffix.startswith("["):
        return [suffix]
    out = []
    for part in suffix.strip("[]").split("%")[0].split(","):
        a, _, b = part.partition("-")
        if a.isdigit() and (not b or b.isdigit()):
            out.extend(str(i) for i in range(int(a), int(b or a) + 1))
    return out


def task_states(ids: dict[str, str]) -> dict[str, dict[str, str]]:
    """{job key: {task: STATE}} from one sacct call: each task of an array job under its number,
    "" for a job that is not an array. The Runs rail reads a replicate's own state from it."""
    real = [v for v in ids.values() if v and not v.startswith("dry-")]
    if not real or shutil.which("sacct") is None:
        return {}
    try:
        r = subprocess.run(["sacct", "-n", "-P", "-X", "-j", ",".join(real), "-o", "JobID,State"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    by_id: dict[str, dict[str, str]] = {}
    for line in r.stdout.splitlines():
        if "|" not in line or not line.split("|", 1)[1].split():
            continue
        jid, state = line.split("|", 1)
        base, _, suffix = jid.partition("_")
        for t in _task_numbers(suffix):
            by_id.setdefault(base, {})[t] = state.split()[0]
        if suffix:                     # also under the task's own id, for a stage that is one task
            by_id.setdefault(jid, {})[""] = state.split()[0]
    return {k: by_id.get(v, {}) for k, v in ids.items()}


def _mdinfo_progress(path: Path) -> tuple[int, float | None]:
    """(completed steps, ns/day) from an Amber mdinfo."""
    try:
        text = path.read_text()
    except OSError:
        return 0, None
    m = re.search(r"Completed\s*:\s*(\d+)", text)
    done = int(m.group(1)) if m else 0
    m2 = re.search(r"ns/day\s*=\s*([0-9.]+)", text)
    return done, (float(m2.group(1)) if m2 else None)


def progress(rundir: Path) -> dict:
    """Per-replicate production progress in ns, plus the stage view the Runs rail draws."""
    spec = Spec.from_json((rundir / "spec.json").read_text())
    pro = spec.protocol
    dt_ns = pro.timestep_fs * 1e-6
    graph = json.loads((rundir / "jobs" / "graph.json").read_text()) if (rundir / "jobs" / "graph.json").exists() else []
    nseg = sum(1 for j in graph if j["key"].startswith("prod_s"))
    seg_steps = int(round(pro.length_ns / max(nseg, 1) * 1e6 / pro.timestep_fs))
    reps = []
    from .engines import engine_for
    try:
        eng = engine_for(spec.engine)
    except ValueError:
        eng = None
    for i in range(1, pro.replicates + 1):
        r = rundir / f"r{i}"
        done_steps = 0.0
        nsday = None
        seg_done = [0.0] * nseg                  # each segment's own steps, for its row in the rail
        for k in range(1, nseg + 1):
            if any((r / f"prod_s{k}.{ext}").exists() for ext in ("rst7", "xml", "cpt", "coor", "done")):
                done_steps += seg_steps          # a finished segment leaves its checkpoint (engine-specific name) behind
                seg_done[k - 1] = seg_steps
                continue
            before = done_steps
            for f in sorted(r.glob(f"prod_s{k}_p*.mdinfo")):
                d, n = _mdinfo_progress(f)
                done_steps += d
                nsday = n or nsday
            if eng is not None:                 # the engine's own view of a running part (GROMACS, NAMD, Desmond logs)
                stems = sorted({p.with_suffix("") for p in list(r.glob(f"prod_s{k}_p*.{eng.TRAJ}")) + list(r.glob(f"prod_s{k}_p*.log"))
                                if re.fullmatch(r"prod_s\d+_p\d+", p.stem)})            # a part's own files only, never grompp_<part>.log and the like
                for stem in stems:              # the hook takes the trajectory's path and reads the log beside it, present or not yet
                    got = eng.part_progress(stem.with_suffix("." + eng.TRAJ))
                    if got:
                        d, n = got
                        done_steps += d
                        nsday = n or nsday
            pj = r / f"prod_s{k}.progress.json"
            if pj.exists():
                try:
                    done_steps += json.loads(pj.read_text()).get("done", 0)
                except ValueError:
                    pass
            seg_done[k - 1] = done_steps - before
            break
        for f in r.glob("prod_s*_p*.nsday"):
            try:
                nsday = float(f.read_text().strip())
            except ValueError:
                pass
        equil_done = any((r / f"eq4.{ext}").exists() for ext in ("rst7", "xml", "coor", "cms", "gro"))   # each engine's own last-stage file
        seg_ns = pro.length_ns / max(nseg, 1)
        reps.append({"replicate": i, "ns_done": round(min(done_steps * dt_ns, pro.length_ns), 2),
                     "ns_total": pro.length_ns, "ns_per_day": nsday, "equilibrated": equil_done,
                     "segments_ns": [round(min(x * dt_ns, seg_ns), 2) for x in seg_done], "segment_ns": round(seg_ns, 3),
                     "analysed": (rundir / "analysis" / f"r{i}_rmsd_rec.dat").exists(),
                     "mmgbsa": (rundir / "analysis" / f"r{i}_mmgbsa.dat").exists()})
    built = (rundir / "build" / "complex.prmtop").exists() or (rundir / "build" / "complex.cms").exists()   # Desmond builds a cms
    ids = _ids(rundir)
    tasks = task_states(ids)
    states = {k: {st: list(m.values()).count(st) for st in set(m.values())} for k, m in tasks.items()}
    system = _read_json(rundir / "build" / "system.json")
    recorded = record_benchmarks(rundir, spec, system)
    return {"built": built, "replicates": reps, "jobs": ids, "states": states,
            "stages": stage_rows(rundir, graph, ids, tasks, reps, built),
            "segments": nseg, "system": system, "benchmarks_recorded": recorded}


#: what the rail says for a Slurm state
WORDS = {"RUNNING": "running", "COMPLETING": "running", "CONFIGURING": "running", "PENDING": "queued", "REQUEUED": "queued",
         "SUSPENDED": "suspended", "RESIZING": "queued", "COMPLETED": "done", "TIMEOUT": "timed out", "CANCELLED": "canceled",
         "OUT_OF_MEMORY": "out of memory"}
BAD_WORDS = ("failed", "timed out", "canceled", "out of memory")


def stage_rows(rundir: Path, graph: list[dict], ids: dict[str, str], tasks: dict[str, dict[str, str]],
               reps: list[dict], built: bool) -> list[dict]:
    """The Runs rail: one row per job of the graph, and per replicate of an array job, in the order
    a run goes through them: Prepare, the parameters, the build, each replicate's ladder and
    production segments, the analysis, each replicate's MM-GBSA, tidy, then any job queued beside
    the graph (an undo of tidy). {key, task, label, text, frac, bad} each; the state is Slurm's for
    that job or task, or, where the accounting has nothing, what the run folder shows."""
    keys = [j["key"] for j in graph] + [k for k in ids if k not in {j["key"] for j in graph}]
    arrays = {j["key"]: j.get("array") for j in graph}
    segs = [k for k in keys if k.startswith("prod_s")]
    n = len(reps)
    tidied = (rundir / "archive" / "tidy.json").exists()

    def word(key: str, task: str, evidence: bool) -> str:
        jid = str(ids.get(key, ""))
        st = tasks.get(key, {}).get(task, "")
        if st:
            return WORDS.get(st, "failed" if st in FAILED_STATES else st.lower())
        if evidence:
            return "done"
        if not jid:
            return "—"
        return "dry run" if jid.startswith("dry") else "queued"

    def row(key: str, task: str, label: str, text: str, frac: float, w: str) -> dict:
        return {"key": key, "task": int(task or 1), "label": label, "text": text, "frac": round(max(0.0, min(frac, 1.0)), 3),
                "bad": w in BAD_WORDS}

    out: list[dict] = []
    for key in keys:
        if key in segs:
            continue                                     # with each replicate's ladder, below
        if key == "equil":
            for rep in reps:
                i = str(rep["replicate"])
                w = word("equil", i, rep["equilibrated"])
                out.append(row("equil", i, f"equil r{i}", w, 1.0 if w == "done" else 0.0, w))
                for k, seg in enumerate(segs):
                    done = rep["segments_ns"][k] if k < len(rep["segments_ns"]) else 0.0
                    w = word(seg, i, done >= rep["segment_ns"] > 0)
                    text = f"{done:g} / {rep['segment_ns']:g} ns"
                    if w == "running" and rep["ns_per_day"]:
                        text += f" · {rep['ns_per_day']} ns/d"
                    elif w not in ("done", "running"):
                        text += f" · {w}"
                    out.append(row(seg, i, f"prod s{k + 1} r{i}" if len(segs) > 1 else f"prod r{i}", text,
                                   done / rep["segment_ns"] if rep["segment_ns"] else 0.0, w))
            continue
        if key == "analysis":
            got = sum(1 for rep in reps if rep["analysed"])
            w = word("analysis", "", n > 0 and got == n)
            out.append(row(key, "", "analysis", f"{got}/{n} · {w}", got / n if n else 0.0, w))
            continue
        if arrays.get(key) or key == "mmgbsa":
            # a job queued before Prepare found the receptor and partner linked ends at once, as it should (MMGBSA_GUARD)
            refused = key == "mmgbsa" and (rundir / "analysis" / "mmgbsa_refused.txt").exists()
            for rep in reps:
                i = str(rep["replicate"])
                w = word(key, i, key == "mmgbsa" and rep["mmgbsa"])
                if refused and w == "done" and not rep["mmgbsa"]:
                    w = "not run: covalently linked"
                label = ("MM-GBSA" if key == "mmgbsa" else key) + (f" r{i}" if n > 1 else "")
                out.append(row(key, i, label, w, 1.0 if w == "done" else 0.0, w))
            continue
        w = word(key, "", (key == "build" and built) or (key == "tidy" and tidied))
        out.append(row(key, "", {"untidy": "undo tidy"}.get(key, key), w, 1.0 if w == "done" else 0.0, w))
    return out


def record_benchmarks(rundir: Path, spec: Spec, system: dict | None = None) -> list[str]:
    """Every finished production part writes its ns/day to r<i>/prod_s<k>_p<n>.nsday; each
    one becomes a row in benchmarks.tsv once (keyed by run/replicate/part in the `run`
    column), which is what estimate.py reads for the next run's walltime. The row goes to the
    table of the runs folder holding the run (estimate.run_tables), when this account may write
    there. Returns the keys it added. Nothing is written until the build has recorded the atom
    count."""
    from .estimate import read_rows, append_row, repo_benchmarks_path, run_tables, speed_engine
    system = system if system is not None else _read_json(rundir / "build" / "system.json")
    atoms = (system or {}).get("atoms")
    if not atoms:
        return []
    files = sorted(rundir.glob("r*/prod_s*_p*.nsday"))
    if not files:
        return []
    table = run_tables(rundir)[0]
    have = {r.get("run") for r in read_rows(repo_benchmarks_path()) + read_rows(table)}
    site = spec.site
    if spec.on_cpu:
        gpu = f"cpu{site.cpu_cores_per_node}"
    else:
        gpu = spec.gpu
    added = []
    for f in files:
        key = f"{rundir.name}/{f.parent.name}/{f.stem}"
        if key in have:
            continue
        try:
            nsday = float(f.read_text().strip())
        except ValueError:
            continue
        if nsday <= 0:
            continue
        try:
            # GROMACS with a four-site water is filed apart from its three-site runs (estimate.speed_engine)
            append_row(site.key, gpu, speed_engine(spec.engine, spec.forcefield.water, spec.on_cpu), int(atoms),
                       spec.protocol.timestep_fs, nsday,
                       time.strftime("%Y-%m-%d", time.localtime(f.stat().st_mtime)), key, path=table)
        except OSError:
            break                  # a runs folder this account cannot write to: its owner's page or tidy job adds the rows
        have.add(key); added.append(key)
    return added


def _read_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


FAILED_STATES = ("FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "BOOT_FAIL", "DEADLINE", "PREEMPTED")
_STATES_CACHE: dict = {"t": 0.0, "key": "", "value": {}}


def overall_state(states: dict[str, dict[str, int]], ids: dict[str, str]) -> str:
    """One word for a run's graph: running (a job is on a node), failed (a job ended badly and
    nothing is running), pending (something is queued), done (every job completed), dry (a
    dry run), none (not submitted), unknown (the accounting knows nothing of it)."""
    if not ids:
        return "none"
    if any(v.startswith("dry-") for v in ids.values()):
        return "dry"
    seen = [st for m in states.values() for st in m]
    if not seen:
        return "unknown"
    if "RUNNING" in seen or "COMPLETING" in seen:
        return "running"
    if any(st.split()[0] in FAILED_STATES for st in seen):
        return "failed"
    if any(st in ("PENDING", "REQUEUED", "SUSPENDED", "RESIZING") for st in seen):
        return "pending"
    if all(st == "COMPLETED" for st in seen) and len(states) >= len(ids):
        return "done"
    return "pending" if len(states) < len(ids) else "unknown"


def states_of_runs(runs: list[tuple[str, dict[str, str]]], max_age_s: float = 20.0) -> dict[str, str]:
    """{run name: overall state} from one sacct call over every job of every run (cached a few
    seconds: the page lists runs often); dry and unsubmitted runs need no call."""
    real = sorted({v for _, ids in runs for v in ids.values() if not v.startswith("dry-")})
    key = ",".join(real)
    now = time.time()
    if key and _STATES_CACHE["key"] == key and now - _STATES_CACHE["t"] < max_age_s:
        by_id = _STATES_CACHE["value"]
    else:
        by_id: dict[str, dict[str, int]] = {}
        if real and shutil.which("sacct"):
            try:
                r = subprocess.run(["sacct", "-n", "-P", "-X", "-j", key, "-o", "JobID,State"], capture_output=True, text=True, timeout=60)
                for line in r.stdout.splitlines():
                    if "|" not in line:
                        continue
                    jid, state = line.split("|", 1)
                    st = state.split()[0]
                    # under both the array job's id and the task's own "<id>_<task>": a stage that
                    # is one task of an array shared with other runs is named by the task, and
                    # counting it under the array alone gave every run of that array one state
                    for k in {jid.split("_")[0], jid}:
                        by_id.setdefault(k, {})
                        by_id[k][st] = by_id[k].get(st, 0) + 1
            except (OSError, subprocess.TimeoutExpired):
                by_id = {}
        _STATES_CACHE.update(t=now, key=key, value=by_id)
    out = {}
    for name, ids in runs:
        states = {k: by_id[v] for k, v in ids.items() if v in by_id}
        out[name] = overall_state(states, ids)
    return out


def list_runs(root: Path | None = None) -> list[dict]:
    root = root or runs_root()
    out = []
    if not root.exists():
        return out
    for d in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        sp = d / "spec.json"
        if not sp.exists():
            continue
        try:
            spec = Spec.from_json(sp.read_text())
        except ValueError:
            continue
        ids = _ids(d)
        out.append({"name": d.name, "rundir": str(d), "engine": spec.engine,
                    "length_ns": spec.protocol.length_ns, "replicates": spec.protocol.replicates,
                    "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(d.stat().st_mtime)),
                    "submitted": bool(ids), "dry_run": any(v.startswith("dry-") for v in ids.values()), "_ids": ids})
    states = states_of_runs([(r["name"], r.pop("_ids")) for r in out])
    for r in out:
        r["state"] = states.get(r["name"], "none")
    return out


def stage_log(rundir: Path, stage: str, task: int = 1, n: int = 120, job: str = "") -> dict:
    """What one stage of a run is doing: the newest log of that stage (an array stage's task), or
    that of the job `job` names (one a resubmission replaced, an earlier video), its last lines,
    and the job's state, node and elapsed time from the queue. The page polls this while the job
    runs. A job outside the graph (a video, the Setup screen's Prepare) is known by its log."""
    name = rundir.name
    ids = _ids(rundir)
    logs = rundir / "logs"
    cands: list[Path] = []
    if logs.exists():
        if stage == "prepare":
            cands = list(logs.glob("prepare.*.out"))
        else:
            cands = [p for p in logs.glob(f"{name}_{stage}.*.out")]
        if job:
            cands = [p for p in cands if p.name.rsplit(".", 2)[-2].split("_")[0] == job]
        if any(p.name.rsplit(".", 2)[-2].count("_") for p in cands):     # an array stage: this task's files
            cands = [p for p in cands if p.name.rsplit(".", 2)[-2].endswith(f"_{task}")] or cands
    cands.sort(key=lambda p: p.stat().st_mtime)
    jid = str(job or ids.get(stage, "") or (cands[-1].name.rsplit(".", 2)[-2].split("_")[0] if cands else ""))
    out: dict = {"stage": stage, "task": task, "job": jid, "file": "", "lines": [], "state": "", "node": "", "elapsed": ""}
    if jid and not jid.startswith("dry"):
        array = stage in ("equil", "mmgbsa") or stage.startswith("prod_s") or any("_" in p.name.rsplit(".", 2)[-2] for p in cands)
        q_id = f"{jid}_{task}" if array else jid
        out["state"] = job_state(q_id) or job_state(jid) or "unknown"
        if shutil.which("sacct"):
            try:
                r = subprocess.run(["sacct", "-n", "-P", "-X", "-j", q_id, "-o", "NodeList,Elapsed"], capture_output=True, text=True, timeout=30)
                first = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ""
                if "|" in first:
                    out["node"], out["elapsed"] = first.split("|", 1)
            except (OSError, subprocess.TimeoutExpired):
                pass
    if cands:
        p = cands[-1]
        try:
            text = p.read_text(errors="replace")
            out["file"] = f"logs/{p.name}"; out["size"] = p.stat().st_size
            out["lines"] = text.splitlines()[-n:]
        except OSError:
            pass
    if stage.startswith("prod_s") and not out["lines"]:
        pass
    out["done"] = out["state"] in ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "unknown", "")
    return out


def run_logs(rundir: Path) -> list[dict]:
    """Every Slurm log of the run, newest first, for the Log tail's list: {file, stage, task, job,
    array}. A job's log is logs/<run>_<stage>.<job>[_<task>].out, the Prepare job's
    logs/prepare.<job>.out, so the jobs beside the graph (videos, snapshots, dense windows, the
    Setup screen's Prepare) and those a resubmission replaced are listed too."""
    name = rundir.name
    folder = rundir / "logs"
    found = sorted(folder.glob("*.out"), key=lambda p: p.stat().st_mtime, reverse=True) if folder.is_dir() else []
    out = []
    for p in found:
        if p.name.startswith("prepare."):
            stage, mid = "prepare", p.name[len("prepare."):-len(".out")]
        elif p.name.startswith(name + "_"):
            stage, _, mid = p.name[len(name) + 1:-len(".out")].rpartition(".")
        else:
            continue
        job, _, task = mid.partition("_")
        if stage and job.isdigit():
            out.append({"file": f"logs/{p.name}", "stage": stage, "task": int(task) if task.isdigit() else 1,
                        "job": job, "array": task.isdigit()})
    return out


def log_tail(rundir: Path, n: int = 40) -> str:
    logs = sorted((rundir / "logs").glob("*.out"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        return ""
    p = logs[0]
    try:
        lines = p.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return f"{p.name}\n" + "\n".join(lines[-n:])
