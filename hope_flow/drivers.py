"""Starting one card's tool, the way that tool's own page starts it.

Every driver here goes through the tool's own command line or its own submitting function. None
reimplements what a tool does, and none writes a job script for a tool: a run queued by a flow
and the same run queued from the tool's page are meant to be the same run, and the only way to be
sure of that is to go through the same door.

Each tool has a Python of its own, so a driver starts a shell, enters that tool's environment the
way the launcher's start lines do, and reads back what it printed. Importing them into one
process is not open to us: they do not agree on a version of Python, let alone on anything else.

A driver is given the card as it was drawn, what arrived at it, and the flow's folder. It returns
(where the run is, [the job ids the next card must wait for]).
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import urllib.error
import urllib.request

from . import cards

#: The HOPE Labs folder, the one holding this checkout, where Set up puts every tool.
LAB_ROOT = os.environ.get("HOPEFLOW_LAB_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _image_install(key):
    """Where a tool that comes as one container image is installed.

    Asked of hope_labs/images.py, which is the one place the image's name and its version live.
    This table is read on import, though, and the drawing half of this package is read where that
    module may not be, so a copy that is not there is looked for instead rather than raising: the
    image's name begins with the tool's key, and a folder with no image at all gives a path that
    the driver refuses by name when a run reaches it.
    """
    try:
        from hope_labs import images as imagesmod
        return imagesmod.image_path(LAB_ROOT, key)
    except ImportError:
        found = sorted(glob.glob(os.path.join(LAB_ROOT, "images", "%s*.sif" % key)))
        return found[-1] if found else os.path.join(LAB_ROOT, "images", "%s.sif" % key)
INSTALLS = {
    "hopemd": LAB_ROOT + "/HOPE-MD/MD",
    "rfdiffusion": LAB_ROOT + "/ML_programs/RFdiffusion",
    "boltzgen": LAB_ROOT + "/envs/boltzgen",
    "rfd3": LAB_ROOT + "/envs/rfd3",
    # The two tools installed as one container image each (hope_labs/images.py). The folder
    # holding the image is where their weights are read from, which is why the path is built
    # rather than written out: the version in the image's name lives in one place.
    "rfd4": _image_install("rfd4"),
    "bioemu": _image_install("bioemu"),
}

#: The design tools the lab runs that have no launcher of their own: no page, no submitting
#: script, nothing that already knows how to queue them. Every other driver here goes through a
#: tool's own door, as the docstring says; for these there is no door, so the flow writes their
#: job itself. That is the exception, and it is written down rather than left to be noticed.
SE3NV = LAB_ROOT + "/envs/SE3nv"                      # RFdiffusion's environment, and ProteinMPNN runs in it too
DL_BINDER_DESIGN = LAB_ROOT + "/ML_programs/dl_binder_design"
BOLTZGEN_CACHE = LAB_ROOT + "/envs/boltzgen_cache"    # the weights, fetched on a login node
FOUNDRY = LAB_ROOT + "/ML_programs/foundry"           # RFdiffusion3's source, and its configs
RFD3_CHECKPOINTS = LAB_ROOT + "/ML_programs/foundry_checkpoints"   # fetched on a login node

#: Where a tool's own run folders go, when the card does not say.
RUNS = {
    "hopemd": os.path.join(os.environ.get("SCRATCH", "/tmp"), "hopemd_runs"),
    "rfdiffusion": os.path.join(os.environ.get("SCRATCH", "/tmp"), "rfdiffusion_runs"),
    "rfd3": os.path.join(os.environ.get("SCRATCH", "/tmp"), "rfd3_runs"),
    "rfd4": os.path.join(os.environ.get("SCRATCH", "/tmp"), "rfd4_runs"),
    "boltzgen": os.path.join(os.environ.get("SCRATCH", "/tmp"), "boltzgen_runs"),
    # The same two folders the folding pages use (hope_labs/folding/job.py, RUNS), so a run queued
    # by a flow is listed on the Runs screen beside one queued from the page. Copied rather than
    # imported because this table is read on import and those pages are not always importable; a
    # test holds the two together wherever both are present.
    "boltz2": os.path.join(os.environ.get("SCRATCH", "/tmp"), "boltz2_runs"),
    "colabfold": os.path.join(os.environ.get("SCRATCH", "/tmp"), "colabfold_runs"),
    # The ensemble page's own (hope_labs/ensemble/job.py, RUNS), for the same reason.
    "bioemu": os.path.join(os.environ.get("SCRATCH", "/tmp"), "bioemu_runs"),
}

#: The cards whose driver has to run where there is a network, which on this cluster means a login
#: node and nowhere else. run.submit starts these itself, before anything is queued, rather than
#: leaving them to a step job on a compute node.
#:
#: The target card is here because an RCSB id is fetched over the internet, and the folding and
#: ensemble cards because the first of their two phases asks the alignment server for one
#: alignment per protein chain. A Grace compute node resolves names and then connects to nothing,
#: so neither could be done from one - and a flow that tried would not fail loudly: it would fetch
#: no alignment and fold from the sequence alone, which is a markedly worse structure and not a
#: word said about it.
#:
#: Being in here is not enough on its own. A card is only started at launch if it has nothing to
#: wait for, which is what keeps a target card fed by a fold out of it: its structure does not
#: exist yet, and what it will do when its turn comes is copy a file.
AT_LAUNCH = ("target", "structurefile", "boltz2", "colabfold", "bioemu")

RCSB = "https://files.rcsb.org/download/%s.pdb"

#: The same entry as mmCIF, and one of its biological assemblies, in the names the file server
#: uses. Written the way HOPE-MD's own importer writes them (hopemd/importers/complex.py,
#: fetch_rcsb), because a flow and a simulation imported by hand should be fetching the same file.
RCSB_CIF = "https://files.rcsb.org/download/%s.cif"
RCSB_ASSEMBLY = "https://files.rcsb.org/download/%s-assembly%s.cif"

#: What a PDB file can hold, which is why a structure is sometimes fetched as mmCIF and converted
#: rather than fetched as a PDB. The atom serial is five columns and the chain id is one, and both
#: overflow silently: gemmi writes a hybrid-36 serial past 99,999 ("A000G"), which the tools below
#: this card read as nothing, and a 27th chain has no letter left. 4V60's 241,956 atoms and 39
#: chains converted into exactly that, and 8GLV has 1,177 chains.
#:
#: Sixty-two rather than twenty-six: a PDB chain id is one character and the readers here take
#: upper case, lower case and digits, which is what gemmi hands out in that order.
PDB_MAX_ATOMS = 99999
PDB_MAX_CHAINS = 62

#: What a Slurm job id looks like coming back from any of them.
JOBID = re.compile(r"\b(\d{4,})\b")


class NotWired(RuntimeError):
    """A card the runner cannot start. Raised with the reason a person can act on."""


def submit(node, flow, carried, flow_dir, record, say=print):
    fn = BY_CARD.get(node.card)
    if fn is None:
        raise NotWired("%s cannot be started by a flow yet" % node.card)
    return fn(node, flow, carried, flow_dir, record, say)


# --- running a tool in its own environment -----------------------------------------------------

def _shell(line, where, say=print, minutes=30):
    """Run one line in bash, in a tool's own folder, and give back what it printed.

    The caches are pushed into scratch first. A tool started from a flow must leave a home folder
    exactly as it found it, and several of these write one by default: matplotlib, CUDA, and
    ProDy, which finds its settings file through USERPROFILE before
    HOME.

    Not a login shell. Everything these lines use comes with the runner's own environment, which
    is a login session's (the hub starts its tools with one, and the flow's step jobs inherit it):
    PATH with sbatch on it (/sw/local/bin on Grace), SCRATCH, and Lmod's module function, which
    Lmod exports. A login shell would only run the site's profile again, and Grace's profile
    initializes any home folder it finds without them: an SSH key pair, .condarc, .forward and
    .fluxbox, written into whatever HOME is. Two of those at once on one node also wait for ever
    at ssh-keygen's overwrite prompt.
    """
    cache = os.path.join(os.environ.get("SCRATCH", "/tmp"), ".cache", "hopeflow")
    env = dict(os.environ, XDG_CACHE_HOME=cache, MPLCONFIGDIR=os.path.join(cache, "matplotlib"),
               CUDA_CACHE_PATH=os.path.join(cache, "nv"))
    env.setdefault("USERPROFILE", cache)
    os.makedirs(cache, exist_ok=True)
    run = subprocess.run(["bash", "-c", line], cwd=where, env=env, capture_output=True,
                         text=True, timeout=minutes * 60)
    out = (run.stdout or "") + (run.stderr or "")
    for tail in out.strip().splitlines()[-6:]:
        say("   | " + tail[:200])
    if run.returncode != 0:
        raise NotWired("the tool refused to start (status %d): %s"
                       % (run.returncode, out.strip()[-500:] or "it said nothing"))
    return out


def _unique(root, name):
    """A run folder of this name that does not exist yet.

    Two branches of one flow can derive the same name - the same target, the same engine - and
    every one of these tools refuses a folder that already holds a run. Settling it here means a
    flow does not fail halfway for a reason that has nothing to do with the science.
    """
    where = os.path.join(root, name)
    n = 2
    while os.path.exists(where):
        where = os.path.join(root, "%s_%d" % (name, n))
        n += 1
    return where


def _target(carried):
    got = (carried.get("target") or [None])[0]
    if not got:
        raise NotWired("no target reached this card")
    return got


def _for_design(target):
    """The receptor file a design tool is given: the target card's cleaned copy, or the target
    itself where there is none (a flow launched before the copy was written)."""
    cleaned = target.get("cleaned") or ""
    return cleaned if cleaned and os.path.isfile(cleaned) else target["path"]


def _prepared_flags(target):
    """(whether the receptor arrived prepared, the pH it was prepared at, or None).

    A receptor through the Prepare receptor card is both. A target straight into a docking card,
    which only a flow launched before that card existed still has, is neither, and its tool
    prepares the receptor itself as it always did.
    """
    if not target.get("prepared"):
        return False, None
    ph = target.get("ph")
    try:
        return True, ("%g" % float(ph)) if ph not in (None, "") else "7.4"
    except (TypeError, ValueError):
        return True, "7.4"


def _run_root(node, card):
    return os.path.abspath(os.path.expandvars(os.path.expanduser(
        node.settings.get("runs") or RUNS[card])))


# --- the target ---------------------------------------------------------------------------------
# Not a tool: the one card that is the person's own input. Its structure is fetched once, into the
# flow's folder, so every track below it reads the same file rather than each fetching its own and
# hoping the RCSB gave them all the same thing on the same day.

def _target_card(node, flow, carried, flow_dir, record, say=print):
    s = node.settings
    where = os.path.join(flow_dir, "inputs")
    os.makedirs(where, exist_ok=True)
    source = (s.get("source") or "rcsb").strip()

    if source == "folded":
        # The third way to get a structure, and the one that cannot happen when the flow is
        # queued: it arrives when the fold in front of this card has finished. Copied into the
        # flow's own inputs like the other two, so that what every track below reads is one file
        # in one place whichever of the three it came from - and so that tidying the fold's run
        # folder later cannot take the target of a finished flow with it.
        got_in = (carried.get("structure") or [None])[0]
        if not got_in or not got_in.get("path"):
            raise NotWired("the target is set to take a folded structure and none arrived")
        src = got_in["path"]
        if not os.path.isfile(src):
            raise NotWired("the folded structure is not there: %s" % src)
        got = os.path.join(where, "target" + (os.path.splitext(src)[1] or ".pdb"))
        shutil.copy2(src, got)
        say("the target is %s, %s" % (os.path.basename(src), got_in.get("what", "folded")))
    elif source == "file":
        src = os.path.abspath(os.path.expandvars(os.path.expanduser(s.get("path") or "")))
        if not os.path.isfile(src):
            raise NotWired("the target file is not there: %s" % src)
        got = os.path.join(where, "target" + (os.path.splitext(src)[1] or ".pdb"))
        shutil.copy2(src, got)
        say("the target was copied from %s" % src)
    else:
        code = (s.get("pdb_id") or "").strip().upper()
        if not re.match(r"^[0-9A-Z]{4}$", code):
            raise NotWired("%r is not a four-character PDB id" % code)
        got = os.path.join(where, "target.pdb")
        _fetch_rcsb(code, str(s.get("assembly") or "asu").strip(), got,
                    chains=_chains(s.get("chains")), say=say)

    if os.path.getsize(got) < 200:
        raise NotWired("the target structure came back empty")
    # The copy the design tools are given. A docking card gets the receptor prepared, through the
    # Prepare receptor card; a design tool must not, because the names a preparation gives - HID,
    # CYX, ASH - and the hydrogens it adds crash some of them and are read as something else by
    # the rest. So they are given the target as it was deposited or folded, cleaned of everything
    # that is not the polymer, and with its numbering untouched, which is what the hotspots name.
    cleaned, said = clean_copy(got, os.path.join(where, "target_cleaned.pdb"),
                               _chains(s.get("chains")))
    if said:
        say("   for the design tools: %s" % said)
    record.set(node.id, path=got, cleaned=cleaned)
    return where, []


def _get(url, where, timeout=120):
    """Fetch one file, or raise urllib's own error. Returns the bytes written."""
    with urllib.request.urlopen(url, timeout=timeout) as answer, open(where, "wb") as out:
        shutil.copyfileobj(answer, out)
    return os.path.getsize(where)


