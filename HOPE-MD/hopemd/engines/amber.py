"""Amber: tleap builds, pmemd.cuda runs, cpptraj and MMPBSA.py analyze.

Run folder layout this renderer produces::

    spec.json                the one hand-editable thing (written by the server)
    input/receptor.pdb       as imported
    input/ligand.pdb|sdf     as imported (absent for apo)
    build/                   tleap inputs, prmtop/inpcrd, HMR prmtop, system.json
    md/                      01_min.in .. 04_prod.in
    r1/ r2/ ...              one folder per replicate: rst7, nc, out, mdinfo
    analysis/                cpptraj outputs, MM-GBSA, the strided viewer trajectory
    jobs/                    the sbatch scripts, submit.sh
    logs/                    Slurm output

Every job script is written so it can be run by hand from the run folder.

A membrane run (system.environment == "membrane") has no tleap: after pdb4amber and pdb2pqr
the build job runs hopemd.membrane, which embeds the solute in a bilayer with OpenMM's
Modeller (lipid17, TIP3P) and writes the same complex.prmtop / complex.inpcrd through ParmEd,
so everything after the build (HMR, the ladder, cpptraj, MM-GBSA) reads the same files. The
NPT stages then couple pressure semi-isotropically with zero surface tension.
"""
from __future__ import annotations

import math
import re

from ..spec import Spec, CUTOFF_A, has_nucleic, trace_mask, HOLD_ATOMS, WATER_SITES, mmgbsa_runs
from .base import (Engine, Render, Job, hopemd_home, membrane_of, carried_ions, crystal_waters,
                   kept_last, metal_mask, VIEW_WATERS)

LEAPRC_PROTEIN = {"ff19SB": "leaprc.protein.ff19SB", "ff14SB": "leaprc.protein.ff14SB"}
#: the RNA set first, the DNA set last: leaprc.RNA.OL3 reloads parm10.dat, and a DNA set loaded before it
#: would have its own torsions in question (checked on Grace: the chi terms of a DNA build come out the
#: same either way, but the order costs nothing and keeps it so)
LEAPRC_NUCLEIC = {"OL21+OL3": ("leaprc.RNA.OL3", "leaprc.DNA.OL21"),
                  "bsc1+OL3": ("leaprc.RNA.OL3", "leaprc.DNA.bsc1"),
                  "OL15+OL3": ("leaprc.RNA.OL3", "leaprc.DNA.OL15")}      # what the OpenMM choice loads in tleap
LEAPRC_WATER = {"OPC": ("leaprc.water.opc", "OPCBOX"),
                "TIP3P": ("leaprc.water.tip3p", "TIP3PBOX"),
                "TIP4P-Ew": ("leaprc.water.tip4pew", "TIP4PEWBOX")}
# tleap's unit names in atomic_ions.lib (Amber 24): Mg2+ is the unit MG (atom type Mg2+). "Mg2+" is no unit:
# tleap stops on it ("addIonsRand: Argument #2 is of type String") and an MgCl2 box does not build.
IONS = {"NaCl": ("Na+", "Cl-"), "KCl": ("K+", "Cl-"), "MgCl2": ("MG", "Cl-")}
SOLVENT_MASK = ":WAT,Na+,Cl-,K+,MG"
CHECKPOINT_MINUTES = 10


def seeded_ions_script(seed: int, water_sites: int) -> str:
    """The Python the build runs after tleap's second pass when the run has a seed of its own. tleap's
    addionsrand cannot be given one: AmberTools 24 calls srand(time(NULL)) as each addionsrand starts, and
    `set default` has no seed, so two builds a second apart place their ions differently. It puts each ion
    on the oxygen of a box water it removes, drawn at random from the waters still there, and moves nothing
    else, and the second pass solvates as the first did, so build/complex_pass1.pdb holds every water an
    ion could take. The draws are made again here from the run's seed, one per ion in the topology's order,
    and build/complex.inpcrd and complex_solv.pdb rewritten: one seed, one system. What comes before the box
    water keeps tleap's coordinates; the box water and the ions take those of the pass-1 file (to 0.001 A),
    so nothing depends on which waters tleap drew. Standard library only: it runs on whatever python3 the
    engine module's node has."""
    return f'''import random, re, sys
SEED, SITES = {seed}, {water_sites}      # the run's seed; atoms per water, a four-site water's extra point included
PRMTOP, INPCRD, PASS1, SOLV = "build/complex.prmtop", "build/complex.inpcrd", "build/complex_pass1.pdb", "build/complex_solv.pdb"

def keep(why):
    print("[hopemd] the ions stay where tleap's clock put them, and the build is not repeatable: " + why)
    sys.exit(0)

text = open(PRMTOP).read()

def section(name):
    m = re.search(r"^%FLAG " + re.escape(name) + r"[ \\t]*$", text, re.M)
    out = []
    for line in text[m.end():].split("\\n")[1:]:
        if line.startswith("%FORMAT") or line.startswith("%COMMENT"):
            continue
        if line.startswith("%"):
            break
        out.append(line)
    return out

pointers = [int(x) for line in section("POINTERS") for x in line.split()]
natom, nres = pointers[0], pointers[11]
labels = [line[i:i + 4].strip() for line in section("RESIDUE_LABEL") for i in range(0, len(line.rstrip()), 4)]
respt = [int(x) for line in section("RESIDUE_POINTER") for x in line.split()] + [natom + 1]
size = [respt[r + 1] - respt[r] for r in range(nres)]
nbox = 0                                   # the box water: the residues that end the topology
while nbox < nres and labels[nres - 1 - nbox] == "WAT" and size[nres - 1 - nbox] == SITES:
    nbox += 1
pass1 = [(float(l[30:38]), float(l[38:46]), float(l[46:54])) for l in open(PASS1) if l.startswith(("ATOM", "HETATM"))]
k, rest = divmod(len(pass1) - natom, SITES - 1)     # each ion took a water's place: SITES atoms out, one in
if k == 0 and rest == 0:
    print("[hopemd] no ion was placed in the box water: nothing to draw from the seed")
    sys.exit(0)
if k < 0 or rest or any(size[r] != 1 for r in range(nres - nbox - k, nres - nbox)):
    keep("%d atoms after the first pass and %d after the second do not differ by whole waters turned into ions" % (len(pass1), natom))
prefix = natom - k - nbox * SITES          # the atoms before the ions: the solute and what the build carries with it

lines = open(INPCRD).read().split("\\n")
vals = [float(l[i:i + 12]) for l in lines[2:] if l.strip() for i in range(0, len(l.rstrip()), 12)]
box_line = [l for l in lines if l.strip()][-1]
if prefix < 1 or len(vals) < 3 * natom + 6:
    keep("%s holds %d values for %d atoms" % (INPCRD, len(vals), natom))
tleap = [tuple(vals[3 * i:3 * i + 3]) for i in range(natom)]
# saveamberparm moves the coordinates into the box (by half its edges) and savepdb does not: the shift, from the
# solute, which the draw leaves alone
off = [sum(tleap[i][d] - pass1[i][d] for i in range(prefix)) / prefix for d in range(3)]
unshift = lambda p: tuple(v - o for v, o in zip(p, off))
shift = lambda p: tuple(v + o for v, o in zip(p, off))
near = lambda a, b: all(abs(x - y) < 0.002 for x, y in zip(a, b))
if not all(near(unshift(tleap[i]), pass1[i]) for i in range(prefix)):
    keep("the two passes did not build the same solute")
waters = [pass1[prefix + i * SITES:prefix + (i + 1) * SITES] for i in range(nbox + k)]
# tleap's own draw, read back: its waters must be the first pass's less k, in order, and its ions on the others' oxygens
kept, j = [], 0
for i in range(nbox):
    o = unshift(tleap[prefix + k + i * SITES])
    while j < len(waters) and not near(waters[j][0], o):
        j += 1
    if j == len(waters):
        keep("a water of the second pass is not one of the first")
    kept.append(j); j += 1
taken = sorted(set(range(len(waters))) - set(kept))
if sorted(next((w for w in taken if near(waters[w][0], unshift(tleap[prefix + i]))), -1) for i in range(k)) != taken:
    keep("tleap's ions are not on the oxygens of the waters it removed")

rng = random.Random(SEED)
left = list(range(len(waters)))
sites = [left.pop(rng.randrange(len(left))) for _ in range(k)]
chosen = set(sites)
placed = [waters[s][0] for s in sites] + [a for w in range(len(waters)) if w not in chosen for a in waters[w]]   # as savepdb has them
xyz = tleap[:prefix] + [shift(p) for p in placed]
flat = [v for p in xyz for v in p]
with open(INPCRD, "w") as f:
    f.write(lines[0] + "\\n" + lines[1] + "\\n")
    for i in range(0, len(flat), 6):
        f.write("".join("%12.7f" % v for v in flat[i:i + 6]) + "\\n")
    f.write(box_line + "\\n")
out, n = [], 0
for l in open(SOLV):
    if l.startswith(("ATOM", "HETATM")):
        if n >= prefix and n < natom:
            l = l[:30] + "%8.3f%8.3f%8.3f" % placed[n - prefix] + l[54:]
        n += 1
    out.append(l)
if n == natom:
    open(SOLV, "w").write("".join(out))
print("[hopemd] %d ions placed from seed %d (tleap drew from the clock): in place of box waters %s of %d"
      % (k, SEED, ", ".join(str(s + 1) for s in sites[:8]) + (" ..." if k > 8 else ""), len(waters)))
'''


