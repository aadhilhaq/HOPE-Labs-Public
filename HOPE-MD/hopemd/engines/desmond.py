"""Desmond: the System Builder builds, multisim relaxes and produces, VMD converts, cpptraj analyzes, Prime MM-GBSA scores.

Desmond has its own force field (OPLS) and its own files (.cms for the system, a .dtr folder for
the trajectory), so nothing of the Amber build is reused: no pdb4amber, pdb2pqr, tleap, parmed or
antechamber. What is reused is the shape of the run: the same folders, the same job keys (build,
equil, prod_s<k>, analysis, mmgbsa, tidy), the same cpptraj analysis, so the page, the report and
the videos read a Desmond run as they read an Amber one. MM-GBSA is Schrödinger's Prime
(thermal_mmgbsa.py, under the Schrodinger module), its per-frame table converted into the files
MMPBSA.py would have written. A bilayer is the System Builder's membrane_box (POPC, POPE, DPPC or
DMPC), relaxed with Schrödinger's membrane protocol and produced under NPgT; the analysis reports
the area per lipid as on the other engines.

Run folder layout this renderer produces::

    spec.json                the one hand-editable thing (written by the server)
    input/receptor.pdb       as imported; the partner in input/ligand.pdb, a small molecule as ligand.sdf/.mol2
    build/                   build.msj, build.sh, topology.sh, complete_termini.py, complex_in.pdb/.mae, complex.cms,
                             system.pdb, system.json, ligand_asl.txt (the partner as Prime names it)
    md/                      relax.msj (the ladder; for a bilayer, a pointer to Schrödinger's membrane protocol),
                             prod.msj (one production segment), dtr_to_dcd.tcl (a segment's frames as DCD, for VMD)
    r1/ r2/ ...              one folder per replicate: eq4.cms, prod_s<k>_p1-out.cms, prod_s<k>_p1_trj/, .dcd, .nsday, .done
    analysis/                cpptraj outputs, the strided viewer trajectory, r<i>_mmgbsa.dat from Prime
    jobs/                    the sbatch scripts, submit.sh
    logs/                    Slurm output
    archive/                 tidy_<stamp>.tar.gz once the tidy job has packed the small files

Everything here follows the files Desmond 2024-4 ships on Grace ($SCHRODINGER =
/sw/restricted/Desmond/2024-4): the relaxation protocol
mmshare-v6.8/data/desmond/desmond_npt_relax.msj; the stage keywords under
internal/lib/python3.11/site-packages/schrodinger/application/desmond/ (stage/prepare/structure.py
for build_geometry, stage/prepare/forcefield.py for assign_forcefield, msj_defaults.py for the
simulate stage, multisimstartup.py for multisim's options, constants.py for the water residue
names, gui.py for the production stage the Maestro panel writes); the engine's log lines in
internal/lib/libstd_plugins.so.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..spec import Spec, CUTOFF_A, FIXED_TIMESTEP, parse_walltime, has_nucleic, mmgbsa_runs
from .amber import AmberEngine, CHECKPOINT_MINUTES
from .base import Render, Job, membrane_of, carried_ions, partner_ions, crystal_waters, kept_last, hopemd_home

#: the water model as build_geometry spells it (stage/prepare/structure.py: the solvent enum) and the
#: residue name the built system carries (constants.py: WATER_MODELS -> SPC, T3P, T4P, T4PE)
WATER = {"SPC": ("SPC", "SPC"), "TIP3P": ("TIP3P", "T3P"), "TIP4P": ("TIP4P", "T4P"), "TIP4P-Ew": ("TIP4PEW", "T4PE")}
#: the four-site waters OPLS_2005 cannot run: constants.py lists them as S_OPLS_WATER_MODELS and
#: build_geometry refuses the pair with "OPLS_2005 cannot be run with <water>" (structure.py, crunch)
S_OPLS_ONLY_WATER = ("TIP4P", "TIP4P-Ew")
#: the ions as build_geometry names them (its positive_ion / negative_ion enums); their templates
#: (mmshare-v6.8/data/system_builder/Na.mae, K.mae, Mg2.mae, Cl.mae) carry the residue names NA, K, MG, CL
IONS = {"NaCl": ("Na", "Cl"), "KCl": ("K", "Cl"), "MgCl2": ("Mg2", "Cl")}
FORCEFIELD = {"OPLS_2005": "OPLS_2005", "OPLS4": "S-OPLS"}
#: what OPLS4 needs: the page says it beside the choice and before Submit, and a run or a batch in its notes
OPLS4_LICENCE = ("OPLS4 needs the OPLS license: Desmond 2024-4 calls it S-OPLS (assign_forcefield { forcefield = S-OPLS }), "
                 "and the academic Desmond license on Grace lacks the feature it takes (FFLD_OPLS_MAIN), so the build stops "
                 "at that stage with a license error. OPLS_2005 is what that license covers.")
#: what cpptraj strips: Desmond's water and ion residue names, plus a crystal water that stayed as HOH
STRIP_MASK = ":SPC,T3P,T4P,T4PE,HOH,WAT,NA,CL,K,MG"
DT_PS = FIXED_TIMESTEP["desmond"] / 1000.0    # 2 fs: the RESPA base step, timestep = [0.002 0.002 0.006]
MULTISIM_MARGIN_S = 25 * 60   # multisim checkpoints itself this long before Slurm's walltime (-max-walltime)
SEED_TOKEN = "HOPEMD_SEED"    # in md/*.msj; the job script fills it per replicate (and per segment)
#: Prime MM-GBSA: every subjob checks out 8 PSP_PLOP tokens, from a pool of 25 on Grace, so the job runs one,
#: which scores a frame in 2.6 to 3.8 min; the job waits up to PRIME_WAIT_H for its tokens before it starts
PRIME_MIN_PER_FRAME = 4.0
PRIME_WAIT_H = 2.0

# md/dtr_to_dcd.tcl: a segment's trajectory as the DCD cpptraj reads, by each frame's time (VMD gives it as
# physical_time). Desmond 2024-4 writes a frame at the time a stage starts, one every interval after it (the
# save interval in whole outer steps: 19.998 ps for 20) and, with write_last_step, one more at the last step;
# a 10 ns segment held 502 frames for its 500.
DTR_TO_DCD_TCL = r'''# HOPE-MD: one production segment's Desmond trajectory as the DCD cpptraj reads, run from the replicate folder:
#     vmd -dispdev text -eofexit -e ../md/dtr_to_dcd.tcl -args <job>
# Desmond writes a frame at the time a stage starts (the structure the segment before ended on, or the
# equilibrated one), one every interval after it, and with write_last_step one more at the last step, between
# two of them; a stage restarted from a checkpoint can hold the stretch past the checkpoint twice. Frame k of the
# segment is k intervals after its start: the DCD keeps each such frame once, the later copy of a time written
# twice, and nothing else.
set job [lindex $argv 0]
mol new $job-out.cms type mae waitfor all
animate delete all
mol addfile ${job}_trj/clickme.dtr type dtr waitfor all
set n [molinfo top get numframes]
if {$n < 2} { puts "hopemd: $n frame(s) in ${job}_trj"; quit }
set t {}
for {set i 0} {$i < $n} {incr i} { animate goto $i; lappend t [molinfo top get physical_time] }
set every [expr {[lindex $t 1] - [lindex $t 0]}]
set kept [dict create]
for {set i [expr {$n - 1}]} {$i >= 0} {incr i -1} {
    set x [expr {([lindex $t $i] - [lindex $t 0]) / $every}]
    set k [expr {round($x)}]
    if {$k < 1 || abs($x - $k) > 0.01 || [dict exists $kept $k]} {
        animate delete beg $i end $i
    } else {
        dict set kept $k 1
    }
}
puts "hopemd: [molinfo top get numframes] of $n frames kept, one every [format %.3f $every] ps from [format %.3f [lindex $t 0]] ps"
animate write dcd $job.dcd waitfor all
quit
'''


class DesmondEngine(AmberEngine):
    STRIP = STRIP_MASK
    key = "desmond"
    label = "Desmond"
    #: the parts are DCD files written by VMD from Desmond's .dtr; the topology is the PDB VMD writes
    #: from the built system, so both come from the same reader and the atoms match
    TRAJ = "dcd"
    TOPOLOGY = "build/system.pdb"
    TOPOLOGY_TYPE = "pdb"
    #: VMD writes that PDB with the input's chain letters and residue numbers, the partner on its own letter
    #: (LIGAND_CHAIN), not tleap's 1, 2, 3 ...: the viewer and the videos select by those
    OWN_NUMBERING = True
    FORCEFIELD_NOTES = {"OPLS4": ("needs the OPLS license", OPLS4_LICENCE)}
    #: MMPBSA.py needs an Amber prmtop; a Desmond run is scored by Schrödinger's own Prime MM-GBSA
    #: (thermal_mmgbsa.py over the frames of the -out.cms), under the full suite's module and license
    MMGBSA = True
    MMGBSA_TOOL = ("Prime MM-GBSA (thermal_mmgbsa.py: each frame minimized and scored with S-OPLS and VSGB) under the Schrödinger "
                   "module; it needs 8 Prime license tokens from the site's shared pool and a user on HPRC's Schrödinger users list, "
                   "scores about one frame every 3 to 4 minutes, and waits up to 2 hours for the tokens; a refusal shows in the "
                   "stage's log and the MM-GBSA button on the Runs screen runs it again later")
    #: the chain letter the partner carries into the build, so Prime's -lig_asl can name it
    LIGAND_CHAIN = "L"

    @classmethod
    def part_progress(cls, part: Path) -> tuple[int, float | None] | None:
        """(completed steps, ns/day or None) for one production part, from the engine's own log
        beside it (r<i>/prod_s<k>_p<n>.log: "Chemical time: T ps, Step: N, ns/day: R" lines, the
        format strings of libstd_plugins.so in Desmond 2024-4) or, failing that, from its .ene
        (the time in the first column; the header line is "# 0:time ...", the pattern
        simulation_block_data.py reads). None when neither is there yet. queue.progress asks for
        the parts that already have their .dcd, so this sees finished parts; a running part's log
        lives in the jobcontrol scratch (r<i>/tmp/...) until the stage ends, and is looked for there."""
        stem = Path(str(part)[:-len(part.suffix)] if part.suffix else str(part))
        name = stem.name
        candidates = [stem.with_name(name + ".log")] + sorted(stem.parent.glob(f"tmp/**/{name}.log"))
        for log in candidates:
            try:
                text = log.read_text(errors="replace")
            except OSError:
                continue
            times = re.findall(r"Chemical time:?\s*([0-9.]+)\s*ps", text)
            if not times:
                continue
            ps = float(times[-1])
            rates = re.findall(r"(?:ns/day:|Avg)\s*([0-9.]+)", text)
            nsday = float(rates[-1]) if rates else None
            return int(round(ps / DT_PS)), nsday
        for ene in [stem.with_name(name + ".ene")] + sorted(stem.parent.glob(f"tmp/**/{name}.ene")):
            try:
                last = ""
                for line in ene.read_text(errors="replace").splitlines():
                    if line.strip() and not line.startswith("#"):
                        last = line
                if last:
                    return int(round(float(last.split()[0]) / DT_PS)), None
            except (OSError, ValueError, IndexError):
                continue
        return None

    # ------------------------------------------------------------------ render
    def render(self, spec: Spec, derived: dict | None = None) -> Render:
        mem = membrane_of(spec)
        ff = spec.forcefield
        if ff.protein == "OPLS_2005" and ff.water in S_OPLS_ONLY_WATER:
            # the build would stop at build_geometry with the install's own message; say it before Submit
            raise NotImplementedError(f"forcefield.water: {ff.water} cannot run with OPLS_2005 in Desmond 2024-4 (its "
                                      f"build_geometry refuses the pair: 'OPLS_2005 cannot be run with {WATER[ff.water][0]}'). "
                                      "Use SPC or TIP3P with OPLS_2005, or OPLS4 with a four-site water.")
        d = self.compute_derived(spec, derived)
        r = Render(engine=self.key, derived=d)
        sysm, pro, box = spec.system, spec.protocol, spec.box
        small = sysm.ligand_class == "small_molecule"
        apo = sysm.ligand_class == "none"
        rec = sysm.receptor_residues or [1, 1]
        lig = sysm.ligand_residues or [rec[1] + 1, rec[1] + 1]
        rec_mask = f":{rec[0]}-{rec[1]}"
        lig_mask = f":{lig[0]}-{lig[1]}"
        solute_mask = f":{rec[0]}-{lig[1]}" if not apo else rec_mask
        seg_ps = d["segment_ns"] * 1000.0
        checkpt_ps = self._checkpoint_ps(d["ns_per_day"], pro.save_ps, seg_ps)
        nseg = int(d["segments"])

        # --- build ---
        r.add("build/build.msj", self._build_msj(spec))
        r.add("build/build.sh", self._build_sh(spec, small), executable=True)
        r.add("build/topology.sh", self._topology_sh(spec), executable=True)
        r.add("build/complete_termini.py", self._complete_termini_py())
        if mem and float(mem.get("center_z_A", 0) or 0):
            r.add("build/shift_z.py", self._shift_z_py())
        if small:
            r.add("build/merge_ligand.py", self._merge_py())

        # --- the ladder and one production segment ---
        r.add("md/relax.msj", self._relax_msj(spec))
        r.add("md/prod.msj", self._prod_msj(spec, seg_ps, checkpt_ps))
        r.add("md/dtr_to_dcd.tcl", DTR_TO_DCD_TCL)

        # --- analysis: Amber's cpptraj template, reading the PDB topology and Desmond's residue names ---
        # a bilayer: the box per frame for the area per lipid, as on the Amber-topology engines (VMD's DCD
        # carries the cell of every frame, so cpptraj reads it from the trajectory)
        # the ions the build carries bear the salt's names (NA, CL, K, MG): with any, the strip keeps them by their place,
        # the solute and its ions first in the built system, and drops what follows (kept_last)
        strip = f"!(:{rec[0]}-{kept_last(spec)})" if carried_ions(spec) else STRIP_MASK
        cpp = self._cpptraj(spec, rec_mask, lig_mask, solute_mask, apo, strip, mem=mem)
        cpp = (cpp.replace("parm build/complex.prmtop", "parm build/system.pdb")
                  .replace("reference build/complex.inpcrd [start]", "reference build/system.pdb [start]"))
        r.add("analysis/analysis.cpptraj.template", cpp)

        # --- jobs ---
        jobs: list[Job] = []
        r.add("jobs/10_build.sbatch", self._build_sbatch(spec), executable=True)
        jobs.append(Job("build", "jobs/10_build.sbatch", description="Desmond System Builder: solvate, ionize, OPLS"))
        r.add("jobs/20_equil.sbatch", self._equil_sbatch(spec), executable=True)
        jobs.append(Job("equil", "jobs/20_equil.sbatch", depends=[("build", "afterok")],
                        array=pro.replicates, gpu=True, description="multisim: Brownian relax, heat, NPT ladder"))
        for k in range(1, nseg + 1):
            r.add(f"jobs/30_prod_s{k}.sbatch", self._prod_sbatch(spec, k, nseg, d["walltime"]), executable=True)
            dep = ("equil", "aftercorr") if k == 1 else (f"prod_s{k-1}", "aftercorr")
            jobs.append(Job(f"prod_s{k}", f"jobs/30_prod_s{k}.sbatch", depends=[dep],
                            array=pro.replicates, gpu=True,
                            description=f"multisim production segment {k} of {nseg}, {d['segment_ns']:g} ns, then VMD to DCD"))
        last = f"prod_s{nseg}"
        # cpptraj comes with the Amber module: the analysis job is Amber's, reading the DCD parts
        r.add("jobs/40_analysis.sbatch", self._analysis_sbatch(spec).replace("prod_s*_p*.nc", "prod_s*_p*.dcd"),
              executable=True)
        jobs.append(Job("analysis", "jobs/40_analysis.sbatch", depends=[(last, "afterok")],
                        description="cpptraj on the DCD parts: RMSD, RMSF, Rg, contacts, viewer trajectory"))
        if mmgbsa_runs(spec):
            r.add("analysis/prime_mmgbsa_to_hopemd.py", self._prime_convert_py())
            r.add("jobs/50_mmgbsa.sbatch", self._mmgbsa_sbatch(spec, nseg, d["segment_ns"]), executable=True)
            jobs.append(Job("mmgbsa", "jobs/50_mmgbsa.sbatch", depends=[(last, "afterok")], array=pro.replicates,
                            description=self._mmgbsa_description(spec)))
        self.add_split_links(spec, r)
        self.add_tidy(spec, r, jobs)
        r.jobs = jobs
        r.add("jobs/submit.sh", self._submit_sh(spec, jobs), executable=True)
        r.add("README.txt", self._readme(spec, d, nseg))

        # --- what to read before Submit ---
        if ff.protein == "OPLS4":
            r.notes.append(OPLS4_LICENCE)
        else:
            r.notes.append("OPLS_2005 is the force field the academic Desmond license covers; OPLS4 (S-OPLS in Desmond 2024-4) "
                           "needs the OPLS license.")
        if has_nucleic(sysm):
            r.notes.append(f"The nucleic acid is typed by assign_forcefield with {FORCEFIELD[ff.protein]}, the force field of the whole "
                           "system, as the protein is. OL21, bsc1, OL15 and OL3 are the nucleic-acid force fields of the Amber-topology "
                           "engines and do not apply on Desmond.")
        if mmgbsa_runs(spec):
            r.notes.append("MM-GBSA on a Desmond run is Schrödinger's Prime MM-GBSA (thermal_mmgbsa.py over the frames of the production, "
                           "each minimized and scored with S-OPLS and VSGB, the partner named by its chain letter), under the full "
                           "Schrödinger module and its license: the job needs a user on HPRC's Schrodinger users list and 8 Prime "
                           "(PSP_PLOP) license tokens free in the site's shared pool. Each Prime subjob takes 8, so the job runs one, at 3 to "
                           "4 minutes a frame, with the walltime to match. When fewer than 8 are free the job waits for them, counting "
                           "every 5 minutes for up to 2 hours; past that, or without access to the suite, it ends red with the license "
                           "or access message in its log, and the MM-GBSA button on the Runs screen runs it again later. MMPBSA.py is "
                           "not used: it needs an Amber topology.")
        r.notes.append("The trajectory is Desmond's .dtr; each finished part is converted to DCD by VMD in the production job, "
                       "and cpptraj reads the parts with build/system.pdb as its topology (no charges: RMSD, RMSF, Rg, "
                       "contacts and the interface metrics work).")
        if small:
            r.notes.append("Small molecule: Desmond's assign_forcefield types the ligand with the OPLS force field in the build; "
                           "there is no antechamber step and no param job. The ligand is merged into the receptor's structure "
                           "from its SDF/MOL2 (bond orders kept) before the System Builder.")
        if box.shape != "rectangular":
            r.notes.append(f"Desmond's System Builder makes an orthorhombic box with {box.padding_A:g} Å of buffer on each side; "
                           f"the {box.shape} shape of the spec is not used here.")
        if spec.compute.gpus_per_replicate > 1:
            r.notes.append(f"Desmond runs each replicate on one GPU; the other {spec.compute.gpus_per_replicate - 1} card(s) "
                           "asked per replicate would sit idle.")
        if mem:
            r.notes.append(f"Bilayer on Desmond: the System Builder's membrane_box tiles a pre-equilibrated {mem['lipid']} patch around the "
                           "solute, which must arrive oriented with the membrane normal along z and the bilayer center at z = 0 (the OPM "
                           "frame; the import's orientation does that, and build/shift_z.py moves a non-zero center). Schrödinger's own "
                           "membrane relaxation protocol (desmond_membrane_relax.msj.template) replaces the water ladder, and production "
                           "runs NPgT (constant pressure and surface tension). Desmond ships POPC, POPE, DPPC and DMPC.")
        r.notes.append("Timestep 2 fs RESPA (2/2/6 fs), no HMR: Desmond's scheme. An interrupted segment continues from multisim's "
                       f"checkpoint (-RESTART): multisim checkpoints itself {MULTISIM_MARGIN_S // 60} min before the job's walltime "
                       "(-max-walltime), and the job asks to be resubmitted. A segment killed before that (a cancel) runs again "
                       "from its start when resubmitted.")
        r.notes.append("The academic license is per user: the build stops at once with a license error for an account that is "
                       "not on the Desmond users list (the desmond group on Grace and ACES). The files follow what Desmond 2024-4 ships.")
        return r

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _checkpoint_ps(ns_per_day: float, save_ps: float, seg_ps: float) -> float:
        """Desmond's checkpt.interval and maeff_output.interval (ps): about CHECKPOINT_MINUTES of
        wall time at the estimated rate, a whole number of frames, never past the segment."""
        ps = ns_per_day * 1000.0 / (24 * 60) * CHECKPOINT_MINUTES
        ps = max(save_ps, min(seg_ps, ps))
        return max(save_ps, round(ps / save_ps) * save_ps)

    @staticmethod
    def _jobname(spec: Spec, stage: str) -> str:
        """A multisim -JOBNAME: letters, digits and underscores from the run's name (check_jobname
        in starter/ui/cmdline.py forbids a leading '-' and any '='; file names come from it)."""
        return f"{stage}_" + re.sub(r"[^A-Za-z0-9_]+", "_", spec.name).strip("_")

    @staticmethod
    def _restraint(weight: float, atoms: str = "solute_heavy_atom") -> str:
        """One harmonic positional restraint in the msj syntax the shipped protocol uses
        (desmond_npt_relax.msj, Desmond 2024-4: restraints.new = [{ name = posre_harm atoms = ...
        force_constants = ... }]); the older `restrain = { atom = ... force_constant = ... }` form
        is not in any shipped file of this version. k in kcal/mol/A^2."""
        return ("   restraints.new = [{\n"
                "      name = posre_harm\n"
                f"      atoms = {atoms}\n"
                f"      force_constants = {weight:g}\n"
                "   }]\n")

    @staticmethod
    def _seed_line(spec: Spec, stage: str) -> str:
        """The per-replicate seed the job script fills in: bash arithmetic on the array index."""
        pro = spec.protocol
        if pro.seed >= 0:
            return f"SEED=$(( {pro.seed} + 1000 * SLURM_ARRAY_TASK_ID{' + 100 * SEG_NO' if stage == 'prod' else ''} ))   # protocol.seed, offset per replicate" + (" and segment" if stage == "prod" else "")
        return "SEED=$(( RANDOM * 32768 + RANDOM + 1 ))   # protocol.seed is -1: one per replicate, printed in the multisim log"

    # ------------------------------------------------------------------ build
    def _build_msj(self, spec: Spec) -> str:
        ff, box = spec.forcefield, spec.box
        solvent = WATER[ff.water][0]
        cation, anion = IONS[box.salt]
        forcefield = FORCEFIELD[ff.protein]
        b = box.padding_A
        mem = membrane_of(spec)
        # build_geometry's membrane_box (structure.py: _membrane_box): the lipid patch, and sizes that are
        # buffers around the solute in x, y and z (use_buffer=1); the bilayer sits at z = 0, normal along z
        mp = float(mem["padding_A"]) if mem else 0.0
        membrane = (f"""   membrane_box = {{
      lipid = {mem['lipid']}
      size  = [{mp:g} {mp:g} {mp:g}]
   }}
