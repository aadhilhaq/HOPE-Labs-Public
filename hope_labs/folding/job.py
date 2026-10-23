"""What each folding tool is literally told, and the two phases that tell it.

By Aadhil Haq

This is the driver for the two tools, and it is a driver of the kind hope_flow/drivers.py calls
the exception: neither install has a submitting script or a page of its own, so there is nothing
to hand a command line to and the job is written here. Everything the job will be told is built
here as text, so the page can show it before anything is queued: for Boltz-2 that is the YAML,
for ColabFold the FASTA record, and for both the sbatch script. Getting a chain id or a copy
count wrong does not fail. It folds something else, for hours.

The two phases
--------------
A compute node on this cluster has no route to the internet, so the work splits where the network
is:

    fetch_alignments()   on the login node, with a network: the alignment per protein sequence,
                         written into the run folder.
    script_text()        in the GPU job, with no network at all: the structure, from those
                         alignments and the weights already in the install.

Boltz-2's alignments come back as the CSV its own compute_msa writes (helpers/boltz_msa.py says
why that is a helper). ColabFold's come back from colabfold_batch --msa-only, with both phases sharing one output
folder so the second finds what the first left.

What the job checks before it trusts the tool
---------------------------------------------
Both tools can finish with nothing to show and say nothing about it. `boltz predict` wraps every
input in a bare `except Exception`, prints "Failed to process <path>. Skipping." and exits 0, so a
YAML it could not parse costs a whole GPU booking and reports success. ColabFold skips a construct
whose alignment is missing with one line on stderr. So the job script looks for the structures
afterwards and exits non-zero when they are not there, rather than leaving the Runs screen to show
a COMPLETED run with nothing in it.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shlex
import subprocess
import sys

from . import (CCD_CODE, CHEMICAL, HEAVY_ATOMS, MAX_TOKENS, SERVES, TYPICAL_LIGAND_ATOMS,
               WARN_TOKENS)
from . import sites as sitesmod

HERE = os.path.dirname(os.path.abspath(__file__))

#: Where the lab's copies are, as hope_labs/tools.py has them. One setting moves both, the same
#: way hope_flow/drivers.py reads HOPEFLOW_LAB_ROOT for the other five tools.
LAB_ROOT = os.environ.get("HOPEFLOW_LAB_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
INSTALLS = {
    "boltz2": LAB_ROOT + "/envs/boltz",
    "colabfold": LAB_ROOT + "/envs/colabfold",
}

#: Where a tool's runs go when nobody says otherwise. The person's own scratch, because folding
#: output is gigabytes and home is 10 GB on this cluster.
RUNS = {
    "boltz2": os.path.join(os.environ.get("SCRATCH", "/tmp"), "boltz2_runs"),
    "colabfold": os.path.join(os.environ.get("SCRATCH", "/tmp"), "colabfold_runs"),
}

#: Cores and memory in GB. Not on the form, because neither is a choice anybody should have to
#: make, and here rather than in the script so the page can show what will be asked for before
#: anything is queued. Boltz-2 gets the larger allowance: it holds a 2.2 GB checkpoint and the
#: pair representation of the whole complex at once.
RESOURCES = {
    "boltz2": {"cpus": 8, "mem": 64},
    "colabfold": {"cpus": 8, "mem": 48},
}

#: Walltimes sbatch takes, from `man sbatch`. Checked on the form, where a mistake is cheap.
WALLTIME = re.compile(r"^(\d+|\d+:\d{1,2}|\d{1,3}:\d{1,2}:\d{1,2}|"
                      r"\d+-\d{1,2}(:\d{1,2}){0,2})$")

#: Roughly how many residues each card has been seen to manage, for the sentence on the form.
#: A guideline and said as one: it depends on how many chains the residues are in and on what the
#: tool decides to keep. It is here because the cards on this cluster differ by a factor of five
#: and the difference is the whole of whether a run finishes.
CARD_RESIDUES = {"h100": 4000, "a100": 2500, "a40": 2500, "rtx": 1200, "a30": 1200, "t4": 900}

#: Boltz-2's weights, already fetched. --cache has to be passed: without it boltz falls back to
#: ~/.boltz, finds nothing, and tries to download six gigabytes from a node with no route out.
#: That is the single most likely way for a first run to fail.
BOLTZ_CACHE = LAB_ROOT + "/envs/boltz/cache"

#: ColabFold's AlphaFold weights. The OUTER params folder, not params/params: ColabFold's own
#: models.py appends "params" to whatever --data is given, so the inner one sends it looking for
#: params/params/params.
CF_PARAMS = "params"

#: The one model type that can run here without a network, and the reason ColabFold's own `auto`
#: cannot be used. `auto` picks alphafold2_ptm for a single chain, and this install has only
#: params_model_2_ptm.npz of the five that needs; worse, the marker file ColabFold checks before
#: deciding it already has weights (download_finished.txt) is not there, so it tries to reach
#: storage.googleapis.com before it looks at a single file. The multimer_v3 weights are all five
#: present and their marker is there, so that is what is asked for, for one chain as well as for
#: twelve.
CF_MODEL = "alphafold2_multimer_v3"


class Setting:
    """One control on the form that the job actually reads.

    Shaped like a hope_flow card's setting and read the same way by the page, so the rail is
    built from this list and the page itself knows nothing about either tool.
    """

    def __init__(self, key, label, kind, default, choices=(), why="", advanced=False,
                 optional=False, least=None, most=None, step=None, placeholder="", notes=None):
        self.key = key
        self.label = label
        self.kind = kind                  # text, number, duration, choice, yesno
        self.default = default
        self.choices = tuple(choices)
        self.why = why
        self.advanced = advanced
        self.optional = optional
        #: What a number may be, which is what the arrows on the box step between and what stops
        #: a box taking 500 models as readily as 5. The same three names the cards use.
        self.least = least
        self.most = most
        self.step = step
        #: The grey words in an empty box, where empty is a real answer.
        self.placeholder = placeholder
        #: {choice: one line}, so a menu can say what each of its values is.
        self.notes = dict(notes or {})

    def as_json(self):
        doc = {"key": self.key, "label": self.label, "kind": self.kind,
               "default": self.default, "choices": list(self.choices), "why": self.why,
               "advanced": self.advanced, "optional": self.optional}
        for name in ("least", "most", "step", "placeholder"):
            value = getattr(self, name)
            if value not in (None, ""):
                doc[name] = value
        if self.notes:
            doc["notes"] = dict(self.notes)
        return doc


#: The alignment choice, which is the one setting on these forms that changes what happens where.
#: It is the same question for both tools and it is asked in the same words.
def _alignments_setting():
    return Setting(
        "alignments", "Alignments", "choice", "fetch", ("fetch", "single"),
        why="Fetch asks the ColabFold MSA server for an alignment per protein chain, here on the "
            "login node, before the job is queued. Single folds from the sequence alone: no "
            "waiting and no network, and markedly worse structures. A compute node cannot reach "
            "the server, so this is decided now rather than in the job.")


def _any_card(site):
    """Whether this site's cards are unknown here, so a job can only ask for a GPU of any kind."""
    return site.gpu_types() == [sitesmod.ANY_GPU]


