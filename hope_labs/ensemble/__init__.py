"""The ensemble page: what a protein does, not what it looks like.

A folding tool answers "what shape is this sequence", once. BioEmu answers "what shapes does it
take", as a spread, and that is the question a simulation is usually asked. So this page sits
beside HOPE-MD rather than beside the folding tools: the two answer the same question by
different means, one by sampling a learned distribution in minutes and one by integrating the
physics for days, and a person who has run one will want the other to compare against.

What it is not: a replacement for a simulation. BioEmu samples structures from an equilibrium
distribution it learned; it does not produce a trajectory, so nothing here is a path through
time, no kinetics can be read off it, and the order the structures come out in means nothing.
That sentence is on the page as well, because an ensemble in a viewer looks exactly like a
trajectory and will be read as one otherwise.

Two phases, as the folding pages run (hope_labs/folding):

  1. On the login node, where there is a network: the alignment for the sequence, from the
     ColabFold server, which is what BioEmu makes its embeddings from.
  2. In the GPU job, with no network at all: the sampling, inside the tool's own image
     (hope_labs/images.py), with the weights that were fetched when it was installed.

    python -m hope_labs.ensemble.serve --host 127.0.0.1 --port 8140

It serves on a login node, for the queue and the alignment server, and prints the line the
launcher reads to know it is up (hope_labs/tools.py, URL_LINE).
"""
from __future__ import annotations

#: The tools this page serves, by their key in hope_labs/tools.py.
SERVES = ("bioemu",)

#: The letters a protein sequence may hold. BioEmu takes one chain of ordinary amino acids: it
#: has no alphabet for a nucleic acid and no way to be given a ligand, so a sequence with
#: anything else in it is refused here rather than sampled into something meaningless.
PROTEIN = set("ACDEFGHIKLMNPQRSTVWY")

#: What the model was trained within. Beyond it the samples are an extrapolation rather than a
#: prediction, and the page says so rather than refusing: somebody may want to look anyway.
TYPICAL_MAX = 500

#: The hard ceiling. Sampling scales with the square of the length in memory, and a sequence
#: this long will not fit a card the clusters here have.
MAX_LENGTH = 1200

#: The least that is worth asking for. A handful of samples says nothing about a distribution.
MIN_SAMPLES = 10
