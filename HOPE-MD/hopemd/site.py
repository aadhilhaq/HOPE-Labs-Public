"""Cluster profiles: what a site has and how it wants to be asked.

Everything in here was either read from the HPRC knowledge base or confirmed
from a login node. When the audit script finds something different, this file
is where it gets corrected.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Partition:
    name: str
    max_hours: float
    gpu: bool = False
    gres_types: tuple[str, ...] = ()      # names accepted after gpu:  e.g. ("a100", "rtx", "t4")
    max_running_jobs: int | None = None
    special: bool = False                 # a queue for a purpose (big memory, remote desktop), never
                                          # one to move an ordinary job onto because it happens to be longer


@dataclass(frozen=True)
class Site:
    key: str
    name: str
    login_hosts: tuple[str, ...]
    scratch: str                          # where runs live; $SCRATCH is expanded on the node
    partitions: dict[str, Partition]
    gpu_partition: str
    cpu_partition: str
    prep_partition: str                   # a short CPU job (the Prepare stage): nothing heavy runs on a login node
    default_gpu: str
    gpu_memory_gb: dict[str, int]
    container_runtime: str                # the command: "singularity" on Grace and ACES
    container_pull_needs: tuple[str, ...] # module loads needed before a pull on a compute node
    mem_required: bool                    # job_submit refuses jobs without --mem (Grace: yes)
    cpu_cores_per_node: int               # what a CPU-only quick check asks for (one whole node)
    cpu_mem_gb: int                       # memory to ask for on that node
    modules: dict[str, tuple[str, ...]]   # engine -> lines that go after `ml purge`
    routes_by_time: bool = False          # the scheduler picks the CPU queue from --time (see partition_to_name)
    notes: dict[str, str] = field(default_factory=dict)

    @property
    def gpu_wall_hours(self) -> float:
        return self.partitions[self.gpu_partition].max_hours

    def partition_to_name(self, part: str, hours: float | None, *, gpu: bool, pinned: bool) -> str:
        """The partition a job should name, or "" to name none and let the scheduler choose.

        Naming a queue whose limit is below the walltime asked for does not fail and does not
        run: the job sits PENDING with Reason=PartitionTimeLimit until somebody looks. Where the
        scheduler routes on --time (routes_by_time), a CPU job names no queue at all and Slurm
        offers it the ones that fit, which is both safer and what the cluster's own guidance
        says. Elsewhere, and for a queue the user pinned, the smallest CPU queue that can hold
        the job is named instead of one that cannot.

        A GPU queue is left alone: it selects the hardware (which gres types a node carries),
        not the length, and widening it would move an a100 run onto a40 nodes."""
        p = self.partitions.get(part)
        if hours is not None and p is not None and not p.gpu and p.max_hours < hours:
            fits = sorted((q for q in self.partitions.values()
                           if not q.gpu and not q.special and q.max_hours >= hours),
                          key=lambda q: q.max_hours)
            if fits:
                part = fits[0].name
        return "" if (self.routes_by_time and not gpu and not pinned) else part

    def gres(self, gpu: str | None = None, count: int = 1, partition: str | None = None) -> str:
        g = gpu or self.default_gpu
        part = partition or self.gpu_partition
        allowed = self.partitions[part].gres_types if part in self.partitions else ()
        if g not in allowed:
            raise ValueError(f"{self.name} has no GPU called {g!r} in partition "
                             f"{part}; it has {', '.join(allowed) or 'no GPUs'}")
        return f"gpu:{g}:{count}"

    def gpu_types(self) -> list[str]:
        """Every GPU type on the site's GPU partitions, fastest first (the order a run that may
        take any of them prefers; estimate.SEED has the throughput each is good for)."""
        order = ("h100", "a100", "a40", "a30", "rtx", "t4")
        have = [g for p in self.partitions.values() if p.gpu for g in p.gres_types]
        return [g for g in order if g in have] + sorted(g for g in have if g not in order)

    def partition_for(self, gpu: str) -> str:
        """The GPU partition that carries this type: the default one when it does (Grace's gpu
        has a100, rtx, t4), else the first that lists it (gpu-a40 for a40)."""
        if gpu in self.partitions[self.gpu_partition].gres_types:
            return self.gpu_partition
        for name, p in self.partitions.items():
            if p.gpu and gpu in p.gres_types:
                return name
        return self.gpu_partition

    def version(self, engine: str) -> str:
        """The version the engine's module line loads, read off the line itself
        ("GROMACS/2023.1-CUDA-12.2.0" gives "2023.1", "Desmond/2023-4" gives "2023-4"), so a
        README or a report never names a version the site does not run. "" when there is none."""
        import re
        m = re.search(rf"(?i)\b{re.escape(engine)}/(\d+(?:[.-]\d+)*)", " ".join(self.modules.get(engine, ())))
        return m.group(1) if m else ""

    def module_block(self, engine: str) -> str:
        """The lines a job script runs before the engine's own command."""
        lines = self.modules.get(engine)
        if lines is None:
            raise ValueError(f"{self.name}: no module line recorded for engine {engine!r}")
        return "\n".join(("module purge",) + tuple(lines))


