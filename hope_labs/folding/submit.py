"""Reading the entity rows, checking them, and queueing a folding run in two phases.

By Aadhil Haq

The form is a list of rows, each a type, a sequence and a number of copies, and everything here
is about turning those rows into the one thing the tool will read and refusing the rows that
would produce a run worth nothing.

Why the checking is here at all, rather than left to the tool
-------------------------------------------------------------
Neither tool refuses a sequence it does not understand. Boltz-2 maps every character it has no
residue for to its unknown token and folds the result: a lowercase sequence becomes a chain of
nothing but UNK, a T in an RNA row becomes N, and both come back as a structure with a confidence
score on it. ColabFold is the same. So a sequence is checked letter by letter here, where a
mistake costs a sentence, rather than in a job that spends four hours of a card on it.

The two phases, as the page runs them
-------------------------------------
queue() does phase one itself, on the login node, and then writes the job that is phase two. In
that order and in one call, because the alignment has to be on disk before the job script can
name it, and because a person pressing Submit should not have to come back and press something
else. A dry run skips phase one as well as sbatch: it writes the input, the script and the
record, and needs neither the network nor the queue.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import time

from . import (CCD_CODE, CHAIN_IDS, CHEMICAL, ENTITIES, MAX_COPIES, MAX_TOKENS, MODIFICATIONS,
               POLYMERS, SERVES, WARN_TOKENS)
from . import job as jobmod
from . import sites as sitesmod
from .job import Refused
#: The same cleaner the design page uses, imported rather than written again. It is also exactly
#: what ColabFold needs: its own safe_filename turns anything outside [A-Za-z0-9_.-] into an
#: underscore when it names the alignment after the FASTA header, so a name that has already been
#: through this one comes out of it unchanged, and the alignment phase one writes is the one phase
#: two looks for.
from ..pages.submit import run_home, safe_name

#: The most an uploaded FASTA may be. A sequence file is kilobytes; two megabytes is about two
#: million residues, which is four hundred times the most either tool will fold in one job, so
#: anything near it is a database dump or a mistake and reading it would only waste the login
#: node.
UPLOAD_CAP = 2 * 1024 * 1024

#: Characters a SMILES is made of. A whitelist rather than a parser, because the parser is
#: RDKit's and RDKit lives in Boltz-2's environment: importing it costs eight seconds, which is
#: too long to spend on every keystroke. So the form checks the characters and Submit checks the
#: molecule, with Boltz-2's own RDKit, before anything is queued.
SMILES_OK = re.compile(r"^[A-Za-z0-9@+\-\[\]\(\)=#$:/\\%.*]+$")

#: What a FASTA header may be before it is cleaned. Anything else in it becomes an underscore.
FASTA_HEADER = re.compile(r"^>\s*(\S+)")


def card_of(tool):
    if tool not in SERVES:
        raise Refused("this page serves %s, not %r" % (" and ".join(SERVES), tool))
    return ENTITIES[tool]


def kinds_of(tool):
    return [e.kind for e in card_of(tool)]


def entity_of(tool, kind):
    for e in card_of(tool):
        if e.kind == kind:
            return e
    return None


def fields(tool, site=None):
    """The rail's settings, as the job's own table has them."""
    return [s.as_json() for s in jobmod.settings(tool, site)]


def entities(tool):
    """The types the form may offer, as the tool's own table has them."""
    return [e.as_json() for e in card_of(tool)]


def _int(value, fallback):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


# --- the rows ----------------------------------------------------------------------------------

def clean_sequence(kind, text):
    """A sequence as the tool will see it: no whitespace, and upper case unless it is a SMILES.

    Upper case, because case is not a distinction either tool makes in a sequence and lower case
    is the single easiest way to fold a chain of nothing: Boltz-2's table has no lower-case
    letters in it and maps every one of them to UNK. A CCD code is upper case in the dictionary
    and in the cache's file names. A SMILES is left exactly as typed, because case is the
    difference between an aromatic carbon and an aliphatic one.
    """
    text = re.sub(r"\s+", "", str(text or ""))
    return text if kind == "smiles" else text.upper()


def _kind_of(kind, text):
    """The kind a row really is. Only ever different for a row that says ligand.

    Before the form had ligands by name, a ligand row was a SMILES, and a form kept from then, a
    flow or anybody posting to the page may still send one that way. So a ligand row whose text
    is not one of the ligands listed is read as what it is - a code if it looks like one, a
    SMILES otherwise - rather than refused for not being on a list it was written before.
    """
    if kind != "ligand":
        return kind
    text = re.sub(r"\s+", "", str(text or ""))
    if not text or text.upper() in {code for code, _name in entity_of("boltz2", "ligand").choices}:
        return "ligand"
    return "ccd" if CCD_CODE.match(text) else "smiles"


