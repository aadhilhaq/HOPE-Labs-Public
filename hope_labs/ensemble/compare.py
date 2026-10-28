"""An ensemble against a simulation of the same chain: do they say the same thing?

By Aadhil Haq

This is why the page sits beside HOPE-MD rather than beside the folding tools. The two answer one
question by different means - what shapes does this protein take, and how often - and the whole
value of having both is being able to put one against the other. Minutes against days: if the
sampled ensemble holds the shapes the simulation visits, the sampling can be trusted for the next
protein; if it does not, that is worth knowing before a week of simulation is planned on it.

What is compared, for the one chain the two have in common:

    the spread      the radius of gyration of each, mean and width, and how far apart the two
                    distributions sit
    the residues    the fluctuation of each residue in each, and the correlation between them,
                    which is the number that says whether the two agree about which parts move
    the shapes      every sampled shape, with the closest frame the simulation reached: a sampled
                    shape the simulation never came within CUT of was either not visited in the
                    time simulated or is not a shape at all

Three things this is careful not to claim. A simulation of a few hundred nanoseconds has not
visited everything, so a shape it missed is not thereby wrong. The sampling draws from an
equilibrium distribution, so its populations are comparable with a long simulation's and not with
a short one's. And neither of them is an experiment.

It runs inside BioEmu's image, where mdtraj is, as a processor job: it reads every frame of the
trajectory, which is the one part of this that takes real time.

    python -m hope_labs.ensemble.compare ENSEMBLE_RUN MD_RUN
"""
from __future__ import annotations

import glob
import json
import os
import sys

from .analyse import CUT

#: Where in a HOPE-MD run the trajectory and the topology are, newest replica first. Read in this
#: order: the stripped topology is the one that matches a production trajectory, water and ions
#: having been taken out of both.
MD_TOPOLOGIES = ("stripped.complex.prmtop", "build/complex_hmr.prmtop", "build/complex.prmtop")
MD_TRAJECTORIES = ("r*/prod_*.nc", "r*/prod*.nc", "*.nc")
#: The viewer export a HOPE-MD run writes for its own page: every tenth frame or so of the
#: production run, as a multi-model PDB. Read when no trajectory is left in the run, which is
#: what a tidied run looks like.
MD_VIEW = ("analysis/r*_view.pdb", "analysis/*view.pdb")

#: How many frames of the simulation to read. A production run is tens of thousands and the
#: comparison needs a fair sample of them, not all of them: read evenly across the whole run so
#: the thinning cannot favor the start, which is where a simulation is least settled.
FRAMES = 2000


def _folder_for(md_run):
    """The folder a comparison is kept in, named after the simulation it is against.

    Named rather than numbered, because somebody comparing one ensemble with three simulations
    wants to see which is which, and cut down to the characters a folder may hold so that a run
    named by somebody else cannot decide where this writes.
    """
    import re
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", os.path.basename(md_run.rstrip(os.sep)))
    return name.strip("_.-")[:64] or "simulation"


def _first(run, patterns):
    for pattern in patterns:
        found = sorted(glob.glob(os.path.join(run, pattern)))
        if found:
            return found[0]
    return ""


def md_files(run):
    """(the topology, the trajectory) of the run to compare against, or ("", "").

    Three kinds of folder answer, and the same two files come back from each:

    * a HOPE-MD run, which is what this was written for: a production trajectory and the stripped
      topology that matches it;
    * a HOPE-MD run that has been tidied, which keeps only the viewer export, a multi-model PDB
      carrying its own topology. Fewer frames, so the answer says which it read;
    * another ensemble run of this page's own, whose samples are a trajectory and a topology
      already. That is how a variant is put against its wild type, and it needed no new code:
      what the comparison measures of a simulation is what it measures of another ensemble.
    """
    samples = os.path.join(run, "out", "samples.xtc")
    topology = os.path.join(run, "out", "topology.pdb")
    if os.path.isfile(samples) and os.path.isfile(topology):
        return topology, samples
    top = ""
    for name in MD_TOPOLOGIES:
        if os.path.isfile(os.path.join(run, name)):
            top = os.path.join(run, name)
            break
    traj = _first(run, MD_TRAJECTORIES)
    if top and traj:
        return top, traj
    view = _first(run, MD_VIEW)
    if view:
        return view, view
    return "", ""


