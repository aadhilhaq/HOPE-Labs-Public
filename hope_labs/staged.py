"""The staged structures folder: where a structure waits between being given and being read.

By Aadhil Haq

Every page that takes a structure puts it in one folder beside the runs, <runs>/.structures: an
upload under the name it arrived with, a fetched PDB entry as rcsb_1YCR.pdb. The page then reads
it to show the chains, and on submission the tool copies what the run uses into the run itself --
the folding pages write inputs/<name>.fasta, the ensemble page writes given.pdb. So a finished run reads nothing from this folder, which is what makes the
folder a cache and not a record.

It was also the one folder no tidy ever swept, and it grows by one file per upload for as long as
a group uses the pages. On Grace the quota that bites is inodes rather than bytes, so a folder
that only ever grows is a fault even when the files are small: the structures a working week
produces are a few megabytes and a few hundred inodes.

Two things make a sweep less obvious than deleting old files:

  * A form can still be pointing at one. Someone who uploads a structure and then leaves the page
    open holds its path in the form, and submitting after the file went would be refused with
    "there is no file at ...". KEEP_DAYS is the answer to that: a form left open for a week is not
    a form, and a fetched entry that goes is fetched again by the next person who asks for it.

    python -m hope_labs.staged RUNS_ROOT [--dry-run] [--days N]
"""
from __future__ import annotations

import os
import sys
import time

#: How long a staged structure is left alone. A week is longer than any form stays open and short
#: enough that a term's uploads do not add up.
KEEP_DAYS = 7

#: The folder's name inside the runs root. One definition, because three pages used to join this
#: string themselves and a fourth would have joined it again.
FOLDER = ".structures"

#: Folders already swept in this process, so that store() can sweep without a page paying for it
#: on every upload.
_swept = set()


def store(roots, sweep_first=True):
    """The folder staged structures are kept in, made if it is not there yet.

    Sweeping here rather than at a call site of its own is deliberate: staging goes through this
    one function, so the folder tidies itself and there is no new call for a later tool to forget.
    It happens once per folder per process, and never on a page whose first act is to read a file
    that is already staged.
    """
    where = os.path.join(os.path.abspath(os.path.expanduser(roots[0] if roots else ".")), FOLDER)
    os.makedirs(where, exist_ok=True)
    if sweep_first and where not in _swept:
        _swept.add(where)
        try:
            sweep(where)
        except OSError:
            # A sweep is housekeeping: a folder that cannot be listed or a file that will not go
            # must never stop someone staging a structure.
            pass
    return where


def _mine(path):
    """True when the file is this person's, which is the only kind a sweep may remove."""
    try:
        return os.stat(path).st_uid == os.getuid()
    except OSError:
        return False


def plan(where, days=KEEP_DAYS, now=None):
    """What a sweep of this folder would remove and what it would leave, and why.

    `where` is the folder itself or a runs root; either is accepted because a person reaches for
    both. Reasons are the words the caller prints, so they are written to be read.
    """
    folder = where if os.path.basename(os.path.abspath(where)) == FOLDER \
        else os.path.join(os.path.abspath(os.path.expanduser(where)), FOLDER)
    out = {"where": folder, "sweep": [], "keep": [], "bytes": 0}
    if not os.path.isdir(folder):
        return out
    cut = (time.time() if now is None else now) - days * 86400
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        try:
            stat = os.stat(path)
        except OSError:
            continue
        if stat.st_uid != os.getuid():
            out["keep"].append((name, "another person's"))
        elif stat.st_mtime > cut:
            out["keep"].append((name, "%.1f day(s) old" % ((time.time() - stat.st_mtime) / 86400.)))
        else:
            out["sweep"].append((name, "%.0f day(s) old" % ((time.time() - stat.st_mtime) / 86400.)))
            out["bytes"] += stat.st_size
    return out


def sweep(where, days=KEEP_DAYS, dry_run=False, now=None):
    """Remove the staged structures this person owns that nothing is waiting on any more."""
    got = plan(where, days=days, now=now)
    got["removed"] = 0
    if dry_run:
        return got
    for name, _why in got["sweep"]:
        try:
            os.remove(os.path.join(got["where"], name))
            got["removed"] += 1
        except OSError:
            # Another process may have taken it, or it may be on a read-only mount.
            pass
    return got


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    days = KEEP_DAYS
    if "--days" in args:
        at = args.index("--days")
        try:
            days = int(args[at + 1])
        except (IndexError, ValueError):
            print(__doc__)
            return 2
        del args[at:at + 2]
    flags = {a for a in args if a.startswith("--")}
    rest = [a for a in args if not a.startswith("--")]
    if len(rest) != 1 or flags - {"--dry-run"}:
        print(__doc__)
        return 2
    got = sweep(rest[0], days=days, dry_run="--dry-run" in flags)
    if not os.path.isdir(got["where"]):
        print("%s: nothing staged" % got["where"])
        return 0
    print("%s: %d file(s) %s (%.1f MB), %d left" % (
        got["where"], len(got["sweep"]),
        "would go" if "--dry-run" in flags else "removed",
        got["bytes"] / 1048576., len(got["keep"])))
    for name, why in got["keep"]:
        print("    kept  %s  (%s)" % (name, why))
    return 0


if __name__ == "__main__":
    sys.exit(main())
