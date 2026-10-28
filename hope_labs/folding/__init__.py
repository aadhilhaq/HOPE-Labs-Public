"""The pages for the two folding tools: Boltz-2 and ColabFold.

By Aadhil Haq

Neither install has a page inside it, so each is driven by one of ours, in this repository, the
way RFdiffusion's and BoltzGen's are (hope_labs/pages). What is different about these two is the
shape of the form rather than the shape of the machinery: a design run has a target and a binder,
and a folding run has a list of entities. The lab asked for an entity form, a row per entity
with a type, a sequence and a number of copies, and that is what this serves.

These are a sibling of hope_labs/pages rather than two more tools inside it. The page there is
drawn from a card in hope_flow/cards.py and submits through hope_flow's own target-then-binder
flow; there is no target here, no binder, and no flow either tool belongs to. What IS shared is
shared by importing it: the documentation kit and the credit bar (hope_labs/docskit.py), the
colors and the shell of the page (hope_labs/pages/web/style.css), and everything about reading a
run folder that does not depend on which tool wrote it (hope_labs/pages/runs.py). The handshake
is the same line in the same shape, so the launcher starts all four the same way.

    python -m hope_labs.folding.serve boltz2 --host 127.0.0.1 --port 8140

--dry-run writes the run folder, the input the tool will read and the job script, and queues
nothing. It also skips the alignment fetch, so a dry run needs no network either.

THE THING THAT SHAPES BOTH OF THESE: a compute node on this cluster has no route to the
internet. It resolves names and every connection fails. Both tools want an alignment from
https://api.colabfold.com and neither can have one from inside the job, so both run in two
phases:

  1. On the login node, where there is a network, the alignments are fetched and written into
     the run folder.
  2. In the GPU job, with no network at all, the structure is predicted from the alignments on
     disk and the weights already in the install.

A page that offered to "fold this" in one step would queue a job that cannot work, which is what
happened to a real run here: it died with "Network is unreachable" after its alignments were
fetched and its GPU allocated.
"""
from __future__ import annotations

import re

#: The tools this page can serve. Each is a tool in hope_labs/tools.py under the same key, and
#: the page refuses anything else rather than drawing a form for a tool it knows nothing about.
SERVES = ("boltz2", "colabfold")


class Entity:
    """One kind of thing that can go in a row of the form.

    The alphabet is the point. Neither tool refuses a letter it does not know: Boltz-2 maps every
    unknown character to its unknown token and folds the result, so a lowercase sequence becomes
    a chain of nothing but UNK and a run that looks like it worked. The letters are therefore
    checked here, where it costs nothing, rather than discovered in a structure that is wrong.
    """

    def __init__(self, kind, label, what, alphabet="", unknown="", min_len=4, max_len=4000,
                 placeholder="", counts=True, given="sequence", choices=()):
        self.kind = kind
        self.label = label
        self.what = what                  # the sentence under the type, on the page
        #: The letters the tool has a residue for. "" for anything that is not a chain: a code
        #: or a SMILES has no alphabet in this sense.
        self.alphabet = alphabet
        #: The letter that means "something, but not one of those". Allowed, because a sequence
        #: from a database carries them and refusing them would send people to edit their input;
        #: counted separately on the page, because a sequence that is mostly these is a mistake.
        self.unknown = unknown
        self.min_len = min_len
        self.max_len = max_len
        self.placeholder = placeholder
        #: Whether copies mean anything for this type. They do for every type either tool takes;
        #: kept as a field because the form reads it rather than knowing which types are which.
        self.counts = counts
        #: How the row is filled in: "sequence" (a chain, letter by letter), "pick" (one of
        #: `choices`, each a CCD code and its name), "code" (any CCD code, typed), or "smiles".
        self.given = given
        self.choices = tuple(choices)

    def letters(self):
        return self.alphabet + self.unknown

    def as_json(self):
        return {"kind": self.kind, "label": self.label, "what": self.what,
                "alphabet": self.alphabet, "unknown": self.unknown,
                "min_len": self.min_len, "max_len": self.max_len,
                "placeholder": self.placeholder, "counts": self.counts,
                "given": self.given, "choices": [list(c) for c in self.choices],
                "modifications": [list(m) for m in MODIFICATIONS.get(self.kind, ())]}


