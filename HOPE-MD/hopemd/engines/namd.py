"""NAMD 3: the same tleap (or membrane) build as Amber, then namd3 runs the prmtop natively.

One topology for the four Amber-topology engines is the point: NAMD reads
build/complex.prmtop and build/complex.inpcrd itself (amber yes), so the build job, the
cpptraj analysis and MM-GBSA are Amber's scripts and only the ladder and the production run
in NAMD. What this renderer adds to Amber's run folder::

    build/restraint_ref.pdb     the coordinates of build/complex.inpcrd, one record per topology atom: the restraint reference
    build/restraint_k.pdb       the same PDB with the B column 1.0 on solute heavy atoms, 0 elsewhere: the weights
    build/restraint_prod_k.pdb  B 1.0 on CA, C and N (P and C4' of a nucleotide) of the residues held in production (only with protocol.restrain)
    build/complex.xsc           the inpcrd box as NAMD's extended system file (cell vectors and origin)
    md/01_min.namd ..           the ladder: min, heat, eq1-4, prod; one namd3 run each
    r<i>/<stage>.namd, .log     the copy of each config the replicate ran, and NAMD's output
    r<i>/<stage>.coor/.vel/.xsc the end of each stage; prod_s<k>_p<n>.dcd are the trajectory parts;
                                prod_s<k>.coor/.vel/.xsc appear once a segment is complete (the queue's marker)

NAMD changes into the directory of the config file it is given, so the jobs copy md/<stage>.namd
into the replicate folder and run the copy; every path in a config is relative to r<i>/.

Minimization and heating run in GPU-offload mode: NAMD 3.0.1's GPU-resident mode refuses the
minimize keyword, and the heating ramp is a Tcl loop that moves the thermostat's target between
run commands. eq1-4 and production run GPU-resident (GPUresident on; the 3.0 manual's
CUDASOAintegrate, which 3.0.1 reports as deprecated), which is where the speed is. Restraints in
that mode are single-GPU, so the ladder always runs on one card.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..spec import Spec, CUTOFF_A, has_nucleic
from .amber import AmberEngine, _resnum
from .base import Render, Job, membrane_of

FOUR_SITE_WATER = ("OPC", "TIP4P-Ew")     # a massless fourth site: NAMD wants waterModel tip4 for it
LADDER_DT_FS = 2.0                        # the ladder runs at 2 fs whatever production runs at, like Amber's
HEAT_RAMP_STEPS = 20                      # the thermostat's target rises in this many steps over 90 % of heating
PISTON_PERIOD_FS, PISTON_DECAY_FS = 200, 100

# the awk that reads a part's log in the production job: steps a finished part ran (its final
# .coor/.vel/.xsc are the files to continue from), steps a cut-off part ran up to the last restart
# NAMD finished writing (its .restart.* files are the ones to continue from), and the ns/day of the
# last TIMING line (NAMD 3 prints "ns/days" on it; an older build gives wall seconds per step)
_DONE_FINAL_AWK = ("'/^Info: FIRST TIMESTEP/{first=$NF} /^(ENERGY|TIMING):/{last=$2} "
                   "END{print ((last > first) ? last - first : 0)}'")
_DONE_RESTART_AWK = ("'/^Info: FIRST TIMESTEP/{first=$NF} /WRITING VELOCITIES TO RESTART FILE AT STEP/{p=$NF} "
                     "/FINISHED WRITING RESTART VELOCITIES/{last=p} END{print ((last > first) ? last - first : 0)}'")
_NSDAY_AWK = ("'/^TIMING:/{ for (i = 1; i <= NF; i++) { if ($i == \"ns/days,\") v = $(i-1); "
              "if ($i == \"Wall:\") { split($(i+2), a, \"/\"); w = a[1] } } } "
              "END { if (v > 0) printf \"%.1f\\n\", v; else if (w > 0) printf \"%.1f\\n\", 86400 * dt / w }'")


def restraint_script(first: int, last: int, prod_residues: list[int]) -> str:
    """The Python the build job runs once tleap has written the topology: it writes the restraint
    reference, build/restraint_ref.pdb, from the prmtop and the inpcrd, one record per topology
    atom, flags the solute's heavy atoms in the B column of build/restraint_k.pdb and the CA/C/N (P/C4'
    of a nucleotide) of the residues held in production in build/restraint_prod_k.pdb, and turns the inpcrd's box
    line into build/complex.xsc. The reference is written here rather than by cpptraj, whose PDB
    leaves out a 4-site water's extra point (OPC, TIP4P-Ew), so NAMD's restraint files could not
    line up with the topology. Atom order and count come from the prmtop. Standard library only:
    it runs on whatever python3 the engine module's node has."""
    return f'''import math, re
PRMTOP, INPCRD, REF = "build/complex.prmtop", "build/complex.inpcrd", "build/restraint_ref.pdb"
FIRST, LAST = {first}, {last}       # the solute's residues, tleap's numbering
PROD = {sorted(prod_residues)!r}      # residues held in production by their CA, C and N, a nucleotide by its P and C4' ([] for none)

text = open(PRMTOP).read()

def section(name):
    """The value lines of one %FLAG section (the %FLAG line is padded to 80 columns by tleap, not by ParmEd)."""
    m = re.search(r"^%FLAG " + re.escape(name) + r"[ \\t]*$", text, re.M)
    if m is None:
        raise SystemExit("[hopemd] " + PRMTOP + " has no %FLAG " + name)
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
mass = [float(x) for line in section("MASS") for x in line.split()]
names = [line[i:i + 4].strip() for line in section("ATOM_NAME") for i in range(0, len(line.rstrip()), 4)]
labels = [line[i:i + 4].strip() for line in section("RESIDUE_LABEL") for i in range(0, len(line.rstrip()), 4)]
respt = [int(x) for line in section("RESIDUE_POINTER") for x in line.split()]
if len(mass) != natom or len(names) != natom or len(respt) != nres or len(labels) != nres:
    raise SystemExit("[hopemd] %s: %d atoms, but %d masses, %d names, %d residue pointers and %d labels for %d residues"
                     % (PRMTOP, natom, len(mass), len(names), len(respt), len(labels), nres))
if not 1 <= FIRST <= LAST <= nres:
    raise SystemExit("[hopemd] the solute is residues %d-%d but %s has %d residues" % (FIRST, LAST, PRMTOP, nres))

def atoms_of(res):
    """0-based atom indices of residue `res` (1-based)."""
    a = respt[res - 1] - 1
    b = respt[res] - 1 if res < nres else natom
    return range(a, b)

sol_a, sol_b = respt[FIRST - 1] - 1, (respt[LAST] - 1 if LAST < nres else natom)
# the weights: 1.0 on solute heavy atoms (mass over 1.5: hydrogens are 1.008 before repartitioning,
# a 4-site water's extra point 0), 0 on everything else, lipids and water included
k_solute = [1.0 if (sol_a <= i < sol_b and mass[i] > 1.5) else 0.0 for i in range(natom)]
k_prod = [0.0] * natom
for res in PROD:
    if not 1 <= res <= nres:
        raise SystemExit("[hopemd] protocol.restrain names residue %d but %s has %d residues" % (res, PRMTOP, nres))
    for i in atoms_of(res):
        if names[i] in ("CA", "C", "N", "P", "C4'"):
            k_prod[i] = 1.0

# the coordinates and the cell: the inpcrd tleap wrote, which holds every topology atom, a 4-site
# water's extra point included, and ends with a b c alpha beta gamma
lines = open(INPCRD).read().split("\\n")
n = int(lines[1].split()[0])
vals = []
for line in lines[2:]:
    line = line.rstrip()
    for i in range(0, len(line), 12):
        vals.append(float(line[i:i + 12]))
if n != natom or len(vals) < 3 * n + 6:
    raise SystemExit("[hopemd] %s: expected %d atoms and a box line, found %d atoms and %d values: the restraint "
                     "files would not line up" % (INPCRD, natom, n, len(vals)))
xyz = vals[:3 * n]

# the reference: one ATOM record per topology atom, in the topology's order. NAMD reads the
# coordinates by position, so the wrapped serial (past 99999) and residue number (past 9999) are
# only labels.
res_of = [0] * natom
for r in range(1, nres + 1):
    for i in atoms_of(r):
        res_of[i] = r
with open(REF, "w") as f:
    for i in range(natom):
        nm = names[i] if len(names[i]) >= 4 else " " + names[i].ljust(3)
        f.write("ATOM  %5d %4s %-4sA%4d    %8.3f%8.3f%8.3f%6.2f%6.2f\\n"
                % ((i + 1) % 100000, nm[:4], labels[res_of[i] - 1][:4], res_of[i] % 10000,
                   xyz[3 * i], xyz[3 * i + 1], xyz[3 * i + 2], 1.0, 0.0))
    f.write("END\\n")

def write_weights(path, k):
    n = 0
    with open(REF) as src, open(path, "w") as dst:
        for line in src:
            if line.startswith("ATOM") or line.startswith("HETATM"):
                if n >= natom:
                    raise SystemExit("[hopemd] %s has more atoms than the %d of %s" % (REF, natom, PRMTOP))
                line = line.rstrip("\\n").ljust(66)
                line = line[:60] + "%6.2f" % k[n] + line[66:] + "\\n"
                n += 1
            dst.write(line)
    if n != natom:
        raise SystemExit("[hopemd] %s has %d atoms, %s %d: the restraint files would not line up" % (REF, n, PRMTOP, natom))

write_weights("build/restraint_k.pdb", k_solute)
if PROD:
    write_weights("build/restraint_prod_k.pdb", k_prod)

# the cell: the inpcrd's last line is a b c alpha beta gamma; NAMD takes three basis vectors and an
# origin, the origin being the point wrapping is done around, so it is the center of what was built
a, b, c, alpha, beta, gamma = vals[-6:]
ca = math.cos(math.radians(alpha)); cb = math.cos(math.radians(beta)); cg = math.cos(math.radians(gamma))
sg = math.sin(math.radians(gamma))
va = (a, 0.0, 0.0)
vb = (b * cg, b * sg, 0.0)
cy = (ca - cb * cg) / sg
vc = (c * cb, c * cy, c * math.sqrt(max(0.0, 1.0 - cb * cb - cy * cy)))
origin = tuple(sum(xyz[d::3]) / n for d in range(3))
with open("build/complex.xsc", "w") as f:
    f.write("# NAMD extended system configuration output file\\n")
    f.write("#$LABELS step a_x a_y a_z b_x b_y b_z c_x c_y c_z o_x o_y o_z\\n")
    f.write("0 " + " ".join("%.6f" % v for v in va + vb + vc + origin) + "\\n")
print("[hopemd] NAMD restraint files: %d solute heavy atoms of %d flagged (residues %d-%d)%s; cell %.2f %.2f %.2f A, "
      "angles %.2f %.2f %.2f, origin %.2f %.2f %.2f"
      % (sum(1 for v in k_solute if v > 0), natom, FIRST, LAST,
         (", %d backbone atoms held in production" % sum(1 for v in k_prod if v > 0)) if PROD else "",
         a, b, c, alpha, beta, gamma, origin[0], origin[1], origin[2]))
'''


