"""GROMACS: Amber's build converted by ParmEd, gmx runs it, cpptraj and MMPBSA.py analyze.

One topology for the four Amber-topology engines: the build job is Amber's (tleap or the
membrane builder, HMR by parmed) and ends by converting build/complex[_hmr].prmtop +
complex.inpcrd into build/complex.top and complex.gro with ParmEd (build/to_gromacs.py), so
GROMACS runs the same ff19SB/OPC (or ff14SB/TIP3P, lipid17, GAFF2) parameters Amber runs, and
cpptraj and MMPBSA.py read the xtc parts with the prmtop as they read Amber's NetCDF. The build
job then runs gmx grompp on the converted input, so a topology it cannot read fails on the build
node and not at the head of the GPU queue.

What this renderer adds to Amber's run folder::

    build/to_gromacs.py     the conversion: complex.top, complex.gro, posres_*.itp, index.ndx
    md/01_min.mdp ...       the same ladder as Amber's mdin files: minimize, heat, eq1-4, prod
    r<i>/<stage>.{tpr,cpt,gro,log,edr}; prod_s<k>_p<n>.xtc; prod_s<k>.cpt once a segment is done

Position restraints go through the topology: every solute moleculetype gets a
[ position_restraints ] include under #ifdef POSRES, the force constant POSRES_FC comes from
each mdp's define line (kcal/mol/A^2 x 418.4 = kJ/mol/nm^2) and the reference is the build's
complex.gro (grompp -r). A production part runs with -maxh from the walltime so it ends
cleanly before Slurm would kill it; the next part continues from its checkpoint (grompp -t,
init-step), so steps and times keep counting across parts, and the queue reads the part's
log for progress (part_progress below).

mdrun writes a frame at the step it starts from as well as every save interval after it. In a
segment's first part that frame is the one the segment before ended on (in the first segment,
the equilibrated structure), which Amber, OpenMM and NAMD do not write, so it is left out when
the part is made whole: frame k of the production is k save intervals in on every engine, and
the MM-GBSA window and the time axis count the same frames.

GROMACS 2024.4 on Grace is a thread-MPI CUDA build (gmx --version: MPI library thread_mpi, GPU
support CUDA): one rank drives the card with -ntmpi 1 and the job's cores as OpenMP threads.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..spec import Spec, CUTOFF_A, WATER_SITES, parse_walltime
from .amber import AmberEngine, CHECKPOINT_MINUTES, _resnum
from .base import Render, membrane_of

#: kcal/mol/A^2 -> kJ/mol/nm^2: 4.184 kJ per kcal, 100 A^2 per nm^2
KJ_NM2_PER_KCAL_A2 = 418.4
#: the spec's restraint weights are Amber's (E = k d^2, as pmemd's restraint_wt and NAMD's harmonic constraints);
#: GROMACS's position restraint is 1/2 k' d^2, so k' = 2 k. Without the factor 2 the GROMACS ladder would hold
#: the solute half as strongly as Amber's and NAMD's for the same number.
POSRES_PER_KCAL_A2 = 2 * KJ_NM2_PER_KCAL_A2
LADDER_DT_PS = 0.002          # the ladder runs at 2 fs on every engine; production at the spec's step
MAXWARN = 2                   # grompp warnings allowed: the 4 fs step with heavy hydrogens draws one or two
MAXH_MARGIN_H = 10.0 / 60.0   # a production part ends this long before the walltime (checkpoint, log, nsday)


# ---------------------------------------------------------------------- build/to_gromacs.py
# Rendered as a file and run by build.sh with the server's interpreter (ParmEd 4.3 there;
# AmberTools' parmed under the module is the fallback). Plain Python 3.8+: the fallback
# interpreter is whatever the engine module carries.
TO_GROMACS_PY = r'''#!/usr/bin/env python3
"""HOPE-MD: the Amber topology as GROMACS input, through ParmEd.

