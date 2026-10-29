"""What every engine produces, and the sbatch boilerplate they share."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..spec import Spec, derive, mmgbsa_refused, split_links


@dataclass
class RenderedFile:
    path: str          # relative to the run folder
    text: str
    executable: bool = False


@dataclass
class Job:
    key: str                              # build, param, equil, prod_s1, analysis, mmgbsa ...
    script: str                           # relative path of the sbatch file
    depends: list[tuple[str, str]] = field(default_factory=list)   # (job key, afterok|aftercorr|afterany)
    array: int | None = None              # array size (replicates) or None
    gpu: bool = False
    description: str = ""


@dataclass
class Render:
    engine: str
    files: list[RenderedFile] = field(default_factory=list)
    jobs: list[Job] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)      # things the person should read before Submit
    derived: dict = field(default_factory=dict)

    def add(self, path: str, text: str, executable: bool = False) -> None:
        self.files.append(RenderedFile(path, text, executable))

    def file(self, path: str) -> str:
        for f in self.files:
            if f.path == path:
                return f.text
        raise KeyError(path)

    @property
    def paths(self) -> list[str]:
        return [f.path for f in self.files]


def hopemd_home() -> str:
    """The checkout that rendered the run: what the job scripts put on PYTHONPATH when
    HOPEMD_HOME is not set, so the analysis step and the membrane build can import hopemd."""
    return str(Path(__file__).resolve().parent.parent.parent)


def cache_lines(fallback: str = "$RUNDIR") -> str:
    """Nothing in $HOME, for every job a run renders: the XDG cache (Mesa's shader cache among
    what follows it), matplotlib's and the CUDA driver's JIT cache go to the user's scratch, or
    to `fallback` (the run folder) when $SCRATCH is not set. HOPEMD_CACHE overrides both."""
    return ("export HOPEMD_CACHE=\"${HOPEMD_CACHE:-${SCRATCH:-" + fallback + "}/.cache/hopemd}\"\n"
            "export XDG_CACHE_HOME=\"$HOPEMD_CACHE\" MPLCONFIGDIR=\"$HOPEMD_CACHE/matplotlib\" "
            "CUDA_CACHE_PATH=\"$HOPEMD_CACHE/nv\"\n")


def membrane_of(spec: Spec) -> dict | None:
    """The bilayer settings when system.environment is "membrane", else None:
    {lipid, padding_A, center_z_A}. Read by name so a spec.json saved before the field
    existed, or one handed in with a plain dict, renders as a water box."""
    sysm = spec.system
    if getattr(sysm, "environment", "water") != "membrane":
        return None
    m = getattr(sysm, "membrane", None)
    if isinstance(m, dict):
        get = m.get
    else:
        get = lambda k, d: getattr(m, k, d)   # noqa: E731
    return {"lipid": str(get("lipid", "POPC")), "padding_A": float(get("padding_A", 10.0)),
            "center_z_A": float(get("center_z_A", 0.0))}


COMPLEX_KINDS = ("complex", "rcsb", "alphafold")


def _hours(hours: float | None, walltime: str | None) -> float | None:
    """The walltime a job asks for, in hours, from whichever of the two forms was given.
    "2-00:00:00" and "08:00:00" are both Slurm's."""
    if hours is not None:
        return hours
    if not walltime:
        return None
    days, _, rest = walltime.partition("-")
    if not rest:
        days, rest = "0", walltime
    try:
        parts = [float(x) for x in rest.split(":")]
    except ValueError:
        return None
    while len(parts) < 3:
        parts.append(0.0)
    return float(days) * 24 + parts[0] + parts[1] / 60 + parts[2] / 3600


def carried_ions(spec: Spec) -> list[str]:
    """The ions in input/ions.pdb ("ZN A:301"): those Clean kept that Prepare carried past protonation, as
    its report records them and the spec carries once Prepare has run (none before then), or those an
    import of files kept (system.ions_kept). The builds add them after the partner: the partner's residue
    range takes those that bind it (partner_ions), and the rest lie outside every range a mask is built from."""
    sysm = spec.system
    if not getattr(sysm, "prepared", False):
        if sysm.source.kind in COMPLEX_KINDS:
            return []
        return [c["group"] for c in getattr(sysm, "ions_kept", None) or [] if isinstance(c, dict) and c.get("group")]
    step = ((getattr(sysm, "prep_report", None) or {}).get("steps") or {}).get("protonate") or {}
    return [c["group"] for c in step.get("carried") or [] if isinstance(c, dict) and c.get("group")]