def _card_menu(site):
    """The Card menu: auto, then the cards there are to choose between."""
    return (sitesmod.AUTO,) if _any_card(site) else (sitesmod.AUTO,) + tuple(site.gpu_types())


def _resource_settings(site):
    """The card, the walltime and the account, with this site's cards in the menu."""
    return [
        # auto first and auto by default, because the card is the one resource on this form
        # that a person has no way to choose well: what matters is how much card memory the
        # complex needs, and the page has already worked that out. Picking one is for somebody
        # who wants a smaller card to get through the queue sooner, or a40 to leave the a100s
        # alone, and both of those are deliberate acts rather than the common case.
        #
        # On a cluster nobody here has a card table for there is nothing to pick between, so the
        # menu is auto alone rather than auto and one synonym for it.
        Setting("gpu", "Card", "choice", sitesmod.AUTO, _card_menu(site),
                why=("Nobody here has a table of %s's cards, so the job asks for a GPU without "
                     "naming a kind and Slurm gives it whichever it has." % site.name)
                    if _any_card(site) else
                    ("Left on auto the job asks for %s, the largest card %s has. The rest are "
                     "there to ask for a smaller one on purpose." % (site.card(), site.name))),
        Setting("walltime", "Walltime", "duration", "04:00:00",
                least=600, most=int(site.max_gpu_hours() * 3600), step=1800,
                why="%s allows %g hours at most on %s. Importing the tool takes the first "
                    "five minutes of it. Estimate fills in an allowance from the sequences on "
                    "the form; a job that ends early is charged for what it used."
                    % (site.name, site.max_gpu_hours(), site.gpu_partition)),
        Setting("account", "Account", "text", "", optional=True,
                why="Left empty, the job is charged to your default account."),
        Setting("keep_files", "Keep every file", "yesno", False, advanced=True,
                why="A finished run packs what its results no longer read, Boltz-2's "
                    "preprocessed inputs and the alignment search's working files, into one "
                    "archive in its folder; python -m hope_labs.folding.tidy RUN TOOL --undo "
                    "puts them back. Tick this to keep every file as the tool wrote it."),
    ]


