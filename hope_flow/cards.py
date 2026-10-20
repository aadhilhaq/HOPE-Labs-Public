"""The cards a flow is drawn from, and what may be linked to what.

A card is one tool. Its ports say what it needs and what it leaves, and a port carries a kind:
a target, sequences, complexes, poses. An edge is allowed only where the kinds meet, which is
what stops the two mistakes that a free-hand canvas would otherwise invite - sending a target
where sequences are wanted, and docking a binder far too large for the docking to take.

Everything a person can draw is described here and nowhere else. The canvas reads this through
the launcher, the runner reads it on the cluster, and neither holds a second opinion about which
links are legal.
"""
from __future__ import annotations

import json
import os
import re

#: What travels along an edge. The text is what the canvas shows when a link is refused, so each
#: one reads as the end of the sentence "this port carries ...".
KINDS = {
    "target":      "a receptor, with the chains and the residues a binder should touch",
    # What a docking card is given in place of the target: the same receptor, chains and site,
    # prepared once by the Prepare receptor card at the flow's pH. A kind of its own so that a
    # design card, which takes a target, can never be handed a protonated receptor by a link.
    "receptor":    "a prepared receptor, with the chains and the residues a binder should touch",
    "structure":   "a predicted structure, with no site named on it yet",
    "sequences":   "named peptide or protein sequences, without structures",
    "complexes":   "structures of a binder already placed on the target",
    "poses":       "docked poses, ranked, in a run folder",
    "prepared":    "structures prepared molecule by molecule, ready to build",
    "trajectories": "simulated complexes, with their binding energies",
}


LINKER_FAMILIES = {}

LOOP_ONLY = ()

#: The letters each kind of chain is made of, copied from hope_labs/folding/__init__.py (PROTEIN,
#: DNA and RNA), with the one letter each allows for "something, but not one of those". Copied for
#: the reason the linker families are: the canvas runs on a laptop, where the folding pages are
#: not bundled, and a sequence has to be refused while somebody is still looking at the card.
#:
#: Refused at all because neither tool refuses one. Boltz-2 maps every character it has no residue
#: for to its unknown token and folds the result, so a T in an RNA chain comes back as a confident
#: structure of something else and a stray digit comes back as a chain with a hole in it. Case is
#: the one thing that is put right rather than refused, here as on the page: it is not a
#: distinction either tool makes. A test holds this against that file wherever both are present.
FOLD_LETTERS = {
    "protein": ("ACDEFGHIKLMNPQRSTVWY", "X"),
    "dna": ("ACGT", "N"),
    "rna": ("ACGU", "N"),
}

#: How each kind is written in a sentence about it, so a refusal reads "an RNA chain" rather than
#: "a rna chain". The folding pages' own labels, from the same Entity table.
FOLD_LABELS = {"protein": "a protein chain", "dna": "a DNA strand", "rna": "an RNA chain"}

#: The longest chain of each kind either page will take, and the shortest worth folding at all,
#: from the same place.
FOLD_LONGEST = {"protein": 4000, "dna": 1000, "rna": 1000}
FOLD_SHORTEST = 4

#: The most copies of one sequence a folding card will ask for, from the same place (MAX_COPIES).
#: Copies become chains, and the chains come out of a list of twenty-six letters.
FOLD_MAX_COPIES = 20

#: How many chains one folding job can name altogether, from the same file (CHAIN_IDS): a
#: structure's chains are lettered A to Z and there is no twenty-seventh letter. Every row's
#: copies count towards it, so a co-fold of three entities with ten copies each is refused here
#: rather than by the page once the flow is launched.
FOLD_MAX_CHAINS = 26

#: How a folding card's boxes are read as a list of entities. A card is a column of single-line
#: boxes, so the list cannot be drawn as the page's own table of rows: the first entity's boxes
#: carry no suffix, which is what every flow drawn before this holds, and each further entity has
#: its own numbered three. An empty sequence box is an entity nobody asked for.
#:
#: Three because a co-fold in a flow is a receptor and what binds it - a protein with a DNA or RNA
#: strand, or with a second protein - and the third is there for a heterotrimer. A structure of
#: more different chains than that is a job for the Boltz-2 page, which draws the table properly.
ENTITY_SUFFIXES = ("", "_2", "_3")

# --- what the simulation will take, copied from HOPE-MD's own tables -------------------------
# These are hopemd/spec.py's compatibility matrix and its bounds: PROTEIN_FF, NUCLEIC_FF, WATER,
# WATER_SITES, THREE_SITE_ONLY, FIXED_TIMESTEP, FIXED_BOX, the replicates rule, the timestep rule,
# the temperature rule, the seed's ceiling and the MM-GBSA stride. Copied for the reason the
# folding alphabets are copied: the canvas runs on a laptop where HOPE-MD is not installed, and a
# card offering what the tool will refuse is a flow that fails at the far end of a queue, hours
# in, having said nothing while it was being drawn.
#
# Every one of these had a card that disagreed with it. A flow could not run Desmond at all: the
# card left the force field, the water and the box empty, which meant ff19SB, OPC and a truncated
# octahedron, and Desmond takes OPLS, a three-site water and an orthorhombic box; it offered 4 fs,
# and Desmond's RESPA scheme is fixed at 2. It offered up to twenty replicates and HOPE-MD allows
# five, any timestep in halves where it takes two numbers, and frame counts whose save interval is
# not a whole number of steps. tests/test_md_bounds.py holds all of it against HOPE-MD's own
# validate() wherever HOPE-MD is importable.
MD_PROTEIN_FF = {"amber": ("ff19SB", "ff14SB"), "openmm": ("ff19SB", "ff14SB"),
                 "gromacs": ("ff19SB", "ff14SB"), "namd": ("ff19SB", "ff14SB"),
                 "desmond": ("OPLS_2005", "OPLS4")}
MD_NUCLEIC_FF = {"amber": ("OL21+OL3", "bsc1+OL3"), "openmm": ("OL15+OL3", "bsc1+OL3"),
                 "gromacs": ("OL21+OL3", "bsc1+OL3"), "namd": ("OL21+OL3", "bsc1+OL3"),
                 "desmond": ()}
MD_WATER = {"ff19SB": ("OPC", "TIP3P", "TIP4P-Ew"), "ff14SB": ("TIP3P", "OPC", "TIP4P-Ew"),
            "OPLS4": ("SPC", "TIP3P", "TIP4P", "TIP4P-Ew"), "OPLS_2005": ("SPC", "TIP3P")}
MD_WATER_SITES = {"TIP3P": 3, "SPC": 3, "SPC/E": 3, "OPC": 4, "TIP4P": 4, "TIP4P-Ew": 4,
                  "TIP4P-D": 4}
#: NAMD 3.0.1 cannot run a four-site water from the Amber topology: it stops before minimising.
MD_THREE_SITE_ONLY = ("namd",)
MD_FIXED_TIMESTEP = {"desmond": 2.0}
MD_FIXED_BOX = {"desmond": "rectangular"}
MD_TIMESTEPS = (2.0, 4.0)
MD_REPLICATES = (1, 5)
MD_TEMPERATURE_K = (250.0, 400.0)
MD_SEED_MAX = 2000000000
#: How often MM-GBSA takes a frame. It cannot use more than every saved frame, so the save
#: interval a frame count works out to has to be this or shorter.
MD_MMGBSA_STRIDE_PS = 200
#: The box, the salt and the environment, from the same file (BOX_SHAPES, CUTOFF_A, SALTS,
#: ENVIRONMENTS). The padding floor is the cutoff: under it the complex sees its own periodic
#: image, which is the one thing validate() refuses a padding for.
MD_BOX_SHAPES = ("compact", "cubic", "rectangular")
MD_CUTOFF_A = 10.0
MD_SALTS = ("NaCl", "KCl", "MgCl2")
MD_SALT_MM = (0.0, 1000.0)
MD_ENVIRONMENTS = ("water", "membrane")
#: The bilayer, from the same file (LIPIDS, DESMOND_LIPIDS, ORIENTATIONS, MEMBRANE_PADDING_A,
#: MEMBRANE_SALTS, MEMBRANE_PROTEIN_FF, MEMBRANE_WATER, DESMOND_MEMBRANE_WATER). A bilayer build
#: has one force field, one water, one box shape and two salts, and the card writes all four for
#: you rather than offering a run HOPE-MD would refuse for every one of them.
MD_LIPIDS = ("POPC", "POPE", "DLPC", "DLPE", "DMPC", "DOPC", "DPPC")
MD_DESMOND_LIPIDS = ("POPC", "POPE", "DPPC", "DMPC")
MD_ORIENTATIONS = ("as_imported", "opm", "annotation")
MD_MEMBRANE_PADDING_A = (5.0, 40.0)
MD_MEMBRANE_SALTS = ("NaCl", "KCl")
MD_MEMBRANE_PROTEIN_FF = "ff14SB"
MD_MEMBRANE_WATER = "TIP3P"
MD_DESMOND_MEMBRANE_WATER = ("SPC", "TIP3P")
#: How a small molecule is parameterised, per engine (LIGAND_PARAMS). One choice on the four
#: Amber-topology engines and none on Desmond, so it is not a box: the driver names the engine's
#: own, because a run whose partner is a small molecule is otherwise refused for want of a
#: parameter set that has exactly one member.
MD_LIGAND_PARAMS = {"amber": ("gaff2-am1bcc",), "openmm": ("gaff2-am1bcc",),
                    "gromacs": ("gaff2-am1bcc",), "namd": ("gaff2-am1bcc",), "desmond": ()}
#: The CPU check (CPU_MAX_NS, CPU_MAX_FRAMES, CPU_ENGINES): one whole node, for when the GPU queue
#: is full and the question is only whether the pose holds for the first few ns.
MD_CPU_MAX_NS = 10.0
MD_CPU_MAX_FRAMES = 500
MD_CPU_ENGINES = ("amber", "gromacs")
#: The GPU types Grace has, fastest first, and what "auto" is spelt (site.py's GRACE.gpu_types and
#: spec.py's GPU_AUTO). Copied like the rest: the canvas has to be able to offer them on a laptop.
#: Empty is the site's default, which on Grace is a100.
MD_GPU_TYPES = ("a100", "a40", "rtx", "t4")
MD_GPU_AUTO = "auto"
#: One node's cores and the memory a whole-node job asks for on Grace (site.py: cpu_cores_per_node,
#: cpu_mem_gb). The memory floor is validate()'s: Grace refuses a job without --mem.
MD_CPUS = (1, 48)
MD_MEM_GB = (1, 180)
#: How many cards of each type one Grace node has, from site.py's own note of what gpuavail shows
#: (100 x gpu:a100:2, 15 x gpu:a40:3, 9 x gpu:rtx:2, 8 x gpu:t4:4). HOPE-MD asks only for at least
#: one GPU per replicate; a request for more cards than the node has is a gres Slurm cannot
#: satisfy and nothing in the tool refuses it, so the card holds each type to its own node.
MD_GPUS_PER_NODE = {"a100": 2, "a40": 3, "rtx": 2, "t4": 4}
#: Prepare's waters, on the two routes HOPE-MD reads them on (DROP_WATERS, KEEP_WATERS, and
#: water_choice, which reads prep.drop_waters on a complex and keep_waters on everything else).
#: One question, two field names, so the preparation card asks it once and the simulation's spec
#: carries the answer to both: see drivers._spec.
MD_DROP_WATERS = ("all", "far", "none")
MD_KEEP_WATERS = ("none", "near_ligand", "all")
MD_WATERS_BOTH_WAYS = {"all": "none", "far": "near_ligand", "none": "all"}
#: pLDDT is a percentage (Prep.trim_plddt, and validate()'s "pLDDT is 0 to 100").
MD_PLDDT = (0.0, 100.0)


def save_interval_ps(length_ns, frames):
    """The interval between frames a length and a frame count come to, as the spec holds it.

    One reading, used by the card's own checks and by the driver that writes the spec, because
    the two rules HOPE-MD applies to it - a whole number of steps, and no longer than the MM-GBSA
    stride - are rules about this number and not about the frame count anybody typed.
    """
    try:
        length_ns, frames = float(length_ns), float(frames)
    except (TypeError, ValueError):
        return None
    if length_ns <= 0 or frames <= 0:
        return None
    return max(0.1, round(length_ns * 1000.0 / frames, 3))


def md_protein_ff(settings):
    """The protein force field a simulation card means, the engine's own where it names none."""
    engine = str((settings or {}).get("engine") or "amber").strip()
    said = str((settings or {}).get("protein") or "").strip()
    return said or (MD_PROTEIN_FF.get(engine) or ("ff19SB",))[0]


def md_water(settings):
    """The water model a simulation card means, the force field's own pairing where it names none.

    The pairing rather than a default of its own: OPC is what ff19SB was fitted to and SPC is
    what Desmond's OPLS_2005 will build, and a card that left the box empty was asking for the
    first of those whichever engine it had chosen.
    """
    said = str((settings or {}).get("water") or "").strip()
    if said:
        return said
    choices = MD_WATER.get(md_protein_ff(settings)) or ("TIP3P",)
    engine = str((settings or {}).get("engine") or "amber").strip()
    if engine in MD_THREE_SITE_ONLY:
        choices = tuple(w for w in choices if MD_WATER_SITES.get(w, 3) == 3) or ("TIP3P",)
    return choices[0]


#: The longest peptide ADCP will take (adcp_dock/peptide.py, MAX_LENGTH). A design longer than
#: this cannot be docked, so an edge carrying sequences into docking is refused when the binder
#: length being asked for is above it. The number lives here as well because the canvas has to
#: answer before anything is submitted, on the laptop, where ADCP is not installed.
DOCKABLE_MAX_LENGTH = 30


