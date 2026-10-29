"""Pack what a finished run no longer needs into one archive, so a run holds tens of inodes
rather than hundreds.

The scratch file systems count files as well as bytes (Grace: 250 000 files per user), and a
finished run leaves 100 to 250 of them: every stage's log, restart and checkpoint, the build's
intermediate PDBs, job control's scratch. What the page, the report, the videos, a resume and a
re-analysis read is a short list; everything else is packed into archive/tidy_<stamp>.tar.gz and
removed, and `--undo` puts it all back. A run keeps:

    spec.json, README.txt, stripped.*                 the run and the viewer's topology
    jobs/ logs/ input/ md/ analysis/ export/ dense/    scripts, Slurm logs, inputs, results, media, report (only the
                                                       cpptraj/VMD scripts and their logs go from analysis/ and export/)
    build/                                             the topology and what re-runs the build: complex.*,
                                                       system.*, prepared.pdb, prep_report.json, posres_*.itp,
                                                       index.ndx, *.msj *.sh *.py *.in hmr.parmed, and a small
                                                       molecule's lig.mol2 and lig.frcmod (the param job's
                                                       AM1-BCC charges and parameters, which the build reads);
                                                       NAMD's restraint_*.pdb (the ladder and a restrained
                                                       production read them) and Desmond's ligand_asl.txt (the
                                                       partner as Prime names it, read by an MM-GBSA rerun),
                                                       and receptor_numbering.json / ligand_numbering.json (each
                                                       residue's own id, which the report and the page name the
                                                       topology's residues by)
    r<i>/                                              the trajectory parts (.nc .xtc .dcd; Desmond's _trj
                                                       folders and -out.cms), each segment's restart
                                                       (prod_s<k>.rst7|xml|cpt|coor|vel|xsc|cms), eq4's,
                                                       each part's .tpr, .nsday, .done, .progress.json, .whole

Everything else goes: min/heat/eq1-3 outputs, per-part restarts and checkpoints, mdinfo, engine
logs, GROMACS .edr/.gro/.mdp, NAMD .restart.*/.xst, Desmond's relax_<n>/ stages, checkpoints and
.tgz archives, job control's tmp/, the build's intermediate PDBs and logs, leap.log. The scripts
and logs of a video, snapshot or dense-window job still queued or running stay until a later tidy.

    python3 -m hopemd.tidy <rundir> [--dry-run] [--undo] [--force]

The tidy job (jobs/90_tidy.sbatch, the last job of every graph unless compute.tidy is off) runs
this once the production and the analysis have succeeded and MM-GBSA has ended (Engine.tidy_depends);
the Runs screen submits it for an older run, and undoes it, with the same script. A run with a job still queued or running is left alone unless --force.
Before it packs, it records the run's finished jobs in the timing tables (hopemd.timing.record),
so a run nobody opens on the Runs screen is timed too.
"""
from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import sys
import tarfile
import time
from pathlib import Path

KEEP_WHOLE_DIRS = ("jobs", "logs", "input", "md", "analysis", "export", "archive", "prepare", "dense", "staging")
KEEP_TOP = ("spec.json", "README.txt", "stripped.*")
KEEP_BUILD = ("complex.*", "complex_hmr.prmtop", "system.*", "prepared.pdb", "prep_report.json", "posres_*.itp",
              "index.ndx", "*.msj", "*.sh", "*.py", "*.in", "hmr.parmed", "*.mdp", "lig.mol2", "lig.frcmod",
              "restraint_*.pdb", "ligand_asl.txt", "*_numbering.json")
_SEG, _PART = r"prod_s\d+", r"prod_s\d+_p\d+"
KEEP_REPLICATE = [re.compile(p) for p in (
    rf"{_PART}\.(nc|xtc|dcd)$",                        # the trajectory parts
    rf"{_PART}\.nsday$", rf"{_SEG}\.done$", rf"{_SEG}\.progress\.json$",
    rf"{_SEG}\.(rst7|xml|cpt|coor|vel|xsc|cms)$",      # the segment restart the next segment starts from
    r"eq4\.(rst7|xml|cpt|gro|coor|vel|xsc|cms)$",       # what production starts from
    rf"{_PART}\.tpr$", rf"{_PART}\.xtc\.whole$",        # GROMACS: a resume, and the whole-molecule marker
    rf"{_PART}_trj(/.*)?$", rf"{_PART}-out\.cms$",      # Desmond's own trajectory, for Maestro
)]
#: inside folders kept whole, the scripts and logs of finished jobs still go (the results, the job
#: records the page reads and the media stay)
PACK_IN_KEPT = {"analysis": ("*.cpptraj", "*.cpptraj.log", "dense_*.log", "reference.frc"),
                "export": ("*.tcl", "*.log", "*.cpptraj", "*.pov", "*.ini")}