def settings(tool, site=None):
    """Every control the form offers for this tool, in the order it shows them."""
    site = site or sitesmod.here()
    if tool == "boltz2":
        own = [
            _alignments_setting(),
            Setting("diffusion_samples", "Structures to write", "number", 1,
                    least=1, most=25, step=1,
                    why="Each is a separate sample of the same model. Five takes about five "
                        "times as long and gives something to compare."),
            Setting("recycling_steps", "Recycling steps", "number", 3,
                    least=1, most=10, step=1,
                    why="Boltz-2's own default. More costs time roughly in proportion."),
            Setting("output_format", "Structure format", "choice", "mmcif", ("mmcif", "pdb"),
                    why="mmCIF is Boltz-2's own default and the only one that can carry a long "
                        "chain correctly. PDB is easier to hand to older tools."),
            Setting("sampling_steps", "Sampling steps", "number", 200, advanced=True,
                    least=10, most=1000, step=10,
                    why="Steps in the diffusion. Boltz-2's default is 200."),
            Setting("seed", "Seed", "number", "", advanced=True, optional=True,
                    least=1, most=2 ** 31 - 1, step=1, placeholder="no seeding",
                    why="No seeding is Boltz-2's default. A number makes the sampling "
                        "repeatable."),
            Setting("use_potentials", "Steering potentials", "yesno", False, advanced=True,
                    why="Slower, and upstream reports structures that make better physical "
                        "sense."),
        ]
    elif tool == "colabfold":
        own = [
            _alignments_setting(),
            Setting("num_models", "Models", "number", 5, least=1, most=5, step=1,
                    why="ColabFold's own default is all five. Each is a separate AlphaFold model "
                        "and they are ranked afterwards."),
            Setting("num_recycle", "Recycling steps", "number", 3, least=1, most=12, step=1,
                    why="What the lab's co-folding has always asked for. ColabFold's own default "
                        "is left to the model config, which makes timings hard to compare."),
            Setting("num_seeds", "Seeds", "number", 1, advanced=True, least=1, most=20, step=1,
                    why="More seeds means more samples per model, and more time in proportion."),
            Setting("rank", "Rank by", "choice", "auto",
                    ("auto", "plddt", "ptm", "iptm", "multimer"), advanced=True,
                    notes={"auto": "multimer for a complex, pLDDT for one chain",
                           "plddt": "the model's own confidence, per residue",
                           "ptm": "predicted TM-score for the whole structure",
                           "iptm": "predicted TM-score across the interface",
                           "multimer": "0.8 iptm and 0.2 ptm, which is AlphaFold-Multimer's own"},
                    why="auto is multimer for a complex and pLDDT for one chain."),
        ]
    else:
        raise ValueError("%s is not a folding tool this page serves" % tool)
    return own + _resource_settings(site)


def default_settings(tool, site=None):
    return {s.key: s.default for s in settings(tool, site)}


# --- what the job is told ----------------------------------------------------------------------

def _yaml_quote(text):
    """A YAML single-quoted scalar, which is the only form that carries a SMILES safely.

    A SMILES has # in it for a triple bond, which starts a comment in plain YAML, and it has [ ]
    which start a flow sequence. Single quotes make the whole of it literal; the one character
    that then matters is the quote itself, which YAML escapes by doubling.
    """
    return "'" + str(text).replace("'", "''") + "'"


def _ids(chains):
    """id: ["A", "B"], always a list and always quoted.

    A list even for one chain, because that is the form Boltz-2 handles without a special case,
    and quoted because yaml.safe_load reads YAML 1.1: a bare N, Y, on or off is a boolean there,
    and chain N would arrive as False and be dropped by the parser's isinstance checks without a
    word said.
    """
    return "[" + ", ".join('"%s"' % c for c in chains) + "]"


def yaml_text(name, rows, msa_for=None):
    """Boltz-2's input, exactly as the job will read it.

    `rows` is the checked form: a list of {kind, sequence, copies, chains, name, modifications}.
    `msa_for` maps a protein sequence to the alignment written for it, or is None for a run
    folding from the sequence alone.

    Every protein chain carries an msa line, and that is not optional. Boltz-2 refuses to mix
    alignments it was given with alignments it would fetch itself ("Cannot mix custom and
    auto-generated MSAs in the same input!"), and a protein with no msa line at all is an
    alignment it would fetch itself. So a chain with nothing fetched for it says `empty`, which is
    Boltz-2's own word for single-sequence mode.

    A ligand, an ion and a CCD code are all one thing to Boltz-2: a ligand with a `ccd:`. The code
    is quoted, because yaml.safe_load reads YAML 1.1 and the component NO (nitric oxide) would
    otherwise arrive as False. A SMILES is a ligand with a `smiles:`, quoted for its # and its
    colons.
    """
    out = ["# Boltz-2 input written by HOPE Labs for the run %s." % name,
           "# Every protein chain names its alignment: a chain with none would send Boltz-2 to",
           "# the MSA server, and a compute node cannot reach it.",
           "version: 1",
           "sequences:"]
    for row in rows:
        kind = row["kind"]
        if kind in CHEMICAL:
            out.append("  - ligand:")
            out.append("      id: %s" % _ids(row["chains"]))
            out.append("      %s: %s" % ("smiles" if kind == "smiles" else "ccd",
                                         _yaml_quote(row["sequence"])))
            continue
        out.append("  - %s:" % kind)
        out.append("      id: %s" % _ids(row["chains"]))
        out.append("      sequence: %s" % row["sequence"])
        if kind == "protein":
            got = (msa_for or {}).get(row["sequence"], "")
            out.append("      msa: %s" % (got or "empty"))
        mods = row.get("modifications") or []
        if mods:
            # Positions count from 1, as Boltz-2's own schema has them and as the page numbers
            # the residues.
            out.append("      modifications:")
            for mod in mods:
                out.append("        - position: %d" % int(mod["position"]))
                out.append("          ccd: %s" % _yaml_quote(mod["ccd"]))
    return "\n".join(out) + "\n"


def fasta_text(name, rows):
    """ColabFold's input, exactly as the job will read it.

    One record, with the chains joined by colons and a copy written out once per copy: ColabFold
    has no count syntax for a protein chain and works out how many of each there are by counting
    the repeats.

    The header is the run's name and the file is named after it, and those two must match. Phase
    one names the alignment after the HEADER, through its own safe_filename; phase two looks for
    an alignment named after the FILE. They agree here because they are the same string, and a
    page that let them drift would fetch an alignment phase two could not find and then skip the
    prediction with one line on stderr.
    """
    chains = []
    for row in rows:
        chains += [row["sequence"]] * int(row["copies"])
    return ">%s\n%s\n" % (name, ":".join(chains))


