"""Read a sampled ensemble: how wide it is, how many shapes are in it, and how populated each one
is.

By Aadhil Haq

This runs inside BioEmu's own image, in the sampling job, because that is where mdtraj is and a
login node should not grow a copy of it. The checkout is bound into the image and put on the path,
so what runs is this file rather than a script pasted into a job (job.ANALYZE).

What it writes, all under ``analysis/`` beside the samples:

    analysis.json     every number below, which is what the page and the report read
    ensemble.pdb      one model per representative shape, most populated first, for the viewer
    rmsf.dat          the spread per residue, as the folding and MD runs write theirs
    rg.dat            the radius of gyration of every sample, in the order they were drawn

Three things are measured, in the order somebody asks them.

**How wide is it.** The radius of gyration of each sample, and the distance of each sample from
the mean shape. One number per sample, so the spread can be plotted and quoted.

**How many shapes.** Samples are grouped by how far apart they are (CA RMSD after alignment),
by complete linkage at CUT nanometers, and each group's share of the samples is its population.
This is the headline: a sequence that samples one shape and a sequence that samples three say
quite different things about the protein, and the second is invisible in any single structure.

**Which residues move.** The fluctuation of each residue about the mean, which is what a B-factor
column of an ensemble ought to carry and what an MD run's RMSF is compared against.

The order the samples come out in means nothing: they are draws from a distribution, not steps
along a path. So nothing here is plotted against time, and the one number that would tempt a
reader into reading it that way - a drift, a trend, an autocorrelation - is not computed at all.

    python -m hope_labs.ensemble.analyse RUN [the structure it was given]
"""
from __future__ import annotations

import json
import os
import sys

#: How far apart two shapes have to be to count as different, in nanometers of CA RMSD after
#: alignment. 0.3 nm is the figure the ensemble literature uses for "the same fold, a different
#: arrangement"; below it two structures differ by side-chain detail this model does not claim to
#: resolve, above it they are different enough to show side by side in a viewer.
CUT = 0.3

#: The most representatives to write into ensemble.pdb, and the least share of the samples a group
#: must hold to be one of them. A viewer with forty models in it is a trajectory to the eye, which
#: is the one thing this page is trying not to look like, and a group holding one sample out of
#: five hundred is a draw rather than a state.
MOST_STATES = 8
LEAST_SHARE = 0.01

#: The most samples to build the distance matrix from. It is a square matrix, so ten thousand
#: samples is a hundred million distances: past this many the samples are thinned evenly, which
#: changes the populations by less than their own uncertainty and keeps the job inside its memory.
MATRIX_CAP = 2000


def _topology(run):
    out = os.path.join(run, "out")
    for name in ("topology.pdb", "samples.pdb"):
        path = os.path.join(out, name)
        if os.path.isfile(path):
            return path
    return ""


def _samples(run):
    out = os.path.join(run, "out")
    path = os.path.join(out, "samples.xtc")
    return path if os.path.isfile(path) else ""


def load(run):
    """The ensemble as mdtraj reads it, CA atoms only.

    CA only for every number here. The model writes a backbone and one side-chain atom per
    residue, so a heavy-atom RMSD would be a backbone RMSD with a different weighting and would
    not be comparable with the CA numbers an MD run of the same protein is quoted by.
    """
    import mdtraj
    top, xtc = _topology(run), _samples(run)
    if not top or not xtc:
        raise SystemExit("this run has no samples to read: out/samples.xtc or out/topology.pdb "
                         "is not there")
    whole = mdtraj.load(xtc, top=top)
    cas = whole.topology.select("name CA")
    if len(cas) < 3:
        raise SystemExit("the samples hold %d CA atoms, which is too few to read" % len(cas))
    return whole.atom_slice(cas), whole


def _thin(trj):
    """The samples the matrix is built from, and the stride that was used."""
    if trj.n_frames <= MATRIX_CAP:
        return trj, 1
    stride = int(trj.n_frames / MATRIX_CAP) + 1
    return trj[::stride], stride


def spread(cas):
    """The width of the ensemble: radius of gyration per sample, distance from the mean shape.

    The mean shape is the average of the aligned coordinates. It is not itself a structure
    anybody should look at - averaging a spread of shapes gives something flatter than any of
    them - so it is used only as the point the distances are measured from.
    """
    import mdtraj
    import numpy as np
    rg = mdtraj.compute_rg(cas)
    aligned = cas[:]
    aligned.superpose(aligned, 0)
    mean = aligned.xyz.mean(axis=0)
    from_mean = np.sqrt(((aligned.xyz - mean) ** 2).sum(axis=2).mean(axis=1))
    per_residue = np.sqrt(((aligned.xyz - mean) ** 2).sum(axis=2).mean(axis=0))
    return {
        "rg": [round(float(v), 4) for v in rg],
        "rg_mean": round(float(rg.mean()), 4),
        "rg_sd": round(float(rg.std()), 4),
        "from_mean": [round(float(v), 4) for v in from_mean],
        "from_mean_mean": round(float(from_mean.mean()), 4),
        "from_mean_sd": round(float(from_mean.std()), 4),
        "rmsf": [round(float(v), 4) for v in per_residue],
    }