def _fetch_rcsb(code, assembly, dst, chains=(), say=print):
    """Fetch an entry into `dst` as a PDB file, whatever format the RCSB has it in.

    The flow asked for `<code>.pdb` and nothing else, so an entry with no PDB file could not be
    fetched by its id at all: 4V60, 5J7V, 7A01, 6QZ9 and 8GLV each answer 404 for the PDB and 200
    for the mmCIF, and the card's only way round it was for somebody to download the mmCIF by
    hand and point the file box at it. A biological assembly is mmCIF-only for every entry.

    Converted where the structure fits the format, and refused with the reason where it does not,
    which is better than the hybrid-36 serials gemmi writes past 99,999 atoms: those are a PDB no
    reader here understands, and nothing would have said so.
    """
    if assembly and assembly != "asu":
        # No PDB to try: the assembly files are mmCIF only.
        _cif_to_pdb(RCSB_ASSEMBLY % (code, assembly), dst,
                    "assembly %s of %s" % (assembly, code), chains, say)
        return
    try:
        if _get(RCSB % code, dst) > 200:
            say("%s was fetched into the flow" % code)
            return
    except urllib.error.HTTPError as why:
        if why.code != 404:
            raise NotWired("the RCSB would not give %s: %s" % (code, why))
    except Exception as why:                                    # noqa: BLE001
        raise NotWired("the RCSB would not give %s: %s" % (code, why))
    # Said as "no PDB file" rather than "fetching the mmCIF", because a mistyped id answers 404
    # for both and this line is read before the refusal that follows it.
    say("   %s has no PDB file; trying the mmCIF" % code)
    _cif_to_pdb(RCSB_CIF % code, dst, code, chains, say)


def _cif_to_pdb(url, dst, what, chains=(), say=print):
    """Fetch an mmCIF and write it to `dst` as PDB, or refuse with the reason.

    The chains the target card names are taken here as well as in the cleaned copy, and that is
    what makes the refusal below actionable: a ribosome is past the format whole, and the two
    chains of it somebody is aiming at are not.

    The mmCIF itself is not kept. It is the download and not the structure the flow reads, the
    entry and the assembly are both written down beside the flow, so it is reproducible without
    it, and these files are large: 8GLV's is 337 MB. A copy of that in every flow folder is the
    quota going, and it is the file count rather than the gigabytes that runs out first here.
    """
    cif = os.path.splitext(dst)[0] + "_original.cif"
    try:
        try:
            if _get(url, cif) < 200:
                raise NotWired("%s came back empty" % what)
        except NotWired:
            raise
        except Exception as why:                                # noqa: BLE001
            raise NotWired("the RCSB would not give %s: %s" % (what, why))
        _cif_to_pdb_file(cif, dst, what, chains, say)
    finally:
        try:
            os.unlink(cif)
        except OSError:
            pass


def _cif_to_pdb_file(cif, dst, what, chains=(), say=print):
    """Write one mmCIF already on disk to `dst` as PDB, or refuse with the reason."""
    try:
        import gemmi
    except ImportError:
        raise NotWired("%s came as mmCIF and gemmi is not installed here, so it cannot be "
                       "turned into the PDB file the tools below this card read" % what)
    try:
        structure = gemmi.read_structure(cif)
        structure.setup_entities()
    except Exception as why:                                    # noqa: BLE001
        raise NotWired("%s could not be read: %s" % (what, str(why)[:200]))
    want = [c for c in chains if c]
    if want:
        try:
            structure = gemmi.Selection("/1/" + ",".join(want)).copy_structure_selection(
                structure)
        except Exception as why:                                # noqa: BLE001
            raise NotWired("%s: chains %s could not be taken out of it: %s"
                           % (what, ",".join(want), str(why)[:160]))
        if not len(structure) or not len(structure[0]):
            raise NotWired("%s has no chain called %s in it"
                           % (what, " or ".join(want)))
        say("   chains %s taken out of it, as this card names them" % ",".join(want))
    model = structure[0] if len(structure) else None
    n_chains = len(model) if model is not None else 0
    atoms = sum(len(residue) for chain in (model or ()) for residue in chain)
    if atoms > PDB_MAX_ATOMS or n_chains > PDB_MAX_CHAINS:
        raise NotWired(
            "%s has %s atoms in %d chain(s), and a PDB file holds %s atoms in %d chains - the "
            "tools below this card all read PDB, so there is no honest way to hand this on. Name "
            "the chains you want on this card, or prepare a smaller file yourself and read it "
            "with the file source"
            % (what, "{:,}".format(atoms), n_chains, "{:,}".format(PDB_MAX_ATOMS),
               PDB_MAX_CHAINS))
    try:
        with open(dst, "w", encoding="utf-8") as out:
            out.write(structure.make_pdb_string())
    except Exception as why:                                    # noqa: BLE001
        raise NotWired("%s could not be written as PDB: %s" % (what, str(why)[:200]))
    say("%s was fetched as mmCIF and converted: %s atoms in %d chain(s)"
        % (what, "{:,}".format(atoms), n_chains))


# --- the target, cleaned for the design tools ---------------------------------------------------

#: The names a prepared structure gives a residue for its protonation state, and the residue each
#: is: pdb2pqr's AMBER names, which HOPE-MD's Prepare writes, and CHARMM's for histidine.
STATE_NAMES = {"HID": "HIS", "HIE": "HIS", "HIP": "HIS", "HSD": "HIS", "HSE": "HIS", "HSP": "HIS",
               "CYX": "CYS", "CYM": "CYS", "ASH": "ASP", "GLH": "GLU", "LYN": "LYS"}

#: What a cleaned receptor is made of: the twenty amino acids and the DNA and RNA nucleotides.
#: Everything else - waters, ions, ligands, sugars, ACE and NME caps - is not part of the polymer a
#: binder is designed against, and the design tools read most of it as an unknown residue.
POLYMER_RESIDUES = frozenset(("ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER "
                              "THR TRP TYR VAL DA DC DG DT DU A C G U").split())

#: Selenomethionine, which a deposited structure writes as a hetero group in the middle of a chain.
#: It is methionine to every design tool, and left out it would be a hole in the chain.
MODIFIED = {"MSE": ("MET", {"SE": ("SD", "S")})}


def _is_hydrogen(line):
    element = line[76:78].strip().upper()
    if element:
        return element in ("H", "D")
    name = line[12:16].strip()
    return name[:1] in ("H", "D") or (name[:1].isdigit() and name[1:2] in ("H", "D"))


def clean_receptor(text, chains=()):
    """A structure's text as the design tools want it: (the cleaned PDB text, what was taken out).

    The target's own chains where it names them, and every chain where it names none: a peptide
    co-crystallised in the site (the macrocycle in 5O45's chain B) is not part of what a binder is
    designed against.

    Heavy atoms only. The state names back to their residues (HID, HIE and HIP to HIS, CYX to CYS,
    ASH to ASP, GLH to GLU, LYN to LYS). No waters, no hetero groups, no caps; selenomethionine as
    methionine. One conformation: the first alternate location of an atom, or the atom with none.
    The first model of several. The chain letters, the residue numbers and the insertion codes
    exactly as they were, because the hotspots are written in them. An OXT only on the last
    residue of its chain: one anywhere else is a break an earlier preparation closed, and a design
    tool reads it as the end of the chain.
    """
    atoms, gone = [], {"hydrogens": 0, "waters": 0, "other": set(), "altlocs": 0, "renamed": 0}
    for line in text.splitlines():
        if line.startswith("ENDMDL"):
            break                                       # the first model, and only the first
        if not line.startswith(("ATOM", "HETATM")) or len(line) < 54:
            continue
        line = line.rstrip("\n").ljust(80)
        if chains and line[21] not in chains:
            gone.setdefault("chains", set()).add(line[21])
            continue
        name = line[17:20].strip()
        if name in ("HOH", "WAT", "DOD", "H2O", "TIP", "TIP3", "SOL"):
            gone["waters"] += 1
            continue
        if _is_hydrogen(line):
            gone["hydrogens"] += 1
            continue
        if line[16] not in (" ", "A"):
            gone["altlocs"] += 1
            continue
        atom = line[12:16]
        if name in STATE_NAMES:
            name = STATE_NAMES[name]
            gone["renamed"] += 1
        elif name in MODIFIED:
            name, atoms_of = MODIFIED[name]
            if atom.strip() in atoms_of:
                new_atom, element = atoms_of[atom.strip()]
                atom = " %-3s" % new_atom
                line = line[:76] + "%2s" % element + line[78:]
        if name not in POLYMER_RESIDUES:
            gone["other"].add(line[17:20].strip())
            continue
        atoms.append("ATOM  " + line[6:12] + atom + " " + "%3s" % name + line[20:])
    last = {}
    for line in atoms:
        last[line[21]] = line[22:27]
    out, chain = [], None
    for line in atoms:
        if line[12:16].strip() == "OXT" and line[22:27] != last.get(line[21]):
            continue
        if chain is not None and line[21] != chain:
            out.append("TER")
        out.append(line.rstrip())
        chain = line[21]
    if out:
        out.append("TER")
    gone["other"] = sorted(gone["other"])
    gone["chains"] = sorted(gone.get("chains") or ())
    return "\n".join(["REMARK   1 CLEANED FOR THE DESIGN TOOLS BY HOPE LABS: HEAVY ATOMS, "
                      "STANDARD NAMES"] + out + ["END"]) + "\n", gone