def token_total(rows):
    """Tokens in the job, copies counted: what decides whether it fits on a card."""
    return sum(tokens_of(row) * int(row["copies"]) for row in rows)


def tokens_of(row):
    """What one copy of a row costs Boltz-2, in tokens.

    A residue of a chain is one token, modified or not (boltz/data/tokenize/boltz2.py tokenises a
    modified residue "at residue level"). Anything that is not a chain is a token per heavy atom,
    since Boltz-2 tokenises a non-polymer atom by atom: ATP is 31 tokens, not one. The atoms are
    counted from the SMILES for a SMILES row and looked up for a listed code; a code that is not
    listed is counted as a typical drug-sized molecule, near enough for which card it needs,
    because the exact count is in the cache and reading it takes RDKit, which the form does not
    load.
    """
    kind, seq = row["kind"], row["sequence"]
    if kind in CHEMICAL:
        if kind == "smiles":
            return max(1, smiles_atoms(seq))
        return HEAVY_ATOMS.get(seq, TYPICAL_LIGAND_ATOMS)
    return len(seq)


#: Atoms in a SMILES: a bracket atom, or one of the organic subset written bare. Hydrogens in
#: brackets are not heavy atoms and are not counted; Cl and Br are tried before C and B.
SMILES_ATOM = re.compile(r"\[([^\]]+)\]|Cl|Br|[BCNOPSFI]|[bcnops]")


def smiles_atoms(smiles):
    """Heavy atoms in a SMILES, read off the string."""
    n = 0
    for found in SMILES_ATOM.finditer(str(smiles or "")):
        inside = found.group(1)
        if inside is not None and re.match(r"^\d*H(?![a-z])", inside):
            continue
        n += 1
    return n


def has_kernels(install):
    """Whether this Boltz-2 install has the CUDA kernels' module (the `cuda` extra) in it.

    Asked of the folder rather than of Python, because importing anything from a Boltz-2
    environment means importing torch, which takes minutes on a shared file system. An install
    that cannot be read is assumed to have them, which is what every install before this had.
    """
    found = glob.glob(os.path.join(install or INSTALLS["boltz2"], "lib", "python3*",
                                   "site-packages", "cuequivariance_torch"))
    if found:
        return True
    return not glob.glob(os.path.join(install or INSTALLS["boltz2"], "lib", "python3*",
                                      "site-packages", "boltz"))


def ccd_folder(install=""):
    """Where Boltz-2 reads its components from: the cache the job is given with --cache."""
    return os.path.join(install or INSTALLS["boltz2"], "cache", "mols")


def ccd_known(install, code):
    """Whether Boltz-2 has the component, as its own cache says: one file per code.

    True or False when the cache can be read from here, and None when it cannot, which is said
    once already by the check that the install is here at all."""
    folder = ccd_folder(install)
    if not CCD_CODE.match(str(code or "")):
        return False
    if not os.path.isdir(folder):
        return None
    return os.path.isfile(os.path.join(folder, code + ".pkl"))


def fits_on(gpu, tokens):
    """What to say about this many residues on this card, or "".

    A guideline, said as one. Both tools hold something that grows with the square of the token
    count, so the card is what decides whether a run finishes rather than the walltime, and the
    cards here run from 16 GB to 80.
    """
    room = CARD_RESIDUES.get(gpu)
    if not room:
        # A cluster read by Set up names its cards as Slurm does (nvidia_a100-sxm4-80gb); the
        # table is by kind of card, so the kind is looked up instead.
        from ..setup import profile as profilemod
        room = CARD_RESIDUES.get(profilemod.family(gpu))
    if not room or tokens <= room:
        return ""
    return ("%d residues on %s, which has been seen to manage about %d. It may run out of card "
            "part way through; a larger card is the fix." % (tokens, gpu, room))


# --- phase one: the alignments, on the login node -----------------------------------------------

def msa_dir(run, tool, name):
    """Where phase one writes, which for ColabFold is also where phase two writes.

    ColabFold decides what to predict from the files in its output folder and writes its
    alignment there, so the lab's existing arrangement gives both phases one folder and lets the
    second find what the first left. Boltz-2 is told its alignments by path, so they get a folder
    of their own.
    """
    return os.path.join(run, "msa" if tool == "boltz2" else os.path.join("fold", name))


def protein_sequences(rows):
    """{sequence: a name for its alignment}, one per distinct protein sequence.

    Distinct, because Boltz-2 groups chains by sequence and insists that every chain of one
    sequence share one alignment, and because fetching the same alignment twice is two waits for
    one answer.
    """
    out = {}
    for row in rows:
        if row["kind"] != "protein" or row["sequence"] in out:
            continue
        out[row["sequence"]] = "entity_%d" % (len(out) + 1)
    return out


