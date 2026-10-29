"""ns/day from finished runs, falling back to a seed table.

``benchmarks.tsv`` at the repository root (or the file named by
``$HOPEMD_BENCHMARKS``) grows one row per finished production segment:
``site gpu engine atoms dt_fs ns_per_day date run``. The estimate for a new run
takes rows for the same site, GPU and engine, scales each by atoms (throughput
is close to 1/atoms over the range we care about) and returns the median. With
no rows it uses the seed table below and says so, so the first run on a new
GPU is never mistaken for a measurement.
"""
from __future__ import annotations

import csv
import os
import statistics
from pathlib import Path

# Seeds: order-of-magnitude numbers for pmemd.cuda-class throughput at 4 fs, taken from
# published engine benchmarks for JAC (23 558 atoms), Factor IX (90 906) and Cellulose
# (408 609) class systems. They are guesses until the table has real rows.
SEED = {
    ("a100", "amber"):   [(23558, 1100.0), (90906, 380.0), (408609, 85.0)],
    ("h100", "amber"):   [(23558, 1500.0), (90906, 520.0), (408609, 120.0)],
    ("a40",  "amber"):   [(23558, 800.0),  (90906, 260.0), (408609, 60.0)],
    ("rtx",  "amber"):   [(23558, 650.0),  (90906, 210.0), (408609, 48.0)],
    ("t4",   "amber"):   [(23558, 260.0),  (90906, 85.0),  (408609, 19.0)],
    ("a30",  "amber"):   [(23558, 700.0),  (90906, 240.0), (408609, 55.0)],
    # one whole CPU node, pmemd.MPI-class throughput at 4 fs; GROMACS on CPU is ~2x this
    ("cpu48", "amber"):  [(23558, 60.0),   (90906, 18.0),  (408609, 4.0)],
    ("cpu96", "amber"):  [(23558, 100.0),  (90906, 32.0),  (408609, 7.0)],
}
# engines relative to pmemd.cuda on the same GPU and system, again rough
ENGINE_FACTOR = {"amber": 1.0, "openmm": 0.85, "gromacs": 0.9, "namd": 0.6, "desmond": 0.9}
CPU_ENGINE_FACTOR = {"amber": 1.0, "gromacs": 2.0, "namd": 1.2, "openmm": 0.1, "desmond": 0.0}
# GROMACS 2024.4 keeps the update and the constraints on the CPU when the water has a fourth site (its extra
# point is a virtual site, which the GPU update does not take), so such a run is slower than its atoms say:
# 1BHL in OPC and in TIP4P-Ew ran at 344 to 380 ns/day on an A100, where the three-site rows scaled to its
# atoms give about 925. Its speeds are kept apart in the tables (the engine column reads "gromacs-4site");
# until there are some, the engine's three-site estimate is scaled by this.
FOUR_SITE_FACTOR = {"gromacs": 0.4}
COLUMNS = ["site", "gpu", "engine", "atoms", "dt_fs", "ns_per_day", "date", "run"]
# timings.tsv, beside benchmarks.tsv: every finished job of every run, so an estimate can say
# how long the build, the ladder, the analysis and MM-GBSA take here, not only the production.
TIMING_COLUMNS = ["site", "engine", "device", "stage", "atoms", "steps", "frames", "elapsed_s", "node", "partition", "date", "run"]


def repo_benchmarks_path() -> Path:
    """The table committed with the code: the rows everyone shares."""
    return Path(__file__).resolve().parent.parent / "benchmarks.tsv"


def benchmarks_path() -> Path:
    """Where a new row goes: $HOPEMD_BENCHMARKS when set (the server points it at the runs
    root when the checkout is a shared, read-only install), else the repository's table."""
    p = os.environ.get("HOPEMD_BENCHMARKS")
    if p:
        return Path(p)
    return repo_benchmarks_path()