#: how many waters the viewer's trajectory keeps around the metal sites, re-chosen every frame.
#: Enough to show a first and second shell without making the file one the browser cannot hold.
VIEW_WATERS = 60


def metal_mask(spec: Spec) -> str:
    """The metals the build carries, as a cpptraj residue-name mask (":ZN,FE"), or "" for a run
    with none. The names are the groups carried_ions records ("ZN A:301"); a mask by name holds
    wherever the build put them, which a residue range does not."""
    names = sorted({str(c).split()[0] for c in carried_ions(spec) if str(c).split()})
    return ":" + ",".join(names) if names else ""


def partner_ions(spec: Spec) -> int:
    """How many of the carried ions bind the partner: they come first in input/ions.pdb, and the
    partner's residue range (system.ligand_residues) takes them."""
    sysm = spec.system
    if not carried_ions(spec):
        return 0
    if not getattr(sysm, "prepared", False):
        return sum(1 for c in sysm.ions_kept or [] if isinstance(c, dict) and c.get("side") == "partner")
    return int(((getattr(sysm, "prep_report", None) or {}).get("split") or {}).get("partner_ions") or 0)


def crystal_waters(spec: Spec) -> list[str]:
    """The crystal waters in input/waters.pdb ("HOH A:301"), which every build adds after the solute and its
    ions: those an import of files kept, or those Clean kept once Prepare has run (none before then)."""
    sysm = spec.system
    if sysm.source.kind in COMPLEX_KINDS and not getattr(sysm, "prepared", False):
        return []
    return [str(w) for w in getattr(sysm, "waters_kept", None) or []]


def kept_last(spec: Spec) -> int:
    """The last residue, in the topology's numbering, of what the analysis keeps: the solute (the receptor, then
    the partner, whose range takes the ions that bind it) and the other ions the build carries after it. What
    follows is solvent (the crystal waters, the salt, the water box; a bilayer's lipids) and is stripped by its
    place, so a carried ion stays, whatever its name, and the salt goes."""
    sysm = spec.system
    rec = sysm.receptor_residues or [1, 1]
    last = (sysm.ligand_residues or rec)[1] if sysm.ligand_class != "none" else rec[1]
    return last + len(carried_ions(spec)) - partner_ions(spec)