""" if mem else "")
        # assign_forcefield names every water of the solvent component after its own water setting (SPC unless told) and
        # types it so: without it, a TIP3P box was built as T3P and run as SPC. The crystal waters a run keeps join that
        # component (the System Builder takes them out of the solute and puts them at its end), so they run as it does
        return f"""# HOPE-MD Desmond system build for {spec.name}: the System Builder stages of multisim.
# Keywords and defaults from the install's stage/prepare/structure.py (build_geometry) and
# stage/prepare/forcefield.py (assign_forcefield), Desmond 2024-4; the shape of the file is the
# shipped system_builder.msj. Distances in A, the salt in mol/l.
task {{
   task = "desmond:auto"
}}

build_geometry {{
   solvent = {solvent}
   box = {{
      shape     = orthorhombic
      size      = [{b:g} {b:g} {b:g}]
      size_type = buffer
   }}
{membrane}   neutralize_system = true
   add_counterion = {{
      ion    = {cation}
      number = 0
   }}
   salt = {{
      positive_ion  = {cation}
      negative_ion  = {anion}
      concentration = {box.salt_mM / 1000.0:g}
   }}
   override_forcefield = {forcefield}
}}

assign_forcefield {{
   forcefield = {forcefield}
   water      = {solvent}
}}
"""

    def split_link_note(self, spec: Spec) -> str:
        return "The build writes each disulfide as an SSBOND record, without which Maestro's PDB reader bonds no two sulfurs"

    def _build_sh(self, spec: Spec, small: bool) -> str:
        sysm, ff, box = spec.system, spec.forcefield, spec.box
        cation, anion = IONS[box.salt]
        jobname = self._jobname(spec, "build")
        # waters that came with the chains go, as pdb4amber --dry does in the Amber build: the crystal waters a
        # run keeps come from input/waters.pdb, with their hydrogens, which is how the System Builder knows a
        # water. Ions and other hetero groups stay. Only ATOM/HETATM/TER lines are kept: END, REMARK and
        # CONECT lines of two files would collide, and Maestro's PDB reader bonds by residue template and
        # geometry (pdbconvert; its -no_geometry flag is what turns that off).
        water_re = " && substr($0, 18, 3) !~ /^(HOH|WAT|SOL|DOD|TIP)/"
        keep = f"awk '(($1 == \"ATOM\" || $1 == \"HETATM\"){water_re}) || $1 == \"TER\"'"
        if small:
            ligand = f"""LIG=$(ls input/ligand.* | head -1)