def kind_of(run):
    """What the other run is: another sampled ensemble, or a simulation.

    Read off the folder rather than asked for, and said in the record and on the page, because
    the sentence to write about the answer differs: a simulation visiting a sampled shape is
    evidence about the sampling, and two ensembles agreeing is evidence about neither.
    """
    if os.path.isfile(os.path.join(run, "out", "samples.xtc")) and \
            os.path.isfile(os.path.join(run, "analysis", "analysis.json")):
        return "ensemble"
    return "simulation"


def _sequence(topology):
    """The one-letter sequence of each chain in a topology, as mdtraj reads it."""
    out = []
    for chain in topology.chains:
        letters = "".join(residue.code or "" for residue in chain.residues)
        out.append({"chain": chain.index, "sequence": letters, "residues": chain.n_residues})
    return out


def _match(chains, wanted):
    """The chain whose sequence is the one that was sampled, or None.

    Matched on the sequence rather than on the chain's name or its length: HOPE-MD renames chains
    as it prepares a complex, and two chains of a dimer have the same length.
    """
    for one in chains:
        if one["sequence"] == wanted:
            return one
    for one in chains:
        if wanted and (wanted in one["sequence"] or one["sequence"] in wanted):
            return dict(one, partial=True)
    return None


def load_md(run, wanted):
    """The simulation's frames for the chain that was sampled, CA atoms only."""
    import mdtraj
    top, traj = md_files(run)
    if not top:
        raise SystemExit("%s holds no trajectory and no viewer export, so there is nothing to "
                         "compare against. A tidied run keeps analysis/r1_view.pdb; this one "
                         "keeps neither." % run)
    whole = mdtraj.load(traj, top=top) if traj != top else mdtraj.load(top)
    chains = _sequence(whole.topology)
    found = _match(chains, wanted)
    if not found:
        raise SystemExit(
            "no chain of this simulation is the sequence that was sampled.\n"
            "  sampled: %d residues, %s\n%s"
            % (len(wanted), wanted[:60] + ("..." if len(wanted) > 60 else ""),
               "\n".join("  chain %d: %d residues, %s" % (one["chain"], one["residues"],
                                                          one["sequence"][:60])
                         for one in chains)))
    picked = whole.atom_slice(whole.topology.select("chainid %d and name CA" % found["chain"]))
    if picked.n_frames > FRAMES:
        picked = picked[::int(picked.n_frames / FRAMES) + 1]
    return picked, found, os.path.relpath(traj, run), whole.n_frames


def _spread(cas):
    import mdtraj
    import numpy as np
    rg = mdtraj.compute_rg(cas)
    aligned = cas[:]
    aligned.superpose(aligned, 0)
    mean = aligned.xyz.mean(axis=0)
    rmsf = np.sqrt(((aligned.xyz - mean) ** 2).sum(axis=2).mean(axis=0))
    return rg, rmsf


def _overlap(a, b):
    """How much two distributions of one number share, from 0 to 1.

    The common area of the two histograms over the same bins: 1 when they sit on top of one
    another, 0 when they do not touch. A plain, readable measure rather than a divergence, which
    is the right thing statistically and means nothing to a reader.
    """
    import numpy as np
    both = np.concatenate([a, b])
    edges = np.histogram_bin_edges(both, bins=40)
    pa, _ = np.histogram(a, bins=edges, density=False)
    pb, _ = np.histogram(b, bins=edges, density=False)
    pa = pa / max(1, pa.sum())
    pb = pb / max(1, pb.sum())
    return float(np.minimum(pa, pb).sum())