def clean_copy(src, dst, chains=()):
    """Write the cleaned copy of the target beside it. Returns (its path, or "", a line to say).

    A structure in mmCIF is read through gemmi into PDB first, where it can be; where it cannot,
    no copy is written and the design tools are given the target as it is, as they always were.
    """
    try:
        if src.lower().endswith((".cif", ".mmcif")):
            import gemmi
            st = gemmi.read_structure(src)
            st.setup_entities()
            text = st.make_pdb_string()
        else:
            with open(src, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
    except Exception as why:                                    # noqa: BLE001
        return "", "no cleaned copy (%s), so they are given the target as it is" % str(why)[:120]
    cleaned, gone = clean_receptor(text, chains)
    if "\nATOM" not in cleaned:
        return "", "no protein or nucleic acid in the target, so they are given it as it is"
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write(cleaned)
    parts = []
    if gone["chains"]:
        parts.append("chain(s) %s" % ",".join(gone["chains"]))
    if gone["hydrogens"]:
        parts.append("%d hydrogen(s)" % gone["hydrogens"])
    if gone["waters"]:
        parts.append("%d water atom(s)" % gone["waters"])
    if gone["other"]:
        parts.append("the groups %s" % ", ".join(gone["other"][:8]))
    if gone["altlocs"]:
        parts.append("%d alternate location(s)" % gone["altlocs"])
    return dst, ("a cleaned copy, without %s" % "; ".join(parts) if parts
                 else "a cleaned copy, which had nothing to take out")


# --- folding a structure for the target ---------------------------------------------------------
# The other two ways to get one. Both go through the folding pages' own submitting function, in
# this repository (hope_labs/folding/submit.py): it is the one thing that knows the two phases, the
# refusals and the two tools' command lines, and a run queued by a flow and a run queued from the
# page are the same run because they are the same call. Nothing here writes a folding job.
#
# That function is imported rather than shelled out to, which is the one place this file departs
# from its own rule. The rule is about tools with a Python of their own, and this is not one: the
# page is ours and runs in the launcher's own Python, shelling out to Boltz-2's only for the
# alignment helper and the SMILES check, which it does itself. Going through the same door here
# means calling it.

def _folding_pages():
    """The folding pages' own machinery, or a sentence saying why a fold cannot be started.

    Imported here rather than at the top of the file so that a laptop, which reads this module's
    cards and never its drivers, is not asked for a package it was not built with.
    """
    try:
        from hope_labs.folding import sites as sitesmod, submit as submitmod
    except ImportError as why:
        raise NotWired("the folding pages are not importable from this install, so a flow cannot "
                       "start a fold here: %s" % why)
    return submitmod, sitesmod


def _folding(node, flow, carried, flow_dir, record, say=print):
    """One sequence folded, in the two phases the tool needs.

    Phase one happens inside this call, on the login node, because it is the only part of a
    folding run that can reach the alignment server. Phase two is the GPU job whose id comes back,
    so the target card after this one waits for the structure rather than for the fetch.
    """
    tool = node.card
    submitmod, sitesmod = _folding_pages()
    s = node.settings
    site = sitesmod.here()
    root = _run_root(node, tool)
    where = _unique(root, "%s_%s" % (flow.name, node.id))

    # One row per entity the card was given: one for a fold, two or three for a co-fold. The
    # boxes as they were filled in, uncorrected - the page cleans a sequence and clamps a number
    # of copies itself, and reports what it was asked for where the two differ, so tidying either
    # here would only hide a sentence somebody needs.
    rows = []
    for row in cards.fold_entities(node.card, s):
        # The modified residues of this chain, read the way the card's own reader reads them so
        # that what the canvas shows and what the fold is given cannot come apart. A list the
        # page cannot make sense of is refused here, by the box it was typed into, rather than
        # arriving as a traceback out of the page's own checker.
        try:
            mods = cards.read_modifications(s.get("modifications" + row["suffix"]))
        except ValueError as why:
            raise NotWired("the modified residues of %s: %s"
                           % ("this card" if not row["index"] else
                              "the %s chain" % cards.ENTITY_ORDINALS[row["index"]],
                              why))
        rows.append({"kind": row["kind"], "sequence": row["sequence"], "copies": row["copies"],
                     "modifications": mods,
                     "name": node.id if not row["index"] else "%s_%d" % (node.id,
                                                                         row["index"] + 1)})
    body = {
        "name": "%s_%s" % (flow.name, node.id),
        "out": where,
        "rows": rows,
        "alignments": str(s.get("alignments") or "fetch"),
        "gpu": _folding_gpu(s, site),
    }
    if tool == "boltz2":
        body["output_format"] = "pdb"
    # Only what the card was given. A key left out falls through to the page's own default, which
    # is where the considered answer lives and where it should stay; a zero or an empty box is
    # "keep yours" rather than a number to pass on.
    for key in ("recycling_steps", "diffusion_samples", "num_models", "num_recycle", "walltime",
                "account",
                # The rest of each page's own rail, which the card could not reach. Every one is
                # read back out of the body by the page's own _asked(), against the same table
                # the page draws, so a key left out here keeps the page's default exactly as it
                # did before any of them existed.
                "sampling_steps", "seed", "num_seeds", "rank"):
        value = s.get(key)
        if value in (None, "") or str(value) in ("0", "0.0"):
            continue
        body[key] = value
    # A tick, which is false by default on both pages, so it cannot go through the loop above: a
    # False there would be passed and a 0 would not, and neither reads as a question answered.
    # Sent only when the card holds an answer.
    if s.get("use_potentials") is not None:
        body["use_potentials"] = bool(s.get("use_potentials"))

    try:
        got = submitmod.queue(tool, body, [root], site=site, dry_run=DRY_RUN, say=say)
    except Exception as why:                                    # noqa: BLE001
        # The page says what is wrong with a form by returning it, and raises only for the things
        # a form cannot be wrong about. Being called rather than posted to, anything it raises
        # arrives here, and a card that will not start has to say why in one sentence whichever
        # of the two it was.
        raise NotWired("the fold was refused: %s" % why)
    if got.get("problems") or any((got.get("fields") or {}).values()):
        raise NotWired("the fold was refused: %s" % _folding_refused(got))
    run = got.get("run") or {}
    jid = str(run.get("job_id") or "")
    if not jid.isdigit() and not DRY_RUN:
        raise NotWired("the fold queued no job (its record says job_id=%r)" % run.get("job_id"))
    say("%s: %s, alignments %s" % (run.get("name") or tool,
                                   ", ".join("%s %s" % (r["residues"], r["kind"])
                                             for r in run.get("rows") or []) or "nothing",
                                   run.get("alignments") or "unknown"))
    return run.get("path") or where, [jid] if jid.isdigit() else []


def _folding_gpu(settings, site):
    """The card this fold will be given, as a name this site knows.

    auto is settled here rather than handed over, because "auto" is not a card: the page checks
    what it is given against the ones the site has. A card the site has not got is refused here
    as well, and for a plainer reason - the page works out the plan before it reports what is
    wrong with the form, and working out the plan is where an unknown card stops it.
    """
    gpu = str((settings or {}).get("gpu") or "auto").strip()
    if gpu in ("", "auto"):
        return site.default_gpu
    if gpu not in site.gpu_types():
        raise NotWired("%s has no %s card; it has %s"
                       % (site.name, gpu, ", ".join(site.gpu_types())))
    return gpu


def _folding_refused(got):
    """The page's complaints as one sentence somebody can act on.

    They come back in the shape the form draws them in, a message per box, and a flow has no form
    to put them back into. The row is not named: a folding card has exactly one.
    """
    said = list(got.get("problems") or [])
    for _which, boxes in sorted((got.get("fields") or {}).items(), key=lambda kv: str(kv[0])):
        for field, why in sorted((boxes or {}).items()):
            said.append("%s: %s" % (field, why))
    return "; ".join(said) or "it did not say why"


# --- the design tools with no launcher of their own -----------------------------------------
# Everything else here hands a tool its own command line and lets that tool queue itself. These
# have nothing to hand to, so this writes the job. Each ends by writing designs.json, which is
# the one thing the next card reads; the shape of it is documented in adapters.py.

#: What a design job asks Slurm for beyond what the card says: cores, and memory in GB. Not in the
#: card because neither is a choice anybody should have to make, and here rather than in the two
#: drivers because the tools' own pages show what will be asked before anything is queued, and a
#: page quoting a number the job does not use is worse than one that says nothing.
#:
#: BoltzGen gets the larger allowance: upstream reports it running out on a modest target, and
#: again in its analysis step at a hundred designs.
DESIGN_RESOURCES = {
    "rfdiffusion": {"cpus": 8, "mem": 48},
    "rfd3": {"cpus": 8, "mem": 64},
    "rfd4": {"cpus": 8, "mem": 64},
    "boltzgen": {"cpus": 8, "mem": 64},
}

#: Which partition a card is in on Grace. `gpu` holds the a100s, the t4s and the rtx cards; the
#: a40s are a partition of their own. A job that asks for an a40 in `gpu` is refused outright by
#: sbatch, so the partition follows the card unless the person names one.
DEFAULT_PARTITION = "gpu"
GPU_PARTITION = {"a40": "gpu-a40"}

CARD_REFUSED = {
    "rfd3": {"rtx": "RFdiffusion3 recompiles past its own limit on an rtx and stops with "
                    "FailOnRecompileLimitHit"},
    "rfd4": {"rtx": "RFD4-Proteina's attention has no backend on an rtx and stops with "
                    "RuntimeError: Invalid backend"},
}

#: The longest a temporary folder may be before a Unix socket opened inside it is refused. The
#: kernel allows an AF_UNIX path 107 characters and Python's own pymp-<8>/listener-<8> names take
#: 32 of them, which leaves this. The same number as the folding jobs' TMPDIR_CAP, for the same
#: reason and from the same measurement on Grace: see hope_labs/folding/job.py, which a test holds
#: this against so the two cannot drift apart.
TMPDIR_CAP = 75

JOB = """#!/bin/bash
#SBATCH --job-name={name}
#SBATCH --partition={partition}
#SBATCH --gres=gpu:{gpu}:1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task={cpus}
#SBATCH --mem={mem}G
#SBATCH --time={walltime}
#SBATCH --output={out}/slurm_%j.out
{account}set -uo pipefail
umask 002
OUT={out}
# Nothing may reach $HOME: it holds 10 000 files and these write caches by the thousand.
export XDG_CACHE_HOME="$OUT/cache" MPLCONFIGDIR="$OUT/cache/mpl" CUDA_CACHE_PATH="$OUT/cache/nv"
export HF_HOME={hf_home} TORCH_HOME="$OUT/cache/torch"
# Triton's compiled kernels (RFdiffusion3's, through torch) go to ~/.triton unless named, and
# RFdiffusion's DGL writes ~/.dgl/config.json unless it is told its backend.
export TRITON_CACHE_DIR="$OUT/cache/triton" DGLBACKEND=pytorch

# TMPDIR is the one of these that must NOT go in the run folder, and it used to.
#
# RFdiffusion3 reads its inputs through a DataLoader with two worker processes
# (inference_num_workers, which defaults to 2) and takes their tensors back over a Unix socket
# opened under TMPDIR. The kernel caps an AF_UNIX path at 107 characters and Python spends 32 of
# them on its own pymp-/listener- names, so a TMPDIR over {tmpcap} characters makes bind() fail,
# in the feeder thread, where nothing catches it. The run then waits for data that never comes,
# for the whole booking, and the only sign of it is a job that reaches its walltime having
# written nothing. Boltz-2 fails the same way and for the same reason; the folding jobs have
# handled it since they were written.
#
# A run folder long enough to cross the line is ordinary rather than unlucky: a runs root in
# personal scratch, a flow name and a card id already reach it, and a second run of the same
# flow adds a suffix. So the scheduler's own folder is kept where it is short enough - Grace
# gives each job /tmp/job.<id> on node-local NVMe, seventeen characters and nothing to do with
# $HOME - and the run folder is used only where there is nothing better, with a line saying so.
MADE_TMP=
if [ -z "${{TMPDIR:-}}" ] || [ ! -d "${{TMPDIR:-}}" ] || [ "${{#TMPDIR}}" -gt {tmpcap} ]; then
  if mkdir -p "/tmp/hopedesign.${{SLURM_JOB_ID:-$$}}" 2>/dev/null; then
    export TMPDIR="/tmp/hopedesign.${{SLURM_JOB_ID:-$$}}"
    MADE_TMP="$TMPDIR"
  else
    export TMPDIR="$OUT/cache/tmp"
    echo 'note: this node has no short temporary folder, so TMPDIR is in the run folder.'
    echo '      A design can wait at no progress for its whole booking if that path is over'
    echo '      {tmpcap} characters.'
  fi
fi
echo "temporary folder: $TMPDIR (${{#TMPDIR}} characters)"
mkdir -p "$TMPDIR" "$MPLCONFIGDIR" "$CUDA_CACHE_PATH" "$TRITON_CACHE_DIR"
unset PYTHONPATH PYTHONHOME
{body}
status=$?
# Only ever a folder this job made, in /tmp on the node it ran on. The scheduler's own is left
# for the scheduler to sweep, and a run folder is the person's to keep.
[ -n "$MADE_TMP" ] && rm -rf "$MADE_TMP"
echo "flow: {name} finished with status $status"
exit $status
"""


#: Write the job and do not queue it. Both of these tools want a large card for hours, so there
#: has to be a way to exercise everything up to sbatch: the target fetched, the plan written, the
#: script written, the refusals raised. Without one, the only way to try a change to either
#: driver is to spend an A100 on it.
#:
#: A flag rather than an argument because drivers.submit's shape is the runner's, and every driver
#: shares it. Set it around the call and put it back; the page does, under its own lock.
DRY_RUN = False


def _queue(where, script, say):
    """Write a job script into the run folder, queue it, and give back its id."""
    path = os.path.join(where, "job.sbatch")
    with open(path, "w") as fh:
        fh.write(script)
    if DRY_RUN:
        say("   | dry run: wrote %s and queued nothing" % path)
        return ""
    done = subprocess.run(["sbatch", "--parsable", path], cwd=where, capture_output=True, text=True)
    out = ((done.stdout or "") + (done.stderr or "")).strip()
    if done.returncode != 0:
        raise NotWired("sbatch refused the job: %s" % (out[-400:] or "it said nothing"))
    found = JOBID.search(done.stdout or "")
    if not found:
        raise NotWired("sbatch queued nothing it could name: %r" % out[-200:])
    say("   | queued job %s" % found.group(1))
    return found.group(1)


def gpu_asked(settings):
    """The card a job will be given, and the partition it has to be asked for in.

    Both tools want a large card and `auto` means the largest there is, so it resolves to an a100
    rather than being handed to Slurm, which has no idea what either tool needs.
    """
    gpu = str((settings or {}).get("gpu") or "a100")
    if gpu == "auto":
        gpu = "a100"
    return gpu, str((settings or {}).get("partition") or GPU_PARTITION.get(gpu, DEFAULT_PARTITION))


def _design_job(node, flow, carried, card, body, say, hf_home='"$OUT/cache/hf"'):
    """The parts of a design job that do not differ between the two tools."""
    s = node.settings
    gpu, partition = gpu_asked(s)
    # Before the folder is made, so a refused card leaves nothing behind to tidy up.
    why = CARD_REFUSED.get(card, {}).get(gpu)
    if why:
        raise NotWired("%s. Choose a100 or a40, or leave the card on auto" % why)
    where = _unique(_run_root(node, card), "%s_%s" % (flow.name, node.id))
    os.makedirs(os.path.join(where, "cache"), exist_ok=True)
    script = JOB.format(
        name="%s_%s" % (card, node.id), partition=partition, gpu=gpu,
        cpus=DESIGN_RESOURCES[card]["cpus"], mem=DESIGN_RESOURCES[card]["mem"],
        walltime=str(s.get("walltime") or "12:00:00"),
        out=where, hf_home=hf_home, tmpcap=TMPDIR_CAP,
        account=("#SBATCH --account=%s\n" % s["account"]) if s.get("account") else "",
        body=body(where))
    return where, [_queue(where, script, say)]


def _rfdiffusion(node, flow, carried, flow_dir, record, say=print):
    """Binder backbones from RFdiffusion, sequences from ProteinMPNN, in one job.

    Both halves run in RFdiffusion's own environment: ProteinMPNN needs only torch and numpy, and
    SE3nv has them. The environment the lab built for it separately is missing its standard
    library and has never run.
    """
    target = _target(carried)
    s = node.settings
    if not os.path.isdir(DL_BINDER_DESIGN):
        raise NotWired("ProteinMPNN is not unpacked at %s, so a design would have no sequence"
                       % DL_BINDER_DESIGN)
    plan = {
        "target": _for_design(target),
        "chains": target.get("chains") or "A",
        "hotspots": target.get("hotspots") or "",
        "binder_min": int(target.get("binder_min") or 70),
        "binder_max": int(target.get("binder_max") or 100),
        "designs": int(s.get("designs") or 10),
        "seqs_per_backbone": int(s.get("seqs_per_backbone") or 8),
        "noise_scale": float(s.get("noise_scale") or 0),
        # _number rather than `or`, because 0 is a real answer to each of these and `or` would
        # read it as "not given" and send the tool's own figure instead.
        "final_noise_scale": _number(s.get("final_noise_scale"), 1),
        "noise_schedule": str(s.get("noise_schedule") or "constant"),
        "diffuser_T": int(s.get("diffuser_T") or 0),
        "ckpt": str(s.get("ckpt") or ""),
        "potential": str(s.get("potential") or ""),
        "potential_weight": _number(s.get("potential_weight"), 1),
        "guide_scale": _number(s.get("guide_scale"), 10),
        "guide_decay": str(s.get("guide_decay") or "constant"),
        "write_trajectory": bool(s.get("write_trajectory")),
        "deterministic": bool(s.get("deterministic")),
        # ProteinMPNN's half of the job. Each is the tool's own default except the seed, which
        # is the 37 this helper has always frozen it at, kept so that a card drawn before these
        # existed designs the same sequences it did.
        "mpnn_model": str(s.get("mpnn_model") or "v_48_020"),
        "mpnn_soluble": bool(s.get("mpnn_soluble")),
        "mpnn_temperature": _number(s.get("mpnn_temperature"), 0.1),
        "mpnn_seed": int(_number(s.get("mpnn_seed"), 37)),
        "mpnn_omit": str(s.get("mpnn_omit") or ""),
        "mpnn_backbone_noise": _number(s.get("mpnn_backbone_noise"), 0),
        "rfdiffusion": INSTALLS["rfdiffusion"],
        "dl_binder_design": DL_BINDER_DESIGN,
        "se3nv_python": os.path.join(SE3NV, "bin", "python"),
    }
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "helpers",
                          "rfdiffusion_design.py")

    def body(where):
        with open(os.path.join(where, "plan.json"), "w") as fh:
            json.dump(dict(plan, out=where), fh, indent=1)
        return ('%s %s %s' % (shlex.quote(plan["se3nv_python"]), shlex.quote(helper),
                              shlex.quote(os.path.join(where, "plan.json"))))

    return _design_job(node, flow, carried, "rfdiffusion", body, say)