def _distances(cas):
    """The pairwise CA RMSD matrix, in nanometers, for the thinned samples."""
    import mdtraj
    import numpy as np
    thinned, stride = _thin(cas)
    n = thinned.n_frames
    matrix = np.zeros((n, n), dtype="float32")
    for i in range(n):
        matrix[i] = mdtraj.rmsd(thinned, thinned, i)
    # The two halves of a distance matrix are the same number measured from either end, and
    # mdtraj's alignment makes them differ in the last decimal. Made symmetric so the linkage
    # below cannot order two groups by a rounding difference.
    matrix = (matrix + matrix.T) / 2.0
    return thinned, stride, matrix


def states(cas):
    """The shapes in the ensemble and what share of the samples each holds.

    Complete linkage, cut at CUT: complete rather than average or single because the question is
    "is every sample in this group the same shape as every other", and single linkage answers a
    different one, chaining two distinct shapes together through whatever lies between them.
    """
    import numpy as np
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform
    thinned, stride, matrix = _distances(cas)
    if thinned.n_frames < 2:
        return {"cut_nm": CUT, "stride": stride, "states": [
            {"rank": 1, "share": 1.0, "samples": thinned.n_frames, "frame": 0, "spread_nm": 0.0}]}
    tree = linkage(squareform(matrix, checks=False), method="complete")
    labels = fcluster(tree, t=CUT, criterion="distance")
    out = []
    for label in sorted(set(labels)):
        members = np.where(labels == label)[0]
        inside = matrix[np.ix_(members, members)]
        # The representative is the sample closest to all the others in its own group: a real
        # structure drawn by the model, not an average of several.
        center = int(members[int(inside.sum(axis=1).argmin())])
        out.append({"samples": int(len(members)),
                    "share": round(float(len(members)) / thinned.n_frames, 4),
                    "frame": center * stride,
                    "thinned_frame": center,
                    "spread_nm": round(float(inside.max()), 4)})
    out.sort(key=lambda one: (-one["samples"], one["frame"]))
    for n, one in enumerate(out, 1):
        one["rank"] = n
    return {"cut_nm": CUT, "stride": stride, "states": out,
            "widest_nm": round(float(matrix.max()), 4),
            "mean_apart_nm": round(float(matrix[np.triu_indices(len(matrix), 1)].mean()), 4)
            if len(matrix) > 1 else 0.0}


def structure(run, whole, found):
    """One model per representative shape, most populated first, as one PDB the viewer opens.

    Every atom the model wrote, not the CA subset the numbers are measured on: a viewer needs the
    backbone to draw a cartoon, and a reader comparing this with a simulation wants the same atoms
    in both.
    """
    keep = [one for one in found["states"][:MOST_STATES] if one["share"] >= LEAST_SHARE]
    if not keep:
        keep = found["states"][:1]
    frames = whole[[one["frame"] for one in keep]]
    frames.superpose(frames, 0)
    where = os.path.join(run, "analysis", "ensemble.pdb")
    frames.save_pdb(where)
    return where, keep


def secondary(run, whole):
    """What share of the samples holds each residue as helix, as sheet, or as neither.

    Read with DSSP from the backbone the model writes, which carries N, CA, C and O. Per residue
    rather than per sample, because the useful question of an ensemble is which parts of the chain
    are reliably structured and which are not.
    """
    import mdtraj
    import numpy as np
    try:
        codes = mdtraj.compute_dssp(whole, simplified=True)
    except Exception as why:                      # noqa: BLE001 - said, not raised: it is a plot
        return {"note": "the secondary structure could not be read (%s)" % why}
    shares = []
    for n in range(codes.shape[1]):
        column = codes[:, n]
        shares.append({"residue": n + 1,
                       "helix": round(float(np.mean(column == "H")), 4),
                       "sheet": round(float(np.mean(column == "E")), 4),
                       "coil": round(float(np.mean(column == "C")), 4)})
    return {"per_residue": shares}


def sequence_of(whole):
    letters = []
    for residue in whole.topology.residues:
        letters.append(residue.code or "X")
    return "".join(letters)