def fetch_alignments(tool, install, run, name, rows, say=print, minutes=40):
    """Phase one. Returns {sequence: the alignment written for it}, or raises Refused.

    Run on the login node, from the page, before anything is queued. It is the one thing in this
    page that needs the network, and the only part of a folding run that is not on a compute node.
    """
    wanted = protein_sequences(rows)
    if not wanted:
        return {}
    where = msa_dir(run, tool, name)
    os.makedirs(where, exist_ok=True)
    if tool == "boltz2":
        return _boltz_alignments(install, where, wanted, say, minutes)
    return _colabfold_alignments(install, run, where, name, say, minutes)


def _boltz_alignments(install, where, wanted, say, minutes):
    helper = os.path.join(HERE, "helpers", "boltz_msa.py")
    python = os.path.join(install, "bin", "python")
    if not os.access(python, os.X_OK):
        raise Refused("Boltz-2's own Python is not at %s, so its alignment client cannot be run"
                      % python)
    plan = {"sequences": {name: seq for seq, name in wanted.items()}, "out": where}
    say("   | fetching %d alignment%s from the MSA server"
        % (len(wanted), "" if len(wanted) == 1 else "s"))
    out = _run([python, helper], plan=plan, where=where, say=say, minutes=minutes)
    got = _last_json(out)
    if got.get("problem"):
        raise Refused("the alignments could not be fetched: %s" % got["problem"])
    written = got.get("written") or {}
    rows = got.get("rows") or {}
    for seq, name in wanted.items():
        if name not in written:
            raise Refused("the MSA server gave nothing back for one of the protein chains")
        say("   | %s: %d sequences" % (name, rows.get(name, 0)))
    return {seq: written[name] for seq, name in wanted.items()}


def _colabfold_alignments(install, run, where, name, say, minutes):
    """colabfold_batch --msa-only, which is what the lab's co-folding has always run.

    No --data: --msa-only sets the model count to zero, and ColabFold then never looks at the
    weights at all. Passing them would only be a longer command line.
    """
    exe = os.path.join(install, "bin", "colabfold_batch")
    if not os.access(exe, os.X_OK):
        raise Refused("colabfold_batch is not at %s" % exe)
    fasta = os.path.join(run, "inputs", "%s.fasta" % name)
    say("   | fetching the alignment from the MSA server")
    _run([exe, "--msa-only", "--num-recycle", "3", fasta, where], where=where, say=say,
         minutes=minutes)
    a3m = os.path.join(where, "%s.a3m" % name)
    if not os.path.isfile(a3m):
        raise Refused("ColabFold fetched no alignment: there is no %s. Its own output is in the "
                      "run folder." % a3m)
    say("   | alignment written: %s" % a3m)
    # Keyed by nothing in particular: ColabFold takes one alignment for the whole record rather
    # than one per chain, so the caller only needs to know that it is there.
    return {"": a3m}


#: The longest a temporary folder may be before a Unix socket opened inside it is refused. The
#: kernel allows an AF_UNIX path 107 characters and Python's own pymp-<8>/listener-<8> names take
#: 32 of them, which leaves this. Measured on Grace: 75 binds and 76 does not.
TMPDIR_CAP = 75


def short_tmpdir(fallback):
    """A temporary folder short enough for a Unix socket, preferring the one we were given.

    This exists because of how Boltz-2 fails without it. Its loader runs two worker processes and
    passes their tensors over a socket under TMPDIR; when the path is too long the bind fails in
    the feeder thread, nothing catches it, and the prediction waits for data that never arrives
    rather than stopping. The run folder is deep -- a runs root, a name, a timestamp -- so it is
    exactly the wrong place to put one, and `fallback` is used only when there is nothing better.
    """
    given = (os.environ.get("TMPDIR") or "").strip()
    if given and os.path.isdir(given) and len(given) <= TMPDIR_CAP:
        return given
    # /tmp itself rather than a folder named after this process. A Grace login node sets no
    # TMPDIR at all, so this is the ordinary answer rather than the unusual one, and a page
    # restarted a dozen times should not leave a dozen empty folders on a shared machine.
    # Python names what it puts in there uniquely in any case.
    if os.path.isdir("/tmp") and os.access("/tmp", os.W_OK):
        return "/tmp"
    return os.path.join(fallback, "tmp")


def _run(argv, where, say=print, minutes=40, plan=None):
    """One command on the login node, with the caches pushed into the run folder.

    Nothing may reach $HOME: it holds 10,000 files on this cluster and both tools write caches by
    the thousand. HOME itself is moved as well, because the alignment clients write a .cache
    under it whatever XDG_CACHE_HOME says.
    """
    cache = os.path.join(where, "_cache")
    os.makedirs(cache, exist_ok=True)
    env = dict(os.environ, XDG_CACHE_HOME=cache, MPLCONFIGDIR=os.path.join(cache, "mpl"),
               CUDA_CACHE_PATH=os.path.join(cache, "nv"), TMPDIR=short_tmpdir(cache),
               HOME=cache, MPLBACKEND="Agg")
    for folder in ("mpl", "nv"):
        os.makedirs(os.path.join(cache, folder), exist_ok=True)
    os.makedirs(env["TMPDIR"], exist_ok=True)
    # Neither tool is started through a shell and neither needs one: both entry points carry an
    # absolute shebang, so there is no environment to activate and no PATH to arrange.
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    try:
        done = subprocess.run(argv, cwd=where, env=env, capture_output=True, text=True,
                              timeout=minutes * 60,
                              input=json.dumps(plan) if plan is not None else None)
    except subprocess.TimeoutExpired:
        raise Refused("the alignment fetch gave up after %d minutes. The MSA server queues when "
                      "it is busy; trying again later usually works." % minutes)
    except OSError as why:
        raise Refused("the alignment fetch could not be started: %s" % why)
    out = (done.stdout or "") + (done.stderr or "")
    for line in out.strip().splitlines()[-4:]:
        say("   | " + line[:200])
    if done.returncode != 0:
        raise Refused("the alignment fetch failed (status %d): %s"
                      % (done.returncode, out.strip()[-500:] or "it said nothing"))
    return out