def _rfd3(node, flow, carried, flow_dir, record, say=print):
    """Binders from RFdiffusion3, sequences from MPNN, in one job.

    Beside RFdiffusion rather than replacing it. The two cannot share an environment - that one
    is Python 3.9 in SE3nv and this is 3.12 - so they are two installs, two cards and two
    drivers, and a flow may hold either.

    The checkpoint is 2.5 GB in the group space, fetched on a login node: a compute node has no
    internet, so the run is pointed at that copy and told not to reach for more.
    """
    target = _target(carried)
    s = node.settings
    if not os.path.isdir(RFD3_CHECKPOINTS):
        raise NotWired("RFdiffusion3's checkpoint is not in %s; it is fetched on a login node "
                       "with `foundry install rfd3`" % RFD3_CHECKPOINTS)
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "helpers",
                          "rfd3_design.py")
    plan = {
        "target": _for_design(target),
        "chains": target.get("chains") or "A",
        "hotspots": target.get("hotspots") or "",
        "binder_min": int(target.get("binder_min") or 70),
        "binder_max": int(target.get("binder_max") or 100),
        "designs": int(s.get("designs") or 8),
        "seqs_per_backbone": int(s.get("seqs_per_backbone") or 8),
        "num_timesteps": int(s.get("num_timesteps") or 200),
        # A scale, not a count: step_scale takes a fraction, and int() here would quietly turn
        # 1.5 into 1 - the same trap BoltzGen's sampling_steps fell into.
        "step_scale": float(s.get("step_scale") or 1.5),
        "cfg": bool(s.get("cfg")),
        # Each of these is the sampler's own figure where the card is left alone, and 0 is a
        # real answer to the last two, so _number rather than `or`.
        "cfg_strength": _number(s.get("cfg_strength"), 1.5),
        "batches": int(_number(s.get("batches"), 1)),
        "seed": "" if s.get("seed") in (None, "") else int(_number(s.get("seed"), 0)),
        "noise_scale": _number(s.get("noise_scale"), 1.003),
        "gamma_0": _number(s.get("gamma_0"), 0.6),
        "cyclic": bool(s.get("cyclic")),
        # On, as this card has always run it. The model's own default says nothing either way.
        "non_loopy": s.get("non_loopy") is not False,
        "rfd3": os.path.join(INSTALLS["rfd3"], "bin", "rfd3"),
        # The sequences come from the lab's ProteinMPNN in SE3nv, the one the RFdiffusion
        # helper uses, rather than the MPNN inside this package: that one is a
        # re-implementation its own README asks not to be used for comparison yet, and it
        # wants weights this install has not got.
        "proteinmpnn": DL_BINDER_DESIGN,
        "se3nv_python": os.path.join(SE3NV, "bin", "python"),
        "foundry": FOUNDRY,
        "checkpoints": RFD3_CHECKPOINTS,
    }

    def body(where):
        with open(os.path.join(where, "plan.json"), "w") as fh:
            json.dump(dict(plan, out=where), fh, indent=1)
        return ("%s %s %s"
                % (shlex.quote(os.path.join(INSTALLS["rfd3"], "bin", "python")),
                   shlex.quote(helper), shlex.quote(os.path.join(where, "plan.json"))))

    return _design_job(node, flow, carried, "rfd3", body, say)