class Port:
    """One socket on a card: what it carries, and whether a flow can be launched without it."""

    def __init__(self, key, label, kinds, required=True, many=False, sized=False):
        self.key = key
        self.label = label
        self.kinds = tuple(kinds)       # the kinds this port accepts, in order of preference
        self.required = required
        self.many = many                # whether several edges may arrive here
        #: True where what leaves here is as long as the target card asked a binder to be: the
        #: designed binders.
        self.sized = sized

    def takes(self, kind):
        return kind in self.kinds

    def as_json(self):
        return {"key": self.key, "label": self.label, "kinds": list(self.kinds),
                "required": self.required, "many": self.many, "sized": self.sized}


#: What `Card.site` says of a card that is always aimed at a site, whatever is filled in on it.
SITE_ALWAYS = "always"


class Card:
    """One tool on the canvas: its ports, and the settings a person may fill in on it.

    `tool` is the key the launcher's catalogue uses for the same tool, so a flow and the Tools
    page are talking about one install rather than two that happen to share a name. The target
    card has no tool: it is where the person's own input goes.
    """

    def __init__(self, key, name, tagline, tool="", inputs=(), outputs=(), settings=(), note="",
                 ready=True, site="", fixed=False):
        self.key = key
        self.name = name
        self.tagline = tagline
        self.tool = tool
        self.inputs = tuple(inputs)
        self.outputs = tuple(outputs)
        self.settings = tuple(settings)
        self.note = note
        self.site = site
        #: A card the canvas puts in by itself and will not take out again. The two preparations
        #: are these: Prepare stands on every link that carries a structure into a docking card or
        #: the simulation, and Prepare receptor between a target and the docking cards it feeds,
        #: because nothing below either may be given coordinates that are not chemistry. Neither
        #: is a card a person adds or removes.
        self.fixed = fixed
        #: Whether a flow can start this tool yet. A card that cannot is still drawn, and still
        #: says what it would do, but a flow holding one is refused before anything is queued
        #: rather than failing in a job twenty minutes later.
        self.ready = ready

    def port_in(self, key):
        return next((p for p in self.inputs if p.key == key), None)

    def port_out(self, key):
        return next((p for p in self.outputs if p.key == key), None)

    def as_json(self):
        return {"key": self.key, "name": self.name, "tagline": self.tagline, "tool": self.tool,
                "note": self.note, "ready": self.ready, "site": self.site, "fixed": self.fixed,
                "inputs": [p.as_json() for p in self.inputs],
                "outputs": [p.as_json() for p in self.outputs],
                "settings": [s.as_json() for s in self.settings]}


class Setting:
    """A field on a card. `kind` is how the canvas draws it, not how it is stored."""

    def __init__(self, key, label, kind, default=None, choices=(), why="", optional=False,
                 needed=False, advanced=False, least=None, most=None, step=None,
                 placeholder="", notes=None, varies=None):
        self.key = key
        self.label = label
        #: text | number | choice | ticks | residues | yesno | path | duration
        self.kind = kind
        self.default = default
        self.choices = tuple(choices)
        self.why = why                  # the line under the field
        self.optional = optional
        #: What a number may be. Given on every number field that has an answer: a box with no
        #: bounds takes 1e9 as readily as 10, and the person who typed it finds out from a run.
        #: They also give the arrows something to step between, which is most of why typing a
        #: number into one of these was ever necessary.
        self.least = least
        self.most = most
        self.step = step
        #: The grey words in an empty box, for the fields where empty is a real answer and means
        #: "whatever the tool would do". Those used to show a 0, which reads as none rather than
        #: as the tool's own, and the line underneath had to explain the zero away.
        self.placeholder = placeholder
        #: {choice: one line}, so a list of choices can say what each one is rather than leaving
        #: the names to be guessed or looked up.
        self.notes = dict(notes or {})
        #: What this field becomes when other fields say something: a list of (key, {value:
        #: overrides}), where overrides may set "value" (which also fixes the field and greys
        #: it), "least", "most", "placeholder" or "why". An engine that fixes its own timestep
        #: has none to set. More than one rule because a
        #: field can be governed by more than one other, and they are applied in order.
        self.varies = list(varies or ())
        #: A setting the flow will not start without. A default is not an answer here: these are
        #: the few where a wrong value is expensive and the right one is nobody's to guess, so a
        #: person says what they mean before anything is queued rather than finding out from a
        #: run that did the wrong thing for a day.
        self.needed = needed
        #: Shown only when a person asks for more. The few settings above the fold are the ones
        #: that change what a run is; these change how it is done, and a tool's own defaults are
        #: the lab's considered answer to most of them. Hiding them is not hiding the choice -
        #: it is not making somebody refuse twenty of them to get to the one they came for.
        self.advanced = advanced

    def as_json(self):
        doc = {"key": self.key, "label": self.label, "kind": self.kind, "default": self.default,
               "choices": list(self.choices), "why": self.why, "optional": self.optional,
               "needed": self.needed, "advanced": self.advanced}
        # Only what this field actually sets, so a launcher built before these existed reads a
        # catalogue the same shape it always did and draws the field it always drew.
        for name in ("least", "most", "step", "placeholder"):
            value = getattr(self, name)
            if value not in (None, ""):
                doc[name] = value
        if self.notes:
            doc["notes"] = dict(self.notes)
        if self.varies:
            doc["varies"] = [{"on": on, "cases": {case_key(k): dict(v) for k, v in cases.items()}}
                             for on, cases in self.varies]
        return doc


