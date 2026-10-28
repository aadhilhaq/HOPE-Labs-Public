"""Reading a target, checking a form, and queueing a design run.

By Aadhil Haq

Everything here goes through hope_flow: the target is read by the target card's own driver, the
form is checked by the flow's own check(), and the run is queued by the tool's own driver. A run
queued from this page and a run queued from the canvas are therefore the same run, refused for
the same reasons and landing in the same folder, because there is one piece of code that does it
and this is not a second copy of it.

The page builds a flow of two cards, a target and the tool on it, and queues it card by card the
way hope_flow/run.py does: the target first, so the structure is in the run folder, then the
adapter that carries it across, then the tool. The difference from a flow is only that nobody had
to draw it.

What is here and not in hope_flow is the part a flow cannot do, because a flow is checked on a
laptop where the structure is not: looking inside the target. The chains, whether a hotspot is a
residue that exists, and what the hotspots become once converted are all read off the file, by
the tool's own parser, so what the page shows is what the job will see.
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import shutil
import time

from hope_flow import adapters, cards, drivers
from hope_flow import flow as flowmod
from hope_flow import state as flowstate
from hope_flow.helpers import boltzgen_design, rfd3_design, rfd4_design, rfdiffusion_design

from . import CARD_ONLY, SERVES
from .. import structure

#: The most a structure may be when it is uploaded through the page. A receptor is a few hundred
#: kilobytes; anything near this is a trajectory or a mistake, and reading it would only waste the
#: login node. A file too large for this is given as a path instead, which costs nothing.
UPLOAD_CAP = 24 * 1024 * 1024

#: What the page takes as an uploaded structure. A PDB or an mmCIF: the target card writes the
#: design tools their own copy of an mmCIF as a PDB (drivers.clean_copy, through gemmi) and
#: hope_labs.structure reads either, so refusing one at the box - which this did until 26 October
#: 2026 - was refusing a file the flow could have used. Anything else is refused here, where a
#: person can act on it, rather than copied in and found to hold nothing.
STRUCTURE_SUFFIXES = (".pdb", ".cif", ".mmcif")

#: The parser each tool's job uses to read a structure. They are the same function in four files
#: and will stay that way, since each runs under its own tool's Python - RFD4-Proteina's being the
#: one inside its image; taking the tool's own is what makes the chains on the page the chains the
#: job will find, rather than nearly them.
PARSER = {"rfdiffusion": rfdiffusion_design, "rfd3": rfd3_design,
          "boltzgen": boltzgen_design, "rfd4": rfd4_design}

#: What an mmCIF target means for each tool, for the one case the page has to refuse one: a file
#: the target card could not write as a PDB. One sentence for all four would be wrong about two of
#: them, so each says what is true of itself, read off the helper that drives it.
CIF_SAYS = {
    "rfdiffusion": "RFdiffusion is handed its target as inference.input_pdb and reads it with its "
                   "own PDB parser, so there is no form of this run that takes an mmCIF.",
    "rfd3": "RFdiffusion3 reads an mmCIF itself, and so does the helper that finds its chains, "
            "but the contig and the hotspots this page shows you first are read off PDB columns, "
            "so the page cannot tell you what the job will be told.",
    "boltzgen": "BoltzGen is given the target as a path in its own specification, but its binding "
                "residues are positions in the chain rather than residue numbers, and this page "
                "counts those positions off PDB columns.",
    "rfd4": "RFD4-Proteina is the one tool here that wants a CIF - a condition spec refuses a PDB "
            "outright, and its helper hands a target that is already a CIF straight on - but the "
            "contig and the selections this page previews are read off PDB columns.",
}

#: Walltimes Slurm takes, from `man sbatch`. Checked here because the form is where a mistake is
#: cheap: sbatch's own complaint arrives after the run folder has been written.
WALLTIME = re.compile(r"^(\d+|\d+:\d{1,2}|\d{1,3}:\d{1,2}:\d{1,2}|"
                      r"\d+-\d{1,2}(:\d{1,2}){0,2})$")

#: What the `gpu` partition allows. A job asking for longer is refused at submission, which is
#: worth saying while the box is being typed in rather than afterwards.
MAX_DAYS = 4


class Refused(ValueError):
    """Something the page will not do, said in terms the person can act on."""


def safe_name(text, fallback="run"):
    """A name a run folder and a flow can both carry.

    hope_flow refuses a flow whose name is not letters, digits, dot, dash or underscore
    (flow.NAME_OK), so a target called "3 d_struct (1).pdb" is cut back to one rather than
    refused after everything else has been filled in.
    """
    out = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or "")).strip("_.-")
    out = re.sub(r"_{2,}", "_", out)[:48].strip("_.-")
    return out if re.match(r"^[A-Za-z0-9]", out or "") else fallback


def card_of(tool):
    if tool not in SERVES:
        raise Refused("this page serves %s, not %r" % (" and ".join(SERVES), tool))
    card = cards.BY_KEY.get(tool)
    if card is None:
        raise Refused("%s is not a card this install has" % tool)
    return card


def fields(tool):
    """The form's fields, which are the tool's card minus what its submitter does not read.

    Returned as the card returns them, defaults and all, so there is nothing here for the page
    and the canvas to disagree about: one description, read twice.
    """
    skip = CARD_ONLY.get(tool, ())
    return [s.as_json() for s in card_of(tool).settings if s.key not in skip]


def target_fields():
    """The target card's own fields, for the boxes above the form."""
    return [s.as_json() for s in cards.TARGET.settings]


def default_of(fs, key):
    return next((f["default"] for f in fs if f["key"] == key), None)


# --- the target --------------------------------------------------------------------------------

def _target_node(settings):
    return flowmod.Node("target", "target", settings)