ARCHIVE_DIR = "archive"


def _keep_build(rel: Path) -> bool:
    return len(rel.parts) == 1 and any(fnmatch.fnmatch(rel.name, pat) for pat in KEEP_BUILD)


def _keep_replicate(rel: Path) -> bool:
    s = rel.as_posix()
    return any(rx.match(s) for rx in KEEP_REPLICATE)


def _left(name: str, leave: tuple[str, ...] | list[str]) -> bool:
    """A file of a job whose files stay: movie_r1.cpptraj and movie_r1_dry.tcl are movie_r1's, movie_r10.tcl is not."""
    return any(name.startswith((stem + ".", stem + "_")) for stem in leave)


def plan(rundir: Path, leave: tuple[str, ...] | list[str] = ()) -> list[Path]:
    """The files to pack, relative to the run folder, in a stable order. `leave` names the stems of
    files in analysis/ and export/ that stay, those of a media job still queued or running."""
    out: list[Path] = []
    for top in sorted(rundir.iterdir()):
        name = top.name
        if top.is_dir():
            if name in KEEP_WHOLE_DIRS:
                for pat in PACK_IN_KEPT.get(name, ()):
                    out.extend(p.relative_to(rundir) for p in sorted(top.glob(pat)) if p.is_file() and not _left(p.name, leave))
                continue
            if name == "build":
                for p in sorted(top.rglob("*")):
                    if p.is_file() and not _keep_build(p.relative_to(top)):
                        out.append(p.relative_to(rundir))
            elif name == "schrodinger":
                # Schrodinger job control's database (job records, job-server logs): every file is packed
                out.extend(p.relative_to(rundir) for p in sorted(top.rglob("*")) if p.is_file())
            elif re.fullmatch(r"r\d+", name):
                for p in sorted(top.rglob("*")):
                    if p.is_file() and not _keep_replicate(p.relative_to(top)):
                        out.append(p.relative_to(rundir))
            # any other folder is not ours to judge: left alone
        elif top.is_file() or top.is_symlink():
            if not any(fnmatch.fnmatch(name, pat) for pat in KEEP_TOP):
                out.append(Path(name))
    return out


def count_inodes(rundir: Path) -> int:
    return 1 + sum(1 for _ in rundir.rglob("*"))


def _prune_empty(rundir: Path, dirs: tuple[str, ...] = ("build", "schrodinger")) -> None:
    """Empty folders the packing left behind go, but never build/ or an r<i>/ themselves."""
    roots = [rundir / d for d in dirs] + [p for p in rundir.iterdir() if p.is_dir() and re.fullmatch(r"r\d+", p.name)]
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted((q for q in root.rglob("*") if q.is_dir()), key=lambda q: len(q.parts), reverse=True):
            try:
                p.rmdir()
            except OSError:
                pass


LIVE = ("RUNNING", "PENDING", "COMPLETING", "REQUEUED", "SUSPENDED", "CONFIGURING")


def active_jobs(rundir: Path) -> list[str]:
    """Job keys of this run still queued or running (the tidy job itself excepted); empty when
    the accounting is not there to ask."""
    ids_file = rundir / "jobs" / "ids.json"
    if not ids_file.exists():
        return []
    try:
        ids = json.loads(ids_file.read_text()).get("jobs", {})
    except (OSError, ValueError):
        return []
    ids = {k: v for k, v in ids.items() if k not in ("tidy", "untidy") and not str(v).startswith("dry-")}
    if not ids:
        return []
    return _live(ids)


def _live(ids: dict[str, str]) -> list[str]:
    """The keys of `ids` whose job is queued or running; empty when the accounting is not there to ask."""
    try:
        from .queue import queue_states
        states = queue_states(ids)
    except Exception:  # no sacct here: the caller decides
        return []
    return sorted(k for k, m in states.items() if any(st.split()[0] in LIVE for st in m))


def media_jobs(rundir: Path) -> dict[str, list[str]]:
    """The video, snapshot and dense-window jobs queued from the Runs screen, from their job records:
    {job id: the stems of the files each reads and writes in export/ or analysis/ (movie_r1, snap_r1,
    dense_r1_5)}. They are not in the graph, so the tidy job does not wait for them."""
    out: dict[str, list[str]] = {}
    for rel, stem in (("export/video_jobs.json", "movie_{}"), ("export/snapshot_jobs.json", "snap_{}")):
        for key, j in _read(rundir / rel).items():
            if isinstance(j, dict) and j.get("job"):
                out.setdefault(str(j["job"]), []).append(stem.format(key))
    for j in _read(rundir / "analysis" / "dense_jobs.json").values():
        if isinstance(j, dict) and j.get("job"):
            tag = f"{float(j.get('centre_ns') or 0):g}".replace(".", "p")      # as submit_dense names its files
            out.setdefault(str(j["job"]), []).append(f"dense_r{j.get('rep')}_{tag}")
    return out