def read_rows(path: Path | None = None) -> list[dict]:
    """Rows from the repository's table and from $HOPEMD_BENCHMARKS, when that is another file."""
    paths = [path] if path else [repo_benchmarks_path()]
    if not path and benchmarks_path() != repo_benchmarks_path():
        paths.append(benchmarks_path())
    out = []
    for pth in paths:
        if not pth.exists():
            continue
        with pth.open() as fh:
            rows = list(csv.DictReader(fh, delimiter="\t"))
        for r in rows:
            try:
                out.append({**r, "atoms": int(r["atoms"]), "dt_fs": float(r["dt_fs"]),
                            "ns_per_day": float(r["ns_per_day"])})
            except (KeyError, ValueError, TypeError):
                continue
    return out


def append_row(site: str, gpu: str, engine: str, atoms: int, dt_fs: float,
               ns_per_day: float, date: str, run: str, path: Path | None = None) -> None:
    path = path or benchmarks_path()
    new = not path.exists() or path.stat().st_size == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        if new:
            w.writerow(COLUMNS)
        w.writerow([site, gpu, engine, atoms, f"{dt_fs:g}", f"{ns_per_day:.1f}", date, run])


def timings_path() -> Path:
    """timings.tsv beside whichever benchmarks table takes new rows."""
    return benchmarks_path().with_name("timings.tsv")


def run_tables(rundir: str | Path) -> tuple[Path, Path]:
    """Where a run's rows go: benchmarks.tsv and timings.tsv in the runs folder that holds it,
    whoever writes them (its tidy job, the page, the report). A shared runs folder keeps the rows of
    its own runs, and a server started on another folder does not take them; for runs in the folder
    a server was started with, these are the tables its estimates read."""
    folder = Path(rundir).resolve().parent
    return folder / "benchmarks.tsv", folder / "timings.tsv"


def read_timings(path: Path | None = None) -> list[dict]:
    """Rows from the repository's timings.tsv and from the one beside $HOPEMD_BENCHMARKS, or from
    `path` alone."""
    paths = [path] if path else [repo_benchmarks_path().with_name("timings.tsv")]
    if not path and timings_path() != paths[0]:
        paths.append(timings_path())
    out = []
    for pth in paths:
        if not pth.exists():
            continue
        with pth.open() as fh:
            rows = list(csv.DictReader(fh, delimiter="\t"))
        for r in rows:
            try:
                out.append({**r, "atoms": int(r["atoms"]), "elapsed_s": int(r["elapsed_s"]),
                            "steps": int(r["steps"]) if r.get("steps") else None,
                            "frames": int(r["frames"]) if r.get("frames") else None})
            except (KeyError, ValueError, TypeError):
                continue
    return out


def append_timing(site: str, engine: str, device: str, stage: str, atoms: int, steps: int | None,
                  frames: int | None, elapsed_s: int, node: str, partition: str, date: str, run: str,
                  path: Path | None = None) -> None:
    path = path or timings_path()
    new = not path.exists() or path.stat().st_size == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        if new:
            w.writerow(TIMING_COLUMNS)
        w.writerow([site, engine, device, stage, atoms, steps or "", frames or "", elapsed_s, node, partition, date, run])


def estimate_stage_seconds(site: str, engine: str, device: str, stage: str, atoms: int,
                           steps: int | None = None, frames: int | None = None,
                           rows: list[dict] | None = None) -> tuple[float | None, str]:
    """How long a stage should take here, from finished jobs of the same site, engine, device
    and stage, each scaled to this system (by atoms; by steps too for the ladder, by frames
    for the analysis and MM-GBSA), as the median. (None, why) when nothing has run yet."""
    rows = read_timings() if rows is None else rows
    mine = [r for r in rows if r["site"] == site and r["engine"] == engine and r["device"] == device and r["stage"] == stage]
    if stage in ("build", "prepare", "tidy"):
        # One CPU job whatever the engine, and whatever the run is going to be run on: the rows
        # carry the run's device, not the job's, so matching on it would throw them away.
        mine = [r for r in rows if r["site"] == site and r["stage"] == stage]
    if not mine:
        return None, f"no finished {stage} job on {device} ({site}) yet"
    scaled = []
    for r in mine:
        f = atoms / max(r["atoms"], 1)
        if stage == "equil" and steps and r.get("steps"):
            f *= steps / r["steps"]
        if stage in ("analysis", "mmgbsa") and frames and r.get("frames"):
            f *= frames / r["frames"]
        scaled.append(r["elapsed_s"] * f)
    return statistics.median(scaled), f"from {len(mine)} finished {stage} job{'s' if len(mine) != 1 else ''} on {device} ({site})"