echo "[hopemd] ligand $LIG -> Maestro, merged with the receptor (bond orders and formal charges kept for OPLS typing)"
"$SCHRODINGER/utilities/structconvert" "$LIG" build/ligand_in.mae > build/structconvert_ligand.log 2>&1
"$SCHRODINGER/run" build/merge_ligand.py build/complex_in.mae build/ligand_in.mae build/complex_in.mae "{spec.name}" {self._ligand_chain(spec)} > build/merge_ligand.log 2>&1 \\
  || {{ tail -20 build/merge_ligand.log; echo '[hopemd] merging the ligand failed; see build/merge_ligand.log'; exit 3; }}
"""
        else:
            ligand = ""
        # the ions Clean kept, carried past protonation by Prepare (input/ions.pdb), go in last, after the
        # partner, where the residue ranges the cpptraj masks use expect them: as PDB lines after a polymer
        # partner, merged after a small molecule, which joins the structure as Maestro
        ions = carried_ions(spec)
        if ions and small:
            ligand += (f"""echo "[hopemd] the ions Prepare carried past protonation go in after the ligand: {', '.join(ions)}"
"$SCHRODINGER/utilities/structconvert" input/ions.pdb build/ions_in.mae > build/structconvert_ions.log 2>&1
"$SCHRODINGER/run" build/merge_ligand.py build/complex_in.mae build/ions_in.mae build/complex_in.mae "{spec.name}" - > build/merge_ions.log 2>&1 \\
  || {{ tail -20 build/merge_ions.log; echo '[hopemd] merging the carried ions failed; see build/merge_ions.log'; exit 3; }}
""")
        lig_chain = self._ligand_chain(spec)
        # those that bind the partner come first and take its chain letter, so Prime's -lig_asl counts them with it
        n_par = partner_ions(spec)
        relabel = (f" | awk -v n={n_par} '{{ if ($1 == \"HETATM\" && ++k <= n) print substr($0, 1, 21) \"{lig_chain}\" substr($0, 23); else print }}'"
                   if n_par else "")
        ion_lines = f"  {keep} input/ions.pdb{relabel}\n" if ions and not small else ""
        # the crystal waters kept go in last; the System Builder takes them out of the solute ("System includes crystal
        # water molecules"), puts no water over them, and adds them to the end of the solvent, typed as its water
        waters = crystal_waters(spec)
        water_lines = "  awk '$1 == \"ATOM\" || $1 == \"HETATM\" || $1 == \"TER\"' input/waters.pdb\n" if waters else ""
        partner = (f"""  {keep} input/ligand.pdb | awk '{{ if ($1 == "ATOM" || $1 == "HETATM") print substr($0, 1, 21) "{lig_chain}" substr($0, 23); else print }}'
  echo TER
""" if sysm.ligand_class in ("peptide", "protein", "nucleic_acid") else "")
        mem = membrane_of(spec)
        cz = float(mem.get("center_z_A", 0) or 0) if mem else 0.0
        shift = (f"""echo "[hopemd] bilayer: the structure arrives oriented with the membrane normal along z; its center at z = {cz:g} A is moved to 0 for the System Builder"
"$SCHRODINGER/run" build/shift_z.py build/complex_in.mae build/complex_in.mae {-cz:g} > build/shift_z.log 2>&1 \\
  || {{ tail -20 build/shift_z.log; echo '[hopemd] shift_z.py failed; see build/shift_z.log'; exit 3; }}
""" if mem and cz else ("echo \"[hopemd] bilayer: the structure arrives oriented with the membrane normal along z and its center at z = 0, as the System Builder's membrane_box needs\"\n" if mem else ""))
        states = ("propka's states came with the Prepare stage as HID/HIE/HIP, ASH, GLH, LYN, CYX, which Maestro's PDB "
                  "reader knows (mmshare-v6.8/data/mmpdb/mmpdb_v3.ini lists them)" if sysm.prepared
                  else "the protonation is what the hydrogens in the input say")
        return f"""#!/bin/bash
