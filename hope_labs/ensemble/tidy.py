"""Tidy a finished ensemble run: what its results no longer read is packed into one archive in the
run's own folder, and Undo puts it back.

By Aadhil Haq

The page, the report, the viewer and a comparison against a simulation read the samples
(out/samples.xtc), the topology they are read with, what reading them found (analysis/), the
alignment phase one fetched, the sequence, the log and submission.json. What nothing reads again:

    out/batch_*.npz   the per-batch arrays the samples were written from. BioEmu writes these as
                      it goes and combines them into one .xtc at the end, so once the .xtc is
                      there they are the same numbers twice.
    cache/embeds      the embeddings made from the alignment. A second run of the same sequence
                      makes them again from the alignment, which stays, and needs no network to
                      do it.
    cache/mpl, cache/nv, cache/tmp
                      matplotlib's font list, the CUDA driver's kernels and a temporary folder
                      used only when the node had no short one.

The rest of cache/ stays: the home folder the image was given, which is where its weights are
linked from.

The packing itself is the folding pages' (hope_labs/folding/tidy.py) rather than a second copy of
it: one archive, verified before anything is removed, and Undo puts every file back. Only the
list of what nothing reads again differs by tool, so that is all this module adds.

A finished job runs this itself (job.TIDY), unless the form said to keep every file, and only
after it has found the samples it wrote: a run that wrote none is left whole to be read.

    python -m hope_labs.ensemble.tidy RUN [--undo] [--dry-run]
"""
from __future__ import annotations

import sys

from ..folding import tidy as shared

#: What nothing reads again, for a run of this kind. Added to the shared table rather than kept
#: here, because the shared tidy looks the tool up in it; registering is what makes
#: hope_labs.folding.tidy RUN bioemu work as well, which is the line a person who has tidied a
#: folding run by hand will try first.
PACKED = ["out/batch_*.npz", "cache/embeds"]
shared.PACKED.setdefault("bioemu", PACKED)

TOOL = "bioemu"


def plan(run):
    return shared.plan(run, TOOL)


def tidy(run, dry_run=False):
    return shared.tidy(run, TOOL, dry_run=dry_run)


def undo(run):
    return shared.undo(run)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    flags = {a for a in args if a.startswith("--")}
    rest = [a for a in args if not a.startswith("--")]
    if len(rest) != 1 or flags - {"--undo", "--dry-run"}:
        print(__doc__)
        return 2
    run = rest[0]
    if "--undo" in flags:
        got = undo(run)
        print("tidy undo %s: %d file(s) restored from %d archive(s)"
              % (run, got["restored"], got["archives"]))
        return 0
    got = tidy(run, dry_run="--dry-run" in flags)
    if got.get("error"):
        print("tidy %s: %s" % (run, got["error"]))
        return 1
    print("tidy %s: %s" % (run, got.get("note") or "%d file(s) %s%s" % (
        got["packed"], "would be packed" if got.get("dry_run") else "packed into ",
        "" if got.get("dry_run") else got["archive"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