def _last_json(text):
    for line in reversed((text or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {}


class Refused(ValueError):
    """Something the page will not do, said in terms the person can act on."""


# --- phase two: the GPU job ---------------------------------------------------------------------

def _q(x):
    """Shell-quote a path going into the job script.

    Every path here comes from a person: a run folder they named, an install they pointed at. A
    folder with a space in it splits into two words and the job fails on a line nobody wrote.
    """
    return shlex.quote(str(x))


#: What #SBATCH --output cannot carry. Slurm parses that line itself and does not accept quoting,
#: so a run folder holding any of these produces a job whose output goes somewhere unintended.
#: Checked before the script is written, where the message can name the folder.
UNSAFE_IN_PATH = set(" \t\n'\"\\;&|<>$`(){}[]*?!#%")


def unsafe_path(path):
    return sorted({c for c in str(path) if c in UNSAFE_IN_PATH})


HEAD = """#!/bin/bash
#SBATCH --job-name={name}
#SBATCH --partition={partition}
#SBATCH --gres={gres}
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task={cpus}
#SBATCH --mem={mem}G
#SBATCH --time={walltime}
#SBATCH --output={out}/slurm_%j.out
{account}
set -uo pipefail
umask 002
OUT={outq}

# Nothing may reach $HOME: it is 10 GB and 10,000 files on this cluster, and both tools write
# caches by the thousand. HOME is moved as well as the cache variables, because the alignment
# clients write under it directly whatever XDG_CACHE_HOME says.
export XDG_CACHE_HOME="$OUT/cache" MPLCONFIGDIR="$OUT/cache/mpl" CUDA_CACHE_PATH="$OUT/cache/nv"
export HOME="$OUT/cache/home" TORCH_HOME="$OUT/cache/torch"
export HF_HOME="$OUT/cache/hf" MPLBACKEND=Agg

# TMPDIR is the one of these that must NOT go in the run folder.
#
# Boltz-2 loads with two worker processes and hands their tensors back over a Unix socket opened
# under TMPDIR. The kernel caps an AF_UNIX path at 107 characters and Python spends 32 of them on
# its own pymp-/listener- names, so a TMPDIR over 75 characters makes bind() fail -- in the
# feeder thread, where nothing catches it. The prediction then waits for data that never comes,
# for the whole booking, and the only sign is one traceback above a progress bar stuck at 0.
# Measured, not guessed: 75 passes and 76 fails.
#
# A run folder long enough to cross that line is ordinary rather than unlucky. The lab's own
# runs root and a fifteen-character run name reach it, and the page allows forty-eight. So the
# scheduler's own temporary folder is left alone -- Grace gives each job /tmp/job.<id> on node
# local NVMe, which is twenty characters and nothing to do with $HOME -- and the run folder is
# used only by a site that hands the job nothing usable, where a long path is the lesser trouble.
if [ -z "${{TMPDIR:-}}" ] || [ ! -d "${{TMPDIR:-}}" ] || [ "${{#TMPDIR}}" -gt {tmpcap} ]; then
  if mkdir -p "/tmp/hopefold.${{SLURM_JOB_ID:-$$}}" 2>/dev/null; then
    export TMPDIR="/tmp/hopefold.${{SLURM_JOB_ID:-$$}}"
  else
    export TMPDIR="$OUT/cache/tmp"
    echo 'note: this node has no short temporary folder, so TMPDIR is in the run folder.'
    echo '      Boltz-2 can stop at 0% and wait there if that path is over {tmpcap} characters.'
  fi
fi
echo "temporary folder: $TMPDIR (${{#TMPDIR}} characters)"
mkdir -p "$TMPDIR" "$MPLCONFIGDIR" "$CUDA_CACHE_PATH" "$HOME" "$TORCH_HOME" "$HF_HOME"

# The page runs with PYTHONPATH pointing at the HOPE Labs checkout. Both tools' entry points
# carry an absolute shebang and find their own packages; an inherited PYTHONPATH would only give
# them a second set to choose from.
unset PYTHONPATH PYTHONHOME

# The run folder, which holds no directory named after a Python package. That is not an idle
# remark: the first entry of sys.path is the working directory, and a folder called matplotlib
# beside the script is imported as matplotlib. It happens: /tmp/matplotlib exists on this login
# node, and with it as the working directory Boltz-2 stops with "No module named
# matplotlib.axes" after five minutes of importing torch.
cd "$OUT" || exit 1

echo '--- node ---'
hostname
nvidia-smi -L 2>&1 | head -4 || echo 'no nvidia-smi on this node'
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>&1 | head -2 || true

# Stop rather than fold on the processor.
#
# The job asked for a card and the scheduler gave it one, so no card here means the driver and
# the tool disagreeing rather than a missing card. Both tools will carry on quite happily on the
# processor at something like a hundred times the cost, and a booking spent that way is worse
# than a job that refuses to start, because it looks like progress.
if ! nvidia-smi -L >/dev/null 2>&1; then
  echo
  echo 'STOPPING: there is no usable GPU on this node.'
  echo 'The job asked for one and was given one, so this is the driver rather than the queue.'
  echo 'Folding on the processor would spend the whole booking and finish nothing.'
  exit 1
fi
echo '--- folding ---'
"""

#: Said at the end of every job, after the tool has had its say. Both tools can finish having
#: produced nothing and report success, so the structures are looked for rather than assumed.
TAIL = """
status=$?
echo '--- what was written ---'
found=$(ls -1 {lookdir}/{pattern} 2>/dev/null | wc -l)
echo "$found structure file(s) in {lookshown}"
if [ "$found" -eq 0 ]; then
  echo
  echo 'STOPPING: the tool finished and wrote no structure.'
  echo '{whynone}'
  echo 'The log above is the tool'"'"'s own. The input it was given is in inputs/.'
  exit 1
fi
{tidy}echo "folding: {name} finished with status $status"
exit $status
"""

#: The run tidying itself, once its structures have been found: only after a success, and left
#: out when the form says to keep every file (tidy.py says what goes and what stays).
TIDY = """# The run tidies itself, its structures being there (hope_labs.folding.tidy).
if [ "$status" -eq 0 ]; then
  PYTHONPATH={checkout} {python} -m hope_labs.folding.tidy "$OUT" {tool} \
    || echo 'tidy: this run could not be tidied, and is as the tool left it'
fi
"""


def body(tool, install, run, name, asked, cpus):
    """The one line that does the folding, and where to look for what it wrote.

    The place to look comes back as a folder and a pattern rather than one string, because the
    folder is quoted into the script and the pattern must not be: `ls '<dir>/*.cif'` with the
    glob inside the quotes matches the literal name and reports nothing found, which would make
    every successful run look like a failure.
    """
    if tool == "boltz2":
        exe = os.path.join(install, "bin", "boltz")
        yaml = os.path.join(run, "inputs", "%s.yaml" % name)
        out = os.path.join(run, "out")
        argv = [exe, "predict", yaml,
                # --cache, always. Without it boltz falls back to ~/.boltz, finds nothing and
                # tries to download six gigabytes from a node with no route out.
                "--cache", os.path.join(install, "cache"),
                "--out_dir", out,
                "--accelerator", "gpu", "--devices", "1",
                "--recycling_steps", str(int(asked.get("recycling_steps") or 3)),
                "--sampling_steps", str(int(asked.get("sampling_steps") or 200)),
                "--diffusion_samples", str(int(asked.get("diffusion_samples") or 1)),
                "--output_format", str(asked.get("output_format") or "mmcif"),
                # Hyphens, uniquely among boltz's options, and it defaults to every core the node
                # has rather than the ones this job was given.
                "--preprocessing-threads", str(int(cpus)),
                # A retry reuses the run folder, and boltz decides what is already done by
                # whether the output directory exists rather than by what is in it: a job killed
                # at the walltime leaves an empty one and would be skipped.
                "--override"]
        if str(asked.get("seed") or "").strip():
            argv += ["--seed", str(int(asked["seed"]))]
        if asked.get("use_potentials"):
            argv.append("--use_potentials")
        if not has_kernels(install):
            # Boltz-2 keeps its CUDA kernels on for any card of compute capability 8.0 or better
            # and imports cuequivariance_torch for them mid-prediction, with no fallback. An
            # install made without the `cuda` extra lacks it, and the job would die after the
            # queue wait and the checkpoint load. Slower without them, and it finishes.
            argv.append("--no_kernels")
        suffix = "cif" if str(asked.get("output_format") or "mmcif") == "mmcif" else "pdb"
        look = os.path.join(out, "boltz_results_%s" % name, "predictions", name)
        pattern = "*.%s" % suffix
        why = ("Boltz-2 prints \"Failed to process\" and exits 0 when it cannot read its input, "
               "so look for that line above.")
    else:
        exe = os.path.join(install, "bin", "colabfold_batch")
        where = msa_dir(run, tool, name)
        single = str(asked.get("alignments") or "fetch") == "single"
        source = (os.path.join(run, "inputs", "%s.fasta" % name) if single
                  else os.path.join(where, "%s.a3m" % name))
        argv = [exe,
                # Resolved on the login node, where the answer is already known. Without the
                # weights ColabFold downloads them as it predicts, and that is the failure this
                # whole arrangement exists to avoid.
                "--data", os.path.join(install, CF_PARAMS),
                # Never `auto`: see CF_MODEL. This is the only model type that can run offline in
                # this install, for one chain as well as for several.
                "--model-type", CF_MODEL,
                "--num-models", str(int(asked.get("num_models") or 5)),
                "--num-recycle", str(int(asked.get("num_recycle") or 3)),
                "--num-seeds", str(int(asked.get("num_seeds") or 1)),
                "--rank", str(asked.get("rank") or "auto")]
        if single:
            argv += ["--msa-mode", "single_sequence"]
        # No --amber, --num-relax or --use-gpu-relax anywhere: this install has neither openmm
        # nor pdbfixer, and --amber quietly sets --num-relax to the model count, so one stray
        # flag turns into five failures at the very end of a GPU job.
        argv += [source, where]
        look, pattern = where, "*rank_00*.pdb"
        why = ("ColabFold skips a construct whose alignment it cannot find, with one line on "
               "stderr. If the alignment is missing, fetch it again from the page.")
    return " ".join(_q(a) for a in argv), look, pattern, why


def script_text(tool, install, run, name, asked, site=None, cpus=None, mem=None):
    """The whole job script, which is what the page shows and what sbatch is handed."""
    if tool not in SERVES:
        raise Refused("%s is not a folding tool this page serves" % tool)
    site = site or sitesmod.here()
    bad = unsafe_path(run)
    if bad:
        raise Refused(
            "the run folder cannot be used for a folding job: Slurm parses #SBATCH --output "
            "itself and will not accept " + ", ".join(repr(c) for c in bad)
            + " in a path.\n  %s\nRename it without those characters and submit again." % run)
    gpu = site.card(asked.get("gpu"))
    cpus = int(cpus or RESOURCES[tool]["cpus"])
    mem = int(mem or RESOURCES[tool]["mem"])
    line, look, pattern, why = body(tool, install, run, name, asked, cpus)
    head = HEAD.format(
        name="%s_%s" % (tool, name), partition=site.partition_for(gpu), gres=site.gres(gpu),
        cpus=cpus, mem=mem, walltime=str(asked.get("walltime") or "04:00:00"),
        out=run, outq=_q(run), tmpcap=TMPDIR_CAP,
        account=("#SBATCH --account=%s" % str(asked["account"]).strip()
                 if str(asked.get("account") or "").strip() else ""))
    keep = asked.get("keep_files") not in (None, "", False, "false", "no", 0, "0")
    tidy = "" if keep else TIDY.format(
        checkout=_q(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
        python=_q(sys.executable), tool=tool)
    tail = TAIL.format(lookdir=_q(look), pattern=pattern,
                       lookshown=os.path.join(os.path.relpath(look, run), pattern),
                       whynone=why.replace("'", "'\"'\"'"), name=name, tidy=tidy)
    return head + line + tail


#: Write the job and queue nothing. Both tools want a large card for hours, so there has to be a
#: way to exercise everything up to sbatch: the input written, the script written, the refusals
#: raised. A flag rather than an argument, the same shape hope_flow/drivers.py uses, set around
#: the call and put back; the page does that under its own lock.
DRY_RUN = False

#: What a Slurm job id looks like coming back from sbatch.
JOBID = re.compile(r"\b(\d{4,})\b")


def queue(run, script, say=print):
    """Write the job script into the run folder, queue it, and give back its id."""
    path = os.path.join(run, "job.sbatch")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(script)
    if DRY_RUN:
        say("   | dry run: wrote %s and queued nothing" % path)
        return ""
    done = subprocess.run(["sbatch", "--parsable", path], cwd=run, capture_output=True, text=True)
    out = ((done.stdout or "") + (done.stderr or "")).strip()
    if done.returncode != 0:
        raise Refused("sbatch refused the job: %s" % (out[-400:] or "it said nothing"))
    found = JOBID.search(done.stdout or "")
    if not found:
        raise Refused("sbatch queued nothing it could name: %r" % out[-200:])
    say("   | queued job %s" % found.group(1))
    return found.group(1)


def plan_of(tool, asked, rows, site=None):
    """What Slurm will be asked for and what the job will do, for the box that says so."""
    site = site or sitesmod.here()
    # The resolved card, not the word on the form: this box is what Slurm will be asked for, and
    # "auto" is not something Slurm is ever asked for. Whether it was chosen is said separately,
    # so the box can explain why it is this card.
    gpu = site.card(asked.get("gpu"))
    chose = str(asked.get("gpu") or "").strip().lower() not in ("", sitesmod.AUTO)
    # A card this site has not got is already a sentence under the Card menu, from check(), and
    # this box is drawn beside that sentence rather than instead of it. So the plan falls back to
    # the default card rather than letting Site.gres refuse: a preview that raised would answer
    # the whole form with an error and the person would never see which menu was wrong.
    if gpu not in site.gpu_types():
        gpu, chose = site.card(), False
    tokens = token_total(rows)
    return {"site": site.name, "gpu": gpu, "chosen": chose, "any_card": _any_card(site),
            "partition": site.partition_for(gpu),
            "gres": site.gres(gpu), "cpus": RESOURCES[tool]["cpus"],
            "mem": RESOURCES[tool]["mem"],
            "walltime": str(asked.get("walltime") or "04:00:00"),
            "max_hours": site.max_gpu_hours(gpu),
            "account": str(asked.get("account") or "").strip(),
            "card_memory": site.memory_of(gpu),
            "tokens": tokens, "warn_tokens": WARN_TOKENS, "max_tokens": MAX_TOKENS,
            "alignments": str(asked.get("alignments") or "fetch"),
            "fits": fits_on(gpu, tokens)}