# HOPE-MD build for {spec.name} on Desmond: receptor and partner into one structure, then multisim's
# System Builder (build_geometry: {WATER[ff.water][0]} water, {box.padding_A:g} A buffer, {box.salt_mM:g} mM {box.salt};
# assign_forcefield: {FORCEFIELD[ff.protein]}). Run from the run folder with the Desmond module loaded
# (jobs/10_build.sbatch does that); build/topology.sh writes the topology and system.json afterwards, under VMD.
set -euo pipefail
mkdir -p build
test -n "${{SCHRODINGER:-}}" || {{ echo '[hopemd] SCHRODINGER is not set: module load Desmond/{spec.site.version('desmond')} first'; exit 2; }}
echo "[hopemd] input: receptor{' + partner' if partner else ''}{' + the carried ions ' + ', '.join(ions) if ion_lines else ''}{f' + {len(waters)} crystal water(s)' if waters else ''} into build/complex_in.pdb ({states})"
{{
  {keep} input/receptor.pdb
  echo TER
{partner}{ion_lines}{water_lines}  echo END
}} > build/complex_in.pdb
# every disulfide as an SSBOND record, those across the split of receptor and partner too: Maestro's PDB reader bonds two
# sulfurs only when a record says so, and the CONECT lines of the two files are not kept (hopemd.structure ssbond)
HOPEMD_HOME="${{HOPEMD_HOME:-{hopemd_home()}}}"
PYTHONPATH="$HOPEMD_HOME" "${{HOPEMD_PYTHON:-python3}}" -s -m hopemd.structure ssbond build/complex_in.pdb \\
  || {{ echo '[hopemd] the disulfides could not be written for Maestro (hopemd.structure ssbond)'; exit 3; }}
{f'echo "chain.name {lig_chain}" > build/ligand_asl.txt   # the partner as Prime MM-GBSA names it (-lig_asl)' if sysm.ligand_class != "none" else ''}
hcount() {{ awk '($1 == "ATOM" || $1 == "HETATM") && (substr($0, 77, 2) ~ /^ *H$/ || substr($0, 13, 4) ~ /^ *[0-9]?H/)' "$1" | wc -l; }}
NH_REC=$(hcount input/receptor.pdb); NH_LIG={'$(hcount input/ligand.pdb)' if partner else '1'}
echo "[hopemd] structconvert: PDB -> Maestro ($SCHRODINGER/utilities/structconvert)"
"$SCHRODINGER/utilities/structconvert" build/complex_in.pdb build/complex_in.mae > build/structconvert.log 2>&1 \\
  || {{ tail -20 build/structconvert.log; echo '[hopemd] structconvert failed; see build/structconvert.log'; exit 3; }}
if [ "$NH_REC" -eq 0 ] || [ "$NH_LIG" -eq 0 ]; then
  # assign_forcefield needs every atom present, in every component: a docked peptide arrives without
  # hydrogens (and without its OXT) beside a protonated receptor, and the OPLS typer stops at it
  # ("mmlewis_apply(): Unable to handle mol 2"). complete_termini.py
  # gives each chain without hydrogens zwitterionic termini (OXT, N+), as the Amber build gets them, and
  # applyhtreat (utilities/) then adds the hydrogens with Maestro's default treatment (all-atom, no
  # lone pairs); chains that have hydrogens keep them. The states are Maestro's, not propka's: run
  # Prepare first for propka's.
  echo "[hopemd] no hydrogens in the $( [ "$NH_REC" -eq 0 ] && printf receptor; [ "$NH_REC" -eq 0 ] && [ "$NH_LIG" -eq 0 ] && printf ' and '; [ "$NH_LIG" -eq 0 ] && printf partner ): termini completed, hydrogens added with applyhtreat (default states, not propka's)"
  "$SCHRODINGER/run" build/complete_termini.py build/complex_in.mae build/complex_in.mae > build/complete_termini.log 2>&1 \\
    || {{ tail -20 build/complete_termini.log; echo '[hopemd] complete_termini.py failed; see build/complete_termini.log'; exit 3; }}
  grep '^\\[hopemd\\]' build/complete_termini.log || true
  "$SCHRODINGER/utilities/applyhtreat" build/complex_in.mae build/complex_in_h.mae > build/applyhtreat.log 2>&1 \\
    && mv -f build/complex_in_h.mae build/complex_in.mae \\
    || {{ tail -20 build/applyhtreat.log; echo '[hopemd] applyhtreat failed; see build/applyhtreat.log'; exit 3; }}
fi
{shift}{ligand}echo "[hopemd] multisim: build_geometry ({WATER[ff.water][0]}, orthorhombic box with {box.padding_A:g} A buffer, neutralized, {box.salt_mM:g} mM {cation}/{anion}), assign_forcefield ({FORCEFIELD[ff.protein]})"
# -HOST localhost is this compute node; -LOCAL keeps the main job in build/ (multisim's own help:
# "run main job in the local directory"); jobcontrol's scratch for the stages is build/tmp
export SCHRODINGER_TMPDIR="$PWD/build/tmp"; mkdir -p "$SCHRODINGER_TMPDIR"
# job control's database in the run folder, not ~/.schrodinger/.jobdb2 (JobDB.pm reads SCHRODINGER_JOBDB2)
export SCHRODINGER_JOBDB2="$PWD/schrodinger/jobdb"; mkdir -p "$SCHRODINGER_JOBDB2"
unset PYTHONPATH   # nothing of ours on Schrodinger's python path (site.py's rule for this module)
( cd build && "$SCHRODINGER/utilities/multisim" -HOST localhost -maxjob 1 -LOCAL -WAIT -JOBNAME {jobname} \\
    -m build.msj complex_in.mae -o complex.cms > multisim_build.out 2>&1 ) \\
  || {{ tail -30 build/multisim_build.out; echo '[hopemd] the system build failed; see build/multisim_build.out and build/{jobname}_multisim.log'; exit 3; }}
test -s build/complex.cms || {{ echo '[hopemd] multisim wrote no build/complex.cms; see build/{jobname}_multisim.log'; exit 3; }}
echo "[hopemd] built: build/complex.cms; the topology and system.json follow from build/topology.sh"
"""

    @staticmethod
    def _complete_termini_py() -> str:
        return '''"""Zwitterionic termini for every molecule that arrives without hydrogens, before applyhtreat builds
