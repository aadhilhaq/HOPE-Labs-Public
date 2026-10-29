"""The trajectory as two videos: the solute alone, and the solute in its water and ions.

A CPU queue job, from the Runs screen's Video button or by hand:

    python3 -m hopemd.export.video <rundir> [replicate]     # writes the files and submits

The job takes the production trajectory itself (every part of every segment, Amber NetCDF or
OpenMM DCD), not the analysis' strided view: cpptraj images the box around the solute and
writes about TARGET_FRAMES evenly spaced frames, once with everything (a DCD the topology
reads) and once stripped of water and ions (a multi-model PDB). VMD in text mode writes every
frame of each as a POV-Ray scene in the page's color convention (receptor gray, the thing
being simulated rust, the interface teal, water pale blue, ions amber), POV-Ray renders it,
FFmpeg assembles export/<run>_r<i>.mp4 and export/<run>_r<i>_water.mp4 at 1920x1080. (Grace's
VMD carries no Tachyon and its sources are unreachable from the cluster; POV-Ray is in the
same module toolchain.) cpptraj, VMD and FFmpeg come from the site's modules (site.py:
"amber", "vmd", "ffmpeg"); they need different toolchains on Grace, so the job loads them
one after the other.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

from ..brand import PALETTE, METALS
from ..engines.base import Engine, cache_lines
from ..spec import Spec
from .. import queue as q

FPS = 12
TARGET_FRAMES = 120          # about ten seconds of video, whatever the run's length

TCL = r'''# HOPE-MD movie for @@RUN@@, replicate @@REP@@, @@MODE@@: receptor gray, the thing being simulated
# rust, the interface (receptor residues within 4.5 A of the partner, followed frame by frame)
# teal@@WATERNOTE@@. Off screen: VMD writes each frame as a POV-Ray scene and POV-Ray renders it.
# Run it as
#   vmd -dispdev text -eofexit < this.tcl
# (fed on stdin: with -e the script runs inside VMD's event loop, which exits at end of
# input before the last commands have run).
set outdir "@@OUTDIR@@"
file mkdir $outdir
color change rgb 10 @@REC_RGB@@
color change rgb 11 @@LIG_RGB@@
color change rgb 12 @@IFACE_RGB@@
color change rgb 13 @@WATER_RGB@@
color change rgb 14 @@ION_RGB@@
color change rgb 15 @@LIPID_RGB@@
color Display Background white
display projection Perspective
display depthcue off
display rendermode Normal
display shadows off
display ambientocclusion off
axes location Off
display resize 1920 1080
@@LOAD@@
mol delrep 0 top
mol representation NewCartoon 0.3 10.0 4.1 0
mol color ColorID 10
mol selection "@@RECSEL@@"
mol material Opaque
mol addrep top
mol representation @@LIGREP@@
mol color ColorID 11
mol selection "@@LIGSEL@@"
mol addrep top
mol representation Licorice 0.2 12.0 12.0
mol color ColorID 12
mol selection "@@IFACESEL@@"
mol addrep top
mol selupdate 2 top 1
@@WATERREPS@@@@METALREPS@@display resetview
@@SIDEVIEW@@scale by @@SCALE@@
proc fixcam {f} {
  # POV-Ray 3.7.0.10 (the Grace module) refuses a camera without an explicit angle, "Viewing
  # angle has to be smaller than 180", and VMD writes none: add the one its vectors imply.
  set fh [open $f r]; set txt [read $fh]; close $fh
  if {![regexp {right <([-0-9.]+),} $txt -> rx]} { return }
  set dz 1.0
  regexp {direction <[-0-9.]+, [-0-9.]+, ([-0-9.]+)>} $txt -> dz
  if {$dz == 0} { set dz 1.0 }
  set ang [expr {2.0 * atan(abs($rx) / 2.0 / abs($dz)) * 180.0 / 3.14159265}]
  regsub {camera \{} $txt [format "camera \{\n  angle %.4f" $ang] txt
  set fh [open $f w]; puts -nonewline $fh $txt; close $fh
}
set n [molinfo top get numframes]
puts "rendering $n frames (@@MODE@@)"
for {set f 0} {$f < $n} {incr f} {
  animate goto $f
  display update
  set base [format "%s/f%04d" $outdir $f]
  render POV3 $base.pov
  fixcam $base.pov
  if {[catch {exec povray +WT@@THREADS@@ +W1920 +H1080 -I$base.pov -O$base.tga -D @@POVQ@@ +FT -GA 2> $base.err} msg]} {
    puts "povray failed on frame $f: $msg"
  } else {
    file delete $base.pov $base.err
  }
}
puts "rendered $n frames (@@MODE@@)"
quit
'''

# VMD's selection language reads + and - as operators, so an ion name carrying one is quoted;
# a selection it cannot parse is not an error but a representation of every atom (the ion
# representation would draw the whole box as amber spheres).
# Lipids as sand-colored lines before the water, so a bilayer reads as a slab through the
# box; a water box has none and the representation is empty. The names: the membrane build
# keeps one residue per lipid under its full name (POPC ...), which a PDB truncates to three
# letters (POP ...); Lipid17's split residues (head groups PA PC PE PS PGR, tails OL ST LAL
# MY AR, cholesterol CHL) cover a topology tleap built.
WATERREPS = '''mol representation Lines 1.0
mol color ColorID 15
mol selection {resname POPC POPE DLPC DLPE DMPC DOPC DPPC POP DLP DMP DOP DPP PA PC PE PS PGR OL ST LAL MY AR CHL}
mol material Opaque
mol addrep top
mol representation Lines 1.0
mol color ColorID 13
mol selection "@@WATERSEL@@"
mol material Opaque
mol addrep top
mol representation VDW 0.6 12.0
mol color ColorID 14
mol selection {resname "Na+" "Cl-" "K+" NA CL K MG}
mol addrep top
'''

SOLVENT_MASK = ":WAT,HOH,TIP3,Na+,Cl-,K+,NA,CL,K,MG"

#: How close a residue's atom must be to a metal to be drawn as one of its ligands, re-chosen
#: every frame so a ligand that leaves is seen leaving. The first-shell distance, as the
#: analysis uses.
SITE_A = 3.0


def metals_of(rundir: Path, spec: Spec) -> list[str]:
    """The metal residue names the topology holds, in the order METALS lists them. Read off the
    topology, not the spec: a metal a prediction placed is in the build whatever the spec says."""
    top = Path(rundir) / topology_of(spec)[0]
    names: set[str] = set()
    try:
        text = top.read_text(errors="replace")
    except OSError:
        return []
    m = re.search(r"%FLAG RESIDUE_LABEL.*?%FORMAT\([^)]*\)\s*\n(.*?)\n%FLAG", text, re.S)
    if m:
        names = {t.strip() for t in m.group(1).split()}
    else:                                   # a PDB topology (Desmond's): residue names in columns 18-20
        names = {l[17:20].strip() for l in text.splitlines() if l.startswith(("ATOM", "HETATM"))}
    return [k for k in METALS if k in names]


def metal_reps(metals: list[str]) -> str:
    """Each metal a sphere in its own color, and the residues holding them as sticks colored by
    element, carbon gray. Color ids from 16 up: 10-15 are the scene's own."""
    if not metals:
        return ""
    lines = ["color Element C gray"]
    for i, name in enumerate(metals):
        cid = 16 + i
        lines += [f"color change rgb {cid} {_rgb(METALS[name][1])}",
                  "mol representation VDW 0.9 24.0",
                  f"mol color ColorID {cid}",
                  f"mol selection {{resname {name}}}",
                  "mol material Opaque",
                  "mol addrep top"]
    sel = " ".join(metals)
    lines += ["mol representation Licorice 0.22 16.0 16.0",
              "mol color Element",
              f"mol selection {{not resname {sel} and not water and noh and same residue as "
              f"(within {SITE_A} of (resname {sel}))}}",
              "mol addrep top",
              "mol selupdate [expr {[molinfo top get numreps] - 1}] top 1"]
    return "\n".join(lines) + "\n"