def live_media(rundir: Path) -> list[str]:
    """The stems of the files of media jobs still queued or running: tidy leaves them, as packing a
    video's scripts under it would stop it."""
    jobs = {jid: stems for jid, stems in media_jobs(rundir).items() if not jid.startswith("dry")}
    if not jobs:
        return []
    return sorted(stem for jid in _live({j: j for j in jobs}) for stem in jobs.get(jid, []))


def tidy(rundir: str | Path, dry_run: bool = False, force: bool = False) -> dict:
    """Pack and remove. Returns what happened; nothing is removed until the archive lists every
    file it was given."""
    rundir = Path(rundir).resolve()
    if not (rundir / "spec.json").exists():
        return {"error": f"{rundir} is not a run folder (no spec.json)"}
    before = count_inodes(rundir)
    leave = live_media(rundir)
    files = plan(rundir, leave)
    if not files:
        return {"packed": 0, "archive": "", "inodes_before": before, "inodes_after": before, "note": "nothing to pack",
                **({"left": leave} if leave else {})}
    if not force:
        live = active_jobs(rundir)
        if live:
            return {"error": f"jobs still queued or running ({', '.join(live)}); tidy when the run is done, or --force"}
    if dry_run:
        return {"packed": len(files), "archive": "", "inodes_before": before, "inodes_after": before - len(files),
                "dry_run": True, "files": [f.as_posix() for f in files], **({"left": leave} if leave else {})}
    stamp = time.strftime("%Y%m%d_%H%M%S")
    adir = rundir / ARCHIVE_DIR
    adir.mkdir(exist_ok=True)
    archive = adir / f"tidy_{stamp}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for rel in files:
            tar.add(rundir / rel, arcname=rel.as_posix(), recursive=False)
    with tarfile.open(archive, "r:gz") as tar:
        names = set(tar.getnames())
    missing = [f.as_posix() for f in files if f.as_posix() not in names]
    if missing:
        archive.unlink()
        return {"error": f"the archive missed {len(missing)} file(s) ({missing[0]} ...); nothing removed"}
    for rel in files:
        (rundir / rel).unlink()
    _prune_empty(rundir)
    after = count_inodes(rundir)
    record = {"archive": archive.name, "when": time.strftime("%Y-%m-%d %H:%M:%S"), "packed": len(files),
              "inodes_before": before, "inodes_after": after}
    status_file = adir / "tidy.json"
    history = _read(status_file)
    history.setdefault("archives", []).append(record)
    status_file.write_text(json.dumps(history, indent=1))
    return dict(record, archive=archive.relative_to(rundir).as_posix(), **({"left": leave} if leave else {}))


def undo(rundir: str | Path) -> dict:
    """Every tidy archive back where it came from, then the archives go."""
    rundir = Path(rundir).resolve()
    adir = rundir / ARCHIVE_DIR
    archives = sorted(adir.glob("tidy_*.tar.gz")) if adir.is_dir() else []
    if not archives:
        return {"restored": 0, "note": "no tidy archive here"}
    n = 0
    for archive in archives:
        with tarfile.open(archive, "r:gz") as tar:
            members = [m for m in tar.getmembers() if m.name and not m.name.startswith(("/", "..")) and ".." not in Path(m.name).parts]
            tar.extractall(rundir, members=members)
            n += len(members)
        archive.unlink()
    for leftover in ("tidy.json",):
        if (adir / leftover).exists():
            (adir / leftover).unlink()
    try:
        adir.rmdir()
    except OSError:
        pass
    return {"restored": n, "archives": len(archives), "inodes_now": count_inodes(rundir)}


def status(rundir: str | Path) -> dict:
    """What the page shows: the archives here and the inode counts of the last tidy."""
    rundir = Path(rundir)
    h = _read(rundir / ARCHIVE_DIR / "tidy.json")
    arcs = h.get("archives", [])
    return {"archives": len(arcs), "packed": sum(a.get("packed", 0) for a in arcs),
            "inodes_before": arcs[0].get("inodes_before") if arcs else None,
            "inodes_after": arcs[-1].get("inodes_after") if arcs else None,
            "last": arcs[-1].get("when") if arcs else None}