def read_target(tool, body, staging):
    """Read a target the three ways the form offers it, and say what it holds.

    The fetch, the copy and every refusal are the target card's driver, called here exactly as
    the runner calls it. The structure is kept in the page's own folder and handed to the run as
    a file when the form is submitted, rather than fetched again: the RCSB answering twice is two
    files, and a person who has looked at the chains of one should get that one.
    """
    kind = (body.get("kind") or "pdb").strip()
    where = os.path.join(staging, secrets.token_hex(4))
    os.makedirs(where, exist_ok=True)
    pdb_id = ""
    if kind == "pdb":
        pdb_id = (body.get("id") or "").strip().upper()
        settings = {"source": "rcsb", "pdb_id": pdb_id}
        name = pdb_id.lower()
    elif kind == "file":
        raw = base64.b64decode(body.get("data") or "", validate=False)
        if not raw:
            raise Refused("that file arrived empty")
        if len(raw) > UPLOAD_CAP:
            raise Refused("that file is %.0f MB, and %d MB is the most the page takes. Put it in "
                          "your scratch and give the path instead"
                          % (len(raw) / 1e6, UPLOAD_CAP // 1024 // 1024))
        given = os.path.basename(body.get("filename") or "target.pdb")
        if not given.lower().endswith(STRUCTURE_SUFFIXES):
            raise Refused("the structure has to be a .pdb or a .cif file: those are the two the "
                          "target card can read, and anything else would be copied in and then "
                          "found to hold no chain")
        # The suffix is kept, because it is what the target card reads the format off: an mmCIF
        # saved as uploaded.pdb would go through the PDB branch of the cleaning and come out empty.
        path = os.path.join(where, "uploaded" + os.path.splitext(given)[1].lower())
        with open(path, "wb") as handle:
            handle.write(raw)
        settings = {"source": "file", "path": path}
        name = os.path.splitext(given)[0]
    elif kind == "path":
        given = os.path.abspath(os.path.expanduser((body.get("path") or "").strip()))
        settings = {"source": "file", "path": given}
        name = os.path.splitext(os.path.basename(given))[0]
    else:
        raise Refused("a target comes from the RCSB, a file or a path, not %r" % kind)

    node = _target_node(settings)
    one = flowmod.Flow(name="target", nodes=[node])
    record = flowstate.State(where)
    try:
        drivers.submit(node, one, {}, where, record, say=lambda *_a: None)
    except drivers.NotWired as why:
        raise Refused(str(why))
    path = record.card("target").get("path") or ""
    if not os.path.isfile(path):
        raise Refused("the target could not be read into the page's folder")
    notes = []
    # Read off the file rather than off its name: a PDB called model.cif was sent down the mmCIF
    # branch and refused as holding no chain this page can read, of a file whose two chains and
    # 137 residues every tool's own reader reads perfectly well.
    if is_mmcif(path):
        path, said = from_cif(tool, path, record)
        notes.append(said)
    elif path.lower().endswith((".cif", ".mmcif")):
        path, said = as_pdb(path)
        notes.append(said)

    chains = PARSER[tool].chains_of(path)
    if not chains:
        raise Refused(no_chains(tool, path))
    # The letters as well as the numbers, so the page can draw each chain and the site can be
    # pressed onto it rather than typed. Sent with the target rather than fetched after it: a
    # second request would draw the ruler a moment late, and the first thing a person does with a
    # target is look for the residues they came to mark.
    letters = residues_of(tool, path)
    # And what this page found in the file that the tool's own reader cannot use: a nucleic acid
    # strand, a ligand or a sugar in a chain of its own, a residue it has no name for. Nothing
    # here refuses the target; it is said because the alternative is a page that silently draws
    # less than the file holds. The chains themselves go with it, because they cannot be designed
    # against and can be read a site off: the button that takes the site from a bound partner
    # offers them by name.
    held = shared_reading(path)
    also = also_in(path, held, chains)
    notes += unseen(tool, held, chains, also)
    return {"name": safe_name(name, "target"), "path": path, "pdb_id": pdb_id,
            "chains": [{"id": chain, "n": len(numbers), "first": numbers[0], "last": numbers[-1],
                        "residues": letters.get(chain, [])}
                       for chain, numbers in sorted(chains.items())],
            "residues": sum(len(numbers) for numbers in chains.values()),
            "also": also,
            "cutoff": CONTACT,
            "notes": [one for one in notes if one]}


def _chains_in(tool, path, asked):
    """{chain: [residue numbers]} for the chains asked for, and the ones that are not there."""
    present = PARSER[tool].chains_of(path)
    wanted = [c.strip() for c in (asked or "").split(",") if c.strip()]
    if not wanted:
        wanted = sorted(present)
    keep = {c: present[c] for c in wanted if c in present}
    return keep, [c for c in wanted if c not in present], present


def is_mmcif(path):
    """Whether this file is an mmCIF, read off what is in it rather than off what it is called.

    An mmCIF opens with a data block and names its columns before its rows; a PDB has neither, and
    no PDB record begins with data_ or an underscore. So the head of the file settles it, and 64 kB
    is far past any deposited entry's header.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            head = handle.read(64 * 1024)
    except OSError:
        return False
    for line in head.splitlines():
        if line.startswith(("data_", "_atom_site.", "loop_")):
            return True
    return False


def shared_reading(path):
    """What hope_labs.structure makes of this file, or [] when it cannot read it either.

    The page's other reader, and the better-read of the two: it reads mmCIF as well as PDB and it
    knows a nucleic acid chain from a protein one, neither of which the tools' own readers do. It
    is written once for every page in this install, so the sequence BioEmu samples, the row
    Boltz-2 folds and the letters under this ruler all come from the same four functions and
    cannot drift apart. What it cannot do is replace chains_of here: the ruler marks residues by
    the numbers a job will aim at, and those are the ones the tool's own reader can see.
    """
    try:
        return structure.read(path)
    except structure.Unreadable:
        return []


def many(count, word, plural=""):
    """`3, "residue"` as "3 residues", `1, "residue"` as "1 residue".

    A page says things to a person, and "(s)" is a form a program fills in rather than a thing
    anybody writes. There were fifteen of them in these sentences and several reached the page:
    the target's ?, the chain chips' tooltips and the job dialog all read "residue(s)".
    """
    return "%d %s" % (count, word if count == 1 else (plural or word + "s"))


def holds(chains):
    """"chain A, 85 protein residues; chain B, 13" - a file's contents, for a refusal to name."""
    return "; ".join("chain %s, %s" % (one["id"], many(one["n"], "%s residue" % one["kind"],
                                                       "%s residues" % one["kind"]))
                     for one in chains)


def _reader_name(chains, want):
    """The shared reader's own name for the chain a tool's reader calls `want`.

    A PDB with nothing in column 22 is one chain called A to every tool's reader here, because A
    is the letter they default to, and "_" to hope_labs.structure, which does not invent one.
    Translated rather than passed through: asked for chain A of such a file the shared reader
    finds no atoms at all, and a site read off it would come back empty with nothing said about
    why.
    """
    have = {one["id"] for one in chains}
    if want in have:
        return want
    return "_" if "_" in have else want


def no_chains(tool, path):
    """Why a file is not a target, in terms of what it actually holds.

    The tools' own readers see amino acids in PDB columns and nothing else, so everything they
    cannot use came back as one sentence: "no amino-acid chain was found in that file". That was
    wrong about an mmCIF, which this page now converts and reads, and wrong about a structure of
    DNA, which the shared reader reads perfectly well and no tool here can design against. So the
    shared reader is asked what the file is, and the refusal names it: a file nothing can read, an
    mmCIF that reached the form without going through the target box, or a structure that is
    simply not protein.
    """
    held = shared_reading(path)
    if not held:
        return ("no chain was found in that file. Every tool here reads a target by its ATOM "
                "records, so a structure with none - or none carrying an alpha carbon - is not a "
                "target any of them can aim at")
    if is_mmcif(path):
        # The file is readable and the chains are there; what is wrong is that it has reached the
        # form without going through the target box, which is where an mmCIF is written as the PDB
        # the tools are handed. Asked of the contents and not of the suffix, so an mmCIF saved
        # under a .pdb name is told the one thing that gets it read rather than being told it
        # holds chains and holds none.
        return ("that file is an mmCIF and it holds %s, but the form has been handed it as it is. "
                "Read it in as a target above, which writes it as the PDB the tools are given. %s"
                % (holds(held), CIF_SAYS[tool]))
    return ("that file holds %s, and no amino-acid chain %s's own reader can see. Every tool here "
            "designs a protein binder onto a protein target, so there is nothing in it to aim at"
            % (holds(held), card_of(tool).name))


def from_cif(tool, path, record):
    """(the PDB the page works from, what to say about it) for a target given as an mmCIF.

    An mmCIF is a target the flow can use and the page could not read: chains_of reads PDB columns,
    and an atom_site row has its fields separated by spaces, so every one of the lab's twelve
    benchmark entries given as mmCIF came back as a target with no chains in it.

    There is no second conversion here. The target card already writes the design tools their own
    copy of the target, cleaned to its polymer residues, and for an mmCIF it writes that copy
    through gemmi - so by the time this runs there is a PDB of the structure beside it, and it is
    the very file the job will read (drivers._for_design). The page works from that one: the chips,
    the ruler, the contig and the site are then read off the file the tool is given, which is the
    whole reason this page reads a target at all. Measured on 1YCR given as an mmCIF: chains A, 25
    to 109, and B, 17 to 29, come out of the conversion as they went in, and the same 22 residues
    of chain A touch the peptide as when the entry is given as a PDB.

    Where no such copy was written - no gemmi in this environment, or a structure past what a PDB
    can hold - the target is refused here, with what the file holds and what an mmCIF means for
    this tool, rather than at the far end of a twelve-hour booking.
    """
    held = shared_reading(path)
    cleaned = record.card("target").get("cleaned") or ""
    # The copy has to be readable as well as written. clean_copy reads the format off the suffix,
    # so an mmCIF called target.pdb went down its PDB branch, and seven of its atom_site rows had
    # a residue name in the columns a PDB keeps one in: it wrote a seven-atom file of nonsense and
    # recorded it as the cleaned copy. A copy this page's own reader finds no chain in is not one.
    kept = shared_reading(cleaned) if cleaned and os.path.isfile(cleaned) else []
    if not kept:
        cleaned = _cleaned_again(path)
        kept = shared_reading(cleaned) if cleaned else []
    if not cleaned:
        raise Refused(
            "that file is an mmCIF and it could not be written as a PDB here, so the residue "
            "numbers a site is written in cannot be read off it. It holds %s. %s Convert it "
            "yourself and give the PDB."
            % (holds(held) or "no chain this page can read", CIF_SAYS[tool]))
    said = ("that file is an mmCIF: the target card has written it as a PDB, which is the copy "
            "the design tools are given, and the chains below are read off that copy")
    # What the cleaning really dropped, matched on the sequence rather than on the chain letter.
    # gemmi writes the one-character chain column a PDB has, so a two-character auth_asym_id -
    # ordinary in a deposited mmCIF - comes out renamed: chain AA becomes A. Compared by letter,
    # every such chain was reported as dropped, in a sentence that also called it protein, and an
    # 81-chain assembly lost 72 of them into one paragraph. A renamed chain is still there.
    sequences = {one["sequence"] for one in kept}
    gone = [one for one in held if one["sequence"] not in sequences]
    if gone:
        # Not a fault: cleaning keeps the polymer and drops the rest, so a chain of ligands or
        # waters is meant to disappear. Said because a chain named in a paper that is no longer
        # there is otherwise a chip somebody looks for and does not find.
        said += ". It does not hold %s%s, which the cleaning drops: a design tool is given the "\
                "polymer and nothing else" % (holds(gone[:6]),
                                              " and " + many(len(gone) - 6, "other chain")
                                              if len(gone) > 6 else "")
    return cleaned, said


def _cleaned_again(path):
    """A second go at the PDB copy of an mmCIF, for one written under a .pdb name. "" when none.

    hope_flow's clean_copy reads the format off the suffix, as the tools' own readers do, so an
    mmCIF called model.pdb went down its PDB branch and came out with no ATOM records in it. The
    page then refused a file holding 98 amino acids in two chains. Nothing is converted here that
    is not converted there - it is the same clean_copy, through the same gemmi - it is only handed
    the file under a name it can recognize, in the page's own staging folder, which is where the
    target card put the copy in the first place.
    """
    where = os.path.dirname(os.path.abspath(path))
    named = os.path.join(where, "target_as_given.cif")
    try:
        if os.path.abspath(path) != named:
            shutil.copyfile(path, named)
    except OSError:
        return ""
    got, _said = drivers.clean_copy(named, os.path.join(where, "target_cleaned.pdb"))
    return got if got and os.path.isfile(got) and shared_reading(got) else ""


def as_pdb(path):
    """(a copy of this file under a .pdb name, what to say about it), for a PDB named .cif.

    A PDB called model.cif is read by every tool's own reader, which takes PDB columns however the
    file is named, and not by this page's: hope_labs.structure dispatches on the suffix and carries
    only the opposite fallback, a .pdb that is really an mmCIF. So the chains came back from the
    tool's reader, the ruler came back without a letter in it, and no site could be measured off a
    file that is a perfectly good PDB of 137 residues.

    Nothing is converted here. It is the same bytes under a name both readers agree about, written
    in the page's own folder, which is where the target card had already put its copy.
    """
    named = os.path.join(os.path.dirname(os.path.abspath(path)), "target_as_given.pdb")
    try:
        if os.path.abspath(path) != named:
            shutil.copyfile(path, named)
    except OSError:
        return path, ""
    return named, ("that file is named .cif and holds a PDB, so the page reads it as one and the "
                   "run is given it as one: every tool here reads PDB columns whatever the file "
                   "is called")


def _seen_by(chains, one):
    """A tool's own residue numbers for this chain of the shared reader's, or None for neither.

    The tool's reader calls a blank chain A, because A is the letter it defaults to, so a file of
    one unnamed chain is "_" to the shared reader and "A" to the tool's. The same chain, and it
    must not be reported as one either of them is missing.
    """
    if one["id"] in chains:
        return chains[one["id"]]
    return chains.get("A") if one["id"] == "_" else None


def atom_chains(path):
    """{chain: how many residues of it carry a heavy atom}, as the file names the chains.

    The other way of counting a chain, and the one a contact needs. hope_labs.structure.read finds
    a chain by one atom per residue - an alpha carbon, or a C1' for a nucleotide - which is what a
    sequence is read from and is the wrong question to ask of a partner: a ligand has neither, nor
    has a sugar, nor an ion. Counted that way a protein with imatinib bound in its site was a
    structure of one chain, so the button that reads the site off what the target is bound to was
    not offered at all - although the ligand's four heavy atoms are 3 A from five residues of the
    chain. A contact is measured on atoms and needs no alphabet.
    """
    out = {}
    for chain, number, _x, _y, _z in structure.heavy_atoms(path):
        out.setdefault(chain, set()).add(number)
    return {chain: len(numbers) for chain, numbers in sorted(out.items())}


def also_in(path, held, chains):
    """[{id, n, kind}] for the chains of this target that a tool's own reader has no key for.

    A nucleic acid strand, a ligand, a sugar or an ion in a chain of its own. None of them is
    something these tools design against, and every one of them is something a target can be
    bound to, so they are no use as a chain to keep and every use as a chain to read a site off.
    The ids are the file's own, which is what the measuring is asked in."""
    mine = {_reader_name(held, chain) for chain in chains}
    kinds = {one["id"]: one for one in held}
    out = []
    for chain, residues in atom_chains(path).items():
        if chain in mine:
            continue
        one = kinds.get(chain)
        out.append({"id": chain, "n": one["n"] if one else residues,
                    "kind": one["kind"] if one else "non-polymer"})
    return out


def _all_chains(path, held, chains):
    """Every chain this target holds, by the file's own names, however it has to be found.

    The union of the three readings, because each sees something the other two do not: the tool's
    own reader sees amino acids in PDB columns, the shared reader sees polymer chains and their
    sequences, and the atoms see a ligand and also miss a chain whose coordinates a model never
    placed. Asked because "this structure holds one chain" must not be said of a file with three
    in it, which is what the every-contact reading said of nucleic.pdb.
    """
    out = set(atom_chains(path)) | {one["id"] for one in held}
    return sorted(out | {_reader_name(held, chain) for chain in chains})


def unseen(tool, held, chains, also=()):
    """What a target holds that the tool's own reader cannot use, as sentences for the page."""
    name = card_of(tool).name
    out = []
    for one in also:
        # A polymer chain is said by its kind and its length; a ligand, a sugar or an ion has no
        # sequence to count, so it is said as the groups it is. "1 non-polymer residue(s)" is what
        # the one sentence read as of a lone calcium.
        what = (many(one["n"], "%s residue" % one["kind"], "%s residues" % one["kind"])
                if one["kind"] != "non-polymer"
                else many(one["n"], "group") + " that are not part of a polymer: a ligand, "
                     "a sugar or an ion")
        out.append("the file also holds chain %s, %s. It is not a chain %s can design against, "
                   "and the button above will read a site off it" % (one["id"], what, name))
    for one in held:
        mine = _seen_by(chains, one)
        if mine is None:
            continue
        aimable, spelled = set(mine), set(one["numbers"])
        absent = sorted(spelled - aimable)
        if absent:
            # The finding first and the reason after the colon, because the card shows the
            # finding alone: "chain B holds 14 residues and RFD4-Proteina's own reader sees 8 of
            # them" is thirteen words, two of which say nothing a person needs here.
            out.append("chain %s holds %s and %s sees %d of them: residue "
                       "%s %s written as a hetero group or outside the twenty amino acids, so "
                       "%s"
                       % (one["id"], many(one["n"], "residue"), name, len(mine), _some(absent),
                          "is" if len(absent) == 1 else "are",
                          "it is not one you can aim a hotspot at" if len(absent) == 1
                          else "they are not residues you can aim a hotspot at"))
        blank = sorted(aimable - spelled)
        if blank:
            out.append("chain %s carries residue %s, which this page's own reader has no residue "
                       "for: the ruler draws %s as \"?\". The number is still the one a hotspot "
                       "is written in"
                       % (one["id"], _some(blank), "it" if len(blank) == 1 else "them"))
    if chains and not held:
        # The ruler is all question marks and nothing else here would say why: the tools' readers
        # take PDB columns as they come and this one skips an atom on a later alternate location,
        # so a structure written entirely on altloc B is a chain to them and no chain at all here.
        out.append("this page's own reader could not read that file, although %s's reader found "
                   "%s in it. The ruler is drawn with its numbers and no letters, and no site can "
                   "be measured off the structure; the numbers are the ones a hotspot is written "
                   "in" % (name, holds([{"id": c, "n": len(n), "kind": "amino-acid"}
                                         for c, n in sorted(chains.items())])))
    for chain, twice in shared_numbers(chains).items():
        out.append("chain %s carries %s more than once (%s), because this "
                   "structure has insertion codes. A hotspot is a number and has nowhere to put "
                   "a code, so the ruler draws every residue and offers only the first residue "
                   "of each shared number"
                   % (chain, many(len(twice), "residue number"), _some(twice)))
    return out


def _some(numbers, most=8):
    """"2, 14" - a few numbers for a sentence, and how many were left out of it."""
    shown = ", ".join(str(number) for number in numbers[:most])
    return shown + (" and %d more" % (len(numbers) - most) if len(numbers) > most else "")


def residues_of(tool, path):
    """{chain: [[number, one-letter], ...]} in file order, for the ruler the site is picked on.

    The site used to be typed as "54,56,66-70", and a person reading a paper will go on typing it
    that way, so the box stays. But a number typed into a box cannot be checked by eye against the
    structure, and the one mistake that matters here is silent: a residue aimed at the wrong face
    designs against a different patch of surface for twelve hours and fails nothing. So the page
    draws the chain and the site is pressed onto it.

    The numbers are the tool's own chains_of, by PDB columns, because the ruler marks the residues
    a job will be able to aim at and those are the ones the tool's reader can see. The letters are
    hope_labs.structure's, read there for every page in this install rather than a second time
    here, and the two are paired BY RESIDUE NUMBER.

    A residue number carried more than once - 21, 21A and 21B are three residues and one number -
    keeps all three, each with its own letter, in the order the file writes them. Only the first
    of them can be marked: the hotspot box says 21 and has nowhere to put the code, so the other
    two are drawn and not offered. shared_numbers() says which numbers those are, and the page
    says so under the target.
    """
    numbers = PARSER[tool].chains_of(path)
    held = shared_reading(path)
    letters = _shared_residues(path)
    out = {}
    for chain, found in sorted(numbers.items()):
        spelled = {}
        for number, letter in letters.get(_reader_name(held, chain), ()):
            spelled.setdefault(number, []).append(letter)
        taken = {}
        drawn = []
        for number in found:
            here = spelled.get(number) or []
            at = taken.get(number, 0)
            taken[number] = at + 1
            drawn.append([number, here[at] if at < len(here) else "?"])
        out[chain] = drawn
    return out


def shared_numbers(chains):
    """{chain: [the residue numbers it carries more than once]}, for the ruler and for a note.

    thrombin 1PPB's heavy chain carries twenty-eight of them - 60A to 60I, 129A to 129C, 186A to
    186D - because chymotrypsin numbering is an insertion code where the sequence is longer than
    the template. A number is all a hotspot can say, so a second button under one number was a
    button that marked the first residue of that number while naming the last: three buttons on
    21, 21A and 21B all carried data-id A21, pressing any marked all three, and the chip came
    back "A21 Pro" when the Leu had been pressed.
    """
    out = {}
    for chain, numbers in sorted(chains.items()):
        seen, twice = set(), []
        for number in numbers:
            if number in seen and number not in twice:
                twice.append(number)
            seen.add(number)
        if twice:
            out[chain] = twice
    return out


def _shared_residues(path):
    """{chain: [(number, letter)]} from the shared reader, or {} where it cannot read the file.

    structure.residues pairs the number and the letter in the one pass that read them both, so
    they cannot come apart on the way here. The guard is shared_reading's, for the same reason: a
    structure this page's reader cannot open is a ruler without letters, not a request that ends.
    """
    try:
        return structure.residues(path)
    except structure.Unreadable:
        return {}


#: How close two heavy atoms have to be to count as touching. hope_labs.structure's figure, which
#: is the one the lab's docking reports use for a contact, so a site taken from a bound partner
#: here and an interface read off a docked pose there are the same residues by the same rule. Read
#: from there rather than written again here: two copies of a measured number are two numbers
#: waiting to disagree.
CONTACT = structure.CONTACT


def _touching(path, held, keep, against, cutoff):
    """{chain: [residue numbers]} of `keep` within `cutoff` of `against`, by the shared reader.

    hope_labs.structure.interface does the measuring: it buckets every heavy atom of the chains
    measured against into cells of the cutoff, so each kept atom looks in twenty-seven cells
    rather than at every partner atom - a 5,000-atom receptor against a 1,000-atom partner is five
    million distance tests done the obvious way, which is seconds of a login node, and this is
    thousandths. It also skips the atoms a model never placed, which are written as NaN and poison
    every distance they are put into.

    `keep` is named as the page names it, which is the tool's own reader's naming, and is
    translated in and out: a blank-chain PDB that every tool here calls chain A is asked after by
    the name the shared reader gave it and answered under the name the page uses. `against` is
    already in the file's own naming, because a partner is found on atoms and the atoms carry the
    file's letters - translating it twice turned a ligand chain L into the blank chain "_".
    """
    mine = {_reader_name(held, c): c for c in keep}
    try:
        found, _said = structure.interface(path, list(mine), list(against), cutoff)
    except structure.Unreadable as why:
        # structure.interface reads the chains before it measures them, and shared_reading's guard
        # does not reach inside it: a file whose every alpha carbon sits on an alternate location
        # other than the first is read by the tools' own readers and not by this one. Unguarded
        # that reached the browser as "the page hit an error", which is not a refusal.
        raise Refused("this page's own reader cannot open that structure, so no site can be "
                      "measured off it: %s Mark the residues on the chain above instead." % why)
    return {mine.get(chain, chain): numbers for chain, numbers in found.items()}


def _aimable(found, present):
    """(the site, how many residues of it were dropped) - the residues a job can be told to aim at.

    interface() measures on atoms, so a ligand or an ion the file carries inside a kept chain comes
    back as a residue of that chain: a calcium written HETATM CA CA A 149, coordinated by the
    partner, is residue 149 of chain A. Marked, that is a hotspot the form then refuses in the
    next breath - the tool's own reader never saw it - so the site is cut back to what can be
    aimed at and the rest is counted and said.
    """
    out, dropped = {}, 0
    for chain, numbers in found.items():
        aimable = set(present.get(chain) or ())
        kept = [number for number in numbers if number in aimable]
        dropped += len(numbers) - len(kept)
        if kept:
            out[chain] = kept
    return out, dropped


def _left_out(dropped):
    """" (1 more was an ion or a hetero group ...)" - what _aimable took out, or "".

    Said rather than dropped quietly. The whole reason this page draws the chain is that a site
    nobody checked is a site that can be wrong without failing, and a reading that marks six
    residues having measured seven is the same fault in miniature.
    """
    if not dropped:
        return ""
    return (" (%d more %s a hetero group or an ion the file carries inside a kept chain, which no "
            "hotspot can name)" % (dropped, "was" if dropped == 1 else "were"))


def interface_with_partner(tool, path, asked_chains, cutoff=CONTACT, every=False):
    """The kept chains' residues that touch a chain this target does not keep.

    THE ONE PRESS THIS PAGE EXISTS FOR
    Most targets worth designing against arrive bound to something: 1YCR is MDM2 with the p53
    peptide in its cleft, and the residues that peptide touches are the site a binder wants. Until
    now a person had to find them in a paper, or in a viewer, and type them in. The structure
    already knows them.

    `every` is the other question a structure of three chains asks: not "what does the partner I
    left out touch" but "where do these chains touch each other at all". A trimer kept whole has
    no partner and so had no answer here; asked this way, every residue of each kept chain within
    the cutoff of ANY other chain in the file is marked, the chains being designed against
    included. The chains it measures against are found on atoms, so a DNA strand, a ligand or a
    co-crystallised peptide counts even where the tool's own reader cannot see it.

    The measuring is hope_labs.structure's, through _touching. The refusals are this page's own,
    because a person who pressed the button is owed the reason it did nothing.
    """
    keep, _absent, present = _chains_in(tool, path, asked_chains)
    if not keep:
        raise Refused("no chain of this target is kept, so there is no site to read")
    held = shared_reading(path)
    if every:
        return _every_contact(path, held, sorted(keep), present, cutoff)
    # The chains the tool's own reader cannot see count as partners here, because a contact is
    # measured on atoms and needs no alphabet: a target bound to a DNA strand, or sitting on a
    # ligand in a chain of its own, used to be told it had no bound partner at all. Named as the
    # file names them, which is what the measuring is asked in, and shown back in the page's own
    # names so a chip and a sentence agree.
    shown = {_reader_name(held, c): c for c in present}
    against = sorted({_reader_name(held, c) for c in present if c not in keep}
                     | {one["id"] for one in also_in(path, held, present)})
    others = [shown.get(c, c) for c in against]
    if not others:
        raise Refused("this structure holds only the chains you are designing against, so there "
                      "is no bound partner to read a site from. Mark the residues yourself, or "
                      "leave them unmarked and the binder may grow anywhere on the target")
    empty = _without_atoms(path, held, sorted(keep))
    if empty:
        raise Refused(_nothing_to_measure(empty))
    found, dropped = _aimable(_touching(path, held, sorted(keep), against, cutoff), present)
    total = sum(len(numbers) for numbers in found.values())
    if not total:
        empty = _without_atoms(path, held, (), against)
        if empty:
            raise Refused(_nothing_to_measure(empty))
        raise Refused("no residue of %s comes within %.1f A of %s, so those chains are not in "
                      "contact in this structure"
                      % (", ".join(sorted(keep)), cutoff, ", ".join(sorted(others))))
    return {"hotspots": found, "partner": sorted(others), "kept": sorted(keep), "cutoff": cutoff,
            "every": False,
            "said": "%d residue%s of %s within %.1f A of chain %s%s"
                    % (total, "" if total == 1 else "s",
                       ", ".join("chain " + c for c in sorted(found)), cutoff,
                       ", ".join(sorted(others)), _left_out(dropped))}


def _without_atoms(path, held, chains, named=()):
    """Which of these chains has no heavy atom in it to measure a contact with, as they are named.

    `chains` are the page's own names and are translated to the file's; `named` are the file's own
    and are not.

    Asked because nothing marked has two causes and they are not the same thing to the person who
    pressed the button: two chains that simply do not touch, and a chain there was nothing to
    measure in at all. The second is a real file. A design RFD4-Proteina wrote carried 1,219 atoms
    it never placed in 2,393, written as NaN, and a structure whose atoms are all on an alternate
    location other than the first reads the same way.
    """
    # One pass over the atoms rather than one per chain: asked the old way of the eight chains of
    # a 249,600-atom assembly, this was eight readings of a 20 MB file to answer one question.
    counted = atom_chains(path)
    return ([chain for chain in chains if _reader_name(held, chain) not in counted]
            + [chain for chain in named if chain not in counted])


def _nothing_to_measure(chains=()):
    """The refusal for a chain with no heavy atoms in it this page can measure.

    One sentence in one place, because the partner reading made it and the every-contact reading
    did not: a two-chain file of NaN coordinates was told that its chains "do not touch each
    other", which is a statement about a structure nobody could measure.
    """
    return ("%s no heavy atoms to measure, so no site can be read from it: the coordinates are "
            "ones a model never placed, or every atom is on an alternate location other than the "
            "first"
            % ("chain %s has" % ", ".join(chains) if chains else "that structure has"))


def _every_contact(path, held, keep, present, cutoff):
    """Every residue of the kept chains within `cutoff` of any other chain of the structure.

    One reading per kept chain, each against all the rest. A pair at a time would say which chain
    each residue touches and would read the file once per pair - fifty-six readings for the eight
    chains of 1CDL - so the sentence names the chains it measured against rather than claiming
    each of them was touched.

    It is still one reading per chain, and each of those is three passes over the file, so the cost
    is linear in the chains kept and not in the pairs. Measured: 1CDL, eight chains and 644
    residues, 0.22 s; a synthetic eight-chain assembly of 249,600 atoms in 19.7 MB, 15.8 s. A real
    deposited entry is nothing; an assembly or a prepared system given by path is a press that
    takes a quarter of a minute, which is the size at which this is worth doing differently.
    """
    ids = _all_chains(path, held, present)
    if len(ids) < 2:
        raise Refused("this structure holds one chain, so there is nothing for its residues to "
                      "touch. Mark the residues yourself, or leave them unmarked and the binder "
                      "may grow anywhere on the target")
    empty = _without_atoms(path, held, keep)
    if empty:
        # The refusal the partner reading has always made and this one did not, which is the whole
        # difference between a sentence and a false statement about the structure.
        raise Refused(_nothing_to_measure(empty))
    found = {}
    for chain in keep:
        mine = _reader_name(held, chain)
        for one, numbers in _touching(path, held, [chain],
                                      [c for c in ids if c != mine], cutoff).items():
            found.setdefault(one, set()).update(numbers)
    found, dropped = _aimable({chain: sorted(numbers)
                               for chain, numbers in sorted(found.items())}, present)
    total = sum(len(numbers) for numbers in found.values())
    against = sorted(set(ids) - {_reader_name(held, c) for c in keep})
    if not total:
        raise Refused("no residue of %s comes within %.1f A of another chain of this structure, "
                      "so its chains do not touch each other"
                      % (", ".join("chain " + c for c in keep), cutoff))
    return {"hotspots": found, "partner": against, "kept": keep, "cutoff": cutoff, "every": True,
            "said": "%d residue%s of %s within %.1f A of another chain of this structure%s%s"
                    % (total, "" if total == 1 else "s",
                       ", ".join("chain " + c for c in sorted(found)), cutoff,
                       " (chain %s)" % ", ".join(against) if against else "",
                       _left_out(dropped))}


def told_from(body):
    """The three conditions as the form sent them, for the one tool that has them.

    Absent means on, which is what the helper means by it too: a flow drawn on the canvas sends
    none of these keys and gets the run it has always had.
    """
    return {key: str(body.get(key, "true")).lower() not in ("false", "no", "0", "")
            for _name, key in rfd4_design.TOLD}


def preview(tool, path, asked_chains, hotspots, binder_min, binder_max, told=None):
    """What the job will be told about the target, worked out the way the job works it out.

    This is the one thing the page can say that a flow drawn on a laptop cannot, and for BoltzGen
    it is the whole reason the page is worth having: its hotspots are positions in the chain, not
    the numbering the structure uses, and getting that wrong does not fail. It designs against a
    different patch of the surface for twelve hours.
    """
    keep, absent, _present = _chains_in(tool, path, asked_chains)
    out = {"chains": sorted(keep), "absent": absent, "missing": [], "refused": [], "lines": []}
    if not keep:
        return out
    if tool == "rfdiffusion":
        named = rfdiffusion_design.residues(hotspots, sorted(keep)[0])
        out["missing"] = [one for one in named
                          if int(one[1:]) not in keep.get(one[0], [])]
        # RFdiffusion has nowhere to put an insertion code, so a hotspot carrying one is refused
        # against the box rather than dropped on the way into a job that would then aim a residue
        # away from the site. Its own words for it, from its own helper, and BoltzGen's below are
        # the same words: RFdiffusion3 is the one of the three that keeps a code.
        out["refused"] = rfdiffusion_design.coded_why(hotspots)
        out["contig"] = rfdiffusion_design.contig(keep, int(binder_min), int(binder_max))
        out["hotspots"] = named
        out["lines"] = ["contigmap.contigs=%s" % out["contig"]]
        if named:
            out["lines"].append("ppi.hotspot_res=[%s]" % ",".join(named))
    elif tool == "rfd3":
        # RFdiffusion3's own contig and its own hotspot list, from its own helper, not
        # BoltzGen's: the preview would show positions in the chain, which RFD3 does not use,
        # and no contig at all.
        #
        # Its spans come from chains_of and segments rather than its own spans_of: that one reads
        # the structure with biotite, which the launcher's environment has not got, and these two
        # are written against PDB columns for exactly this reason. The same stretches either way.
        asked_order = [c.strip() for c in (asked_chains or "").split(",") if c.strip()]
        order = [c for c in (asked_order or sorted(keep)) if c in keep]
        spans = {c: rfd3_design.segments(keep[c]) for c in order}
        named = rfd3_design.hotspots_of({"hotspots": hotspots, "chains": ",".join(order)})
        out["missing"], out["refused"] = rfd3_design.absent(named, spans)
        out["contig"] = rfd3_design.contig_of(
            {"binder_min": int(binder_min), "binder_max": int(binder_max), "target": path},
            order, spans)
        out["hotspots"] = sorted(named)
        out["lines"] = ["contig: %s" % out["contig"]]
        if named:
            out["lines"].append('select_hotspots: %s, each as a whole residue ("ALL")'
                                % ", ".join(sorted(named)))
            out["lines"].append("infer_ori_strategy: hotspots, so the binder grows off the face "
                                "they are on")
        else:
            out["lines"].append("infer_ori_strategy: com, so the binder may grow anywhere on "
                                "the target")
    elif tool == "rfd4":
        # RFD4-Proteina's own contig and its own selections, from its own helper. Its numbers are
        # the target's own: a residue is named in a selection by the number the structure carries,
        # which is what makes this preview worth reading - the same box filled in for BoltzGen
        # means a position in the chain instead.
        #
        # Its spans come from chains_of and segments rather than by reading the converted CIF: the
        # conversion needs atomworks, which lives in the image, and these two are written against
        # PDB columns for exactly this reason. The same stretches either way.
        asked_order = [c.strip() for c in (asked_chains or "").split(",") if c.strip()]
        order = [c for c in (asked_order or sorted(keep)) if c in keep]
        spans = {c: rfd4_design.segments(keep[c]) for c in order}
        switched = dict(told or {})
        plan = dict(switched, hotspots=hotspots, chains=",".join(order),
                    binder_min=int(binder_min), binder_max=int(binder_max), target=path)
        out["refused"] = rfd4_design.coded_why(hotspots)
        named = (rfd4_design.hotspots_of(plan, spans)
                 if rfd4_design.told(plan, "use_hotspots") else {})
        out["missing"] = rfd4_design.absent(named, spans)[0]
        out["contig"] = rfd4_design.contig_of(plan, order, spans)
        out["hotspots"] = ["%s%d" % (c, n) for c in sorted(named) for n in named[c]]
        out["lines"] = ["contig: %s" % out["contig"]]
        holes = sum(len(runs) - 1 for runs in spans.values())
        if holes:
            # Said here because it is the one thing about this tool's contig that cannot be made
            # to match the structure: see the helper's own note on why a break is not an option.
            out["lines"].append("the target has %s in its chains, and RFD4-Proteina cannot be "
                                "told a chain stops and starts again, so each chain goes in as "
                                "one piece" % many(holes, "gap"))
        if rfd4_design.told(plan, "hold_target"):
            out["lines"].append("C_CRDGRP: the target's backbone in one group, so it is held "
                                "rigid with its side chains left free")
        else:
            out["lines"].append("no C_CRDGRP: the target is not held, so the model may move it")
        if rfd4_design.told(plan, "reveal_sequence"):
            out["lines"].append("C_SEQ: the target's own sequence")
        else:
            out["lines"].append("no C_SEQ: the model is not told which residues it is designing "
                                "against, only where they are")
        if out["hotspots"]:
            out["lines"].append("C_HOT: %s, by the numbers this structure carries"
                                % ", ".join(out["hotspots"]))
        elif not rfd4_design.told(plan, "use_hotspots"):
            out["lines"].append("no C_HOT: the site is switched off, so the binder may grow "
                                "anywhere on the target")
        else:
            out["lines"].append("no C_HOT, so the binder may grow anywhere on the target")
        # The spec itself, built by the helper that writes it for the job rather than described a
        # second time here: what the form shows and what the job reads are one implementation.
        # Only once the hotspots are sound, because the helper refuses a missing one by raising,
        # and on this page that is a sentence against the box rather than the end of a request.
        if not out["missing"] and not out["refused"]:
            spec = rfd4_design.spec_of(plan, order, spans, "<the target, converted to CIF>")
            out["spec"] = "\n".join(rfd4_design.spec_lines(spec))
    else:
        # BoltzGen has nowhere to put an insertion code either: its binding residues are
        # positions in the chain, and the box is read here as a chain, a number and a range, so
        # a coded hotspot was dropped on the way into a twelve-hour run aimed at the residue of
        # the same number. Its own words for it, from its own helper, which are RFdiffusion's.
        out["refused"] = boltzgen_design.coded_why(hotspots)
        asked = boltzgen_design.wanted(hotspots, sorted(keep)[0])
        binding = {}
        for chain, numbers in sorted(asked.items()):
            if chain not in keep:
                out["missing"] += ["%s%d" % (chain, n) for n in numbers]
                continue
            got, absent_here = boltzgen_design.positions(keep[chain], numbers)
            out["missing"] += ["%s%d" % (chain, n) for n in absent_here]
            if got:
                binding[chain] = got
                out["lines"].append(
                    "chain %s: %s in the file are positions %s (the chain is numbered %d to %d)"
                    % (chain, ",".join(str(n) for n in sorted(set(numbers))),
                       ",".join(str(p) for p in got), keep[chain][0], keep[chain][-1]))
        out["binding"] = binding
        out["sequence"] = "%d..%d" % (int(binder_min), int(binder_max))
        out["lines"].append('the binder is asked for as "%s"' % out["sequence"])
    return out


# --- the form ----------------------------------------------------------------------------------

def _settings_from(tool, body):
    """The tool card's settings, as the form sent them, in the types the card says they are."""
    out = {}
    for field in fields(tool):
        key, kind = field["key"], field["kind"]
        if key not in body:
            continue
        value = body[key]
        if kind == "yesno":
            out[key] = bool(value) if not isinstance(value, str) else value.lower() in (
                "1", "true", "yes", "on")
        elif kind == "number":
            # None is what a field means by "whatever the tool would do", and the form carries it
            # as one when it was filled from the defaults. str() would make it the word None,
            # which is not a number and is not empty either, so it would reach the driver as text.
            text = "" if value is None else str(value).strip()
            if text == "":
                # A field whose default is "whatever the tool would do" has no number to fall
                # back to, and says so with no default at all. Empty is how that is carried
                # everywhere else - the canvas stores it, the drivers skip it - so it is what
                # goes here too, rather than a null that later has to be guarded against.
                out[key] = field["default"] if field["default"] is not None else ""
                continue
            try:
                out[key] = int(text) if re.match(r"^-?\d+$", text) else float(text)
            except ValueError:
                # Left as the text it was: the flow's own check says what is wrong with it, in
                # the same words it would use for a card filled in on the canvas.
                out[key] = text
        else:
            out[key] = str(value).strip()
    # The conditions, for the one tool that has them. Not card settings, so the loop above does
    # not see them: they are this page's own switches, carried in the node so the driver can put
    # them in the plan the helper reads. A run with none of them set is the run this tool has
    # always made, because the helper reads every one of them as on unless told otherwise.
    if tool == "rfd4":
        out.update(told_from(body))
    return out


def _target_settings(body):
    """The target card's settings for a form that has already read its structure."""
    return {"source": "file", "path": (body.get("path") or "").strip(),
            "chains": (body.get("chains") or "").strip(),
            "hotspots": (body.get("hotspots") or "").strip(),
            "binder_min": _int(body.get("binder_min"), 70),
            "binder_max": _int(body.get("binder_max"), 100)}


def _int(value, fallback):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def build(tool, body):
    """The flow this form describes: a target, the tool, and the link between them.

    A flow of two cards rather than one, because the target is a card: fetching the structure,
    copying it into the run and refusing a bad id are all the target card's driver, and the page
    has no business doing any of it a second way.
    """
    settings = _target_settings(body)
    target = _target_node(settings)
    node = flowmod.Node(tool, tool, _settings_from(tool, body))
    name = safe_name(body.get("name") or "", "") or safe_name(
        os.path.splitext(os.path.basename(settings["path"]))[0], "run")
    one = flowmod.Flow(name=name, nodes=[target, node],
                       edges=[flowmod.Edge("target", "target", tool, "target")])
    return one, target, node


def check(tool, body, roots=()):
    """What is wrong with the form, what Slurm will be asked for, and what is worth saying.

    The problems are the flow's own check(), word for word, so the page refuses what the canvas
    refuses. The per-field messages beside them are the page's alone: they are the checks that
    need the structure open, which is the one thing a flow drawn on a laptop cannot do.
    """
    card_of(tool)
    path = (body.get("path") or "").strip()
    bad, notes = {}, []
    nothing = {"problems": [], "plan": {}, "notes": notes, "preview": {}}
    if not path:
        # Said against the target box rather than in the list of problems: the box is where a
        # person is looking, and the flow's own words for it name a file when they have not got
        # as far as choosing how to give one.
        bad["target"] = "give a target first: a PDB id, a .pdb file or a path on the cluster"
        return dict(nothing, fields=bad)
    if not os.path.isfile(path):
        bad["target"] = "the structure is no longer at %s: read it again" % path
        return dict(nothing, fields=bad)

    # Every number on the form, not only the ones the flow will not start without. The flow
    # checks those; an optional one with a typo in it would otherwise reach the driver, which
    # calls int() on it and fails in a way nobody can act on.
    for field in fields(tool):
        if field["kind"] != "number":
            continue
        value = body.get(field["key"], "")
        # None is the answer a field gives when empty means "whatever the tool would do", and it
        # arrives here as one when the form was filled from the defaults. str() would turn it
        # into the word None and refuse it as not a number.
        text = "" if value is None else str(value).strip()
        if text and not re.match(r"^-?\d+(\.\d+)?$", text):
            bad[field["key"]] = "%s wants a number" % field["label"].lower()
    for key in ("binder_min", "binder_max"):
        if not re.match(r"^\d+$", str(body.get(key, "")).strip()):
            bad[key] = "a residue count, as a whole number"
    if bad.get("binder_min") or bad.get("binder_max"):
        # Nothing below can be worked out without them: the contig map, the specification and
        # the plan all carry the length, so there is nothing honest to show until they are numbers.
        return dict(nothing, fields=bad)

    lo, hi = _int(body.get("binder_min"), 70), _int(body.get("binder_max"), 100)
    if not _chains_in(tool, path, "")[2]:
        # Nothing the tool's own reader can see in this file at all. That is an answer about the
        # target rather than about the chains box, and the shared reader can say what the file
        # is: an mmCIF path typed straight into the form was told "the target has no chain A".
        bad["target"] = no_chains(tool, path)
        return dict(nothing, fields=bad)
    one, _target, node = build(tool, body)
    problems = one.check()
    try:
        seen = preview(tool, path, body.get("chains"), body.get("hotspots"), lo, hi,
                       told=told_from(body) if tool == "rfd4" else None)
    except SystemExit as why:
        # A helper refuses a target it cannot describe by raising SystemExit, which is how it
        # says so to the job it was written for: a target in more stretches of resolved residues
        # than RFdiffusion3 has chain letters, a chain numbered backwards. Read on the page, that
        # is an answer about the target rather than a reason to end the request.
        bad["target"] = str(why)
        return dict(nothing, fields=bad)
    if seen["absent"]:
        bad["chains"] = "the target has no chain %s" % ", ".join(seen["absent"])
    elif not seen["chains"]:
        bad["chains"] = "name at least one chain of the target"
    if seen.get("refused"):
        # The tool's own words for a hotspot it will not take, said against the box the number
        # was typed into rather than left for a job twelve hours later to say.
        bad["hotspots"] = " ".join(seen["refused"][:8])
    elif seen["missing"]:
        bad["hotspots"] = ("the target has no residue %s. Hotspots are residue numbers of this "
                           "structure, which is numbered as the chips above say"
                           % ", ".join(seen["missing"][:8]))

    walltime = str(body.get("walltime") or "").strip()
    if walltime and not WALLTIME.match(walltime):
        bad["walltime"] = "a walltime Slurm takes: 12:00:00, or 2-00:00:00 for two days"
    elif "-" in walltime and _int(walltime.split("-")[0], 0) > MAX_DAYS:
        bad["walltime"] = "the gpu partition allows %d days at most" % MAX_DAYS

    out = (body.get("out") or "").strip()
    if out:
        home = os.path.realpath(os.path.expanduser("~"))
        full = os.path.realpath(os.path.abspath(os.path.expanduser(out)))
        if full == home or full.startswith(home + os.sep):
            bad["out"] = ("home is 10 GB and 10 000 files on Grace, and one design run writes "
                          "more than that: put the run in your scratch")
        elif roots and not any(full == os.path.realpath(r)
                               or full.startswith(os.path.realpath(r) + os.sep) for r in roots):
            # Allowed, because somebody naming a folder usually means it. Said, because the Runs
            # screen reads only the folders the page was started with, so a run put outside them
            # is queued and then invisible here, which is a worse surprise than this sentence.
            notes.append("that folder is outside the ones this page reads, so the run will be "
                         "queued but will not appear on the Runs screen")

    gpu, partition = drivers.gpu_asked(node.settings)
    plan = {"gpu": gpu, "partition": partition,
            "cpus": drivers.DESIGN_RESOURCES[tool]["cpus"],
            "mem": drivers.DESIGN_RESOURCES[tool]["mem"],
            "walltime": walltime or "12:00:00",
            "account": str(body.get("account") or "").strip(),
            "designs": _int(body.get("designs"), default_of(fields(tool), "designs") or 10)}

    if hi - lo > 60:
        notes.append("a binder length as wide as %d to %d residues makes designs that are hard "
                     "to compare with each other" % (lo, hi))
    # Said against the menu it was chosen from, as well as by the driver, which is what stops a
    # flow. A card this model does not run on is a refusal rather than a note: the run would reach
    # the GPU and stop there, a quarter of an hour after it was queued.
    refused = drivers.CARD_REFUSED.get(tool, {}).get(gpu)
    if refused:
        bad["gpu"] = "%s. Choose a100 or a40, or leave the card on auto" % refused
    if tool == "boltzgen" and gpu == "a40":
        notes.append("BoltzGen wants 40 GB of card and an a40 has 48, so it fits, but its "
                     "analysis step has been reported running out at a hundred designs")
    # The driver refuses this too, which is what stops a flow; said here as well so that it is
    # said against the menu it was chosen from rather than after the target has been fetched.
    if tool == "boltzgen" and str(body.get("protocol") or "") == "protein-small_molecule":
        bad["protocol"] = ("this protocol designs against the target's own ligand, and a design "
                           "run is given the receptor cleaned to its polymer residues, so there "
                           "is no ligand left to design against")
    if tool == "rfd3":
        passes = _int(body.get("batches"), 1)
        if passes > 1:
            notes.append("%d designs in all: %d in each pass, over %d passes"
                         % (plan["designs"] * passes, plan["designs"], passes))
    if tool == "rfd4":
        if body.get("compile"):
            notes.append("the warm-up compiles the model for a set of input lengths before the "
                         "first design, which costs minutes and pays for itself only over a "
                         "campaign: leave it off for a handful of designs")
        if _int(body.get("nsteps"), 60) < 30:
            notes.append("fewer than thirty steps is a smoke test rather than a design: the "
                         "backbones come out rough")
    if tool == "rfdiffusion":
        sequences = plan["designs"] * _int(body.get("seqs_per_backbone"), 8)
        if sequences > 400:
            notes.append("that is %d sequences to design, and ProteinMPNN is run once per "
                         "backbone: a walltime longer than the default is worth setting"
                         % sequences)
    return {"problems": problems, "fields": bad, "plan": plan, "notes": notes, "preview": seen}


# --- queueing ----------------------------------------------------------------------------------

def run_folder(root, name):
    """Where this submission goes: the run root, the name, and the minute it was asked for.

    The minute rather than a counter, because a person submitting the same target twice in an
    afternoon should be able to tell the two apart in a folder listing a month later.
    """
    stamp = time.strftime("%Y%m%d-%H%M")
    where = os.path.join(root, "%s_%s" % (name, stamp))
    n = 2
    while os.path.exists(where):
        where = os.path.join(root, "%s_%s_%d" % (name, stamp, n))
        n += 1
    return where


def run_home(out, name):
    """(the folder a run given the Run folder `out` is written to, what to say about it).

    The folder itself, when no run is in it yet. When one already is - two runs given one folder,
    which is how a person keeps a batch together - the new run goes in a folder of its own inside
    it, named after the run. Written straight into the same folder, the second run's record took
    the place of the first's: both jobs were in the queue and the Runs screen showed one.
    """
    full = os.path.abspath(os.path.expanduser(out))
    held = ""
    for marker in ("submission.json", "flow.json"):
        path = os.path.join(full, marker)
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    held = str(json.load(handle).get("name") or "") or "a run"
            except (OSError, ValueError, AttributeError):
                held = "a run"
            break
    if not held:
        return full, ""
    inner, n = os.path.join(full, name), 2
    while os.path.exists(inner):
        inner = os.path.join(full, "%s_%d" % (name, n))
        n += 1
    return inner, ("%s already holds %s, so this run goes in a folder of its own inside it: %s"
                   % (full, held if held == "a run" else "the run " + held, inner))


def queue(tool, body, roots, dry_run=False, say=print):
    """Queue the run, card by card, the way hope_flow/run.py queues a flow.

    Returns what was written down beside the run. The order is the runner's: the target card
    first, which puts the structure in the run's own folder, then the adapter that carries it
    across, then the tool's driver, which writes the job and queues it.
    """
    checked = check(tool, body, roots)
    if checked["problems"] or checked["fields"]:
        return {"problems": checked["problems"], "fields": checked["fields"]}

    where = (body.get("out") or "").strip()
    one, target, node = build(tool, body)
    where = run_home(where, one.name)[0] if where else run_folder(roots[0], one.name)
    os.makedirs(where, exist_ok=True)
    node.settings["runs"] = where
    record = flowstate.State(where)
    with open(os.path.join(where, "flow.json"), "w", encoding="utf-8") as handle:
        handle.write(one.dumps())

    # Everything but sbatch, when it is a dry run: the structure is fetched, the plan is written
    # and the job script with it, so the page can be proved without asking for an A100 for twelve
    # hours. The driver owns the flag; this only puts it back, so a dry run cannot leave the
    # server unable to queue anything afterwards.
    was, drivers.DRY_RUN = drivers.DRY_RUN, bool(dry_run)
    try:
        drivers.submit(target, one, {}, where, record, say=say)
        carried = adapters.carry(one, record, one.edges[0], where)
        job, jobs = drivers.submit(node, one, {"target": [carried]}, where, record, say=say)
        jobs = [j for j in jobs if j]
        record.set(node.id, state=flowstate.QUEUED, rundir=job, jobs=jobs,
                   note="dry run: the job was written and not queued" if dry_run else "")
    except drivers.NotWired as why:
        record.set(node.id, state=flowstate.FAILED, note=str(why)[:400])
        record.write()
        raise Refused(str(why))
    finally:
        drivers.DRY_RUN = was
    record.write()

    kept = {
        "tool": tool, "name": one.name, "user": os.environ.get("USER") or "",
        "submitted": time.strftime("%Y-%m-%d %H:%M:%S"),
        "job_id": jobs[0] if jobs else "", "dry_run": bool(dry_run),
        "path": where, "rundir": job,
        "target": {"path": carried["path"], "pdb_id": (body.get("pdb_id") or "").strip().upper(),
                   "chains": carried["chains"], "hotspots": carried["hotspots"],
                   "binder": [carried["binder_min"], carried["binder_max"]]},
        "asked": dict(node.settings, **{"runs": where}),
        "plan": checked["plan"],
    }
    with open(os.path.join(where, "submission.json"), "w", encoding="utf-8") as handle:
        json.dump(kept, handle, indent=2)
        handle.write("\n")
    return {"run": kept}
