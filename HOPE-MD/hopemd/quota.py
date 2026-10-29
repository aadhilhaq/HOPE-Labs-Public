"""Room for a run before Submit: what the user has used of the space the runs folder is in, from
HPRC's showquota, set against what the run will add.

Grace's scratch counts files as well as bytes (1 TB and 250 000 files per user), and a run that
reaches either limit stops wherever it is, its files half written. The run's bytes come from the
estimate's trajectory size (derive's frames and atoms), its files from the job graph: each engine
leaves a known number per run, per replicate and per production segment before its tidy job packs
the small ones, and every job and array task writes a Slurm log. Submit refuses a run that cannot
fit, and warns when one takes either count past 90 % of its limit. When the usage cannot be read
(no showquota on this machine, an output this does not understand, a runs folder in no space it
lists), the check says so and the run goes ahead.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

#: the share of a limit past which Submit warns
CLOSE = 0.9

#: files a run leaves before its tidy job packs them, by engine: those every run has (spec, inputs,
#: scripts, the build, the analysis's own files), those each replicate adds (its folder, the
#: ladder's outputs, its analysis and MM-GBSA tables) and those each production segment adds to
#: each replicate. Counted from finished runs on Grace (October 2026), to within about 10 %; the
#: Slurm logs, one per job and array task, come on top.
FILES = {"amber": (68, 45, 7), "openmm": (62, 22, 6), "gromacs": (79, 68, 13), "namd": (71, 81, 17),
         "desmond": (63, 146, 36)}
#: Desmond keeps its own trajectory beside the DCD the analysis reads
TRAJECTORY_COPIES = {"desmond": 2.0}
#: topologies, restarts and the analysis on top of the trajectory
OTHER_BYTES = 0.1

#: showquota's answer, read at most every five minutes (the page asks on every change), and what
#: this server has submitted since into each space: a batch counts as a whole, whose files are not
#: written yet when the next pose is checked
_CACHE: dict = {"t": 0.0, "spaces": None, "added": {}}
_UNITS = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4, "P": 1024 ** 5}
_LINE = re.compile(r"^(/\S+)\s+([\d.]+)([KMGTP]?)\*?\s+([\d.]+)([KMGTP]?)\*?\s+(\d+)\*?\s+(\d+)\*?$")


def parse_showquota(text: str) -> list[dict]:
    """The spaces showquota lists: [{space, used_bytes, limit_bytes, used_files, limit_files}].
    It prints sizes as 506.7G or 1.0T (powers of 1024, a * past the limit) and files as counts:

        /scratch/user/<NetID>    506.7G      1.0T    232827     250000"""
    out = []
    for line in text.splitlines():
        m = _LINE.match(line.strip())
        if m:
            out.append({"space": m.group(1), "used_bytes": float(m.group(2)) * _UNITS[m.group(3)],
                        "limit_bytes": float(m.group(4)) * _UNITS[m.group(5)],
                        "used_files": int(m.group(6)), "limit_files": int(m.group(7))})
    return out


def read_usage(root: str | Path, max_age_s: float = 300.0) -> tuple[dict | None, str]:
    """The quota of the space holding `root`, with what this server has submitted there since
    showquota was read; (None, why) when it cannot be read."""
    now = time.time()
    if _CACHE["spaces"] is None or now - _CACHE["t"] > max_age_s:
        exe = shutil.which("showquota")
        if not exe:
            return None, "showquota is not on this machine"
        try:
            r = subprocess.run([exe], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            return None, f"showquota did not answer ({e})"
        spaces = parse_showquota(r.stdout)
        if not spaces:
            return None, "showquota printed no quota this check understands"
        _CACHE.update(t=now, spaces=spaces, added={})
    paths = {str(Path(root)), str(Path(root).resolve())}
    mine = [s for s in _CACHE["spaces"] if any(p == s["space"] or p.startswith(s["space"].rstrip("/") + "/") for p in paths)]
    if not mine:
        return None, f"showquota lists no quota for {root}"
    q = dict(max(mine, key=lambda s: len(s["space"])))
    b, f = _CACHE["added"].get(q["space"], (0.0, 0))
    q["used_bytes"] += b
    q["used_files"] += f
    return q, ""


def run_adds(spec, derived: dict, jobs: list | None = None) -> tuple[float, int]:
    """(bytes, files) the run writes before its tidy job packs it. `jobs` is the rendered graph;
    without it (the Setup screen, before a render) the graph's shape comes from the spec."""
    from .spec import BYTES_PER_ATOM_FRAME
    engine = spec.forcefield.engine
    trajectory = float(derived.get("frames") or 0) * float(derived.get("atoms") or 0) * BYTES_PER_ATOM_FRAME * spec.protocol.replicates
    nbytes = trajectory * TRAJECTORY_COPIES.get(engine, 1.0) * (1 + OTHER_BYTES)
    per_run, per_rep, per_seg = FILES.get(engine, FILES["desmond"])
    reps = spec.protocol.replicates
    if jobs:
        segs = sum(1 for j in jobs if j.key.startswith("prod_s"))
        logs = sum(j.array or 1 for j in jobs)
    else:
        segs = int(derived.get("segments") or 1)
        logs = 3 + reps * (2 + segs)              # build, analysis, tidy; each replicate's ladder, MM-GBSA, segments
    return nbytes, per_run + reps * (per_rep + segs * per_seg) + logs