#: The twenty standard amino acids, as Boltz-2's own table has them
#: (boltz/data/const.py, prot_letter_to_token). X, and also B, J, O, U and Z, are in that table
#: as the unknown token; X is the one anybody writes, so it is the one offered.
PROTEIN = Entity(
    "protein", "Protein",
    "One chain. Give the chain, not the whole complex: another row is another chain.",
    alphabet="ACDEFGHIKLMNPQRSTVWY", unknown="X",
    min_len=4, max_len=4000,
    placeholder="Type or paste one protein chain, in one-letter codes")

#: A, C, G, T and N, which is Boltz-2's whole DNA alphabet. U is not in it: a U under DNA becomes
#: the unknown base DN without a word said, so it is refused here with the sentence that says so.
DNA = Entity(
    "dna", "DNA",
    "One strand. A duplex is two rows, or one row with two copies if the strands are the same.",
    alphabet="ACGT", unknown="N",
    min_len=4, max_len=1000,
    placeholder="Type or paste one DNA strand")

#: A, C, G, U and N. T is not in it, and a DNA sequence pasted into an RNA row turns every T into
#: N silently, which is the single easiest way to fold the wrong molecule here.
RNA = Entity(
    "rna", "RNA",
    "One strand, in RNA letters.",
    alphabet="ACGU", unknown="N",
    min_len=4, max_len=1000,
    placeholder="Type or paste one RNA strand")

#: The common ligands and ions, offered by name, with the lab's own
#: additions after them where that list is short for the work done here (see IONS), as the
#: Chemical Component
#: Dictionary codes Boltz-2 takes. Each was looked for in the install's own cache of components,
#: which is where a code has to be for Boltz-2 to build it, and read for what it is made of: the
#: phosphates of each nucleotide, the iron of each heme, one atom of the right charge for each
#: ion. Four chlorophylls (CLA, CHL, BCL, BCB) are left out because the cache
#: does not have them, and a run asking for one would fail on the GPU rather than here.
LIGANDS = (
    ("ADP", "adenosine diphosphate"), ("ATP", "adenosine triphosphate"),
    ("AMP", "adenosine monophosphate"), ("GTP", "guanosine triphosphate"),
    ("GDP", "guanosine diphosphate"), ("FAD", "flavin adenine dinucleotide"),
    ("NAD", "nicotinamide adenine dinucleotide"), ("NAP", "NADP"), ("NDP", "NADPH"),
    ("HEM", "heme"), ("HEC", "heme C"), ("PLM", "palmitic acid"), ("OLA", "oleic acid"),
    ("MYR", "myristic acid"), ("CIT", "citric acid"),
)
IONS = (
    ("MG", "Mg\u00b2\u207a, magnesium"), ("ZN", "Zn\u00b2\u207a, zinc"), ("CL", "Cl\u207b, chloride"),
    ("CA", "Ca\u00b2\u207a, calcium"), ("NA", "Na\u207a, sodium"), ("MN", "Mn\u00b2\u207a, manganese"),
    ("K", "K\u207a, potassium"), ("FE", "Fe\u00b3\u207a, iron"), ("CU", "Cu\u00b2\u207a, copper"),
    ("CO", "Co\u00b2\u207a, cobalt"),
    # Past that list, and the lab's own, for one reason: every metal above is in a
    # single oxidation state, and the state that list carries for iron is the one a
    # ferroxidase does not transport. FtrABCD takes up Fe2+ and the mineral it stores is Fe3+, so
    # a list whose only iron is FE - which is genuinely the ferric code, correctly labeled - hands
    # somebody studying iron uptake the wrong ion with nothing said, because the tile reads
    # "iron". The same holds for the copper the cycle turns over. Both codes were looked for in
    # the install's component cache, as every code above was, and both are there.
    ("FE2", "Fe\u00b2\u207a, iron (ferrous)"), ("CU1", "Cu\u207a, copper (cuprous)"),
)