def compare(ensemble_run, md_run):
    """Read both runs and write the comparison into the ensemble run. Returns the document."""
    import numpy as np
    from . import analyse as analysemod
    ensemble_run, md_run = os.path.abspath(ensemble_run), os.path.abspath(md_run)
    cas, whole = analysemod.load(ensemble_run)
    sampled = analysemod.sequence_of(whole)
    md, chain, traj, frames_total = load_md(md_run, sampled)

    ens_rg, ens_rmsf = _spread(cas)
    md_rg, md_rmsf = _spread(md)

    # The two chains may differ by a residue or two at the ends when the match was partial, and
    # the per-residue numbers are only comparable over what they share.
    width = min(len(ens_rmsf), len(md_rmsf))
    correlation = (float(np.corrcoef(ens_rmsf[:width], md_rmsf[:width])[0, 1])
                   if width > 2 else None)

    # Every sampled shape against the closest frame the simulation reached. The ensemble's own
    # representatives, so the rows line up with the shapes the page and the report already show.
    import mdtraj
    found = analysemod.states(cas)
    reached = []
    for one in found["states"]:
        frame = cas[one["frame"]]
        distances = mdtraj.rmsd(md, frame, 0)
        reached.append({"rank": one["rank"], "share": one["share"],
                        "closest_nm": round(float(distances.min()), 4),
                        "frame": int(distances.argmin()),
                        "visited": bool(distances.min() <= CUT)})

    doc = {
        "ensemble": {"run": ensemble_run, "name": os.path.basename(ensemble_run),
                     "samples": int(cas.n_frames), "residues": int(cas.n_atoms),
                     "sequence": sampled},
        "simulation": {"run": md_run, "name": os.path.basename(md_run),
                       "read": traj, "frames_read": int(md.n_frames),
                       "frames_in_run": int(frames_total),
                       "chain": chain["chain"], "partial_match": bool(chain.get("partial"))},
        "spread": {
            "ensemble_rg_mean": round(float(ens_rg.mean()), 4),
            "ensemble_rg_sd": round(float(ens_rg.std()), 4),
            "simulation_rg_mean": round(float(md_rg.mean()), 4),
            "simulation_rg_sd": round(float(md_rg.std()), 4),
            "rg_overlap": round(_overlap(ens_rg, md_rg), 4),
        },
        "residues": {
            "width": int(width),
            "correlation": round(correlation, 4) if correlation is not None else None,
            "ensemble_rmsf": [round(float(v), 4) for v in ens_rmsf[:width]],
            "simulation_rmsf": [round(float(v), 4) for v in md_rmsf[:width]],
        },
        "shapes": reached,
        "cut_nm": CUT,
        "visited": sum(1 for one in reached if one["visited"]),
        "of": len(reached),
        "share_visited": round(sum(one["share"] for one in reached if one["visited"]), 4),
    }
    doc["headline"] = headline(doc)
    where = os.path.join(ensemble_run, "compare", _folder_for(md_run))
    os.makedirs(where, exist_ok=True)
    with open(os.path.join(where, "compare.json"), "w", encoding="utf-8") as handle:
        json.dump(doc, handle, indent=2)
        handle.write("\n")
    doc["written"] = os.path.relpath(os.path.join(where, "compare.json"), ensemble_run)
    return doc


def headline(doc):
    """The one sentence the comparison is read by, said here so the page, the report and the
    terminal all say it the same way."""
    shapes, spread = doc["shapes"], doc["spread"]
    agree = doc["residues"].get("correlation")
    return (
        "The simulation came within %.1f nm of %d of the %d sampled shape%s, which is %.0f%% of "
        "the sampled spread. The two agree about which residues move %s (correlation %s), and "
        "their radii of gyration overlap by %.0f%%."
        % (doc["cut_nm"], doc["visited"], doc["of"], "" if doc["of"] == 1 else "s",
           100 * doc["share_visited"],
           ("closely" if (agree or 0) >= 0.7 else "in part" if (agree or 0) >= 0.4 else "little"),
           ("%.2f" % agree) if agree is not None else "not measured",
           100 * spread["rg_overlap"]))


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print(__doc__)
        return 2
    doc = compare(args[0], args[1])
    print("%s against %s" % (doc["ensemble"]["name"], doc["simulation"]["name"]))
    print("  " + doc["headline"])
    for one in doc["shapes"]:
        print("  shape %-3s %5.1f%% of samples  closest frame %.2f nm  %s"
              % (one["rank"], 100 * one["share"], one["closest_nm"],
                 "visited" if one["visited"] else "not visited"))
    print("  written to %s" % doc["written"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