# --------------------------------------------------------------------------- Grace
# Hardware and queues: hprc.tamu.edu/kb/User-Guides/Grace/Hardware and /Batch.
# gpuavail: 100 x gpu:a100:2, 15 x gpu:a40:3 (partition gpu-a40), 9 x gpu:rtx:2,
# 8 x gpu:t4:4; the a100/rtx/t4 nodes serve partitions vnc,gpu,special.
# Modules: as module spider lists them.
GRACE = Site(
    key="grace",
    name="Grace",
    login_hosts=("grace.hprc.tamu.edu",),
    scratch="$SCRATCH/hopemd_runs",
    partitions={
        "short":   Partition("short",   2),
        "medium":  Partition("medium",  24),
        "long":    Partition("long",    7 * 24),
        "xlong":   Partition("xlong",   21 * 24),
        "gpu":     Partition("gpu",     4 * 24, gpu=True, gres_types=("a100", "rtx", "t4")),
        "gpu-a40": Partition("gpu-a40", 4 * 24, gpu=True, gres_types=("a40",)),
        "bigmem":  Partition("bigmem",  2 * 24, special=True),
    },
    gpu_partition="gpu",
    cpu_partition="medium",
    prep_partition="short",
    default_gpu="a100",
    gpu_memory_gb={"a100": 40, "a40": 48, "rtx": 24, "t4": 16},
    container_runtime="singularity",
    container_pull_needs=("module load WebProxy",),
    mem_required=True,   # srun/sbatch: "either --mem or --mem-per-cpu should be specified"
    # Grace's job_submit plugin offers a job every CPU queue its walltime fits
    # (Partition=short,medium,long), so naming one can only narrow the choice or deadlock it
    routes_by_time=True,
    cpu_cores_per_node=48,   # Xeon 6248R nodes: 48 cores, 384 GB
    cpu_mem_gb=180,
    modules={
        # Amber 24 loads on Grace and bin/ carries pmemd.cuda, pmemd.cuda_SPFP, cpptraj.cuda,
        # MMPBSA.py, ante-MMPBSA.py. The toolchain line is the docs' line for Amber 24 on
        # FASTER/ACES; if `module load Amber/24-...` alone works, the two prerequisite
        # modules are harmless.
        "amber":   ("module load GCC/13.2.0 OpenMPI/4.1.6 Amber/24-NCCL-2.20.5-CUDA-12.4.1",),
        # OpenMM is a module on Grace (7.5.0 .. 8.3.1). No conda env needed. A bare
        # `module load OpenMM/8.3.1-CUDA-12.2.0` is refused by Lmod ("cannot be loaded as
        # requested"); it needs its toolchain in front, like Amber.
        "openmm":  ("module load GCC/13.2.0 OpenMPI/4.1.6 OpenMM/8.3.1-CUDA-12.2.0",),
        # A bare `module load GROMACS` resolves to 2024.4-PLUMED-2.9.3 with GPU support
        # DISABLED (confirmed). Name the CUDA build. Prereqs confirmed by module spider.
        "gromacs": ("module load GCC/13.2.0 OpenMPI/4.1.6 GROMACS/2024.4-CUDA-12.4.1",),
        # module -t spider NAMD: NAMD/2.14-CUDA-11.7.0, NAMD/2.14-mpi, NAMD/3.0-mpi,
        # NAMD/3.0.1-muliticore-CUDA (sic — the typo is in the module name).
        "namd":    ("module load NAMD/3.0.1-muliticore-CUDA",),
        # VMD/1.9.4a57 needs the same toolchain as Amber (module spider); a bare
        # `module load VMD/1.9.4a57` is refused. It has no Tachyon: the video job renders with
        # the POV-Ray 3.7 that comes with the module.
        "vmd":     ("module load GCC/13.2.0 OpenMPI/4.1.6 VMD/1.9.4a57",),
        # FFmpeg/5.1.2 sits on GCCcore/12.2.0 (confirmed); it cannot share a shell with the
        # VMD toolchain, so the video job purges and loads it after the frames are rendered.
        "ffmpeg":  ("module load GCCcore/12.2.0 FFmpeg/5.1.2",),
        # Desmond 2024-4 is the default at /sw/restricted/Desmond/2024-4. It MUST be loaded
        # into a purged shell: with an EasyBuild Python module loaded, Schrodinger's own
        # python dies on `import schrodinger` (sitecustomize clash, confirmed). Never load
        # the Schrodinger module for the Desmond jobs: that one is the LMS license, loaded only by
        # Prime's MM-GBSA job (below).
        # multisim runs with -HOST localhost inside the sbatch job: $SCHRODINGER/schrodinger.hosts on Grace also
        # lists grace-* hosts that would submit through Slurm themselves; never those from a job
        "desmond": ("module load Desmond/2024-4", "unset PYTHONPATH"),
        # the full suite (LMS license, /sw/restricted/lms): Prime's thermal_mmgbsa.py for MM-GBSA on a Desmond
        # run; only users on HPRC's "Schrodinger users" list get past its license check
        "schrodinger": ("module load Schrodinger/2025-3", "unset PYTHONPATH"),
    },
    notes={
        "amber_licence": "Amber 24 is free academically; HPRC gates the module group "
                         "(register at ambermd.org, email help@hprc.tamu.edu).",
        "desmond_licence": "Academic Desmond license via `ml Desmond`; Prime (thermal_mmgbsa) "
                           "lives in the LMS-only Schrodinger module.",
        "gpu_note": "grace1 has an A100, grace2 an RTX 6000, grace3 a T4; grace4-5 none. "
                    "Login nodes are not for running: about 1 h of processing and 8 cores per session.",
    },
)