def case_key(value):
    """How a `varies` case is spelt in the catalogue.

    A tick is "true" or "false", a menu its own label, a number its digits. Python prints a bool
    as True and JavaScript as true, and the canvas looks a case up by what the browser calls the
    answer - so a rule keyed on a tick was written one way and read the other, and the field it
    governed never changed at all. One spelling, used on both sides, and by whoever reads a
    catalogue back.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# --- the cards -------------------------------------------------------------------------------
# The target is the one card a flow cannot do without, and in the simplest flow it is the only
# card anybody fills in: a structure, the residues to aim at, and how long the binder should be.
# Every other card takes its target through an edge rather than asking again, so the same
# receptor and the same site reach the design, the docking and the simulation by construction.

TARGET = Card(
    "target", "Target", "what everything is aimed at",
    # The one input this card has, and it is not required: a structure may arrive from a card that
    # folded one instead of being fetched or read off the cluster. What leaves here is a target
    # either way, so nothing below this card knows or cares which of the three it was.
    inputs=[Port("structure", "a folded structure", ["structure"], required=False)],
    outputs=[Port("target", "target", ["target"])],
    settings=[
        Setting("source", "Where the structure comes from", "choice", "rcsb",
                choices=["rcsb", "file", "folded"],
                why="a four-character RCSB id, a file on the cluster, or the structure a folding "
                    "card hands in"),
        # Two boxes for three sources, so each says when it is not the one being read. Without
        # it the card asks for a PDB id with no "optional" against it while the source is set to
        # a file, and marks the file - the one box that has to be filled in - as optional: the
        # panel told a person the opposite of what the flow would be refused for.
        Setting("pdb_id", "PDB id", "text", "",
                why="fetched on a login node and copied into the flow",
                varies=[("source", {
                    "file": {"optional": True, "placeholder": "not read: the structure comes "
                                                              "from the file below",
                             "why": "the structure comes from a file, so this is left alone"},
                    "folded": {"optional": True, "placeholder": "not read: the structure is "
                                                                "folded for this card",
                               "why": "the structure arrives from a folding card, so this is "
                                      "left alone"}})]),
        Setting("path", "Structure file", "path", "", optional=True,
                varies=[("source", {
                    "file": {"optional": False, "why": "the file the flow reads the receptor "
                                                       "from, on the cluster"},
                    "rcsb": {"placeholder": "not read: the structure is fetched by its id",
                             "why": "the structure is fetched by its PDB id, so this is left "
                                    "alone"},
                    "folded": {"placeholder": "not read: the structure is folded for this card",
                               "why": "the structure arrives from a folding card, so this is "
                                      "left alone"}})]),
        Setting("chains", "Chains", "text", "", optional=True,
                why="empty takes every chain in the file"),
        # Needed by the cards that aim at a site, and by nothing else. A design tool grows or
        # diffuses a binder onto named residues and cannot start without them; a blind docking, a
        # co-fold and a simulation have no site at all, and asking for one there was a box a
        # person had to fill in with numbers that nothing would read. Which cards those are is
        # said on the cards themselves (Card.site), and Flow.check() asks the flow rather than
        # this field.
        Setting("hotspots", "Hotspots", "residues", "", optional=True,
                why="residue numbers of this structure, as 54,56,66-70 or A54,B12-16. Needed by "
                    "the cards that aim at a site - the design tools and ADCP, and HADDOCK "
                    "docking when it docks by the interface. A folded "
                    "structure is numbered by the sequence it was folded from, so 54 is its "
                    "54th residue and the chains are named from A"),
        # What the design tools are asked for.
        Setting("binder_min", "Shortest designed binder, residues", "number", 70,
                least=4, most=500, step=5),
        Setting("binder_max", "Longest designed binder, residues", "number", 100,
                least=4, most=500, step=5),
    ])

# --- the two cards that fold a structure for the target to take ------------------------------
# A flow is aimed at one thing, and the target card is where that one thing is said: the chains,
# the residues a binder should touch, and how long the binder should be. These two cards sit in
# front of it and hand it the structure, so folding becomes a third way for the target card to get
# one, beside a PDB id and a file on the cluster. Nothing below the target card changes at all -
# what leaves it is a target as it always was.
#
# Which is why these cards carry no site of their own. A folded structure is a structure and
# nothing more: nobody has said where to bind on it, and the card that asks that question is the
# card that has always asked it. Having the fold answer it as well would put the site in one place
# or another depending on how a flow was drawn, and this file exists so that nothing holds a second
# opinion. The failure worth avoiding - a flow that folds and then designs against no hotspots at
# all - is avoided where it always was, on the target card, by Flow.check() refusing it before
# anything is queued.
#
# What the person has to know to fill the target card in is in their hand: a folded model is
# numbered by the sequence on the fold card. Both tools number each chain from 1 in the order the
# rows were given, and the chain ids are handed out in that same order
# (hope_labs/folding/submit.py, rows_from, which says so of the ids it shows). So hotspots of
# "54,56" on the target mean the 54th and 56th letters of the sequence that was folded.
#
# One chain each, with copies, and not a list of chains. A card's settings are single-line boxes,
# so a form that is a list of entities cannot be drawn on one; a homo-oligomer is the one
# many-chain structure that can be said in a line, so copies are offered and a complex of
# different chains stays a job for the tool's own page. Each card says so.


def _fold_settings(kinds, own, entities=1):
    """The settings both folding cards share, with each tool's own in the middle.

    `entities` is how many chains this card can be given. One is a fold: a sequence, what it is,
    and copies of it. More than one is a co-fold, and the further chains are the same three boxes
    again, numbered - which is what lets a receptor and the strand or peptide that binds it
    be folded together as one complex.
    """
    out = [
        Setting("sequence", "Sequence", "text", "", needed=True,
                why="one chain, in one-letter codes. Case does not matter - neither tool makes "
                    "the distinction, so a sequence pasted in lower case is read as upper - but "
                    "a letter that is not a residue does, and is refused here rather than folded "
                    "as nothing"),
    ]
    if len(kinds) > 1:
        out.append(Setting("entity", "What it is", "choice", kinds[0], choices=kinds,
                           why="the alphabet the sequence is checked against, and the model used "
                               "for it"))
    out += [
        Setting("copies", "Copies", "number", 1, optional=True, least=1, most=20, step=1,
                why="more than one folds a homo-oligomer: the same sequence again as chains A, "
                    "B, C"),
    ]
    #: The further chains, in the words of the first three. Ordinals rather than numbers in the
    #: labels, because "Sequence 2" beside "Copies 2" reads as two of something.
    ordinals = ("", "second", "third")
    for n in range(2, min(entities, len(ENTITY_SUFFIXES)) + 1):
        suffix, which = ENTITY_SUFFIXES[n - 1], ordinals[n - 1]
        out += [
            Setting("sequence" + suffix, "Sequence of the %s chain" % which, "text", "",
                    optional=True, advanced=(n > 2),
                    placeholder="empty folds without it",
                    why="fill this in to fold a complex rather than one chain: the two are folded "
                        "together, in the order of the boxes, and what comes out is a complex"),
            Setting("entity" + suffix, "What the %s one is" % which, "choice", kinds[0],
                    choices=kinds, optional=True, advanced=(n > 2),
                    why="the alphabet it is checked against, and the model used for it"),
            Setting("copies" + suffix, "Copies of the %s chain" % which, "number", 1,
                    optional=True, advanced=(n > 2), least=1, most=20, step=1),
        ]
    out += [
        # The one setting that decides what happens where, and it is the same question on both
        # pages, asked in the same words. Fetching costs the Launch press a minute or two per
        # chain on the login node, because a compute node cannot reach the server at all.
        Setting("alignments", "Alignments", "choice", "fetch", choices=["fetch", "single"],
                why="fetch asks the alignment server for one per protein chain while the flow is "
                    "being queued, on the login node, which is the only place that can reach it. "
                    "single folds from the sequence alone: nothing is fetched, and the structure "
                    "is markedly worse"),
    ] + list(own) + [
        Setting("gpu", "Card", "choice", "auto",
                choices=["auto", "a100", "a40", "rtx", "t4", "h100", "a30"],
                optional=True, advanced=True,
                why="auto takes the largest card this cluster has. A card the cluster has not got "
                    "is refused before anything is queued"),
        Setting("walltime", "Walltime", "duration", "04:00:00", optional=True, advanced=True,
                least=600, most=2 * 24 * 3600, step=1800,
                why="importing the tool takes the first five minutes of it. A job that ends early "
                    "is charged for what it used, so round up rather than cut it fine"),
        Setting("account", "Account to charge", "text", "", optional=True, advanced=True),
    ]
    return out


BOLTZ2 = Card(
    "boltz2", "Boltz-2", "fold a sequence, or co-fold a complex",
    tool="boltz2",
    # One socket carrying either kind, because which it is is not the drawing's question but the
    # card's: one chain folded is a structure, and two chains folded together are a complex. The
    # adapter says which when the fold has run, by what was asked of it.
    outputs=[Port("structure", "folded structure or complex", ["structure", "complexes"])],
    settings=_fold_settings(
        # Protein, DNA and RNA, and not the ligand its page also offers: a lone small molecule is
        # not a receptor with chains and residues in it, and that is what the target card below
        # this one is describing.
        ["protein", "dna", "rna"],
        [# Its page's own field and its own name for it, so the job reads it without translation.
         # More than one is a separate sample of the same model: all of them are handed on, best
         # first, and what takes them decides how many to use.
         Setting("diffusion_samples", "Structures to fold", "number", 1, optional=True,
                 least=1, most=25, step=1,
                 why="each is a separate sample of the same model, and five takes about five "
                     "times as long. They are handed on best first, and the card below takes the "
                     "best few of them"),
         Setting("recycling_steps", "Recycling steps", "number", 3, optional=True, advanced=True,
                 least=1, most=10, step=1,
                 why="Boltz-2's own default. More costs time roughly in proportion")],
        entities=3),
    note="One sequence folds one structure, for a target card to take. Fill in a second sequence "
         "and the two are folded together as a complex - a receptor with the DNA, RNA or "
         "peptide that binds it - which goes on to be prepared and simulated. A structure of more chains "
         "than three is a job for the Boltz-2 page.")

COLABFOLD = Card(
    "colabfold", "ColabFold", "fold a protein chain into a structure",
    tool="colabfold",
    outputs=[Port("structure", "folded structure", ["structure"])],
    # Protein and nothing else, because ColabFold has no model for anything else: it would take a
    # nucleic acid only by reading the letters as amino acids.
    settings=_fold_settings(
        ["protein"],
        [Setting("num_models", "Models", "number", 5, optional=True, advanced=True,
                 least=1, most=5, step=1,
                 why="all five is ColabFold's own default. They are ranked and the best is the "
                     "one handed on"),
         Setting("num_recycle", "Recycling steps", "number", 3, optional=True, advanced=True,
                 least=1, most=12, step=1,
                 why="what the lab's co-folding has always asked for")]),
    note="Folds the sequence on the card with AlphaFold 2 and hands the best-ranked model to a "
         "target card, which is where the chains and the hotspots are named.")

BINDCRAFT = Card(
    "bindcraft", "BindCraft2", "design a miniprotein binder",
    tool="bindcraft", site=SITE_ALWAYS,
    inputs=[Port("target", "target", ["target"])],
    # Two ways out, which is the whole of the choice a person has here. A design arrives with the
    # binder already placed on the target, so it can be simulated as it stands; its sequence can
    # instead be docked, but only where the binder is short enough for the docking to take it.
    outputs=[Port("complexes", "designed complexes", ["complexes"]),
             Port("sequences", "design sequences", ["sequences"], sized=True)],
    settings=[
        Setting("designs", "Stop when this many designs pass", "number", 10, needed=True,
                least=1, most=500, step=1,
                why="a campaign runs until it has this many, or until its walltime ends"),
        Setting("gpu", "Card", "choice", "auto",
                choices=["auto", "a100", "a40", "rtx", "t4"], optional=True,
                why="auto picks a card the complex fits in"),
        Setting("walltime", "Walltime", "duration", "24:00:00", optional=True,
                least=3600, most=7 * 24 * 3600, step=3600,
                why="a campaign that runs out carries on when it is queued again"),
        Setting("coldspots", "Coldspots", "residues", "", optional=True, advanced=True,
                why="residues to keep clear of"),
        Setting("forced", "Focus on the hotspots", "choice", "auto",
                choices=["auto", "yes", "no"], optional=True, advanced=True,
                why="the surface outside them is rebuilt so a binder cannot settle elsewhere; "
                    "left on auto it focuses whenever hotspots are given"),
        Setting("max_trajectories", "Give up after this many attempts", "number", "",
                optional=True, advanced=True, least=1, most=100000, step=100,
                why="a campaign that will never find its designs otherwise runs to its walltime"),
        Setting("gpus", "Cards", "number", 1, optional=True, advanced=True,
                least=1, most=4, step=1,
                why="one campaign over this many cards on the one node"),
        Setting("account", "Account to charge", "text", "", optional=True, advanced=True),
    ],
    note="Designs come out already placed on the target, so docking them again is optional.")

# The machine-learning design tools. Each is the same shape as BindCraft2 - a target in, a
# binder placed on it out - and each differs from it in the same way: they have no page of their
# own on the Tools screen, so a flow is the only way the lab runs them.
RFDIFFUSION = Card(
    "rfdiffusion", "RFdiffusion", "design a binder backbone, then its sequence",
    tool="rfdiffusion", site=SITE_ALWAYS,
    inputs=[Port("target", "target", ["target"])],
    # Same two ways out as BindCraft2, and for the same reason: RFdiffusion keeps the target in
    # the structure it writes, so a design is a complex already, and ProteinMPNN is what gives it
    # a sequence to dock.
    outputs=[Port("complexes", "designed complexes", ["complexes"]),
             Port("sequences", "design sequences", ["sequences"], sized=True)],
    settings=[
        Setting("designs", "Backbones to generate", "number", 10, needed=True,
                least=1, most=500, step=1,
                why="each is a separate diffusion, and the whole run is this many"),
        Setting("seqs_per_backbone", "Sequences per backbone", "number", 8, needed=True,
                least=1, most=64, step=1,
                why="ProteinMPNN designs this many for each backbone, and the best is kept"),
        Setting("gpu", "Card", "choice", "auto",
                choices=["auto", "a100", "a40", "rtx"], optional=True),
        Setting("walltime", "Walltime", "duration", "12:00:00", optional=True,
                least=1800, most=2 * 24 * 3600, step=1800),
        # RFdiffusion's own knobs. The defaults are the ones its binder-design example uses, and
        # changing them changes what the diffusion does rather than how it is run.
        # Not RFdiffusion's own default, which is 1: the binder-design example lowers it to 0, and
        # that is what makes the backbones worth giving a sequence to. Raising it is how to ask
        # for more varied designs, at the cost of how many of them hold up.
        Setting("noise_scale", "Noise scale", "number", 0, optional=True, advanced=True,
                least=0, most=2, step=0.1,
                why="0 is what the binder-design example uses, and is a real answer rather than "
                    "an empty one. Higher is more varied and fewer of them fold"),
        # Fifteen, not ten: RFdiffusion asserts T >= 15 while it builds its noise schedule, so
        # anything lower takes the card, loads the weights and dies on an assertion with nothing
        # written. The bound is the page's as well - the form is drawn from this card - so the
        # number is refused while it is being typed rather than by a job an hour later.
        Setting("diffuser_T", "Diffusion steps", "number", 50, optional=True, advanced=True,
                least=15, most=200, step=5,
                why="fewer is faster and rougher, and RFdiffusion itself asserts at least 15"),
        Setting("ckpt", "Checkpoint", "choice", "",
                choices=["", "Complex_base_ckpt.pt", "Complex_beta_ckpt.pt"],
                optional=True, advanced=True, why="empty takes the binder-design default"),
        # There is no "relax each design before scoring" here, and that is deliberate. It was
        # offered and did nothing: relaxing needs dl_binder_design's own interface script, which
        # needs a PyRosetta licence this install has not got. Somebody would have set it, watched
        # a twelve-hour run and concluded it made no difference to the science, rather than that
        # it had never been passed on. If the licence is ever bought, the driver and the helper
        # have to learn it before this comes back.
        Setting("partition", "Partition", "text", "", optional=True, advanced=True),
        Setting("account", "Account to charge", "text", "", optional=True, advanced=True),
    ],
    note="Designs come out placed on the target, so docking them again is optional.")

RFD3 = Card(
    "rfd3", "RFdiffusion3", "design a binder, every atom at once",
    tool="rfd3", site=SITE_ALWAYS,
    inputs=[Port("target", "target", ["target"])],
    # The same two ways out as RFdiffusion, so whatever could follow the one can follow the
    # other: the target stays in the structure, which makes each design a complex already, and
    # the sequence is what the docking cards downstream need.
    outputs=[Port("complexes", "designed complexes", ["complexes"]),
             Port("sequences", "design sequences", ["sequences"], sized=True)],
    settings=[
        # Designs come out of one diffusion pass rather than a loop, so this is a batch size:
        # a larger number costs GPU memory rather than wall clock. Eight is the model's own
        # default and what its published demo runs.
        Setting("designs", "Designs to generate", "number", 8, needed=True,
                least=1, most=64, step=1,
                why="generated together in one pass, so this costs GPU memory rather than time"),
        Setting("seqs_per_backbone", "Sequences per design", "number", 8, needed=True,
                least=1, most=64, step=1,
                why="RFdiffusion3 writes a sequence of its own, and MPNN is still what the "
                    "authors' own pipeline redesigns protein backbones with before ordering"),
        Setting("gpu", "Card", "choice", "auto",
                choices=["auto", "a100", "a40", "rtx"], optional=True),
        Setting("walltime", "Walltime", "duration", "12:00:00", optional=True,
                least=1800, most=2 * 24 * 3600, step=1800),
        # The sampler's own knobs, under the names its configuration uses, so that somebody
        # reading the upstream documentation finds the same words on this card.
        Setting("step_scale", "Step scale", "number", 1.5, optional=True, advanced=True,
                least=0.5, most=3.0, step=0.1,
                why="the diversity knob. Higher is less varied and more of them hold up"),
        Setting("num_timesteps", "Diffusion steps", "number", 200, optional=True, advanced=True,
                least=20, most=500, step=10,
                why="fewer is faster and rougher"),
        Setting("cfg", "Classifier-free guidance", "yesno", False, optional=True, advanced=True,
                why="holds a design closer to what was asked of it. Off is the model's own "
                    "default, and it is turned on for conditioning that must be obeyed"),
        Setting("partition", "Partition", "text", "", optional=True, advanced=True),
        Setting("account", "Account to charge", "text", "", optional=True, advanced=True),
    ],
    note="Designs come out placed on the target, as RFdiffusion's do.")

BOLTZGEN = Card(
    "boltzgen", "BoltzGen", "generate a binder against the target",
    tool="boltzgen", site=SITE_ALWAYS,
    inputs=[Port("target", "target", ["target"])],
    outputs=[Port("complexes", "designed complexes", ["complexes"]),
             Port("sequences", "design sequences", ["sequences"], sized=True)],
    settings=[
        # The protocol is the whole of what this tool is asked: a peptide and a nanobody against
        # the same target are different runs of the same model, not different settings of one.
        Setting("protocol", "What to design", "choice", "protein-anything",
                choices=["protein-anything", "peptide-anything", "nanobody-anything",
                         "antibody-anything", "protein-small_molecule", "protein-redesign"],
                needed=True, why="the model's own protocols; the binder kind is chosen here"),
        Setting("designs", "Designs to generate", "number", 10, needed=True,
                least=1, most=500, step=1),
        # 16 GB is not enough: upstream reports running out of memory on a modest target, and on
        # the analysis step at a hundred designs. So the small cards are not offered here.
        Setting("gpu", "Card", "choice", "a100", choices=["a100", "a40"], optional=True,
                why="BoltzGen needs 40 GB or more; the smaller cards run out"),
        Setting("walltime", "Walltime", "duration", "12:00:00", optional=True,
                least=1800, most=2 * 24 * 3600, step=1800),
        Setting("cyclic", "Cyclic peptide", "yesno", False, optional=True, advanced=True,
                why="peptide protocols only"),
        # The key is still sampling_steps because saved flows carry it under that name, and
        # renaming it would quietly drop the setting out of every flow already drawn. What it is
        # is BoltzGen's --step_scale, which scales the diffusion step rather than counting steps,
        # and the label says so: the old one read as a number of steps and it is not one.
        Setting("sampling_steps", "Step scale", "number", None, optional=True, advanced=True,
                least=0.1, most=10, step=0.1, placeholder="the protocol's own",
                why="it scales the diffusion step, and is not a count of steps"),
        Setting("fold", "Fold and score each design", "yesno", True, optional=True, advanced=True,
                why="the ranking comes from this; off leaves designs unranked"),
        Setting("partition", "Partition", "text", "", optional=True, advanced=True),
        Setting("account", "Account to charge", "text", "", optional=True, advanced=True),
    ],
    note="Generates binders against a target of any kind, protein, peptide, nucleic acid or "
         "small molecule, and leaves them already placed on it.")

ADCP = Card(
    "adcp", "ADCP docking", "dock peptides into the target",
    tool="adcp", site=SITE_ALWAYS,
    inputs=[Port("target", "prepared receptor", ["receptor"]),
            Port("sequences", "peptides to dock", ["sequences"], many=True)],
    outputs=[Port("poses", "docked poses", ["poses"])],
    settings=[
        Setting("poses", "Poses to keep per peptide", "number", 10, optional=True,
                least=1, most=100, step=1,
                why="ranked, best first; the simulation takes its best from these, and the "
                    "rescoring cannot take more poses than are kept"),
        Setting("replicas", "Replicas", "number", 50, optional=True,
                least=1, most=1000, step=10,
                why="how many independent searches per peptide"),
        # A ring is the one thing about a peptide that no design card upstream can say: the
        # sequences arrive linear and ADCP closes them, head to tail or through a disulfide. The
        # lab's macrocycle work could not be docked as a ring from a flow at all. A peptide that
        # cannot close the ring asked for is left behind by name, with the reason, rather than
        # refusing the whole run as the docking's own command line does.
        Setting("cyclic", "Dock each peptide as", "choice", "none", optional=True,
                choices=["none", "backbone", "disulfide"],
                notes={"none": "a linear chain, which is how every design arrives",
                       "backbone": "a ring closed head to tail (ADCP's -cyc)",
                       "disulfide": "a ring through a disulfide between two of its cysteines "
                                    "(-cys); a peptide with fewer than two is left behind"}),
        # Where the grid box goes. The card had only the hotspots, which is the residues mode;
        # the other two are ADCP's own, and the ligand one is how a peptide already in the
        # structure sets the box - the macrocycle in 5O45's chain B - which is a site no list of
        # residues describes as well as the thing sitting in it.
        Setting("site_mode", "Build the box around", "choice", "residues", optional=True,
                choices=["residues", "ligand", "pocket"],
                notes={"residues": "the hotspots named on the target card",
                       "ligand": "a ligand or peptide in the receptor, named below, which is "
                                 "kept out of the receptor it sets the box for",
                       "pocket": "the pocket AutoSite finds"},
                why="the hotspots are still asked for on the target card, and the other two modes "
                    "do not read them"),
        Setting("site_ligand", "The ligand or peptide the box is built around", "text", "",
                optional=True, placeholder="not read: the box comes from the hotspots",
                why="read only while the box is built around a ligand",
                varies=[("site_mode", {
                    "ligand": {"optional": False, "placeholder": "",
                               "why": "a peptide's chain, as B, or a hetero group as "
                                      "CHAIN:NAME:NUMBER, as A:NUT:201"},
                    "pocket": {"placeholder": "not read: the box comes from AutoSite's pocket",
                               "why": "the box comes from a pocket, so this is left alone"}})]),
        Setting("site_pocket", "Which pocket", "choice", "best", optional=True, advanced=True,
                choices=["best", "all"],
                notes={"best": "AutoSite's best scoring pocket",
                       "all": "every pocket it finds, in one box"},
                varies=[("site_mode", {
                    "residues": {"why": "read only while the box is built around a pocket"},
                    "ligand": {"why": "read only while the box is built around a pocket"}})]),
        Setting("mmgbsa", "Score the best poses with MM-GBSA", "yesno", True, optional=True,
                why="slower, and the more considered estimate of binding"),
        # The three the rescoring is actually set by, and the number it reports. Each is ADCP's
        # own flag with ADCP's own default: without them a flow could only have the rescoring on
        # or off, and never on GBn2 or through OpenMM, so a flow's numbers could not be put beside
        # a run made from the page with anything but the one model.
        Setting("mmgbsa_poses", "Poses to rescore per peptide", "number", 5, optional=True,
                advanced=True, least=1, most=10, step=1,
                why="of the poses kept above; the docking refuses more than it keeps"),
        Setting("mmgbsa_model", "Solvation model", "choice", "obc2", optional=True, advanced=True,
                choices=["obc2", "gbn2"],
                notes={"obc2": "GB-OBC(II), ADCP's own default and what every earlier run used",
                       "gbn2": "GBn2, with its own radii"},
                why="both run in AmberTools; compare peptides only within one model"),
        Setting("mmgbsa_engine", "MM-GBSA engine", "choice", "amber", optional=True,
                advanced=True, choices=["amber", "openmm", "schrodinger"],
                notes={"amber": "AmberTools, the reference every earlier number came from",
                       "openmm": "the same force field and geometry solved again",
                       "schrodinger": "Prime, with OPLS and VSGB 2.0, on its own scale"},
                why="each writes its own column, and none is comparable with another by value"),
        Setting("top_n", "Report the mean of the best", "number", 5, optional=True,
                advanced=True, least=1, most=50, step=1,
                why="poses per peptide, which is the number the results table ranks on"),
        Setting("helical", "Replicas that start helical, %", "number", 25, optional=True,
                advanced=True, least=0, most=100, step=5,
                why="the rest start as coil; 0 starts every one as coil"),
        Setting("steps", "Steps per replica", "number", None, optional=True, advanced=True,
                least=100000, most=50000000, step=500000,
                placeholder="the docking's own: 2,500,000",
                why="the length of each search; longer costs time in proportion"),
        Setting("omm_minimize", "Poses to minimise with OpenMM", "number", 0, optional=True,
                advanced=True, least=0, most=10, step=1,
                why="ADCP's own minimisation of its top poses, after the docking; 0 does none"),
        Setting("omm_env", "Minimise in", "choice", "implicit", optional=True, advanced=True,
                choices=["implicit", "vacuum"],
                notes={"implicit": "implicit solvent, which is ADCP's own default",
                       "vacuum": "no solvent at all"},
                why="the solvent the minimisation above is run in",
                varies=[("omm_minimize", {0: {"why": "read only while poses are being minimised"},
                                          "": {"why": "read only while poses are being "
                                                      "minimised"}})]),
        # The two maps settings a flow could not reach. Both are ADCP's own flags, and the
        # gradient is off by its authors' own finding rather than by an oversight.
        Setting("autosite", "AutoSite", "choice", "1.1", optional=True, advanced=True,
                choices=["1.1", "1.0"],
                why="which AutoSite places the translation points and finds the pockets"),
        Setting("receptor_gradient", "Add the receptor gradient", "yesno", False, optional=True,
                advanced=True,
                why="AGFR's -g: a slope towards the surface inside the receptor. Off because it "
                    "costs time and ADCP's authors found it did not help"),
        Setting("keep_trajectories", "Keep every replica's trajectory", "yesno", False,
                optional=True, advanced=True,
                why="one archive per peptide; tens of thousands of files otherwise thrown away"),
        Setting("cpus", "Cores", "number", 48, optional=True, advanced=True,
                least=1, most=96, step=1,
                why="ADCP searches in parallel, one replica per core"),
        Setting("walltime", "Walltime", "duration", "08:00:00", optional=True, advanced=True,
                least=1800, most=2 * 24 * 3600, step=1800,
                why="per peptide: each is one task of the array, not the run as a whole"),
        Setting("throttle", "Most tasks at once", "number", 0, optional=True, advanced=True,
                least=0, most=200, step=1,
                why="of the peptide array; 0 lets the queue run as many as it will"),
        # The placeholder here used to say "the docking's own", and the docking has none: it is
        # left blank on purpose, so that a cluster naming its partitions differently is not sent
        # one that does not exist (adcp_dock/job.py). Empty means the scheduler's own default.
        Setting("partition", "Partition", "text", "", optional=True, advanced=True,
                placeholder="the cluster's own default",
                why="the docking names none of its own; empty lets the scheduler choose"),
        Setting("account", "Account to charge", "text", "", optional=True, advanced=True,
                why="where the cluster requires one"),
    ],
    note="Takes peptides of %d residues or fewer." % DOCKABLE_MAX_LENGTH)

HADDOCK = Card(
    "haddock", "HADDOCK docking", "dock proteins, DNA or RNA onto the target, by its interface",
    tool="haddock", site="use_site",
    inputs=[Port("target", "prepared receptor", ["receptor"]),
            Port("partner", "prepared partners", ["prepared"], many=True)],
    outputs=[Port("complexes", "docked complexes", ["complexes"])],
    settings=[
        Setting("mode", "Docking mode", "choice", "auto", optional=True,
                choices=["auto", "protein-protein", "protein-DNA", "protein-RNA"],
                notes={"auto": "protein-DNA or protein-RNA when a partner is DNA or RNA, "
                               "protein-protein otherwise",
                       "protein-protein": "HADDOCK's standard settings",
                       "protein-DNA": "HADDOCK's protein-DNA protocol",
                       "protein-RNA": "the same nucleic acid settings, for RNA"}),
        Setting("use_site", "Drive the docking by the target's hotspots", "yesno", True,
                optional=True,
                why="the hotspots named on the target card become the receptor's active residues, "
                    "and each partner's whole surface is passive; untick to dock ab initio"),
        # One box served every partner, so a protein partner and a two-chain DNA duplex could not
        # be docked in one card: whatever was typed went to both. The first partner's box carries
        # no suffix, which is what every flow drawn before this holds, and a partner with no box
        # of its own takes it - so a card filled in as it always was docks as it always did.
        # Three, as the folding cards offer three entities: a run of more partners than that is
        # several cards, or the HADDOCK page, which draws the list properly.
        Setting("partner_chains", "Partners' chains", "text", "", optional=True,
                placeholder="every protein, DNA and RNA chain folded",
                why="as A,B; empty docks the whole of what was folded, and a partner with no box "
                    "of its own takes this one"),
        Setting("partner_chains_2", "Second partner's chains", "text", "", optional=True,
                advanced=True, placeholder="the box above",
                why="as A,B, for the second partner to arrive"),
        Setting("partner_chains_3", "Third partner's chains", "text", "", optional=True,
                advanced=True, placeholder="the box above",
                why="as A,B, for the third partner to arrive"),
        # The interface, per molecule. The driver forced every partner to a passive surface and
        # the receptor to the surface around its hotspots, so a known epitope on a partner could
        # not be named at all: the one thing HADDOCK is driven by, and the one thing a flow could
        # not say. Written as the page writes them, "A:45, A:48-52".
        Setting("partner_active", "Partners' active residues", "residues", "", optional=True,
                placeholder="nothing known: the whole surface is passive",
                why="residues of the partner believed to be at the interface, as A:45,A:48-52; a "
                    "partner with no box of its own takes this one"),
        Setting("partner_active_2", "Second partner's active residues", "residues", "",
                optional=True, advanced=True, placeholder="the box above",
                why="for the second partner to arrive"),
        Setting("partner_active_3", "Third partner's active residues", "residues", "",
                optional=True, advanced=True, placeholder="the box above",
                why="for the third partner to arrive"),
        Setting("partner_passive", "Partners' passive residues", "choice", "auto", optional=True,
                advanced=True, choices=["auto", "around", "surface", "list", "none"],
                notes={"auto": "the surface around its own active residues where it has any, "
                               "its whole surface where it has none",
                       "around": "the surface within 6.5 A of its active residues, as HADDOCK's "
                                 "web server chooses them",
                       "surface": "the whole surface, for a partner nothing is known about",
                       "list": "the residues named below",
                       "none": "none"}),
        Setting("partner_passive_list", "Partners' passive residues, named", "residues", "",
                optional=True, advanced=True,
                placeholder="read only while the passive residues are a list",
                why="as A:30,A:31",
                varies=[("partner_passive", {"list": {
                    "optional": False, "placeholder": "",
                    "why": "the partner's own passive residues, as A:30,A:31"}})]),
        Setting("receptor_passive", "Receptor's passive residues", "choice", "auto",
                optional=True, advanced=True,
                choices=["auto", "around", "surface", "list", "none"],
                notes={"auto": "the surface around the hotspots while they drive the docking, "
                               "its whole surface where a partner's residues drive it instead",
                       "around": "the surface within 6.5 A of the hotspots",
                       "surface": "the whole surface",
                       "list": "the residues named below",
                       "none": "none"}),
        Setting("receptor_passive_list", "Receptor's passive residues, named", "residues", "",
                optional=True, advanced=True,
                placeholder="read only while the passive residues are a list",
                why="residues of the prepared receptor, as A:30,A:31",
                varies=[("receptor_passive", {"list": {
                    "optional": False, "placeholder": "",
                    "why": "residues of the prepared receptor, as A:30,A:31"}})]),
        Setting("partner_dotbracket", "Partners' secondary structure", "text", "", optional=True,
                placeholder="none: HADDOCK keeps the fold the structure has",
                why="dot-bracket, one character a nucleotide in the order docked and \"&\" "
                    "between strands; its base pairs are then held through every refinement. One "
                    "box for every partner, so a protein partner beside an RNA one is refused "
                    "by name"),
        Setting("preset", "Sampling", "choice", "standard", optional=True,
                choices=["quick", "standard", "thorough"],
                notes={"quick": "200 rigid-body models, 40 refined: for checking a flow",
                       "standard": "1000 rigid-body models, 200 refined: HADDOCK's own default",
                       "thorough": "2000 rigid-body models, 400 refined: for ab initio docking"}),
        # Past the three presets. Each empty takes the preset's own number, which is what the
        # placeholders say; filled in, they are HADDOCK's sampling and select, and more cannot be
        # refined than were sampled, which the docking says before anything is queued.
        Setting("sampling", "Rigid-body models made", "number", None, optional=True,
                advanced=True, least=10, most=50000, step=100,
                placeholder="the sampling above",
                why="past the three presets: HADDOCK's own sampling"),
        Setting("select", "Of those, how many are refined", "number", None, optional=True,
                advanced=True, least=1, most=50000, step=50,
                placeholder="the sampling above",
                why="no more than were sampled, which the docking refuses before it queues"),
        Setting("clustering", "How models are grouped into clusters", "choice", "fcc",
                optional=True, advanced=True, choices=["fcc", "rmsd"],
                notes={"fcc": "by shared contacts, HADDOCK's own default",
                       "rmsd": "by RMSD"}),
        Setting("clust_cutoff", "How alike two models must be to share a cluster", "number",
                None, optional=True, advanced=True, least=0.0, most=100.0, step=0.1,
                placeholder="HADDOCK's own: 0.6 shared contacts",
                why="a fraction of shared contacts for fcc, angstroms for rmsd",
                varies=[("clustering", {"rmsd": {
                    "placeholder": "HADDOCK's own: 7.5 A",
                    "why": "in angstroms, since the clustering is by RMSD"}})]),
        Setting("min_population", "Fewest models a cluster may have", "number", None,
                optional=True, advanced=True, least=1, most=9999, step=1,
                placeholder="the sampling above: 4, or 2 for a quick check"),
        # Browser-side only on the page, which ticks it for any mode but protein-protein
        # (static/app.js). A flow sent no options at all, so a docking of protein with DNA or RNA
        # quietly lost the water refinement the page would have given it.
        Setting("water", "Refine the final models in water", "yesno", False, optional=True,
                why="slower; HADDOCK's page ticks it for a DNA or RNA partner, and this card is "
                    "the only place a flow can ask for it",
                varies=[("mode", {
                    "protein-DNA": {"why": "slower; the page ticks it for a DNA partner, as "
                                           "HADDOCK's web server ends its protocol"},
                    "protein-RNA": {"why": "slower; the page ticks it for an RNA partner, as "
                                           "HADDOCK's web server ends its protocol"}})]),
        # HADDOCK's score ranks the models of one run well and was not made to compare one
        # complex with another, which is exactly what this card hands on: the complexes of every
        # partner, ranked together. Off is the tool's own answer and the lab's is usually on.
        Setting("mmgbsa", "Rescore the top poses with MM-GBSA", "yesno", False, optional=True,
                why="the only way to compare one partner with another: HADDOCK's own score was "
                    "not made to. It needs AmberTools, and says so before anything is queued"),
        Setting("mmgbsa_engines", "MM-GBSA engines", "ticks", "amber", optional=True,
                advanced=True, choices=["amber", "openmm", "prime"],
                notes={"amber": "AmberTools, always run: its minimised pose is what the others "
                                "score",
                       "openmm": "the same topology and geometry, OpenMM's own GBn2",
                       "prime": "Schrodinger's MM-GBSA, OPLS with VSGB"},
                placeholder="AmberTools alone",
                why="each writes its own column, and none is comparable with another by value",
                varies=[("mmgbsa", {False: {"why": "switch the rescoring on above to choose "
                                                   "which engines score the poses"}})]),
        Setting("mmgbsa_poses", "Poses rescored", "choice", "12", optional=True, advanced=True,
                choices=["3", "12", "20"],
                notes={"3": "the best of each of the top 3 clusters, the quickest",
                       "12": "the best 4 of each of the top 3 clusters",
                       "20": "the best 4 of each of the top 5 clusters"},
                varies=[("mmgbsa", {False: {"why": "read only while the rescoring is on"}})]),
        Setting("cofold", "Co-fold each pair with Boltz-2 and compare it", "yesno", False,
                optional=True, advanced=True,
                why="a second, independent answer to where the partner sits, beside the docking"),
        # Three is HADDOCK's own MAX_REPLICATES, and a test holds this against it.
        Setting("replicates", "Dock each partner this many times", "number", 1, optional=True,
                advanced=True, least=1, most=3, step=1,
                why="each with a random seed of its own, which is how a pose is told from a "
                    "lucky one; each replicate is a Slurm task of its own"),
        Setting("seed", "Random seed", "number", None, optional=True, advanced=True,
                least=0, most=10 ** 9, step=1, placeholder="HADDOCK's own, 917",
                why="the first seed; replicates take it and 1000 more each"),
        Setting("restraints_file", "Your own restraints file", "path", "", optional=True,
                advanced=True, placeholder="none: the restraints come from the residues above",
                why="a .tbl of assign lines on the cluster, added as unambiguous restraints"),
        Setting("partner_cyclic", "Dock each partner as a ring, head to tail", "yesno", False,
                optional=True, advanced=True,
                why="HADDOCK3's cyclicpept, for a peptide of one chain and 40 residues at most; "
                    "anything else is refused by name before it is queued"),
        Setting("receptor_flexible", "Receptor's fully flexible residues", "residues", "",
                optional=True, advanced=True,
                placeholder="none: HADDOCK loosens the interface by itself",
                why="residues HADDOCK may move freely during refinement, as A:10-20"),
        Setting("partner_flexible", "Partners' fully flexible residues", "residues", "",
                optional=True, advanced=True,
                placeholder="none: HADDOCK loosens the interface by itself",
                why="residues HADDOCK may move freely during refinement, as A:10-20"),
        Setting("reference", "A known complex to compare the models with", "path", "",
                optional=True, advanced=True, placeholder="none: the models are scored only",
                why="its chains carry the receptor's and the partner's own letters and residue "
                    "numbers; on a run of several partners it is checked against each of them"),
        Setting("keep_het", "Ions and cofactors to keep", "text", "", optional=True,
                advanced=True, placeholder="none: HADDOCK leaves every hetero group out",
                why="by residue name, as ZN,ATP. The preparation keeps the ions this docking has "
                    "parameters for, and HADDOCK drops them again unless they are named here"),
        Setting("top", "Complexes to hand on", "number", 5, optional=True,
                least=1, most=50, step=1,
                why="per partner: the best model of each of its best clusters. What leaves this "
                    "card is then ranked across all of its partners together, best score first"),
        Setting("cpus", "Cores per partner", "number", 24, optional=True, advanced=True,
                least=1, most=96, step=4,
                why="one HADDOCK3 run per partner, spread over them"),
        Setting("mem", "Memory", "text", "", optional=True, advanced=True,
                placeholder="twice the cores, in GB",
                why="per partner, as 48G"),
        Setting("walltime", "Walltime", "duration", "", optional=True, advanced=True,
                least=1800, most=7 * 24 * 3600, step=1800,
                placeholder="from the estimate",
                why="left empty, the docking works one out from the size of the molecules and "
                    "the sampling"),
        Setting("throttle", "Most partners at once", "number", 0, optional=True, advanced=True,
                least=0, most=50, step=1,
                why="of the array, one task a partner; 0 lets the queue run them all"),
        Setting("partition", "Partition", "text", "", optional=True, advanced=True,
                placeholder="chosen to fit the time asked",
                why="empty lets the docking pick one the walltime fits in"),
        Setting("account", "Account to charge", "text", "", optional=True, advanced=True,
                placeholder="if your site needs one"),
        Setting("extra", "Other Slurm options", "text", "", optional=True, advanced=True,
                placeholder="--qos=normal",
                why="written with = so they are not read as options of the docking itself"),
    ],
    note="Takes the target as the receptor and each folded structure as a partner.")

#: The keys of the two preparation cards, which the rules below and the flow ask for by key. A
#: catalogue without them is a catalogue where nothing can be prepared, which the insertion has to
#: be able to notice.
PREPARE_KEY = "prepare"
PREPARE_RECEPTOR_KEY = "prepare_receptor"


def _prep_settings(receptor=False):
    """The fields the two preparation cards share, in the order the card draws them.

    One list for both, because both are HOPE-MD's own Prepare stage asked the same questions: a
    receptor prepared for the docking and a complex prepared for the simulation are protonated,
    built and minimised by the same answers, and two cards offering two wordings of one setting
    would be two settings. The receptor card leaves out the ones about waters and hetero groups:
    a receptor goes to a docking, and into a docking every water and every hetero group is dropped
    except the ions that docking can use, whatever those boxes would say.

    Which is also where the hetero questions are answered rather than where they are asked. Each
    molecule is split out as polymer residues alone (helpers/prepare_molecules.one_molecule), so a
    water or a cofactor is not in front of the per-molecule Prepare at all: these answers travel
    with the structures into HOPE-MD's spec (drivers._spec) and are read by HOPE-MD's own Prepare
    of the assembled complex, which is the stage that decides what the build keeps and which side
    of the interface each group belongs to.
    """
    out = [
        # One pH for the flow. Every molecule of every structure is protonated at it, and two
        # preparation cards in one flow disagreeing about it would be two halves of one complex
        # titrated differently, so Flow.check() refuses that rather than letting it happen.
        Setting("ph", "pH", "number", 7.4, least=0, most=14, step=0.1,
                why=("the receptor is protonated at this pH, and one flow has one pH" if receptor
                     else "every molecule is protonated at this pH, and one flow has one pH")),
        Setting("protonation", "How the hydrogens are decided", "choice", "propka",
                choices=["propka", "pdbfixer", "none"],
                notes={"propka": "pdb2pqr with propka at the pH above: each titratable residue "
                                 "gets the state its own surroundings call for",
                       "pdbfixer": "PDBFixer's rules at the pH above, which are the standard "
                                   "states and no more",
                       "none": "the hydrogens in the file, or the engine's own defaults where "
                               "there are none"}),
        Setting("model_loops_max", "Longest gap to model, residues", "number", 12,
                optional=True, least=0, most=12, step=1,
                why="a gap inside a chain up to this long, found from the sequence the file records, "
                    "is built, relaxed and its joins checked, as ADCP builds one; a longer one is left "
                    "open and its two ends capped (Cap the breaks). 12 at most, 0 builds none, and a "
                    "chain's missing ends are never built"),
        # The one preparation setting a predicted model has, and every predicted model a flow
        # folds or designs goes through one of these cards. A terminus with no confidence behind
        # it is a tail flailing through the box: it is the first thing to come off a model, and
        # the preparation trims it rather than simulating a guess.
        Setting("trim_plddt", "Trim predicted tails below pLDDT", "number", 50,
                optional=True, advanced=True,
                least=MD_PLDDT[0], most=MD_PLDDT[1], step=5,
                why="for a predicted model: terminal residues under this confidence are trimmed; "
                    "0 keeps every one, and a structure with no pLDDT in it is untouched"),
        Setting("altloc", "Alternate location to keep", "text", "A", optional=True,
                advanced=True,
                why="a crystal structure that models two positions for a side chain keeps this "
                    "one; the atoms of the others are dropped"),
        Setting("model", "Model to take", "number", 1, optional=True, advanced=True,
                least=1, most=100, step=1,
                why="for an NMR ensemble or any multi-model file: which model is prepared. The "
                    "first unless you say otherwise"),
    ]
    if not receptor:
        out += [
            Setting("drop_waters", "Crystal waters", "choice", "all",
                    choices=list(MD_DROP_WATERS), optional=True, advanced=True,
                    why="for the simulation; into a docking card every water is dropped",
                    notes={"all": "drop every one",
                           "far": "keep those within 5 Å of the partner",
                           "none": "keep every one"}),
            Setting("keep_cofactors", "Keep metals and cofactors", "yesno", True, optional=True,
                    advanced=True,
                    why="into the simulation they ride through at their own coordinates, and it "
                        "decides which side of the interface each belongs to; into a docking "
                        "card only the ions that docking can use are kept, and the rest are "
                        "listed as dropped"),
            # The other three hetero questions, which go with the two above and are read in the
            # same place: HOPE-MD's own Prepare of the assembled complex. Left out of the
            # receptor card for the same reason those two are.
            Setting("drop_buffers", "Drop the crystallisation buffers", "yesno", True,
                    optional=True, advanced=True,
                    why="glycerol, sulfate, PEG, acetate, DMSO and the rest of what the "
                        "crystallographer added; off, each one has to be covered by a force "
                        "field or the run is refused by name"),
            Setting("keep_other_hetero", "Keep the hetero groups that are neither", "yesno",
                    False, optional=True, advanced=True,
                    why="a group that is not a buffer, a metal or a known cofactor: kept only if "
                        "a force field here covers it, and named in the refusal if not"),
            Setting("drop_hetero", "Also drop, by residue name", "text", "", optional=True,
                    advanced=True, placeholder="nothing beyond the classes above",
                    why="residue names dropped whatever their class, separated by commas: NAD, "
                        "HEM. This is what a refusal naming a group it cannot cover asks for"),
        ]
    out += [
        Setting("cap_breaks", "Cap the breaks", "yesno", True, optional=True,
                advanced=True,
                why="a gap left open has its two ends capped, an NME on the residue before it and "
                    "an ACE on the residue after it, so neither is a charged terminus the protein "
                    "does not have; the chain's own termini stay charged. Off, each piece ends in "
                    "charged termini, an OXT before the gap and NH3+ after it"),
        Setting("minimise", "Minimise each molecule", "yesno", True, optional=True, advanced=True,
                why="restrained, in vacuum: it takes the clashes out and moves nothing else"),
        Setting("minimise_steps", "Minimisation steps", "number", 500, optional=True,
                advanced=True, least=0, most=100000, step=100,
                why="L-BFGS steps on each molecule; 0 skips the minimisation as surely as the "
                    "switch above",
                varies=[("minimise", {False: {"why": "not read: the minimisation is off"}})]),
        Setting("restraint_kcal", "Minimisation restraint, kcal/mol/Å²", "number", 50,
                optional=True, advanced=True, least=0, most=1000, step=10,
                why="held on the heavy atoms the file already had, so only the modelled residues "
                    "move; 0 lets everything move",
                varies=[("minimise", {False: {"why": "not read: the minimisation is off"}})]),
        Setting("implicit_solvent", "Minimise in implicit solvent", "yesno", False,
                optional=True, advanced=True,
                why="GBn2 instead of vacuum: a better answer for a modelled loop, and about ten "
                    "times slower",
                varies=[("minimise", {False: {"why": "not read: the minimisation is off"}})]),
        Setting("walltime", "Walltime", "duration", "02:00:00", optional=True, advanced=True,
                least=600, most=24 * 3600, step=600,
                why=("one job prepares the receptor once, for every docking below it" if receptor
                     else "one job prepares everything that arrives, a few minutes a molecule")),
        Setting("partition", "Partition", "text", "", optional=True, advanced=True,
                placeholder="the cluster's short queue",
                why="the queue the one preparation job goes to; empty takes the short one, which "
                   "is where it belongs"),
        Setting("account", "Account to charge", "text", "", optional=True, advanced=True,
                placeholder="whatever you are charged by default",
                why="the Slurm account this job is charged to, for a flow being run against one "
                    "allocation"),
    ]
    return out


PREPARE = Card(
    "prepare", "Prepare", "prepare the receptor and the partner each on its own, then rejoin them",
    fixed=True,
    # One socket in, taking any of the three things a structure arrives as: a fold's model, a
    # docked or designed complex, or the poses of a docking run. Several links may arrive, so two
    # tracks can be prepared by one card and reach the simulation side by side.
    inputs=[Port("structure", "what to prepare", ["structure", "complexes", "poses"], many=True)],
    outputs=[Port("prepared", "prepared structures", ["prepared"])],
    settings=_prep_settings(),
    note="Put in by the canvas on every link that carries a structure into a docking card or the "
         "simulation, and part of that link rather than a card of its own: it splits what arrives "
         "into the receptor and the partner, prepares each on its own - protonation at the pH "
         "above, termini, disulfides, gaps - puts them back together and checks the net charge. "
         "A side of several chains is prepared whole, so the bonds between its chains are kept. "
         "Into a docking card the waters and hetero groups are dropped, except the ions that "
         "docking can use. What it decided is listed on the card when it has run.")

PREPARE_RECEPTOR = Card(
    "prepare_receptor", "Prepare receptor", "prepare the target's receptor once, for the docking",
    fixed=True,
    inputs=[Port("target", "target", ["target"])],
    outputs=[Port("receptor", "prepared receptor", ["receptor"])],
    settings=_prep_settings(receptor=True),
    note="Put in by the canvas between a target and every docking card it feeds, once per target: "
         "the target's chains alone, through HOPE-MD's Prepare - protonation at the flow's pH, "
         "AMBER names, termini and breaks, gaps up to the length above built back from the "
         "sequence the file records - with the waters and hetero groups dropped, except the ions "
         "the docking can use. The hotspots are checked against what comes out: one on a built "
         "residue is flagged on the card, and one that is missing stops the docking, by name.")

HOPEMD = Card(
    "hopemd", "HOPE-MD", "simulate what arrives",
    tool="hopemd",
    # One port taking either kind, and as many edges as there are branches: this is where the
    # tracks of a flow meet, and one batch of simulations comes out of all of them.
    inputs=[Port("structures", "prepared structures", ["prepared"], many=True)],
    outputs=[Port("trajectories", "trajectories", ["trajectories"])],
    settings=[
        # One run per structure, and which structures is the person's own choice. By rank is the usual answer and the one that needs no typing; by name
        # is for coming back to a flow and simulating the third and the eleventh because of
        # something seen in the docking, which no number can say.
        Setting("pick", "Which of what arrives to simulate", "choice", "rank",
                choices=["rank", "name"],
                notes={"rank": "the best few, by the score the tool that made them gave them, "
                               "ranked across all of its partners together",
                       "name": "the ones named below, whatever their rank"}),
        # How many to simulate is the one setting that decides what a flow costs, so it is asked
        # for. Everything arriving here is ranked by the tool that made it, across every partner
        # of that tool rather than within each one; this takes that many from the top.
        Setting("top_n", "Simulate the best of what arrives", "number", 10, needed=True,
                least=1, most=100, step=1,
                why="per track, best score first across all of that track's partners. Each one "
                    "is a full simulation, so this is what the flow costs",
                varies=[("pick", {"name": {"why": "not read: the structures named below are the "
                                                  "ones simulated"}})]),
        Setting("names", "Structures to simulate, by name", "text", "", optional=True,
                placeholder="not read: the best few are taken by rank",
                why="as the preparation named them, separated by commas",
                varies=[("pick", {"name": {
                    "optional": False, "placeholder": "",
                    "why": "the names as they arrive here, separated by commas: a docking's are "
                           "<partner>_cluster<n>, a fold's <card>_model<n>, a peptide's "
                           "<peptide>_pose<n>. A name nothing answers to is said before anything "
                           "is queued"}})]),
        # The one thing a batch of runs cannot be asked in a single row of boxes: that this one
        # structure is simulated for longer, or on another engine, than the rest. Typed rather
        # than drawn, because a card is a column of single-line boxes and this is a table.
        Setting("overrides", "Settings for one structure", "text", "", optional=True,
                advanced=True, placeholder="every run takes the settings on this card",
                why="a group per structure, as name: key=value, key=value, with a semicolon "
                    "between groups. The keys are this card's own, so "
                    "\"3cc1_cluster2: length_ns=200, engine=openmm\" runs that one structure "
                    "for longer on another engine and leaves the rest alone. A value that is "
                    "itself a list, as the restrained residues are, is separated by spaces here, "
                    "because the commas separate the settings"),
        Setting("poses_each", "Poses per peptide", "number", 1, optional=True,
                least=1, most=10, step=1,
                why="from a docking: the best few poses of each peptide, not only its first; "
                    "the best of what arrives still caps the whole"),
        Setting("engine", "Engine", "choice", "amber",
                choices=["amber", "openmm", "gromacs", "namd", "desmond"], needed=True,
                why="the same system on any of the five; Amber is the fastest here and what the "
                    "lab's benchmarks are in",
                notes={"amber": "pmemd.cuda: the fastest on these cards, and the engine the "
                                "lab's timings are measured on",
                       "openmm": "a Python engine on the same Amber topology",
                       "gromacs": "the Amber topology converted by ParmEd",
                       "namd": "NAMD 3, reading the prmtop itself; no four-site water",
                       "desmond": "Schrödinger's, with its own OPLS force field and box"}),
        # The bilayer. Above the fold because it changes what the run is rather than how it is
        # done: a membrane protein simulated in a water box is not a slower answer, it is the
        # wrong one, and until this box existed a flow could only ask for water.
        Setting("environment", "What it is simulated in", "choice", "water",
                choices=list(MD_ENVIRONMENTS),
                notes={"water": "a periodic water box around the complex",
                       "membrane": "a lipid bilayer, with water above and below it. The force "
                                   "field, the water, the box shape and the salt follow the "
                                   "bilayer build and are written for you"}),
        Setting("length_ns", "Length, ns", "number", 100, needed=True,
                least=1, most=5000, step=10,
                why="production, per replicate; past the queue's limit it is chained as segments",
                varies=[("device", {"cpu": {
                    "most": MD_CPU_MAX_NS,
                    "why": "a CPU check is at most %g ns: it answers whether the pose holds at "
                           "the start, not how it behaves" % MD_CPU_MAX_NS}})]),
        # Two rules, both HOPE-MD's, and both about the interval this works out to rather than
        # about the number itself: it has to be a whole number of steps, and it cannot be longer
        # than the stride MM-GBSA takes its frames at. A hundred frames of a hundred nanoseconds
        # is a frame every nanosecond, and MM-GBSA would have nothing to score.
        #
        # 5000 is HOPE-MD's own answer, not a number of ours. Its default and all three of its
        # presets save every 20 ps, and 5000 frames of the 100 ns above is every 20 ps. The 500
        # this used to be was every 200 ps - a tenth of the trajectory the tool would have kept,
        # and exactly the MM-GBSA stride, which is the floor MM-GBSA's own stride may not go
        # under: at 200 ps saved nobody can ever rescore the run more finely than 200 ps. It also
        # put a wall at 100 ns, because 500 frames of anything longer is an interval over the
        # stride and the canvas refuses it - so a person who changed only the length, the most
        # obvious knob on the card, was refused for a box they had not touched. 5000 moves that
        # wall to 1000 ns. The cost is disk: 5.5 GB a run of three replicates for a 30 000-atom
        # system where 500 frames was 0.6 GB (hopemd.spec.derive), which is what Tidy is for.
        Setting("frames", "Frames to keep", "number", 5000, optional=True,
                least=10, most=100000, step=50,
                why="over the whole production, so the interval is the length divided by this: "
                    "5000 of 100 ns is one every 20 ps, which is HOPE-MD's own. It has to be a "
                    "whole number of steps and no longer than the %g ps MM-GBSA takes its frames "
                    "at, and the canvas says which number to type instead" % MD_MMGBSA_STRIDE_PS,
                varies=[("device", {"cpu": {
                    "most": MD_CPU_MAX_FRAMES,
                    "why": "a CPU check keeps at most %d frames" % MD_CPU_MAX_FRAMES}})]),
        # Three is the lab's own finding rather than a convention: rescoring the same three
        # peptides on docked poses and on dynamics showed the spread between replicates larger
        # than the differences being ranked, so replicates are what buys confidence here.
        Setting("replicates", "Replicates", "number", 3, optional=True,
                least=MD_REPLICATES[0], most=MD_REPLICATES[1], step=1,
                why="independent repeats, each with its own seed; %d is the most HOPE-MD runs"
                    % MD_REPLICATES[1]),
        Setting("mmgbsa", "Binding energy by MM-GBSA", "yesno", True, optional=True,
                why="GB-Neck2 over the frames below, per replicate: the number a flow is usually "
                    "drawn to get. Off for a run of one molecule, which has no interface"),
        # The bilayer's own five, from HOPE-MD's Membrane. Under More settings because they mean
        # nothing in water, and each says so there rather than sitting in the card looking unset.
        Setting("lipid", "Lipid", "choice", "POPC", choices=list(MD_LIPIDS),
                optional=True, advanced=True,
                why="the bilayer is tiled from a pre-equilibrated patch of this lipid",
                varies=[("environment", {"water": {
                             "why": "not read: the run is in a water box"}}),
                        ("engine", {"desmond": {
                             "why": "Desmond's System Builder ships %s bilayers"
                                    % ", ".join(MD_DESMOND_LIPIDS)}})]),
        Setting("membrane_padding_A", "Water above and below the bilayer, Å", "number", 10,
                optional=True, advanced=True,
                least=MD_MEMBRANE_PADDING_A[0], most=MD_MEMBRANE_PADDING_A[1], step=1,
                why="under %g Å the head groups see their own image, over %g Å is water for "
                    "nothing" % MD_MEMBRANE_PADDING_A,
                varies=[("environment", {"water": {
                    "why": "not read: the run is in a water box"}})]),
        # A coordinate, so HOPE-MD bounds it nowhere. The pair here is the card's own, in the
        # style of the pressure and heating boxes: a bilayer offset by more than the height of
        # any box this build makes is a bilayer with the protein nowhere near it.
        Setting("center_z_A", "Bilayer centre along z, Å", "number", 0,
                optional=True, advanced=True, least=-100, most=100, step=1,
                why="where the middle of the bilayer sits; 0 is the OPM convention and what an "
                    "oriented structure expects",
                varies=[("environment", {"water": {
                    "why": "not read: the run is in a water box"}})]),
        Setting("orientation", "How the structure is oriented", "choice", "as_imported",
                choices=list(MD_ORIENTATIONS), optional=True, advanced=True,
                notes={"as_imported": "the structure's membrane normal is already z, as an OPM "
                                      "file's is. A folded or docked model is not: say its "
                                      "segments below, or orient it before the flow",
                       "opm": "fetch the OPM orientation, which needs the internet and so a "
                              "login node",
                       "annotation": "orient on the transmembrane segments, from the ones typed "
                                     "below or the ones UniProt annotates"},
                varies=[("environment", {"water": {
                    "why": "not read: the run is in a water box"}})]),
        Setting("segments", "Transmembrane segments", "text", "", optional=True, advanced=True,
                placeholder="found from the sequence",
                why="as chain:start-end, separated by commas: A:30-56, A:71-96. Typed ones are "
                    "used in place of whatever was found",
                varies=[("environment", {"water": {
                    "why": "not read: the run is in a water box"}})]),
        Setting("box_shape", "Box shape", "choice", "compact", choices=list(MD_BOX_SHAPES),
                optional=True, advanced=True,
                notes={"compact": "a truncated octahedron: the fewest waters for the same "
                                  "minimum image distance, and the default",
                       "cubic": "a cube, for comparison with work that used one",
                       "rectangular": "an orthorhombic box, which is what a bilayer takes"},
                varies=[("engine", {engine: {"value": shape,
                                             "why": "%s's System Builder builds an orthorhombic "
                                                    "box whatever is asked" % engine.title()}
                                    for engine, shape in MD_FIXED_BOX.items()}),
                        ("environment", {"membrane": {
                             "value": "rectangular",
                             "why": "a bilayer is periodic in x and y, so the box is "
                                    "rectangular"}})]),
        # The floor is HOPE-MD's, the only thing it refuses a padding for. The ceiling is the
        # largest water layer it builds anywhere, which is the bilayer's: past that the box is
        # mostly water and the run is mostly solvent, and HOPE-MD sets no limit of its own.
        Setting("padding_A", "Padding, Å", "number", 12, optional=True, advanced=True,
                least=MD_CUTOFF_A, most=MD_MEMBRANE_PADDING_A[1], step=1,
                why="water between the solute and the box edge; under the %g Å cutoff the "
                    "complex sees its own periodic image" % MD_CUTOFF_A,
                varies=[("environment", {"membrane": {
                    "why": "not read: a bilayer's water is the padding above and below it"}})]),
        Setting("salt", "Salt", "choice", "NaCl", choices=list(MD_SALTS),
                optional=True, advanced=True,
                why="the box is neutralised first, then brought to the concentration below",
                notes={"NaCl": "physiological, and the default",
                       "KCl": "for work done in potassium buffer",
                       "MgCl2": "for a nucleic acid, which binds Mg2+; not for a bilayer, which "
                                "takes a monovalent pair"},
                varies=[("environment", {"membrane": {
                    "why": "a bilayer build places a monovalent pair: %s"
                           % " or ".join(MD_MEMBRANE_SALTS)}})]),
        Setting("salt_mM", "Salt concentration, mM", "number", 150, optional=True, advanced=True,
                least=MD_SALT_MM[0], most=MD_SALT_MM[1], step=10,
                why="150 mM is physiological. It is also the salt MM-GBSA is computed at, so it "
                    "changes the binding energy as well as the box"),
        # Residues held still through production. Not a site: a site is on the target card and is
        # where a binder goes, and these are the residues of this complex that are not to move.
        Setting("restrain", "Residues held still in production", "residues", "",
                optional=True, advanced=True,
                placeholder="nothing is held: the whole complex is free",
                why="as 54,56,66-70 or A54,B12-16, in the numbering the preparation kept. For a "
                    "run meant to answer what the rest does while these stay put"),
        Setting("restraint_kcal", "Production restraint, kcal/mol/Å²", "number", 5,
                optional=True, advanced=True, least=0, most=1000, step=1,
                why="how hard those residues are held, and the first rung of the equilibration "
                    "ladder either way",
                varies=[("restrain", {"": {"why": "the first rung of the equilibration ladder; "
                                                 "no residue is held in production"}})]),
        # The MM-GBSA window. The run is one trajectory and the score is an average over a slice
        # of it, and which slice is a real question: the first nanoseconds are the complex still
        # settling into its box, and averaging them in is averaging in the equilibration.
        Setting("mmgbsa_from_ns", "Score from, ns", "number", 0, optional=True, advanced=True,
                least=0, most=5000, step=10,
                why="skip the start, where the complex is still settling; 0 scores from the "
                    "first frame",
                varies=[("mmgbsa", {False: {"why": "not read: MM-GBSA is off"}})]),
        Setting("mmgbsa_to_ns", "Score to, ns", "number", None, optional=True, advanced=True,
                least=0, most=5000, step=10, placeholder="the end of the run",
                why="empty scores to the end",
                varies=[("mmgbsa", {False: {"why": "not read: MM-GBSA is off"}})]),
        Setting("mmgbsa_stride_ps", "Score every, ps", "number", MD_MMGBSA_STRIDE_PS,
                optional=True, advanced=True, least=1, most=100000, step=10,
                why="how often a frame of that window is scored; it can be no finer than the "
                    "interval the frames above are kept at",
                varies=[("mmgbsa", {False: {"why": "not read: MM-GBSA is off"}})]),
        # A flow that runs overnight should leave something to read in the morning. Neither of
        # these is made by a run on its own: they are asked for afterwards, from the Runs screen,
        # which is a person pressing buttons on twenty runs. Asked for here, the flow does it.
        Setting("report", "Write each run's report when it finishes", "yesno", True,
                optional=True, advanced=True,
                why="the run's own report, as the Runs screen would write it: one job per run, "
                    "behind the simulation"),
        Setting("videos", "Make each run's videos when it finishes", "yesno", False,
                optional=True, advanced=True, why="a job of its own per run, cut from the trajectory"),
        Setting("tidy", "Tidy each run when it finishes", "yesno", True, optional=True,
                advanced=True,
                why="a last job packs away the small files a finished run no longer needs; the "
                    "results still load. Off, a batch of runs is a great many files"),
        Setting("viewer_stride", "Frames the viewer shows, one in", "number", 20, optional=True,
                advanced=True, least=1, most=1000, step=5,
                why="the trajectory the Results viewer loads is thinned by this, so 20 of the "
                    "5000 frames kept is 250 to scrub through"),
        Setting("temperature_K", "Temperature, K", "number", 300, optional=True, advanced=True,
                least=MD_TEMPERATURE_K[0], most=MD_TEMPERATURE_K[1], step=5,
                why="the thermostat's target through heating, equilibration and production"),
        Setting("pressure_bar", "Pressure, bar", "number", 1.0, optional=True, advanced=True,
                least=0, most=1000, step=1,
                why="the barostat's target once the box is equilibrated at constant pressure"),
        # Two numbers, not a range: 2 fs, or 4 with hydrogen mass repartitioning. The arrows
        # step between them and nothing else is accepted, because nothing else is.
        Setting("timestep_fs", "Timestep, fs", "number", 4.0, optional=True, advanced=True,
                least=MD_TIMESTEPS[0], most=MD_TIMESTEPS[1],
                step=MD_TIMESTEPS[1] - MD_TIMESTEPS[0],
                why="2 fs, or 4 fs with hydrogen mass repartitioning",
                varies=[("engine", {engine: {"value": fixed,
                                             "why": "%s runs its RESPA scheme: the timestep is "
                                                    "fixed at %g fs and there is no hydrogen "
                                                    "mass repartitioning"
                                                    % (engine.title(), fixed)}
                                    for engine, fixed in MD_FIXED_TIMESTEP.items()})]),
        Setting("equil_ps", "Equilibration, ps", "number", 1000, optional=True, advanced=True,
                least=0, most=100000, step=100,
                why="over the restraint ladder"),
        Setting("heat_ps", "Heating, ps", "number", 200, optional=True, advanced=True,
                least=0, most=10000, step=50,
                why="from 0 K to the temperature above, restrained, before the ladder"),
        Setting("minimise_steps", "Minimisation steps", "number", 5000, optional=True,
                advanced=True, least=0, most=1000000, step=500,
                why="on the built box, before any heating: it takes the clashes out of the "
                    "solvation"),
        Setting("seed", "Random seed", "number", -1, optional=True, advanced=True,
                least=-1, most=MD_SEED_MAX, step=1,
                why="-1 lets each replicate pick its own"),
        # Every water and every force field any of the five engines takes, because a menu cannot
        # be rewritten per engine; which engine takes which is the line under the box, and a
        # pairing the engine would refuse is refused while the card is being filled in.
        Setting("water", "Water", "choice", "",
                choices=["", "OPC", "TIP3P", "TIP4P-Ew", "TIP4P", "SPC"],
                optional=True, advanced=True,
                why="empty takes what the force field was fitted to",
                notes={"": "the force field's own pairing: OPC with ff19SB, TIP3P with ff14SB, "
                           "SPC with Desmond's OPLS",
                       "OPC": "ff19SB's own, and not for NAMD or Desmond",
                       "TIP3P": "every engine takes it",
                       "TIP4P-Ew": "not for NAMD, which cannot run a four-site water from the "
                                   "Amber topology",
                       "TIP4P": "Desmond's OPLS4 only",
                       "SPC": "Desmond"},
                varies=[("environment", {"membrane": {
                    "why": "a bilayer build has one water and it is written for you: %s, or SPC "
                           "on Desmond, whose lipid patches come hydrated with it"
                           % MD_MEMBRANE_WATER}})]),
        Setting("protein", "Protein force field", "choice", "",
                choices=["", "ff19SB", "ff14SB", "OPLS_2005", "OPLS4"],
                optional=True, advanced=True,
                why="empty takes the engine's own: ff19SB on the four Amber-topology engines, "
                    "OPLS_2005 on Desmond",
                notes={"": "the engine's own",
                       "ff19SB": "Amber, OpenMM, GROMACS, NAMD",
                       "ff14SB": "the same four, for comparison with older work",
                       "OPLS_2005": "Desmond, which types the whole system with its own OPLS",
                       "OPLS4": "Desmond, with the OPLS licence"},
                varies=[("engine", {engine: {"why": "%s types the whole system with its own "
                                                    "OPLS: %s" % (engine.title(),
                                                                  " or ".join(fields))}
                                    for engine, fields in MD_PROTEIN_FF.items()
                                    if engine == "desmond"}),
                        ("environment", {"membrane": {
                             "why": "a bilayer build has one force field and it is written for "
                                    "you: %s, which is the only one OpenMM ships lipid17 "
                                    "alongside, or Desmond's own OPLS"
                                    % MD_MEMBRANE_PROTEIN_FF}})]),
        Setting("nucleic", "DNA and RNA force field", "choice", "",
                choices=["", "OL21+OL3", "OL15+OL3", "bsc1+OL3"], optional=True, advanced=True,
                why="for a strand in the run; empty takes the engine's usual",
                notes={"": "OL21+OL3, or OL15+OL3 on OpenMM",
                       "OL21+OL3": "Amber, GROMACS and NAMD",
                       "OL15+OL3": "OpenMM",
                       "bsc1+OL3": "any engine but Desmond, which types DNA and RNA with its "
                                   "own OPLS and takes no choice"}),
        # Where it runs. The GPU type matters here more than anywhere: on Grace the a40 nodes can
        # sit idle while a100 jobs queue, and auto is what takes whichever has a free card at
        # submit. The CPU check is for when every card is busy and the question is only whether
        # the pose holds at all.
        Setting("device", "Run on", "choice", "gpu", choices=["gpu", "cpu"],
                optional=True, advanced=True,
                notes={"gpu": "one card per replicate, which is how these engines are meant to "
                              "be run",
                       "cpu": "one whole node, for a short check while the GPU queue is full: "
                              "Amber or GROMACS, at most %g ns and %d frames"
                              % (MD_CPU_MAX_NS, MD_CPU_MAX_FRAMES)}),
        Setting("gpu", "GPU type", "choice", "",
                choices=[""] + [MD_GPU_AUTO] + list(MD_GPU_TYPES), optional=True, advanced=True,
                why="empty takes the cluster's usual card",
                notes={"": "the cluster's default, which on Grace is a100",
                       MD_GPU_AUTO: "the fastest type with a free card at submit, which is what "
                                    "keeps a run out of the a100 queue when a40s are idle",
                       "a100": "40 GB, the fastest here and the default",
                       "a40": "48 GB, on its own partition and often free",
                       "rtx": "24 GB", "t4": "16 GB, for a small system"},
                varies=[("device", {"cpu": {"why": "not read: the run is on CPU"}})]),
        Setting("gpus_per_replicate", "GPUs per replicate", "number", 1, optional=True,
                advanced=True, least=1, most=max(MD_GPUS_PER_NODE.values()), step=1,
                why="one is what Amber, OpenMM and Desmond each run a replicate on, however many "
                    "are asked for; NAMD uses them all",
                varies=[("gpu", {gpu: {"most": cards,
                                       "why": "a Grace %s node has %d cards" % (gpu, cards)}
                                 for gpu, cards in MD_GPUS_PER_NODE.items()}),
                        ("device", {"cpu": {"why": "not read: the run is on CPU"}})]),
        Setting("cpus", "CPU cores per replicate", "number", 4, optional=True, advanced=True,
                least=MD_CPUS[0], most=MD_CPUS[1], step=2,
                why="beside the card, for the parts that are not on it; a CPU check takes a "
                    "whole node whatever this says"),
        Setting("mem_gb", "Memory per replicate, GB", "number", 16, optional=True,
                advanced=True, least=MD_MEM_GB[0], most=MD_MEM_GB[1], step=4,
                why="Grace refuses a job that asks for none; raise it for a system of many "
                    "hundreds of thousands of atoms"),
        Setting("max_concurrent", "Replicates running at once", "number", None, optional=True,
                advanced=True, least=1, most=100, step=1,
                placeholder="every one at once",
                why="a throttle for a batch of runs against one allocation: empty queues them "
                    "all and lets the cluster decide"),
        Setting("walltime", "Walltime", "duration", "", optional=True, advanced=True,
                least=3600, most=7 * 24 * 3600, step=3600,
                placeholder="from the estimate",
                why="left empty, the simulation works one out from the length, the replicates and "
                    "the size of the system, which is the number to beat. A typed one is each "
                    "production job's limit, so a shorter one cuts a long run into more segments"),
        Setting("partition", "Partition", "text", "", optional=True, advanced=True,
                placeholder="the one that carries the card",
                why="empty takes the partition the GPU type lives on, which is what you want "
                    "unless you are aiming at a reservation"),
        Setting("account", "Account to charge", "text", "", optional=True, advanced=True,
                placeholder="whatever you are charged by default",
                why="the Slurm account every job of every run is charged to, the preparation "
                    "included"),
    ])

# The two folding cards come before the target, because that is where they stand in a flow: they
# hand it the structure it would otherwise fetch.
CARDS = (TARGET, BOLTZ2, COLABFOLD, BINDCRAFT, RFDIFFUSION, RFD3, BOLTZGEN, 
         ADCP, HADDOCK, PREPARE, PREPARE_RECEPTOR, HOPEMD)
BY_KEY = {c.key: c for c in CARDS}


def aims_at_site(card, settings=None):
    """Whether this card is aimed at a site, so the flow it is in needs hotspots.

    The one question behind the hotspots: a tool that grows, diffuses or docks a binder onto named
    residues cannot be started without them, and a tool with no site - a fold, a preparation, a
    simulation, a docking searching the whole surface - must not be made to ask for them. Read off
    the card and the tick on it rather than from a list of names here, so a card added to a
    catalogue on the cluster is answered correctly by a launcher that has never heard of it.
    """
    if isinstance(card, str):
        card = BY_KEY.get(card)
    if card is None or not card.site:
        return False
    if card.site == SITE_ALWAYS:
        return True
    field = next((s for s in card.settings if s.key == card.site), None)
    usual = field.default if field is not None else True
    value = (settings or {}).get(card.site, usual)
    if value is None:
        value = usual
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def fold_entities(card, settings=None):
    """The chains a folding card asks for, as rows in the order the boxes were filled in.

    [{"index": n, "suffix": the boxes' suffix, "kind": protein | dna | rna, "sequence": as typed,
      "copies": what was asked for}]. The first row is always there, empty sequence and all, so
    that an unanswered card is one row with nothing in it rather than no rows at all - the
    difference between "the sequence is missing" and "there is nothing to fold". Every further row
    is there only if its own sequence box has something in it.

    One reading, used by the checker on the laptop and by the driver on the cluster, so what the
    canvas refuses and what the fold is given cannot come apart.
    """
    if isinstance(card, str):
        card = BY_KEY.get(card)
    settings = settings or {}
    keys = {s.key for s in (card.settings if card else ())}
    out = []
    for n, suffix in enumerate(ENTITY_SUFFIXES):
        if "sequence" + suffix not in keys:
            continue
        text = str(settings.get("sequence" + suffix) or "")
        if n and not text.strip():
            continue
        out.append({"index": n, "suffix": suffix, "sequence": text,
                    "kind": str(settings.get("entity" + suffix) or "protein").strip().lower(),
                    "copies": settings.get("copies" + suffix)})
    return out


def folds_a_structure(card):
    """Whether this card folds a sequence into a structure for a target card to take.

    Asked of the ports and not of the key, so a third folding tool added to a catalogue on the
    cluster has its sequence checked by a launcher built before it existed, without anything here
    being taught its name.
    """
    if isinstance(card, str):
        card = BY_KEY.get(card)
    return bool(card) and any("structure" in p.kinds for p in card.outputs)


#: What the shape of a catalogue means. Raised when a card gains something the rules below have
#: to understand - a new kind, a new sort of port - not when a card is added or a setting changes.
#: A launcher reading a catalogue newer than it understands keeps its own rather than guessing.
#:
#: 2, for the folded target: a structure kind, two cards that leave one, and a target card that
#: takes one in. A launcher built before those adopts the cards happily - they are only data - and
#: then refuses every flow that folds its target, because its own copy of check() has never heard
#: of a source called "folded" and sees a fold with no target upstream of it. It says the fold is
#: not joined to a target it is plainly joined to, it shuts Launch, and there is nothing the person
#: can do about it. Raising this is what has that launcher keep its own eight cards instead: no
#: folding offered, and everything it can still draw still runs.
#:
#: 3, for the site rule and the preparation. A launcher built before them holds a check() that
#: asks every flow for a target card and for hotspots on it, and knows nothing of the card the
#: canvas puts on a link by itself. Given these cards it would refuse a blind docking for having
#: no hotspots, refuse a co-fold into the simulation for having no target at all, and call the
#: preparation a card it does not know - none of which the person could do anything about. So it
#: keeps its own cards, where every flow it can draw still runs, until it is replaced.
#:
#: 4, for the prepared receptor: a receptor kind, a second card the canvas puts in by itself, and
#: docking cards whose target socket takes only a receptor. A launcher built before it would read
#: every link from a target into a docking as one it cannot draw - its own copy of the rules has
#: one card that prepares, and that card takes no target - and refuse the lab's own example. So it
#: keeps its own cards, whose docking cards take the target as they always did; the flow it
#: launches is given its receptor card on the cluster, which reads every flow with the rules it has.
RULES = 4


def catalogue():
    """Every card, for the canvas, and for a launcher that takes its cards from the cluster."""
    return {"rules": RULES, "kinds": KINDS, "dockable_max_length": DOCKABLE_MAX_LENGTH,
            "linker_families": LINKER_FAMILIES, "cards": [c.as_json() for c in CARDS]}


#: The catalogue as a file in the install, beside this module. A launcher built before 0.6.1 drops
#: each setting's bounds, step, placeholder, notes and rules as it adopts the cluster's cards, and
#: the canvas page - which is fetched from the cluster at every sign-in, unlike the launcher - reads
#: this file to put them back. tests/test_flow.py fails when it no longer matches the cards above;
#: `python -m hope_flow.cli catalogue --write` writes it again.
CATALOGUE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalogue.json")


def write_catalogue(path=CATALOGUE_FILE):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(catalogue(), handle, indent=1, ensure_ascii=False)
        handle.write("\n")
    return path


def adopt(doc):
    """Replace the cards with the ones in this catalogue. Returns what was adopted, or "".

    This is what lets a card added on the cluster appear in a launcher built before it. The cards
    are data - their names, sockets, kinds and settings - while the rules about what may be linked
    to what are code, and the code stays in the launcher. So a new card, a new setting or a
    reworded line reaches people without another download, and a change to the rules themselves
    does not pretend to.

    Anything unreadable leaves the built-in cards exactly as they were.
    """
    global CARDS, BY_KEY, KINDS, DOCKABLE_MAX_LENGTH, LINKER_FAMILIES
    try:
        if not isinstance(doc, dict) or not doc.get("cards"):
            return ""
        if int(doc.get("rules", 1)) > RULES:
            return ""                      # written for rules this launcher does not have
        made = [_card_from(c) for c in doc["cards"]]
        if not made or not any(c.key == "target" for c in made):
            return ""                      # no target card: not a catalogue worth having
    except Exception:                                           # noqa: BLE001
        return ""
    CARDS = tuple(made)
    BY_KEY = {c.key: c for c in CARDS}
    if isinstance(doc.get("kinds"), dict) and doc["kinds"]:
        KINDS = dict(doc["kinds"])
    if isinstance(doc.get("linker_families"), dict) and doc["linker_families"]:
        LINKER_FAMILIES = dict(doc["linker_families"])
    try:
        DOCKABLE_MAX_LENGTH = int(doc.get("dockable_max_length") or DOCKABLE_MAX_LENGTH)
    except (TypeError, ValueError):
        pass
    return "%d cards" % len(CARDS)


def _card_from(d):
    return Card(d["key"], d.get("name") or d["key"], d.get("tagline", ""),
                tool=d.get("tool", ""), note=d.get("note", ""), ready=bool(d.get("ready", True)),
                site=str(d.get("site") or ""), fixed=bool(d.get("fixed")),
                inputs=[_port_from(p) for p in d.get("inputs", [])],
                outputs=[_port_from(p) for p in d.get("outputs", [])],
                settings=[_setting_from(x) for x in d.get("settings", [])])


def _port_from(d):
    return Port(d["key"], d.get("label") or d["key"], d.get("kinds") or [],
                required=bool(d.get("required", True)), many=bool(d.get("many")),
                sized=bool(d.get("sized")))


def _setting_from(d):
    """One field, read back from a catalogue - all of it.

    Everything the panel draws a field from has to survive this, because a launcher that takes
    its cards from the cluster re-serialises what it adopted and hands that to the canvas. What
    is dropped here is dropped from the page: the bounds under a number and the size of its
    arrows' step, the grey words saying what an empty box means, the line against each name in a
    list, and the rules that change one field with another's answer. All of it went,
    and only on the laptops that had signed in - the cluster's own copy was never read back.
    """
    return Setting(d["key"], d.get("label") or d["key"], d.get("kind", "text"),
                   default=d.get("default"), choices=d.get("choices") or (),
                   why=d.get("why", ""), optional=bool(d.get("optional")),
                   needed=bool(d.get("needed")), advanced=bool(d.get("advanced")),
                   least=d.get("least"), most=d.get("most"), step=d.get("step"),
                   placeholder=d.get("placeholder") or "", notes=d.get("notes"),
                   varies=_varies_from(d.get("varies")))


def _varies_from(rules):
    """The `varies` rules of a catalogue, back into the (key, {case: overrides}) they are held as.

    Anything misshapen is left out rather than refused: a rule nobody can read is one field that
    does not change with another, where a refusal would be a whole card the launcher has not got.
    """
    out = []
    for rule in rules or ():
        if not isinstance(rule, dict) or not isinstance(rule.get("cases"), dict):
            continue
        on = rule.get("on")
        # A catalogue written before the spelling was settled keys a tick the way Python prints
        # one, so it is put right here rather than missed at every lookup afterwards.
        cases = {{"True": "true", "False": "false"}.get(k, case_key(k)): dict(v)
                 for k, v in rule["cases"].items() if isinstance(v, dict)}
        if on and cases:
            out.append((on, cases))
    return out


#: The preparation card's boxes that are HOPE-MD's own prep fields, under their own names
#: (hopemd/spec.py, Prep). The card asks for them in its own words and hands them over unchanged:
#: the preparation is HOPE-MD's Prepare stage, called once per molecule, so every answer it takes
#: is already a field of that stage rather than something of ours that has to be translated.
PREP_FIELDS = ("protonation", "model_loops_max", "trim_plddt", "altloc", "model", "drop_waters",
               "keep_cofactors", "drop_buffers", "keep_other_hetero", "cap_breaks", "minimise",
               "minimise_steps", "restraint_kcal", "implicit_solvent")

#: The one prep field that is a list of residue names rather than a value, so the box is read into
#: the list HOPE-MD's Clean takes (Prep.drop_hetero).
PREP_LIST_FIELDS = ("drop_hetero",)


def prep_from(settings):
    """The prep fields a preparation card was filled in with, as HOPE-MD's Prepare takes them.

    Only what was answered: a box left empty is HOPE-MD's own default, which is where the lab's
    considered answer lives, and passing an empty string on would be a worse answer than none.
    """
    out = {}
    for key in PREP_FIELDS:
        value = (settings or {}).get(key)
        if value not in (None, ""):
            out[key] = value
    for key in PREP_LIST_FIELDS:
        named = names_asked((settings or {}).get(key))
        if named:
            out[key] = [n.upper() for n in named]
    return out


def names_asked(text):
    """The structures named on a simulation card, in the order they were typed."""
    out = []
    for part in re.split(r"[,;\s]+", str(text or "")):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


#: The settings one structure may be given its own answer to. Everything that changes what a run
#: is rather than which runs there are: the picker, the names and the overrides themselves cannot
#: be overridden, because they are about the batch and not about one of it.
NOT_PER_STRUCTURE = ("pick", "names", "overrides", "top_n", "poses_each", "runs")


def per_structure(card, text):
    """{structure: {setting: value}} from the overrides box, and what is wrong with it.

    One group per structure, "name: key=value, key=value", with a semicolon between groups. Typed
    rather than drawn because a card is a column of single-line boxes and this is a table; read in
    one place, here, so that what the canvas refuses and what a run is given cannot differ.
    """
    allowed = {f.key: f for f in (card.settings if card else ())
               if f.key not in NOT_PER_STRUCTURE}
    out, bad = {}, []
    for group in str(text or "").split(";"):
        group = group.strip()
        if not group:
            continue
        if ":" not in group:
            bad.append("%r does not say which structure it is for: write it as "
                       "name: key=value" % group[:40])
            continue
        name, rest = group.split(":", 1)
        name = name.strip()
        if not name:
            bad.append("%r names no structure" % group[:40])
            continue
        mine = out.setdefault(name, {})
        for pair in rest.split(","):
            pair = pair.strip()
            if not pair:
                continue
            if "=" not in pair:
                bad.append("%s: %r is not a setting and a value, written key=value"
                           % (name, pair[:30]))
                continue
            key, value = (x.strip() for x in pair.split("=", 1))
            field = allowed.get(key)
            if field is None:
                bad.append("%s: this card has no setting called %r" % (name, key))
                continue
            if field.kind == "number":
                try:
                    value = float(value)
                    if value == int(value):
                        value = int(value)
                except ValueError:
                    bad.append("%s: %s takes a number, not %r" % (name, field.label.lower(),
                                                                  value))
                    continue
                if field.least is not None and value < field.least:
                    bad.append("%s: %s is %s, and the least it can be is %g"
                               % (name, field.label.lower(), value, field.least))
                    continue
                if field.most is not None and value > field.most:
                    bad.append("%s: %s is %s, and the most it can be is %g"
                               % (name, field.label.lower(), value, field.most))
                    continue
            elif field.kind == "yesno":
                value = str(value).strip().lower() in ("1", "true", "yes", "on")
            elif field.choices and value not in field.choices:
                bad.append("%s: %r is not one of the choices for %s (%s)"
                           % (name, value, field.label.lower(), ", ".join(field.choices)))
                continue
            mine[key] = value
    return out, bad


def preparations():
    """The cards that prepare, which are the cards the canvas puts in by itself, in catalogue order.

    Asked of the cards rather than written as a list of two keys, so a catalogue read back from the
    cluster is answered by what it holds.
    """
    return [c for c in CARDS if c.fixed and c.inputs and c.outputs]


def prepared_between(from_card, from_port, to_card, to_port):
    """Which preparation belongs on this link: the key of the card that bridges it, or "".

    The rule, and the standing one behind it: before any docking or any simulation, every receptor
    and every partner is prepared on its own. So a link that carries a structure - a fold's model,
    a docked complex, a docking run's poses - into something that takes prepared structures is a
    link with a preparation in the middle of it, and the canvas puts one there rather than
    refusing the drawing or letting coordinates through as if they were chemistry. And a link that
    carries a target into a card that takes a prepared receptor - every docking card - has the
    receptor's own preparation in the middle of it, for the same reason.

    The key rather than yes or no, because there are two preparations and they are not put in the
    same way: Prepare stands on its one link, Prepare receptor is shared by every docking card the
    target feeds. Anything that only asks whether a preparation is wanted reads "" as no, as it
    always read False.

    Asked of the sockets rather than of a list of card names, so a tool added to a catalogue on
    the cluster is prepared for without anything here being taught its name.
    """
    a, b = BY_KEY.get(from_card), BY_KEY.get(to_card)
    if a is None or b is None or a.fixed:
        return ""                  # what leaves a preparation is never prepared a second time
    out, into = a.port_out(from_port), b.port_in(to_port)
    if out is None or into is None:
        return ""
    # Only where the two could not have been linked anyway: a fold handing the target card its
    # structure is a structure given to the one card whose business is to name a site on it, and
    # nothing is prepared for that.
    if any(into.takes(k) for k in out.kinds):
        return ""
    for prep in preparations():
        if (any(prep.inputs[0].takes(k) for k in out.kinds)
                and any(into.takes(k) for k in prep.outputs[0].kinds)):
            return prep.key
    return ""


def link_refused(from_card, from_port, to_card, to_port, settings=None):
    """Why this link cannot be drawn, or "" when it can.

    The canvas asks before it draws and the runner asks again before it submits, because a flow
    can reach the cluster from a file as well as from the canvas.
    """
    a, b = BY_KEY.get(from_card), BY_KEY.get(to_card)
    if a is None or b is None:
        return "there is no such card"
    out, into = a.port_out(from_port), b.port_in(to_port)
    if out is None:
        return "%s leaves nothing called %s" % (a.name, from_port)
    if into is None:
        return "%s takes nothing called %s" % (b.name, to_port)
    kind = next((k for k in out.kinds if into.takes(k)), "")
    if not kind:
        # A link that needs a preparation is a link that can be drawn: the canvas puts the
        # preparation in the middle of it, and what reaches the card below is prepared. Said here
        # rather than in the canvas because the runner reads the same answer out of this file.
        if prepared_between(from_card, from_port, to_card, to_port):
            return ""
        # Where the same card has another socket that would fit, say which. Two sockets a few
        # pixels apart, one of which is the one wanted, is the likeliest reason anybody is
        # reading this at all.
        instead = next((p.label for p in a.outputs
                        if p.key != from_port and any(into.takes(k) for k in p.kinds)), "")
        return ("%s carries %s, and %s takes %s.%s"
                % (out.label, " or ".join(KINDS[k] for k in out.kinds), into.label,
                   " or ".join(KINDS[k] for k in into.kinds),
                   (" Try %s instead." % instead) if instead else ""))
    return ""


def link_warning(from_card, from_port, to_card, to_port, settings=None):
    """What is worth saying about a link that is allowed anyway, or "" when there is nothing.

    Length is a caution rather than a refusal. What the target card asks for is what the design
    tool will aim at, not what it will produce, and a person who shortens the binder afterwards
    should not have to draw the link again to be let through. The run decides: designs that are
    short enough are docked and the rest are left behind by name, which is what the record shows.
    """
    a, b = BY_KEY.get(from_card), BY_KEY.get(to_card)
    if a is None or b is None:
        return ""
    out = a.port_out(from_port)
    if out is None or not out.sized or b.key != "adcp":
        return ""
    longest = (settings or {}).get("binder_max")
    try:
        longest = int(longest)
    except (TypeError, ValueError):
        return ""
    if longest > DOCKABLE_MAX_LENGTH:
        return ("the docking takes peptides of %d residues or fewer, and this flow asks for "
                "binders of up to %d. Designs longer than %d will be left behind rather than "
                "docked; shorten the binder, or simulate them directly instead."
                % (DOCKABLE_MAX_LENGTH, longest, DOCKABLE_MAX_LENGTH))
    return ""