Reads the prmtop the build made (the HMR one when the run is at 4 fs) with its inpcrd and
writes, beside them: complex.top and complex.gro (one moleculetype per kind of molecule,
water with SETTLE, a four-site water's extra point as a virtual site), posres_<mol>.itp for
every solute moleculetype (its heavy atoms, indices relative to the moleculetype as GROMACS
wants them, force constant POSRES_FC from the mdp's define line, included in the .top under
#ifdef POSRES), posres_prod_<mol>.itp for the backbone of residues held in production
(#ifdef POSRES_PROD), and index.ndx with the groups the mdp files name.

Run from the run folder:
    python3 build/to_gromacs.py --prmtop build/complex_hmr.prmtop --inpcrd build/complex.inpcrd \
        --out build --solute-residues 270
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import parmed as pmd
from parmed.topologyobjects import AtomType, ExtraPoint
from parmed.utils import tag_molecules

WATER_NAMES = {"WAT", "HOH", "SOL", "TIP3", "OPC", "TP3", "T4E"}
BACKBONE = ("CA", "C", "N", "P", "C4'")      # a held amino acid by CA, C and N, a nucleotide by P and C4'


def log(*a):
    print("[hopemd]", *a, flush=True)


def parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prmtop", required=True)
    ap.add_argument("--inpcrd", required=True)
    ap.add_argument("--out", default="build")
    ap.add_argument("--solute-residues", type=int, required=True,
                    help="the solute is residues 1..N of the topology (receptor, then partner or ligand)")
    ap.add_argument("--restrain-residues", default="",
                    help="sequential residue numbers whose backbone (CA, C, N; P, C4' of a nucleotide) is held in production, comma-separated")
    ap.add_argument("--membrane", action="store_true",
                    help="a bilayer: the residues after the solute that are neither water nor ions are lipids")
    ap.add_argument("--name", default="HOPE-MD")
    return ap


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def is_water(res) -> bool:
    if res.name in WATER_NAMES:
        return True
    return len(res.atoms) <= 4 and all(a.atomic_number in (1, 8) for a in res.atoms)


def split_cmap_types(struct) -> int:
    """ff19SB gives each residue its own CMAP grid, and every one of them sits on the same
    five backbone atom types (C N XC C N). GROMACS looks a CMAP up by those types, and so does
    ParmEd's writer, so all but one grid would be dropped and every residue would run on the
    same map, which gives the wrong backbone energy to every residue whose own grid was dropped.
    The CA of each CMAP gets a type of its own per grid (XC1, XC2, ...) with the same LJ
    parameters; every bonded parameter is written inline, so nothing else changes."""
    if not struct.cmaps:
        return 0
    used = {a.type for a in struct.atoms}
    by_grid = {}
    new_types = {}
    for cmap in struct.cmaps:
        key = id(cmap.type)
        ca = cmap.atom3
        base = re.sub(r"\d+$", "", ca.type) or ca.type
        if key not in by_grid:
            n = len(by_grid) + 1
            name = f"{base}{n}"
            while name in used:
                n += 1
                name = f"{base}{n}"
            used.add(name)
            by_grid[key] = name
        name = by_grid[key]
        if ca.type == name:
            continue
        if name not in new_types:
            old = ca.atom_type
            at = AtomType(name, None, old.mass, old.atomic_number)
            at.set_lj_params(old.epsilon, old.rmin, old.epsilon_14, old.rmin_14)
            new_types[name] = at
        ca.atom_type = new_types[name]
        ca.type = name
    return len(by_grid)


def pair_scaling(top) -> tuple:
    """The 1-4 scaling GROMACS applies through [ defaults ] (gen-pairs yes, fudgeQQ, fudgeLJ).
    ParmEd takes one value for the whole topology only when every dihedral type carries the
    same scee/scnb; a prmtop written from OpenMM (the membrane build's) has 1.0 on its impropers
    and a placeholder on the hydroxyl 1-4 pairs whose LJ is zero anyway, so ParmEd would fall
    back to fudge 1.0 and every 1-4 interaction would run unscaled. The scaling is read from the
    dihedrals that make the 1-4 pairs instead; a pair that would not follow it cannot be written
    with gen-pairs and stops the conversion."""
    pairs = [d for d in top.dihedrals if d.type is not None and not d.improper and not d.ignore_end]
    if not pairs:
        return None
    counts = {}
    for d in pairs:
        key = (round(d.type.scee, 4), round(d.type.scnb, 4))
        counts[key] = counts.get(key, 0) + 1
    scee, scnb = max(counts, key=counts.get)
    for d in pairs:
        zero_lj = d.atom1.epsilon * d.atom4.epsilon == 0      # scaling cannot change a zero
        if abs(d.type.scee - scee) > 1e-4 or (abs(d.type.scnb - scnb) > 1e-4 and not zero_lj):
            raise ValueError(f"the 1-4 pair {d.atom1.residue.name} {d.atom1.residue.idx + 1} {d.atom1.name} - "
                             f"{d.atom4.residue.name} {d.atom4.residue.idx + 1} {d.atom4.name} is scaled by 1/{d.type.scee:g} "
                             f"(Coulomb) and 1/{d.type.scnb:g} (LJ) while the topology's pairs use 1/{scee:g} and 1/{scnb:g}; "
                             "GROMACS applies one scaling through [ defaults ], so this topology cannot be converted as is")
    top.defaults.gen_pairs = "yes"
    top.defaults.fudgeQQ = 1.0 / scee
    top.defaults.fudgeLJ = 1.0 / scnb
    return scee, scnb


def block_name(block: str):
    """The name on the first line of a [ moleculetype ] block."""
    for line in block.splitlines():
        s = line.split(";", 1)[0].strip()
        if s:
            return s.split()[0]
    return None


def moleculetype_names(text: str) -> list:
    return [block_name(b) for b in text.split("[ moleculetype ]")[1:]]


def add_includes(text: str, includes: dict) -> str:
    """Append include lines at the end of the named moleculetype blocks (before the next
    [ moleculetype ] or the [ system ] section)."""
    parts = text.split("[ moleculetype ]")
    out = [parts[0]]
    for block in parts[1:]:
        extra = includes.get(block_name(block), "")
        if extra:
            i = block.find("[ system ]")
            if i < 0:
                block = block.rstrip("\n") + "\n\n" + extra + "\n\n"
            else:
                block = block[:i].rstrip("\n") + "\n\n" + extra + "\n\n" + block[i:]
        out.append(block)
    return "[ moleculetype ]".join(out)


def ndx_group(name: str, indices: list) -> str:
    lines = [f"[ {name} ]"]
    for i in range(0, len(indices), 15):
        lines.append(" ".join(f"{k:6d}" for k in indices[i:i + 15]))
    return "\n".join(lines) + "\n"


def posres_itp(comment: str, indices: list) -> str:
    lines = [f"; HOPE-MD: {comment}; the force constant POSRES_FC (kJ/mol/nm^2) comes from the mdp's define line",
             "[ position_restraints ]", ";  atom  type      fx         fy         fz"]
    lines += [f"{k:7d}     1  POSRES_FC  POSRES_FC  POSRES_FC" for k in indices]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    struct = pmd.load_file(a.prmtop, xyz=a.inpcrd)
    if struct.box is None:
        log(f"{a.inpcrd} carries no box; GROMACS needs one")
        return 3
    n_solute = a.solute_residues
    if not (1 <= n_solute <= len(struct.residues)):
        log(f"--solute-residues {n_solute} is outside 1..{len(struct.residues)}")
        return 3
    restrain = {int(x) for x in a.restrain_residues.replace(" ", "").split(",") if x}

    # GROMACS concatenates the molecules in the order [ molecules ] lists them: the atom order
    # of the .gro and the xtc is the prmtop's only when every molecule is contiguous in it.
    # cpptraj and MMPBSA.py read the xtc with the prmtop, so anything else has to stop here.
    tag_molecules(struct)
    seen = set()
    last = None
    for atom in struct.atoms:
        m = atom.marked
        if m != last:
            if m in seen:
                log(f"molecule {m} (residue {atom.residue.name} {atom.residue.idx + 1}) is not contiguous in "
                    f"{a.prmtop}: GROMACS would reorder the atoms and the analysis could not read the xtc "
                    "with the prmtop. A bond between chains that are not next to each other does this: a disulfide "
                    "from the partner to a receptor chain that another receptor chain follows, say. Amber, OpenMM and "
                    "NAMD run the prmtop as it is.")
                return 3
            seen.add(m)
            last = m
    by_mol = {}
    for atom in struct.atoms:
        by_mol.setdefault(atom.marked, []).append(atom)

    n_grids = split_cmap_types(struct)
    top = pmd.gromacs.GromacsTopologyFile.from_structure(struct)
    top.title = a.name
    try:
        scaling = pair_scaling(top)
    except ValueError as e:
        log(str(e))
        return 3
    top_path, gro_path = out / "complex.top", out / "complex.gro"
    top.write(str(top_path))
    pmd.gromacs.GromacsGroFile.write(struct, str(gro_path), precision=3)

    # which moleculetype is which: the writer walks split() in this same order
    text = top_path.read_text()
    names = moleculetype_names(text)
    mols = top.split()
    if len(names) != len(mols):
        log(f"{len(names)} moleculetypes in {top_path} but {len(mols)} kinds of molecule in the structure")
        return 3
    includes = {}
    solute_types, posres_files, restrained, held = [], [], 0, 0
    for name, (mol, instances) in zip(names, mols):
        first = by_mol[min(instances) + 1]
        if len(first) != len(mol.atoms):
            log(f"moleculetype {name}: {len(mol.atoms)} atoms in the topology, {len(first)} in the structure")
            return 3
        if not any(atom.residue.idx < n_solute for atom in first):
            continue
        solute_types.append(f"{name} x{len(instances)}")
        safe = safe_name(name)
        heavy = [k + 1 for k, atom in enumerate(first)
                 if atom.residue.idx < n_solute and atom.atomic_number > 1 and not isinstance(atom, ExtraPoint)]
        (out / f"posres_{safe}.itp").write_text(
            posres_itp(f"heavy atoms of moleculetype {name} held to the build coordinates", heavy))
        posres_files.append(f"posres_{safe}.itp")
        restrained += len(heavy) * len(instances)
        inc = f'#ifdef POSRES\n#include "posres_{safe}.itp"\n#endif'
        if restrain:
            bb = [k + 1 for k, atom in enumerate(first)
                  if atom.residue.idx + 1 in restrain and atom.name in BACKBONE]
            if bb:
                (out / f"posres_prod_{safe}.itp").write_text(
                    posres_itp(f"backbone (CA, C, N; P, C4') of residues {sorted(restrain)} of moleculetype {name}, held in production", bb))
                inc += f'\n#ifdef POSRES_PROD\n#include "posres_prod_{safe}.itp"\n#endif'
                held += len(bb) * len(instances)
        includes[name] = inc
    if not includes:
        log(f"no molecule lies in residues 1..{n_solute}; nothing to restrain")
        return 3
    top_path.write_text(add_includes(text, includes))

    # index groups for the mdp files (tc-grps, comm-grps); with -n grompp makes no default groups
    solute, solvent, lipids = [], [], []
    for res in struct.residues:
        idx = [atom.idx + 1 for atom in res.atoms]
        if res.idx < n_solute:
            solute += idx
        elif a.membrane and not is_water(res) and len(res.atoms) > 1:
            lipids += idx
        else:
            solvent += idx
    groups = [("System", [atom.idx + 1 for atom in struct.atoms]), ("Solute", solute), ("Solvent", solvent)]
    if a.membrane:
        if not lipids:
            log("a bilayer run without lipid residues after the solute")
            return 3
        groups += [("Lipids", lipids), ("Solute_Lipids", solute + lipids)]
    (out / "index.ndx").write_text("".join(ndx_group(n, g) for n, g in groups))

    n_ep = sum(1 for atom in struct.atoms if isinstance(atom, ExtraPoint))
    water = "four-site (virtual sites)" if n_ep else "three-site (SETTLE)"
    box = " x ".join(f"{x / 10:.3f}" for x in struct.box[:3])
    cmap = f"{n_grids} CMAP grids on their own CA types; " if n_grids else ""
    fudge = f"1-4 pairs scaled by 1/{scaling[0]:g} (Coulomb) and 1/{scaling[1]:g} (LJ); " if scaling else ""
    log(f"gromacs topology: {len(struct.atoms)} atoms in {len(by_mol)} molecules of {len(mols)} kinds; "
        f"water {water}; {fudge}{cmap}box {box} nm")
    log(f"solute: residues 1-{n_solute} = moleculetype(s) {', '.join(solute_types)}; {restrained} heavy atoms "
        f"restrained through {', '.join(posres_files)}" + (f"; {held} backbone atoms held in production" if held else ""))
    log("index groups: " + ", ".join(f"{n} ({len(g)})" for n, g in groups))
    log(f"wrote {top_path}, {gro_path}, {out / 'index.ndx'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def _mdp(title: str, sections: list) -> str:
    """An mdp file: a title comment, then sections of `key = value` lines under a comment."""
    out = [f"; {title}"]
    for heading, items in sections:
        out.append("")
        out.append(f"; {heading}")
        for k, v in items:
            out.append(f"{k:<24s}= {v}")
    return "\n".join(out) + "\n"


class GromacsEngine(AmberEngine):
    key = "gromacs"
    label = "GROMACS"
    TRAJ = "xtc"                # cpptraj reads the xtc parts with build/complex.prmtop (TOPOLOGY inherited)
    MMGBSA = True               # MMPBSA.py reads them the same way, through cpptraj

    # ------------------------------------------------------------------ progress
    @classmethod
    def part_progress(cls, part: Path) -> tuple[int, float | None] | None:
        """(steps completed in this part, ns/day or None) from prod_s<k>_p<n>.log beside the
        xtc. The steps in the log are absolute (init-step counts on from the previous parts), so
        the part's own count is the last energy block's step (the line under a "Step Time"
        header; mdrun writes one every nstlog steps and at the last step) minus the init-step
        the log's parameter dump records. The ns/day is the "Performance:" line mdrun writes
        when it finishes, walltime stop included. None when the log is not there yet."""
        log = part.with_suffix(".log")
        try:
            text = log.read_text(errors="replace")
        except OSError:
            return None
        m = re.search(r"^\s*init-step\s*=\s*(\d+)", text, re.M)
        init = int(m.group(1)) if m else 0
        steps = re.findall(r"^\s*Step\s+Time\s*\n\s*(\d+)\s", text, re.M)
        last = int(steps[-1]) if steps else init
        perf = re.search(r"^Performance:\s+([0-9.]+)", text, re.M)
        return max(0, last - init), (float(perf.group(1)) if perf else None)

    # ------------------------------------------------------------------ render
    def render(self, spec: Spec, derived: dict | None = None) -> Render:
        # Amber's render builds the whole graph and calls the overrides below for the build
        # script, the build/equil/prod jobs and the README; only its mdin ladder is dropped here
        r = super().render(spec, derived)
        r.engine = self.key
        d = r.derived
        pro = spec.protocol
        mem = membrane_of(spec)
        r.files = [f for f in r.files if not f.path.startswith("md/")]
        seg_steps = int(round(d["segment_ns"] * 1e6 / pro.timestep_fs))
        save_steps = int(round(pro.save_ps * 1000 / pro.timestep_fs))
        r.add("build/to_gromacs.py", TO_GROMACS_PY, executable=True)
        r.add("md/01_min.mdp", self._min_mdp(spec))
        r.add("md/02_heat.mdp", self._heat_mdp(spec, mem))
        for i, w in enumerate((pro.restraint_kcal, 1.0, 0.1, 0.0), start=1):
            r.add(f"md/03_eq{i}.mdp", self._eq_mdp(spec, w, i, mem))
        r.add("md/04_prod.mdp", self._prod_mdp(spec, seg_steps, save_steps, mem))
        # cpptraj and MMPBSA.py read xtc: swap the trajectory glob in Amber's analysis jobs, and
        # make whole any part still as mdrun wrote it (a killed part, an older run) before they read;
        # a segment's first part (prod_s<k>_p1) loses the frame at the segment's first step there too
        pre = self.preamble(spec, "amber")
        whole = ("# a part still as mdrun wrote it (a killed part, an older run) is made whole first: cpptraj images by whole molecules;\n"
                 "# a segment's first part also loses the frame at the step the segment started from, as in the production job\n"
                 + spec.site.module_block("gromacs") + "\n"
                 "for f in r*/prod_s*_p*.xtc; do\n"
                 "  [ -f \"$f.whole\" ] && continue\n"
                 "  t=${f%.xtc}.tpr; [ -s \"$t\" ] || continue\n"
                 "  b=$(basename $f .xtc); first=\"\"\n"
                 f"  case $b in *_p1) k=${{b#prod_s}}; first=\"-b $(awk -v k=${{k%_p1}} 'BEGIN{{printf \"%.3f\", ((k-1)*{seg_steps}+{save_steps}/2)*{pro.timestep_fs / 1000:g}}}')\";; esac\n"
                 "  ( flock 9; [ -f \"$f.whole\" ] || { echo 0 | gmx trjconv -quiet -f \"$f\" -s \"$t\" -pbc whole $first -o \"$(dirname $f)/whole_$(basename $f)\" > \"${f%.xtc}.trjconv.log\" 2>&1"
                 " && mv -f \"$(dirname $f)/whole_$(basename $f)\" \"$f\" && touch \"$f.whole\"; } ) 9>\"$f.lock\"\n"
                 "done\n" + spec.site.module_block("amber") + "\n")
        for f in r.files:
            if f.path in ("jobs/40_analysis.sbatch", "jobs/50_mmgbsa.sbatch"):
                f.text = f.text.replace("prod_s*_p*.nc", "prod_s*_p*.xtc")
                assert pre in f.text
                f.text = f.text.replace(pre, pre + whole, 1)
        for j in r.jobs:
            if j.key == "build":
                j.description += ", ParmEd to GROMACS, grompp check"
            elif j.key == "equil":
                j.description = "GROMACS: minimize, heat, NPT ladder"
            elif j.key.startswith("prod_s"):
                j.description = "GROMACS " + j.description
        ff = spec.forcefield
        r.notes.append(f"GROMACS runs the {'membrane build' if mem else 'tleap'} topology converted by ParmEd in the build job "
                       f"({ff.protein} / {ff.water}, the same parameters Amber runs; hydrogen masses repartitioned in the "
                       "topology when the step is 4 fs), and the build job checks the result with gmx grompp before the equilibration "
                       "queues. Position restraints go through build/posres_*.itp includes in complex.top (#ifdef POSRES, "
                       "force constants from the mdp define lines); cpptraj and MMPBSA.py read the xtc parts with the prmtop.")
        if ff.protein == "ff19SB":
            r.notes.append("ff19SB in GROMACS: its residue-specific CMAP grids are looked up by atom type, so the conversion "
                           "gives the CA of each grid a type of its own (XC1, XC2, ...) with the same LJ parameters; without "
                           "that every residue would run on one grid.")
        if not spec.on_cpu:
            if self._four_site_water(spec):
                r.notes.append(f"{ff.water} is a four-site water and its extra point is a virtual site: GROMACS {spec.site.version('gromacs')} does not "
                               "run virtual sites in its GPU update, so the update and constraints stay on the CPU threads "
                               "(-nb, -pme and -bonded on the GPU); compute.cpus = 8 gives it room. TIP3P runs GPU-resident.")
            else:
                r.notes.append("mdrun runs non-bonded, PME, bonded and the update on the GPU (one thread-MPI rank, the job's "
                               "cores as OpenMP threads).")
        r.notes.append("Each production part runs with -maxh from its walltime, so it ends cleanly (checkpoint written, "
                       "ns/day in the log) about ten minutes before Slurm's limit; a segment left unfinished exits 4 so "
                       "the chain stops there, and Resubmit continues it from the part's checkpoint into the next part "
                       "(the previous part's xtc is trimmed to the checkpoint so no frame repeats). The frame mdrun "
                       "writes at the step a segment starts from is left out, so frame k is k save intervals into the "
                       "production, as on the other engines.")
        return r

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _four_site_water(spec: Spec) -> bool:
        return WATER_SITES.get(spec.forcefield.water, 3) == 4

    @staticmethod
    def _solute_residues(spec: Spec) -> int:
        """The solute is residues 1..N of the topology: receptor, then partner or ligand."""
        sysm = spec.system
        rec = sysm.receptor_residues or [1, 1]
        lig = sysm.ligand_residues or [rec[1] + 1, rec[1] + 1]
        return rec[1] if sysm.ligand_class == "none" else lig[1]

    @staticmethod
    def _groups(mem: dict | None) -> tuple[list[str], str]:
        """tc-grps and comm-grps, all from build/index.ndx: the solute and the solvent (water,
        ions) thermostatted apart; in a bilayer the lipids as a third group, and the center of
        mass of solute + lipids and of the solvent removed separately, so the membrane cannot
        drift through the water (the usual membrane setting)."""
        if mem:
            return ["Solute", "Lipids", "Solvent"], "Solute_Lipids Solvent"
        return ["Solute", "Solvent"], "System"

    @staticmethod
    def _define(weight_kcal: float, macro: str = "POSRES") -> str:
        return f"-D{macro} -DPOSRES_FC={weight_kcal * POSRES_PER_KCAL_A2:g}"

    @staticmethod
    def _nonbonded() -> list:
        # DispCorr = EnerPres: the long-range dispersion correction to energy and pressure that pmemd applies by
        # default (vdwmeth = 1), OpenMM's NonbondedForce under PME and NAMD's LJcorrection, so the four engines
        # give a box the same density and a bilayer the same area per lipid
        return [("cutoff-scheme", "Verlet"), ("nstlist", 20), ("pbc", "xyz"),
                ("coulombtype", "PME"), ("rcoulomb", f"{CUTOFF_A / 10:g}"), ("rvdw", f"{CUTOFF_A / 10:g}"),
                ("DispCorr", "EnerPres")]

    @staticmethod
    def _constraints(dt_fs: float, continuation: bool) -> list:
        # LINCS on the bonds with hydrogen (Amber's ntc=2); at 4 fs with heavy hydrogens the
        # expansion is taken further (order 6, two iterations), the usual setting for that step
        hmr = dt_fs > 2.5
        return [("constraints", "h-bonds"), ("constraint-algorithm", "LINCS"),
                ("lincs-order", 6 if hmr else 4), ("lincs-iter", 2 if hmr else 1),
                ("continuation", "yes" if continuation else "no")]

    @staticmethod
    def _thermostat(spec: Spec, groups: list[str]) -> list:
        n = len(groups)
        return [("tcoupl", "V-rescale"), ("tc-grps", " ".join(groups)),
                ("tau_t", " ".join(["0.1"] * n)), ("ref_t", " ".join([f"{spec.protocol.temperature_K:g}"] * n))]

    @staticmethod
    def _barostat(spec: Spec, mem: dict | None) -> list:
        # C-rescale: stable, first order (no barostat state to lose when a part restarts through
        # grompp -t) and on the GPU update's list. A bilayer scales x and y together and z on its
        # own at equal reference pressures, which is zero surface tension.
        p = f"{spec.protocol.pressure_bar:g}"
        if mem:
            return [("pcoupl", "C-rescale"), ("pcoupltype", "semiisotropic"), ("tau_p", "2.0"),
                    ("ref_p", f"{p} {p}"), ("compressibility", "4.5e-5 4.5e-5"), ("refcoord-scaling", "com")]
        return [("pcoupl", "C-rescale"), ("pcoupltype", "isotropic"), ("tau_p", "2.0"),
                ("ref_p", p), ("compressibility", "4.5e-5"), ("refcoord-scaling", "com")]

    @staticmethod
    def _com(comm_grps: str) -> list:
        return [("comm-mode", "Linear"), ("nstcomm", 100), ("comm-grps", comm_grps)]

    @staticmethod
    def _output(nstlog: int, xtc_steps: int = 0) -> list:
        items = [("nstxout", 0), ("nstvout", 0), ("nstfout", 0), ("nstxout-compressed", xtc_steps)]
        if xtc_steps:
            items += [("compressed-x-grps", "System"), ("compressed-x-precision", 1000)]
        items += [("nstlog", nstlog), ("nstenergy", nstlog), ("nstcalcenergy", 100)]
        return items

    # ------------------------------------------------------------------ mdp ladder
    def _min_mdp(self, spec: Spec) -> str:
        pro = spec.protocol
        return _mdp(f"HOPE-MD minimization for {spec.name}: steepest descent, solute heavy atoms held "
                    f"({pro.restraint_kcal:g} kcal/mol/A^2 in Amber's k d^2 = {pro.restraint_kcal * POSRES_PER_KCAL_A2:g} kJ/mol/nm^2 in GROMACS's 1/2 k d^2)", [
            ("restraints (posres_*.itp in build/complex.top)", [("define", self._define(pro.restraint_kcal)), ("include", "-I../build")]),
            ("run control", [("integrator", "steep"), ("nsteps", pro.minimise_steps), ("emtol", "100.0"),
                             ("emstep", "0.01"), ("nstlog", 100)]),
            (f"neighbor list and electrostatics ({CUTOFF_A:g} A cutoff, PME)", self._nonbonded()),
            ("constraints", [("constraints", "h-bonds"), ("constraint-algorithm", "LINCS")]),
        ])

    def _heat_mdp(self, spec: Spec, mem: dict | None) -> str:
        pro = spec.protocol
        steps = int(round(pro.heat_ps / LADDER_DT_PS))
        groups, comm = self._groups(mem)
        n = len(groups)
        ramp_end = 0.9 * pro.heat_ps     # ramp over 90 % of the stage, hold for the rest, as the Amber input does
        temps = f"0 {pro.temperature_K:g} {pro.temperature_K:g}"
        times = f"0 {ramp_end:g} {pro.heat_ps:g}"
        return _mdp(f"HOPE-MD heating for {spec.name}: 0 -> {pro.temperature_K:g} K under NVT over {pro.heat_ps:g} ps, "
                    f"solute heavy atoms held ({pro.restraint_kcal:g} kcal/mol/A^2)", [
            ("restraints", [("define", self._define(pro.restraint_kcal)), ("include", "-I../build")]),
            ("run control", [("integrator", "md"), ("dt", f"{LADDER_DT_PS:g}"), ("nsteps", steps), ("tinit", 0), ("init-step", 0)]),
            ("output", self._output(5000)),
            (f"neighbor list and electrostatics ({CUTOFF_A:g} A cutoff, PME)", self._nonbonded()),
            ("temperature: V-rescale, the reference annealed from 0 K; a few kelvin of starting velocities",
             self._thermostat(spec, groups)
             + [("annealing", " ".join(["single"] * n)), ("annealing-npoints", " ".join(["3"] * n)),
                ("annealing-time", " ".join([times] * n)), ("annealing-temp", " ".join([temps] * n)),
                # what grompp resolves "auto" to here (annealed groups); GROMACS 2023.1 (ACES) stores
                # "auto" in the tpr and mdrun then stops on an assertion, so it is said outright.
                # 2024 builds the same tpr either way (gmx check: only the seed differs).
                ("ensemble-temperature-setting", "not available"),
                ("gen_vel", "yes"), ("gen_temp", 5), ("gen_seed", "HOPEMD_SEED"), ("ld-seed", "HOPEMD_SEED")]),
            ("pressure", [("pcoupl", "no"), ("refcoord-scaling", "com")]),
            ("constraints", self._constraints(LADDER_DT_PS * 1000, continuation=False)),
            ("center of mass", self._com(comm)),
        ])

    def _eq_mdp(self, spec: Spec, weight: float, stage: int, mem: dict | None) -> str:
        pro = spec.protocol
        steps = int(round(pro.equil_ps / 4 / LADDER_DT_PS))
        groups, comm = self._groups(mem)
        restr = ([("define", self._define(weight)), ("include", "-I../build")] if weight > 0
                 else [("include", "-I../build")])
        return _mdp(f"HOPE-MD equilibration {stage}/4 for {spec.name} under NPT, {pro.equil_ps / 4:g} ps, restraint "
                    f"{weight:g} kcal/mol/A^2" + (f" = {weight * POSRES_PER_KCAL_A2:g} kJ/mol/nm^2 (1/2 k d^2)" if weight > 0 else " (none)"), [
            ("restraints", restr),
            ("run control", [("integrator", "md"), ("dt", f"{LADDER_DT_PS:g}"), ("nsteps", steps), ("tinit", 0), ("init-step", 0)]),
            ("output", self._output(5000)),
            (f"neighbor list and electrostatics ({CUTOFF_A:g} A cutoff, PME)", self._nonbonded()),
            ("temperature", self._thermostat(spec, groups) + [("gen_vel", "no"), ("ld-seed", "HOPEMD_SEED")]),
            ("pressure" + (" (semi-isotropic: a bilayer)" if mem else ""), self._barostat(spec, mem)),
            ("constraints", self._constraints(LADDER_DT_PS * 1000, continuation=True)),
            ("center of mass", self._com(comm)),
        ])

    def _prod_mdp(self, spec: Spec, seg_steps: int, save_steps: int, mem: dict | None) -> str:
        pro = spec.protocol
        groups, comm = self._groups(mem)
        restr = [("include", "-I../build")]
        title_restr = ""
        if pro.restrain:
            nums = ",".join(str(_resnum(k, spec.system)) for k in pro.restrain)
            restr = [("define", self._define(pro.restraint_kcal, "POSRES_PROD")), ("include", "-I../build")]
            title_restr = f"; CA, C, N of residues {nums} held ({pro.restraint_kcal:g} kcal/mol/A^2)"
        return _mdp(f"HOPE-MD production for {spec.name}, one segment; nsteps and init-step are replaced per part "
                    f"by the job script{title_restr}", [
            ("restraints", restr),
            ("run control", [("integrator", "md"), ("dt", f"{pro.timestep_fs / 1000:g}"), ("nsteps", seg_steps),
                             ("tinit", 0), ("init-step", 0)]),
            (f"output: a frame every {pro.save_ps:g} ps into the part's xtc, whole system", self._output(save_steps, save_steps)),
            (f"neighbor list and electrostatics ({CUTOFF_A:g} A cutoff, PME)", self._nonbonded()),
            ("temperature", self._thermostat(spec, groups) + [("gen_vel", "no"), ("ld-seed", "HOPEMD_SEED")]),
            ("pressure" + (" (semi-isotropic: a bilayer)" if mem else ""), self._barostat(spec, mem)),
            ("constraints", self._constraints(pro.timestep_fs, continuation=True)),
            ("center of mass", self._com(comm)),
        ])

    # ------------------------------------------------------------------ build
    def _build_sh(self, spec: Spec, small: bool, mem: dict | None = None) -> str:
        """Amber's build script, then the conversion to GROMACS input."""
        pro = spec.protocol
        prmtop = "build/complex_hmr.prmtop" if spec.hmr else "build/complex.prmtop"
        args = f"--solute-residues {self._solute_residues(spec)}"
        if pro.restrain:
            args += " --restrain-residues " + ",".join(str(_resnum(k, spec.system)) for k in pro.restrain)
        if mem:
            args += " --membrane"
        return super()._build_sh(spec, small, mem) + f"""echo "[hopemd] gromacs input: converting {prmtop} with ParmEd"
# the server's interpreter (HOPEMD_PYTHON, exported by the job preamble) has ParmEd 4.3; -I keeps the
# engine module's PYTHONPATH (another Python's site-packages) out of it, as on the pdb2pqr line. The
# module's own python3 carries AmberTools' parmed and is the fallback.
CONV=""
if [ -n "${{HOPEMD_PYTHON:-}}" ] && "$HOPEMD_PYTHON" -I -c "import parmed" >/dev/null 2>&1; then CONV="$HOPEMD_PYTHON -I"
elif python3 -c "import parmed" >/dev/null 2>&1; then CONV=python3; fi
[ -n "$CONV" ] || {{ echo '[hopemd] no Python with ParmEd for the GROMACS conversion (is HOPEMD_PYTHON set?)'; exit 3; }}
$CONV build/to_gromacs.py --prmtop {prmtop} --inpcrd build/complex.inpcrd --out build {args} --name {spec.name} \\
  > build/to_gromacs.log 2>&1 || {{ tail -30 build/to_gromacs.log; echo '[hopemd] the GROMACS conversion failed; see build/to_gromacs.log'; exit 3; }}
grep '^\\[hopemd\\]' build/to_gromacs.log || true
test -s build/complex.top && test -s build/complex.gro && test -s build/index.ndx \\
  || {{ echo '[hopemd] the conversion left no build/complex.top, complex.gro or index.ndx; see build/to_gromacs.log'; exit 3; }}
echo "[hopemd] gromacs input written: build/complex.top, build/complex.gro, build/posres_*.itp, build/index.ndx"
"""

    def _build_sbatch(self, spec: Spec) -> str:
        """Amber's build job, then gmx grompp on the converted input: a topology GROMACS cannot
        read fails here, on the build node, not at the head of the GPU queue."""
        modules = spec.site.module_block("gromacs")
        return super()._build_sbatch(spec) + f"""
echo "[hopemd] grompp check of the GROMACS input"
{modules}
cd build
for STAGE in 01_min 04_prod; do
  sed "s/HOPEMD_SEED/-1/" ../md/$STAGE.mdp > check_$STAGE.mdp
  gmx grompp -f check_$STAGE.mdp -c complex.gro -r complex.gro -p complex.top -n index.ndx -o check_$STAGE.tpr \\
    -po check_$STAGE.mdout.mdp -maxwarn {MAXWARN} > grompp_check_$STAGE.log 2>&1 \\
    || {{ tail -40 grompp_check_$STAGE.log; echo "[hopemd] grompp refuses md/$STAGE.mdp with build/complex.top; see build/grompp_check_$STAGE.log"; exit 3; }}
  grep -A3 -E '^(WARNING|NOTE) [0-9]+' grompp_check_$STAGE.log | head -40 || true
  rm -f check_$STAGE.tpr check_$STAGE.mdout.mdp
done
cd ..
echo "[hopemd] grompp accepts build/complex.top with the minimization and production inputs"
"""

    # ------------------------------------------------------------------ jobs
    def _mdrun_flags(self, spec: Spec) -> str:
        """The MDRUN and MDRUN_MIN lines of a job script: what gmx mdrun gets on this device."""
        # Grace's job environment carries OMP_NUM_THREADS=1, which gmx refuses beside -ntomp N ("have
        # different values"): the count is exported to match
        if spec.on_cpu:
            return ('export OMP_NUM_THREADS=$SLURM_NTASKS\n'
                    'MDRUN="-ntmpi 1 -ntomp $SLURM_NTASKS"   # CPU check: one whole node, one rank, every core a thread\n'
                    'MDRUN_MIN="$MDRUN"')
        gpu = "-nb gpu -pme gpu -bonded gpu"
        if self._four_site_water(spec):
            # GROMACS 2024.4 refuses -update gpu with virtual sites ("Virtual sites are not
            # supported with the GPU update"), and a four-site water's extra point is one
            update = "   # update on the CPU: this water's extra point is a virtual site"
        else:
            gpu += " -update gpu"
            update = "   # everything GPU-resident: three-site water, LINCS, V-rescale, C-rescale"
        return ('export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK\n'
                f'MDRUN="{gpu} -ntmpi 1 -ntomp $SLURM_CPUS_PER_TASK"{update}\n'
                '# steepest descent cannot take the bonded or update tasks on the GPU (mdrun: "requires a dynamical integrator")\n'
                'MDRUN_MIN="-nb gpu -ntmpi 1 -ntomp $SLURM_CPUS_PER_TASK"')

    def _equil_sbatch(self, spec: Spec, mem: dict | None = None) -> str:
        pro = spec.protocol
        est_h = 6.0 if spec.on_cpu else 1.5   # min + 200 ps heat + 1 ns NPT at 2 fs
        if mem and not spec.on_cpu:
            est_h = 3.0                        # a bilayer system is two to three times the atoms of a water box
        return (self.sbatch_header(spec, "equil", gpu=True, hours=est_h, array=pro.replicates,
                                   throttle=spec.compute.max_concurrent)
                + self.preamble(spec, "gromacs") + f"""
export GMX_MAXBACKUP=-1        # a stage run again overwrites its files instead of leaving #min.log.1# copies
{self._mdrun_flags(spec)}
R=r${{SLURM_ARRAY_TASK_ID}}
mkdir -p "$R"; cd "$R"
TOP=../build/complex.top; REF=../build/complex.gro; NDX=../build/index.ndx
{self.seed_line(spec, "equil")}
stage() {{ # name, mdp, coordinates [, checkpoint]: grompp, then mdrun; -r is the build structure the restraints hold to
  echo "[hopemd] $1"
  local t=""; [ $# -ge 4 ] && t="-t $4"
  sed "s/HOPEMD_SEED/$SEED/" ../md/$2 > $1.mdp
  gmx grompp -f $1.mdp -c $3 -r $REF $t -p $TOP -n $NDX -o $1.tpr -po $1.mdout.mdp -maxwarn {MAXWARN} > grompp_$1.log 2>&1 \\
    || {{ tail -30 grompp_$1.log; echo "[hopemd] grompp failed for $1; see $R/grompp_$1.log"; exit 3; }}
  local flags="$MDRUN"; [ "$1" = min ] && flags="$MDRUN_MIN"
  gmx mdrun -deffnm $1 $flags
}}
stage min  01_min.mdp  ../build/complex.gro
stage heat 02_heat.mdp min.gro
stage eq1  03_eq1.mdp  heat.gro heat.cpt
stage eq2  03_eq2.mdp  eq1.gro  eq1.cpt
stage eq3  03_eq3.mdp  eq2.gro  eq2.cpt
stage eq4  03_eq4.mdp  eq3.gro  eq3.cpt
test -s eq4.cpt && test -s eq4.gro || {{ echo "[hopemd] eq4 left no checkpoint (eq4.cpt) or structure (eq4.gro)"; exit 3; }}
echo "[hopemd] equilibrated replicate $SLURM_ARRAY_TASK_ID"
""")

    def _prod_sbatch(self, spec: Spec, k: int, nseg: int, seg_steps: int, walltime: str) -> str:
        pro = spec.protocol
        prev = "eq4.cpt" if k == 1 else f"prod_s{k-1}.cpt"
        save_steps = int(round(pro.save_ps * 1000 / pro.timestep_fs))
        hours = parse_walltime(walltime) or 1.0
        margin = min(MAXH_MARGIN_H, 0.1 * hours)
        return (self.sbatch_header(spec, f"prod_s{k}", gpu=True, walltime=walltime, array=pro.replicates,
                                   throttle=spec.compute.max_concurrent)
                + self.preamble(spec, "gromacs") + f"""
export GMX_MAXBACKUP=-1
{self._mdrun_flags(spec)}
R=r${{SLURM_ARRAY_TASK_ID}}
cd "$R"
TOP=../build/complex.top; REF=../build/complex.gro; NDX=../build/index.ndx
SEG=s{k}; SEG_NO={k}; TOTAL={seg_steps}; INIT0={(k - 1) * seg_steps}; DT={pro.timestep_fs / 1000:g}; SAVE={save_steps}; PREV={prev}
WALL_H={hours:.3f}   # the job's walltime in hours; -maxh is what is left of it, less {margin * 60:.0f} min
# Files that are not the part's own (grompp's log, the trim) carry a prefix: the queue reads every
# prod_${{SEG}}_p*.log beside an xtc as a part's log.
# Resume: steps are absolute across parts (init-step), so the last "Writing checkpoint, step N" line of
# the latest part's log says where its .cpt is, and that is what the next part continues from.
# cpptraj reads prod_${{SEG}}_p*.xtc in order.
DONE=0; PART=1; START=$PREV; LASTP=""
for f in $(ls prod_${{SEG}}_p*.log 2>/dev/null | sort -V); do
  p=${{f%.log}}
  S=$(grep -oE 'Writing checkpoint, step [0-9]+' "$f" | tail -1 | awk '{{print $4}}' || true)
  if [ -n "$S" ] && [ -s "$p.cpt" ]; then DONE=$((S-INIT0)); START=$p.cpt; PART=$((PART+1)); LASTP=$p; fi
done
LEFT=$((TOTAL-DONE))
if [ "$LEFT" -le 0 ]; then echo "[hopemd] segment $SEG already complete"; cp -f "$START" prod_${{SEG}}.cpt; exit 0; fi
test -s "$START" || {{ echo "[hopemd] nothing to start from: $START is missing (the previous stage did not finish)"; exit 3; }}
if [ -n "$LASTP" ] && [ -s "$LASTP.xtc" ]; then
  # the part before was stopped (walltime or a kill): keep its frames up to the checkpoint step only,
  # the ones after it are simulated again by this part; frames sit on multiples of SAVE, so the cut
  # falls half a save interval past the last frame kept
  F=$(( (DONE+INIT0-1)/SAVE*SAVE ))
  E=$(awk -v f=$F -v s=$SAVE -v dt=$DT 'BEGIN{{printf "%.3f", (f+s/2)*dt}}')
  echo "[hopemd] trimming $LASTP.xtc to $E ps (checkpoint at step $((DONE+INIT0)))"
  if echo 0 | gmx trjconv -f $LASTP.xtc -o trim_$LASTP.xtc -e $E > trim_$LASTP.log 2>&1 && [ -s trim_$LASTP.xtc ]; then
    mv -f trim_$LASTP.xtc $LASTP.xtc
  else
    echo "[hopemd] could not trim $LASTP.xtc (see trim_$LASTP.log); frames past the checkpoint may repeat"; rm -f trim_$LASTP.xtc
  fi
fi
P=prod_${{SEG}}_p${{PART}}
{self.seed_line(spec, "prod")}
sed -e "s/^nsteps .*/nsteps = $LEFT/" -e "s/^init-step .*/init-step = $((INIT0+DONE))/" -e "s/HOPEMD_SEED/$SEED/" ../md/04_prod.mdp > $P.mdp
echo "[hopemd] production $SEG part $PART: $LEFT of $TOTAL steps from $START (step $((INIT0+DONE)))"
gmx grompp -f $P.mdp -c $REF -r $REF -t "$START" -p $TOP -n $NDX -o $P.tpr -po $P.mdout.mdp -maxwarn {MAXWARN} > grompp_$P.log 2>&1 \\
  || {{ tail -30 grompp_$P.log; echo "[hopemd] grompp failed for $P; see $R/grompp_$P.log"; exit 3; }}
# -maxh: mdrun stops at 0.99 of it, checkpoint written; measured from now, so the trim above is paid for
MAXH=$(awk -v w=$WALL_H -v e=$SECONDS -v m={margin:.4f} 'BEGIN{{h=(w-e/3600-m)/0.99; if (h < 0.05) h=0.05; printf "%.3f", h}}')
gmx mdrun -deffnm $P -cpt {CHECKPOINT_MINUTES} -maxh $MAXH $MDRUN
S=$(grep -oE 'Writing checkpoint, step [0-9]+' $P.log | tail -1 | awk '{{print $4}}' || true)
NSDAY=$(grep -E '^Performance:' $P.log | tail -1 | awk '{{print $2}}' || true)   # the ns/day column
[ -n "$NSDAY" ] && echo "$NSDAY" > $P.nsday
# mdrun writes the coordinates as they are, molecules broken across the box, and cpptraj images by
# whole molecules, so a molecule left broken would inflate the RMSD:
# the part is made whole here, once; the state files are untouched.
# mdrun also writes the step it starts from. In a segment's first part that frame is the one the
# segment before ended on (in the first segment, the equilibrated structure), so it is left out and
# frame k of the production is k save intervals in, as on the other engines; a later part's first
# frame is one the trim above cut from the part before
FIRST=""; [ "$PART" -eq 1 ] && FIRST="-b $(awk -v s=$INIT0 -v k=$SAVE -v dt=$DT 'BEGIN{{printf "%.3f", (s+k/2)*dt}}')"
if [ -s $P.xtc ] && [ ! -f $P.xtc.whole ]; then
  echo 0 | gmx trjconv -quiet -f $P.xtc -s $P.tpr -pbc whole $FIRST -o whole_$P.xtc > trjconv_$P.log 2>&1 && mv -f whole_$P.xtc $P.xtc && touch $P.xtc.whole
fi
GOT=$(( ${{S:-$((INIT0+DONE))}} - INIT0 - DONE ))
if [ "$GOT" -lt "$LEFT" ] || [ ! -s $P.cpt ]; then
  echo "[hopemd] segment $SEG stopped after $GOT of $LEFT steps (walltime or a kill); resubmit from prod_$SEG to continue (${{NSDAY:-?}} ns/day)"
  exit 4
fi
cp -f $P.cpt prod_${{SEG}}.cpt
echo "[hopemd] segment $SEG of {nseg} done for replicate $SLURM_ARRAY_TASK_ID (${{NSDAY:-?}} ns/day)"
""")

    # ------------------------------------------------------------------ README
    def _readme(self, spec: Spec, d: dict, nseg: int, mem: dict | None = None) -> str:
        text = super()._readme(spec, d, nseg, mem)
        text = text.replace("engine: Amber (", f"engine: GROMACS {spec.site.version('gromacs')} on the Amber topology (", 1)
        text = text.replace("complex.inpcrd", "complex.inpcrd; complex.top, complex.gro, posres_*.itp, index.ndx for GROMACS", 1)
        text = text.replace("the mdin ladder", "the mdp ladder", 1)
        text = text.replace("prod_s<k>_p<n>.nc are the trajectory parts in order",
                            "prod_s<k>_p<n>.xtc are the trajectory parts in order, prod_s<k>.cpt marks a finished segment", 1)
        return text
