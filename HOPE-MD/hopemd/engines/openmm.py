"""OpenMM: the same tleap build as Amber, then one Python script runs the ladder.

Sharing the build is the point: complex.prmtop/inpcrd from tleap are what OpenMM's
AmberPrmtopFile loads, so MMPBSA.py rescoring works on an OpenMM trajectory with
no conversion. The run script writes DCD parts and a checkpoint every few minutes;
a resumed segment continues from the checkpoint into a new part, like the Amber
job does, and the frames the stopped part wrote past the checkpoint are cut from
it first. Production counts its own steps, so frame k is k save intervals in.

A membrane run shares the build the same way: hopemd.membrane writes the prmtop through
ParmEd, and the runner swaps the barostat for MonteCarloMembraneBarostat (x and y together,
z free, zero surface tension) and restrains the solute by residue range so the lipids stay free.
"""
from __future__ import annotations

from ..spec import Spec, has_nucleic, HOLD_ATOMS
from .amber import AmberEngine, SOLVENT_MASK, _resnum
from .base import Render, Job, membrane_of


class OpenMMEngine(AmberEngine):
    key = "openmm"
    label = "OpenMM"

    def render(self, spec: Spec, derived: dict | None = None) -> Render:
        r = super().render(spec, derived)
        r.engine = self.key
        d = r.derived
        pro = spec.protocol
        # keep: build, param, analysis, mmgbsa; replace: equil and prod jobs, md/ inputs
        r.files = [f for f in r.files if not f.path.startswith("md/") and not f.path.startswith("jobs/2")
                   and not f.path.startswith("jobs/30_") and f.path != "jobs/submit.sh"]
        seg_steps = int(round(d["segment_ns"] * 1e6 / pro.timestep_fs))
        save_steps = int(round(pro.save_ps * 1000 / pro.timestep_fs))
        mem = membrane_of(spec)
        r.add("md/run_openmm.py", self._run_py(spec, seg_steps, save_steps, mem))
        # cpptraj reads DCD; swap the trajectory glob in the analysis job scripts
        for f in r.files:
            if f.path in ("jobs/40_analysis.sbatch", "jobs/50_mmgbsa.sbatch"):
                f.text = f.text.replace("prod_s*_p*.nc", "prod_s*_p*.dcd")
        # module lines: preamble was rendered with the amber block; both engines are needed
        # (tleap/cpptraj/MMPBSA from Amber, python from OpenMM). Build/analysis keep amber;
        # equil/prod get the openmm block.
        nseg = int(d["segments"])
        jobs = [j for j in r.jobs if j.key in ("param", "build", "analysis", "mmgbsa", "tidy")]
        r.add("jobs/20_equil.sbatch", self._omm_sbatch(spec, "equil", "equil", None, 1.5, None), executable=True)
        jobs.insert(2 if any(j.key == "param" for j in jobs) else 1,
                    Job("equil", "jobs/20_equil.sbatch", depends=[("build", "afterok")],
                        array=pro.replicates, gpu=True, description="OpenMM: minimize, heat, NPT ladder"))
        insert_at = [j.key for j in jobs].index("equil") + 1
        for k in range(1, nseg + 1):
            r.add(f"jobs/30_prod_s{k}.sbatch",
                  self._omm_sbatch(spec, f"prod_s{k}", "prod", k, None, d["walltime"]), executable=True)
            dep = ("equil", "aftercorr") if k == 1 else (f"prod_s{k-1}", "aftercorr")
            jobs.insert(insert_at, Job(f"prod_s{k}", f"jobs/30_prod_s{k}.sbatch", depends=[dep],
                                       array=pro.replicates, gpu=True,
                                       description=f"OpenMM production segment {k} of {nseg}"))
            insert_at += 1
        for j in jobs:
            if j.key in ("analysis", "mmgbsa"):
                j.depends = [(f"prod_s{nseg}", "afterok")]
        r.jobs = jobs
        r.add("jobs/submit.sh", self._submit_sh(spec, jobs), executable=True)
        if mem:
            r.notes.append("OpenMM runs from the prmtop the membrane build writes (Modeller + ParmEd), so the "
                           "build and the MM-GBSA jobs are Amber's scripts; only equilibration and production run "
                           "in OpenMM, with MonteCarloMembraneBarostat (xy isotropic, z free, zero surface tension).")
        else:
            r.notes.append("OpenMM runs from the tleap topology, so the build and the MM-GBSA jobs are "
                           "Amber's; only equilibration and production run in OpenMM.")
        return r

    def _omm_sbatch(self, spec: Spec, stage: str, mode: str, seg: int | None, hours, walltime) -> str:
        pro = spec.protocol
        head = self.sbatch_header(spec, stage, gpu=True, hours=hours, walltime=walltime,
                                  array=pro.replicates, throttle=spec.compute.max_concurrent)
        pre = self.preamble(spec, "openmm")
        segarg = f" --segment {seg}" if seg else ""
        return head + pre + f"""
R=r${{SLURM_ARRAY_TASK_ID}}
mkdir -p "$R"
python3 md/run_openmm.py --replicate "$SLURM_ARRAY_TASK_ID" --mode {mode}{segarg}
"""

    def _run_py(self, spec: Spec, seg_steps: int, save_steps: int, mem: dict | None = None) -> str:
        # the ladder runs at 2 fs: heat_ps and equil_ps are picoseconds, so steps = ps / 0.002, not ps / 2
        pro, ff = spec.protocol, spec.forcefield
        hmass = 4.0 if spec.hmr else 1.008
        # held residues as tleap numbered them (the topology's index + 1), through the importer's map, as
        # Amber, NAMD and GROMACS hold them: a residue key's own number is the structure's, and a chain
        # numbered from 57 would put the hold 56 residues along
        restrain = sorted({_resnum(k, spec.system) for k in pro.restrain})
        hold = HOLD_ATOMS if has_nucleic(spec.system) else HOLD_ATOMS[:3]      # a nucleotide is held by its P and C4'
        if mem:
            # a bilayer: x and y scale together, z on its own, at zero surface tension (the
            # Amber stages say ntp=3, csurften=3, gamma_ten=0 for the same thing). The solute
            # is the first N_SOLUTE residues of the membrane build's topology (receptor, then
            # partner; lipids, water and ions follow), so the restraint selects by residue index
            # rather than by residue name and the lipids stay free.
            sysm = spec.system
            rec = sysm.receptor_residues or [1, 1]
            lig = sysm.ligand_residues or [rec[1] + 1, rec[1] + 1]
            n_solute = rec[1] if sysm.ligand_class == "none" else lig[1]
            barostat = ("mm.MonteCarloMembraneBarostat(PRES, 0.0 * u.bar * u.nanometer, TEMP, "
                        "mm.MonteCarloMembraneBarostat.XYIsotropic, mm.MonteCarloMembraneBarostat.ZFree, 25)")
            extra = f"N_SOLUTE = {n_solute}   # residues of the solute; lipids, water and ions come after them\n"
            solute_test = "a.residue.index < N_SOLUTE and a.element.symbol != \"H\""
        else:
            barostat = "mm.MonteCarloBarostat(PRES, TEMP, 25)"
            extra = ""
            solute_test = "a.residue.name not in (\"WAT\", \"HOH\", \"Na+\", \"Cl-\", \"K+\", \"MG\") and a.element.symbol != \"H\""
        return f'''#!/usr/bin/env python3
"""HOPE-MD OpenMM runner for {spec.name}. Same ladder as the Amber renderer:
minimize (restrained) -> heat NVT -> NPT ladder 4 stages -> production in segments.
Run from the run folder: python3 md/run_openmm.py --replicate 1 --mode equil
                          python3 md/run_openmm.py --replicate 1 --mode prod --segment 1
"""
import argparse, json, os, random, sys, time
from pathlib import Path
import openmm as mm
import openmm.app as app
from openmm import unit as u

TEMP = {pro.temperature_K:g} * u.kelvin
PRES = {pro.pressure_bar:g} * u.bar
DT_EQ = 0.002 * u.picoseconds
DT_PROD = {pro.timestep_fs / 1000:g} * u.picoseconds
HMASS = {hmass} * u.amu
SEG_STEPS = {seg_steps}
SAVE_STEPS = {save_steps}
CHECK_STEPS = max(5000, SAVE_STEPS, (SEG_STEPS // 40) // 1000 * 1000)   # never under a frame: a part cut at its checkpoint keeps one
HEAT_STEPS = {int(round(pro.heat_ps / 0.002))}
EQ_STEPS = {int(round(pro.equil_ps / 4 / 0.002))}
LADDER = [{pro.restraint_kcal:g}, 1.0, 0.1, 0.0]
RESTRAIN_PROD = {restrain!r}
HOLD_ATOMS = {hold!r}
CUTOFF = 1.0 * u.nanometer
{extra}
def log(*a):
    print("[hopemd]", time.strftime("%H:%M:%S"), *a, flush=True)

def make_system(prmtop, hmr):
    system = prmtop.createSystem(nonbondedMethod=app.PME, nonbondedCutoff=CUTOFF,
                                 constraints=app.HBonds, rigidWater=True,
                                 hydrogenMass=(HMASS if hmr else None))
    # the long-range LJ correction to energy and pressure, as pmemd (vdwmeth = 1), GROMACS (DispCorr = EnerPres)
    # and NAMD (LJcorrection) apply it: NonbondedForce's default, set here so the four engines stay alike
    for f in system.getForces():
        if isinstance(f, mm.NonbondedForce):
            f.setUseDispersionCorrection(True)
    return system

def add_restraint(system, positions, atoms, k_kcal, selector):
    """Harmonic positional restraint on selected atoms, k in kcal/mol/A^2, in Amber's form E = k d^2
    (pmemd's restraint_wt; NAMD's harmonic constraints have the same form), not 0.5 k d^2, so the same
    number holds the solute as strongly here as in the Amber and NAMD ladders."""
    if k_kcal <= 0:
        return None
    k = k_kcal * u.kilocalories_per_mole / u.angstrom**2
    f = mm.CustomExternalForce("k*periodicdistance(x, y, z, x0, y0, z0)^2")
    f.addGlobalParameter("k", k)
    for p in ("x0", "y0", "z0"):
        f.addPerParticleParameter(p)
    for a in atoms:
        if selector(a):
            f.addParticle(a.index, positions[a.index])
    system.addForce(f)
    return f

def solute_heavy(a):
    return {solute_test}

def platform():
    for name in ("CUDA", "OpenCL", "CPU"):
        try:
            p = mm.Platform.getPlatformByName(name)
            props = {{"Precision": "mixed"}} if name in ("CUDA", "OpenCL") else {{}}
            return p, props
        except Exception:
            continue
    raise SystemExit("no OpenMM platform")

def simulation(prmtop, system, dt, seed):
    integ = mm.LangevinMiddleIntegrator(TEMP, 1.0 / u.picosecond, dt)
    if seed >= 0:
        integ.setRandomNumberSeed(seed)
    p, props = platform()
    sim = app.Simulation(prmtop.topology, system, integ, p, props)
    return sim

def save_checkpoint(sim, path):
    """Written whole or not at all: a job stopped while writing it leaves the checkpoint before."""
    tmp = path.with_name(path.name + ".tmp")
    sim.saveCheckpoint(str(tmp))
    os.replace(tmp, path)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replicate", type=int, required=True)
    ap.add_argument("--mode", choices=["equil", "prod"], required=True)
    ap.add_argument("--segment", type=int, default=1)
    a = ap.parse_args()
    rdir = Path(f"r{{a.replicate}}"); rdir.mkdir(exist_ok=True)
    prmtop = app.AmberPrmtopFile("build/complex_hmr.prmtop" if {spec.hmr} else "build/complex.prmtop")
    inpcrd = app.AmberInpcrdFile("build/complex.inpcrd")
    seed = ({pro.seed} + 1000 * a.replicate + 100 * a.segment) if {pro.seed} >= 0 else random.SystemRandom().randrange(1, 2 ** 31)   # -1: one per replicate; fixed: reproducible, offset per replicate and segment
    atoms = list(prmtop.topology.atoms())

    if a.mode == "equil":
        # ---- minimize with heavy-atom restraints
        system = make_system(prmtop, {spec.hmr})
        add_restraint(system, inpcrd.positions, atoms, 10.0, solute_heavy)
        sim = simulation(prmtop, system, DT_EQ, seed)
        sim.context.setPositions(inpcrd.positions)
        if inpcrd.boxVectors is not None:
            sim.context.setPeriodicBoxVectors(*inpcrd.boxVectors)
        log("minimize"); sim.minimizeEnergy(maxIterations={pro.minimise_steps})
        pos = sim.context.getState(getPositions=True).getPositions()
        # ---- heat under NVT with restraints, ramping the thermostat
        system = make_system(prmtop, {spec.hmr})
        add_restraint(system, pos, atoms, LADDER[0], solute_heavy)
        sim = simulation(prmtop, system, DT_EQ, seed)
        sim.context.setPositions(pos); sim.context.setPeriodicBoxVectors(*inpcrd.boxVectors)
        sim.context.setVelocitiesToTemperature(5 * u.kelvin, seed)
        log("heat")
        nramp = 20
        for i in range(1, nramp + 1):
            sim.integrator.setTemperature(TEMP * i / nramp)
            sim.step(HEAT_STEPS // nramp)
        pos = sim.context.getState(getPositions=True).getPositions()
        vel = sim.context.getState(getVelocities=True).getVelocities()
        # ---- NPT ladder
        for stage, kres in enumerate(LADDER, start=1):
            system = make_system(prmtop, {spec.hmr})
            system.addForce({barostat})
            add_restraint(system, pos, atoms, kres, solute_heavy)
            sim = simulation(prmtop, system, DT_EQ, seed + stage)
            sim.context.setPositions(pos); sim.context.setPeriodicBoxVectors(*inpcrd.boxVectors)
            sim.context.setVelocities(vel)
            log(f"eq{{stage}} restraint {{kres}} kcal/mol/A^2"); sim.step(EQ_STEPS)
            st = sim.context.getState(getPositions=True, getVelocities=True)
            pos, vel = st.getPositions(), st.getVelocities()
            inpcrd.boxVectors = st.getPeriodicBoxVectors()
        sim.saveState(str(rdir / "eq4.xml"))
        log("equilibrated")
        return

    # ---- production segment, resumable
    seg = a.segment
    system = make_system(prmtop, {spec.hmr})
    system.addForce({barostat})
    if RESTRAIN_PROD:
        keep = set(RESTRAIN_PROD)                 # tleap's sequential residue numbers
        add_restraint(system, inpcrd.positions, atoms, {pro.restraint_kcal:g},
                      lambda at: at.residue.index + 1 in keep and at.name in HOLD_ATOMS)
    sim = simulation(prmtop, system, DT_PROD, seed + 100 + seg)
    prev = rdir / ("eq4.xml" if seg == 1 else f"prod_s{{seg-1}}.xml")
    progress = rdir / f"prod_s{{seg}}.progress.json"
    first = (seg - 1) * SEG_STEPS          # the production's own step count where this segment starts
    done, part = 0, 1
    if progress.exists():
        # the checkpoint carries its step count, which says how far the segment got (the progress file is
        # written after it). The frames the stopped part wrote past it are simulated again by this part, so
        # they are cut from its DCD first and every frame is read once (hopemd is on PYTHONPATH in the job).
        # Its frames sit on the multiples of SAVE_STEPS after the step it started from (the DCD's header
        # gives the interval, not that step), so the ones up to the checkpoint are a count.
        from hopemd.analysis.parts import keep_frames
        j = json.loads(progress.read_text())
        sim.loadCheckpoint(str(rdir / f"prod_s{{seg}}_p{{j['part']}}.chk"))
        done, part = sim.currentStep - first, j["part"] + 1
        stopped = rdir / f"prod_s{{seg}}_p{{j['part']}}.dcd"
        if stopped.exists():
            kept, dropped = keep_frames(stopped, sim.currentStep // SAVE_STEPS - j["start"] // SAVE_STEPS)
            if dropped:
                log(f"{{stopped.name}}: {{dropped}} frame(s) past the checkpoint cut, {{kept}} kept")
        log(f"resume segment {{seg}} at {{done}} steps, part {{part}}")
    else:
        sim.loadState(str(prev))
        # the state carries the step count of the run that saved it (eq4's is the ladder's): the production
        # counts its own, so its frames sit on whole save intervals from its start, as on the other engines
        sim.currentStep = first
    start = sim.currentStep                # where this part starts: the progress file records it for a resume
    left = SEG_STEPS - done
    if left <= 0:
        sim.saveState(str(rdir / f"prod_s{{seg}}.xml")); log("segment complete"); return
    dcd = rdir / f"prod_s{{seg}}_p{{part}}.dcd"
    sim.reporters.append(app.DCDReporter(str(dcd), SAVE_STEPS, enforcePeriodicBox=True))
    sim.reporters.append(app.StateDataReporter(str(rdir / f"prod_s{{seg}}_p{{part}}.log"), SAVE_STEPS,
        step=True, time=True, temperature=True, volume=True, speed=True, separator="\\t"))
    chk = rdir / f"prod_s{{seg}}_p{{part}}.chk"
    t0, ran = time.time(), 0
    while left > 0:
        n = min(CHECK_STEPS, left)
        sim.step(n); left -= n; done += n; ran += n
        save_checkpoint(sim, chk)
        progress.write_text(json.dumps({{"done": done, "part": part, "start": start}}))
    sim.saveState(str(rdir / f"prod_s{{seg}}.xml"))
    hours = (time.time() - t0) / 3600
    if hours > 0:                          # this part's own steps over its own hours
        (rdir / f"prod_s{{seg}}_p{{part}}.nsday").write_text(f"{{ran * DT_PROD.value_in_unit(u.nanoseconds) / hours * 24:.1f}}\\n")
    log(f"segment {{seg}} done")

if __name__ == "__main__":
    main()
'''