them: an OXT (formal charge -1) on a C that ends a peptide without one, +1 on an N that starts it,
so the hydrogens come out as NH3+ and COO-, as the Amber build gets them. A molecule that carries
hydrogens is left as it is. Runs under $SCHRODINGER/run (Schrodinger's python).
Usage: $SCHRODINGER/run build/complete_termini.py in.mae out.mae
"""
import math
import sys

from schrodinger import structure


def oxt_position(c, ca, o, length=1.25):
    """Where the second carboxylate oxygen goes: along the bisector away from CA and O, in their plane."""
    def unit(a, b):
        d = [a[i] - b[i] for i in range(3)]
        n = math.sqrt(sum(x * x for x in d)) or 1.0
        return [x / n for x in d]
    u, v = unit(c, ca), unit(c, o)
    d = [u[i] + v[i] for i in range(3)]
    n = math.sqrt(sum(x * x for x in d)) or 1.0
    return [c[i] + length * d[i] / n for i in range(3)]


def main(src, dst):
    st = structure.StructureReader.read(src)
    log = []
    # by molecule (what is bonded together), not by chain letter: a docked peptide often carries the
    # receptor's chain letter, and it is its own molecule either way
    for mol in st.molecule:
        residues = list(mol.residue)
        if not residues or any(a.element == "H" for a in mol.atom):
            continue                                    # protonated already (or empty): nothing to complete
        for res in residues:
            names = {a.pdbname.strip(): a for a in res.atom}
            c, ca, o, n = names.get("C"), names.get("CA"), names.get("O"), names.get("N")
            if c is not None and ca is not None and o is not None:
                bonded_n = [b for b in c.bonded_atoms if b.pdbname.strip() == "N" and b.resnum != res.resnum]
                if not bonded_n and "OXT" not in names:
                    x, y, z = oxt_position(c.xyz, ca.xyz, o.xyz)
                    new = st.addAtom("O", x, y, z)
                    new.pdbname, new.pdbres, new.resnum, new.inscode, new.chain = " OXT", c.pdbres, c.resnum, c.inscode, c.chain
                    new.formal_charge = -1
                    st.addBond(c.index, new.index, 1)
                    log.append("OXT added to %s %s%d" % (res.chain, res.pdbres.strip(), res.resnum))
            if n is not None:
                bonded_c = [b for b in n.bonded_atoms if b.pdbname.strip() == "C" and b.resnum != res.resnum]
                if not bonded_c and n.formal_charge == 0:
                    n.formal_charge = 1
                    log.append("N-terminus %s %s%d set to NH3+" % (res.chain, res.pdbres.strip(), res.resnum))
    st.write(dst)
    for line in log:
        print("[hopemd] " + line)
    if not log:
        print("[hopemd] no termini to complete")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
'''

    def _ligand_chain(self, spec: Spec) -> str:
        """A chain letter no receptor chain uses, for the partner: Prime's -lig_asl names it, and the
        pose files of a docking run often carry the receptor's letter."""
        used = {str(c.get("id", "")).strip().upper() for c in (spec.system.chains_found or [])}
        for letter in self.LIGAND_CHAIN + "ZYXWVUTSRQPONMKJIHGFEDCBA":
            if letter not in used:
                return letter
        return "L"

    @staticmethod
    def _shift_z_py() -> str:
        return '''"""Move a structure along z so the bilayer center the import found sits at z = 0, where Desmond's
System Builder places its membrane. Runs under $SCHRODINGER/run.
Usage: $SCHRODINGER/run build/shift_z.py in.mae out.mae <dz in A>
"""
import sys

from schrodinger import structure

st = structure.StructureReader.read(sys.argv[1])
dz = float(sys.argv[3])
xyz = st.getXYZ()
xyz[:, 2] += dz
st.setXYZ(xyz)
st.write(sys.argv[2])
print("[hopemd] moved %d atoms by %.2f A along z" % (st.atom_total, dz))
'''

    @staticmethod
    def _merge_py() -> str:
        return '''"""Merge the receptor and a small-molecule ligand into one structure for multisim's System
Builder. Runs under $SCHRODINGER/run (Schrodinger's python): the SDF/MOL2 keeps the ligand's bond
orders and formal charges, which a PDB would lose and OPLS typing needs. A chain of "-" merges the
second file as it is (the ions Prepare carried, after the ligand, with their own names).
Usage: $SCHRODINGER/run build/merge_ligand.py receptor.mae ligand.mae out.mae [title] [chain]
"""
import sys

from schrodinger import structure

rec = structure.StructureReader.read(sys.argv[1])
lig = structure.StructureReader.read(sys.argv[2])
chain = sys.argv[5] if len(sys.argv) > 5 else "L"
for atom in lig.atom:                       # the chain letter Prime's -lig_asl names (chain.name L)
    if chain != "-":
        atom.chain = chain
        atom.pdbres = "LIG "
out = rec.merge(lig, copy_props=False)
out.title = sys.argv[4] if len(sys.argv) > 4 else "complex"
out.write(sys.argv[3])
print("merged %d + %d atoms into %s" % (rec.atom_total, lig.atom_total, sys.argv[3]))
'''

    def _topology_sh(self, spec: Spec) -> str:
        ff = spec.forcefield
        return f"""#!/bin/bash
# HOPE-MD: the topology and the system record of a Desmond build. Run from the run folder with VMD
# loaded (jobs/10_build.sbatch loads it after the Desmond steps). build/system.pdb is written by VMD
# from build/complex.cms, so its atoms are exactly the ones VMD puts into every DCD part (the same
# mae reader; a four-site water's virtual site counts the same way in both). build/system.json is
# counted from that PDB, with the box read from the cms header (r_chorus_box_ax/by/cz).
set -euo pipefail
test -s build/complex.cms || {{ echo '[hopemd] no build/complex.cms: run build/build.sh first'; exit 2; }}
echo "[hopemd] topology for cpptraj and the videos: build/system.pdb (VMD's mae reader)"
vmd -dispdev text -eofexit > build/system_pdb.log 2>&1 <<'EOF'
mol new build/complex.cms type mae waitfor all
set sel [atomselect top all]
$sel writepdb build/system.pdb
quit
EOF
test -s build/system.pdb || {{ tail -20 build/system_pdb.log; echo '[hopemd] VMD wrote no build/system.pdb; see build/system_pdb.log'; exit 3; }}
# the counts, with the stdlib of whatever python is at hand (the server's interpreter when it is)
PY="${{HOPEMD_PYTHON:-python3}}"; command -v "$PY" >/dev/null 2>&1 || PY=python3
"$PY" -I - <<'EOF'
import json
water = {{"SPC", "T3P", "T4P", "T4PE", "HOH", "WAT"}}
cations, anions = {{"NA", "K", "MG"}}, {{"CL"}}
lipid_names = {{"POPC", "POPE", "DPPC", "DMPC"}}   # the bilayer patches the System Builder ships
atoms = waters = cat = an = 0
lipid_res = set(); lipid_kind = ""
for line in open("build/system.pdb"):
    if not line.startswith(("ATOM", "HETATM")):
        continue
    atoms += 1
    resname = line[17:21].strip().upper()
    if resname in lipid_names:
        lipid_res.add((line[21], line[22:27], resname)); lipid_kind = resname
    if resname in water:
        waters += line[12:16].strip().upper().startswith("O")   # one oxygen per water
    elif resname in cations:
        cat += 1
    elif resname in anions:
        an += 1
box = "?"
try:
    text = open("build/complex.cms", errors="replace").read()
    head = text[:text.index("m_atom[")]                       # the first CT: the full system
    names, values = head[head.index("f_m_ct {{") + 8:].split(":::", 1)
    props = dict(zip(names.split(), [v.strip() for v in values.strip().splitlines()]))
    box = " x ".join("%.1f" % float(props[k]) for k in ("r_chorus_box_ax", "r_chorus_box_by", "r_chorus_box_cz"))
except (ValueError, KeyError, OSError):
    pass
info = {{"atoms": atoms, "waters": waters, "ion_pairs": min(cat, an), "ions": cat + an, "box": box, "hmr": False,
        "water": "{ff.water}", "forcefield": "{FORCEFIELD[ff.protein]}", "engine": "desmond"}}
if lipid_res:   # the area per lipid divides the box's xy area by the lipids of one leaflet (summarise)
    info.update(lipids=len(lipid_res), lipid=lipid_kind, membrane=True)
json.dump(info, open("build/system.json", "w"))
print("[hopemd] built: %d atoms, %d waters, %d ion pairs, box %s A" % (atoms, waters, min(cat, an), box))
EOF
"""

    # ------------------------------------------------------------------ msj
    #: the membrane relaxation protocol Desmond ships (mmshare/data/desmond/), with $TEMPERATURE to fill
    MEMBRANE_RELAX_TEMPLATE = "desmond_membrane_relax.msj.template"

    def _relax_msj(self, spec: Spec) -> str:
        pro = spec.protocol
        if membrane_of(spec):
            return f"""# HOPE-MD relaxation for {spec.name} in a bilayer: Schrödinger's own lipid-protein protocol is used as
# shipped ($SCHRODINGER/mmshare-v*/data/desmond/{self.MEMBRANE_RELAX_TEMPLATE}: Brownian dynamics at 10 K,
# water barriers and NPgT stages with the membrane held in z, heating to the run's temperature, the restraints
# released), with $TEMPERATURE = {pro.temperature_K:g}. jobs/20_equil.sbatch copies it into r<i>/relax.msj from the
# installation at run time; the ladder settings of spec.json (heat_ps, equil_ps, restraint_kcal) are not used.
"""
        T, P = pro.temperature_K, pro.pressure_bar
        stage_ps = pro.equil_ps / 4.0
        heat = pro.heat_ps
        ramp = round(heat * 0.9, 1)
        k = pro.restraint_kcal
        npt = []
        for i, w in enumerate((k, 1.0, 0.1, 0.0), start=1):
            held = f"solute heavy atoms restrained at {w:g} kcal/mol/A^2" if w > 0 else "no restraints"
            # the shipped protocol couples the restrained NPT stages with barostat.tau = 50 and the free
            # one with 2.0, centering the trajectory on the solute only once the restraints are off
            tau = 50.0 if w > 0 else 2.0
            npt.append(f"""# {i + 2}. NPT {i}/4: {stage_ps:g} ps at {T:g} K and {P:g} bar, {held}
simulate {{
   title       = "NPT {i}/4, {T:g} K, {P:g} bar, {held}, {stage_ps:g} ps"
   annealing   = off
   time        = {stage_ps:g}
   timestep    = [0.002 0.002 0.006]
   temperature = {T:g}
   pressure    = {P:g}
   ensemble = {{
      class  = NPT
      method = Langevin
      thermostat.tau = 0.1
      barostat.tau   = {tau:g}
   }}
{self._restraint(w) if w > 0 else ''}   randomize_velocity.seed = {SEED_TOKEN}
   eneseq.interval   = 0.3
   trajectory.center = {'solute' if w == 0 else '[]'}
}}
""")
        return f"""# HOPE-MD relaxation for {spec.name}, shaped from Desmond's own protocol
# ($SCHRODINGER/mmshare-v6.8/data/desmond/desmond_npt_relax.msj, 2024-4): the same stages and keywords,
# with the ladder's weights, times, temperature and pressure from spec.json. Times in ps, energies in
# kcal/mol, force constants in kcal/mol/A^2. {SEED_TOKEN} is filled per replicate by jobs/20_equil.sbatch.
# The restraint syntax is the one the shipped file uses (restraints.new = [{{ name = posre_harm ... }}]).
task {{
   task = "desmond:auto"
   set_family = {{
      desmond = {{
         checkpt.write_last_step = no
      }}
   }}
}}

# 1. In place of a minimization: GPU Desmond 2024-4 has no minimize stage (none in the install's
#    stage/*.py), and its protocol relaxes with Brownian dynamics at 10 K and small timesteps instead,
#    the solute heavy atoms held at {k:g} kcal/mol/A^2 (protocol.minimise_steps is not used).
simulate {{
   title       = "Brownian dynamics NVT, 10 K, small timesteps, solute heavy atoms restrained at {k:g} kcal/mol/A^2, 100 ps"
   annealing   = off
   time        = 100
   timestep    = [0.001 0.001 0.003]
   temperature = 10.0
   ensemble = {{
      class = "NVT"
      method = "Brownie"
      brownie = {{
         delta_max = 0.1
      }}
   }}
{self._restraint(k)}   randomize_velocity.seed = {SEED_TOKEN}
}}

# 2. Heating: NVT from 10 K to {T:g} K over {heat:g} ps on Desmond's annealing schedule
#    ([temperature time] pairs), the solute heavy atoms still held at {k:g} kcal/mol/A^2
simulate {{
   title       = "heat NVT 10 -> {T:g} K over {heat:g} ps, solute heavy atoms restrained at {k:g} kcal/mol/A^2"
   annealing   = on
   time        = {heat:g}
   timestep    = [0.002 0.002 0.006]
   temperature = [[10.0 0] [{T:g} {ramp:g}] [{T:g} {heat:g}]]
   ensemble = {{
      class  = NVT
      method = Langevin
      thermostat.tau = 0.1
   }}
{self._restraint(k)}   randomize_velocity.seed = {SEED_TOKEN}
   randomize_velocity.temperature = 10.0
   eneseq.interval   = 0.3
   trajectory.center = []
}}

{''.join(npt)}"""

    def _prod_msj(self, spec: Spec, seg_ps: float, checkpt_ps: float) -> str:
        pro = spec.protocol
        # a bilayer runs NPgT (constant pressure and surface tension, the membrane's area free to move; the
        # ensemble Schrodinger's membrane protocols and kinetics_membrane_md.msj use), a water box NPT
        ens_class = "NPgT" if membrane_of(spec) else "NPT"
        restr = ""
        if pro.restrain:
            # the residues kept restrained in production, by chain and PDB number (the keys are "A:41", or "H:60A"
            # with an insertion code, which ASL reads as res.inscode), as an ASL selection: backbone atoms only, as
            # the other engines hold them, CA, C and N of an amino acid and P and C4' of a nucleotide (spec.HOLD_ATOMS)
            # A number that insertion codes share (thrombin's H:60 and H:60A-60I) is held without them: res.num alone
            # would hold all ten (checked with Desmond 2024-4's evaluate_asl on 1PPB)
            terms = []
            index = getattr(spec.system, "residue_index", None) or {}
            for key in pro.restrain:
                key = str(key)
                if ":" in key:
                    chain, num = key.split(":", 1)
                    m = re.fullmatch(r"\s*(-?\d+)([A-Za-z])\s*", num)
                    if m:
                        terms.append(f"(chain.name {chain} AND res.num {m.group(1)} AND res.inscode {m.group(2)})")
                    elif any(re.fullmatch(re.escape(f"{chain}:{num.strip()}") + "[A-Za-z]", k) for k in index):
                        terms.append(f"(chain.name {chain} AND res.num {num} AND NOT res.inscode "
                                     + ",".join(chr(c) for c in range(ord("A"), ord("Z") + 1)) + ")")
                    else:
                        terms.append(f"(chain.name {chain} AND res.num {num})")
                else:
                    terms.append(f"(res.num {key})")
            atoms = "atom.ptype CA,C,N"
            if has_nucleic(spec.system):
                # Desmond 2024-4's ASL reads a primed name only on its own and as its four PDB characters in double
                # quotes (atom.ptype " C4'"); in a list with others (CA,C,N,P," C4'") it matches the base's C4 instead.
                # The quotes are escaped inside the msj's own double-quoted string (checked with multisim's parser)
                atoms = "atom.ptype CA,C,N,P OR atom.ptype \\\" C4'\\\""
            asl = f'"asl:({" OR ".join(terms)}) AND ({atoms})"'
            restr = self._restraint(pro.restraint_kcal, asl)
        # trajectory.write_last_step (the segment's last step as a frame, when the segment is not a whole number
        # of frame intervals) is a key Desmond 2024 added: Desmond 2023-4 (ACES) refuses the stage with
        # "Unrecognized parameters for stage[2]: trajectory.write_last_step", so it goes only to 2024 and later.
        ver = spec.site.version("desmond")
        write_last = "" if ver[:4].isdigit() and int(ver[:4]) < 2024 else "   trajectory.write_last_step = true\n"
        return f"""# HOPE-MD production for {spec.name}: one segment of {seg_ps:g} ps per multisim run, frames every
# {pro.save_ps:g} ps, 2 fs RESPA (near 2, far 6 fs: timestep = [0.002 0.002 0.006]), a checkpoint and the
# output cms every {checkpt_ps:g} ps. Keys as in the install's msj_defaults.py (the MD block) and its
# GUI's production stage (gui.py: jobname = "$MAINJOBNAME" dir = "." compress = "" checkpt.write_last_step = yes),
# which keeps the stage's files in the replicate folder under the multisim job's name:
# <JOBNAME>-out.cms, <JOBNAME>_trj/, <JOBNAME>.ene, <JOBNAME>.log, <JOBNAME>.cpt. The ensemble is the
# GUI's production default (MTK, thermostat.tau 1.0, barostat.tau 2.0: relaxation_protocols/Macromolecule_Relaxation_6.cfg).
# {SEED_TOKEN} is filled per replicate and segment by jobs/30_prod_s<k>.sbatch.
task {{
   task = "desmond:auto"
}}

simulate {{
   title    = "production segment, {seg_ps:g} ps at {pro.temperature_K:g} K and {pro.pressure_bar:g} bar"
   jobname  = "$MAINJOBNAME"
   dir      = "."
   compress = ""
   time        = {seg_ps:g}
   timestep    = [0.002 0.002 0.006]
   temperature = {pro.temperature_K:g}
   pressure    = {pro.pressure_bar:g}
   annealing   = off
   cutoff_radius  = {CUTOFF_A:g}
   coulomb_method = useries
   ensemble = {{
      class  = {ens_class}
      method = MTK
      thermostat.tau = 1.0
      barostat.tau   = 2.0
   }}
   glue = solute
   trajectory.interval        = {pro.save_ps:g}
   trajectory.center          = solute
   trajectory.periodicfix     = true
   trajectory.frames_per_file = 250
{write_last}   checkpt.interval        = {checkpt_ps:g}
   checkpt.write_last_step = yes
   maeff_output.interval     = {checkpt_ps:g}
   maeff_output.center_atoms = solute
{restr}   randomize_velocity.seed = {SEED_TOKEN}
}}
"""

    # ------------------------------------------------------------------ sbatch
    def _desmond_env(self) -> str:
        """The lines every Desmond job runs after the preamble: nothing of ours on Schrodinger's
        python path, and jobcontrol's scratch inside the folder the job works in (SCHRODINGER_TMPDIR,
        the Job Control variable that overrides the hosts file's tmpdir; the hosts file on Grace
        names /scratch/user for localhost), so a killed job leaves its files where they can be seen."""
        return ("unset PYTHONPATH   # site.py's rule for the Desmond module: keep Schrodinger's python clean\n"
                "export SCHRODINGER_TMPDIR=\"$PWD/tmp\"; mkdir -p tmp\n"
                # job control's database (job records, the per-node job server's logs) in the run folder:
                # without it every job writes to ~/.schrodinger/.jobdb2 (JobDB.pm reads SCHRODINGER_JOBDB2)
                "export SCHRODINGER_JOBDB2=\"$RUNDIR/schrodinger/jobdb\"; mkdir -p \"$SCHRODINGER_JOBDB2\"\n")

    def _build_sbatch(self, spec: Spec) -> str:
        return (self.sbatch_header(spec, "build", gpu=False, hours=1.0, cpus=4, mem_gb=16)
                + self.preamble(spec, "desmond") + f"""
bash build/build.sh
# the topology comes from VMD's reader, the one that will convert every trajectory part, so the atoms
# match by construction; VMD's toolchain cannot share a shell with the Desmond module, hence a fresh set
{spec.site.module_block("vmd")}
bash build/topology.sh
echo "[hopemd] build done"
""")

    def _equil_sbatch(self, spec: Spec) -> str:
        pro = spec.protocol
        ladder = 100.0 + pro.heat_ps + pro.equil_ps
        if membrane_of(spec):
            relax = (f"""TPL=$(ls "$SCHRODINGER"/mmshare-v*/data/desmond/{self.MEMBRANE_RELAX_TEMPLATE} 2>/dev/null | head -1)
test -s "$TPL" || {{ echo "[hopemd] the membrane relaxation protocol {self.MEMBRANE_RELAX_TEMPLATE} is not in this Desmond installation"; exit 2; }}
# Schrodinger's lipid-protein protocol, as shipped, with the run's temperature; the replicate's seed goes into
# every simulate stage through the task's set_family, else each replicate would relax with Desmond's default
sed -e "s/simulate.temperature = \\$TEMPERATURE/simulate.temperature = {pro.temperature_K:g}\\n      simulate.randomize_velocity.seed = $SEED/" \\
    -e "s/\\$TEMPERATURE/{pro.temperature_K:g}/g" "$TPL" > relax.msj
""")
            ladder_text = f"Schrödinger's membrane protocol (Brownian, water barriers, NPgT stages with the membrane held in z, heating to {pro.temperature_K:g} K, restraints released), about 0.5 ns"
        else:
            relax = f'sed "s/{SEED_TOKEN}/$SEED/g" ../md/relax.msj > relax.msj\n'
            ladder_text = f"Brownian 100 ps, heat {pro.heat_ps:g} ps, NPT 4 x {pro.equil_ps / 4:g} ps (restraints {pro.restraint_kcal:g}, 1, 0.1, 0 kcal/mol/A^2), {ladder / 1000:g} ns in all"
        return (self.sbatch_header(spec, "equil", gpu=True, hours=2.0, array=pro.replicates,
                                   throttle=spec.compute.max_concurrent)
                + self.preamble(spec, "desmond", workdir="r${SLURM_ARRAY_TASK_ID}") + f"""
R=r${{SLURM_ARRAY_TASK_ID}}
{self._desmond_env()}{self._seed_line(spec, "equil")}
{relax}if [ -s eq4.cms ]; then echo "[hopemd] replicate $SLURM_ARRAY_TASK_ID already equilibrated (eq4.cms)"; exit 0; fi
echo "[hopemd] relaxation: {ladder_text}"
# no -gpu: multisim 2024-4 has no such option (multisimstartup.py rejects what it does not know) and the
# backend's own -gpu is "ignored completely as gpu is always True" (desmond_startup.py); the card Slurm
# gave this task is the one it runs on. -HOST localhost is this node; -LOCAL keeps the job in this folder.
"$SCHRODINGER/utilities/multisim" -HOST localhost -maxjob 1 -LOCAL -WAIT -JOBNAME relax -m relax.msj ../build/complex.cms -o eq4.cms > relax_multisim.out 2>&1 \\
  || {{ tail -30 relax_multisim.out; echo "[hopemd] the relaxation failed for replicate $SLURM_ARRAY_TASK_ID; see $R/relax_multisim.log"; exit 3; }}
test -s eq4.cms || {{ echo "[hopemd] multisim wrote no eq4.cms; see $R/relax_multisim.log"; exit 3; }}
# multisim leaves every stage's files twice, as relax_<n>/ and as relax_<n>-out.tgz (about 33 MB each
# for 40 k atoms); the archives go, the folders and the log stay
rm -f relax_*-out.tgz
echo "[hopemd] equilibrated replicate $SLURM_ARRAY_TASK_ID"
""")

    def _prod_sbatch(self, spec: Spec, k: int, nseg: int, walltime: str) -> str:
        pro = spec.protocol
        prev = "eq4.cms" if k == 1 else f"prod_s{k-1}.cms"
        hours = parse_walltime(walltime) or 1.0
        maxwall = max(600, int(hours * 3600) - MULTISIM_MARGIN_S)
        return (self.sbatch_header(spec, f"prod_s{k}", gpu=True, walltime=walltime, array=pro.replicates,
                                   throttle=spec.compute.max_concurrent)
                + self.preamble(spec, "desmond", workdir="r${SLURM_ARRAY_TASK_ID}") + f"""
R=r${{SLURM_ARRAY_TASK_ID}}
{self._desmond_env()}SEG=s{k}; SEG_NO={k}; PREV={prev}; JOB=prod_${{SEG}}_p1
if [ -s prod_${{SEG}}.done ]; then echo "[hopemd] segment $SEG already complete"; exit 0; fi
test -s "$PREV" || {{ echo "[hopemd] $PREV is missing: the stage before this one has not finished"; exit 2; }}
{self._seed_line(spec, "prod")}
sed "s/{SEED_TOKEN}/$SEED/g" ../md/prod.msj > ${{JOB}}.msj
# the chemical time a part has reached, from the engine's log ("Chemical time: T ps, Step: N, ns/day: R"
# lines, the format strings of libstd_plugins.so) or its .ene (time in the first column); 0 when neither exists
chem_time() {{
  local t=""
  [ -s "$1.log" ] && t=$(grep -oE 'Chemical time:? *[0-9.]+' "$1.log" | tail -1 | grep -oE '[0-9.]+$' || true)
  [ -z "$t" ] && [ -s "$1.ene" ] && t=$(awk '!/^#/ && NF {{ t = $1 }} END {{ print t + 0 }}' "$1.ene")
  echo "${{t:-0}}"
}}
T0=$(chem_time "$JOB")
# One segment is one multisim job of {pro.length_ns / nseg:g} ns. multisim checkpoints itself after {maxwall} s
# (-max-walltime, {MULTISIM_MARGIN_S // 60} min before this job's walltime) and an interrupted segment continues from that
# checkpoint with -RESTART (multisim's own help: "restart the job from the interruption point"), the trajectory
# appended; when no checkpoint exists the segment starts from $PREV. A stage that was killed (a cancel, or Slurm at
# the walltime) left no data for multisim to continue from, and -RESTART runs it again from the segment's start
# ("No data of this subjob were saved from the previous run. Will restart it from scratch."). No -gpu: see
# jobs/20_equil.sbatch.
if [ -s ${{JOB}}-multisim_checkpoint ] && [ ! -s prod_${{SEG}}.cms ]; then
  echo "[hopemd] production $SEG: restart from ${{JOB}}-multisim_checkpoint (the last attempt reached $T0 ps; a stage killed before multisim checkpointed it runs again from the segment's start)"
  "$SCHRODINGER/utilities/multisim" -HOST localhost -maxjob 1 -LOCAL -WAIT -JOBNAME $JOB -RESTART ${{JOB}}-multisim_checkpoint \\
    -o prod_${{SEG}}.cms -max-walltime {maxwall} > ${{JOB}}_multisim.out 2>&1 || true
else
  echo "[hopemd] production $SEG: {pro.length_ns / nseg:g} ns from $PREV"
  "$SCHRODINGER/utilities/multisim" -HOST localhost -maxjob 1 -LOCAL -WAIT -JOBNAME $JOB -m ${{JOB}}.msj $PREV \\
    -o prod_${{SEG}}.cms -max-walltime {maxwall} > ${{JOB}}_multisim.out 2>&1 || true
fi
if [ ! -s prod_${{SEG}}.cms ]; then
  if [ -s ${{JOB}}-multisim_checkpoint ]; then
    echo "[hopemd] segment $SEG stopped at multisim's walltime checkpoint ($(chem_time "$JOB") ps): resubmit this job (Resume) to continue"
    exit 4
  fi
  tail -30 ${{JOB}}_multisim.out; echo "[hopemd] production $SEG failed for replicate $SLURM_ARRAY_TASK_ID; see $R/${{JOB}}_multisim.log"; exit 3
fi
T1=$(chem_time "$JOB")
# ns/day: Desmond's own figure from the last progress line of its log, else a plain measurement of this
# job's wall time (bash SECONDS) over the time it simulated
NSDAY=$(grep -oE 'ns/day: *[0-9.]+|Avg *[0-9.]+' ${{JOB}}.log 2>/dev/null | tail -1 | grep -oE '[0-9.]+$' || true)
[ -z "$NSDAY" ] && NSDAY=$(awk -v t0="$T0" -v t1="$T1" -v s="$SECONDS" 'BEGIN {{ if (s > 0 && t1 > t0) printf "%.1f", (t1 - t0) / 1000.0 / (s / 86400.0) }}')
[ -n "$NSDAY" ] && echo "$NSDAY" > ${{JOB}}.nsday
# the trajectory as DCD for cpptraj: VMD reads the stage's cms and its .dtr folder (clickme.dtr is the file
# the dtr plugin opens), drops the cms's own frame, and md/dtr_to_dcd.tcl keeps frame k of the segment, k
# intervals after its start, once each: not the frame at the start, nor the one write_last_step adds, nor a
# stretch a restart wrote twice. VMD's toolchain needs a fresh module set.
{spec.site.module_block("vmd")}
echo "[hopemd] VMD: ${{JOB}}_trj -> ${{JOB}}.dcd"
vmd -dispdev text -eofexit -e ../md/dtr_to_dcd.tcl -args ${{JOB}} < /dev/null > ${{JOB}}_dcd.log 2>&1
grep '^hopemd:' ${{JOB}}_dcd.log | sed 's/^hopemd:/[hopemd] DCD:/' || true
test -s ${{JOB}}.dcd || {{ tail -20 ${{JOB}}_dcd.log; echo "[hopemd] VMD wrote no ${{JOB}}.dcd; see $R/${{JOB}}_dcd.log"; exit 3; }}
touch prod_${{SEG}}.done
echo "[hopemd] segment $SEG of {nseg} done for replicate $SLURM_ARRAY_TASK_ID (${{NSDAY:-?}} ns/day)"
""")

    @staticmethod
    def _mmgbsa_description(spec: Spec) -> str:
        from ..spec import mmgbsa_window, mmgbsa_frames
        a0, a1, st = mmgbsa_window(spec)
        return f"Prime MM-GBSA (thermal_mmgbsa.py) over {mmgbsa_frames(spec)} frames, {a0:g}-{a1:g} ns every {st:g} ps"

    def _mmgbsa_sbatch(self, spec: Spec, nseg: int, segment_ns: float) -> str:
        """Prime MM-GBSA over the window of the production, one job per replicate: thermal_mmgbsa.py
        on each finished part's -out.cms (frames numbered over the run, mapped onto the part), the
        partner by its chain letter, then the per-frame table into the files summarise reads."""
        from ..spec import mmgbsa_window, mmgbsa_frames
        pro = spec.protocol
        a0, a1, st = mmgbsa_window(spec)
        save = pro.save_ps
        start = int(a0 * 1000 / save) + 1
        end = int(round(a1 * 1000 / save))
        step = max(1, int(round(st / save)))
        fpp = max(1, int(round(segment_ns * 1000 / save)))       # frames per part, all but the last
        mem = membrane_of(spec)
        # the wait for the tokens, then one subjob over the window's frames, with a fifth to spare; a window too long
        # for the CPU partition's limit goes to the next partition that holds it
        hours = PRIME_WAIT_H + mmgbsa_frames(spec) * PRIME_MIN_PER_FRAME / 60.0 * 1.2 + 0.5
        site = spec.site
        fits = [n for n in (site.cpu_partition, "long", "xlong") if n in site.partitions and site.partitions[n].max_hours >= hours]
        part = fits[0] if fits else site.cpu_partition
        hours = min(hours, site.partitions[part].max_hours)
        return (self.sbatch_header(spec, "mmgbsa", gpu=False, hours=hours, cpus=2, mem_gb=16, array=pro.replicates,
                                   partition=part)
                + self.preamble(spec, "schrodinger", workdir="r${SLURM_ARRAY_TASK_ID}") + self.MMGBSA_GUARD + f"""
# Prime MM-GBSA (the Schrödinger suite, LMS license): only a user on HPRC's "Schrodinger users" list gets past the
# license check; a refusal ends this job red with the message below, and the MM-GBSA button runs it again later.
# Prime's minimize-and-score job type (REAL_MIN) checks out 8 PSP_PLOP tokens for each subjob (Schrodinger 2025-3
# Prime.py: 'REAL_MIN': 8; with 10 free, the second of 8 subjobs was refused "requested 8, available 5"), so on a
# pool of 25 the job runs one subjob; HOPEMD_PRIME_NJOBS runs more where the pool holds 8 for each.
# Prime gives up at once when fewer than it needs are free, so the job counts the free tokens first (HPRC's
# license_status) and waits for them: every 5 minutes, for up to HOPEMD_PRIME_WAIT_MIN minutes (2 hours unless set)
# over the whole job. Past that it ends red and the MM-GBSA button runs it again. Where no count can be read, Prime
# finds out for itself.
NJOBS=${{HOPEMD_PRIME_NJOBS:-1}}
NEED=$(( 8 * NJOBS )); WAIT_MIN=${{HOPEMD_PRIME_WAIT_MIN:-120}}; DEADLINE=$(( $(date +%s) + WAIT_MIN * 60 ))
plop_count() {{   # "issued free" PSP_PLOP tokens, or nothing when no count can be read here
  {{ license_status -v Schrodinger 2>/dev/null || /sw/local/bin/license_status -v Schrodinger 2>/dev/null; }} \\
    | awk '/Users of PSP_PLOP:/ {{ k = 0; for (i = 2; i <= NF; i++) if ($(i-1) ~ /Total$/ && $i == "of") n[++k] = $(i+1); if (k >= 2) print n[1], n[1] - n[2]; exit }}'
}}
plop_wait() {{   # 0 once NEED tokens are free (or nothing can be counted), 1 when the wait is over, 2 when the pool is too small
  local got free
  while :; do
    got=$(plop_count)
    [ -z "$got" ] && return 0
    POOL=${{got% *}}; free=${{got#* }}
    [ "$NEED" -gt "$POOL" ] && return 2
    [ "$free" -ge "$NEED" ] && {{ echo "[hopemd] Prime MM-GBSA: $free PSP_PLOP license tokens free"; return 0; }}
    [ "$(date +%s)" -ge "$DEADLINE" ] && return 1
    echo "[hopemd] Prime MM-GBSA: $free of the $NEED PSP_PLOP license tokens it needs are free; waiting for them, until $(date -d @$DEADLINE +%H:%M) at the latest"
    sleep 300
  done
}}
i=$SLURM_ARRAY_TASK_ID; R=r$i
{self._desmond_env()}mkdir -p ../analysis
START={start}; END={end}; STEP={step}; FPP={fpp}   # the window {a0:g}-{a1:g} ns as frame numbers over the run, one part = $FPP frames
# the partner as Prime names it: the letter the build gave it (build/ligand_asl.txt); an older build kept the input's
# letters, so the partner's own letters, less the receptor's, stand in; a partner sharing its letter cannot be named
LIG_ASL=$(cat ../build/ligand_asl.txt 2>/dev/null || true)
if [ -z "$LIG_ASL" ] && [ -s ../input/ligand.pdb ]; then
  RC=" $(awk '($1 == "ATOM" || $1 == "HETATM") {{ print substr($0, 22, 1) }}' ../input/receptor.pdb | sort -u | tr '\n' ' ')"
  keep=""; for c in $(awk '($1 == "ATOM" || $1 == "HETATM") {{ print substr($0, 22, 1) }}' ../input/ligand.pdb | sort -u); do case "$RC" in *" $c "*) ;; *) keep="$keep,$c";; esac; done
  [ -n "$keep" ] && LIG_ASL="chain.name ${{keep#,}}"
fi
[ -n "$LIG_ASL" ] || {{ echo "[hopemd] the partner cannot be named for Prime: this run was built before the build tagged the partner, and it shares a chain letter with the receptor; resubmit from the build stage"; exit 2; }}
echo "[hopemd] Prime MM-GBSA ligand ASL: $LIG_ASL"
n=0; RAN=0
for cms in $(ls prod_s*_p*-out.cms 2>/dev/null | sort -V); do
  n=$((n + 1)); JOB=$(basename "$cms" -out.cms)
  lo=$(( (n - 1) * FPP + 1 )); hi=$(( n * FPP ))
  s=$(( START > lo ? START : lo )); e=$(( END < hi ? END : hi ))
  [ "$s" -gt "$e" ] && {{ echo "[hopemd] $JOB: outside the window"; continue; }}
  ls=$(( s - lo + 1 )); le=$(( e - lo + 1 ))
  # thermal_mmgbsa.py -j X writes X-prime-out.csv (Schrodinger 2025-3); the -prime_mmgbsa-out.csv name is also accepted
  if [ -s ${{JOB}}_mmgbsa-prime-out.csv ] || [ -s ${{JOB}}_mmgbsa-prime_mmgbsa-out.csv ]; then echo "[hopemd] $JOB: Prime MM-GBSA already done"; RAN=1; continue; fi
  echo "[hopemd] Prime MM-GBSA on $cms: frames $ls-$le of this part every $STEP (run frames $s-$e)"
  while :; do
    plop_wait; w=$?
    [ "$w" = 2 ] && {{ echo "[hopemd] Prime MM-GBSA: $NJOBS subjob(s) need $NEED PSP_PLOP license tokens, 8 each, and the site's pool holds $POOL: run it with fewer (HOPEMD_PRIME_NJOBS)"; exit 3; }}
    [ "$w" = 1 ] && {{ echo "[hopemd] Prime MM-GBSA: fewer than the $NEED Prime (PSP_PLOP) license tokens it needs were free for $WAIT_MIN minutes; run it again later with the MM-GBSA button"; exit 3; }}
    # thermal_mmgbsa.py reads frames range(start, end, step): its end is exclusive, so the last frame asked for is le + 1
    "$SCHRODINGER/run" thermal_mmgbsa.py -j ${{JOB}}_mmgbsa -start_frame $ls -end_frame $((le + 1)) -step_size $STEP \\
      -lig_asl "$LIG_ASL"{' -membrane' if mem else ''} -HOST localhost:$NJOBS -NJOBS $NJOBS "$cms" > ${{JOB}}_mmgbsa.out 2>&1 && break
    tail -30 ${{JOB}}_mmgbsa.out; grep -h -m3 -i -E "licen|denied|does not match" ${{JOB}}_mmgbsa-prime.log 2>/dev/null
    if grep -q -i -E "insufficient licen|could not checkout licen|unable to check out" ${{JOB}}_mmgbsa.out ${{JOB}}_mmgbsa-prime.log 2>/dev/null; then
      # another job took the tokens between the count and Prime's checkout: wait for them again while the time lasts
      if [ "$(date +%s)" -lt "$DEADLINE" ]; then echo "[hopemd] Prime MM-GBSA: the tokens went to another job first; waiting again"; sleep 300; continue; fi
      echo "[hopemd] Prime MM-GBSA: fewer than the $NEED Prime (PSP_PLOP) license tokens it needs are free on the site's server; run it again later with the MM-GBSA button"
    else
      echo "[hopemd] Prime MM-GBSA failed on $cms: the reason reads above (access to the Schrödinger suite, or an ASL that matches nothing); the MM-GBSA button runs it again"
    fi
    exit 3
  done
  RAN=1
done
[ "$RAN" = 1 ] || {{ echo "[hopemd] no finished part in the window: nothing scored"; exit 2; }}
# the per-frame dG_Bind table -> analysis/r<i>_mmgbsa_frames.csv and r<i>_mmgbsa.dat (what summarise reads, MMPBSA.py's layout)
# the job is inside $R, so the tables are in "." ("$R" here would name $R/$R)
"${{HOPEMD_PYTHON:-python3}}" -I ../analysis/prime_mmgbsa_to_hopemd.py . ../analysis $i || python3 -I ../analysis/prime_mmgbsa_to_hopemd.py . ../analysis $i
cd ..
# analysis/summary.json was written before these energies existed: it is rewritten here, under a
# lock, so that the last task of the array leaves a summary with every replicate's energy in it
( flock 9 2>/dev/null || true
  "${{HOPEMD_PYTHON:-python3}}" -m hopemd.analysis . >/dev/null 2>&1 \
    || python3 -m hopemd.analysis . >/dev/null 2>&1 \
    || echo "[hopemd] summarise not available on this node; the page will do it"
) 9>analysis/.summary.lock || true
echo "[hopemd] MM-GBSA replicate $i done (Prime)"
""")

    @staticmethod
    def _prime_convert_py() -> str:
        return r'''"""Prime MM-GBSA's per-frame tables (thermal_mmgbsa.py -j <job>: <job>-prime-out.csv, one row per
frame, the binding energy in r_psp_MMGBSA_dG_Bind) into the two files the run's summary reads:
analysis/r<i>_mmgbsa_frames.csv (frame, dG) and analysis/r<i>_mmgbsa.dat with MMPBSA.py's
"DELTA TOTAL mean std sem" line. Standard library only.
Usage: python3 prime_mmgbsa_to_hopemd.py <replicate folder> <analysis folder> <replicate number>
"""
import csv
import glob
import math
import os
import sys

rdir, adir, rep = sys.argv[1], sys.argv[2], sys.argv[3]
values = []
PATTERNS = ("prod_s*_p*_mmgbsa-prime-out.csv", "prod_s*_p*_mmgbsa-prime_mmgbsa-out.csv")
# the replicate folder as given, else as seen from the analysis folder's parent, else the working folder
# (an older job script runs inside r<i>/ and still passes "r<i>")
for cand in (rdir, os.path.join(adir, os.pardir, os.path.basename(os.path.normpath(rdir))), os.curdir):
    if any(glob.glob(os.path.join(cand, p)) for p in PATTERNS):
        rdir = cand
        break
# Schrodinger 2025-3 names the table <job>-prime-out.csv; the -prime_mmgbsa-out.csv name is read too.
# One table per part, in part order.
parts = {}
for pattern in PATTERNS:
    for path in glob.glob(os.path.join(rdir, pattern)):
        parts.setdefault(os.path.basename(path).split("_mmgbsa-prime")[0], path)
for path in sorted(parts.values(), key=lambda p: (len(p), p)):
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    col = next((c for c in (rows[0].keys() if rows else []) if c.strip() == "r_psp_MMGBSA_dG_Bind"), None)
    if col is None and rows:
        col = next((c for c in rows[0].keys() if "MMGBSA_dG_Bind" in c and "(" not in c), None)
    if col is None:
        print("[hopemd] %s: no dG_Bind column" % path)
        continue
    for row in rows:
        try:
            values.append(float(row[col]))
        except (TypeError, ValueError):
            pass
if not values:
    print("[hopemd] no Prime MM-GBSA energies found in %s" % rdir)
    sys.exit(3)
n = len(values)
mean = sum(values) / n
std = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1)) if n > 1 else 0.0
sem = std / math.sqrt(n) if n > 1 else 0.0
with open(os.path.join(adir, "r%s_mmgbsa_frames.csv" % rep), "w") as fh:
    fh.write("Frame #,DELTA TOTAL\n")
    for k, v in enumerate(values, 1):
        fh.write("%d,%.4f\n" % (k, v))
with open(os.path.join(adir, "r%s_mmgbsa.dat" % rep), "w") as fh:
    fh.write("| Prime MM-GBSA (thermal_mmgbsa.py, Schrodinger): binding energy per frame, VSGB solvation, OPLS\n")
    fh.write("| Number of frames: %d\n\n" % n)
    fh.write("DELTA TOTAL %20.4f %20.4f %20.4f\n" % (mean, std, sem))
print("[hopemd] Prime MM-GBSA: %d frames, %.2f +/- %.2f kcal/mol (SEM %.2f)" % (n, mean, std, sem))
'''

    def _readme(self, spec: Spec, d: dict, nseg: int, mem: dict | None = None) -> str:
        pro, ff = spec.protocol, spec.forcefield
        return f"""HOPE-MD run: {spec.name}
engine: Desmond {spec.site.version('desmond')} ({FORCEFIELD[ff.protein]} / {ff.water}), site: {spec.site.name}, device: {spec.compute.device}
protocol: {pro.length_ns:g} ns x {pro.replicates}, dt 2 fs RESPA (2/2/6), {pro.temperature_K:g} K, {pro.pressure_bar:g} bar, frames every {pro.save_ps:g} ps
derived at submit: ~{d['atoms']} atoms, {d['box']}, {d['steps']} steps, {d['frames']} frames, ~{d['ns_per_day']} ns/day ({d['ns_per_day_note']}), {nseg} segment(s) of {d['segment_ns']:g} ns, walltime {d['walltime']}

spec.json     the run, as submitted. Edit nothing else by hand.
build/        build.msj and build.sh (the System Builder through multisim), complex.cms (the built system),
              system.pdb (the topology cpptraj and the videos read, written by VMD), system.json
md/           relax.msj (Brownian relax, heat, NPT ladder; a bilayer relaxes with Schrodinger's membrane protocol,
              copied into r<i>/relax.msj), prod.msj (one production segment), dtr_to_dcd.tcl (each segment's frames
              as DCD, k save intervals after its start, once each)
r1/ r2/ ...   one folder per replicate: eq4.cms, then per segment prod_s<k>_p1-out.cms, prod_s<k>_p1_trj/ (Desmond's
              trajectory), prod_s<k>_p1.dcd (the same, for cpptraj), prod_s<k>.cms, prod_s<k>.done, .nsday
analysis/     cpptraj outputs (r<i>_*.dat), r<i>_view.pdb for the viewer, r<i>_mmgbsa.dat from Prime MM-GBSA
jobs/         sbatch scripts, graph.json; ids.json holds the ids the page submitted (jobs/submit.sh, run by hand, writes ids.txt)
logs/         Slurm output
archive/      tidy_<stamp>.tar.gz once the tidy job has packed the small files (Undo tidy on the Runs screen unpacks them)
"""