def progress_from_log(log: Path) -> tuple[int, float | None] | None:
    """(steps this part has run, ns/day or None) from a NAMD log: the step of the last ENERGY or
    TIMING line less the part's FIRST TIMESTEP, and the ns/day NAMD 3 prints on its TIMING lines
    (an older build gives wall seconds per step, turned into ns/day with the log's TIMESTEP; before
    the first TIMING line the Benchmark line's days/ns is used). None when there is no log."""
    try:
        text = log.read_text(errors="replace")
    except OSError:
        return None
    first, last, dt_fs, nsday, wall_per_step, bench = 0, None, None, None, None, None
    for line in text.splitlines():
        if line.startswith(("ENERGY:", "TIMING:")):
            parts = line.split()
            if len(parts) < 2 or not parts[1].isdigit():
                continue
            last = int(parts[1])
            if line.startswith("TIMING:"):
                m = re.search(r"([0-9.eE+-]+) ns/days", line)
                if m:
                    nsday = float(m.group(1))
                m = re.search(r"Wall: [0-9.eE+-]+, ([0-9.eE+-]+)/step", line)
                if m:
                    wall_per_step = float(m.group(1))
        elif line.startswith("Info: FIRST TIMESTEP"):
            first = int(line.split()[-1])
        elif re.match(r"^Info: TIMESTEP\s+[0-9.]+\s*$", line):
            dt_fs = float(line.split()[-1])
        elif line.startswith("Info: Benchmark time:"):
            m = re.search(r"([0-9.eE+-]+) days/ns", line)
            if m and float(m.group(1)) > 0:
                bench = 1.0 / float(m.group(1))
    if last is None:
        return 0, None
    if nsday is None and wall_per_step and dt_fs:
        nsday = 86400.0 * dt_fs * 1e-6 / wall_per_step
    if nsday is None and bench:
        nsday = bench
    return max(0, last - first), (round(nsday, 1) if nsday is not None else None)


