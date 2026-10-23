"""Tidy a finished folding run: what its results no longer read is packed into one archive in the
run's own folder, and Undo puts it back.

The page, the report, the viewer and a flow after the fold read the structures, their confidence
and PAE files, the alignments offered for download (msa/*.csv, fold/*/*.a3m), Boltz-2's record
of what it was given (processed/records), the inputs, the log and submission.json (runs.py).
What nothing reads again:

    Boltz-2     out/boltz_results_*/processed/structures, mols, msa, constraints
                the tensors Boltz-2 made from the inputs before predicting
    ColabFold   fold/*/*_env/      the alignment search's own working files
    both        cache/mpl, cache/nv, cache/tmp
                matplotlib's font list, the CUDA driver's kernels and a temporary folder
                used only when the node had no short one

The rest of cache/ stays: the model weights and the home folder the job was given, which a
compute node could not fetch again for a run that is queued once more.

A finished job runs this itself (job.TAIL), unless the form said to keep every file, and only
after it has found the structures it wrote: a run that wrote none is left whole to be read.

    python -m hope_labs.folding.tidy RUN TOOL [--undo] [--dry-run]
"""
import glob
import json
import os
import sys
import tarfile
import time

ARCHIVE = "archive"
STATUS = "tidy.json"
PACKED = {
    "boltz2": ["out/boltz_results_*/processed/%s" % part
               for part in ("structures", "mols", "msa", "constraints")],
    "colabfold": ["fold/*/*_env"],
}
CACHES = ["cache/mpl", "cache/nv", "cache/tmp"]


def plan(run, tool):
    """The files to pack, relative to the run, in a stable order."""
    run = os.path.abspath(run)
    out = []
    for pattern in PACKED.get(tool, []) + CACHES:
        for top in sorted(glob.glob(os.path.join(run, pattern))):
            for root, _dirs, names in os.walk(top):
                for n in sorted(names):
                    out.append(os.path.relpath(os.path.join(root, n), run))
    return out


def tidy(run, tool, dry_run=False):
    run = os.path.abspath(run)
    if not os.path.isfile(os.path.join(run, "submission.json")):
        return {"error": "%s is not a folding run (no submission.json)" % run}
    files = plan(run, tool)
    if not files:
        return {"packed": 0, "note": "nothing to pack"}
    if dry_run:
        return {"packed": len(files), "dry_run": True, "files": files}
    adir = os.path.join(run, ARCHIVE)
    os.makedirs(adir, exist_ok=True)
    archive = os.path.join(adir, "tidy_%s.tar.gz" % time.strftime("%Y%m%d_%H%M%S"))
    with tarfile.open(archive, "w:gz") as tar:
        for rel in files:
            tar.add(os.path.join(run, rel), arcname=rel, recursive=False)
    with tarfile.open(archive, "r:gz") as tar:
        names = set(tar.getnames())
    missing = [f for f in files if f not in names]
    if missing:
        os.remove(archive)
        return {"error": "the archive missed %d file(s) (%s ...); nothing removed"
                % (len(missing), missing[0])}
    for rel in files:
        os.remove(os.path.join(run, rel))
    for rel in sorted({os.path.dirname(f) for f in files}, key=lambda d: d.count(os.sep),
                      reverse=True):
        while rel and rel not in ("cache", "out", "fold"):
            try:
                os.rmdir(os.path.join(run, rel))
            except OSError:
                break
            rel = os.path.dirname(rel)
    record = {"archive": os.path.basename(archive), "packed": len(files),
              "when": time.strftime("%Y-%m-%d %H:%M:%S")}
    sfile = os.path.join(adir, STATUS)
    try:
        with open(sfile) as fh:
            hist = json.load(fh)
    except (OSError, ValueError):
        hist = {}
    hist.setdefault("archives", []).append(record)
    with open(sfile, "w") as fh:
        json.dump(hist, fh, indent=1)
    return record


def undo(run):
    """Every archive back where it came from, then the archives go."""
    run = os.path.abspath(run)
    adir = os.path.join(run, ARCHIVE)
    arcs = sorted(f for f in os.listdir(adir) if f.startswith("tidy_") and f.endswith(".tar.gz")) \
        if os.path.isdir(adir) else []
    n = 0
    for a in arcs:
        with tarfile.open(os.path.join(adir, a), "r:gz") as tar:
            members = [m for m in tar.getmembers() if m.name and not m.name.startswith(("/", ".."))
                       and ".." not in m.name.split("/")]
            tar.extractall(run, members=members)
            n += len(members)
        os.remove(os.path.join(adir, a))
    if os.path.exists(os.path.join(adir, STATUS)):
        os.remove(os.path.join(adir, STATUS))
    try:
        os.rmdir(adir)
    except OSError:
        pass
    return {"restored": n, "archives": len(arcs)}


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    flags = {a for a in args if a.startswith("--")}
    rest = [a for a in args if not a.startswith("--")]
    if len(rest) != 2 or rest[1] not in PACKED or flags - {"--undo", "--dry-run"}:
        print(__doc__)
        return 2
    run, tool = rest
    if "--undo" in flags:
        r = undo(run)
        print("tidy undo %s: %d file(s) restored from %d archive(s)" % (run, r["restored"],
                                                                       r["archives"]))
        return 0
    r = tidy(run, tool, dry_run="--dry-run" in flags)
    if r.get("error"):
        print("tidy %s: %s" % (run, r["error"]))
        return 1
    print("tidy %s: %s" % (run, r.get("note") or "%d file(s) %s%s" % (
        r["packed"], "would be packed" if r.get("dry_run") else "packed into ",
        "" if r.get("dry_run") else r["archive"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