# --------------------------------------------------------------------------- ACES
# Partitions and modules from an ACES login node (scripts/audit.sh, 2 Oct 2026): sinfo gives
# gpu (48 h; h100:4, h100:8 and a30:2 nodes), gpu_debug (2 h; h100:2 and a30:2) and cpu (72 h);
# every node has 96 cores and 500 GB. gpu_debug runs under the QoS "limited", which lets one
# person have 2 jobs queued or running there at a time.
# Modules: each line below was loaded on ACES and the programs the jobs call were found under it.
ACES = Site(
    key="aces",
    name="ACES",
    # aces.hprc.tamu.edu does not resolve; the login nodes sit behind aces-jump.hprc.tamu.edu:8822
    login_hosts=("login.aces.hprc.tamu.edu",),
    scratch="$SCRATCH/hopemd_runs",
    partitions={
        "cpu":       Partition("cpu",       3 * 24, max_running_jobs=40),
        "gpu":       Partition("gpu",       2 * 24, gpu=True, gres_types=("h100", "a30"), max_running_jobs=40),
        "gpu_debug": Partition("gpu_debug", 2,      gpu=True, gres_types=("h100", "a30"), max_running_jobs=2),
    },
    gpu_partition="gpu",
    cpu_partition="cpu",
    prep_partition="cpu",
    default_gpu="h100",
    gpu_memory_gb={"h100": 80, "a30": 24},
    container_runtime="singularity",
    container_pull_needs=("module load WebProxy",),
    mem_required=True,
    cpu_cores_per_node=96,   # sinfo: 96 cores and 500 GB on every node
    cpu_mem_gb=240,
    modules={
        # bin/ carries tleap, pdb4amber, parmed, pmemd.cuda, pmemd.MPI, cpptraj.OMP, MMPBSA.py,
        # antechamber; the module group is amber, as on Grace. The module does not put Amber's own
        # Python packages on PYTHONPATH (its amber.sh would), so pdb4amber, parmed and MMPBSA.py
        # die with "No package metadata was found for pdb4amber"; the second line adds them.
        "amber":   ("module load GCC/13.2.0 OpenMPI/4.1.6 Amber/24-NCCL-2.20.5-CUDA-12.4.1",
                    'export PYTHONPATH="$AMBERHOME/lib/python3.11/site-packages${PYTHONPATH:+:$PYTHONPATH}"'),
        # a bare `module load OpenMM` is 8.2.0 without CUDA; 8.3.1-CUDA-12 needs its toolchain
        "openmm":  ("module load GCC/13.2.0 OpenMPI/4.1.6 OpenMM/8.3.1-CUDA-12",),
        # GROMACS/2024.1 is the only 2024 build and has GPU support disabled. Of the CUDA builds,
        # 2023.1-CUDA-12.2.0 is compiled for sm_75, sm_80, sm_86 and sm_90, so it runs on the A30
        # (sm_80) and the H100 (sm_90); 2023.3-CUDA-12.1.1 has no sm_80 code and would not run on
        # an A30. Its gmx is a thread-MPI build, as on Grace.
        "gromacs": ("module load GCC/11.3.0 OpenMPI/4.1.4 GROMACS/2023.1-CUDA-12.2.0",),
        # No "namd": ACES has NAMD/2.14-mpi and no NAMD 3, and the NAMD jobs run namd3. The page
        # leaves NAMD out on ACES (spec.choices) and says why (spec.validate, notes["namd"]).
        # VMD 1.9.4a57 brings POV-Ray 3.7.0.10 with it (the video job renders with povray)
        "vmd":     ("module load GCC/11.3.0 OpenMPI/4.1.4 VMD/1.9.4a57",),
        "ffmpeg":  ("module load GCCcore/12.2.0 FFmpeg/5.1.2",),
        # the only Desmond on ACES; academic license, module group desmond
        "desmond": ("module load Desmond/2023-4", "unset PYTHONPATH"),
        # Prime's thermal_mmgbsa.py; module group schrodinger_swgrp
        "schrodinger": ("module load Schrodinger/2025-3", "unset PYTHONPATH"),
    },
    notes={
        "namd": "ACES has no NAMD 3 module (only NAMD/2.14-mpi), and HOPE-MD's NAMD jobs run namd3: "
                "run Amber, OpenMM, GROMACS or Desmond here, or NAMD on Grace.",
        "gpu_debug": "gpu_debug takes 2 jobs per person at a time (QoS limited).",
    },
)

SITES: dict[str, Site] = {s.key: s for s in (GRACE, ACES)}


def site_for_host(hostname: str) -> Site | None:
    h = hostname.lower()
    if h.startswith("grace"):
        return GRACE
    if h.startswith(("aces", "ac", "alogin")):     # login nodes are alogin<n>, compute nodes ac<nnn>
        return ACES
    return None