class NamdEngine(AmberEngine):
    key = "namd"
    label = "NAMD 3"
    TRAJ = "dcd"           # cpptraj reads the parts with build/complex.prmtop, as it does Amber's .nc
    MMGBSA = True          # the prmtop is Amber's: MMPBSA.py rescoring runs unchanged

    @classmethod
    def part_progress(cls, part: Path) -> tuple[int, float | None] | None:
        return progress_from_log(part.with_suffix(".log"))

    # ------------------------------------------------------------------ render
    def render(self, spec: Spec, derived: dict | None = None) -> Render:
        r = super().render(spec, derived)
        r.engine = self.key
        d = r.derived
        pro, sysm, ff = spec.protocol, spec.system, spec.forcefield
        mem = membrane_of(spec)
        apo = sysm.ligand_class == "none"
        rec = sysm.receptor_residues or [1, 1]
        lig = sysm.ligand_residues or [rec[1] + 1, rec[1] + 1]
        first, last = rec[0], (rec[1] if apo else lig[1])        # the solute: receptor, then the partner
        prod_res = sorted({_resnum(k, sysm) for k in pro.restrain})
        gpu = not spec.on_cpu
        # keep: build (NAMD's files appended), param, analysis, mmgbsa; replace: md/, equil, prod, submit, README
        r.files = [f for f in r.files if not f.path.startswith("md/") and not f.path.startswith("jobs/2")
                   and not f.path.startswith("jobs/30_") and f.path not in ("jobs/submit.sh", "README.txt")]
        for f in r.files:
            if f.path == "build/build.sh":
                f.text += self._build_step(first, last, prod_res)
            elif f.path in ("jobs/40_analysis.sbatch", "jobs/50_mmgbsa.sbatch"):
                f.text = f.text.replace("prod_s*_p*.nc", "prod_s*_p*.dcd")

        # --- the ladder ---
        seg_steps = int(round(d["segment_ns"] * 1e6 / pro.timestep_fs))
        save_steps = int(round(pro.save_ps * 1000 / pro.timestep_fs))
        # a restart every ~10 minutes, on a frame boundary: a part resumed from it repeats whole frames
        restart_steps = max(save_steps, self._ntwr(d["ns_per_day"], pro.timestep_fs, seg_steps) // save_steps * save_steps)
        r.add("md/01_min.namd", self._namd_min(spec))
        r.add("md/02_heat.namd", self._namd_heat(spec))
        prev = "heat"
        for i, w in enumerate((pro.restraint_kcal, 1.0, 0.1, 0.0), start=1):
            r.add(f"md/03_eq{i}.namd", self._namd_eq(spec, mem, i, w, prev, gpu))
            prev = f"eq{i}"
        r.add("md/04_prod.namd", self._namd_prod(spec, mem, seg_steps, save_steps, restart_steps, bool(prod_res), gpu))

        # --- jobs: Amber's graph with NAMD's equil and production scripts ---
        nseg = int(d["segments"])
        jobs = [j for j in r.jobs if j.key in ("param", "build", "analysis", "mmgbsa", "tidy")]
        r.add("jobs/20_equil.sbatch", self._namd_equil_sbatch(spec, mem), executable=True)
        at = [j.key for j in jobs].index("build") + 1
        jobs.insert(at, Job("equil", "jobs/20_equil.sbatch", depends=[("build", "afterok")],
                            array=pro.replicates, gpu=True, description="NAMD: minimize, heat, NPT ladder"))
        at += 1
        for k in range(1, nseg + 1):
            r.add(f"jobs/30_prod_s{k}.sbatch",
                  self._namd_prod_sbatch(spec, k, nseg, seg_steps, d["walltime"], bool(prod_res)), executable=True)
            dep = ("equil", "aftercorr") if k == 1 else (f"prod_s{k-1}", "aftercorr")
            jobs.insert(at, Job(f"prod_s{k}", f"jobs/30_prod_s{k}.sbatch", depends=[dep],
                                array=pro.replicates, gpu=True,
                                description=f"NAMD production segment {k} of {nseg}, {d['segment_ns']:g} ns"))
            at += 1
        for j in jobs:
            if j.key in ("analysis", "mmgbsa"):
                j.depends = [(f"prod_s{nseg}", "afterok")]
        r.jobs = jobs
        r.add("jobs/submit.sh", self._submit_sh(spec, jobs), executable=True)
        r.add("README.txt", self._namd_readme(spec, d, nseg, mem))

        # --- what a person must know before Submit ---
        topo = "the prmtop the membrane build writes" if mem else f"the tleap topology ({ff.protein} / {ff.water})"
        r.notes.append(f"NAMD 3 runs {topo} natively (amber yes), so the build, the analysis and the MM-GBSA jobs are "
                       "Amber's; only the ladder and production run in NAMD, and cpptraj reads the DCD parts with "
                       "build/complex.prmtop.")
        r.notes.append("Restraints go through the B column of build/restraint_k.pdb (1.0 on solute heavy atoms, written "
                       f"by the build from the prmtop) scaled by constraintScaling per stage ({pro.restraint_kcal:g}, 1, 0.1, "
                       "then off)" + (f"; HMR at {pro.timestep_fs:g} fs is the masses of build/complex_hmr.prmtop with rigidBonds all."
                                      if spec.hmr else "; rigidBonds all at 2 fs."))
        if gpu:
            r.notes.append("Minimization and heating run in GPU-offload mode (NAMD 3.0.1's GPU-resident mode refuses "
                           "minimize, and the heating ramp is scripted); eq1-4 and production run GPU-resident "
                           "(GPUresident on) on one card, since restraints are single-GPU in that mode.")
        else:
            r.notes.append("CPU check: Grace's NAMD module is the CUDA build, which may refuse a node without a GPU; "
                           "if it does, the check needs a CPU build (NAMD/3.0-mpi) recorded in site.py.")
        if ff.water in FOUR_SITE_WATER:
            r.notes.append(f"{ff.water} is a 4-site water: waterModel tip4 has NAMD treat the extra site as a lone pair, "
                           "the one lone-pair case its GPU-resident mode supports. Check the first log says "
                           "'Using TIP4P water model' before trusting the run.")
        if spec.box.shape == "compact" and not mem:
            r.notes.append("The compact box is a truncated octahedron, a triclinic cell in NAMD (build/complex.xsc): check "
                           "the first log for the cell it reports and that PME accepted it; rectangular and cubic are the "
                           "safe shapes if it does not.")
        if mem:
            r.notes.append("Bilayer in NAMD: useFlexibleCell with useConstantRatio scales x and y together and z on its "
                           "own at zero surface tension, the semi-isotropic coupling of the Amber stages.")
        if spec.compute.gpus_per_replicate > 1 and gpu:
            r.notes.append(f"gpus_per_replicate is {spec.compute.gpus_per_replicate}: production uses them all (+devices), "
                           "the ladder one" + (", and so does production while protocol.restrain holds residues." if prod_res else "."))
        r.notes.append("NAMD changes into the directory of its config file, so the jobs copy md/<stage>.namd into r<i>/ "
                       "and run the copy; the copies are the record of what ran.")
        return r

    # ------------------------------------------------------------------ build
    @staticmethod
    def _build_step(first: int, last: int, prod_res: list[int]) -> str:
        """Appended to Amber's build/build.sh: the restraint reference from cpptraj (the amber
        module is loaded), then the weights and the cell by the script above."""
        return f"""echo "[hopemd] restraint reference and cell for NAMD"
# NAMD's positional restraints take a reference PDB and a PDB whose B column holds each atom's weight;
# the cell comes from the inpcrd's box line as an extended system file. All of them are written from
# the topology that was just built, so they line up with it atom for atom, extra points included.
python3 - <<'EOF'
{restraint_script(first, last, prod_res)}EOF
test -s build/restraint_k.pdb && test -s build/complex.xsc || {{ echo '[hopemd] the NAMD restraint files were not written'; exit 3; }}
"""

    # ------------------------------------------------------------------ configs
    @staticmethod
    def _seed(spec: Spec) -> int:
        """The seed line the md/ files carry (replicate 1's); the jobs set one per replicate and segment."""
        return spec.protocol.seed + 1 if spec.protocol.seed >= 0 else 1

    def _ff_block(self, spec: Spec) -> str:
        parm = "../build/complex_hmr.prmtop" if spec.hmr else "../build/complex.prmtop"
        water = "\nwaterModel         tip4" if spec.forcefield.water in FOUR_SITE_WATER else ""
        return f"""# the Amber topology, read natively; the masses are the prmtop's (repartitioned at 4 fs)
amber              yes
parmfile           {parm}
readexclusions     yes
exclude            scaled1-4
oneFourScaling     0.833333
scnb               2.0
# nonbonded: the {CUTOFF_A:g} A cutoff of the Amber stages, no switching, PME
cutoff             {CUTOFF_A:g}
switching          off
# the long-range correction to the LJ energy and pressure past the cutoff, which the other engines apply:
# pmemd by default (vdwmeth = 1), OpenMM's NonbondedForce under PME, GROMACS with DispCorr = EnerPres.
# Amber's lipids are simulated with it (the Lipid21 paper: a 10 A cutoff and the analytical correction to
# energy and pressure); without it a POPC bilayer settled 4 % larger in area per lipid than on pmemd and OpenMM
LJcorrection       on
pairlistdist       {CUTOFF_A + 2:g}
PME                yes
PMEGridSpacing     1.0
rigidBonds         all{water}
wrapAll            on
"""

    @staticmethod
    def _integration(dt_fs: float) -> str:
        # at 4 fs (HMR) every step does the full electrostatics; at 2 fs every second step, as Amber does
        return f"""timestep           {dt_fs:g}
nonbondedFreq      1
fullElectFrequency {1 if dt_fs > 2.5 else 2}
stepspercycle      20
"""

    @staticmethod
    def _thermostat(spec: Spec, target: bool = True) -> str:
        """target=False leaves langevinTemp out: the heating ramp defines it in Tcl, and NAMD refuses
        a keyword defined twice before the first run ("Multiple definitions of 'langevinTemp'")."""
        return f"""langevin           on
langevinDamping    1
""" + (f"langevinTemp       {spec.protocol.temperature_K:g}\n" if target else "")

    @staticmethod
    def _piston(spec: Spec, mem: dict | None) -> str:
        """Constant pressure by Langevin piston. A bilayer keeps x and y in ratio and lets z move on
        its own at zero surface tension (the Amber stages' ntp=3, csurften=3, gamma_ten=0)."""
        pro = spec.protocol
        cell = ("useFlexibleCell    yes\nuseConstantRatio   yes\nuseConstantArea    no" if mem
                else "useFlexibleCell    no\nuseConstantArea    no")
        return f"""langevinPiston     on
langevinPistonTarget {pro.pressure_bar:g}
langevinPistonPeriod {PISTON_PERIOD_FS}
langevinPistonDecay  {PISTON_DECAY_FS}
langevinPistonTemp   {pro.temperature_K:g}
useGroupPressure   yes
{cell}
"""

    @staticmethod
    def _restraint(weight: float, kfile: str = "../build/restraint_k.pdb") -> str:
        if weight <= 0:
            return "constraints        off\n"
        return f"""constraints        on
consexp            2
consref            ../build/restraint_ref.pdb
conskfile          {kfile}
conskcol           B
constraintScaling  {weight:g}
"""

    @staticmethod
    def _resident(gpu: bool) -> str:
        return "GPUresident        on\n" if gpu else ""

    def _namd_min(self, spec: Spec) -> str:
        pro = spec.protocol
        return f"""# HOPE-MD NAMD 3 minimization for {spec.name}: {pro.minimise_steps} steps, solute heavy atoms restrained
# The job copies this file into the replicate folder and runs the copy there (NAMD changes into the
# directory of its config file); every path is relative to r<i>/.
{self._ff_block(spec)}ambercoor          ../build/complex.inpcrd
extendedSystem     ../build/complex.xsc
temperature        0
outputName         min
seed               {self._seed(spec)}
{self._integration(LADDER_DT_FS)}{self._restraint(pro.restraint_kcal)}# GPU-offload mode: NAMD 3.0.1's GPU-resident mode refuses the minimize keyword
outputEnergies     100
minimize           {pro.minimise_steps}
"""

    def _namd_heat(self, spec: Spec) -> str:
        pro = spec.protocol
        steps = int(round(pro.heat_ps / (LADDER_DT_FS / 1000)))
        chunk = int(steps * 0.9) // HEAT_RAMP_STEPS
        hold = steps - chunk * HEAT_RAMP_STEPS
        ramp = ("\n".join([
            f"# the thermostat's target rises in {HEAT_RAMP_STEPS} steps over 90 % of the stage, then holds at {pro.temperature_K:g} K",
            f"set T {pro.temperature_K:g}",
            "for {set i 1} {$i <= " + str(HEAT_RAMP_STEPS) + "} {incr i} {",
            "    langevinTemp [expr {$T * $i / " + f"{HEAT_RAMP_STEPS}.0" + "}]",
            f"    run {chunk}",
            "}",
            "langevinTemp $T",
            f"run {hold}",
        ]) + "\n")
        return f"""# HOPE-MD NAMD 3 heating for {spec.name}: 0 -> {pro.temperature_K:g} K under NVT over {pro.heat_ps:g} ps, solute heavy atoms restrained
{self._ff_block(spec)}ambercoor          ../build/complex.inpcrd   # NAMD wants a coordinate file even when bincoordinates supplies the positions
bincoordinates     min.coor
extendedSystem     min.xsc
temperature        0
outputName         heat
seed               {self._seed(spec)}
{self._integration(LADDER_DT_FS)}{self._thermostat(spec, target=False)}{self._restraint(pro.restraint_kcal)}# GPU-offload mode here: the ramp below defines the thermostat's target and raises it between run commands
outputEnergies     1000
outputTiming       1000
restartfreq        10000
{ramp}"""

    def _namd_eq(self, spec: Spec, mem: dict | None, stage: int, weight: float, prev: str, gpu: bool) -> str:
        pro = spec.protocol
        steps = int(round(pro.equil_ps / 4 / (LADDER_DT_FS / 1000)))
        return f"""# HOPE-MD NAMD 3 equilibration {stage}/4 for {spec.name}: NPT, {pro.equil_ps / 4:g} ps, restraint {weight:g} kcal/mol/A^2
{self._ff_block(spec)}ambercoor          ../build/complex.inpcrd   # NAMD wants a coordinate file even when bincoordinates supplies the positions
bincoordinates     {prev}.coor
binvelocities      {prev}.vel
extendedSystem     {prev}.xsc
outputName         eq{stage}
seed               {self._seed(spec)}
{self._integration(LADDER_DT_FS)}{self._thermostat(spec)}{self._piston(spec, mem)}{self._restraint(weight)}{self._resident(gpu)}outputEnergies     1000
outputTiming       1000
restartfreq        10000
run                {steps}
"""

    def _namd_prod(self, spec: Spec, mem: dict | None, seg_steps: int, save_steps: int, restart_steps: int,
              restrained: bool, gpu: bool) -> str:
        pro = spec.protocol
        restr = (self._restraint(pro.restraint_kcal, "../build/restraint_prod_k.pdb") if restrained
                 else self._restraint(0.0))
        return f"""# HOPE-MD NAMD 3 production for {spec.name}: one segment of {seg_steps} steps at {pro.timestep_fs:g} fs; the job sets
# the input files, outputName, firsttimestep and the step count of each part (resume) with sed
{self._ff_block(spec)}ambercoor          ../build/complex.inpcrd   # NAMD wants a coordinate file even when bincoordinates supplies the positions
bincoordinates     eq4.coor
binvelocities      eq4.vel
extendedSystem     eq4.xsc
outputName         prod_s1_p1
firsttimestep      0
seed               {self._seed(spec)}
{self._integration(pro.timestep_fs)}{self._thermostat(spec)}{self._piston(spec, mem)}{restr}{self._resident(gpu)}dcdfreq            {save_steps}
xstFreq            {save_steps}
restartfreq        {restart_steps}
outputEnergies     {save_steps}
outputTiming       {save_steps}
run                {seg_steps}
"""

    # ------------------------------------------------------------------ sbatch
    @staticmethod
    def _namd_line(spec: Spec, single_gpu: bool) -> str:
        if spec.on_cpu:
            return ("# CPU check, one whole node; Grace's module is the CUDA build, which may refuse a node without a GPU\n"
                    'NAMD="namd3 +p${SLURM_NTASKS:-48} +setcpuaffinity"')
        if single_gpu:
            return ("# one device: restraints (constraints on) are single-GPU in GPU-resident mode\n"
                    'NAMD="namd3 +p${SLURM_CPUS_PER_TASK:-4} +setcpuaffinity +devices 0"')
        return ("# the CUDA runtime numbers the GPUs Slurm gave this task from 0, whatever CUDA_VISIBLE_DEVICES says\n"
                "NDEV=$(echo \"${CUDA_VISIBLE_DEVICES:-0}\" | tr ',' '\\n' | grep -c .)\n"
                'NAMD="namd3 +p${SLURM_CPUS_PER_TASK:-4} +setcpuaffinity +devices $(seq -s, 0 $((NDEV-1)))"')

    @staticmethod
    def _seed_line(spec: Spec, segment: int = 0) -> str:
        seed = spec.protocol.seed
        if seed >= 0:
            return (f"SEED=$(( {seed} + 1000 * SLURM_ARRAY_TASK_ID" + (f" + 100 * {segment}" if segment else "")
                    + " ))   # protocol.seed, offset per replicate" + (" and segment" if segment else ""))
        return "SEED=$(( RANDOM * 32768 + RANDOM + 1 ))   # protocol.seed is -1: each replicate (and each production segment) draws its own, printed in the log as RANDOM NUMBER SEED"

    def _namd_equil_sbatch(self, spec: Spec, mem: dict | None) -> str:
        pro = spec.protocol
        est_h = 8.0 if spec.on_cpu else (5.0 if mem else 3.0)   # min in offload mode, 200 ps heat, 1 ns NPT
        return (self.sbatch_header(spec, "equil", gpu=True, hours=est_h, array=pro.replicates,
                                   throttle=spec.compute.max_concurrent)
                + self.preamble(spec, "namd") + f"""
{self._namd_line(spec, single_gpu=True)}
R=r${{SLURM_ARRAY_TASK_ID}}
mkdir -p "$R"; cd "$R"
{self._seed_line(spec)}
stage() {{ # name, config: the copy runs here (NAMD changes into the directory of its config file), seeded per replicate
  sed -e "s/^seed .*/seed               $SEED/" "../md/$2" > "$1.namd"
  echo "[hopemd] $1"
  $NAMD "$1.namd" > "$1.log" 2>&1 || {{ tail -n 30 "$1.log"; echo "[hopemd] $1 failed; see $R/$1.log"; exit 1; }}
  test -s "$1.coor" || {{ echo "[hopemd] $1 wrote no $1.coor; see $R/$1.log"; exit 1; }}
}}
stage min  01_min.namd
stage heat 02_heat.namd
stage eq1  03_eq1.namd
stage eq2  03_eq2.namd
stage eq3  03_eq3.namd
stage eq4  03_eq4.namd
echo "[hopemd] equilibrated replicate $SLURM_ARRAY_TASK_ID"
""")

    def _namd_prod_sbatch(self, spec: Spec, k: int, nseg: int, seg_steps: int, walltime: str, restrained: bool) -> str:
        pro = spec.protocol
        prev = "eq4" if k == 1 else f"prod_s{k-1}"
        dt_ns = pro.timestep_fs * 1e-6
        return (self.sbatch_header(spec, f"prod_s{k}", gpu=True, walltime=walltime, array=pro.replicates,
                                   throttle=spec.compute.max_concurrent)
                + self.preamble(spec, "namd") + f"""
{self._namd_line(spec, single_gpu=restrained)}
R=r${{SLURM_ARRAY_TASK_ID}}
cd "$R"
SEG=s{k}; TOTAL={seg_steps}; PREV={prev}; FIRST0={(k - 1) * seg_steps}
{self._seed_line(spec, k)}
# Resume: the earlier parts of this segment say in their logs how far they got. A part that finished
# left its final prod_${{SEG}}_p<n>.coor/.vel/.xsc; a part that was cut off left prod_${{SEG}}_p<n>.restart.*
# at the last restart NAMD finished writing, and that is the step to continue from. The frames its DCD
# holds past that step are simulated again by the next part, so they are cut from it first: cpptraj
# reads prod_${{SEG}}_p*.dcd in order and meets every frame once.
DONE=0; PART=1; START=$PREV; LASTP=""
for f in $(ls prod_${{SEG}}_p*.log 2>/dev/null | sort -V); do
  stem=${{f%.log}}
  if [ -s "$stem.coor" ] && [ -s "$stem.vel" ] && [ -s "$stem.xsc" ]; then
    n=$(awk {_DONE_FINAL_AWK} "$f"); from=$stem
  elif [ -s "$stem.restart.coor" ] && [ -s "$stem.restart.vel" ] && [ -s "$stem.restart.xsc" ]; then
    n=$(awk {_DONE_RESTART_AWK} "$f"); from=$stem.restart
  else
    n=0
  fi
  if [ "$n" -gt 0 ]; then DONE=$((DONE+n)); START=$from; PART=$((PART+1)); LASTP=$stem; fi
done
LEFT=$((TOTAL-DONE))
if [ "$LEFT" -le 0 ]; then
  echo "[hopemd] segment $SEG already complete"
  for ext in coor vel xsc; do cp -f "$START.$ext" "prod_${{SEG}}.$ext"; done
  exit 0
fi
FIRST=$((FIRST0+DONE))
if [ -n "$LASTP" ] && [ -s "$LASTP.dcd" ]; then
  "${{HOPEMD_PYTHON:-python3}}" -m hopemd.analysis.parts cut "$LASTP.dcd" --step $FIRST \\
    || {{ echo "[hopemd] could not cut $LASTP.dcd at its restart (step $FIRST); stopped here, as its frames past it would repeat"; exit 3; }}
fi
OUT=prod_${{SEG}}_p${{PART}}
sed -e "s|^bincoordinates .*|bincoordinates     $START.coor|" -e "s|^binvelocities .*|binvelocities      $START.vel|" \\
    -e "s|^extendedSystem .*|extendedSystem     $START.xsc|" -e "s|^outputName .*|outputName         $OUT|" \\
    -e "s|^firsttimestep .*|firsttimestep      $FIRST|" -e "s|^run .*|run                $LEFT|" \\
    -e "s|^seed .*|seed               $SEED|" ../md/04_prod.namd > $OUT.namd
echo "[hopemd] production $SEG part $PART: $LEFT of $TOTAL steps from $START (step $FIRST on)"
$NAMD $OUT.namd > $OUT.log 2>&1 || {{ tail -n 30 $OUT.log; echo "[hopemd] $OUT failed; see $R/$OUT.log"; exit 1; }}
test -s $OUT.coor && test -s $OUT.vel && test -s $OUT.xsc || {{ echo "[hopemd] $OUT wrote no final $OUT.coor/.vel/.xsc; see $R/$OUT.log"; exit 1; }}
for ext in coor vel xsc; do cp -f "$OUT.$ext" "prod_${{SEG}}.$ext"; done
# ns/day from the last TIMING line: NAMD 3 prints it as ns/days; an older build gives wall seconds per step
NSDAY=$(awk -v dt={dt_ns:g} {_NSDAY_AWK} $OUT.log)
[ -n "$NSDAY" ] && echo "$NSDAY" > $OUT.nsday
echo "[hopemd] segment $SEG of {nseg} done for replicate $SLURM_ARRAY_TASK_ID (${{NSDAY:-?}} ns/day)"
""")

    def _namd_readme(self, spec: Spec, d: dict, nseg: int, mem: dict | None = None) -> str:
        pro, ff = spec.protocol, spec.forcefield
        if mem:
            env_line = (f"environment: {mem['lipid']} bilayer (OpenMM Modeller, lipid17), {mem['padding_A']:g} A of water above and below, "
                        f"center z = {mem['center_z_A']:g} A, ~{d.get('lipids', '?')} lipids; x and y in ratio, z free, zero surface tension\n")
            build_line = "build/        the membrane build's topology (complex.prmtop, complex_hmr.prmtop, complex.inpcrd), membrane.log, system.json"
        else:
            env_line = ""
            build_line = "build/        tleap inputs and the topology (complex.prmtop, complex_hmr.prmtop, complex.inpcrd)"
        return f"""HOPE-MD run: {spec.name}
engine: NAMD 3 on the Amber topology ({ff.protein}{", " + ff.nucleic if has_nucleic(spec.system) and ff.nucleic else ""} / {ff.water}), site: {spec.site.name}, device: {spec.compute.device}{' (CPU check, one node)' if spec.on_cpu else ''}
protocol: {pro.length_ns:g} ns x {pro.replicates}, dt {pro.timestep_fs:g} fs{' (HMR)' if spec.hmr else ''}, {pro.temperature_K:g} K, {pro.pressure_bar:g} bar, frames every {pro.save_ps:g} ps
{env_line}derived at submit: ~{d['atoms']} atoms, {d['box']}, {d['steps']} steps, {d['frames']} frames, ~{d['ns_per_day']} ns/day ({d['ns_per_day_note']}), {nseg} segment(s) of {d['segment_ns']:g} ns, walltime {d['walltime']}

spec.json     the run, as submitted. Edit nothing else by hand.
{build_line}
              plus NAMD's restraint_ref.pdb / restraint_k.pdb (B column = weight) and complex.xsc (the cell), written by the build
md/           the NAMD ladder: 01_min, 02_heat, 03_eq1-4, 04_prod (.namd); the jobs copy each into r<i>/ and run the copy
r1/ r2/ ...   one folder per replicate: <stage>.namd/.log/.coor/.vel/.xsc; prod_s<k>_p<n>.dcd are the trajectory parts in order,
              prod_s<k>.coor/.vel/.xsc mark a finished segment
analysis/     cpptraj outputs (r<i>_*.dat), r<i>_view.pdb for the viewer, MM-GBSA
jobs/         sbatch scripts, graph.json; ids.json holds the ids the page submitted (jobs/submit.sh, run by hand, writes ids.txt)
logs/         Slurm output
archive/      tidy_<stamp>.tar.gz once the tidy job has packed the small files (Undo tidy on the Runs screen unpacks them)
"""