def _rfd4(node, flow, carried, flow_dir, record, say=print):
    """Binders from RFD4-Proteina, in one job, inside the tool's own container image.

    Beside the other design cards rather than replacing any of them: it states the problem as
    conditions on atoms rather than as a contig of what to keep, and its designs are compared
    against theirs rather than instead of them.

    The tool is one image file and its weights are beside it in the HOPE Labs folder, fetched
    when it was installed. A compute node reaches no network, so the run is pointed at that copy
    with the model hub switched off, exactly as the ensemble and folding jobs are.
    """
    target = _target(carried)
    s = node.settings
    from hope_labs import images as imagesmod
    install = INSTALLS["rfd4"]
    root = imagesmod.root_of(install)
    if not os.path.isfile(install):
        raise NotWired("RFD4-Proteina's image is not at %s; it is pulled on a login node from "
                       "the Set up page" % install)
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "helpers",
                          "rfd4_design.py")
    plan = {
        "target": _for_design(target),
        "chains": target.get("chains") or "A",
        "hotspots": target.get("hotspots") or "",
        "binder_min": int(target.get("binder_min") or 70),
        "binder_max": int(target.get("binder_max") or 100),
        "designs": int(s.get("designs") or 8),
        # Each of these is the model's own figure where the card is left alone, and the steps and
        # the schedule are one setting in two halves: a schedule that expands to more steps than
        # are asked for is refused by the tool outright.
        "nsteps": int(_number(s.get("nsteps"), 60)),
        "recycling": str(s.get("recycling") or "medium/front_loaded"),
        "batch_size": int(_number(s.get("batch_size"), 1)),
        "compile": bool(s.get("compile")),
        "seed": "" if s.get("seed") in (None, "") else int(_number(s.get("seed"), 0)),
        # The three conditions, as the page's switches set them. Absent means on, which is what
        # the helper reads and what a flow drawn before the switches existed still asks for, so
        # an old flow keeps designing exactly as it did.
        "hold_target": s.get("hold_target") is not False,
        "reveal_sequence": s.get("reveal_sequence") is not False,
        "use_hotspots": s.get("use_hotspots") is not False,
        # The refold that ranks them, and where Boltz-2 is. Off, the designs are written and the
        # table says in so many words that their order is not a ranking.
        "fold": s.get("fold_back") is not False,
        "boltz": INSTALLS.get("boltz2") or (LAB_ROOT + "/envs/boltz"),
    }
    ranker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "helpers", "rfd4_rank.py")

    def body(where):
        with open(os.path.join(where, "plan.json"), "w") as fh:
            json.dump(dict(plan, out=where), fh, indent=1)
        # The run folder, the helper and the target's folder are bound in; the helper is this
        # repository's file rather than a copy of it pasted into a job, so a fix to it is a fix
        # to every run after it.
        home = os.path.join(where, "home")
        binds = [where, os.path.dirname(os.path.abspath(helper)),
                 os.path.dirname(os.path.abspath(plan["target"]))]
        lines = imagesmod.home_lines(root, "rfd4", home)
        lines.append(imagesmod.line(imagesmod.exec_argv(
            root, "rfd4", ["python", helper, os.path.join(where, "plan.json")],
            binds=binds, home=home)))
        # The job's status is the status of its last line, so the ranking below, which is
        # allowed to fail, used to decide it: a run whose designs were never made reported
        # COMPLETED and showed as a finished card with nothing in it. The tool's own status is
        # kept here and given back as the body's, whatever the ranking does.
        lines.append("rfd4_rc=$?")
        if plan["fold"]:
            # Outside the image and on the same card: Boltz-2 is an environment of its own, and
            # the two tools share nothing but this job's GPU. Allowed to fail without taking the
            # run with it, because the designs are the result and the ranking is a measurement of
            # them: a Boltz-2 that will not start leaves eight designs and a sentence. Skipped
            # outright when the designs were not made, since there is nothing to rank.
            lines.append('if [ "$rfd4_rc" -eq 0 ]; then')
            lines.append("  echo '--- folding each design back from its sequence alone ---'")
            lines.append("  %s %s %s || echo 'the designs were not ranked; they are written and "
                         "their order is the order they came out in'"
                         % (shlex.quote(os.path.join(plan["boltz"], "bin", "python")),
                            shlex.quote(ranker), shlex.quote(os.path.join(where, "plan.json"))))
            lines.append("fi")
        lines.append('(exit "$rfd4_rc")')
        return "\n".join(lines)

    return _design_job(node, flow, carried, "rfd4", body, say)


def _boltzgen(node, flow, carried, flow_dir, record, say=print):
    """Binders generated by BoltzGen, folded and ranked by its own pipeline.

    The weights are 10 GB and live in the group space, fetched on a login node: a compute node has
    no internet, so the cache is pointed at that copy and the run is told not to reach for more.
    """
    target = _target(carried)
    s = node.settings
    if not os.path.isdir(BOLTZGEN_CACHE):
        raise NotWired("BoltzGen's weights are not in %s; they are fetched on a login node"
                       % BOLTZGEN_CACHE)
    # The one protocol a flow cannot feed. It designs a binder against the target's own ligand,
    # and every design card is given the receptor cleaned to its polymer residues
    # (clean_receptor), so by the time a target reaches this card the ligand is not in the file.
    # Said here, before anything is queued, rather than by a GPU job that reads a specification
    # naming a molecule that is not there. The card's note and its menu say so too.
    if str(s.get("protocol") or "") == "protein-small_molecule":
        raise NotWired("the small-molecule protocol designs against the target's ligand, and a "
                       "flow hands the design cards a receptor cleaned to its polymer residues, "
                       "so there is no ligand left to design against. Run it from BoltzGen's own "
                       "specification, or choose another protocol")
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "helpers",
                          "boltzgen_design.py")
    plan = {
        "target": _for_design(target),
        "chains": target.get("chains") or "A",
        "hotspots": target.get("hotspots") or "",
        "binder_min": int(target.get("binder_min") or 70),
        "binder_max": int(target.get("binder_max") or 100),
        "designs": int(s.get("designs") or 10),
        "protocol": str(s.get("protocol") or "protein-anything"),
        "cyclic": bool(s.get("cyclic")),
        "coldspots": str(s.get("coldspots") or ""),
        # Empty is a real answer to each of these and means BoltzGen's own, so they go over as
        # empty rather than as a number this card made up.
        "budget": "" if s.get("budget") in (None, "") else int(_number(s.get("budget"), 0)),
        "seqs_per_backbone": int(_number(s.get("seqs_per_backbone"), 1)),
        "avoid": str(s.get("avoid") or ""),
        "noise_scale": "" if s.get("noise_scale") in (None, "") else _number(
            s.get("noise_scale"), 0),
        "alpha": "" if s.get("alpha") in (None, "") else _number(s.get("alpha"), 0),
        "filter_biased": s.get("filter_biased") is not False,
        "checkpoints": str(s.get("checkpoints") or "both"),
        # A scale, not a count: BoltzGen's own --step_scale takes a fraction, and int() here was
        # quietly turning 1.5 into 1. The key keeps its old name because saved flows carry it.
        "sampling_steps": float(s.get("sampling_steps") or 0),
        "fold": s.get("fold") is not False,
        "boltzgen": os.path.join(INSTALLS["boltzgen"], "bin", "boltzgen"),
        "cache": BOLTZGEN_CACHE,
    }

    def body(where):
        with open(os.path.join(where, "plan.json"), "w") as fh:
            json.dump(dict(plan, out=where), fh, indent=1)
        return ("export HF_HUB_OFFLINE=1\n%s %s %s"
                % (shlex.quote(os.path.join(INSTALLS["boltzgen"], "bin", "python")),
                   shlex.quote(helper), shlex.quote(os.path.join(where, "plan.json"))))

    # HF_HOME is the group's cache rather than the run's: the weights are 10 GB and a compute node
    # cannot fetch them, so every run reads the one copy.
    return _design_job(node, flow, carried, "boltzgen", body, say,
                       hf_home=shlex.quote(BOLTZGEN_CACHE))


def _last_json(text):
    """The last JSON object printed, so a tool's chatter before it does not matter.

    Read as JSON from each { in turn rather than a line at a time, because the answer is not
    always on one line: the simulation prints its own with indent=2, over as many lines as it has
    runs, and a reader that wanted one line found the innermost brace of it, collapsed that into
    something that was not JSON at all, and reported that the tool had printed nothing it could
    read. The whole of the last object is the one that ends latest, and where two start at the
    same end - an object inside another - the outer one is the document.
    """
    text = text or ""
    best = None
    at = text.find("{")
    while at >= 0:
        try:
            got, ends = json.JSONDecoder().raw_decode(text, at)
        except ValueError:
            got, ends = None, -1
        if isinstance(got, dict) and (best is None or ends > best[0]):
            best = (ends, got)
        at = text.find("{", at + 1)
    if best is None:
        raise NotWired("the tool printed nothing this could read:\n%s" % text[-400:])
    return best[1]


# --- the binder design ---------------------------------------------------------------------------

def _refuse_absent_hotspots(target, tool):
    """Refuse a hotspot the receptor has no residue for, before anything is queued.

    Read with the RFdiffusion helper's parser, which is PDB columns and the standard library and
    nothing else, so it runs here as readily as in a job; and said in its words, so the same typo
    is refused in the same sentence whichever card it was typed into.
    """
    from .helpers import rfdiffusion_design
    path = _for_design(target)
    if not str(target.get("hotspots") or "").strip() or not os.path.isfile(path):
        return
    present = rfdiffusion_design.chains_of(path)
    keep = {c: present[c] for c in (_chains(target.get("chains")) or sorted(present))
            if c in present}
    if not keep:
        return
    pairs = rfdiffusion_design.residue_pairs(target["hotspots"], sorted(keep)[0])
    told = rfdiffusion_design.absent(pairs, keep)[1]
    if told:
        raise NotWired("%s is handed its hotspots as they were typed and drops one it cannot "
                       "find without saying so. These are not residues of the receptor, so "
                       "nothing was queued:\n  %s" % (tool, "\n  ".join(told)))


# --- the docking ----------------------------------------------------------------------------------


def _heavy_atoms(path, chains=()):
    """Heavy atoms in these chains of a PDB file, or 0 if it cannot be counted."""
    want = {c for c in chains}
    n = 0
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                if line.startswith("ENDMDL"):
                    break                       # the first model is the receptor
                if not line.startswith(("ATOM  ", "HETATM")):
                    continue
                if want and (len(line) < 22 or line[21] not in want):
                    continue
                element, name = line[76:78].strip().upper(), line[12:16].strip()
                if element == "H" or (not element and name.startswith("H")):
                    continue
                n += 1
    except OSError:
        return 0
    return n


def _chains(text):
    """The chain letters a card was given, as A,B or A B, in order and once each."""
    out = []
    for chain in re.split(r"[,\s]+", str(text or "")):
        if chain and chain not in out:
            out.append(chain)
    return out