class Engine:
    #: the production trajectory's file suffix: r<i>/prod_s<k>_p<n>.<TRAJ> (cpptraj reads all of them)
    TRAJ: str = "nc"
    #: the topology cpptraj, the videos and the snapshots read the trajectory with, and its VMD type
    TOPOLOGY: str = "build/complex.prmtop"
    TOPOLOGY_TYPE: str = "parm7"
    #: the topology numbers its residues 1, 2, 3 ... in the build's order, as tleap does, so the viewer and the
    #: videos select by the spec's ranges (system.receptor_residues, ligand_residues); one that keeps the input's
    #: chain letters and residue numbers (Desmond's) is selected by those instead (own_ranges)
    OWN_NUMBERING: bool = False
    #: what a force-field choice needs beyond the engine's module, said beside it on the page, before Submit and
    #: after a batch: {force field: (the words its label carries, the note)}
    FORCEFIELD_NOTES: dict[str, tuple[str, str]] = {}
    #: what the solute-only views drop (water and ions as the engine names them); a bilayer run overrides it by residue range
    STRIP: str = ":WAT,HOH,TIP3,Na+,Cl-,K+,NA,CL,K,MG"
    #: MM-GBSA needs an Amber prmtop: engines that run one keep it; Desmond has none
    MMGBSA: bool = True
    #: which binding-energy code pairs with the engine, said on the page and in the report
    MMGBSA_TOOL: str = "MMPBSA.py (AmberTools 24), GB-Neck2 on the run's own Amber topology"

    @classmethod
    def part_progress(cls, part: "Path") -> tuple[int, float | None] | None:
        """(completed steps, ns/day or None) for one production part file, from whatever the
        engine writes beside it while it runs; None when the engine has nothing to say and the
        generic files (Amber's .mdinfo, OpenMM's .progress.json) are to be read instead."""
        return None

    key = "base"
    label = "base"

    def render(self, spec: Spec, derived: dict | None = None) -> Render:
        raise NotImplementedError

    # ------------------------------------------------------------- shared
    @staticmethod
    def sbatch_header(spec: Spec, stage: str, *, gpu: bool, hours: float | None = None,
                      walltime: str | None = None, cpus: int | None = None, mem_gb: int | None = None,
                      array: int | None = None, throttle: int = 0, partition: str | None = None) -> str:
        """The #SBATCH block. Every site rule lives here: partition, gres, the --mem that
        Grace insists on, array throttling, log paths. `partition` overrides the choice for a
        small CPU job that belongs on the short queue (the tidy job)."""
        site = spec.site
        c = spec.compute
        # `gpu` marks the engine's own stages (equilibrate, produce). With device=cpu those
        # become one whole CPU node each; everything else (build, analysis) is a small CPU job.
        whole_node = gpu and spec.on_cpu
        if whole_node:
            part = c.partition or site.cpu_partition
        elif gpu:
            part = spec.gpu_partition           # the partition that carries the GPU type the run got
        else:
            part = site.cpu_partition
        if partition and not gpu:
            part = partition
        # the queue to name, if any: on a site whose scheduler routes on --time a CPU job names
        # none, and a queue too short for the walltime asked for is widened rather than deadlocked
        part = site.partition_to_name(part, _hours(hours, walltime), gpu=gpu, pinned=bool(c.partition))
        lines = [
            "#!/bin/bash",
            f"#SBATCH --job-name={spec.name}_{stage}",
        ]
        if part:
            lines.append(f"#SBATCH --partition={part}")
        # The allocation this run is charged to, when one was named. Every job of the run carries
        # it: a batch of runs charged to one allocation and its analysis charged to another is
        # an accounting answer nobody meant to give.
        if c.account:
            lines.append(f"#SBATCH --account={c.account}")
        if gpu and not whole_node:
            lines.append(f"#SBATCH --gres={site.gres(spec.gpu, c.gpus_per_replicate, part or spec.gpu_partition)}")
        if whole_node:
            lines += [
                "#SBATCH --nodes=1",
                f"#SBATCH --ntasks={site.cpu_cores_per_node}",
                "#SBATCH --cpus-per-task=1",
                f"#SBATCH --mem={site.cpu_mem_gb}G",
            ]
        else:
            lines += [
                "#SBATCH --nodes=1",
                "#SBATCH --ntasks=1",
                f"#SBATCH --cpus-per-task={cpus or c.cpus}",
                f"#SBATCH --mem={mem_gb or c.mem_gb}G",          # required on Grace, harmless elsewhere
            ]
        if walltime:
            lines.append(f"#SBATCH --time={walltime}")
        elif hours is not None:
            from ..spec import format_walltime
            lines.append(f"#SBATCH --time={format_walltime(hours)}")
        if array:
            lines.append(f"#SBATCH --array=1-{array}" + (f"%{throttle}" if throttle else ""))
            lines.append("#SBATCH --output=logs/%x.%A_%a.out")
        else:
            lines.append("#SBATCH --output=logs/%x.%j.out")
        return "\n".join(lines) + "\n"

    #: What asks the engine that has just been loaded for its own version, per engine key. The
    #: report cites Amber 24 and NAMD 3 from a table of references, and only the module line says
    #: what was loaded; this records what the program says of itself, so a reference can be
    #: checked against what ran. Each is run with its errors thrown away and its failure ignored:
    #: a version nobody can read must never stop a job.
    VERSION_COMMANDS = {
        "amber": "pmemd.cuda --version || pmemd --version || echo \"${AMBERHOME:-}\"",
        "gromacs": "gmx --version | sed -n 's/^GROMACS version: *//p'",
        "namd": "namd3 --version 2>&1 | head -2 | tr '\\n' ' '",
        "openmm": "\"${HOPEMD_PYTHON:-python3}\" -c 'import openmm; print(\"OpenMM\", openmm.version.version)'",
        "desmond": "cat \"${SCHRODINGER:-}/version\" | tr '\\n' ' '",
    }

    @classmethod
    def version_line(cls, engine_key: str | None) -> str:
        """The shell that writes one line of jobs/software.txt: the engine key, the job's name and
        what the engine reported. Appended under a lock, because an array's tasks run together."""
        cmd = cls.VERSION_COMMANDS.get(engine_key or "")
        if not cmd:
            return ""
        return (
            "# what the engine says its own version is, for the report's references (jobs/software.txt)\n"
            "{ _v=$( { " + cmd + "; } 2>/dev/null | head -1 | tr -d '\\t' ) || _v=\"\"; } || _v=\"\"\n"
            "if [ -n \"${_v:-}\" ]; then mkdir -p \"$RUNDIR/jobs\" && { flock 9 2>/dev/null || true; "
            "printf '%s\\t%s\\t%s\\n' \"" + (engine_key or "") + "\" \"${SLURM_JOB_NAME:-}\" \"$_v\" "
            ">> \"$RUNDIR/jobs/software.txt\"; } 9>\"$RUNDIR/jobs/.software.lock\" || true; fi\n")

    @staticmethod
    def preamble(spec: Spec, engine_key: str | None, workdir: str | None = None) -> str:
        """cd into the run folder (and into the job's own folder, workdir, when it has one) before any
        module is loaded, load the engine (none for a job that needs no module), keep every cache out of
        $HOME, fail loudly. A Schrodinger job writes where it starts, so its folder comes first."""
        site = spec.site
        modules = site.module_block(engine_key) if engine_key else "module purge 2>/dev/null || true"
        # the analysis job runs hopemd.analysis.summarise with the engine module's Python,
        # which knows nothing of this checkout; the path of the code that rendered the run
        # is recorded so the import works, and HOPEMD_HOME overrides it
        home = hopemd_home()
        python = sys.executable   # the interpreter that rendered the run: it has pdb2pqr when the server's environment does
        return (
            "set -euo pipefail\n"
            "RUNDIR=${HOPEMD_RUNDIR:-$SLURM_SUBMIT_DIR}\n"
            "cd \"$RUNDIR\"\n"
            "mkdir -p logs\n"
            + (f"mkdir -p \"{workdir}\"; cd \"{workdir}\"   # the job's own folder, before the modules\n" if workdir else "")
            + "type module >/dev/null 2>&1 || source /etc/profile   # Lmod in a batch shell\n"
            f"{modules}\n"
            + cache_lines()
            + f"export PYTHONPATH=\"${{HOPEMD_HOME:-{home}}}:${{PYTHONPATH:-}}\"   # hopemd, for the analysis step\n"
            f"export HOPEMD_PYTHON=\"${{HOPEMD_PYTHON:-{python}}}\"   # the server's interpreter, for pdb2pqr in the build\n"
            # what ran where: the timing table reads this line (hopemd/timing.py)
            "echo \"[hopemd] $(date) host=$(hostname) job=${SLURM_JOB_ID:-none} task=${SLURM_ARRAY_TASK_ID:-0}"
            " cpu=\\\"$(lscpu 2>/dev/null | sed -n 's/^Model name: *//p' | head -1)\\\""
            # only what a working nvidia-smi says: ACES's CPU nodes carry nvidia-smi but no driver,
            # and it prints "NVIDIA-SMI has failed ..." on stdout (exit 9)
            " gpu=\\\"$(g=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null) && printf '%s\\n' \"$g\" | paste -sd, -)\\\"\"\n"
            + Engine.version_line(engine_key)
        )

    #: what the tidy stage says of itself on the page and in the graph
    TIDY_DESCRIPTION = "pack the small files the finished run no longer needs into one archive (hopemd.tidy; Undo unpacks)"

    def tidy_sbatch(self, spec: Spec) -> str:
        """The last job of the graph: hopemd.tidy over the run folder, on the short CPU queue,
        no engine module. HOPEMD_TIDY_ARGS carries --undo when the page asks for the files back."""
        return (self.sbatch_header(spec, "tidy", gpu=False, hours=0.5, cpus=1, mem_gb=4, partition=spec.site.prep_partition)
                + self.preamble(spec, None) + """
# the small files a finished run no longer needs (stage logs, intermediate restarts, build scratch) go into
# archive/tidy_<stamp>.tar.gz and are removed: tens of inodes instead of hundreds. --undo brings them back.
"${HOPEMD_PYTHON:-python3}" -m hopemd.tidy ${HOPEMD_TIDY_ARGS:-} . || "python3" -m hopemd.tidy ${HOPEMD_TIDY_ARGS:-} .
echo "[hopemd] tidy done"
""")

    @staticmethod
    def tidy_depends(keys: list[str], terminal: list[str]) -> list[tuple[str, str]]:
        """What the tidy job waits for, given the graph's job keys in order and those nothing else
        waits for. The production must have succeeded: a failed segment resumes from the per-part
        restarts and checkpoints tidy packs, so the last segment is an afterok of its own. So must the
        analysis: the run's results come from it, its cpptraj logs are what to read when it fails, and
        its rerun queues tidy again. MM-GBSA need only have ended (afterany): it fails on its own, most
        often on Prime's license tokens, and its rerun from the Runs screen works on a tidied run, so a
        failed MM-GBSA no longer leaves a finished run untidied."""
        segments = [k for k in keys if k.startswith("prod_s")]
        deps = [(k, "afterany" if k == "mmgbsa" else "afterok") for k in terminal]
        if segments and segments[-1] not in terminal:
            deps.insert(0, (segments[-1], "afterok"))
        return deps

    def split_link_note(self, spec: Spec) -> str:
        """How this engine's build bonds the disulfides across the split, for the note before Submit: one sentence."""
        return "The build bonds them"

    #: the first lines of an MM-GBSA job: a run whose receptor and partner are covalently linked has the reason in
    #: analysis/mmgbsa_refused.txt (add_split_links), which a job queued before Prepare found the link reads and stops on
    MMGBSA_GUARD = ("# a disulfide across the split makes receptor and partner one molecule: no MM-GBSA (hopemd.spec.mmgbsa_refused)\n"
                    "if [ -s \"$RUNDIR/analysis/mmgbsa_refused.txt\" ]; then\n"
                    "  echo \"[hopemd] $(cat \"$RUNDIR/analysis/mmgbsa_refused.txt\")\"; exit 0\n"
                    "fi\n")

    def add_split_links(self, spec: Spec, r: "Render") -> None:
        """A run whose receptor and partner a disulfide joins (System.split_disulfides): a note on how the build bonds it,
        and MM-GBSA's refusal, which the render has already kept out of the graph, in analysis/mmgbsa_refused.txt, where
        a job queued before a Prepare re-render found the link reads it and stops (MMGBSA_GUARD)."""
        links = split_links(spec.system)
        if not links:
            return
        r.notes.append(f"Covalently linked: the disulfide{'s' if len(links) > 1 else ''} {', '.join(links)} join"
                       f"{'' if len(links) > 1 else 's'} the receptor to the partner, so the run is one molecule. "
                       f"{self.split_link_note(spec)}.")
        why = mmgbsa_refused(spec.system)
        r.add("analysis/mmgbsa_refused.txt", why + "\n")
        if spec.analysis.get("mmgbsa", True) and spec.system.ligand_class != "none":
            r.notes.append(why)

    def add_tidy(self, spec: Spec, r: "Render", jobs: list["Job"]) -> None:
        """Append the tidy job after every job nothing else waits for (analysis, MM-GBSA), when
        the run asks for it (compute.tidy, on by default)."""
        if not spec.compute.tidy or not jobs:
            return
        waited = {d[0] for j in jobs for d in j.depends}
        terminal = [j.key for j in jobs if j.key not in waited]
        r.add("jobs/90_tidy.sbatch", self.tidy_sbatch(spec), executable=True)
        jobs.append(Job("tidy", "jobs/90_tidy.sbatch", depends=self.tidy_depends([j.key for j in jobs], terminal),
                        description=self.TIDY_DESCRIPTION))

    @staticmethod
    def compute_derived(spec: Spec, derived: dict | None) -> dict:
        return derived if derived is not None else derive(spec)