#: Heavy atoms in each listed component, read from the same cache. Boltz-2 makes a token of every
#: atom of a small molecule, so this is what each costs (folding/job.py, tokens_of).
HEAVY_ATOMS = {
    "ADP": 27, "ATP": 31, "AMP": 23, "GTP": 32, "GDP": 28, "FAD": 53, "NAD": 44, "NAP": 48,
    "NDP": 48, "HEM": 43, "HEC": 43, "PLM": 18, "OLA": 20, "MYR": 16, "CIT": 13,
    "MG": 1, "ZN": 1, "CL": 1, "CA": 1, "NA": 1, "MN": 1, "K": 1, "FE": 1, "CU": 1, "CO": 1,
    "FE2": 1, "CU1": 1,
}
#: What a code that is not listed is counted as: a typical drug-sized molecule.
TYPICAL_LIGAND_ATOMS = 30

#: The common modifications, by the kind of chain: (CCD code, name, the
#: residue it is made from). Every one is in Boltz-2's cache. The residue was found by matching
#: each component against the standard residues in the same cache rather than remembered, and
#: settled by the formula where the match alone could not tell: citrulline (CIR) comes from
#: arginine, and 5HC, 1CC, 5FC and RSQ have a cytosine's three nitrogens, not a uracil's two.
#: 3DR is an abasic site, a sugar with no base, so it can stand in for any base ("*").
#: The page offers only the modifications of the residue that was clicked, and the server checks
#: the same, because Boltz-2 does not: it would put a phosphoserine wherever it was told. Any
#: other code can still be typed; that is checked to exist, not for its residue.
MODIFICATIONS = {
    "protein": (("SEP", "phosphoserine", "S"), ("TPO", "phosphothreonine", "T"),
                ("PTR", "phosphotyrosine", "Y"), ("NEP", "N1-phosphonohistidine", "H"),
                ("HIP", "ND1-phosphonohistidine", "H"), ("ALY", "N6-acetyllysine", "K"),
                ("MLY", "N6,N6-dimethyllysine", "K"), ("M3L", "N6,N6,N6-trimethyllysine", "K"),
                ("MLZ", "N6-methyllysine", "K"), ("2MR", "dimethylarginine", "R"),
                ("AGM", "5-methylarginine", "R"), ("MCS", "malonylcysteine", "C"),
                ("HYP", "4-hydroxyproline", "P"), ("HY3", "3-hydroxyproline", "P"),
                ("LYZ", "5-hydroxylysine", "K"), ("AHB", "beta-hydroxyasparagine", "N"),
                ("P1L", "S-palmitoylcysteine", "C"), ("SNN", "aminosuccinimide", "N"),
                ("SNC", "S-nitrosocysteine", "C"), ("TRF", "N1-formyltryptophan", "W"),
                ("KCR", "N6-crotonyllysine", "K"), ("CIR", "citrulline", "R"),
                ("YHA", "homocitrulline", "K")),
    "dna": (("5CM", "5-methylcytosine", "C"), ("C34", "N4-methylcytosine", "C"),
            ("5HC", "5-hydroxymethylcytosine", "C"), ("6OG", "O6-methylguanine", "G"),
            ("6MA", "N6-methyladenine", "A"), ("1CC", "5-carboxycytosine", "C"),
            ("8OG", "8-oxoguanine", "G"), ("5FC", "5-formylcytosine", "C"),
            ("3DR", "abasic site", "*")),
    "rna": (("PSU", "pseudouridine", "U"), ("5MC", "5-methylcytidine", "C"),
            ("OMC", "2\u2032-O-methylcytidine", "C"), ("4OC", "N4,2\u2032-O-dimethylcytidine", "C"),
            ("5MU", "5-methyluridine", "U"), ("OMU", "2\u2032-O-methyluridine", "U"),
            ("UR3", "3-methyluridine", "U"), ("A2M", "2\u2032-O-methyladenosine", "A"),
            ("MA6", "N6,N6-dimethyladenosine", "A"), ("6MZ", "N6-methyladenosine", "A"),
            ("2MG", "N2-methylguanosine", "G"), ("OMG", "2\u2032-O-methylguanosine", "G"),
            ("7MG", "7-methylguanosine", "G"), ("RSQ", "5-formylcytidine", "C")),
}