def _one_peptides_file(flow_dir, nid, carried_sequences):
    """Several tracks of peptides, as the one file the docking takes."""
    if len(carried_sequences) == 1:
        return carried_sequences[0]["path"]
    where = os.path.join(flow_dir, "inputs", "%s_peptides_all.txt" % nid)
    seen, header, at = set(), "", 1
    rows = []
    for got in carried_sequences:
        with open(got["path"], encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                if "\t" in line:
                    cells = [c.strip() for c in line.rstrip("\n").split("\t")]
                    if "sequence" in [c.lower() for c in cells]:
                        header = header or line
                        at = [c.lower() for c in cells].index("sequence")
                        continue
                    seq = (cells[at] if at < len(cells) else cells[0]).upper()
                else:
                    seq = line.split(":", 1)[-1].strip().upper()
                if seq and seq not in seen:
                    seen.add(seq)
                    rows.append(line if line.endswith("\n") else line + "\n")
    with open(where, "w", encoding="utf-8") as out:
        if header:
            out.write(header if header.endswith("\n") else header + "\n")
        out.writelines(rows)
    return where


def _site(target, codes=True, tool="", say=None):
    """The residues the site is made of, in the form the tool takes: A:41,A:45, or H:60A.

    The flow writes hotspots the way the design tools want them - bare numbers, or a chain letter
    stuck to the front, a range as 66-70, an insertion code after the number as H60A. The tools
    want a colon between chain and number, so this puts one in and gives a residue with no chain
    the target's first."""
    chain = (_chains(target.get("chains")) or ["A"])[0]
    out, by_number = [], []
    for part in re.split(r"[,\s]+", target.get("hotspots") or ""):
        part = part.strip()
        if not part:
            continue
        found = re.match(r"^([A-Za-z]?):?(\d+)(?:([A-Za-z])|-(\d+))?$", part)
        if not found:
            continue
        where, first, code, last = (found.group(1) or chain, int(found.group(2)),
                                    (found.group(3) or "").upper(), found.group(4))
        if code:
            one = "%s:%d%s" % (where, first, code if codes else "")
            if not codes:
                by_number.append("%s%d%s as %s" % (where, first, code, one))
            if one not in out:
                out.append(one)
            continue
        for n in range(first, int(last) + 1 if last else first + 1):
            if "%s:%d" % (where, n) not in out:
                out.append("%s:%d" % (where, n))
    if by_number and say is not None:
        say("   %s reads a residue by its number alone, so the site has %s: every residue of that "
            "number on the chain is in it" % (tool or "this tool", ", ".join(by_number)))
    return ",".join(out)


# --- preparing what arrives, molecule by molecule ----------------------------------------------
# The one card that is not a tool of its own and not a person's input: it is part of the link it
# stands on. The work is HOPE-MD's own Prepare stage, called once per molecule by the helper, in
# HOPE-MD's own environment - the one that has pdb2pqr, PDBFixer and OpenMM in it - so the
# chemistry here is the chemistry every run of the lab's has always been prepared by.


def _prepare_entries(got):
    """What one thing that arrived at a preparation is, as the helper's plan takes it.

    Three shapes arrive and they are read by what they carry rather than by which card sent them:
    a list of designs or complexes, each its own file with its chains named; a docking run's poses,
    which are a run folder and a selection each, staged by HOPE-MD's own importer; or one
    structure with a path.
    """
    out = []
    for n, design in enumerate(got.get("designs") or [], 1):
        if not design.get("path"):
            continue
        out.append({"name": design.get("name") or "structure_%d" % n, "kind": "complex",
                    "path": design["path"],
                    "receptor_chains": list(design.get("receptor_chains") or []),
                    "partner_chains": list(design.get("partner_chains") or []),
                    "score": design.get("score"), "rank": design.get("rank") or n})
    if out:
        return out
    if got.get("kind") == "poses":
        return out
    if got.get("path"):
        out.append({"name": "structure", "kind": "complex", "path": got["path"],
                    "receptor_chains": list(got.get("receptor_chains") or []),
                    "partner_chains": list(got.get("partner_chains") or []), "rank": 1})
    return out


#: What the preparation job asks Slurm for. Not on the card: pdb2pqr, PDBFixer and a restrained
#: minimization are a few minutes of a few cores per molecule, and nobody should have to decide
#: that. The walltime is on the card, because how many molecules arrive is the person's own doing.
PREPARE_CPUS = 4
PREPARE_MEM = "16G"

DOCKING_IONS = {}


def _prepare_below(flow, node):
    """What the preparation leads into, as its helper's plan takes it (plan["below"]).

    {"card": "hopemd"} when it is the simulation alone, which carries the waters and hetero groups
    as they arrived for HOPE-MD's own Prepare to decide. Anything else is a docking, named by its
    card, with the ions every docking card below can use: a preparation feeding a docking and the
    simulation together prepares for the docking, since a water or an inhibitor left in the site is
    the one mistake a docking cannot recover from.
    """
    below = []
    for edge in flow.out_of(node.id):
        dst = flow.node(edge.dst)
        if dst is not None and dst.card not in below:
            below.append(dst.card)
    if below and all(card == "hopemd" for card in below):
        return {"card": "hopemd"}
    docking = [card for card in below if card != "hopemd"]
    ions = None
    for card in docking:
        have = set(DOCKING_IONS.get(card, ()))
        ions = have if ions is None else ions & have
    out = {"card": ",".join(docking) or "not said", "ions": sorted(ions or ())}
    if "hopemd" in below:
        out["also"] = ["hopemd"]
    return out


def _prepare_card(node, flow, carried, flow_dir, record, say=print):
    """Split everything that arrived into its molecules, prepare each, and put them back together.

    One job for everything on this link, in HOPE-MD's environment, running the helper beside this
    file. What it leaves is prepared.json in the card's own folder, which is what the card below
    reads - and the record of what was decided for every molecule, which is what the card shows.
    """
    arriving = [g for g in (carried.get("structure") or []) if g.get("count", 1)]
    if not arriving:
        raise NotWired("nothing reached the preparation")
    s = node.settings
    structures = []
    for got in arriving:
        structures.extend(_prepare_entries(got))
    if not structures:
        raise NotWired("nothing that arrived at the preparation is a structure it can split: "
                       + "; ".join(sorted({str(g.get("kind")) for g in arriving})))
    plan = {"out": os.path.join(flow_dir, "prepare", node.id), "ph": _number(s.get("ph"), 7.4),
            "prep": cards.prep_from(s), "below": _prepare_below(flow, node),
            "structures": structures}
    say("%d structure(s) to prepare, %s"
        % (len(structures), "molecule by molecule at pH %g" % plan["ph"]))
    return _queue_preparation(node, flow_dir, plan, say)


def _prepare_receptor_card(node, flow, carried, flow_dir, record, say=print):
    """The target's receptor, prepared once for every docking card the target feeds.

    The same job as the other preparation's, with the helper in its receptor mode: the target's
    own chains and nothing else, prepared as one molecule by HOPE-MD's Prepare at the flow's pH,
    the waters and hetero groups dropped for the docking, and the hotspots checked against what
    came out - each one present, built, or missing - so that the card can say so and a docking
    aimed at a site that is not there is stopped before it starts.
    """
    target = _target(carried)
    s = node.settings
    chains = _chains(target.get("chains"))
    plan = {"out": os.path.join(flow_dir, "prepare", node.id), "ph": _number(s.get("ph"), 7.4),
            "prep": cards.prep_from(s), "below": _prepare_below(flow, node),
            "receptor": {"chains": chains, "hotspots": target.get("hotspots") or ""},
            "structures": [{"name": "receptor", "kind": "complex", "path": target["path"],
                            "receptor_chains": chains, "partner_chains": [], "rank": 1}]}
    say("the receptor %s, chain(s) %s, to be prepared at pH %g for %s"
        % (os.path.basename(target["path"]), ",".join(chains) or "every one", plan["ph"],
           plan["below"]["card"]))
    return _queue_preparation(node, flow_dir, plan, say)


def _queue_preparation(node, flow_dir, plan, say):
    """Write a preparation's plan and its job, and queue the job. Returns (its folder, [its job]).

    Both preparation cards end here: the job is HOPE-MD's own Prepare called by the helper beside
    this file, in HOPE-MD's own environment, whichever of the two wrote the plan.
    """
    s = node.settings
    where = plan["out"]
    os.makedirs(where, exist_ok=True)
    plan_file = os.path.join(where, "plan.json")
    with open(plan_file, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=2)
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "helpers",
                          "prepare_molecules.py")
    logs = os.path.join(flow_dir, "logs")
    os.makedirs(logs, exist_ok=True)
    from . import queue as q
    lines = ["#!/bin/bash",
             "#SBATCH --job-name=flow-%s-prepare" % node.id,
             "#SBATCH --partition=%s" % (s.get("partition") or q.STEP_PARTITION),
             "#SBATCH --time=%s" % (s.get("walltime") or "02:00:00"),
             "#SBATCH --nodes=1", "#SBATCH --ntasks=1",
             "#SBATCH --cpus-per-task=%d" % PREPARE_CPUS,
             "#SBATCH --mem=%s" % PREPARE_MEM,
             "#SBATCH --output=%s/%s-prepare-%%j.out" % (logs, node.id)]
    if s.get("account"):
        lines.append("#SBATCH --account=%s" % s["account"])
    lines += ['export XDG_CACHE_HOME=%s' % shlex.quote(os.path.join(where, "cache")),
              'export MPLCONFIGDIR="$XDG_CACHE_HOME/matplotlib"',
              'mkdir -p "$MPLCONFIGDIR"',
              "cd %s || exit 1" % shlex.quote(INSTALLS["hopemd"]),
              'if [ -f ./activate.sh ]; then . ./activate.sh; fi',
              'PY="${HOPEMD_PYTHON:-python3}"',
              "export PYTHONPATH=%s" % shlex.quote(INSTALLS["hopemd"]),
              '"$PY" %s %s' % (shlex.quote(helper), shlex.quote(plan_file))]
    script = os.path.join(where, "prepare.sbatch")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.chmod(script, 0o755)
    if plan["below"]["card"] == "hopemd":
        say("   for HOPE-MD: the waters and hetero groups go through as they arrived, for its "
            "Prepare to decide")
    else:
        say("   for %s: the waters and hetero groups are dropped%s"
            % (plan["below"]["card"],
               ", except the ions it can use" if plan["below"]["ions"] else ""))
    if DRY_RUN:
        say("   | dry run: wrote %s and queued nothing" % script)
        return where, []
    jid = q.submit(script, flow_dir, name="flow-%s-prepare" % node.id)
    say("   | queued job %s" % jid)
    return where, [str(jid)]


# --- the simulation --------------------------------------------------------------------------------

def _run_key(got, pick):
    """What one simulation is, whichever attempt of the card asks for it: its source and its pick."""
    return json.dumps([got.get("source_kind") or got.get("kind"), got.get("path"), pick],
                      sort_keys=True)


def _finished_before(record, node_id):
    """{run key: the run} for the runs an earlier attempt of this card finished, every job of them.

    Carrying on a simulation card that failed starts it again, and every run it had already
    finished would be simulated again with it: twenty runs re-queued for the one that died, or for
    a report job that could not write. These are kept instead, and only what did not finish is
    queued.
    """
    if record is None:
        return {}
    runs = [r for r in (record.card(node_id).get("runs") or []) if isinstance(r, dict)]
    ids = [str(j) for r in runs for j in (r.get("jobs") or []) if str(j)]
    if not ids:
        return {}
    from . import queue as q
    seen = q.states(ids)
    out = {}
    for run in runs:
        mine = [str(j) for j in (run.get("jobs") or []) if str(j)]
        finished, bad = q.ended(seen, mine)
        if mine and finished and not bad and os.path.isdir(str(run.get("path") or "")):
            out[run["key"]] = run
    return out


def _hopemd(node, flow, carried, flow_dir, record, say=print):
    """The simulation, one run per pose or per design, through its own submitting command line.

    The runs an earlier attempt of this card finished are kept rather than queued again, and the
    jobs that write each run's report and videos are held apart from the card's own: a report that
    could not be written is not a simulation that failed.
    """
    arriving = carried.get("structures") or []
    if not arriving:
        raise NotWired("nothing reached the simulation")
    s = node.settings
    root = _run_root(node, "hopemd")
    os.makedirs(root, exist_ok=True)
    before = _finished_before(record, node.id)
    rundirs, jobs, fresh, kept, reused = [], [], [], [], 0
    #: {run folder: what that run is} - the structure it came from, the score it arrived with, and
    #: the settings it was given where they are not the card's. Written into the record so the
    #: flow's own page can put the runs side by side without reading anything back out of them.
    runs_of = {}
    overrides, bad = cards.per_structure(node.kind, s.get("overrides"))
    if bad:
        raise NotWired("the per-structure settings on this card cannot be read: " + "; ".join(bad))
    left_out = []

    def queued(todo, spec, picks, name, about=None):
        where, ids, made = _hopemd_one(flow_dir, node.id, spec, picks, name, root, say)
        rundirs.extend(where)
        fresh.extend(where)
        jobs.extend(ids)
        for one in where:
            runs_of[one] = dict(about or {})
        # Which run is which, so a later attempt can tell what this one finished. By order, as
        # the simulation's command line answers; when some were refused and the order cannot be
        # trusted, nothing is recorded and those are simply queued again next time.
        if len(made) == len(todo):
            for key, path in zip(todo, made):
                kept.append({"key": key, "path": path,
                             "jobs": [str(j) for j in made[path].values() if str(j).isdigit()],
                             **(runs_of.get(path) or {})})

    top = _top_n(s)
    for got in arriving:
        if got["kind"] == "poses":
            # What arrives is already in the order the tool that made it ranked, so the best of
            # it is the front of it.
            picks = (got["selections"] or [])[:top]
            spec = _spec(s, {"kind": got["source_kind"], "path": got["path"]},
                         prepared=bool(got.get("prepared")), prep=got.get("prep"),
                         ph=got.get("ph"))
            todo = [p for p in picks if _run_key(got, p) not in before]
            for p in picks:
                if _run_key(got, p) in before:
                    run = before[_run_key(got, p)]
                    rundirs.append(run["path"])
                    kept.append(run)
                    reused += 1
            if todo:
                queued([_run_key(got, p) for p in todo], spec, todo, "")
        else:
            # One run per structure, and the person says which: the best few by the rank they
            # arrived in, or the ones named on the card.
            picked, missing = _picked(s, got.get("designs") or [])
            left_out.extend(missing)
            for d in picked:
                name = "%s_%s" % (flow.name, d["name"])
                key = _run_key(got, {"design": name, "path": d["path"]})
                if key in before:
                    rundirs.append(before[key]["path"])
                    kept.append(before[key])
                    reused += 1
                    continue
                # What this one structure was told to differ in, if anything. The rest of the card
                # is unchanged, so an override is a line about one run rather than a second card.
                mine = overrides.get(d["name"]) or {}
                asked = dict(s, **mine) if mine else s
                queued([key], _spec(asked, {"kind": "complex", "path": d["path"]},
                                    d["receptor_chains"], d["partner_chains"],
                                    prepared=bool(got.get("prepared")), prep=got.get("prep"),
                                    ph=got.get("ph")), None, name,
                       about={"name": d["name"], "score": d.get("score"), "rank": d.get("rank"),
                              "charge": d.get("charge"), "settings": mine})
    if record is not None:
        record.set(node.id, runs=kept)
    if reused:
        say("%d run(s) an earlier attempt finished are kept, not simulated again" % reused)
    for name, why in left_out:
        # A name that answers to nothing is said rather than quietly dropped: the whole point of
        # picking by name is that these are the runs somebody meant to have.
        say("%s was named on this card and %s" % (name, why))
    if not jobs and not reused:
        raise NotWired("the simulation queued nothing"
                       + (": " + "; ".join("%s %s" % x for x in left_out) if left_out else ""))
    # Neither the report nor the videos are made by a run on its own: they are asked for
    # afterwards, from the Runs screen, which for twenty runs is twenty rounds of pressing
    # buttons. A flow that ran overnight should leave something to read in the morning, so it
    # queues one small job per run to do it, behind the runs it belongs to - and only for the runs
    # queued now, since a run kept from before has its own.
    finishing = []
    if fresh and (s.get("report", True) or s.get("videos")):
        reps_of = {where: dict(s, **((runs_of.get(where) or {}).get("settings") or {}))
                   for where in fresh}
        finishing = _finish_runs(fresh, jobs, s, flow_dir, node.id, say, reps_of)
    if record is not None:
        record.set(node.id, finishing=finishing)
    return (rundirs[0] if len(rundirs) == 1 else root), jobs