def _size(b: float) -> str:
    for unit, k in (("TB", 1024 ** 4), ("GB", 1024 ** 3), ("MB", 1024 ** 2)):
        if b >= k:
            return f"{b / k:.1f} {unit}"
    return f"{b / 1024:.0f} KB"


def _n(x: int) -> str:
    return f"{int(x):,}".replace(",", " ")


def check(spec, derived: dict, root: str | Path, jobs: list | None = None) -> dict:
    """What Submit should do about the quota: {"level": ok | close | full | unknown, "text": the
    sentence for the page, "space", "adds": [bytes, files]}. full refuses the run; close and
    unknown go with it as a note."""
    nbytes, nfiles = run_adds(spec, derived, jobs)
    q, why = read_usage(root)
    out = {"adds": [nbytes, nfiles]}
    if q is None:
        return {**out, "level": "unknown", "space": "", "text": f"The quota was not checked: {why}."}
    space, ub, lb, uf, lf = q["space"], q["used_bytes"], q["limit_bytes"], q["used_files"], q["limit_files"]
    out["space"] = space
    adds = f"this run writes about {_n(nfiles)} files and {_size(nbytes)} before its tidy job packs the small ones"
    if uf + nfiles > lf or ub + nbytes > lb:
        over = (f"{_n(uf)} of its {_n(lf)} files are used" if uf + nfiles > lf else f"{_size(ub)} of its {_size(lb)} are used")
        return {**out, "level": "full", "text": f"This run cannot fit in {space}: {over}, and {adds}. Tidy or delete "
                                               "finished runs, or open a runs folder in another space, then Submit."}
    used = f"{_n(uf)} of {_n(lf)} files and {_size(ub)} of {_size(lb)} used in {space}"
    if uf + nfiles > CLOSE * lf or ub + nbytes > CLOSE * lb:
        share = max((uf + nfiles) / lf, (ub + nbytes) / lb) if lf and lb else 1.0
        return {**out, "level": "close", "text": f"Close to the quota: {used}; {adds}, which brings it to "
                                                f"{share * 100:.0f} % of the limit. Tidy or delete finished runs soon."}
    return {**out, "level": "ok", "text": f"Quota: {used}; {adds}."}


def commit(result: dict) -> None:
    """Count a submitted run against its space until showquota is read again."""
    space = result.get("space")
    if space and _CACHE["spaces"] is not None:
        b, f = _CACHE["added"].get(space, (0.0, 0))
        _CACHE["added"][space] = (b + result["adds"][0], f + result["adds"][1])
