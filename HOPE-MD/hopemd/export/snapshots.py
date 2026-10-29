"""Still images of a run: the first and the last frame of a replicate, the solute alone and
the solute in its water (and lipids), as PNGs for the report:

    export/snap_r<i>_first_dry.png   export/snap_r<i>_last_dry.png
    export/snap_r<i>_first_water.png export/snap_r<i>_last_water.png

    python3 -m hopemd.export.snapshots <rundir> [replicate]     # writes the files and submits

The same machinery as the videos (video.py: cpptraj cuts the frames from the production
trajectory, VMD writes POV-Ray scenes in the page's colors, POV-Ray renders) on two frames,
so it is a CPU job of a few minutes; FFmpeg turns the frames into PNGs.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from ..engines.base import Engine, cache_lines
from ..spec import Spec
from .. import queue as q
from .video import trajectory_files, tcl_script, strip_mask, topology_of, parts_of, image_lines

WIDTH = 1280


def cpptraj_script(files: list[str], outdir: str, strip: str, topology: str = "build/complex.prmtop",
                   image: list[str] | None = None) -> str:
    """Two frames: the first of the first part and the last of the last part, imaged as the videos are (image_lines)."""
    L = [f"parm {topology}", f"trajin {files[0]} 1 1", f"trajin {files[-1]} lastframe",
         *(image or ["autoimage"]), f"outtraj {outdir}/solv.dcd dcd", f"strip {strip}", f"outtraj {outdir}/dry.pdb pdb", "run"]
    return "\n".join(L) + "\n"


def outputs(rep: int) -> dict:
    return {f"{when}_{mode}": f"export/snap_r{rep}_{when}_{mode}.png" for when in ("first", "last") for mode in ("dry", "water")}


def job_script(spec: Spec, rep: int, files: list[str]) -> str:
    site = spec.site
    fr = f"export/snap_r{rep}"
    out = outputs(rep)
    png = "".join(f"ffmpeg -y -loglevel error -i {fr}/{mode}/f{i:04d}.tga -vf scale={WIDTH}:-2 {out[f'{when}_{mode}']}\n"
                  for mode in ("dry", "water") for i, when in enumerate(("first", "last")))
    return (Engine.sbatch_header(spec, f"snap_r{rep}", gpu=False, hours=0.5, cpus=8, mem_gb=16)
            + "set -euo pipefail\n"
            "RUNDIR=${HOPEMD_RUNDIR:-$SLURM_SUBMIT_DIR}\n"
            "cd \"$RUNDIR\"\n"
            f"mkdir -p logs export {fr}/dry {fr}/water\n"
            "type module >/dev/null 2>&1 || source /etc/profile   # Lmod in a batch shell\n"
            + cache_lines() +   # nothing in $HOME (VMD's Mesa, among others, keeps a cache)
            f"echo \"[hopemd] $(date) host=$(hostname) job=${{SLURM_JOB_ID:-none}} snapshots for replicate {rep}\"\n"
            f"{site.module_block('amber')}\n"
            f"cpptraj -i {fr}.cpptraj > {fr}.cpptraj.log 2>&1 || {{ echo '[hopemd] cpptraj failed; see {fr}.cpptraj.log'; exit 3; }}\n"
            f"{site.module_block('vmd')}\n"
            f"vmd -dispdev text -eofexit < {fr}_dry.tcl > {fr}_dry.log 2>&1\n"
            f"vmd -dispdev text -eofexit < {fr}_water.tcl > {fr}_water.log 2>&1\n"
            f"ls {fr}/dry/f0001.tga {fr}/water/f0001.tga >/dev/null 2>&1 || {{ echo '[hopemd] VMD rendered fewer than two frames; see {fr}_dry.log and {fr}_water.log'; exit 4; }}\n"
            f"{site.module_block('ffmpeg')}\n"
            + png
            + f"rm -rf {fr}\n"
            f"echo \"[hopemd] $(date) snapshots written: {' '.join(out.values())}\"\n")


def render_snapshots(spec: Spec, rep: int = 1, files: list[str] | None = None, parts: dict | None = None) -> list[tuple[str, str]]:
    files = files or [f"r{rep}/prod_s1_p1.nc"]
    fr = f"export/snap_r{rep}"
    return [(f"{fr}.cpptraj", cpptraj_script(files, fr, strip_mask(spec), topology=topology_of(spec)[0], image=image_lines(spec))),
            (f"{fr}_dry.tcl", tcl_script(spec, rep, "dry", f"{fr}/dry", threads=8, parts=parts)),
            (f"{fr}_water.tcl", tcl_script(spec, rep, "water", f"{fr}/water", threads=8, parts=parts)),
            (f"jobs/61_snap_r{rep}.sbatch", job_script(spec, rep, files))]


def submit_snapshots(rundir: str | Path, rep: int = 1, dry_run: bool = False) -> dict:
    rundir = Path(rundir)
    spec = Spec.from_json((rundir / "spec.json").read_text())
    traj = trajectory_files(rundir, rep)
    if not traj and not dry_run:
        return {"error": f"no production trajectory in r{rep}/ yet: the snapshots are cut from it"}
    files = render_snapshots(spec, rep, [str(p.relative_to(rundir)) for p in traj] or None, parts=parts_of(spec, rundir))
    for path, text in files:
        p = rundir / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    base = {"files": [f for f, _ in files], "outputs": outputs(rep)}
    if dry_run or not q.sbatch_available():
        return {**base, "job": "dry-snap", "dry_run": True}
    r = subprocess.run(["sbatch", "--parsable", files[-1][0]], cwd=rundir, capture_output=True, text=True, timeout=60)
    jid = r.stdout.strip().split(";")[0]
    if r.returncode != 0 or not jid:
        return {"error": f"sbatch refused the snapshot job: {r.stderr.strip() or 'no job id printed'}", "files": base["files"]}
    p = rundir / "export" / "snapshot_jobs.json"
    try:
        jobs = json.loads(p.read_text())
    except (OSError, ValueError):
        jobs = {}
    jobs[f"r{rep}"] = {"job": jid, "submitted": time.strftime("%Y-%m-%d %H:%M:%S")}
    p.write_text(json.dumps(jobs, indent=2))
    return {**base, "job": jid, "dry_run": False}


def snapshots(rundir: Path) -> dict:
    """{ "r1": {"first_dry": "export/...png", ...}, ... } for the files that exist."""
    rundir = Path(rundir)
    out: dict = {}
    for png in sorted((rundir / "export").glob("snap_r*_*_*.png")) if (rundir / "export").exists() else []:
        rep, when, mode = png.stem.split("_")[1:4]
        out.setdefault(rep, {})[f"{when}_{mode}"] = f"export/{png.name}"
    return out


if __name__ == "__main__":
    rd = sys.argv[1] if len(sys.argv) > 1 else "."
    print(json.dumps(submit_snapshots(rd, int(sys.argv[2]) if len(sys.argv) > 2 else 1), indent=2))