def _rgb(hexs: str) -> str:
    return " ".join(f"{int(hexs[i:i + 2], 16) / 255:.3f}" for i in (1, 3, 5))


def trajectory_files(rundir: Path, rep: int) -> list[Path]:
    """Every production part of the replicate in order: prod_s<k>_p<n>.nc (Amber) or .dcd (OpenMM)."""
    def key(p: Path):
        m = re.match(r"prod_s(\d+)_p(\d+)", p.stem)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    r = rundir / f"r{rep}"
    return sorted([p for p in r.glob("prod_s*_p*.*") if p.suffix in (".nc", ".dcd", ".xtc", ".trr")], key=key) if r.exists() else []


def topology_of(spec: Spec) -> tuple[str, str]:
    """(file, VMD type) the trajectory is read with: the engine's (a prmtop for the Amber-built
    engines, a PDB for Desmond)."""
    from ..engines import engine_for
    try:
        e = engine_for(spec.engine)
        return e.TOPOLOGY, e.TOPOLOGY_TYPE
    except ValueError:
        return "build/complex.prmtop", "parm7"


def parts_of(spec: Spec, rundir: Path) -> dict | None:
    """The receptor and the partner as runs of chain letter and residue number, on an engine whose
    topology keeps the input's (Desmond: {"rec": [{"chain": "A", "range": [274, 899]}], "lig": ...});
    None where the topology numbers its residues as the spec's ranges do."""
    from ..engines.base import own_ranges
    s = spec.system
    own = own_ranges(rundir, spec, [s.receptor_residues, s.ligand_residues])
    return {"rec": own[0], "lig": own[1]} if own else None