def _finish_runs(rundirs, after, s, flow_dir, node_id, say=print, reps_of=None):
    """Queue one job per finished run to write its report, and its videos when asked for.

    It waits on everything the simulation queued rather than on that run's own jobs: a flow has
    no business picking apart another tool's graph, and a few minutes of a short queue after the
    last run is cheaper than being clever about it.
    """
    from . import queue as q
    want_report = bool(s.get("report", True))
    want_videos = bool(s.get("videos"))
    # Per run, because one structure may have been given its own number of replicates: a video
    # asked for from a replicate that does not exist is a line of noise in the log, and one not
    # asked for from a replicate that does is a video nobody gets.
    reps_of = reps_of or {}
    where = os.path.join(flow_dir, "logs")
    os.makedirs(where, exist_ok=True)
    script = os.path.join(where, "finish-%s.sbatch" % node_id)
    lines = ["#!/bin/bash",
             "#SBATCH --job-name=flow-%s-finish" % node_id,
             # The step jobs' partition, never the card's own: the card's partition is where its
             # simulations run, which is a GPU partition when somebody names one, and a report
             # and a video are CPU work that would hold a GPU node for an hour doing nothing on it.
             "#SBATCH --partition=%s" % q.STEP_PARTITION,
             "#SBATCH --time=01:00:00", "#SBATCH --ntasks=1", "#SBATCH --cpus-per-task=2",
             "#SBATCH --mem=8G",
             "#SBATCH --output=%s/%s-finish-%%j.out" % (where, node_id)]
    if s.get("account"):
        lines.append("#SBATCH --account=%s" % s["account"])
    lines += ['export XDG_CACHE_HOME="${SCRATCH:-/tmp}/.cache/hopeflow"',
              'export MPLCONFIGDIR="$XDG_CACHE_HOME/matplotlib"',
              'mkdir -p "$XDG_CACHE_HOME"',
              "cd %s || exit 1" % shlex.quote(INSTALLS["hopemd"]),
              'if [ -f ./activate.sh ]; then . ./activate.sh; fi',
              'PY="${HOPEMD_PYTHON:-python3}"',
              "export PYTHONPATH=%s" % shlex.quote(INSTALLS["hopemd"]),
              ""]
    for rundir in rundirs:
        if want_report:
            # Each on its own line and never fatal: one run whose report will not write must not
            # take the reports of the nineteen beside it with it.
            lines.append('"$PY" -m hopemd.export.report %s || echo "no report for %s"'
                         % (shlex.quote(rundir), os.path.basename(rundir)))
        if want_videos:
            reps = max(1, int(_number((reps_of.get(rundir) or s).get("replicates"), 3)))
            for rep_n in range(1, reps + 1):
                lines.append('"$PY" -m hopemd.export.video %s %d || echo "no video %d for %s"'
                             % (shlex.quote(rundir), rep_n, rep_n, os.path.basename(rundir)))
    with open(script, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.chmod(script, 0o755)
    try:
        jid = q.submit(script, flow_dir,
                       dependency="afterok:" + ":".join(str(j) for j in after),
                       name="flow-%s-finish" % node_id)
    except RuntimeError as why:
        # Not worth failing the simulation for: the runs are queued and the report can be asked
        # for from the page as it always could.
        say("the finishing job was refused (%s); the runs are queued regardless" % why)
        return []
    say("%s will write %s when the runs finish"
        % (jid, " and ".join(x for x in (("reports" if want_report else ""),
                                         ("videos" if want_videos else "")) if x)))
    return [jid]


def _top_n(s):
    """How many of what arrives to simulate. Everything arriving is already ranked."""
    try:
        return max(1, int(s.get("top_n") or 10))
    except (TypeError, ValueError):
        return 10


def _picked(s, designs):
    """(the structures to simulate, in order, what was asked for and did not arrive).

    One run per structure, and which structures is the card's own question: the best few by the
    rank they arrived in, or the ones named. By name is how somebody comes back to a flow and
    simulates the third and the eleventh because of something they saw in the docking.
    """
    if str(s.get("pick") or "rank").strip().lower() != "name":
        return list(designs)[:_top_n(s)], []
    by = {}
    for one in designs:
        by.setdefault(str(one.get("name") or ""), one)
    out, missing = [], []
    for name in cards.names_asked(s.get("names")):
        if name in by:
            out.append(by[name])
        else:
            missing.append((name, "nothing of that name arrived here"))
    return out, missing


#: The DNA and RNA force field each engine takes when the card names none: the first of HOPE-MD's
#: own choices for it (hopemd/spec.py, NUCLEIC_FF). Desmond types nucleic acids with its OPLS and
#: takes no choice.
NUCLEIC_USUAL = {"amber": "OL21+OL3", "openmm": "OL15+OL3", "gromacs": "OL21+OL3",
                 "namd": "OL21+OL3"}


def _spec(s, source, receptor_chains=None, partner_chains=None, prepared=False, prep=None,
          ph=None):
    """The card's settings as the simulation's own spec.

    The engine belongs to the force field rather than to the compute settings - putting it in the
    wrong place is silently ignored and the run comes out on the default engine - and the number
    of frames is written as the interval between them, which is what the spec holds.

    `prepared` is the one thing the preparation card above has to be able to say: every molecule
    of this structure has been prepared on its own, at `ph`, so HOPE-MD's own Prepare must not
    model or protonate the assembled complex again and undo it. `prep` is what that card was
    filled in with, under HOPE-MD's own field names, so the clean and the split it still does are
    done with the answers the person gave rather than with the defaults.
    """
    system = {"source": source}
    if receptor_chains:
        system["receptor_chains"] = receptor_chains
    if partner_chains:
        system["partner_chains"] = partner_chains
    if ph not in (None, ""):
        system["ph"] = _number(ph, 7.4)
    settled = dict(prep or {})
    if prepared:
        settled["prepared_per_molecule"] = True
    if settled:
        system["prep"] = settled
    waters = settled.get("drop_waters")
    if waters in cards.MD_WATERS_BOTH_WAYS:
        system["keep_waters"] = cards.MD_WATERS_BOTH_WAYS[waters]
    if "keep_cofactors" in settled:
        system["keep_cofactors"] = bool(settled["keep_cofactors"])
        system["keep_metals"] = bool(settled["keep_cofactors"])
    # The bilayer, when the card asks for one. Its five fields are HOPE-MD's own Membrane, and
    # what a bilayer fixes - the force field, the water, the box shape - is written below rather
    # than offered, as Desmond's is.
    membrane = str(s.get("environment") or "water").strip() == "membrane"
    if membrane:
        system["environment"] = "membrane"
        mem = {}
        if s.get("lipid"):
            mem["lipid"] = str(s["lipid"]).strip()
        for key, into in (("membrane_padding_A", "padding_A"), ("center_z_A", "center_z_A")):
            if s.get(key) not in (None, ""):
                mem[into] = _number(s[key], 0)
        if s.get("orientation"):
            mem["orientation"] = str(s["orientation"]).strip()
        if str(s.get("segments") or "").strip():
            mem["segments"] = str(s["segments"]).strip()
        if mem:
            system["membrane"] = mem

    ns = _number(s.get("length_ns"), 100.0)
    protocol = {"length_ns": ns, "replicates": int(_number(s.get("replicates"), 3))}
    frames = _number(s.get("frames"), 0)
    if frames and frames > 0:
        # The spec keeps how often a frame is written, not how many there are; a person thinks in
        # frames. One is the other over the length of the run, worked out in cards.py so that what
        # the card checks and what the spec says are one number.
        protocol["save_ps"] = cards.save_interval_ps(ns, frames)

    for key in ("temperature_K", "pressure_bar", "timestep_fs", "equil_ps", "heat_ps",
                "minimise_steps", "restraint_kcal", "seed"):
        if s.get(key) not in (None, ""):
            protocol[key] = _number(s[key], 0)
    if s.get("seed") in (None, ""):
        protocol.pop("seed", None)
    # The residues held still in production, as the engines take them: a list of keys, each a
    # chain and a residue number, read with the same parser the hotspots are.
    held = _residue_keys(s.get("restrain"))
    if held:
        protocol["restrain"] = held
    engine = str(s.get("engine") or "amber").strip()
    # The force field, the water and the box are named rather than left out, whatever the card
    # says. Left out, the spec's own defaults apply - ff19SB, OPC, a truncated octahedron - and
    # those are the four Amber-topology engines' answers: Desmond takes OPLS, a three-site water
    # and an orthorhombic box, so every Desmond run a flow ever queued was refused for all three
    # before it had built anything.
    forcefield = {"engine": engine, "protein": cards.md_protein_ff(s),
                  "water": cards.md_water(s)}
    # A small molecule cannot be parameterized without being told how, and on the four
    # Amber-topology engines there is exactly one way (GAFF2 with AM1-BCC charges). So it is
    # named rather than asked for: a run whose partner turns out to be a small molecule was
    # otherwise refused for want of a choice that has one member. Its net charge is a separate
    # matter and is not confirmed here - a charge nobody has looked at is the one thing that
    # refusal exists to stop.
    if cards.MD_LIGAND_PARAMS.get(engine):
        forcefield["ligand"] = cards.MD_LIGAND_PARAMS[engine][0]
    spec_box = {}
    if s.get("box_shape"):
        spec_box["shape"] = str(s["box_shape"]).strip()
    if s.get("padding_A") not in (None, ""):
        spec_box["padding_A"] = _number(s["padding_A"], 12.0)
    if s.get("salt"):
        spec_box["salt"] = str(s["salt"]).strip()
    if s.get("salt_mM") not in (None, ""):
        spec_box["salt_mM"] = _number(s["salt_mM"], 150.0)
    if engine in cards.MD_FIXED_BOX:
        # Its System Builder builds one shape whatever the spec asks for, and the spec is refused
        # for asking for another.
        spec_box["shape"] = cards.MD_FIXED_BOX[engine]
    if membrane:
        # What a bilayer build fixes, written rather than offered: the box is rectangular because
        # a membrane is periodic in x and y, and on the Amber-topology engines the build is
        # OpenMM's Modeller with amber14's lipid17, whose data ships one protein force field and
        # one water. A card that left these to the engine's usual had every membrane run refused
        # for all three, which is how the Desmond ones used to go.
        spec_box["shape"] = "rectangular"
        if engine == "desmond":
            if forcefield["water"] not in cards.MD_DESMOND_MEMBRANE_WATER:
                forcefield["water"] = cards.MD_DESMOND_MEMBRANE_WATER[0]
        else:
            forcefield["protein"] = cards.MD_MEMBRANE_PROTEIN_FF
            forcefield["water"] = cards.MD_MEMBRANE_WATER
        # A bilayer's water is the padding above and below it, not the box's, and a padding under
        # the cutoff is refused on a water box alone - so the box's is left to the spec.
        spec_box.pop("padding_A", None)
    # An engine that fixes its timestep is given that one: its RESPA scheme is not a setting.
    if engine in cards.MD_FIXED_TIMESTEP:
        protocol["timestep_fs"] = cards.MD_FIXED_TIMESTEP[engine]
    nucleic = s.get("nucleic") or NUCLEIC_USUAL.get(engine, "")
    if nucleic and engine in NUCLEIC_USUAL:
        forcefield["nucleic"] = nucleic
    compute = {}
    for key in ("walltime", "partition", "account", "gpu"):
        if s.get(key):
            compute[key] = s[key]
    if s.get("device"):
        compute["device"] = str(s["device"]).strip()
    for key in ("cpus", "mem_gb", "max_concurrent", "gpus_per_replicate"):
        if s.get(key) not in (None, ""):
            compute[key] = int(_number(s[key], 0))
    if s.get("tidy") is not None:
        compute["tidy"] = bool(s["tidy"])

    # MM-GBSA over a slice of the production rather than all of it: the first nanoseconds are the
    # complex settling into its box, and averaging them in is averaging in the equilibration. The
    # end empty is the end of the run, which is what 0 means to the tool.
    analysis = {"mmgbsa": bool(s.get("mmgbsa", True))}
    for key in ("mmgbsa_from_ns", "mmgbsa_to_ns", "mmgbsa_stride_ps"):
        if s.get(key) not in (None, ""):
            analysis[key] = _number(s[key], 0)
    if s.get("viewer_stride") not in (None, ""):
        analysis["viewer_stride"] = int(_number(s["viewer_stride"], 20))

    spec = {"system": system, "forcefield": forcefield, "protocol": protocol,
            "analysis": analysis}
    if spec_box:
        spec["box"] = spec_box
    if compute:
        spec["compute"] = compute
    return spec


#: A residue as the card is filled in - 54, A54, H60A, 66-70 - which is how the hotspots are
#: written on the target card. Read with the same shapes so that one habit serves both boxes.
RESIDUE = re.compile(r"^([A-Za-z]?):?(\d+)(?:([A-Za-z])|-(\d+))?$")


def _residue_keys(text, chain="A"):
    """["A:41", "H:60A"] from a box of residues, as HOPE-MD keys them (protocol.restrain).

    The same spellings the hotspots box takes, and a residue with no chain letter gets the first
    one, which for a recombined structure out of the preparation is A. A part nothing can be made
    of is left out rather than guessed at: the card's own residues field is what refuses it.
    """
    out = []
    for part in re.split(r"[,\s]+", str(text or "")):
        found = RESIDUE.match(part.strip())
        if not found:
            continue
        where, first, code, last = (found.group(1) or chain, int(found.group(2)),
                                    (found.group(3) or "").upper(), found.group(4))
        if code:
            key = "%s:%d%s" % (where, first, code)
            if key not in out:
                out.append(key)
            continue
        for n in range(first, int(last) + 1 if last else first + 1):
            if "%s:%d" % (where, n) not in out:
                out.append("%s:%d" % (where, n))
    return out


def _number(value, fallback):
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def spec_name(nid, spec):
    """The file a card's simulation spec is written to, named by a digest of what is in it.

    The same in every process. Python's own hash() of a string is salted afresh each time a
    process starts, so a step job queued again wrote the same spec under a second name, and two
    specs could, rarely, share one.
    """
    digest = hashlib.sha1(json.dumps(spec, sort_keys=True).encode("utf-8")).hexdigest()[:10]
    return "%s_spec_%s.json" % (nid, digest)


#: How long one call of the simulation's command line is given, in minutes. It has to be under
#: the step job's own walltime, or the timeout can never fire: the job is killed by Slurm first
#: and the card is left with no answer and no reason. So it is read off queue.STEP_TIME rather
#: than written here, with five minutes kept back for the card to write down what happened. It
#: used to be a flat 60 inside a 30 minute job.
def _submit_minutes(keep_back=5):
    from . import queue as q
    whole = (walltime_minutes(q.STEP_TIME) or 30) - keep_back
    return max(1, whole)


def walltime_minutes(text):
    """A Slurm walltime in whole minutes, rounded down, or None where it is not one."""
    from .flow import walltime_seconds
    seconds = walltime_seconds(text)
    return None if seconds is None else seconds // 60


def _hopemd_one(flow_dir, nid, spec, picks, name, root, say):
    """One call of the simulation's command line, for one source. Returns (run folders, job ids)."""
    where = os.path.join(flow_dir, "inputs")
    os.makedirs(where, exist_ok=True)
    spec_file = os.path.join(where, spec_name(nid, spec))
    with open(spec_file, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=2)
    args = ["--runs", root, "--quiet"]
    if name:
        args += ["--name", name]
    if picks:
        picks_file = spec_file.replace("_spec_", "_picks_")
        with open(picks_file, "w", encoding="utf-8") as fh:
            json.dump(picks, fh)
        args += ["--selections", picks_file]
    line = ('if [ -f ./activate.sh ]; then . ./activate.sh; fi; PY="${HOPEMD_PYTHON:-python3}"; '
            'PYTHONPATH=%s "$PY" -m hopemd.submit %s %s'
            % (shlex.quote(INSTALLS["hopemd"]), shlex.quote(spec_file),
               " ".join(shlex.quote(a) for a in args)))
    out = _shell(line, INSTALLS["hopemd"], say, minutes=_submit_minutes())
    got = _last_json(out)
    runs = got.get("runs") or {}
    ids = [str(j) for one in runs.values() for j in one.values() if str(j).isdigit()]
    for why in (got.get("refused") or {}).items():
        say("   refused %s: %s" % why)
    return list(runs), ids, runs


def _per_partner(s, key, i):
    """What this partner's own box says, or the first partner's where it has none.

    The first partner's box carries no suffix and each further one is numbered, as the folding
    cards number their entities. A card filled in before the numbered boxes existed has only the
    first, and every partner takes it, which is what the one box used to mean.
    """
    mine = str(s.get("%s_%d" % (key, i + 1)) or "").strip() if i else ""
    return mine or str(s.get(key) or "").strip()


def _passive_mode(said, has_active, fallback):
    """The passive residues one molecule is given: the card's own answer, or, given "auto", the
    surface around its own active residues where it has any and `fallback` where it has none."""
    said = str(said or "auto").strip().lower()
    return "around" if has_active else fallback


def _ensemble_page():
    """The ensemble page's own machinery, or a sentence saying why a run cannot be started.

    Imported here rather than at the top of the file so that a laptop, which reads this module's
    cards and never its drivers, is not asked for a package it was not built with.
    """
    try:
        from hope_labs.ensemble import submit as submitmod
        from hope_labs.folding import sites as sitesmod
    except ImportError as why:
        raise NotWired("the ensemble page is not importable from this install, so a flow cannot "
                       "sample an ensemble here: %s" % why)
    return submitmod, sitesmod


def _bioemu(node, flow, carried, flow_dir, record, say=print):
    """One sequence sampled, in the two phases the tool needs.

    Phase one happens inside this call, on the login node, because it is the only part of a run
    that can reach the alignment server; the folding cards are here for the same reason and in
    the same words. Phase two is the GPU job whose id comes back, and it reads its own samples at
    the end, so a finished job is a run the card below can take shapes from.
    """
    submitmod, sitesmod = _ensemble_page()
    s = node.settings
    site = sitesmod.here()
    root = _run_root(node, node.card)
    where = _unique(root, "%s_%s" % (flow.name, node.id))
    body = {
        "name": "%s_%s" % (flow.name, node.id),
        "out": where,
        "sequence": str(s.get("sequence") or ""),
        "gpu": _folding_gpu(s, site),
    }
    # Only what the card was given. A key left out falls through to the page's own default, which
    # is where the considered answer lives; an empty box is "keep yours" rather than a number to
    # pass on. shapes_out is the flow's own and is not the page's, so it is not sent.
    for key in ("num_samples", "model_name", "seed", "walltime", "account"):
        value = s.get(key)
        if value in (None, "") or str(value) in ("0", "0.0"):
            continue
        body[key] = value
    try:
        got = submitmod.queue("bioemu", body, [root], site=site, dry_run=DRY_RUN, say=say)
    except Exception as why:                                    # noqa: BLE001
        # The page says what is wrong with a form by returning it, and raises only for what a
        # form cannot be wrong about. Being called rather than posted to, anything it raises
        # arrives here, and a card that will not start has to say why in one sentence whichever
        # of the two it was.
        raise NotWired("the sampling was refused: %s" % why)
    if got.get("problems") or any((got.get("fields") or {}).values()):
        raise NotWired("the sampling was refused: %s"
                       % "; ".join([str(p) for p in got.get("problems") or []]
                                   + [str(v) for v in (got.get("fields") or {}).values() if v]))
    run = got.get("run") or {}
    jid = str(run.get("job_id") or "")
    if not jid.isdigit() and not DRY_RUN:
        raise NotWired("the sampling queued no job (its record says job_id=%r)"
                       % run.get("job_id"))
    say("%s: %s residues, %s samples, alignment %s"
        % (run.get("name") or "bioemu", run.get("residues"),
           (run.get("asked") or {}).get("num_samples"), run.get("alignments") or "unknown"))
    return run.get("path") or where, [jid] if jid.isdigit() else []


# --- a structure already in hand ----------------------------------------------------------------
# Not a tool either, and the smallest card there is: it copies one file into the flow and says so.
# Copied rather than pointed at for the same reason the target's is - a flow that reads a file
# where somebody left it is a flow that breaks when that folder is tidied, and the run folders in
# this lab are tidied automatically.

def _structurefile_card(node, flow, carried, flow_dir, record, say=print):
    s = node.settings
    where = os.path.join(flow_dir, "inputs")
    os.makedirs(where, exist_ok=True)
    src = os.path.abspath(os.path.expandvars(os.path.expanduser(s.get("path") or "")))
    if not src:
        raise NotWired("no structure file was named on this card")
    if not os.path.isfile(src):
        raise NotWired("the structure file is not there: %s" % src)
    got = os.path.join(where, "%s%s" % (node.id, os.path.splitext(src)[1] or ".pdb"))
    shutil.copy2(src, got)
    if os.path.getsize(got) < 200:
        raise NotWired("the structure file is empty, or very nearly: %s" % src)
    say("the structure was copied from %s" % src)
    record.set(node.id, path=got)
    return where, []


BY_CARD = {
    "target": _target_card,
    "structurefile": _structurefile_card,
    "prepare": _prepare_card,
    "prepare_receptor": _prepare_receptor_card,
    "boltz2": _folding,
    "colabfold": _folding,
    "bioemu": _bioemu,
    "rfdiffusion": _rfdiffusion,
    "rfd3": _rfd3,
    "rfd4": _rfd4,
    "boltzgen": _boltzgen,
    "hopemd": _hopemd,
}