def against_given(cas, given):
    """How far every sample sits from the structure the run was given, in nm of CA deviation.

    The question somebody with a bound structure has, and the reason the page keeps the file
    rather than only reading a sequence off it: a peptide sampled out of a complex either stays
    near the pose it was in or does not, and that is the first thing to look at.

    Matched on the chain's own sequence rather than on residue order: the given file may start at
    residue 17 and the samples are numbered from one, so anything that counted positions would
    compare the wrong residues and say so to three decimal places.
    """
    import mdtraj
    import numpy as np
    try:
        reference = mdtraj.load(given)
    except Exception as why:                        # noqa: BLE001 - said, not raised
        return {"note": "the structure given with this run could not be read (%s)" % why}
    ref_ca = reference.atom_slice(reference.topology.select("name CA"))
    if ref_ca.n_atoms != cas.n_atoms:
        # One chain of it, where that chain is the length of what was sampled: a complex was
        # given and one of its chains is what the samples are of.
        picked = None
        for chain in ref_ca.topology.chains:
            atoms = [a.index for a in chain.atoms]
            if len(atoms) == cas.n_atoms:
                picked = ref_ca.atom_slice(atoms)
                break
        if picked is None:
            return {"note": "the structure given with this run has %d alpha carbons and the "
                            "samples have %d, and no one chain of it matches, so the two were "
                            "not compared" % (ref_ca.n_atoms, cas.n_atoms)}
        ref_ca = picked
    moved = cas[:]
    moved.superpose(ref_ca, 0)
    one = ref_ca.xyz[0]
    away = np.sqrt(((moved.xyz - one) ** 2).sum(axis=2).mean(axis=1))
    per_residue = np.sqrt(((moved.xyz - one) ** 2).sum(axis=2).mean(axis=0))
    return {
        "file": os.path.basename(given),
        "mean_nm": round(float(away.mean()), 4),
        "sd_nm": round(float(away.std()), 4),
        "closest_nm": round(float(away.min()), 4),
        "furthest_nm": round(float(away.max()), 4),
        "closest_sample": int(away.argmin()),
        "per_sample": [round(float(v), 4) for v in away],
        "per_residue": [round(float(v), 4) for v in per_residue],
    }


def analyze(run, given=""):
    """Read the samples and write everything under analysis/. Returns the document written."""
    run = os.path.abspath(run)
    os.makedirs(os.path.join(run, "analysis"), exist_ok=True)
    cas, whole = load(run)
    wide = spread(cas)
    found = states(cas)
    where, keep = structure(run, whole, found)
    doc = {
        "samples": int(cas.n_frames),
        "residues": int(cas.n_atoms),
        "sequence": sequence_of(whole),
        "spread": {k: v for k, v in wide.items() if not isinstance(v, list)},
        "states": found,
        "shown": [one["rank"] for one in keep],
        "ensemble": os.path.relpath(where, run),
        "secondary": secondary(run, whole),
    }
    if given and os.path.isfile(given):
        doc["given"] = against_given(cas, given)
    # The per-sample and per-residue arrays go beside the document rather than inside it: a
    # thousand samples is a thousand numbers three times over, and the page plots them from a
    # file the way the MD runs' plots are read.
    with open(os.path.join(run, "analysis", "rg.dat"), "w", encoding="utf-8") as handle:
        handle.write("# sample  radius of gyration (nm)  distance from the mean shape (nm)\n")
        for n, (rg, far) in enumerate(zip(wide["rg"], wide["from_mean"]), 1):
            handle.write("%d %.4f %.4f\n" % (n, rg, far))
    with open(os.path.join(run, "analysis", "rmsf.dat"), "w", encoding="utf-8") as handle:
        handle.write("# residue  fluctuation about the mean shape (nm)\n")
        for n, value in enumerate(wide["rmsf"], 1):
            handle.write("%d %.4f\n" % (n, value))
    with open(os.path.join(run, "analysis", "analysis.json"), "w", encoding="utf-8") as handle:
        json.dump(doc, handle, indent=2)
        handle.write("\n")
    return doc


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or len(args) > 2:
        print(__doc__)
        return 2
    doc = analyze(args[0], args[1] if len(args) > 1 else "")
    shapes = doc["states"]["states"]
    print("%d samples of %d residues: %s"
          % (doc["samples"], doc["residues"],
             "one shape" if len(shapes) == 1 else "%d shapes" % len(shapes)))
    for one in shapes[:MOST_STATES]:
        print("  shape %d  %5.1f%% of the samples  spread %.2f nm"
              % (one["rank"], 100 * one["share"], one["spread_nm"]))
    print("  radius of gyration %.2f nm, give or take %.2f"
          % (doc["spread"]["rg_mean"], doc["spread"]["rg_sd"]))
    given = doc.get("given") or {}
    if given.get("mean_nm") is not None:
        print("  %.2f nm from %s on average, closest %.2f"
              % (given["mean_nm"], given["file"], given["closest_nm"]))
    elif given.get("note"):
        print("  " + given["note"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