def _interp_seed(table: list[tuple[int, float]], atoms: int) -> float:
    # log-log interpolation between seed points; extrapolate with 1/atoms beyond the ends
    import math
    pts = sorted(table)
    if atoms <= pts[0][0]:
        return pts[0][1] * pts[0][0] / max(atoms, 1)
    if atoms >= pts[-1][0]:
        return pts[-1][1] * pts[-1][0] / atoms
    for (a0, v0), (a1, v1) in zip(pts, pts[1:]):
        if a0 <= atoms <= a1:
            t = (math.log(atoms) - math.log(a0)) / (math.log(a1) - math.log(a0))
            return math.exp(math.log(v0) + t * (math.log(v1) - math.log(v0)))
    return pts[-1][1]


def speed_engine(engine: str, water: str, on_cpu: bool = False) -> str:
    """The engine column of benchmarks.tsv for a run: the engine, or "<engine>-4site" for one whose four-site
    water costs it more than its atoms on a GPU (FOUR_SITE_FACTOR), whose speeds are not those of its
    three-site runs."""
    from .spec import WATER_SITES
    if engine in FOUR_SITE_FACTOR and not on_cpu and WATER_SITES.get(water, 3) == 4:
        return f"{engine}-4site"
    return engine


def estimate_ns_per_day(site: str, gpu: str, engine: str, atoms: int, dt_fs: float,
                        rows: list[dict] | None = None) -> tuple[float, str]:
    """Returns (ns_per_day, note). The note says where the number came from. `engine` is the
    table's engine column (speed_engine)."""
    rows = read_rows() if rows is None else rows
    mine = [r for r in rows if r["site"] == site and r["gpu"] == gpu and r["engine"] == engine]
    if mine:
        scaled = [r["ns_per_day"] * (r["atoms"] / max(atoms, 1)) * (dt_fs / r["dt_fs"]) for r in mine]
        med = statistics.median(scaled)
        return med, f"from {len(mine)} finished {engine} run{'s' if len(mine) != 1 else ''} on {gpu} ({site})"
    base = engine[:-len("-4site")] if engine.endswith("-4site") else ""
    if base in FOUR_SITE_FACTOR:
        # no four-site run on this GPU yet: the engine's three-site estimate, slowed as measured
        ns, note = estimate_ns_per_day(site, gpu, base, atoms, dt_fs, rows)
        return ns * FOUR_SITE_FACTOR[base], f"{note}, × {FOUR_SITE_FACTOR[base]:g} for a four-site water"
    table = SEED.get((gpu, "amber"))
    if table is None:
        table = SEED[("cpu48", "amber")] if gpu.startswith("cpu") else SEED[("a100", "amber")]
        gpu_note = f"no seed for {gpu}; using the {'48-core' if gpu.startswith('cpu') else 'A100'} table"
    else:
        gpu_note = (f"first CPU-node run here: seed table, treat as a guess" if gpu.startswith("cpu")
                    else f"first run on {gpu} here: seed table, treat as a guess")
    factor = (CPU_ENGINE_FACTOR if gpu.startswith("cpu") else ENGINE_FACTOR).get(engine, 0.8)
    base = _interp_seed(table, atoms) * max(factor, 0.01) * (dt_fs / 4.0)
    return base, gpu_note