#: A Chemical Component Dictionary code: one to five capitals and digits.
CCD_CODE = re.compile(r"^[A-Z0-9]{1,5}$")

LIGAND = Entity(
    "ligand", "Ligand",
    "A small molecule, by its Chemical Component Dictionary code: the common ones are listed.",
    min_len=1, max_len=5, given="pick", choices=LIGANDS)

ION = Entity(
    "ion", "Ion",
    "One ion. Several of the same ion are copies of one row.",
    min_len=1, max_len=5, given="pick", choices=IONS)

#: Any component in the Chemical Component Dictionary, by its code. Checked against the install's
#: cache, one file per code, because a code Boltz-2 does not have fails on the GPU, not here.
CCD = Entity(
    "ccd", "CCD Code",
    "Any component in the Chemical Component Dictionary, by its code.",
    min_len=1, max_len=5, given="code", placeholder="a code, for example ATP or HEM")

#: A small molecule as SMILES, for one no code names: Boltz-2
#: reads it with RDKit and standardizes it itself.
SMILES = Entity(
    "smiles", "SMILES",
    "A small molecule, as SMILES. Boltz-2 reads it with RDKit and standardizes it itself.",
    min_len=1, max_len=400, given="smiles",
    placeholder="SMILES, for example CC(=O)Oc1ccccc1C(=O)O")

#: The kinds that are chains, with residues to number and modify, and the kinds that are not:
#: each of those is one small molecule or ion, written to Boltz-2 as a ligand.
POLYMERS = ("protein", "dna", "rna")
CHEMICAL = ("ligand", "ion", "ccd", "smiles")

#: What each tool's form may offer. ColabFold gets protein and nothing else, because it has no
#: model for anything else: it would take a nucleic acid row in its FASTA only by treating the
#: letters as amino acids. A form that offered a type the tool cannot fold would be offering to
#: waste a GPU booking.
ENTITIES = {
    "boltz2": (PROTEIN, DNA, RNA, LIGAND, ION, CCD, SMILES),
    "colabfold": (PROTEIN,),
}

#: What a structure may be called, when the rows are read off one rather than typed in. An mmCIF
#: is in the list because the lab's own folding runs write one: somebody folding a complex they
#: have just folded should not have to convert it first, and a structure of more than 62 chains
#: has no PDB file at all. One list, read both by the dialog that lists files on the cluster and
#: by the route that reads one, so every file the dialog offers is a file the page will read.
STRUCTURE_FILES = (".pdb", ".ent", ".cif", ".mmcif")

#: The chain ids a folding job can hand out, in order. Boltz-2 names chains itself from these and
#: ColabFold names them A, B, C by position in the FASTA record, so the same letters describe
#: both. Twenty-six is the cap on chains for that reason, and it is far above anything either
#: tool will fold in one job.
CHAIN_IDS = tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

#: The most copies one row may ask for. A number rather than no limit, because copies multiply
#: into chains and the chains come out of CHAIN_IDS: twenty of one thing and seven of another is
#: a job that cannot be given chain names.
MAX_COPIES = 20

#: Tokens in one job, over which it is refused, and over which it is allowed with a word said.
#: Both tools scale with the square of the token count in memory, and the cards differ by a
#: factor of five across this cluster, so the limit that matters is the card's and the page says
#: which card it has in mind (folding/job.py, fits_on).
WARN_TOKENS = 1500
MAX_TOKENS = 5000
