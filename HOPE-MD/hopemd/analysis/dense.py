"""The dense window: ±N ns around a frame at (close to) full save resolution.

The Runs viewer plays analysis/r<i>_view.pdb, every 20th frame with no water, which is right
for 100 ns and coarse for a moment. A request from the scrubber writes
analysis/r<i>_dense_<ns>.pdb from the production parts around that time, at the finest stride
that stays under `max_frames` (a 500-frame window at 4 000 atoms is 170 MB of PDB; 100 frames
is what a browser plays). A cpptraj job on the site's short partition, a few seconds of work.

Frames per part come from the part's .mdinfo (Completed steps / save steps), so the window
is cut with per-file `trajin start stop offset` lines and nothing has to be read twice.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

from ..engines.amber import SOLVENT_MASK
from ..engines.base import Engine
from ..spec import Spec
from .. import queue as q


TRAJ_SUFFIXES = (".nc", ".dcd", ".xtc", ".trr")


def frames_in_file(p: Path) -> int | None:
    """Frames in an Amber NetCDF or a DCD from its header alone: NetCDF classic keeps the
    record (frame) count in bytes 4-8 big-endian, a DCD keeps NSET at bytes 8-12. None when
    the file is not one of those or the count is the streaming marker."""
    try:
        with p.open("rb") as fh:
            head = fh.read(12)
    except OSError:
        return None
    if head[:3] == b"CDF":
        n = int.from_bytes(head[4:8], "big")
        return None if n == 0xFFFFFFFF else n
    if head[4:8] == b"CORD":
        return int.from_bytes(head[8:12], "little")
    if head[:4] == b"\x00\x00\x07\xcb":         # GROMACS xtc: magic 1995, then natoms, step, time; frames are walked
        return _xtc_frames(p)
    return None


def _xtc_frames(p: Path) -> int | None:
    """Frames in a GROMACS xtc by walking its frames: each starts with the magic, the atom
    count, the step and the time (4 big-endian words), then the box (9 floats), the atom count
    again, and the compressed coordinates whose byte count sits after the precision, the
    integer bounds and the small index (for 10 or more atoms; fewer are written whole)."""
    n = 0
    try:
        with p.open("rb") as fh:
            while True:
                head = fh.read(16)
                if len(head) < 16:
                    break
                if head[:4] != b"\x00\x00\x07\xcb":
                    break
                natoms = int.from_bytes(head[4:8], "big")
                fh.seek(9 * 4 + 4, 1)                       # box, atom count
                if natoms <= 9:
                    fh.seek(natoms * 12, 1)
                else:
                    fh.seek(4 + 3 * 4 + 3 * 4 + 4, 1)        # precision, minint, maxint, smallidx
                    nbytes = int.from_bytes(fh.read(4), "big")
                    fh.seek((nbytes + 3) // 4 * 4, 1)
                n += 1
    except OSError:
        return None
    return n


def frames_per_part(rundir: Path, rep: int) -> list[tuple[Path, int]]:
    """(part file, frames in it) for r<rep>/prod_s*_p*.nc|dcd in order: from the file's own
    header, else from the part's .mdinfo (Completed steps / save steps, which lags the file by
    up to one report interval), else OpenMM's progress file."""
    spec = Spec.from_json((rundir / "spec.json").read_text())
    save_steps = max(1, int(round(spec.protocol.save_ps * 1000 / spec.protocol.timestep_fs)))
    out = []
    parts = sorted([p for p in (rundir / f"r{rep}").glob("prod_s*_p*.*") if p.suffix in TRAJ_SUFFIXES],
                   key=lambda p: [int(x) for x in re.findall(r"\d+", p.name)])
    for p in parts:
        n = frames_in_file(p)
        if n is None:
            info = p.with_suffix(".mdinfo")
            done = 0
            if info.exists():
                m = re.search(r"Completed\s*:\s*(\d+)", info.read_text())
                done = int(m.group(1)) if m else 0
            else:
                pj = p.parent / (p.stem.rsplit("_p", 1)[0] + ".progress.json")   # OpenMM keeps a progress file per segment
                try:
                    done = json.loads(pj.read_text()).get("done", 0)
                except (OSError, ValueError):
                    done = 0
            n = max(0, done // save_steps)
        out.append((p, n))
    return out


def window(parts: list[tuple[Path, int]], save_ps: float, centre_ns: float, half_ns: float, max_frames: int) -> tuple[list[str], int, float, float]:
    """The trajin lines for the frames within ±half_ns of centre_ns, thinned to max_frames.
    Returns (lines, frames written, first ns, last ns)."""
    per_ns = 1000.0 / save_ps
    total = sum(n for _, n in parts)
    f0 = max(1, int(math.floor((centre_ns - half_ns) * per_ns)) + 1)
    f1 = min(total, int(math.ceil((centre_ns + half_ns) * per_ns)))
    if total == 0 or f1 < f0:
        return [], 0, 0.0, 0.0
    offset = max(1, int(math.ceil((f1 - f0 + 1) / max_frames)))
    lines, written, base = [], 0, 0
    for p, n in parts:
        lo, hi = base + 1, base + n
        s, e = max(f0, lo), min(f1, hi)
        if n and e >= s:
            lines.append(f"trajin {p.as_posix()} {s - base} {e - base} {offset}")
            written += (e - s) // offset + 1
        base += n
    return lines, written, (f0 - 1) / per_ns, f1 / per_ns


def dense_script(rundir: Path, spec: Spec, rep: int, centre_ns: float, half_ns: float = 5.0, max_frames: int = 100) -> tuple[str, str, dict]:
    """(cpptraj input, output path, facts) for the window."""
    from ..export.video import topology_of, strip_mask
    topology, strip = topology_of(spec)[0], strip_mask(spec)       # the engine's topology and what it calls water and ions
    parts = frames_per_part(rundir, rep)
    lines, written, ns0, ns1 = window(parts, spec.protocol.save_ps, centre_ns, half_ns, max_frames)
    tag = f"{centre_ns:g}".replace(".", "p")
    out = f"analysis/r{rep}_dense_{tag}ns.pdb"
    script = (f"# HOPE-MD dense window for {spec.name} r{rep}: {ns0:.2f}-{ns1:.2f} ns, {written} frames\n"
              f"parm {topology}\n" + "\n".join(lines) + "\n"
              "autoimage\n"
              f"strip {strip}\n"
              f"trajout {out} pdb\nrun\n")
    return script, out, {"frames": written, "ns0": round(ns0, 3), "ns1": round(ns1, 3), "centre_ns": centre_ns, "half_ns": half_ns}


def job_script(spec: Spec, rep: int, tag: str) -> str:
    # the short-queue request goes through sbatch_header's own `partition`, not a string replace
    # on the line it wrote: a site that names no partition has no such line to replace
    return (Engine.sbatch_header(spec, f"dense_r{rep}_{tag}", gpu=False, hours=0.5, cpus=2, mem_gb=8,
                                 partition=spec.site.prep_partition)
            + Engine.preamble(spec, "amber")
            + f"cpptraj -i analysis/dense_r{rep}_{tag}.cpptraj > analysis/dense_r{rep}_{tag}.log 2>&1\n"
            f"echo \"[hopemd] dense window written: $(grep -c '^MODEL' analysis/r{rep}_dense_{tag}ns.pdb) frames\"\n")


def submit_dense(rundir: str | Path, rep: int, centre_ns: float, half_ns: float = 5.0, max_frames: int = 100, dry_run: bool = False) -> dict:
    rundir = Path(rundir)
    spec = Spec.from_json((rundir / "spec.json").read_text())
    script, out, facts = dense_script(rundir, spec, rep, centre_ns, half_ns, max_frames)
    if not facts["frames"]:
        return {"error": f"no production frames near {centre_ns:g} ns yet for replicate {rep}"}
    tag = f"{centre_ns:g}".replace(".", "p")
    (rundir / "analysis").mkdir(exist_ok=True)
    (rundir / "analysis" / f"dense_r{rep}_{tag}.cpptraj").write_text(script)
    (rundir / "jobs").mkdir(exist_ok=True)
    js = rundir / "jobs" / f"70_dense_r{rep}_{tag}.sbatch"
    js.write_text(job_script(spec, rep, tag))
    if dry_run or not q.sbatch_available():
        return {"job": "dry-dense", "output": out, "dry_run": True, **facts}
    r = subprocess.run(["sbatch", "--parsable", js.relative_to(rundir).as_posix()], cwd=rundir, capture_output=True, text=True, timeout=60)
    jid = r.stdout.strip().split(";")[0]
    if r.returncode != 0 or not jid:
        return {"error": f"sbatch refused the dense-window job: {r.stderr.strip() or 'no job id printed'}"}
    p = rundir / "analysis" / "dense_jobs.json"
    try:
        jobs = json.loads(p.read_text())
    except (OSError, ValueError):
        jobs = {}
    jobs[out] = {"job": jid, "submitted": time.strftime("%Y-%m-%d %H:%M:%S"), "rep": rep, **facts}
    p.write_text(json.dumps(jobs, indent=2))
    return {"job": jid, "output": out, "dry_run": False, **facts}


def dense_windows(rundir: Path) -> list[dict]:
    """What the Runs screen offers beside the strided view: per window, the file when it is
    written, else the job and its state."""
    rundir = Path(rundir)
    p = rundir / "analysis" / "dense_jobs.json"
    try:
        jobs = json.loads(p.read_text())
    except (OSError, ValueError):
        jobs = {}
    out = []
    for path, j in jobs.items():
        f = rundir / path
        row = {"file": path, "rep": j.get("rep"), "centre_ns": j.get("centre_ns"), "half_ns": j.get("half_ns"), "job": j.get("job")}
        if f.exists() and f.stat().st_size > 0:
            row.update(ready=True, size_mb=round(f.stat().st_size / 1e6, 1), frames=j.get("frames"))
        else:
            row.update(ready=False, state=q.job_state(j.get("job", "")) or "gone")
        out.append(row)
    return out


if __name__ == "__main__":
    print(json.dumps(submit_dense(sys.argv[1], int(sys.argv[2]), float(sys.argv[3])), indent=2))