class AmberEngine(Engine):
    key = "amber"
    label = "Amber (pmemd.cuda)"

    # ------------------------------------------------------------------ render
    def render(self, spec: Spec, derived: dict | None = None) -> Render:
        d = self.compute_derived(spec, derived)
        r = Render(engine=self.key, derived=d)
        sysm = spec.system
        pro = spec.protocol
        small = sysm.ligand_class == "small_molecule"
        apo = sysm.ligand_class == "none"
        rec = sysm.receptor_residues or [1, 1]
        lig = sysm.ligand_residues or [rec[1] + 1, rec[1] + 1]
        rec_mask = f":{rec[0]}-{rec[1]}"
        lig_mask = f":{lig[0]}-{lig[1]}"
        solute_mask = f":{rec[0]}-{lig[1]}" if not apo else rec_mask
        heavy_restraint = f"{solute_mask}&!@H="
        mem = membrane_of(spec)
        # A bilayer's topology carries the solute first (receptor, then partner), then lipids,
        # water and ions, so "everything after the solute" is what the stripped view and MM-GBSA
        # drop. A range needs no lipid names: hopemd.membrane names each lipid by its full name
        # (POPC, one residue per lipid), water WAT and ions Na+/Cl-/K+, and a range is right
        # whatever the names are. The ions the build carries are kept by their place in the same way
        # (kept_last): a carried Mg2+ or K+ bears the salt's name, and MM-GBSA counts it with the
        # side whose range holds it rather than strip it with the salt.
        strip_mask = f"!(:{rec[0]}-{kept_last(spec)})" if mem or carried_ions(spec) else SOLVENT_MASK

        # --- build ---
        if mem is None:
            r.add("build/tleap_pass1.in", self._tleap(spec, pass_no=1))
            r.add("build/tleap_pass2.in", self._tleap(spec, pass_no=2))
        r.add("build/hmr.parmed", "HMassRepartition\nparmout build/complex_hmr.prmtop\n")
        r.add("build/build.sh", self._build_sh(spec, small, mem), executable=True)

        # --- mdin ladder ---
        seg_steps = int(round(d["segment_ns"] * 1e6 / pro.timestep_fs))
        save_steps = int(round(pro.save_ps * 1000 / pro.timestep_fs))
        ntwr = self._ntwr(d["ns_per_day"], pro.timestep_fs, seg_steps, save_steps)
        r.add("md/01_min.in", self._min(spec, heavy_restraint))
        r.add("md/02_heat.in", self._heat(spec, heavy_restraint))
        for i, w in enumerate((pro.restraint_kcal, 1.0, 0.1, 0.0), start=1):
            r.add(f"md/03_eq{i}.in", self._eq(spec, heavy_restraint, w, i, mem))
        r.add("md/04_prod.in", self._prod(spec, seg_steps, save_steps, ntwr, mem))

        # --- analysis ---
        r.add("analysis/analysis.cpptraj.template",
              self._cpptraj(spec, rec_mask, lig_mask, solute_mask, apo, strip_mask, mem))
        if metal_mask(spec):
            r.add("analysis/view.cpptraj.template", self._view_cpptraj(spec))
        r.add("analysis/mmpbsa.in", self._mmpbsa_in(spec, strip_mask))
        r.add("analysis/ligand_mask.txt", f":{lig[0]}-{lig[1]}\n")   # read by the MM-GBSA job: re-rendered when Prepare was chained

        # --- jobs ---
        jobs: list[Job] = []
        if small:
            r.add("jobs/05_param.sbatch", self._param_sbatch(spec), executable=True)
            jobs.append(Job("param", "jobs/05_param.sbatch", description="antechamber + parmchk2"))
            r.notes.append("Small molecule: parameters are computed by antechamber (GAFF2, AM1-BCC) "
                           f"with net charge {sysm.ligand_charge:+d}; this is the slow setup step.")
        r.add("jobs/10_build.sbatch", self._build_sbatch(spec), executable=True)
        jobs.append(Job("build", "jobs/10_build.sbatch",
                        depends=[("param", "afterok")] if small else [],
                        description="membrane build (OpenMM Modeller), HMR" if mem else "tleap, HMR"))
        r.add("jobs/20_equil.sbatch", self._equil_sbatch(spec, mem), executable=True)
        jobs.append(Job("equil", "jobs/20_equil.sbatch", depends=[("build", "afterok")],
                        array=pro.replicates, gpu=True, description="minimize, heat, NPT ladder"))
        nseg = int(d["segments"])
        for k in range(1, nseg + 1):
            r.add(f"jobs/30_prod_s{k}.sbatch", self._prod_sbatch(spec, k, nseg, seg_steps, d["walltime"]),
                  executable=True)
            dep = ("equil", "aftercorr") if k == 1 else (f"prod_s{k-1}", "aftercorr")
            jobs.append(Job(f"prod_s{k}", f"jobs/30_prod_s{k}.sbatch", depends=[dep],
                            array=pro.replicates, gpu=True,
                            description=f"production segment {k} of {nseg}, {d['segment_ns']:g} ns"))
        last = f"prod_s{nseg}"
        r.add("jobs/40_analysis.sbatch", self._analysis_sbatch(spec), executable=True)
        jobs.append(Job("analysis", "jobs/40_analysis.sbatch", depends=[(last, "afterok")],
                        description="cpptraj: RMSD, ligand RMSD, RMSF, Rg, contacts, viewer trajectory"))
        if mmgbsa_runs(spec):
            r.add("jobs/50_mmgbsa.sbatch", self._mmgbsa_sbatch(spec, strip_mask), executable=True)
            jobs.append(Job("mmgbsa", "jobs/50_mmgbsa.sbatch", depends=[(last, "afterok")],
                            array=pro.replicates, description=self._mmgbsa_description(spec)))
        self.add_split_links(spec, r)
        self.add_tidy(spec, r, jobs)
        r.jobs = jobs
        r.add("jobs/submit.sh", self._submit_sh(spec, jobs), executable=True)
        r.add("README.txt", self._readme(spec, d, nseg, mem))

        if mem:
            r.notes.append(f"Bilayer: the solute is embedded in a {mem['lipid']} bilayer built by OpenMM's Modeller "
                           f"(lipid17) with {mem['padding_A']:g} Å of TIP3P water above and below, centered at "
                           f"z = {mem['center_z_A']:g} Å, {spec.box.salt_mM:g} mM {spec.box.salt}; no tleap. The membrane normal "
                           "must be z in the imported coordinates (an OPM entry is oriented that way). Pressure "
                           "coupling is semi-isotropic with zero surface tension; lipids are never restrained.")
            if any(j.key == "mmgbsa" for j in jobs):
                r.notes.append("MM-GBSA on a bilayer run strips the lipids with the water, so the interface is "
                               "scored in implicit solvent alone; read it as a trend between replicates, not an energy.")
        if sysm.ligand_class == "nucleic_acid" and any(j.key == "mmgbsa" for j in jobs):
            r.notes.append("MM-GBSA of a DNA or RNA partner: the generalized Born desolvation of a polyanion is large and can outweigh "
                           "the Coulomb attraction of a basic protein, so the total may come out positive; read it as a trend between "
                           "replicates and poses rather than as an absolute binding energy.")
        if sysm.breaks:
            capped = [b for b in sysm.breaks if b in set(sysm.capped or [])]
            open_ = [b for b in sysm.breaks if b not in set(capped)]
            if capped:
                r.notes.append(f"Chain breaks capped: {', '.join(capped)}. Prepare put an NME on the residue before each gap and an "
                               "ACE on the residue after it, so its ends are neutral amides, not charged termini the protein does "
                               "not have; the build ends the chain with TER between the two caps, and nothing is modeled into it.")
            if open_:
                r.notes.append(f"Chain breaks{' left open' if capped else ''}: {', '.join(open_)}. The build ends the chain at each "
                               "with TER, so each piece gets its own termini instead of a bond across the gap; nothing is modeled "
                               "into it.")
        if spec.forcefield.water != "OPC" and spec.forcefield.protein == "ff19SB":
            r.notes.append("ff19SB was fitted with OPC; running it with "
                           f"{spec.forcefield.water} is a documented mismatch.")
        if spec.box.salt == "MgCl2":
            r.notes.append("Mg2+ takes the Li/Merz 12-6 parameters its water model's leaprc loads (the OPC set of 2020, or the "
                           "TIP3P and TIP4P-Ew set of 2013), with two Cl- per Mg2+. A divalent ion in a nonpolarizable "
                           "model binds carboxylates too tightly, so read contacts made through Mg2+ with care.")
        return r

    # ------------------------------------------------------------------ tleap
    def _tleap(self, spec: Spec, pass_no: int) -> str:
        ff, box, sysm = spec.forcefield, spec.box, spec.system
        small = sysm.ligand_class == "small_molecule"
        apo = sysm.ligand_class == "none"
        wat_rc, wat_box = LEAPRC_WATER[ff.water]
        cation, anion = IONS[box.salt]
        L = [f"# HOPE-MD tleap pass {pass_no} — {spec.name}",
             f"source {LEAPRC_PROTEIN[ff.protein]}"]
        # DNA or RNA anywhere in the run, the receptor (a duplex alone, a quadruplex) as much as a partner
        if has_nucleic(sysm):
            if ff.nucleic not in LEAPRC_NUCLEIC:
                raise NotImplementedError(f"no tleap files for the nucleic-acid force field {ff.nucleic!r}: "
                                          f"choose {', '.join(LEAPRC_NUCLEIC)}")
            L += [f"source {x}" for x in LEAPRC_NUCLEIC[ff.nucleic]]
        L.append(f"source {wat_rc}")
        if small:
            L += ["source leaprc.gaff2", "loadamberparams build/lig.frcmod",
                  "LIG = loadmol2 build/lig.mol2"]
        L.append("rec = loadpdb build/receptor_prep.pdb")
        # Disulfides: pdb4amber (build.sh) renames the cysteines CYX and writes CONECT records,
        # and tleap turns those into the S-S bonds. An explicit `bond` on top of that is a
        # duplicate and tleap aborts on it ("1-4: cannot add bond"), so the import's list is
        # recorded here and not acted on.
        if sysm.disulfides:
            L.append(f"# {len(sysm.disulfides)} disulfide(s) from the import: "
                     + ", ".join(f"{a}-{b}" for a, b in sysm.disulfides)
                     + "; bonded through pdb4amber's CYX + CONECT records")
        if apo:
            L.append("complex = rec")
        elif small:
            L += ["lig = loadpdb build/ligand_prep.pdb", "complex = combine {rec lig}"]
        else:
            L += ["lig = loadpdb build/ligand_prep.pdb", "complex = combine {rec lig}"]
            # a disulfide across the split: pdb4amber reads the two files apart, so no CONECT joins them; build.sh
            # (hopemd.structure links) named both cysteines CYX and checked that these are their places in the unit,
            # the receptor's 1 to N and the partner's from N + 1
            for d in sysm.split_disulfides or []:
                p, q = d["residues"]
                L += [f"# disulfide {d['receptor']}-{d['partner']}, receptor to partner: topology residues {int(p)} and {int(q)}",
                      f"bond complex.{int(p)}.SG complex.{int(q)}.SG"]
        if carried_ions(spec):
            # the ions Clean kept, carried past protonation by Prepare, go last: those that bind the partner
            # first, which its residue range takes, the rest outside every range a mask is built from
            L += ["ions = loadpdb build/ions_prep.pdb", "desc ions", "complex = combine {complex ions}"]
        if crystal_waters(spec):
            # the crystal waters kept, after the ions: tleap names them after the water model's unit and adds a
            # four-site water's extra point. As solute residues (restype undefined) addionsrand cannot put a
            # salt ion in place of one, and the solvation places no water over them
            L += ["waters = loadpdb build/waters_prep.pdb", "set waters restype undefined", "desc waters",
                  "complex = combine {complex waters}"]
        L.append("check complex")
        solv = {"compact": "solvateoct", "cubic": "solvatebox", "rectangular": "solvatebox"}[box.shape]
        iso = " iso" if box.shape == "cubic" else ""
        L.append(f"{solv} complex {wat_box} {box.padding_A:g}{iso}")
        if pass_no == 1:
            L += ["savepdb complex build/complex_pass1.pdb", "quit"]
            return "\n".join(L) + "\n"
        # pass 2: neutralize, then salt. NPAIR is substituted by build.sh after counting waters.
        L += [f"addionsrand complex {cation} 0", f"addionsrand complex {anion} 0",
              # a divalent cation takes two anions per formula unit: equal numbers of Mg2+ and Cl- left an MgCl2
              # box charged +NPAIR (NANION is 2 x NPAIR for MgCl2, NPAIR otherwise; build.sh fills both)
              f"addionsrand complex {cation} NPAIR {anion} NANION",
              "saveamberparm complex build/complex.prmtop build/complex.inpcrd",
              "savepdb complex build/complex_solv.pdb", "quit"]
        return "\n".join(L) + "\n"

    def split_link_note(self, spec: Spec) -> str:
        how = "OpenMM's Modeller bonds them as it builds the bilayer" if membrane_of(spec) else "tleap bonds them"
        return f"pdb4amber reads the two apart and bonds neither, so the build names both cysteines CYX and {how}"

    def _build_sh(self, spec: Spec, small: bool, mem: dict | None = None) -> str:
        sysm, box = spec.system, spec.box
        cation, anion = IONS[box.salt]
        # the crystal waters a run keeps come from input/waters.pdb (crystal_waters), never with the chains
        dry = "--dry "
        lig_prep = ""
        pdb_partner = sysm.ligand_class in ("peptide", "protein", "nucleic_acid")
        if pdb_partner:
            # a partner made of chains (a peptide, the other chain group of a complex, a DNA or RNA strand) is a
            # PDB like the receptor, numbered for pdb4amber as the receptor is; without these lines tleap finds no
            # build/ligand_prep.pdb
            lig_prep = ("NUMBER input/ligand.pdb build/ligand_numbered.pdb build/ligand_numbering.json\n"
                        "pdb4amber -i build/ligand_numbered.pdb -o build/ligand_prep.pdb --nohyd --dry\n")
        elif small:
            lig_prep = ("# small molecule: antechamber output from jobs/05_param.sbatch is in build/lig.mol2\n"
                        "test -s build/lig.mol2 || { echo 'build/lig.mol2 missing: run the param job first'; exit 2; }\n"
                        "antechamber -i build/lig.mol2 -fi mol2 -o build/ligand_prep.pdb -fo pdb -dr no\n")
        if sysm.prepared:
            # the Prepare stage already ran propka at the pH and wrote HID/HIE/HIP/ASH/GLH/LYN
            # into input/receptor.pdb; pdb4amber --nohyd keeps the names, tleap reads them
            ph_block = (
                f"echo '[hopemd] protonation states came with the Prepare stage (propka at pH {sysm.ph:g}); pdb2pqr not run again'\n"
                "cp build/receptor_4amber.pdb build/receptor_prep.pdb\n")
        else:
            # what protonates when pdb2pqr cannot: tleap's defaults, or the membrane build's
            fallback = "the default protonation of the membrane build" if mem else "tleap default protonation"
            fallback_note = ("the default protonation of the membrane build" if mem
                             else "tleap default protonation (HIE, ASP-, GLU-, LYS+, ARG+)")
            ph_block = (
            # the engine module's node has no pdb2pqr; the interpreter that rendered this run
            # (HOPEMD_PYTHON, exported by the job preamble) usually has, and pdb2pqr 3.6 reads
            # pdb4amber's CYX + CONECT output fine. It runs isolated (-I): the engine module puts
            # its own Python's site-packages on PYTHONPATH, and a numpy built for that Python
            # breaks the import.
            'P2P=""\n'
            'if [ -n "${HOPEMD_PYTHON:-}" ] && "$HOPEMD_PYTHON" -I -c "import pdb2pqr" >/dev/null 2>&1; then P2P="$HOPEMD_PYTHON -I -m pdb2pqr"\n'
            "elif command -v pdb2pqr30 >/dev/null 2>&1; then P2P=pdb2pqr30\n"
            "elif command -v pdb2pqr >/dev/null 2>&1; then P2P=pdb2pqr; fi\n"
            'if [ -n "$P2P" ]; then\n'
            f"  $P2P --ff=AMBER --ffout=AMBER --with-ph {sysm.ph:g} --titration-state-method propka "
            "--keep-chain --drop-water build/receptor_4amber.pdb build/receptor_pqr.pdb > build/pdb2pqr.log 2>&1 \\\n"
            "    && awk '$1==\"ATOM\"||$1==\"HETATM\"||$1==\"TER\"||$1==\"END\"' build/receptor_pqr.pdb "
            "| cut -c1-54 > build/receptor_ph.pdb \\\n"
            # pdb2pqr writes the chains in an order of its own (H before L): back in the build's, which the ranges count
            '    && PYTHONPATH="$HOPEMD_HOME" "${HOPEMD_PYTHON:-python3}" -s -m hopemd.structure order build/receptor_ph.pdb \\\n'
            "    && pdb4amber -i build/receptor_ph.pdb -o build/receptor_prep.pdb --nohyd \\\n"
            f"    && echo \"[hopemd] protonation states from propka at pH {sysm.ph:g}\" \\\n"
            f"    || {{ echo '[hopemd] pdb2pqr failed; falling back to {fallback}'; "
            "cp build/receptor_4amber.pdb build/receptor_prep.pdb; }\n"
            "else\n"
            f"  echo '[hopemd] pdb2pqr not found: {fallback_note}; pH {sysm.ph:g} not applied'\n"
            "  cp build/receptor_4amber.pdb build/receptor_prep.pdb\n"
            "fi\n")
        # the chains as the topology build must read them, on every route: pdb2pqr writes TER only where a chain
        # ends and names nucleotides as Amber did before OL3, so each break gets its TER again (C..N or O3'..P too
        # far apart to be a bond), the nucleotides the names OL3 and OL21 use, and the first nucleotide of each strand
        # loses a 5'-terminal phosphate the force fields have no template for. Each file goes with the numbering it was
        # given, which puts back the TERs pdb2pqr drops at the breaks found on the structure's own ids
        files = "build/receptor_prep.pdb --numbering build/receptor_numbering.json" \
            + (" build/ligand_prep.pdb --numbering build/ligand_numbering.json" if pdb_partner else "")
        chain_block = (
            f'PYTHONPATH="$HOPEMD_HOME" "${{HOPEMD_PYTHON:-python3}}" -s -m hopemd.structure chains {files} \\\n'
            "  || { echo '[hopemd] the chains could not be read for the build (hopemd.structure chains)'; exit 3; }\n")
        if pdb_partner:
            # a disulfide between the receptor and the partner: both cysteines CYX for the bond the topology makes (tleap's
            # bond, or OpenMM's Modeller on a bilayer), and the build stops if the files hold any the run does not bond
            bonds = "".join(f" --bond {int(d['residues'][0])} {int(d['residues'][1])}" for d in sysm.split_disulfides or [])
            chain_block += (
                'PYTHONPATH="$HOPEMD_HOME" "${HOPEMD_PYTHON:-python3}" -s -m hopemd.structure links build/receptor_prep.pdb '
                "build/receptor_numbering.json build/ligand_prep.pdb build/ligand_numbering.json build/links.json"
                f"{bonds} \\\n"
                "  || { echo '[hopemd] the disulfides across the split are not those the run bonds (hopemd.structure links)'; exit 3; }\n")
        ions = carried_ions(spec)
        ion_prep = ""
        who = "Prepare carried past protonation" if sysm.prepared else "the import kept"
        if ions and mem:
            # the bilayer build reads the solute's files: the ions go at the end of the last one, after the partner,
            # once the chains are ready
            last = "build/ligand_prep.pdb" if lig_prep else "build/receptor_prep.pdb"
            ion_prep = (f"echo '[hopemd] the ions {who} go in after the solute: {', '.join(ions)}'\n"
                        f"grep -v '^END' {last} > build/solute_ions.pdb; cat input/ions.pdb >> build/solute_ions.pdb; "
                        f"mv -f build/solute_ions.pdb {last}\n")
        elif ions:
            ion_prep = (f"echo '[hopemd] the ions {who} go in after the solute: {', '.join(ions)}'\n"
                        "cp input/ions.pdb build/ions_prep.pdb\n")
        waters = crystal_waters(spec)
        if waters and not mem:
            ion_prep += (f"echo '[hopemd] {len(waters)} crystal water(s) kept go in after the solute and its ions, as "
                         f"{spec.forcefield.water} waters (input/waters.pdb)'\n"
                         "cp input/waters.pdb build/waters_prep.pdb\n")
        # a seed of the run's own places the ions too: addionsrand draws from the clock, so they are drawn again from it
        seeded = ""
        if spec.protocol.seed >= 0:
            seeded = (f"echo \"[hopemd] the ions: addionsrand drew their places from the clock; drawn again from the run's seed ({int(spec.protocol.seed)})\"\n"
                      "python3 - <<'EOF'\n" + seeded_ions_script(int(spec.protocol.seed), WATER_SITES.get(spec.forcefield.water, 3)) + "EOF\n")
        what = "embed in a bilayer, ionize, HMR" if mem else "solvate, ionize, HMR"
        head = f"""#!/bin/bash
# HOPE-MD build for {spec.name}: prepare, {what}. Run from the run folder.
set -euo pipefail
mkdir -p build
HOPEMD_HOME="${{HOPEMD_HOME:-{hopemd_home()}}}"
# pdb4amber reads with ParmEd, which bonds a residue to the next only when their numbers follow: insertion codes
# (60A, 60B ...) and numbers a scheme skips would each cut the chain, with charged termini either side. Each file goes
# to it numbered 1, 2, 3 ... in file order, a TER at every real break (found on the file's own ids), and those ids
# kept in build/<part>_numbering.json for whatever names a residue of the topology
NUMBER() {{
  PYTHONPATH="$HOPEMD_HOME" "${{HOPEMD_PYTHON:-python3}}" -s -m hopemd.structure number "$@" \\
    || {{ echo "[hopemd] $1 could not be numbered for the build (hopemd.structure number)"; exit 3; }}
}}
echo "[hopemd] prepare receptor"
NUMBER input/receptor.pdb build/receptor_numbered.pdb build/receptor_numbering.json
pdb4amber -i build/receptor_numbered.pdb -o build/receptor_4amber.pdb --nohyd {dry}> build/pdb4amber_receptor.log 2>&1
{ph_block}{lig_prep}{chain_block}{ion_prep}
"""
        if mem:
            return head + self._membrane_build(spec, mem, with_ligand=bool(lig_prep))
        return head + f"""echo "[hopemd] tleap pass 1: solvate and count waters"
tleap -s -f build/tleap_pass1.in > build/tleap_pass1.log
NW=$(grep -c ' O   WAT' build/complex_pass1.pdb || true)
NPAIR=$(python3 -c "print(round({box.salt_mM:g}/1000.0*$NW/55.5))")
NANION=$(( NPAIR * {2 if box.salt == "MgCl2" else 1} ))
echo "[hopemd] $NW waters -> $NPAIR {cation} and $NANION {anion} for {box.salt_mM:g} mM {box.salt}"
sed "s/NANION/$NANION/g; s/NPAIR/$NPAIR/g" build/tleap_pass2.in > build/tleap_pass2.run.in
echo "[hopemd] tleap pass 2: ions and topology"
tleap -s -f build/tleap_pass2.run.in > build/tleap_pass2.log
# tleap prints "teLeap: Warning!" and the message on the next line; show the messages, once each
awk '/teLeap: (Warning|Error|Fatal Error)!/{{getline; print}}' build/tleap_pass2.log | grep -viE 'Close contact|nonstandard' | sort | uniq -c | sort -rn | head -20 || true
test -s build/complex.prmtop || {{ echo '[hopemd] tleap wrote no topology; see build/tleap_pass2.log'; exit 3; }}
{seeded}echo "[hopemd] hydrogen mass repartitioning"
parmed -O -p build/complex.prmtop -i build/hmr.parmed > build/parmed.log
NATOM=$(awk '/FLAG POINTERS/{{getline; getline; print $1; exit}}' build/complex.prmtop)
BOX=$(tail -1 build/complex.inpcrd | awk '{{printf "%.1f x %.1f x %.1f", $1, $2, $3}}')
NION=$(grep -cE ' (Na\\+|Cl-|K\\+|MG) ' build/complex_solv.pdb || true)
cat > build/system.json <<EOF
{{"atoms": $NATOM, "waters": $NW, "ion_pairs": $NPAIR, "ions": $NION, "box": "$BOX", "hmr": true}}
EOF
echo "[hopemd] built: $NATOM atoms, $NW waters, box $BOX"
"""

    @staticmethod
    def _membrane_build(spec: Spec, mem: dict, with_ligand: bool) -> str:
        """The bilayer in place of the two tleap passes: hopemd.membrane embeds the prepared
        solute with OpenMM's Modeller and writes complex.prmtop / complex.inpcrd / complex_solv.pdb
        / system.json through ParmEd; HMR then runs on that topology as it does on tleap's."""
        lig = " --ligand build/ligand_prep.pdb" if with_ligand else ""
        # DNA or RNA in the bilayer run takes the run's nucleic-acid force field, as tleap would source it
        nuc = f" --nucleic {spec.forcefield.nucleic}" if has_nucleic(spec.system) and spec.forcefield.nucleic else ""
        # a seed of the run's own: Modeller's hydrogens, ions and relaxation draw from it, so the bilayer repeats
        seed = f" --seed {int(spec.protocol.seed)}" if spec.protocol.seed >= 0 else ""
        return f"""echo "[hopemd] membrane build: {mem['lipid']} bilayer, {mem['padding_A']:g} A of water above and below (OpenMM Modeller, lipid17)"
# hopemd.membrane needs OpenMM and ParmEd, which the server's interpreter (HOPEMD_PYTHON) has and the
# engine module's Python has not. -I as on the pdb2pqr line would drop PYTHONPATH and with it this
# checkout (the group env does not install hopemd), so PYTHONPATH is set to the
# checkout alone, which replaces the module's site-packages the preamble exported through it; -s keeps
# $HOME/.local out and -P the run folder. The Modeller relaxation runs on OpenMM's CPU platform, which
# starts one thread per core of the node unless told the allocation's count. The pH is the run's: the
# hydrogens go on here (pdb4amber --nohyd took them off), the states pdb2pqr named are kept.
HOPEMD_HOME="${{HOPEMD_HOME:-{hopemd_home()}}}"
OPENMM_CPU_THREADS=${{SLURM_CPUS_PER_TASK:-4}} PYTHONPATH="$HOPEMD_HOME" "$HOPEMD_PYTHON" -s -P -m hopemd.membrane build --receptor build/receptor_prep.pdb{lig} \\
  --lipid {mem['lipid']} --padding-A {mem['padding_A']:g} --center-z-A {mem['center_z_A']:g} --salt-mM {spec.box.salt_mM:g} --ions {spec.box.salt} --ph {spec.system.ph:g} --out build{nuc}{seed} \\
  > build/membrane.log 2>&1 || {{ tail -30 build/membrane.log; echo '[hopemd] the membrane build failed; see build/membrane.log'; exit 3; }}
test -s build/complex.prmtop || {{ echo '[hopemd] the membrane build wrote no topology (build/complex.prmtop); see build/membrane.log'; exit 3; }}
test -s build/complex.inpcrd || {{ echo '[hopemd] the membrane build wrote no coordinates (build/complex.inpcrd); see build/membrane.log'; exit 3; }}
echo "[hopemd] hydrogen mass repartitioning"
parmed -O -p build/complex.prmtop -i build/hmr.parmed > build/parmed.log
# the membrane build wrote build/system.json (atoms, waters, ion_pairs, lipids, box); record the HMR beside them
python3 -c "import json; p='build/system.json'; d=json.load(open(p)); d['hmr']=True; json.dump(d, open(p, 'w'))"
echo "[hopemd] built: $(cat build/system.json)"
"""

    # ------------------------------------------------------------------ mdin
    def _common(self, spec: Spec, shake: bool = True) -> str:
        # ig is filled by the job per replicate (and per production segment): HOPEMD_SEED -> -1 (Amber's
        # own random seed) when protocol.seed is -1, else protocol.seed + 1000 x replicate + 100 x segment,
        # so a fixed seed reproduces a run and its replicates still differ. The LJ past the cutoff is pmemd's
        # default long-range correction to energy and pressure (vdwmeth = 1), which the other engines apply too
        return f"cut={CUTOFF_A:g}, {'ntc=2, ntf=2' if shake else 'ntc=1, ntf=1'}, ig=HOPEMD_SEED,"

    @staticmethod
    def seed_line(spec: Spec, stage: str) -> str:
        """The per-replicate seed the job scripts fill in (bash, from the array index)."""
        seed = spec.protocol.seed
        if seed < 0:
            return "SEED=-1   # protocol.seed is -1: the engine draws its own seed, one per replicate"
        return f"SEED=$(( {seed} + 1000 * SLURM_ARRAY_TASK_ID{' + 100 * SEG_NO' if stage == 'prod' else ''} ))   # protocol.seed, offset per replicate" + (" and segment" if stage == "prod" else "")

    def _min(self, spec: Spec, mask: str) -> str:
        """The restrained minimization, without SHAKE: with ntc=2 pmemd's minimizer stops after about 100
        steps (a 1BHL complex in OPC held -8.79e4 kcal/mol and an RMS gradient of 19.8 from step 100 to
        5000), while unconstrained it goes on to -1.354e5 and 0.19 in the same 5000 steps."""
        pro = spec.protocol
        return f"""minimization, solute heavy atoms restrained
 &cntrl
  imin=1, maxcyc={pro.minimise_steps}, ncyc={pro.minimise_steps // 2},
  ntb=1, {self._common(spec, shake=False)}
  ntpr=100,
  ntr=1, restraint_wt=10.0, restraintmask='{mask}',
 /
"""

    def _heat(self, spec: Spec, mask: str) -> str:
        pro = spec.protocol
        dt = 0.002                                  # ps: the ladder runs at 2 fs
        # steps = ps / dt with both in picoseconds: dividing by 2 (the step in fs) would run the
        # ladder a thousand times too short
        steps = int(round(pro.heat_ps / dt))
        ramp_end = int(steps * 0.9)
        return f"""heat 0 -> {pro.temperature_K:g} K under NVT, solute heavy atoms restrained
 &cntrl
  imin=0, irest=0, ntx=1,
  ntb=1, {self._common(spec)}
  tempi=0.0, temp0={pro.temperature_K:g}, ntt=3, gamma_ln=1.0,
  nstlim={steps}, dt={dt},
  ntpr=500, ntwx=0, ntwr={steps},
  ntr=1, restraint_wt={pro.restraint_kcal:g}, restraintmask='{mask}',
  nmropt=1,
 /
 &wt type='TEMP0', istep1=0, istep2={ramp_end}, value1=0.0, value2={pro.temperature_K:g}, /
 &wt type='TEMP0', istep1={ramp_end + 1}, istep2={steps}, value1={pro.temperature_K:g}, value2={pro.temperature_K:g}, /
 &wt type='END', /
"""

    @staticmethod
    def _npt(spec: Spec, mem: dict | None) -> str:
        """The pressure line of the NPT stages. A bilayer is coupled semi-isotropically: x and y
        scale together and z on its own (ntp=3) at zero surface tension (csurften=3, gamma_ten=0,
        two interfaces), so the area per lipid settles where the force field puts it; the Monte
        Carlo barostat (barostat=2) as in the water box."""
        pro = spec.protocol
        if mem:
            return f"ntb=2, ntp=3, barostat=2, csurften=3, gamma_ten=0.0, ninterface=2, pres0={pro.pressure_bar:g}, taup=2.0,"
        return f"ntb=2, ntp=1, barostat=2, pres0={pro.pressure_bar:g}, taup=2.0,"

    def _eq(self, spec: Spec, mask: str, weight: float, stage: int, mem: dict | None = None) -> str:
        pro = spec.protocol
        dt = 0.002                                  # ps, as in _heat: steps = ps / dt
        steps = int(round(pro.equil_ps / 4 / dt))
        restr = (f"  ntr=1, restraint_wt={weight:g}, restraintmask='{mask}',\n" if weight > 0
                 else "  ntr=0,\n")
        return f"""equilibration {stage}/4 under NPT, restraint {weight:g} kcal/mol/A^2
 &cntrl
  imin=0, irest=1, ntx=5,
  {self._npt(spec, mem)}
  {self._common(spec)}
  temp0={pro.temperature_K:g}, ntt=3, gamma_ln=1.0,
  nstlim={steps}, dt={dt},
  ntpr=1000, ntwx=0, ntwr={steps},
{restr} /
"""

    def _prod(self, spec: Spec, seg_steps: int, save_steps: int, ntwr: int, mem: dict | None = None) -> str:
        pro = spec.protocol
        restr = "  ntr=0,\n"
        if pro.restrain:
            nums = ",".join(str(_resnum(k, spec.system)) for k in pro.restrain)
            restr = f"  ntr=1, restraint_wt={pro.restraint_kcal:g}, restraintmask=':{nums}@CA,C,N',\n"
            if has_nucleic(spec.system):
                # a held nucleotide is held by its P and C4'; the prime of C4' needs the mask in double quotes
                restr = f"  ntr=1, restraint_wt={pro.restraint_kcal:g}, restraintmask=\":{nums}@{','.join(HOLD_ATOMS)}\",\n"
        return f"""production, one segment; NSTLIM is replaced per segment or on resume
 &cntrl
  imin=0, irest=1, ntx=5,
  {self._npt(spec, mem)}
  {self._common(spec)}
  temp0={pro.temperature_K:g}, ntt=3, gamma_ln=1.0,
  nstlim={seg_steps}, dt={pro.timestep_fs / 1000:g},
  ntpr={save_steps}, ntwx={save_steps}, ntwr={ntwr}, ioutfm=1, iwrap=1,
{restr} /
"""

    @staticmethod
    def _ntwr(ns_per_day: float, dt_fs: float, seg_steps: int, save_steps: int = 1000) -> int:
        """Steps between restarts: about CHECKPOINT_MINUTES at the estimated rate, in whole frames
        (save_steps) and at least 5 000 steps, never past the segment. pmemd counts a part's frames
        from the part's first step, so a part resumed from a restart on a frame writes its frames
        where the stopped part wrote them."""
        steps_per_min = ns_per_day / 24 / 60 * 1e6 / dt_fs
        n = int(round(steps_per_min * CHECKPOINT_MINUTES / save_steps)) * save_steps
        return max(-(-5000 // save_steps) * save_steps, min(n, seg_steps))

    @staticmethod
    def _segment_bound(j: int, nseg: int, seg_steps: int, save_steps: int, steps: int) -> int:
        """The production step segment j ends on (0 for j = 0), on a frame: pmemd counts a part's
        frames from the part's own first step, so a segment that started between two frames would
        put every frame after it off the production's save interval. The last segment ends at the
        production's own length (`steps`), which segments of a rounded length could miss by a few."""
        if j >= nseg:
            return steps
        return int(round(j * seg_steps / save_steps)) * save_steps

    # ------------------------------------------------------------------ analysis
    def _cpptraj(self, spec: Spec, rec_mask: str, lig_mask: str, solute_mask: str, apo: bool,
                 strip_mask: str = SOLVENT_MASK, mem: dict | None = None) -> str:
        # nativecontacts takes the first frame as its reference when no `reference`/`ref` keyword
        # is given; `reference` alone asks for a loaded reference structure and cpptraj aborts
        # with "Reference index 0 not found"
        stride = int(spec.analysis.get("viewer_stride", 20))
        lig_block = "" if apo else f"""rms LigNoFit {lig_mask}&!@H= nofit out analysis/rREP_rmsd_lig.dat
nativecontacts name NC {lig_mask}&!@H= {rec_mask}&!@H= byresidue distance 4.5 \\
  out analysis/rREP_contacts.dat resout analysis/rREP_contact_res.dat \\
  writecontacts analysis/rREP_native.dat
hbond HB {solute_mask} out analysis/rREP_hbond_count.dat avgout analysis/rREP_hbond_avg.dat series
"""
        # the chains are traced by Cα, and a nucleotide by its P and C4' (trace_mask): a Cα-only mask would
        # select nothing of DNA or RNA
        trace = trace_mask(spec.system)
        # a protein or nucleic-acid partner is an interface, not a ligand: the trace atoms within 8 Å of the
        # other side, measured after the receptor fit and without their own fit, and the buried
        # surface (Connolly, mask atoms only) as rec + partner - complex per frame. The distance
        # mask is evaluated on the starting structure, loaded as the reference for it.
        interface = spec.system.ligand_class in ("protein", "nucleic_acid")
        iface_block = "" if not interface else f"""rms IfaceNoFit ({lig_mask}<:8.0)&{rec_mask}{trace}|({rec_mask}<:8.0)&{lig_mask}{trace} nofit out analysis/rREP_rmsd_iface.dat
molsurf SAcom {solute_mask} out analysis/rREP_sa_complex.dat
molsurf SArec {rec_mask} out analysis/rREP_sa_rec.dat
molsurf SApar {lig_mask} out analysis/rREP_sa_partner.dat
"""
        ref_line = "" if not interface else "reference build/complex.inpcrd [start]\n"
        # a bilayer: the box lengths per frame, before the strip; summarise turns x*y over the
        # lipids of one leaflet into the area per lipid, the number that says the membrane settled
        box_line = "" if not mem else "vector vbox box out analysis/rREP_box.dat\n"
        return f"""# HOPE-MD analysis for {spec.name}, replicate REP (filled by the job script)
parm build/complex.prmtop
{ref_line}TRAJIN
autoimage
{box_line}strip {strip_mask} outprefix stripped
rms RecFit {rec_mask}{trace} first out analysis/rREP_rmsd_rec.dat
{lig_block}{iface_block}atomicfluct out analysis/rREP_rmsf.dat {rec_mask}{trace} byres
radgyr out analysis/rREP_rg.dat {solute_mask} mass nomax
trajout analysis/rREP_view.pdb pdb offset {stride}
run
"""

    def _view_cpptraj(self, spec: Spec) -> str:
        """The viewer's trajectory for a run that carries a metal, made from the solvated frames.

        A metal site is made by its water as much as by its protein donors, and the stripped
        trajectory the rest of the analysis works on cannot show a water arriving or leaving. This
        keeps, in every frame, the waters nearest the metals - cpptraj's `closest`, which chooses
        them again at each frame - and drops the bulk, the counter-ions and the hydrogens, which
        nothing draws. It runs after the main pass and only replaces the stripped view on success,
        so a run whose second pass fails still has a trajectory to open."""
        stride = int(spec.analysis.get("viewer_stride", 20))
        mask = metal_mask(spec)
        # the salt goes by its place, not by its name: a run whose carried ion is itself a K+ or a
        # Na+ (the cations a nucleic acid binds) would lose it to a strip that named the salt.
        # Everything up to the last carried ion stays, and so does the water, which closest then
        # picks from; what is left between them is the salt.
        keep = f":1-{kept_last(spec)}|:WAT,HOH,TIP3"
        return f"""# HOPE-MD viewer trajectory for {spec.name}, replicate REP (filled by the job script)
parm build/complex.prmtop
TRAJIN
autoimage anchor {mask}
strip !({keep})
closest {VIEW_WATERS} {mask} first
strip @H=
trajout analysis/rREP_view_water.pdb pdb offset {stride}
run
"""

    @staticmethod
    def _mmgbsa_description(spec: Spec) -> str:
        from ..spec import mmgbsa_window, mmgbsa_frames
        a0, a1, st = mmgbsa_window(spec)
        return f"MMPBSA.py over {mmgbsa_frames(spec)} frames, {a0:g}-{a1:g} ns every {st:g} ps"

    def _mmpbsa_in(self, spec: Spec, strip_mask: str = SOLVENT_MASK) -> str:
        # the window of the production the energies come from: frames are numbered over the parts
        # in order, one every save_ps; startframe/endframe/interval are MMPBSA.py's own keywords
        from ..spec import mmgbsa_window
        a0, a1, st = mmgbsa_window(spec)
        save = spec.protocol.save_ps
        interval = max(1, int(round(st / save)))
        start = int(a0 * 1000 / save) + 1
        end = int(round(a1 * 1000 / save))
        # use_sander=1: MMPBSA.py's own GB code (mmpbsa_py_energy) and sander agree under igb=8 on proteins, not
        # on phosphates or nucleic acids, whose GBneck2 parameters sander carries. Over the same frames an ATP pose
        # scored -424 kcal/mol with mmpbsa_py_energy and +12 with sander (+14 and +16 under igb 2 and 5, -6 by PB),
        # and a DNA partner differed by 16 kcal/mol. Sander takes about as long.
        return f"""HOPE-MD MM-GBSA over frames {start}-{end} (of the production, {a0:g}-{a1:g} ns), every {interval} ({st:g} ps)
&general
  startframe={start}, endframe={end}, interval={interval}, verbose=2, keep_files=0, use_sander=1,
  strip_mask='{strip_mask}',
/
&gb
  igb=8, saltcon={spec.box.salt_mM / 1000:g},
/
"""

    # ------------------------------------------------------------------ sbatch
    def _param_sbatch(self, spec: Spec) -> str:
        q = spec.system.ligand_charge or 0
        return (self.sbatch_header(spec, "param", gpu=False, hours=2.0, cpus=4, mem_gb=8)
                + self.preamble(spec, "amber") + f"""
mkdir -p build
LIG=$(ls input/ligand.* | head -1)
case "${{LIG##*.}}" in sdf|sd|mol) FI=mdl;; mol2) FI=mol2;; pdb) FI=pdb;; *) echo "[hopemd] unknown ligand format $LIG"; exit 2;; esac
echo "[hopemd] antechamber GAFF2 / AM1-BCC on $LIG (-fi $FI), net charge {q:+d}"
antechamber -i "$LIG" -fi "$FI" -o build/lig.mol2 -fo mol2 -c bcc -nc {q} -at gaff2 -rn LIG -pf y -dr no
parmchk2 -i build/lig.mol2 -f mol2 -o build/lig.frcmod -s gaff2
grep -c ATTN build/lig.frcmod && echo "[hopemd] some parameters are guessed (ATTN); read build/lig.frcmod" || true
echo "[hopemd] param done"
""")

    def _build_sbatch(self, spec: Spec) -> str:
        return (self.sbatch_header(spec, "build", gpu=False, hours=1.0, cpus=4, mem_gb=16)
                + self.preamble(spec, "amber") + "\nbash build/build.sh\n")

    @staticmethod
    def _md_command(spec: Spec) -> str:
        if spec.on_cpu:
            return 'MD="mpirun -np $SLURM_NTASKS pmemd.MPI"   # CPU check: one whole node'
        return 'MD="pmemd.cuda"'

    def _equil_sbatch(self, spec: Spec, mem: dict | None = None) -> str:
        pro = spec.protocol
        est_h = 6.0 if spec.on_cpu else 1.5   # min + 200 ps heat + 1 ns NPT
        if mem and not spec.on_cpu:
            est_h = 3.0                        # a bilayer system is two to three times the atoms of a water box
        return (self.sbatch_header(spec, "equil", gpu=True, hours=est_h, array=pro.replicates,
                                   throttle=spec.compute.max_concurrent)
                + self.preamble(spec, "amber") + f"""
{self._md_command(spec)}
R=r${{SLURM_ARRAY_TASK_ID}}
mkdir -p "$R"; cd "$R"
P=../build/complex_hmr.prmtop
REF=../build/complex.inpcrd
{self.seed_line(spec, "equil")}
run() {{ # stage, mdin, coords
  echo "[hopemd] $1"
  sed "s/HOPEMD_SEED/$SEED/" ../md/$2 > $1.in
  $MD -O -i $1.in -o $1.out -p $P -c $3 -ref $REF -r $1.rst7 -x $1.nc -inf $1.mdinfo
}}
run min  01_min.in  ../build/complex.inpcrd
run heat 02_heat.in min.rst7
run eq1  03_eq1.in  heat.rst7
run eq2  03_eq2.in  eq1.rst7
run eq3  03_eq3.in  eq2.rst7
run eq4  03_eq4.in  eq3.rst7
echo "[hopemd] equilibrated replicate $SLURM_ARRAY_TASK_ID"
""")

    def _prod_sbatch(self, spec: Spec, k: int, nseg: int, seg_steps: int, walltime: str) -> str:
        pro = spec.protocol
        prev = "eq4.rst7" if k == 1 else f"prod_s{k-1}.rst7"
        save_steps = int(round(pro.save_ps * 1000 / pro.timestep_fs))
        steps = int(round(pro.length_ns * 1e6 / pro.timestep_fs))
        total = (self._segment_bound(k, nseg, seg_steps, save_steps, steps)
                 - self._segment_bound(k - 1, nseg, seg_steps, save_steps, steps))
        return (self.sbatch_header(spec, f"prod_s{k}", gpu=True, walltime=walltime, array=pro.replicates,
                                   throttle=spec.compute.max_concurrent)
                + self.preamble(spec, "amber") + f"""
{self._md_command(spec)}
R=r${{SLURM_ARRAY_TASK_ID}}
cd "$R"
P=../build/complex_hmr.prmtop
SEG=s{k}; SEG_NO={k}; TOTAL={total}; DT={pro.timestep_fs / 1000:g}; SAVE_PS={pro.save_ps:g}; PREV={prev}
PARTS="${{HOPEMD_PYTHON:-python3}} -m hopemd.analysis.parts"
# Resume: a part that was stopped (walltime or a kill) left its restart, prod_${{SEG}}_p<n>.rst7, written
# every ntwr steps on a frame. The time in the restart, less the time in $PREV, is how far the segment got
# (the mdinfo's count runs ahead of the restart), and the next part runs the rest of the segment from it.
# The frames the stopped part wrote past its restart are simulated again by the next part, so they are
# cut from it first: cpptraj reads prod_${{SEG}}_p*.nc in order and meets every frame once.
DONE=0; PART=1; START=$PREV; LASTP=""; T0=""
for f in $(ls prod_${{SEG}}_p*.rst7 2>/dev/null | sort -V); do
  [ -n "$T0" ] || T0=$($PARTS time "$PREV") || {{ echo "[hopemd] cannot read the time in $PREV"; exit 3; }}
  T=$($PARTS time "$f") || continue         # a restart cut off as it was written: its part runs again
  n=$(awk -v a="$T0" -v b="$T" -v dt=$DT 'BEGIN{{printf "%d", (b - a) / dt + 0.5}}')
  if [ "$n" -gt 0 ]; then DONE=$n; START=$f; TSTART=$T; PART=$((PART+1)); LASTP=${{f%.rst7}}; fi
done
LEFT=$((TOTAL-DONE))
if [ "$LEFT" -le 0 ]; then echo "[hopemd] segment $SEG already complete"; cp -f "$START" prod_${{SEG}}.rst7; exit 0; fi
if [ -n "$LASTP" ] && [ -s "$LASTP.nc" ]; then
  $PARTS cut "$LASTP.nc" --ps "$TSTART" --save-ps $SAVE_PS \\
    || {{ echo "[hopemd] could not cut $LASTP.nc at its restart ($TSTART ps); stopped here, as its frames past it would repeat"; exit 3; }}
fi
{self.seed_line(spec, "prod")}
sed "s/nstlim=[0-9]*/nstlim=$LEFT/; s/HOPEMD_SEED/$SEED/" ../md/04_prod.in > prod_${{SEG}}_p${{PART}}.in
echo "[hopemd] production $SEG part $PART: $LEFT of $TOTAL steps from $START"
$MD -O -i prod_${{SEG}}_p${{PART}}.in -o prod_${{SEG}}_p${{PART}}.out -p $P -c "$START" -ref ../build/complex.inpcrd \\
  -r prod_${{SEG}}_p${{PART}}.rst7 -x prod_${{SEG}}_p${{PART}}.nc -inf prod_${{SEG}}_p${{PART}}.mdinfo
cp -f prod_${{SEG}}_p${{PART}}.rst7 prod_${{SEG}}.rst7
NSDAY=$(grep -oE 'ns/day *= *[0-9.]+' prod_${{SEG}}_p${{PART}}.out | tail -1 | grep -oE '[0-9.]+$' || true)
[ -n "$NSDAY" ] && echo "$NSDAY" > prod_${{SEG}}_p${{PART}}.nsday
echo "[hopemd] segment $SEG of {nseg} done for replicate $SLURM_ARRAY_TASK_ID (${{NSDAY:-?}} ns/day)"
""")

    def _analysis_sbatch(self, spec: Spec) -> str:
        pro = spec.protocol
        return (self.sbatch_header(spec, "analysis", gpu=False, hours=2.0, cpus=8, mem_gb=32)
                + self.preamble(spec, "amber") + f"""
mkdir -p analysis
for i in $(seq 1 {pro.replicates}); do
  R=r$i
  ls $R/prod_s*_p*.nc >/dev/null 2>&1 || {{ echo "[hopemd] no trajectory for $R"; continue; }}
  TRAJ=$(for f in $(ls $R/prod_s*_p*.nc | sort -V); do echo "trajin $f"; done)
  awk -v rep=$i -v traj="$TRAJ" '{{ if ($0=="TRAJIN") print traj; else {{ gsub(/rREP/, "r" rep); print }} }}' \\
    analysis/analysis.cpptraj.template > analysis/r${{i}}.cpptraj
  echo "[hopemd] cpptraj replicate $i"
  cpptraj.OMP -i analysis/r${{i}}.cpptraj > analysis/r${{i}}.cpptraj.log 2>&1 || cpptraj -i analysis/r${{i}}.cpptraj > analysis/r${{i}}.cpptraj.log 2>&1
  # a run with a metal gets its viewer trajectory a second time, with the water at the sites kept;
  # the stripped one stays in place unless this writes a whole file
  if [ -f analysis/view.cpptraj.template ]; then
    awk -v rep=$i -v traj="$TRAJ" '{{ if ($0=="TRAJIN") print traj; else {{ gsub(/rREP/, "r" rep); print }} }}' \
      analysis/view.cpptraj.template > analysis/r${{i}}_view.cpptraj
    echo "[hopemd] cpptraj viewer trajectory, replicate $i"
    if cpptraj.OMP -i analysis/r${{i}}_view.cpptraj > analysis/r${{i}}_view.cpptraj.log 2>&1 \
       || cpptraj -i analysis/r${{i}}_view.cpptraj > analysis/r${{i}}_view.cpptraj.log 2>&1; then
      [ -s analysis/r${{i}}_view_water.pdb ] && mv analysis/r${{i}}_view_water.pdb analysis/r${{i}}_view.pdb
    else
      echo "[hopemd] the viewer's water pass failed for r$i; the stripped trajectory is kept"
    fi
  fi
done
python3 -m hopemd.analysis . || echo "[hopemd] summarise not available on this node; the server will do it"
echo "[hopemd] analysis done"
""")

    def _mmgbsa_sbatch(self, spec: Spec, strip_mask: str = SOLVENT_MASK) -> str:
        pro = spec.protocol
        return (self.sbatch_header(spec, "mmgbsa", gpu=False, hours=6.0, cpus=8, mem_gb=32, array=pro.replicates)
                + self.preamble(spec, "amber") + self.MMGBSA_GUARD + f"""
mkdir -p analysis
i=$SLURM_ARRAY_TASK_ID
# the MM-GBSA topologies are shared by every replicate: the first task writes them under a lock and the others
# wait for it. Without the lock, tasks that start together collide ("FileExists: analysis/com.prmtop exists; not
# overwriting") and all but the first end without their MM-GBSA
(
  flock 9
  if [ ! -s analysis/com.prmtop ] || [ ! -s analysis/rec.prmtop ] || [ ! -s analysis/lig.prmtop ]; then
    rm -f analysis/com.prmtop analysis/rec.prmtop analysis/lig.prmtop
    ante-MMPBSA.py -p build/complex.prmtop -c analysis/com.prmtop -r analysis/rec.prmtop -l analysis/lig.prmtop \\
      -s '{strip_mask}' -n "$(cat analysis/ligand_mask.txt)" --radii=mbondi3
  fi
) 9>analysis/.mmgbsa_topologies.lock
TRAJ=$(ls r$i/prod_s*_p*.nc | sort -V | tr '\\n' ' ')
echo "[hopemd] MMPBSA.py replicate $i over: $TRAJ"
cd analysis
# every replicate's MMPBSA.py works in analysis/ at the same time: its intermediate files carry the replicate
# in their prefix, else the tasks read and overwrite each other's (_MMPBSA_complex.mdcrd.0 and the rest).
# The job is one Slurm task on several CPUs, so mpirun is told to use them.
mpirun --oversubscribe --bind-to none -np $SLURM_CPUS_PER_TASK MMPBSA.py.MPI -O -i mmpbsa.in -prefix _MMPBSA_r${{i}}_ \\
  -o r${{i}}_mmgbsa.dat -eo r${{i}}_mmgbsa_frames.csv \\
  -sp ../build/complex.prmtop -cp com.prmtop -rp rec.prmtop -lp lig.prmtop -y $(echo $TRAJ | sed 's#r'$i'/#../r'$i'/#g') \\
  || MMPBSA.py -O -i mmpbsa.in -prefix _MMPBSA_r${{i}}_ -o r${{i}}_mmgbsa.dat -eo r${{i}}_mmgbsa_frames.csv \\
  -sp ../build/complex.prmtop -cp com.prmtop -rp rec.prmtop -lp lig.prmtop -y $(echo $TRAJ | sed 's#r'$i'/#../r'$i'/#g')
rm -f reference.frc      # a 40 MB forces dump MMPBSA.py leaves beside its output (Amber 24)
cd ..
# analysis/summary.json was written by the analysis job, before these energies existed: it is
# rewritten here so that what the run holds matches what the run has done. Every task of the
# array does it under a lock, so the last one in writes a summary with all of them in it; the
# report itself never writes here.
( flock 9 2>/dev/null || true
  python3 -m hopemd.analysis . >/dev/null 2>&1 \
    || echo "[hopemd] summarise not available on this node; the page will do it"
) 9>analysis/.summary.lock || true
echo "[hopemd] MM-GBSA replicate $i done"
""")

    def _submit_sh(self, spec: Spec, jobs: list[Job]) -> str:
        L = ["#!/bin/bash",
             f"# Submit the whole graph for {spec.name}. Run from the run folder. Prints job ids to jobs/ids.txt.",
             "set -euo pipefail", "cd \"$(dirname \"$0\")/..\"", "mkdir -p logs", "declare -A ID", ": > jobs/ids.txt"]
        for j in jobs:
            deps = ",".join(f"{mode}:${{ID[{key}]}}" for key, mode in j.depends)
            dep = f" --dependency={deps}" if deps else ""
            L.append(f"ID[{j.key}]=$(sbatch --parsable{dep} {j.script})")
            L.append(f"echo \"{j.key} ${{ID[{j.key}]}}\" | tee -a jobs/ids.txt")
        L.append("echo submitted")
        return "\n".join(L) + "\n"

    def _readme(self, spec: Spec, d: dict, nseg: int, mem: dict | None = None) -> str:
        if mem:
            env_line = (f"environment: {mem['lipid']} bilayer (OpenMM Modeller, lipid17), {mem['padding_A']:g} A of water above and below, "
                        f"center z = {mem['center_z_A']:g} A, ~{d.get('lipids', '?')} lipids; semi-isotropic NPT at zero surface tension\n")
            build_line = "build/        the membrane build's topology (complex.prmtop, complex_hmr.prmtop, complex.inpcrd, complex_solv.pdb), membrane.log, system.json"
        else:
            env_line = ""
            build_line = "build/        tleap inputs and the topology (complex.prmtop, complex_hmr.prmtop, complex.inpcrd)"
        return f"""HOPE-MD run: {spec.name}
engine: Amber ({spec.forcefield.protein}{", " + spec.forcefield.nucleic if has_nucleic(spec.system) and spec.forcefield.nucleic else ""} / {spec.forcefield.water}), site: {spec.site.name}, device: {spec.compute.device}{' (CPU check, one node)' if spec.on_cpu else ''}
protocol: {spec.protocol.length_ns:g} ns x {spec.protocol.replicates}, dt {spec.protocol.timestep_fs:g} fs{' (HMR)' if spec.hmr else ''}, {spec.protocol.temperature_K:g} K, {spec.protocol.pressure_bar:g} bar, frames every {spec.protocol.save_ps:g} ps
{env_line}derived at submit: ~{d['atoms']} atoms, {d['box']}, {d['steps']} steps, {d['frames']} frames, ~{d['ns_per_day']} ns/day ({d['ns_per_day_note']}), {nseg} segment(s) of {d['segment_ns']:g} ns, walltime {d['walltime']}

spec.json     the run, as submitted. Edit nothing else by hand.
{build_line}
md/           the mdin ladder: 01_min, 02_heat, 03_eq1-4, 04_prod
r1/ r2/ ...   one folder per replicate; prod_s<k>_p<n>.nc are the trajectory parts in order
analysis/     cpptraj outputs (r<i>_*.dat), r<i>_view.pdb for the viewer, MM-GBSA
jobs/         sbatch scripts, graph.json; ids.json holds the ids the page submitted (jobs/submit.sh, run by hand, writes ids.txt)
logs/         Slurm output
archive/      tidy_<stamp>.tar.gz once the tidy job has packed the small files (Undo tidy on the Runs screen unpacks them)
"""


def _resnum(key: str, sysm) -> int:
    """'A:41' or 'H:60A' -> tleap sequential residue number, via the map the importer stored (keyed by the number
    and the insertion code together, as the viewer names a residue clicked); a bare number passes through, and a key
    the map has not falls back on its number."""
    if ":" not in str(key):
        return int(key)
    m = getattr(sysm, "residue_index", None) or {}
    if key in m:
        return int(m[key])
    return int(re.match(r"\s*(-?\d+)", str(key).split(":", 1)[1]).group(1))
