"""The viewer's waters: the strided view again, with the waters nearest the partner kept.

The Runs viewer plays analysis/r<i>_view.pdb, which the analysis job writes with every water and
ion stripped: a water box is tens of thousands of atoms a frame, which no browser plays. What a
person looking at an interface wants of the water is the few molecules at it, the ones that bridge
the two sides. cpptraj's `closest` keeps the N solvent molecules nearest a mask in each frame and
drops the rest, so this writes analysis/r<i>_view_waters.pdb: the same frames as the view (the
production parts in order, every `viewer_stride`-th), the solute as the view has it, and after it
the NEAREST waters nearest the partner, or nearest the receptor in a run with no partner.

What it is not, and the page says so beside the button: `closest` chooses again every frame and
renumbers what it keeps, so the waters are not followed from one frame to the next, and a water
that leaves the shell is simply another water in the next frame. It is a picture of the shell, not
a trajectory of any one molecule. A cpptraj job on the site's short partition, as the dense window
is (analysis/dense.py), and a few seconds to a minute of work.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from ..engines.base import Engine
from ..spec import Spec, trace_mask
from .. import queue as q
from .dense import frames_per_part

#: How many waters are kept in each frame: a shell around a peptide or a ligand, small enough that
#: the file is the view's size and a few percent.
NEAREST = 30
#: Water, by every name an engine here gives it: Amber's and the Amber-topology engines' WAT,
#: OpenMM's and a PDB's HOH, NAMD's TIP3, GROMACS's SOL, Desmond's SPC, T3P, T4P and T4PE.
WATER_MASK = ":WAT,HOH,TIP3,SOL,SPC,T3P,T4P,T4PE"


def output(rep: int) -> str:
    return f"analysis/r{rep}_view_waters.pdb"


def near_what(spec: Spec) -> tuple[str, str]:
    """(mask, words): the partner the waters are measured to, as the analysis masks it, or the
    receptor in a run with none."""
    s = spec.system
    rec = s.receptor_residues or [1, 1]
    if s.ligand_class == "none" or not s.ligand_residues:
        return f":{rec[0]}-{rec[1]}", "the receptor"
    lig = s.ligand_residues
    return f":{lig[0]}-{lig[1]}", "the partner" if s.ligand_class in ("protein", "nucleic_acid") else "the ligand"


def waters_script(rundir: Path, spec: Spec, rep: int, nearest: int = NEAREST) -> tuple[str, dict]:
    """(cpptraj input, facts) for one replicate."""
    from ..export.video import image_lines, strip_mask, topology_of
    topology = topology_of(spec)[0]
    parts = frames_per_part(rundir, rep)
    stride = int(spec.analysis.get("viewer_stride", 20))
    mask, words = near_what(spec)
    rec = spec.system.receptor_residues or [1, 1]
    frames = sum(n for _p, n in parts)
    # What the view strips, less the water: the ions, and in a bilayer the lipids, go as they do
    # from the view, and the waters `closest` kept stay.
    strip = f"({strip_mask(spec)})&!({WATER_MASK})"
    script = (f"# HOPE-MD viewer waters for {spec.name} r{rep}: the {nearest} waters nearest {words}, "
              f"every {stride} frames\n"
              f"parm {topology}\n"
              + "".join(f"trajin {p.as_posix()}\n" for p, n in parts if n)
              + "\n".join(image_lines(spec)) + "\n"
              f"solvent {WATER_MASK}\n"
              f"closest {nearest} {mask} first noimage\n"
              f"strip {strip}\n"
              # fitted on the receptor's trace to the first frame, as the analysis fits the view
              # it writes (RecFit): unfitted, the complex tumbled, and a frame of this file and the
              # same frame of the view were the same molecule turned up to 20 degrees apart
              f"rms WatFit :{rec[0]}-{rec[1]}{trace_mask(spec.system)} first\n"
              f"trajout {output(rep)} pdb offset {stride}\n"
              "run\n")
    return script, {"frames": (frames + stride - 1) // stride if frames else 0, "nearest": nearest,
                    "near": words, "stride": stride}


def job_script(spec: Spec, rep: int) -> str:
    # the short-queue request goes through sbatch_header's own `partition`, not a string replace
    # on the line it wrote: a site that names no partition has no such line to replace
    return (Engine.sbatch_header(spec, f"waters_r{rep}", gpu=False, hours=0.5, cpus=2, mem_gb=8,
                                 partition=spec.site.prep_partition)
            + Engine.preamble(spec, "amber")
            + f"cpptraj -i analysis/waters_r{rep}.cpptraj > analysis/waters_r{rep}.log 2>&1\n"
            f"echo \"[hopemd] viewer waters written: $(grep -c '^MODEL' {output(rep)}) frames\"\n")


def submit_waters(rundir: str | Path, rep: int, dry_run: bool = False) -> dict:
    rundir = Path(rundir)
    spec = Spec.from_json((rundir / "spec.json").read_text())
    script, facts = waters_script(rundir, spec, rep)
    if not facts["frames"]:
        return {"error": f"no production frames yet for replicate {rep}"}
    (rundir / "analysis").mkdir(exist_ok=True)
    (rundir / "analysis" / f"waters_r{rep}.cpptraj").write_text(script)
    (rundir / "jobs").mkdir(exist_ok=True)
    js = rundir / "jobs" / f"71_waters_r{rep}.sbatch"
    js.write_text(job_script(spec, rep))
    if dry_run or not q.sbatch_available():
        return {"job": "dry-waters", "output": output(rep), "dry_run": True, **facts}
    r = subprocess.run(["sbatch", "--parsable", js.relative_to(rundir).as_posix()], cwd=rundir,
                       capture_output=True, text=True, timeout=60)
    jid = r.stdout.strip().split(";")[0]
    if r.returncode != 0 or not jid:
        return {"error": f"sbatch refused the waters job: {r.stderr.strip() or 'no job id printed'}"}
    p = rundir / "analysis" / "waters_jobs.json"
    try:
        jobs = json.loads(p.read_text())
    except (OSError, ValueError):
        jobs = {}
    jobs[str(rep)] = {"job": jid, "submitted": time.strftime("%Y-%m-%d %H:%M:%S"), "file": output(rep), **facts}
    p.write_text(json.dumps(jobs, indent=2))
    return {"job": jid, "output": output(rep), "dry_run": False, **facts}


def waters_views(rundir: Path) -> list[dict]:
    """Per replicate asked for: the file when it is written, else the job and its state. A file
    written by hand or by an earlier job, with no record, is offered all the same."""
    rundir = Path(rundir)
    try:
        jobs = json.loads((rundir / "analysis" / "waters_jobs.json").read_text())
    except (OSError, ValueError):
        jobs = {}
    for f in sorted((rundir / "analysis").glob("r*_view_waters.pdb")):
        rep = f.name[1:].split("_", 1)[0]
        if rep.isdigit():
            jobs.setdefault(rep, {"file": f"analysis/{f.name}", "nearest": NEAREST})
    out = []
    for rep, j in sorted(jobs.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
        f = rundir / (j.get("file") or output(int(rep)))
        row = {"rep": int(rep), "file": j.get("file") or output(int(rep)), "nearest": j.get("nearest", NEAREST),
               "near": j.get("near", ""), "job": j.get("job", "")}
        if f.exists() and f.stat().st_size > 0:
            row.update(ready=True, size_mb=round(f.stat().st_size / 1e6, 1))
        else:
            row.update(ready=False, state=q.job_state(j.get("job", "")) or "gone")
        out.append(row)
    return out


if __name__ == "__main__":
    print(json.dumps(submit_waters(sys.argv[1], int(sys.argv[2])), indent=2))