def _modifications(raw):
    """[{position, ccd}] as the form sent them, cleaned and in order of position.

    A position that is not a whole number is kept as it was sent, so that check_rows can say so
    in a sentence rather than the row losing a modification without a word."""
    out = []
    for mod in (raw or [])[:500]:
        if not isinstance(mod, dict):
            continue
        position = mod.get("position")
        try:
            position = int(str(position).strip())
        except (TypeError, ValueError):
            position = str(position)
        out.append({"position": position,
                    "ccd": re.sub(r"\s+", "", str(mod.get("ccd") or "")).upper()})
    return sorted(out, key=lambda m: (not isinstance(m["position"], int),
                                      m["position"] if isinstance(m["position"], int) else 0))


def rows_from(tool, body):
    """The rows as the form sent them, cleaned, with their chain ids worked out.

    Chain ids are handed out in row order, one per copy, which is the order both tools name their
    chains in: Boltz-2 is told them outright and ColabFold numbers them by position in the FASTA
    record. So the ids the page shows are the ids in the structure that comes back.
    """
    kinds = kinds_of(tool)
    out, used = [], 0
    for index, raw in enumerate(body.get("rows") or []):
        kind = str((raw or {}).get("kind") or kinds[0]).strip().lower()
        kind = _kind_of(kind, (raw or {}).get("sequence")) if tool == "boltz2" else kind
        asked = _int((raw or {}).get("copies"), 1)
        # Clamped here so that nothing below has to cope with nought or a thousand copies, and
        # reported by check_rows as it was asked for, so a number out of range is a sentence
        # rather than a quiet change to what somebody typed.
        copies = max(1, min(MAX_COPIES, asked))
        row = {"index": index, "kind": kind,
               "name": safe_name((raw or {}).get("name") or "", "") or "",
               "sequence": clean_sequence(kind, (raw or {}).get("sequence")),
               "copies": copies, "asked_copies": asked,
               # Only a chain can carry one. Dropped from anything else rather than refused: a
               # ligand has no residues, so a modification sent with one is left over from
               # something, not asked for.
               "modifications": (_modifications((raw or {}).get("modifications"))
                                 if kind in POLYMERS else [])}
        row["chains"] = list(CHAIN_IDS[used:used + copies])
        used += copies
        out.append(row)
    return out