def own_ranges(rundir: Path, spec: Spec, ranges: list) -> list | None:
    """Residue ranges in tleap's numbering ([first, last], counted 1, 2, 3 ... in the topology's order, as
    cpptraj's masks count them) named as a topology that keeps the input's chain letters and residue numbers
    names them: for each range, [{"chain": "A", "range": [274, 899]}, ...], one entry per stretch of one
    chain whose numbers do not go back, or None for a range that is None. What the viewer and the videos
    select by on such an engine (Desmond); None on the others, whose topology numbers its residues as the
    ranges do, and before the build has written the topology. Ranges are cut at the solute's last residue."""
    from . import engine_for
    try:
        engine = engine_for(spec.engine)
    except ValueError:
        return None
    sysm = spec.system
    last = max((r[1] for r in (sysm.receptor_residues, sysm.ligand_residues) if r), default=0)
    if not engine.OWN_NUMBERING or last < 1:
        return None
    # a residue starts wherever the chain, number, insertion code or name changes from the atom before, as
    # cpptraj reads one from a PDB; only the solute's are needed, and it comes first
    residues: list[tuple[str, int]] = []
    seen = None
    try:
        with open(Path(rundir) / engine.TOPOLOGY, errors="replace") as fh:
            for line in fh:
                if not line.startswith(("ATOM", "HETATM")) or line[17:27] == seen:   # name, chain, number, insertion code
                    continue
                if len(residues) == last:
                    break
                seen = line[17:27]
                residues.append((line[21].strip(), int(line[22:26])))
    except (OSError, ValueError):
        return None
    if len(residues) < last:
        return None
    out: list = []
    for r in ranges:
        if not r:
            out.append(None)
            continue
        runs: list[dict] = []
        for chain, num in residues[max(1, r[0]) - 1:min(r[1], last)]:
            if runs and runs[-1]["chain"] == chain and num >= runs[-1]["range"][1]:
                runs[-1]["range"][1] = num
            else:
                runs.append({"chain": chain, "range": [num, num]})
        out.append(runs)
    return out