def _read(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def submit(rundir: str | Path, undo_: bool = False, dry_run: bool = False) -> dict:
    """The tidy job for a run that has no such job yet (an older run, or an undo): the script is
    rendered if missing, the stage is added to the graph so the page shows it, and sbatch takes
    it with every job of the run still queued or running as an afterany dependency (Slurm refuses
    one on a job it has already forgotten, a minute after it ended on Grace). Undo runs the same
    script with --undo."""
    rundir = Path(rundir).resolve()
    from .engines import engine_for
    from .spec import Spec
    spec = Spec.from_json((rundir / "spec.json").read_text())
    engine = engine_for(spec.forcefield.engine)
    script = rundir / "jobs" / "90_tidy.sbatch"
    if not script.exists():
        script.parent.mkdir(exist_ok=True)
        script.write_text(engine.tidy_sbatch(spec))
        script.chmod(0o755)
    gpath = rundir / "jobs" / "graph.json"
    graph = _read_list(gpath)
    if not any(j.get("key") == "tidy" for j in graph):
        terminal = [j["key"] for j in graph if not any(j["key"] == d[0] for k in graph for d in k.get("depends", []))]
        graph.append({"key": "tidy", "script": "jobs/90_tidy.sbatch",
                      "depends": [list(d) for d in engine.tidy_depends([j["key"] for j in graph], terminal)],
                      "array": None, "gpu": False, "description": engine.TIDY_DESCRIPTION})
        gpath.write_text(json.dumps(graph, indent=2))
    ids_file = rundir / "jobs" / "ids.json"
    ids = _read(ids_file)
    key = "untidy" if undo_ else "tidy"
    others = {k: v for k, v in ids.get("jobs", {}).items() if k not in ("tidy", "untidy") and not str(v).startswith("dry-")}
    live = [others[k] for k in _live(others)]
    cmd = ["sbatch", "--parsable", f"--job-name={spec.name}_{key}"]
    if live:
        cmd.append("--dependency=afterany:" + ":".join(live))
    if undo_:
        cmd.append("--export=ALL,HOPEMD_TIDY_ARGS=--undo")
    cmd.append("jobs/90_tidy.sbatch")
    log = [" ".join(cmd)]
    if dry_run:
        jid = f"dry-{key}"
    else:
        r = subprocess.run(cmd, cwd=rundir, capture_output=True, text=True)
        if r.returncode != 0:
            return {"error": (r.stderr or r.stdout).strip()[:400], "log": log}
        jid = r.stdout.strip().split(";")[0]
    ids.setdefault("jobs", {})[key] = jid
    ids_file.write_text(json.dumps(ids, indent=2))
    log.append(f"{key} -> {jid}")
    return {"job": jid, "key": key, "log": log, "dry_run": dry_run}


def _read_list(p: Path) -> list:
    try:
        v = json.loads(p.read_text())
        return v if isinstance(v, list) else []
    except (OSError, ValueError):
        return []


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    flags = {a for a in args if a.startswith("--")}
    dirs = [a for a in args if not a.startswith("--")]
    if not dirs:
        print(__doc__)
        return 2
    rc = 0
    for d in dirs:
        if "--dry-run" not in flags and (Path(d) / "spec.json").exists():
            _record(d)
        if "--undo" in flags:
            r = undo(d)
            print(f"[hopemd] tidy undo {d}: " + (r.get("error") or f"{r.get('restored', 0)} file(s) restored from {r.get('archives', 0)} archive(s)" + (f", {r['inodes_now']} inodes now" if 'inodes_now' in r else "")))
        else:
            r = tidy(d, dry_run="--dry-run" in flags, force="--force" in flags)
            if r.get("error"):
                print(f"[hopemd] tidy {d}: {r['error']}")
                rc = 1
            elif r.get("dry_run"):
                print(f"[hopemd] tidy {d} (dry run): {r['packed']} file(s) would be packed, {r['inodes_before']} -> {r['inodes_after']} inodes")
                for f in r["files"]:
                    print("   " + f)
            else:
                print(f"[hopemd] tidy {d}: {r['packed']} file(s) packed into {r['archive'] or 'nothing'}; {r['inodes_before']} -> {r['inodes_after']} inodes" + (f" ({r['note']})" if r.get('note') else ""))
            if r.get("left"):
                print(f"[hopemd] tidy {d}: the files of {', '.join(r['left'])} stay, their job still queued or running; a later tidy packs them")
    return rc


def _record(rundir: str) -> None:
    """The run's finished jobs into the timing tables beside it, before anything is packed. The tidy
    job ends every graph, so a run is timed whether or not anyone opens it; a failure here never
    stops the tidy."""
    try:
        from .timing import record
        got = record(rundir)
    except Exception as e:  # no accounting here, a folder that is not a run
        print(f"[hopemd] timing of {rundir} not recorded: {e}")
        return
    if got["timings"] or got["benchmarks"]:
        print(f"[hopemd] timing of {rundir}: {len(got['timings'])} job row(s) and {len(got['benchmarks'])} speed row(s) "
              f"added to the tables in {got['folder']}")


if __name__ == "__main__":
    sys.exit(main())