def check_rows(tool, rows, install=""):
    """{row index: {field: what is wrong}} and the sentences worth saying about the rows.

    Every message names the row, because a form of eight rows reporting "a sequence has a letter
    that is not an amino acid" is a form nobody can put right.
    """
    bad, notes = {}, []
    kinds = kinds_of(tool)
    install = install or jobmod.INSTALLS.get(tool, "")

    def wrong(index, field, says):
        bad.setdefault(index, {})[field] = says

    for row in rows:
        index, kind, seq = row["index"], row["kind"], row["sequence"]
        if row["asked_copies"] != row["copies"]:
            wrong(index, "copies",
                  "between 1 and %d copies; %d copies of one sequence is more chains than "
                  "either tool will fold in one job" % (MAX_COPIES, row["asked_copies"]))
        entity = entity_of(tool, kind)
        if entity is None:
            wrong(index, "kind",
                  "%s cannot fold %s. It takes %s." % (tool, kind, " and ".join(kinds)))
            continue
        if not seq:
            wrong(index, "sequence", {"ligand": "pick a ligand, or take the row off",
                                      "ion": "pick an ion, or take the row off",
                                      "ccd": "give a CCD code, or take the row off",
                                      "smiles": "give a SMILES, or take the row off"}.get(
                                          kind, "give a sequence, or take the row off"))
            continue
        if entity.given == "pick":
            listed = [code for code, _name in entity.choices]
            if seq not in listed:
                wrong(index, "sequence", "%s is not one of the %ss listed. Any other component "
                                         "goes in a CCD Code row." % (seq, entity.label.lower()))
            elif jobmod.ccd_known(install, seq) is False:
                wrong(index, "sequence", _not_in_cache(seq, install))
            continue
        if kind == "ccd":
            if not CCD_CODE.match(seq):
                wrong(index, "sequence", "a CCD code is one to five capital letters and digits, "
                                         "such as ATP or HEM. A molecule written out goes in a "
                                         "SMILES row.")
            elif jobmod.ccd_known(install, seq) is False:
                wrong(index, "sequence", _not_in_cache(seq, install))
            continue
        if kind == "smiles":
            if not SMILES_OK.match(seq):
                offenders = sorted({c for c in seq if not SMILES_OK.match(c)})
                wrong(index, "sequence",
                      "that is not a SMILES: %s has no place in one"
                      % ", ".join(repr(c) for c in offenders[:6]))
            elif len(seq) > entity.max_len:
                wrong(index, "sequence", "a SMILES of %d characters is longer than anything "
                                         "Boltz-2 is meant to be given" % len(seq))
            elif CCD_CODE.match(seq) and jobmod.ccd_known(install, seq):
                notes.append("row %d is the SMILES %s, which is also a CCD code: if the "
                             "component was meant, make the row a CCD Code" % (index + 1, seq))
            continue
        allowed = set(entity.letters())
        if "-" in seq:
            # Its own message, because a gap is the one wrong character somebody has a reason to
            # have typed: it comes from an alignment. Boltz-2 does not refuse it either, and it
            # does not become UNK: it reaches the atom tables as a residue called "-" and the job
            # dies there rather than in the parser.
            wrong(index, "sequence",
                  "take the gaps out: a - is not a residue, and it stops the job rather than "
                  "being ignored. Paste the sequence, not a row of an alignment")
            continue
        strange = sorted({c for c in seq if c not in allowed})
        if strange:
            says = ("%s is not %s. %s takes %s%s."
                    % (", ".join(repr(c) for c in strange[:6]),
                       "an amino acid" if kind == "protein"
                       else ("an RNA base" if kind == "rna" else "a DNA base"),
                       entity.label, entity.alphabet,
                       " and %s for an unknown one" % entity.unknown if entity.unknown else ""))
            if kind == "rna" and "T" in strange:
                says += " A T in an RNA chain becomes N without a word said, which is why it is "\
                        "refused here."
            if kind == "dna" and "U" in strange:
                says += " A U in a DNA strand becomes an unknown base without a word said."
            wrong(index, "sequence", says)
            continue
        if len(seq) < entity.min_len:
            wrong(index, "sequence", "%d residues is shorter than anything worth folding; %d is "
                                     "the least" % (len(seq), entity.min_len))
        elif len(seq) > entity.max_len:
            wrong(index, "sequence", "%d residues, and %d is the most this page takes for a %s "
                                     "chain" % (len(seq), entity.max_len, entity.label.lower()))
        if entity.unknown:
            unknown = seq.count(entity.unknown)
            if unknown and unknown * 5 >= len(seq):
                notes.append("row %d is %d%% %s, which is a sequence the model has little to go "
                             "on with" % (index + 1, 100 * unknown // len(seq), entity.unknown))
        says = _modification_problem(row, entity, install)
        if says:
            wrong(index, "modifications", says)
    _same_sequence_problems(rows, bad)
    return bad, notes


def _not_in_cache(code, install):
    return ("Boltz-2 has no component called %s: its dictionary (%s) has no file for it, and a job "
            "asking for one stops before it folds anything. Check the code at "
            "https://www.rcsb.org/ligand/%s" % (code, jobmod.ccd_folder(install), code))


def _modification_problem(row, entity, install):
    """What is wrong with a chain's modifications, in one sentence, or "".

    The residue under each one is checked against the residue the modification is made from, as
    AlphaFold Server checks it, because Boltz-2 does not: it swaps the residue at that position
    for whatever component it is given and folds the result. A typed code that is not one of the
    listed ones is checked to exist and nothing more, since its residue is not known here.
    """
    seq, mods = row["sequence"], row.get("modifications") or []
    if not mods:
        return ""
    ours = {code: (name, parent) for code, name, parent in MODIFICATIONS.get(row["kind"], ())}
    theirs = {code: kind for kind, listed in MODIFICATIONS.items() if kind != row["kind"]
              for code, _n, _p in listed}
    seen = {}
    for mod in mods:
        position, code = mod["position"], mod["ccd"]
        if not isinstance(position, int):
            return "a modification's position is a residue number, and %r is not one" % position
        if not 1 <= position <= len(seq):
            return ("a modification at residue %d, and the %s has %d"
                    % (position, "chain" if row["kind"] == "protein" else "strand", len(seq)))
        if position in seen:
            return ("residue %d has two modifications, %s and %s, and a residue can carry one"
                    % (position, seen[position], code))
        seen[position] = code
        if not CCD_CODE.match(code):
            return ("a modification is a CCD code, one to five capital letters and digits, and "
                    "%r is not one" % code)
        here = seq[position - 1]
        if code in ours:
            name, parent = ours[code]
            if parent != "*" and here != parent:
                return ("%s (%s) is made from %s, and residue %d is %s"
                        % (code, name, _residue_name(row["kind"], parent), position,
                           _residue_name(row["kind"], here)))
        elif code in theirs and theirs[code] != row["kind"]:
            return ("%s is a modification of %s, and this row is %s"
                    % (code, {"protein": "a protein", "dna": "DNA", "rna": "RNA"}[theirs[code]],
                       {"protein": "a protein", "dna": "DNA", "rna": "RNA"}[row["kind"]]))
        if jobmod.ccd_known(install, code) is False:
            return _not_in_cache(code, install)
    return ""


def _residue_name(kind, letter):
    if kind == "protein":
        return {"A": "alanine", "R": "arginine", "N": "asparagine", "D": "aspartate",
                "C": "cysteine", "Q": "glutamine", "E": "glutamate", "G": "glycine",
                "H": "histidine", "I": "isoleucine", "L": "leucine", "K": "lysine",
                "M": "methionine", "F": "phenylalanine", "P": "proline", "S": "serine",
                "T": "threonine", "W": "tryptophan", "Y": "tyrosine",
                "V": "valine"}.get(letter, letter)
    return {"A": "an A", "C": "a C", "G": "a G", "T": "a T", "U": "a U"}.get(letter, letter)


def _same_sequence_problems(rows, bad):
    """Rows of one type and one sequence must carry the same modifications.

    Boltz-2 makes one entity of every row with the same type and sequence, whatever else the rows
    say, and takes that entity's modifications from the first of them (boltz/data/parse/schema.py,
    parse_boltz_schema: items are grouped on (type, sequence) and items[0]'s modifications are
    applied). So a second row of the same chain with a phosphoserine the first does not have
    would be folded without it, and nothing would say so.
    """
    first = {}
    for row in rows:
        if row["kind"] not in POLYMERS or not row["sequence"]:
            continue
        key = (row["kind"], row["sequence"])
        mods = [(m["position"], m["ccd"]) for m in row.get("modifications") or []]
        if key not in first:
            first[key] = (row["index"], mods)
            continue
        index, theirs = first[key]
        if mods != theirs and "modifications" not in bad.get(row["index"], {}):
            bad.setdefault(row["index"], {})["modifications"] = (
                "row %d has this sequence with %s. Boltz-2 folds rows of one sequence as copies "
                "of one entity and gives them all the first row's modifications, so this row's "
                "would be lost: give both rows the same modifications, or make them one row with "
                "two copies" % (index + 1, "different modifications" if theirs else
                                "no modifications"))


def check(tool, body, roots=(), site=None, install=""):
    """Everything wrong with the form, what the job will be told, and what Slurm is asked for."""
    card_of(tool)
    site = site or sitesmod.here()
    install = install or jobmod.INSTALLS[tool]
    rows = rows_from(tool, body)
    problems, notes = [], []
    bad, row_notes = check_rows(tool, rows, install)
    notes += row_notes

    there, says = sitesmod.installed_here(site, tool, install)
    if not there:
        problems.append(says)

    if not rows:
        problems.append("add an entity: a folding run is a list of them")
    # Counted from the copies, not from the ids handed out: CHAIN_IDS runs out, so the rows of a
    # form asking for too many chains between them come back with fewer ids than copies and a
    # count of the ids would never notice.
    chains = sum(r["copies"] for r in rows)
    if chains > len(CHAIN_IDS):
        problems.append("%d chains in all, counting copies, and %d is the most that can be given "
                        "a chain name" % (chains, len(CHAIN_IDS)))

    name = safe_name(body.get("name") or "", "") or _name_from(rows)
    tokens = jobmod.token_total(rows)
    if tokens > MAX_TOKENS:
        problems.append("%d residues in all, and %d is the most this page will queue. A complex "
                        "that size wants more than one card." % (tokens, MAX_TOKENS))

    asked = _asked(tool, body, site)
    walltime = str(asked.get("walltime") or "").strip()
    if walltime and not jobmod.WALLTIME.match(walltime):
        bad.setdefault("settings", {})["walltime"] = (
            "a walltime Slurm takes: 04:00:00, or 1-00:00:00 for a day")
    elif walltime:
        hours = _hours(walltime)
        if hours > site.max_gpu_hours(asked.get("gpu")):
            bad.setdefault("settings", {})["walltime"] = (
                "%s allows %g hours at most on %s" % (site.name,
                                                      site.max_gpu_hours(asked.get("gpu")),
                                                      site.partition_for(asked.get("gpu"))))
    # Through Site.card, so auto - which is the menu's default and not a card - is accepted and
    # anything that is neither auto nor a card this site has is refused with the list.
    if site.card(asked.get("gpu")) not in site.gpu_types():
        bad.setdefault("settings", {})["gpu"] = (
            "%s has %s, or auto to be given the largest"
            % (site.name, ", ".join(site.gpu_types())))

    out = (body.get("out") or "").strip()
    if out:
        home = os.path.realpath(os.path.expanduser("~"))
        full = os.path.realpath(os.path.abspath(os.path.expanduser(out)))
        if full == home or full.startswith(home + os.sep):
            bad.setdefault("settings", {})["out"] = (
                "home is 10 GB and 10,000 files on this cluster, and one folding run writes more "
                "than that: put the run in your scratch")
        elif roots and not any(full == os.path.realpath(r)
                               or full.startswith(os.path.realpath(r) + os.sep) for r in roots):
            notes.append("that folder is outside the ones this page reads, so the run will be "
                         "queued but will not appear on the Runs screen")
    # Where it will really go, said now rather than found later: a folder that already holds a
    # run gets this one in a folder of its own inside it (pages/submit.py, run_home).
    run_at = ""
    if out:
        run_at, moved = run_home(out, name)
        if moved:
            notes.append(moved)

    plan = jobmod.plan_of(tool, asked, rows, site)
    if plan["fits"]:
        notes.append(plan["fits"])
    if tokens > WARN_TOKENS and not plan["fits"]:
        notes.append("%d residues is a large complex: give it more walltime than the default, "
                     "and expect the memory rather than the clock to be what runs out" % tokens)
    if plan["alignments"] == "single":
        notes.append("folding from the sequence alone. Nothing is fetched, so the job can be "
                     "queued at once, and the structures will be markedly worse than with an "
                     "alignment")
    elif not jobmod.protein_sequences(rows):
        notes.append("there is no protein chain here, so there is no alignment to fetch and "
                     "nothing happens on the login node")

    clean = not problems and not any(bad.values())
    return {"problems": problems, "fields": bad, "notes": notes, "plan": plan,
            "name": name, "rows": _shown(rows),
            "told": told(tool, rows, name, asked, run_at) if rows and clean else {}}


def _name_from(rows):
    """A run name taken from the first row that has one, or the first sequence."""
    for row in rows:
        if row["name"]:
            return row["name"]
    for row in rows:
        if row["sequence"]:
            return safe_name(row["sequence"][:12], "fold")
    return "fold"


def _hours(walltime):
    """A walltime in hours, for comparing with what a partition allows."""
    days, _, rest = walltime.partition("-")
    if not rest:
        days, rest = 0, walltime
    bits = [int(b) for b in rest.split(":")] if ":" in rest else [int(rest), 0]
    while len(bits) < 3:
        bits.append(0)
    return int(days) * 24 + bits[0] + bits[1] / 60.0 + bits[2] / 3600.0


def _asked(tool, body, site):
    """The rail's settings, in the types the job's own table says they are."""
    out = {}
    for setting in jobmod.settings(tool, site):
        value = body.get(setting.key, setting.default)
        if setting.kind == "yesno":
            out[setting.key] = (bool(value) if not isinstance(value, str)
                                else value.lower() in ("1", "true", "yes", "on"))
        elif setting.kind == "number":
            text = str(value).strip()
            out[setting.key] = _int(text, setting.default) if text else setting.default
        else:
            out[setting.key] = str(value).strip()
    return out


def _shown(rows):
    """The rows as the page shows them back: the chains each one became, and its size.

    The size is residues for a chain and heavy atoms for anything else, because that is what each
    costs Boltz-2: a chain is a token a residue, and a small molecule a token an atom. A code is
    shown with it, since a record that said "ligand, 31 atoms" would not say which ligand."""
    out = []
    for r in rows:
        shown = {"index": r["index"], "kind": r["kind"], "name": r["name"],
                 "chains": r["chains"], "copies": r["copies"],
                 "residues": jobmod.tokens_of(r)}
        if r["kind"] in CHEMICAL and r["kind"] != "smiles":
            shown["ccd"] = r["sequence"]
        if r.get("modifications"):
            shown["modifications"] = ["%s%d %s" % (r["sequence"][m["position"] - 1]
                                                   if isinstance(m["position"], int)
                                                   and 0 < m["position"] <= len(r["sequence"])
                                                   else "?", m["position"], m["ccd"])
                                      if isinstance(m["position"], int) else m["ccd"]
                                      for m in r["modifications"]]
        out.append(shown)
    return out


def told(tool, rows, name, asked, out=""):
    """What the job will literally be told: the input file, and the command that reads it.

    The run folder is not chosen until Submit, so a preview names it <run folder> unless the form
    has been given one. Which chain gets which alignment is exact either way, and that is the
    part that silently wastes a booking.
    """
    run = (os.path.abspath(os.path.expanduser(out)) if out else "<run folder>")
    single = str(asked.get("alignments") or "fetch") == "single"
    if tool == "boltz2":
        msa_for = {}
        if not single:
            for seq, entity in jobmod.protein_sequences(rows).items():
                msa_for[seq] = os.path.join(run, "msa", "%s.csv" % entity)
        text = jobmod.yaml_text(name, rows, msa_for)
        what = "inputs/%s.yaml" % name
    else:
        text = jobmod.fasta_text(name, rows)
        what = "inputs/%s.fasta" % name
    line, _look, _pattern, _why = jobmod.body(
        tool, jobmod.INSTALLS[tool], run, name, asked, jobmod.RESOURCES[tool]["cpus"])
    return {"what": what, "text": text, "command": line,
            "phase_one": _phase_one_line(tool, rows, run, name, single)}


def _phase_one_line(tool, rows, run, name, single):
    """What the login node will run before the job is queued, said plainly."""
    if single or not jobmod.protein_sequences(rows):
        return ""
    if tool == "boltz2":
        n = len(jobmod.protein_sequences(rows))
        return ("Boltz-2's own MSA client, here on the login node, for %d protein sequence%s; "
                "the alignments land in %s" % (n, "" if n == 1 else "s",
                                               os.path.join(run, "msa")))
    return ("colabfold_batch --msa-only, here on the login node; the alignment lands in %s"
            % os.path.join(jobmod.msa_dir(run, tool, name), "%s.a3m" % name))


# --- pasted and uploaded sequences --------------------------------------------------------------

def read_sequences(tool, text):
    """Rows from pasted text or an uploaded file: one per FASTA record, or one bare sequence.

    The type is inferred only where the letters settle it, which means a U where there is no T,
    or a T where there is no U. A sequence of nothing but A, C and G is all three things at once,
    so it is offered as a protein with the guess marked: the page shows what it read and lets it
    be changed, and never decides quietly.
    """
    kinds = kinds_of(tool)
    records = _records(text)
    if not records:
        raise Refused("there is no sequence in that: a bare sequence, or FASTA with > headers")
    rows, said = [], []
    for header, seq in records:
        kind, sure = _infer(seq)
        if kind not in kinds:
            # ColabFold's case. Said rather than guessed around: a nucleic acid folded as a
            # protein is twenty residues of nonsense per codon, and it would not fail.
            said.append("%s looks like %s, and %s folds protein chains only. Fold it with "
                        "Boltz-2, or change the type if that reading is wrong."
                        % (header or "the sequence", kind.upper(), tool))
            continue
        rows.append({"kind": kind, "name": safe_name(header, "") or "",
                     "sequence": clean_sequence(kind, seq), "copies": 1,
                     "guessed": not sure})
        if not sure:
            said.append("%s is only A, C and G, which is a protein, DNA and RNA sequence all at "
                        "once. It has been read as a protein: change the type if it is not one."
                        % (header or "the sequence"))
    if not rows:
        raise Refused(" ".join(said) or "nothing in that could be folded here")
    return {"rows": rows, "said": said}


def _records(text):
    """[(header, sequence)] from FASTA or from a bare sequence."""
    text = str(text or "").strip()
    if not text:
        return []
    if ">" not in text:
        return [("", re.sub(r"\s+", "", text))]
    out, header, seq = [], None, []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(">"):
            if header is not None and seq:
                out.append((header, "".join(seq)))
            found = FASTA_HEADER.match(line)
            header, seq = (found.group(1) if found else ""), []
        elif line:
            seq.append(re.sub(r"\s+", "", line))
    if header is not None and seq:
        out.append((header, "".join(seq)))
    return [(h, s) for h, s in out if s]


def _infer(seq):
    """(the type, whether the letters settle it)."""
    letters = set(re.sub(r"\s+", "", str(seq or "")).upper())
    if letters <= set("ACGUN"):
        if "U" in letters:
            return "rna", True
        if "T" not in letters:
            return "protein", False          # A, C and G alone: every type at once
    if letters <= set("ACGTN") and "T" in letters:
        return "dna", True
    return "protein", True


def read_upload(tool, body):
    """Rows from a file the browser sent. The page is reached over a forwarded port and the
    cluster cannot see the machine the browser runs on, so the file arrives as its contents."""
    raw = base64.b64decode(body.get("data") or "", validate=False)
    if not raw:
        raise Refused("that file arrived empty")
    if len(raw) > UPLOAD_CAP:
        raise Refused("that file is %.1f MB, and %d MB is the most this page takes. A sequence "
                      "file is kilobytes; that one is something else."
                      % (len(raw) / 1e6, UPLOAD_CAP // 1024 // 1024))
    given = os.path.basename(str(body.get("filename") or "sequences.fasta"))
    if not given.lower().endswith((".fasta", ".fa", ".faa", ".txt", ".seq")):
        raise Refused("the sequences have to be in a .fasta, .fa, .faa or .txt file")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise Refused("that file is not text: a FASTA is plain text with > headers")
    return read_sequences(tool, text)


def read_path(tool, path):
    """Rows from a FASTA that is already on the cluster, read here rather than uploaded.

    The cap is the same two megabytes as an upload, for a different reason and said as one:
    nothing crosses the connection here, so the size is not about what the page accepts but about
    what a sequence file is. Two megabytes is about two million residues, four hundred times the
    most either tool will fold in one job, so a file near it is a database dump and reading it
    would only hold up the login node.
    """
    given = str(path or "").strip()
    if not given:
        raise Refused("give a path, or browse to the file")
    full = os.path.abspath(os.path.expanduser(given))
    if os.path.isdir(full):
        raise Refused("%s is a folder: pick the FASTA inside it" % full)
    if not os.path.isfile(full):
        raise Refused("no such file on this cluster: %s" % full)
    if not full.lower().endswith((".fasta", ".fa", ".faa", ".txt", ".seq")):
        raise Refused("the sequences have to be in a .fasta, .fa, .faa, .txt or .seq file")
    try:
        size = os.path.getsize(full)
    except OSError as why:
        raise Refused("could not read %s: %s" % (full, why))
    if size > UPLOAD_CAP:
        raise Refused("that file is %.1f MB, and %d MB is the most this page reads. Nothing was "
                      "copied anywhere: a sequence file is kilobytes, and that one is something "
                      "else." % (size / 1e6, UPLOAD_CAP // 1024 // 1024))
    try:
        with open(full, "rb") as handle:
            raw = handle.read(UPLOAD_CAP + 1)
    except OSError as why:
        raise Refused("could not read %s: %s" % (full, getattr(why, "strerror", why)))
    if not raw:
        raise Refused("%s is empty" % full)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise Refused("that file is not text: a FASTA is plain text with > headers")
    got = read_sequences(tool, text)
    # Said back, because a path read on the cluster leaves nothing on the person's own machine to
    # confirm what was taken: the flash is the only record of which file this came from.
    got["from"] = full
    return got


# --- the molecules, checked with the tool's own RDKit -------------------------------------------

def check_molecules(install, rows, minutes=2):
    """{row index: what is wrong with its SMILES}, read by Boltz-2's own RDKit.

    At Submit rather than on every keystroke: importing RDKit in that environment takes eight
    seconds, which is too long for a form and nothing at all beside an alignment fetch. It is
    worth doing at all because a SMILES RDKit cannot read is a row Boltz-2 fails on after it has
    the card, and because Boltz-2 standardises what it does read, so the molecule folded is not
    always the molecule typed.
    """
    want = {r["index"]: r["sequence"] for r in rows if r["kind"] == "smiles"}
    if not want:
        return {}
    python = os.path.join(install, "bin", "python")
    if not os.access(python, os.X_OK):
        return {}                     # said already by the install check; not said twice here
    script = ("import json,sys\n"
              "from rdkit import Chem, RDLogger\n"
              "RDLogger.DisableLog('rdApp.*')\n"
              "got = json.load(sys.stdin)\n"
              "out = {}\n"
              "for key, smiles in got.items():\n"
              "    mol = Chem.MolFromSmiles(smiles)\n"
              "    out[key] = None if mol is None else mol.GetNumHeavyAtoms()\n"
              "print(json.dumps(out))\n")
    try:
        done = subprocess.run([python, "-c", script], input=json.dumps(
            {str(k): v for k, v in want.items()}), capture_output=True, text=True,
            timeout=minutes * 60, env=dict(os.environ, HOME=os.path.join("/tmp"),
                                           PYTHONPATH="", PYTHONHOME=""))
    except (OSError, subprocess.SubprocessError):
        return {}                     # RDKit not answering is not a reason to refuse the run
    got = jobmod._last_json((done.stdout or "") + (done.stderr or ""))
    bad = {}
    for key, atoms in got.items():
        if atoms is None:
            bad[int(key)] = ("RDKit cannot read that SMILES, and Boltz-2 reads it with the same "
                             "RDKit, so the row would fail once the job had the card")
    return bad


# --- queueing -----------------------------------------------------------------------------------

def run_folder(root, name):
    """Where this submission goes: the root, the name, and the minute it was asked for."""
    stamp = time.strftime("%Y%m%d-%H%M")
    where = os.path.join(root, "%s_%s" % (name, stamp))
    n = 2
    while os.path.exists(where):
        where = os.path.join(root, "%s_%s_%d" % (name, stamp, n))
        n += 1
    return where


def queue(tool, body, roots, install="", site=None, dry_run=False, say=print):
    """Phase one and then phase two: fetch the alignments, write the job, queue it.

    Returns what was written down beside the run, which is what the Runs screen reads.
    """
    site = site or sitesmod.here()
    install = install or jobmod.INSTALLS[tool]
    checked = check(tool, body, roots, site, install)
    if checked["problems"] or any(checked["fields"].values()):
        return {"problems": checked["problems"], "fields": checked["fields"]}

    rows = rows_from(tool, body)
    molecules = check_molecules(install, rows) if not dry_run else {}
    if molecules:
        return {"problems": [], "fields": {str(k): {"sequence": v}
                                           for k, v in molecules.items()}}

    name = checked["name"]
    asked = _asked(tool, body, site)
    where = (body.get("out") or "").strip()
    where = run_home(where, name)[0] if where else run_folder(roots[0], name)
    os.makedirs(os.path.join(where, "inputs"), exist_ok=True)
    os.makedirs(os.path.join(where, "cache"), exist_ok=True)

    # The rows, exactly as they were given, beside the run. A structure that comes back a year
    # later is worth nothing without the sequences that went in, and the input file is the
    # tool's rather than the person's: it carries chain ids and alignment paths, not row names.
    with open(os.path.join(where, "entities.json"), "w", encoding="utf-8") as handle:
        json.dump({"tool": tool, "name": name, "rows": rows}, handle, indent=2)
        handle.write("\n")

    single = str(asked.get("alignments") or "fetch") == "single"
    source = os.path.join(where, "inputs", "%s.%s"
                          % (name, "yaml" if tool == "boltz2" else "fasta"))

    # ColabFold's FASTA is written before the fetch and Boltz-2's YAML after it, and the order
    # is not a preference either way.
    #
    # ColabFold is handed the FASTA in phase one and works out from it what to align, so a fetch
    # run before the file exists stops with "could not be found" after a minute of importing JAX
    # and the submission is lost. Boltz-2's YAML cannot be written that early: every protein
    # chain in it has to name the alignment file, and until the fetch has returned there is no
    # name to write. So each tool's input is written at the one point in this order where it can
    # be written correctly.
    if tool == "colabfold":
        with open(source, "w", encoding="utf-8") as handle:
            handle.write(jobmod.fasta_text(name, rows))

    msa_for = {}
    if not single and not dry_run:
        # Phase one. On the login node, because this is the only part of a folding run that can
        # reach the MSA server, and before the job script is written, because the script names
        # the files this leaves.
        say("phase one: the alignments, here on the login node")
        got = jobmod.fetch_alignments(tool, install, where, name, rows, say=say)
        msa_for = {seq: path for seq, path in got.items() if seq}
    elif not single:
        say("dry run: the alignments were not fetched, so the input says empty")

    if tool == "boltz2":
        with open(source, "w", encoding="utf-8") as handle:
            handle.write(jobmod.yaml_text(name, rows, msa_for))

    say("phase two: the job, which runs with no network at all")
    script = jobmod.script_text(tool, install, where, name, asked, site)
    was, jobmod.DRY_RUN = jobmod.DRY_RUN, bool(dry_run)
    try:
        job_id = jobmod.queue(where, script, say=say)
    finally:
        jobmod.DRY_RUN = was

    kept = {
        "tool": tool, "name": name, "user": os.environ.get("USER") or "",
        "submitted": time.strftime("%Y-%m-%d %H:%M:%S"),
        "job_id": job_id, "dry_run": bool(dry_run), "path": where,
        "site": site.key, "install": install,
        "rows": _shown(rows), "asked": asked,
        # Said as it happened, which on a dry run is that nothing was fetched. "None needed" is
        # true of a run with no protein chain in it and a lie about a dry run that has two.
        "alignments": ("single sequence" if single else
                       ("not fetched: dry run" if dry_run else
                        ("fetched" if msa_for or tool == "colabfold" else "none needed"))),
        "input": os.path.relpath(source, where),
        "results": os.path.relpath(jobmod.msa_dir(where, tool, name)
                                   if tool == "colabfold"
                                   else os.path.join(where, "out", "boltz_results_%s" % name,
                                                     "predictions", name), where),
        "plan": checked["plan"],
    }
    with open(os.path.join(where, "submission.json"), "w", encoding="utf-8") as handle:
        json.dump(kept, handle, indent=2)
        handle.write("\n")
    return {"run": kept}


# --- taking the results somewhere ---------------------------------------------------------------

#: What may be copied out of a run, and what each means. The whole folder is gigabytes once the
#: alignments are in it, and somebody who wants one model should not have to take all of that.
COPIES = ("run", "structures")


def copy_out(run, dest, what="structures", say=print):
    """Copy a finished run, or just its structures, to a folder on the cluster.

    On the cluster rather than through the browser, because the output is large and the lab's
    work stays on Grace. Refused into home for the reason that is always the reason here: home is
    10 GB and 10,000 files, and the alignments alone are more files than that.
    """
    from . import runs as runsmod
    if what not in COPIES:
        raise Refused("there is nothing called %r to copy" % what)
    dest = os.path.abspath(os.path.expanduser(str(dest or "").strip()))
    if not dest:
        raise Refused("give a folder to copy into")
    home = os.path.realpath(os.path.expanduser("~"))
    if os.path.realpath(dest) == home or os.path.realpath(dest).startswith(home + os.sep):
        raise Refused("home is 10 GB and 10,000 files on this cluster, and a folding run is more "
                      "than both: copy it into your scratch instead")
    inside = os.path.realpath(run)
    if os.path.realpath(dest) == inside or os.path.realpath(dest).startswith(inside + os.sep):
        raise Refused("that folder is inside the run, so copying it there would copy it into "
                      "itself")
    try:
        os.makedirs(dest, exist_ok=True)
    except OSError as why:
        raise Refused("that folder cannot be made: %s" % why)

    name = os.path.basename(run.rstrip(os.sep))
    if what == "run":
        target = os.path.join(dest, name)
        if os.path.exists(target):
            raise Refused("%s is there already: copy it somewhere else, or move that one aside"
                          % target)
        shutil.copytree(run, target)
        say("copied the whole run to %s" % target)
        return {"where": target, "files": sum(len(f) for _d, _s, f in os.walk(target))}

    found = runsmod.structures(run)
    if not found:
        raise Refused("this run has written no structure yet, so there is nothing to copy")
    target = os.path.join(dest, "%s_structures" % name)
    os.makedirs(target, exist_ok=True)
    for one in found:
        shutil.copy2(one["path"], os.path.join(target, os.path.basename(one["path"])))
    say("copied %d structure%s to %s" % (len(found), "" if len(found) == 1 else "s", target))
    return {"where": target, "files": len(found)}