def by_chain(runs: list[dict]) -> str:
    """A VMD selection of residue runs by chain letter and number, one bracket per run when there are
    several. VMD parses a negative number only in quotes (resid '-3' to 5; checked with 1.9.4a57)."""
    num = lambda n: f"'{n}'" if n < 0 else str(n)   # noqa: E731
    terms = [(f"chain {r['chain']} and " if r["chain"] else "") + f"resid {num(r['range'][0])} to {num(r['range'][1])}" for r in runs]
    return terms[0] if len(terms) == 1 else " or ".join(f"({t})" for t in terms)


def stride_for(total_frames: int) -> int:
    return max(1, total_frames // TARGET_FRAMES)


def strip_mask(spec: Spec) -> str:
    """What the solute-only video drops: water and ions, or, in a bilayer or beside the ions the build
    carries, everything after the solute and those ions (lipids, crystal waters, water, the salt: the
    build numbers the solute first and its ions after it, so a carried ion stays whatever its name)."""
    s = spec.system
    from ..engines.base import carried_ions, kept_last
    if getattr(s, "environment", "water") == "membrane" or carried_ions(spec):
        return f"!(:1-{kept_last(spec)})"
    from ..engines import engine_for
    try:
        return engine_for(spec.engine).STRIP
    except ValueError:
        return SOLVENT_MASK


def image_lines(spec: Spec) -> list[str]:
    """How cpptraj images the frames the views are cut from. A compact box is a truncated octahedron on the
    four Amber-topology engines, and cpptraj takes it for one only in Amber's NetCDF: it reads GROMACS's xtc
    cell as triclinic, and OpenMM's and NAMD's DCD cell as rhombohedral, and images those into a skewed or a
    near-cubic block (GROMACS's water then spanned 115 Å where Amber's spans 85, and VMD drew it smaller).
    The cell is given the octahedron's angles and imaged into its familiar shape, so every engine's water
    looks alike. Desmond builds an orthorhombic box, and a bilayer's is rectangular."""
    if spec.box.shape == "compact" and getattr(spec.system, "environment", "water") != "membrane" and spec.engine != "desmond":
        return ["box truncoct", "autoimage familiar"]
    return ["autoimage"]


def cpptraj_script(files: list[str], stride: int, outdir: str, topology: str = "build/complex.prmtop", strip: str = SOLVENT_MASK,
                   image: list[str] | None = None) -> str:
    """The frames both videos share: imaged (image_lines), evenly thinned, once whole and once solute only."""
    L = [f"parm {topology}"]
    L += [f"trajin {f} 1 last {stride}" for f in files]
    L += [*(image or ["autoimage"]),
          f"outtraj {outdir}/solv.dcd dcd",
          f"strip {strip}",
          f"outtraj {outdir}/dry.pdb pdb",
          "run"]
    return "\n".join(L) + "\n"


def tcl_script(spec: Spec, rep: int, mode: str, outdir: str, threads: int = 16, parts: dict | None = None,
               metals: list[str] | None = None) -> str:
    """mode 'dry' (the solute from dry.pdb) or 'water' (everything, from the topology and solv.dcd).
    parts: the receptor and the partner by chain letter and number (parts_of), for a topology that
    keeps the input's; else the spec's ranges, which the topology numbers 1, 2, 3 ..."""
    s = spec.system
    rec = s.receptor_residues or [1, 1]
    lig = s.ligand_residues or [rec[1] + 1, rec[1] + 1]
    partner = s.ligand_class in ("protein", "nucleic_acid")
    # nothing is being simulated against the receptor (a lone protein, a bilayer run): no partner
    # representation and no interface, or the first lipid would be drawn as the partner
    has_lig = bool(s.ligand_residues) and s.ligand_class != "none"
    rec_sel = f"resid {rec[0]} to {rec[1]}"
    lig_sel = f"resid {lig[0]} to {lig[1]}" if has_lig else "none"
    if parts and parts.get("rec"):
        # Desmond's build/system.pdb, and the frames cpptraj cuts from it, carry the input's chain letters
        # and residue numbers, not the ranges' 1, 2, 3 ...
        rec_sel = by_chain(parts["rec"])
        if has_lig and parts.get("lig"):
            lig_sel = by_chain(parts["lig"])
    iface_sel = f"({rec_sel}) and noh and same residue as (within 4.5 of ({lig_sel}))" if has_lig else "none"
    if metals and has_lig:
        # a metal's ligand is drawn once, by element, not again in teal on the same atoms
        iface_sel += f" and not same residue as (within {SITE_A} of (resname {' '.join(metals)}))"
    frames = outdir.rsplit("/", 1)[0]
    if mode == "water":
        top, ttype = topology_of(spec)
        # a PDB topology (Desmond's) comes with coordinates, which VMD keeps as a first frame: the built system's,
        # not the trajectory's, so a snapshot's first frame would be the build and its last the trajectory's first
        drop = "animate delete all\n" if ttype == "pdb" else ""
        load = f'mol new "{top}" type {ttype} waitfor all\n{drop}mol addfile "{frames}/solv.dcd" type dcd waitfor all'
    else:
        load = f'mol new "{frames}/dry.pdb" type pdb waitfor all'
    fill = {"RUN": spec.name, "REP": str(rep), "MODE": mode, "OUTDIR": outdir, "LOAD": load,
            "WATERNOTE": ", water pale blue as lines, ions amber" if mode == "water" else "",
            "WATERREPS": WATERREPS if mode == "water" else "",
            "METALREPS": metal_reps(metals or []),
            # Desmond names TIP3P and the four-site waters T3P, T4P and T4PE, which VMD's "water" leaves out (SPC it takes)
            "WATERSEL": "water or resname T3P T4P T4PE" if spec.engine == "desmond" else "water",
            "SCALE": "0.95" if mode == "water" else "1.15",
            # a bilayer is looked at from the side, the membrane normal (z) up the screen, so the
            # two leaflets and the water above and below read as such; VMD's reset view looks down z
            "SIDEVIEW": "rotate x by -90\n" if getattr(s, "environment", "water") == "membrane" else "",
            "POVQ": "-A +Q5" if mode == "water" else "+A0.3",      # anti-aliasing costs most; the water pass has ten times the objects
            "REC_RGB": _rgb(PALETTE["receptor"][0]), "LIG_RGB": _rgb(PALETTE["rust"][0]), "IFACE_RGB": _rgb(PALETTE["teal"][0]),
            "WATER_RGB": "0.600 0.780 0.920", "ION_RGB": _rgb(PALETTE["amber"][0]), "LIPID_RGB": "0.850 0.750 0.450",
            "RECSEL": rec_sel, "LIG0": str(lig[0]), "LIG1": str(lig[1]), "LIGSEL": lig_sel, "IFACESEL": iface_sel,
            "LIGREP": "NewCartoon 0.3 10.0 4.1 0" if partner else "Licorice 0.3 12.0 12.0", "THREADS": str(threads)}
    text = TCL
    for k, v in fill.items():
        text = text.replace(f"@@{k}@@", v)
    assert "@@" not in text, "an unfilled token in the movie script"
    return text


def outputs(spec: Spec, rep: int) -> dict:
    return {"dry": f"export/{spec.name}_r{rep}.mp4", "water": f"export/{spec.name}_r{rep}_water.mp4"}


def job_script(spec: Spec, rep: int, files: list[str]) -> str:
    site = spec.site
    out = outputs(spec, rep)
    fr = f"export/frames_r{rep}"
    py = sys.executable or "python3"     # the server's interpreter: it has PIL, the modules' do not
    ov = (f'"{py}" export/movie_overlay.py export/movie_r{rep}_overlay.json {{mode}} {fr}/{{mode}} {fr}/dry.pdb '
          f'|| {{{{ echo "[hopemd] the clock and legend could not be drawn ({{mode}})"; exit 5; }}}}\n')
    ff = ("ffmpeg -y -loglevel error -framerate {fps} -i {src}/f%04d.png "
          "-vf \"scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:white,format=yuv420p\" "
          "-c:v libx264 -crf 18 -movflags +faststart {dst}\n")
    return (Engine.sbatch_header(spec, f"video_r{rep}", gpu=False, hours=4.0, cpus=16, mem_gb=32)
            + "set -euo pipefail\n"
            "RUNDIR=${HOPEMD_RUNDIR:-$SLURM_SUBMIT_DIR}\n"
            "cd \"$RUNDIR\"\n"
            f"mkdir -p logs export {fr}/dry {fr}/water\n"
            "type module >/dev/null 2>&1 || source /etc/profile   # Lmod in a batch shell\n"
            + cache_lines() +   # nothing in $HOME (VMD's Mesa, among others, keeps a cache)
            f"for f in {' '.join(files)}; do test -s \"$f\" || {{ echo \"[hopemd] no $f: the production has not written it\"; exit 2; }}; done\n"
            f"echo \"[hopemd] $(date) host=$(hostname) job=${{SLURM_JOB_ID:-none}} two videos for replicate {rep}\"\n"
            f"{site.module_block('amber')}\n"
            f"cpptraj -i export/movie_r{rep}.cpptraj > export/movie_r{rep}.cpptraj.log 2>&1 || {{ echo '[hopemd] cpptraj failed; see export/movie_r{rep}.cpptraj.log'; exit 3; }}\n"
            f"test -s {fr}/dry.pdb && test -s {fr}/solv.dcd || {{ echo '[hopemd] cpptraj wrote no frames; see export/movie_r{rep}.cpptraj.log'; exit 3; }}\n"
            f"echo \"[hopemd] $(date) frames cut: $(grep -c '^MODEL' {fr}/dry.pdb)\"\n"
            f"{site.module_block('vmd')}\n"
            f"vmd -dispdev text -eofexit < export/movie_r{rep}_dry.tcl > export/movie_r{rep}_dry.log 2>&1\n"
            f"ls {fr}/dry/*.tga >/dev/null 2>&1 || {{ echo '[hopemd] VMD rendered nothing (dry); see export/movie_r{rep}_dry.log'; exit 4; }}\n"
            f"echo \"[hopemd] $(date) $(ls {fr}/dry/*.tga | wc -l) frames rendered without water\"\n"
            f"vmd -dispdev text -eofexit < export/movie_r{rep}_water.tcl > export/movie_r{rep}_water.log 2>&1\n"
            f"ls {fr}/water/*.tga >/dev/null 2>&1 || {{ echo '[hopemd] VMD rendered nothing (water); see export/movie_r{rep}_water.log'; exit 4; }}\n"
            f"echo \"[hopemd] $(date) $(ls {fr}/water/*.tga | wc -l) frames rendered with water\"\n"
            + ov.format(mode="dry") + ov.format(mode="water")
            + f"{site.module_block('ffmpeg')}\n"
            + ff.format(fps=FPS, src=f"{fr}/dry", dst=out["dry"])
            + ff.format(fps=FPS, src=f"{fr}/water", dst=out["water"])
            + f"rm -rf {fr}\n"
            f"echo \"[hopemd] $(date) videos written: {out['dry']} ($(( $(stat -c %s {out['dry']}) / 1048576 )) MB), {out['water']} ($(( $(stat -c %s {out['water']}) / 1048576 )) MB)\"\n")


def _font() -> str:
    """A TrueType font for the overlay: matplotlib's DejaVu Sans, which every install of the
    server carries, rather than PIL's bitmap default, which is unreadable at 1920 wide."""
    try:
        import matplotlib
        f = Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf" / "DejaVuSans.ttf"
        if f.exists():
            return str(f)
    except Exception:
        pass
    return "DejaVuSans.ttf"


def frame_times(rundir: Path, files: list[str], stride: int, save_ps: float) -> list[float]:
    """The production time, in ns from the start of production, of every frame the cut keeps.

    cpptraj takes frames 1, 1+stride, ... of each file in turn, so these are read at the same
    indices out of each file's own clock (Amber NetCDF's `time`, in ps). A clock assumed from the
    save interval is how a 20 ns run came to be labelled 40 ns: its trajectory was written every
    10 ps and the label counted 20. A file with no clock (OpenMM's DCD) falls back to save_ps."""
    from ..analysis.dense import frames_in_file
    out: list[float] = []
    t0 = None
    for f in files:
        times = None
        try:
            from scipy.io import netcdf_file
            nc = netcdf_file(str(Path(rundir) / f), "r", mmap=False)
            if "time" in nc.variables:
                times = [float(x) for x in nc.variables["time"].data]
            nc.close()
        except Exception:
            times = None
        if times is None:
            n = int(frames_in_file(Path(rundir) / f) or 0)
            base = (out[-1] * 1000.0 + save_ps) if out else 0.0
            times = [base + i * save_ps for i in range(n)]
        picked = times[::max(1, stride)]
        if t0 is None and picked:
            t0 = picked[0]
        out += [(t - t0) / 1000.0 for t in picked]
    return out


#: what the legend says each thing is, beside its color, in the order it is drawn
def legend_items(spec: Spec, metals: list[str], mode: str) -> list[tuple[str, str, str]]:
    """[(label, hex, "sphere" | "stick" | "line")] for the overlay."""
    s = spec.system
    items = [("receptor", PALETTE["receptor"][0], "line")]
    if s.ligand_residues and s.ligand_class != "none":
        items += [("partner" if s.ligand_class in ("protein", "nucleic_acid") else "ligand",
                   PALETTE["rust"][0], "line"),
                  ("interface residues", PALETTE["teal"][0], "stick")]
    items += [(METALS[m][0], METALS[m][1], "sphere") for m in metals]
    if metals:
        # the residues holding a metal, and then only the elements they actually contain: the
        # overlay reads those off the first frame, so a carboxylate site is not given a sulfur
        items += [("residues holding a metal", "#808080", "stick"), ("@site-elements", "", "")]
    if mode == "water":
        items += [("water", "#99c7eb", "line"), ("Na+ / Cl-", PALETTE["amber"][0], "sphere")]
    return items


OVERLAY = r'''"""Burn the clock and the legend into every rendered frame of one video."""
import glob, json, sys
from PIL import Image, ImageDraw, ImageFont
spec = json.load(open(sys.argv[1]))
mode, src = sys.argv[2], sys.argv[3]
ELEMENTS = [("O", "oxygen", "#d62728"), ("N", "nitrogen", "#2457c5"), ("S", "sulfur", "#d4a017")]


def site_elements(pdb, metals, cut=3.0):
    """The elements of the residues within `cut` of a metal in ANY frame drawn: the scene picks
    the ligands again every frame, so an element that arrives late still belongs in the legend."""
    have, atoms, mets = set(), [], []

    def close_frame():
        near = set()
        for _, _, (mx, my, mz) in mets:
            for key, _, (x, y, z) in atoms:
                if (x - mx) ** 2 + (y - my) ** 2 + (z - mz) ** 2 < cut * cut:
                    near.add(key)
        have.update(el for key, el, _ in atoms if key in near)

    for line in open(pdb):
        if line.startswith("ENDMDL"):
            close_frame(); atoms, mets = [], []
            continue
        if not line.startswith(("ATOM", "HETATM")):
            continue
        res, name = line[17:20].strip(), line[12:16].strip()
        el = (line[76:78].strip() or name[:1]).upper()
        xyz = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
        (mets if res in metals else atoms).append(((line[21], line[22:27]), el, xyz))
    if atoms:
        close_frame()
    return have


have = site_elements(sys.argv[4], set(spec.get("metals", []))) if len(sys.argv) > 4 else set()
font = ImageFont.truetype(spec["font"], 34)
small = ImageFont.truetype(spec["font"], 26)
frames = sorted(glob.glob(src + "/f*.tga"))
times = spec["times"]
for k, path in enumerate(frames):
    im = Image.open(path).convert("RGB")
    d = ImageDraw.Draw(im)
    d.text((40, 30), spec["title"] + ("  \u00b7  in water" if mode == "water" else ""),
           fill=(15, 23, 25), font=small)
    if k < len(times):
        d.text((40, 70), "%.1f ns" % times[k], fill=(15, 23, 25), font=font)
    items = []
    for it in spec["items_" + mode]:
        if it[0] == "@site-elements":
            items += [(word, hexc, "stick") for el, word, hexc in ELEMENTS if el in have]
        else:
            items.append(it)
    y = im.height - 40 - 38 * len(items)
    for label, hexc, kind in items:
        c = tuple(int(hexc[i:i + 2], 16) for i in (1, 3, 5))
        if kind == "sphere":
            d.ellipse((40, y + 4, 66, y + 30), fill=c)
        elif kind == "stick":
            d.rounded_rectangle((38, y + 13, 68, y + 21), radius=4, fill=c)
        else:
            d.line((38, y + 17, 68, y + 17), fill=c, width=6)
        d.text((80, y), label, fill=(15, 23, 25), font=small)
        y += 38
    im.save(path[:-4] + ".png")
print("overlaid %d frames (%s)" % (len(frames), mode))
'''


def render_video(spec: Spec, rep: int = 1, files: list[str] | None = None, stride: int = 1,
                 parts: dict | None = None, metals: list[str] | None = None) -> list[tuple[str, str]]:
    """(path, text) for the cpptraj script, the two Tcl scripts and the job script."""
    files = files or [f"r{rep}/prod_s1_p1.nc"]
    fr = f"export/frames_r{rep}"
    return [(f"export/movie_r{rep}.cpptraj", cpptraj_script(files, stride, fr, topology=topology_of(spec)[0], strip=strip_mask(spec),
                                                            image=image_lines(spec))),
            (f"export/movie_r{rep}_dry.tcl", tcl_script(spec, rep, "dry", f"{fr}/dry", parts=parts, metals=metals)),
            (f"export/movie_r{rep}_water.tcl", tcl_script(spec, rep, "water", f"{fr}/water", parts=parts, metals=metals)),
            (f"export/movie_overlay.py", OVERLAY),
            (f"jobs/60_video_r{rep}.sbatch", job_script(spec, rep, files))]


def submit_video(rundir: str | Path, rep: int = 1, dry_run: bool = False) -> dict:
    """Write the files into the run folder and queue the job. Returns {"job", "files", "output", "outputs"}."""
    from ..analysis.dense import frames_in_file
    rundir = Path(rundir)
    spec = Spec.from_json((rundir / "spec.json").read_text())
    traj = trajectory_files(rundir, rep)
    if not traj and not dry_run:
        return {"error": f"no production trajectory in r{rep}/ yet (prod_s*_p*.nc or .dcd): the videos are cut from it"}
    total = 0
    for p in traj:
        try:
            total += int(frames_in_file(p) or 0)
        except Exception:
            pass
    stride = stride_for(total) if total else 1
    rel = [str(p.relative_to(rundir)) for p in traj]
    metals = metals_of(rundir, spec)
    files = render_video(spec, rep, rel or None, stride, parts=parts_of(spec, rundir), metals=metals)
    overlay = {"title": f"{spec.name}, replicate {rep}", "metals": metals,
               "font": _font(), "times": frame_times(rundir, rel, stride, spec.protocol.save_ps),
               "items_dry": legend_items(spec, metals, "dry"),
               "items_water": legend_items(spec, metals, "water")}
    files.append((f"export/movie_r{rep}_overlay.json", json.dumps(overlay)))
    for path, text in files:
        p = rundir / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    # the job script by its name, not by its place: a file written after it (the overlay's data)
    # once made the last entry a JSON file, and sbatch was handed that
    script = next(f for f, _ in files if f.endswith(".sbatch"))
    out = outputs(spec, rep)
    base = {"files": [f for f, _ in files], "output": out["dry"], "outputs": out, "frames_in": total, "stride": stride,
            "frames_out": (total // stride) if total else None}
    if dry_run or not q.sbatch_available():
        return {**base, "job": "dry-video", "dry_run": True}
    r = subprocess.run(["sbatch", "--parsable", script], cwd=rundir, capture_output=True, text=True, timeout=60)
    jid = r.stdout.strip().split(";")[0]
    if r.returncode != 0 or not jid:
        return {"error": f"sbatch refused the video job: {r.stderr.strip() or 'no job id printed'}", "files": base["files"]}
    p = rundir / "export" / "video_jobs.json"
    try:
        jobs = json.loads(p.read_text())
    except (OSError, ValueError):
        jobs = {}
    jobs[f"r{rep}"] = {"job": jid, "submitted": time.strftime("%Y-%m-%d %H:%M:%S"), "output": out["dry"], "outputs": out}
    p.write_text(json.dumps(jobs, indent=2))
    return {**base, "job": jid, "dry_run": False}


def videos(rundir: Path) -> dict:
    """What the Runs screen and the report show: per replicate, the job (if any) and the files
    (if written): {"r1": {"file", "size_mb", "water_file", "water_size_mb", "job", "submitted", "state"}}."""
    rundir = Path(rundir)
    p = rundir / "export" / "video_jobs.json"
    try:
        jobs = json.loads(p.read_text())
    except (OSError, ValueError):
        jobs = {}
    out: dict = {}
    for mp4 in sorted((rundir / "export").glob("*_r*.mp4")) if (rundir / "export").exists() else []:
        tail = mp4.stem.rsplit("_r", 1)[1]
        water = tail.endswith("_water")
        key = "r" + (tail[:-len("_water")] if water else tail)
        d = out.setdefault(key, {})
        d["water_file" if water else "file"] = f"export/{mp4.name}"
        d["water_size_mb" if water else "size_mb"] = round(mp4.stat().st_size / 1e6, 1)
    for key, j in jobs.items():
        out.setdefault(key, {}).update(job=j.get("job"), submitted=j.get("submitted"))
        if "file" not in out[key] or "water_file" not in out[key]:
            out[key]["state"] = q.job_state(j.get("job", "")) or "gone"
    return out


if __name__ == "__main__":
    rd = sys.argv[1] if len(sys.argv) > 1 else "."
    print(json.dumps(submit_video(rd, int(sys.argv[2]) if len(sys.argv) > 2 else 1), indent=2))
