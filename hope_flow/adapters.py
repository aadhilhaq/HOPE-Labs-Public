"""The junctions: what one card left, read as what the next card needs.

This is the whole of what a flow adds. Every card is its tool, untouched; an adapter is the few
seconds between two of them that a person would otherwise spend copying a path, opening a table
and typing a sequence into another page.

An adapter is given the edge and the flow's record, and returns a small dictionary describing
what is being carried:

    {"kind": one of cards.KINDS, "what": a line for the log, "count": how many things,
     "dropped": [(name, why), ...], ... whatever that kind carries ...}

`count` is read by the runner: nothing carried means the track before this one produced nothing,
which ends that track quietly rather than starting a tool with an empty input. `dropped` is what
was deliberately left behind, with the reason, so a pruned branch can be told from a lost one.
"""
from __future__ import annotations

import csv
import json
import os
import re

from . import cards
from .cards import DOCKABLE_MAX_LENGTH

#: What ADCP will take, from adcp_dock/peptide.py: the standard twenty, and five residues at the
#: short end because below that every replica of the search fails. Repeated here because an
#: adapter runs on a compute node where ADCP's own package may not be importable, and refusing a
#: peptide with the reason beats letting the docking refuse it later, once per task.
STANDARD = set("ACDEFGHIKLMNPQRSTVWY")
DOCKABLE_MIN_LENGTH = 5

#: ADCP 1.1's own syntax for the residues outside the standard twenty, from the same file
#: (peptide.EXTENDED): an optional "&" for the D form, the one-letter code of the standard
#: residue whose coarse potential is used, and a non-standard side chain's library name in angle
#: brackets. "N<BHD>PIS&DVD" is seven residues, the first an asparagine derivative and the fifth
#: a D-aspartate. Read here because this file was counting characters and comparing them with the
#: twenty: every such peptide was left behind as having no rotamer, which ADCP 1.1 does have.
#:
#: The library names are not checked. ADCP checks them against the install's own side-chain
#: library, which an adapter has no access to, so an unknown name is the docking's refusal to
#: make rather than one to guess at here.
EXTENDED = re.compile(r"&?[A-Z](?:<[A-Z0-9]{1,4}>)?")

#: Where a thing with no score of its own sorts: last, and never in the middle of the ones that
#: have one. The same number the docking's own report sorts a missing score to.
NO_SCORE = 1e9


class NotWired(RuntimeError):
    """A junction that cannot be made, named so a person knows which and why."""


def carry(flow, record, edge, flow_dir):
    src = flow.node(edge.src)
    dst = flow.node(edge.dst)
    fn = BY_JUNCTION.get((src.card, edge.src_port, dst.card)) \
        or LEGACY.get((src.card, edge.src_port, dst.card))
    if fn is None:
        raise NotWired("nothing yet carries %s from %s to %s"
                       % (edge.src_port, src.card, dst.card))
    return fn(flow, record, edge, flow_dir)


def _rundir(record, nid):
    where = record.card(nid).get("rundir") or ""
    if not where or not os.path.isdir(where):
        raise NotWired("the run folder of %s is not there: %r" % (nid, where))
    return where


def _rows(path, sep="\t"):
    """A table as a list of dicts, whatever it is delimited by."""
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        if sep is None:
            sep = "\t" if sample.count("\t") >= sample.count(",") else ","
        return [dict(r) for r in csv.DictReader(fh, delimiter=sep)]


def _column(row, *names):
    """The first of these columns that this row has, however it was capitalised."""
    lower = {str(k).strip().lower(): v for k, v in row.items() if k}
    for name in names:
        if name.lower() in lower:
            return (lower[name.lower()] or "").strip()
    return ""


def _residues(seq):
    """The residues of a sequence, one string each: "A", or "&D" and "N<BHD>" in ADCP 1.1's own
    syntax. Its own reading (peptide.residues), so a length is residues and not characters."""
    return EXTENDED.findall(seq) if ("<" in seq or "&" in seq) else list(seq)


def _dockable(name, seq):
    """Why this peptide cannot be docked, or "" when it can.

    The same refusals ADCP's own check makes, in the same order, so a peptide this lets through
    is one the docking will take rather than one it will refuse a minute later. A sequence in
    ADCP 1.1's extended syntax is read as that: its residues are counted as residues, and each
    one's coarse letter is what has to be one of the twenty.
    """
    seq = (seq or "").strip().upper()
    if not seq:
        return "no sequence"
    residues = _residues(seq)
    if "<" in seq or "&" in seq:
        # Whatever the pattern does not account for is a mistake in the syntax, which is how
        # ADCP's own check reads it, and the position is given as a person counts.
        covered = set()
        for found in EXTENDED.finditer(seq):
            covered.update(range(found.start(), found.end()))
        stray = [i + 1 for i in range(len(seq)) if i not in covered]
        if stray:
            return ("the docking cannot read %s at position%s %s: a non-standard residue is a "
                    "letter and its library name, as N<BHD>, and a D residue is & before its "
                    "letter, as &D"
                    % ("/".join(sorted({seq[i - 1] for i in stray})),
                       "" if len(stray) == 1 else "s",
                       ", ".join(str(i) for i in stray[:8])))
        odd = sorted({r.lstrip("&")[0] for r in residues} - STANDARD)
    else:
        odd = sorted(set(seq) - STANDARD)
    if odd:
        return "the docking has no rotamer for %s" % ", ".join(odd)
    if len(residues) < DOCKABLE_MIN_LENGTH:
        return "%d residues, shorter than the %d the docking needs" % (len(residues),
                                                                       DOCKABLE_MIN_LENGTH)
    if len(residues) > DOCKABLE_MAX_LENGTH:
        return "%d residues, longer than the %d the docking takes" % (len(residues),
                                                                      DOCKABLE_MAX_LENGTH)
    return ""


#: What the ring named on an ADCP card is called in the peptide file's third column, and in a
#: sentence about a peptide that cannot take it (adcp_dock/peptide.py, RING_NAMES and RING_AS).
RING_NAMES = {"none": "linear", "backbone": "head to tail", "disulfide": "disulfide"}
RING_AS = {"none": "as a linear chain", "backbone": "as a ring head to tail",
           "disulfide": "as a ring through a disulfide"}


def _ring_asked(flow, edge):
    """The ring the docking card asks every peptide to be closed into: "", "backbone" or
    "disulfide". "" where the card says linear, which is what it says unless somebody changed
    it, and the file is then written in the plain named form it always was."""
    dst = flow.node(edge.dst)
    said = str((dst.settings if dst else {}).get("cyclic") or "none").strip().lower()
    return said if said in ("backbone", "disulfide") else ""


def _ring_problem(seq, ring):
    """Why this peptide cannot be closed into `ring`, or "" when it can.

    ADCP does not check this itself: given -cys and a peptide with one cysteine, ADCP 1.1.21
    docked it and reported poses with no warning, so the run described as cyclic a peptide that
    was never closed. Its own command line refuses the whole run for one such peptide; a flow
    leaves that peptide behind by name and docks the rest.
    """
    if ring != "disulfide":
        return ""
    # C, and its D form &C. A non-standard residue built on the cysteine model (C<HCS>) is not
    # counted: whether its side chain has a thiol where cysteine's is depends on the residue.
    have = sum(1 for r in _residues(seq) if r in ("C", "&C"))
    if have >= 2:
        return ""
    return ("a disulfide ring needs two cysteines and this has %s"
            % ("none" if have == 0 else "one"))


def _peptides_file(flow_dir, edge, pairs, ring=""):
    """Write ADCP's peptides file: one "name: SEQUENCE" a line, which it reads as it stands.

    A colon cannot occur in a sequence, so the named form is the one of the several ADCP accepts
    that cannot be misread. Duplicates are dropped here rather than at the docking, which refuses
    a whole run for them because it keys its folders on the sequence.

    The file is named for the link and not for the card it arrives at, because the docking takes
    several tracks of peptides at once and each track writes its own. Named for the card, the
    second track wrote over the first and both carried back the same path, so the docking was
    given one file holding one track: a design run's peptides went missing between a log saying
    how many had been carried and the run that docked the others.

    `ring` is the ring the card asks every peptide to be closed into. With one, the file is
    written as the three columns ADCP reads with a header - name, sequence, ring - so that each
    peptide carries its ring rather than inheriting it, and a peptide that cannot close the ring
    asked for is left behind by name. Without one, which is the usual case, the file is the plain
    named form it has always been.
    """
    where = os.path.join(flow_dir, "inputs",
                         "%s_from_%s_peptides.txt" % (edge.dst, edge.src))
    os.makedirs(os.path.dirname(where), exist_ok=True)
    seen, kept, dropped = {}, [], []
    for name, seq in pairs:
        seq = seq.strip().upper()
        if seq in seen:
            dropped.append((name, "the same sequence as %s" % seen[seq]))
            continue
        why = _ring_problem(seq, ring)
        if why:
            dropped.append((name, "%s, so it cannot be docked %s" % (why, RING_AS[ring])))
            continue
        seen[seq] = name
        kept.append((re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "peptide", seq))
    with open(where, "w", encoding="utf-8") as fh:
        if ring:
            fh.write("name\tsequence\tring\n")
            for name, seq in kept:
                fh.write("%s\t%s\t%s\n" % (name, seq, RING_NAMES[ring]))
        else:
            for name, seq in kept:
                fh.write("%s: %s\n" % (name, seq))
    return where, kept, dropped


# --- from the target -------------------------------------------------------------------------
# Every tool needs the receptor and the site, and they are the same for all of them, so this one
# adapter serves every card the target feeds: the design tools, and the receptor's preparation in
# front of the docking cards. `cleaned` is the copy the target card wrote for the design tools -
# heavy atoms, standard names, nothing but the polymer - which is the file they are given.

def _target_to_anything(flow, record, edge, flow_dir):
    node = flow.node(edge.src)
    s = node.settings
    mine = record.card(node.id)
    got = mine.get("path") or os.path.join(flow_dir, "inputs", "target.pdb")
    if not os.path.isfile(got):
        raise NotWired("the target structure is not in the flow's folder: %s" % got)
    cleaned = mine.get("cleaned") or ""
    return {"kind": "target", "count": 1, "path": got,
            "cleaned": cleaned if cleaned and os.path.isfile(cleaned) else "",
            "chains": (s.get("chains") or "").strip(),
            "hotspots": (s.get("hotspots") or "").strip(),
            "binder_min": s.get("binder_min"), "binder_max": s.get("binder_max"),
            "what": "the target %s, hotspots %s" % (os.path.basename(got),
                                                    s.get("hotspots") or "none")}


# --- the prepared receptor into the docking ----------------------------------------------------

def _receptor_to_dock(flow, record, edge, flow_dir):
    """The receptor the Prepare receptor card made, for a docking card: the same target, chains and
    site, now a structure prepared at the flow's pH, with the word that tells the tool not to
    prepare it again.

    The hotspots are the one thing that can go wrong between the two, and the card's own job has
    checked them against what came out (helpers/prepare_molecules.py, receptor mode): each is
    present, built - a residue of a loop that was missing from the file and has been modelled - or
    missing. A built one is said, since a site on a modelled loop is a site on a guess. A missing
    one stops a card that aims at the site, by name: docking against a site that is not in the
    structure does not fail, it docks somewhere else.
    """
    where = _rundir(record, edge.src)
    got = _prepared_record(where)
    node = flow.node(edge.src)
    dst = flow.node(edge.dst)
    one = next((x for x in got.get("structures") or [] if x.get("path")
                and os.path.isfile(x["path"])), None)
    if one is None:
        why = "; ".join("%s: %s" % tuple(d) for d in got.get("dropped") or []
                        if isinstance(d, (list, tuple)) and len(d) == 2)
        raise NotWired("the receptor could not be prepared%s" % ((": " + why) if why else ""))
    target = flow.target_of(edge.src)
    t = target.settings if target is not None else {}
    checked = one.get("hotspots") or {}
    built = [h["residue"] for h in checked.get("residues") or [] if h.get("state") == "built"]
    missing = [h for h in checked.get("residues") or [] if h.get("state") == "missing"]
    aimed = dst is not None and cards.aims_at_site(dst.kind, dst.settings)
    # Said on the receptor card itself, which is where a person looks to see what its preparation
    # decided: a hotspot on a modelled loop is a flag on that card, and a missing one is named
    # there as well as on the docking it stops.
    flags = []
    if built:
        flags.append(_on_built(built))
    if missing:
        flags.append("hotspot%s %s %s missing" % ("" if len(missing) == 1 else "s",
                                                  ", ".join(h["residue"] for h in missing),
                                                  "is" if len(missing) == 1 else "are"))
    if flags and hasattr(record, "set"):
        record.set(edge.src, note="; ".join(flags))
    if missing and aimed:
        raise NotWired("%s is aimed at the hotspots, and %s %s not in the prepared receptor: %s. "
                       "Correct the hotspots on the target card, then carry on"
                       % (dst.kind.name, ", ".join(h["residue"] for h in missing),
                          "is" if len(missing) == 1 else "are",
                          "; ".join("%s %s" % (h["residue"], h.get("why") or "is missing")
                                    for h in missing[:4])))
    settings = node.settings if node else {}
    ph = settings.get("ph")
    said = []
    if built:
        said.append(_on_built(built))
    if missing:
        said.append("hotspot%s %s %s not in it, and this card does not aim at the site"
                    % ("" if len(missing) == 1 else "s", ", ".join(h["residue"] for h in missing),
                       "is" if len(missing) == 1 else "are"))
    return {"kind": "receptor", "count": 1, "path": one["path"], "prepared": True,
            "chains": (t.get("chains") or "").strip(),
            "chains_prepared": list(one.get("receptor_chains") or []),
            "hotspots": (t.get("hotspots") or "").strip(),
            "binder_min": t.get("binder_min"), "binder_max": t.get("binder_max"),
            "ph": ph, "prep": cards.prep_from(settings), "hotspot_check": checked,
            "built": built, "dropped": [],
            "what": "the receptor prepared at pH %s, chains %s%s"
                    % (ph if ph not in (None, "") else "7.4",
                       ",".join(one.get("receptor_chains") or []) or "all",
                       ("; " + "; ".join(said)) if said else "")}


def _on_built(built):
    """The hotspots on built residues, as a flag reads: a site on a modelled loop."""
    if len(built) == 1:
        return "hotspot %s is on a built residue" % built[0]
    return "hotspots %s are on built residues" % ", ".join(built)


# --- a folded structure into the target ------------------------------------------------------

def _fold_rows(where):
    """The entities a fold was given, from entities.json beside its run.

    Written by the folding pages' own queue as the rows were sent, each with the chain ids it
    became, so this is the fold's own account of what it folded rather than a guess from the
    structure that came out. A run from before that file existed has none, and then one chain is
    all anybody can honestly say was folded.
    """
    try:
        with open(os.path.join(where or "", "entities.json"), encoding="utf-8") as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return []
    return [r for r in (got.get("rows") or []) if isinstance(r, dict)]


def _fold_split(rows):
    """(the receptor's chains, the partner's chains) of a co-fold, as the boxes were filled in.

    The first entity is the receptor and everything after it is what binds it, which is the order
    the card asks for them in. Taken from the record rather than from the sizes of the chains,
    because a strand of DNA or RNA, or a peptide, can be the longer of the two and the simulation would then
    take it for the receptor.
    """
    receptor = [c for c in (rows[0].get("chains") or []) if c] if rows else []
    partner = [c for r in rows[1:] for c in (r.get("chains") or []) if c]
    return receptor, partner


def _folded_models(where, how_many=1):
    """The structures a fold wrote, best first, at most this many.

    Which structure is the folding page's own question to answer, and it is asked rather than
    worked out again here: runs.structures reads the confidence files beside each model and hands
    them back best first, which for ColabFold is the rank in the file name and for Boltz-2 is the
    order its own predictions come in.
    """
    try:
        from hope_labs.folding import runs as runsmod
    except ImportError as why:
        raise NotWired("the folding pages are not importable from this install, so the structure "
                       "this fold wrote cannot be read: %s" % why)
    found = runsmod.structures(where)
    if not found:
        # The job can end having written nothing and still report success - both tools do, which
        # is why their own job script counts the files before it exits - so this says which run to
        # look in rather than letting the next card fail on a path that was never written.
        raise NotWired("the fold wrote no structure in %s. Its job's log says what the tool said "
                       "about it" % where)
    return found[:max(1, int(how_many or 1))], len(found)


def _fold_to_target(flow, record, edge, flow_dir):
    """The structure a folding run wrote, for the target card to take as its own.

    Which structure is the folding page's own question to answer, and it is asked rather than
    worked out again here: runs.structures reads the confidence files beside each model and hands
    them back best first, which for ColabFold is the rank in the file name and for Boltz-2 is the
    order its own predictions come in. Taking the front of that list is taking the model the tool
    says is its best, rather than the one that sorts first by name.

    Nothing is copied here. The target card's own driver copies it into the flow's inputs, exactly
    as it copies a file somebody named, so that every track below reads one file in one place
    whichever of the three ways the structure arrived.
    """
    where = _rundir(record, edge.src)
    best, many = _folded_models(where, 1)
    best = best[0]
    return {"kind": "structure", "count": 1, "path": best["path"], "dropped": [],
            "what": "%s, the best of %d model(s) %s wrote"
                    % (best.get("name") or os.path.basename(best["path"]), many,
                       best.get("kind") or "the fold")}


# --- designed peptides into the docking ------------------------------------------------------


def _bindcraft_to_adcp(flow, record, edge, flow_dir):
    """Accepted designs short enough to dock.

    The two tools barely overlap: the design tool's own floor is eight residues and its usual
    range is seventy to a hundred, while the docking takes five to thirty. A campaign asked for
    the usual range can produce nothing this junction will pass, which is why the canvas refuses
    the link before anything is queued; this says the same thing again for a flow that reached
    the cluster as a file.
    """
    where = _rundir(record, edge.src)
    # What the campaign was asked for is a prediction; what it designed is a fact, and the fact is
    # on disk by the time this runs. So the lengths are read from the designs rather than from the
    # request: a campaign asked for long binders that happened to pass a short one is not turned
    # away, and one asked for short binders that drifted long is not waved through.
    asked = _asked_lengths(where)
    if asked and asked[1] > DOCKABLE_MAX_LENGTH:
        say = "this campaign asked for binders of up to %d residues and the docking takes %d" \
              % (asked[1], DOCKABLE_MAX_LENGTH)
    else:
        say = ""
    rows, read = _bindcraft_designs(where)
    pairs, dropped = [], []
    for row in rows:
        name = _column(row, "design", "name") or "design"
        seq = _column(row, "binder_sequence", "sequence")
        why = _dockable(name, seq)
        if why:
            dropped.append((name, why))
        else:
            pairs.append((name, seq))
    path, kept, same = _peptides_file(flow_dir, edge, pairs, _ring_asked(flow, edge))
    dropped.extend(same)
    return {"kind": "sequences", "count": len(kept), "path": path, "dropped": dropped,
            "what": "%d design(s) from %s%s%s"
                    % (len(kept), read, _said(dropped), ("; " + say) if say and not kept else "")}


def _said(dropped):
    if not dropped:
        return ""
    return "; %d left behind (%s)" % (len(dropped), "; ".join("%s: %s" % d for d in dropped[:3]))


def _asked_lengths(where):
    """[shortest, longest] the campaign was asked for, known before it has designed anything."""
    for name in ("submission.json", "design.json",
                 os.path.join("results", "campaign_metadata.json"),
                 "campaign_metadata.json"):
        path = os.path.join(where, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                got = json.load(fh)
        except (ValueError, OSError):
            continue
        pair = got.get("binder_lengths") or (got.get("settings") or {}).get("binder_lengths")
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            try:
                return [int(pair[0]), int(pair[1])]
            except (TypeError, ValueError):
                pass
    return None


def _campaign(where):
    """Where the campaign's own output sits inside a run folder.

    A run queued through the lab's wrapper keeps the campaign under results/; a campaign started
    on its own writes those folders at the top. Both are met on this cluster, so both are looked
    for rather than one being declared correct.
    """
    for here in (os.path.join(where, "results"), where):
        if os.path.isdir(os.path.join(here, "3_Ranked")) or \
                os.path.isdir(os.path.join(here, "2_Refolded")):
            return here
    # Neither folder: the campaign may still have run and kept nothing. Its own account of itself
    # is what says so, and it sits in the same two places.
    for here in (os.path.join(where, "results"), where):
        if os.path.isdir(os.path.join(here, "1_Trajectories")) or \
                os.path.isfile(os.path.join(here, "summary.csv")):
            return here
    return where


def _campaign_ran(here):
    """Whether this folder holds a campaign's own account of itself, however little it kept.

    The trajectories it tried and the summary it writes at the end. A campaign reaches this
    junction only once its job has finished well - the step job waits on afterok - so a folder
    with these in it and no table of designs is a campaign that ran and accepted none, which is
    an outcome rather than a fault.
    """
    return os.path.isdir(os.path.join(here, "1_Trajectories")) or \
        os.path.isfile(os.path.join(here, "summary.csv"))


def _bindcraft_designs(where):
    """(the accepted designs' rows, which table they came from).

    The ranked table is the campaign's own answer. Before a campaign has ranked anything, the
    refolded table holds the same columns with an outcome beside each, so the accepted ones can
    be taken from it.

    A campaign that ran and kept nothing has neither, and that is not the same as a folder which
    holds no campaign at all. Two of the four campaigns on this cluster are of the first sort -
    they made their trajectories, wrote their summary, and passed not one design to refolding -
    and this refused them both, so the card was marked failed and the person was shown "the
    campaign left no table of designs" over a campaign that had finished perfectly well. Nothing
    is carried either way; the difference is whether the flow calls it a fault. It is not one, and
    the runner ends that track quietly instead.
    """
    here = _campaign(where)
    ranked = os.path.join(here, "3_Ranked", "!_Ranked.csv")
    if os.path.isfile(ranked):
        return _rows(ranked, sep=","), "the ranked designs"
    refolded = os.path.join(here, "2_Refolded", "!_Refolded.csv")
    if os.path.isfile(refolded):
        rows = [r for r in _rows(refolded, sep=",")
                if _column(r, "outcome").lower() == "accepted"]
        return rows, "the refolded designs that were accepted"
    if _campaign_ran(here):
        return [], "this campaign, which passed no design to refolding"
    raise NotWired("the campaign left no table of designs in %s" % where)


# --- structures into the simulation -----------------------------------------------------------

def _adcp_to_hopemd(flow, record, edge, flow_dir):
    """Docked poses, as the simulation's own ADCP source reads them.

    Nothing is copied or rewritten: the simulation imports an ADCP run folder as it stands. What
    this does is choose which poses, and take each peptide's name from the folder it was docked
    in rather than from the table, because the importer matches that folder name exactly and a
    re-cased name quietly matches the wrong files.

    In the docking's own order, which is the whole of what the simulation card means by "the best
    of what arrives": it takes the front of this list. Listed as the folders happened to be
    named, the best of seven peptides was whichever began with the earliest letter, and a flow
    asked for the best two simulated two that the docking had ranked fourth and seventh while the
    two it ranked first were never simulated at all - with nothing said anywhere about it.
    """
    where = _rundir(record, edge.src)
    docking = os.path.join(where, "docking")
    if not os.path.isdir(docking):
        raise NotWired("the docking run has no docking/ folder: %s" % where)
    picks, poses_each = [], _poses_each(flow, edge)
    for peptide in _docked_in_rank_order(where, docking):
        folder = os.path.join(docking, peptide)
        ranked = [f for f in os.listdir(folder) if re.match(r"^out_ranked_\d+\.pdb$", f)] \
            if os.path.isdir(folder) else []
        for n in range(1, min(poses_each, len(ranked)) + 1):
            picks.append({"peptide": peptide, "pose": n})
    return {"kind": "poses", "count": len(picks), "source_kind": "adcp", "path": where,
            "selections": picks, "dropped": [],
            "what": "%d pose(s) of %d peptide(s) in %s"
                    % (len(picks), len(set(p["peptide"] for p in picks)), os.path.basename(where))}


def _docked_in_rank_order(where, docking):
    """The peptide folders of a docking run, best first.

    results.tsv is the docking's own answer, written by the job that gathers a run: one row per
    peptide, in rank order, the sequence under `peptide`. The folders are named by the sequence
    (adcp_dock/job.py, `key`), with anything that is not a letter replaced, so a row is matched to
    its folder through the same substitution rather than by trusting either spelling.

    A run with no table yet - one gathered by an older console, or a folder copied by hand - keeps
    the order the folders are in, which is what this always did.
    """
    folders = sorted(f for f in os.listdir(docking) if os.path.isdir(os.path.join(docking, f)))
    try:
        rows = _rows(os.path.join(where, "results.tsv"))
    except OSError:
        return folders
    left, out = list(folders), []
    for row in rows:
        for said in (_column(row, "peptide", "sequence"), _column(row, "name")):
            spelt = said if said.isalpha() else re.sub(r"[^A-Za-z0-9]+", "_", said).strip("_")
            if spelt in left:
                out.append(spelt)
                left.remove(spelt)
                break
    # Whatever the table did not name goes after it, in the order it always was. A peptide the
    # gathering job could not score is still a docked peptide, and dropping it here would be this
    # junction deciding something the simulation card is the one to decide.
    return out + left


def _bindcraft_to_hopemd(flow, record, edge, flow_dir):
    """Designed complexes: the binder already placed on the target, simulated as they stand.

    Each design is its own structure file, so each becomes its own run. The one thing that must
    not be left to chance is which chain is which: the simulation splits a complex by size, and a
    binder longer than the target it was designed against would be taken for the receptor. Every
    design file states the answer itself, and this reads it from there.
    """
    where = _rundir(record, edge.src)
    rows, read = _bindcraft_designs(where)
    ranked = os.path.join(_campaign(where), "3_Ranked")
    designs, dropped = [], []
    for row in rows:
        name = _column(row, "design", "name")
        if not name:
            continue
        found = [os.path.join(ranked, f) for f in (os.listdir(ranked) if os.path.isdir(ranked) else [])
                 if f.startswith(name) and f.endswith(".cif") and not f.endswith("_monomer.cif")]
        if not found:
            dropped.append((name, "no structure in 3_Ranked"))
            continue
        chains = _bindcraft_chains(found[0])
        if not chains:
            dropped.append((name, "the structure does not say which chain is the binder"))
            continue
        designs.append({"name": name, "path": found[0],
                        "receptor_chains": chains["target"], "partner_chains": chains["binder"]})
    return {"kind": "complexes", "count": len(designs), "source_kind": "complex",
            "designs": designs, "dropped": dropped,
            "what": "%d complex(es) from %s%s" % (len(designs), read, _said(dropped))}


def _bindcraft_chains(path):
    """{"binder": [...], "target": [...]} as the design file itself states them.

    Every design carries a block naming its own chains. Reading it is the difference between a
    split that is right and one that happens to be right because the target was the larger of the
    two.
    """
    want = {"_bindcraft.binder_chains": "binder", "_bindcraft.target_chains": "target"}
    got = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                for key, which in want.items():
                    if line.startswith(key):
                        value = line[len(key):].strip().strip("'\"")
                        if value:
                            got[which] = [c for c in re.split(r"[,\s]+", value) if c]
    except OSError:
        return None
    return got if "binder" in got and "target" in got else None


#: What a design tool with no page of its own leaves for the next card. RFdiffusion and BoltzGen
#: are driven from this repository rather than by a tool that already had a run folder of its own,
#: so their drivers write this file and these two adapters are all that is needed to read it. One
#: settled file rather than a scrape of each tool's output: the driver knows what it ran, and a
#: format agreed here cannot drift the way a parsed log does.
#:
#: <rundir>/designs.json
#:   {"tool": "rfdiffusion",
#:    "designs": [{"name": "design_0", "path": "/abs/design_0.pdb",
#:                 "binder_chains": ["B"], "target_chains": ["A"],
#:                 "sequence": "MKT...", "score": 0.87}, ...]}
#:
#: `score` is whatever that tool ranks by, and the driver writes the list best first, so the
#: simulation takes the best few off the top without having to know which number it was.
DESIGNS_FILE = "designs.json"


def _designs(where):
    """[the designs a driver recorded], best first, and a line saying where they came from."""
    path = os.path.join(where or "", DESIGNS_FILE)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return [], "no %s in %s" % (DESIGNS_FILE, where or "the run")
    designs = [d for d in (got.get("designs") or []) if isinstance(d, dict)]
    return designs, "%s (%s)" % (DESIGNS_FILE, got.get("tool") or "a design run")


def _designs_to_hopemd(flow, record, edge, flow_dir):
    """Designed complexes, simulated as they stand.

    The binder is already placed on the target in all of these, so there is nothing to dock first.
    Which chain is which is taken from the record the driver wrote rather than guessed from size:
    a binder longer than its target would otherwise be taken for the receptor.
    """
    where = _rundir(record, edge.src)
    designs, read = _designs(where)
    kept, dropped = [], []
    for got in designs:
        name = str(got.get("name") or "").strip() or "design"
        path = str(got.get("path") or "")
        if not path or not os.path.isfile(path):
            dropped.append((name, "no structure on disk"))
            continue
        binder = [c for c in (got.get("binder_chains") or []) if c]
        target = [c for c in (got.get("target_chains") or []) if c]
        if not binder or not target:
            dropped.append((name, "the record does not say which chain is the binder"))
            continue
        kept.append({"name": name, "path": path,
                     "receptor_chains": target, "partner_chains": binder})
    return {"kind": "complexes", "count": len(kept), "source_kind": "complex",
            "designs": kept, "dropped": dropped,
            "what": "%d complex(es) from %s%s" % (len(kept), read, _said(dropped))}


def _designs_to_adcp(flow, record, edge, flow_dir):
    """The designed sequences, for those short enough to dock.

    A tool asked for a hundred-residue binder produces nothing this junction can pass, and the
    canvas says so as a caution while the link is drawn. A caution rather than a refusal because
    what the target card asks for is what the tool aims at, not what it produces: the answer is
    only known here, from the designs themselves.
    """
    where = _rundir(record, edge.src)
    designs, read = _designs(where)
    pairs, dropped = [], []
    for got in designs:
        name = str(got.get("name") or "").strip() or "design"
        seq = str(got.get("sequence") or "").strip().upper()
        why = _dockable(name, seq)
        if why:
            dropped.append((name, why))
        else:
            pairs.append((name, seq))
    path, kept, same = _peptides_file(flow_dir, edge, pairs, _ring_asked(flow, edge))
    dropped.extend(same)
    return {"kind": "sequences", "count": len(kept), "path": path, "dropped": dropped,
            "what": "%d design(s) from %s%s" % (len(kept), read, _said(dropped))}


def _haddock_to_hopemd(flow, record, edge, flow_dir):
    """Docked complexes, best first across every partner, as HADDOCK docking scored them: the best
    model of each of a partner's best clusters, as many clusters as the card asked for.

    Read from the summary each partner's task writes (docking/<partner>/summary.json), which is
    what the page itself shows, so the complexes handed on are the ones a person would pick from
    its table. HADDOCK keeps its models gzipped, and the simulation reads plain PDB, so each is
    unpacked into the flow's folder. The receptor is chain A and the partner chain B, as HADDOCK
    docked them.

    Ranked across the partners and not within each one. The partners' folders are read in whatever
    order they are named, which is alphabetical, and what came out was the first partner's
    complexes, then the second's: a card asked for the best five of three partners simulated five
    of the first partner, four of which the docking had scored worse than every complex of the
    other two. HADDOCK's score is lower for better, as its own page and its own cluster ranking
    read it (haddock_gui/report.py sorts ascending, with a missing score last), and that is the
    order this hands them on in.
    """
    import gzip
    where = _rundir(record, edge.src)
    node = flow.node(edge.src)
    try:
        top = max(1, int(node.settings.get("top") or 5))
    except (TypeError, ValueError):
        top = 5
    try:
        with open(os.path.join(where, "job.json"), encoding="utf-8") as fh:
            job = json.load(fh)
    except (OSError, ValueError):
        raise NotWired("the docking left no job.json in %s" % where)
    out_dir = os.path.join(flow_dir, "inputs", "%s_complexes" % edge.src)
    os.makedirs(out_dir, exist_ok=True)
    kept, dropped = [], []
    docking = os.path.join(where, "docking")
    for key in sorted(os.listdir(docking)) if os.path.isdir(docking) else []:
        part = os.path.join(docking, key)
        try:
            with open(os.path.join(part, "summary.json"), encoding="utf-8") as fh:
                summary = json.load(fh)
        except (OSError, ValueError):
            dropped.append((key, "this partner did not finish"))
            continue
        clusters = summary.get("clusters") or []
        if not clusters:
            dropped.append((key, "no clusters"))
            continue
        for cluster in clusters[:top]:
            model = os.path.join(part, cluster.get("best_model") or "")
            name = "%s_cluster%d" % (key, int(cluster.get("rank") or 0))
            if not cluster.get("best_model") or not os.path.isfile(model):
                dropped.append((name, "no model on disk"))
                continue
            path = os.path.join(out_dir, name + ".pdb")
            opener = gzip.open if model.endswith(".gz") else open
            with opener(model, "rt") as src, open(path, "w", encoding="utf-8") as dst:
                dst.write(src.read())
            kept.append({"name": name, "path": path,
                         "receptor_chains": list(summary.get("receptor_chains") or ["A"]),
                         "partner_chains": list(summary.get("partner_chains") or ["B"]),
                         "partner": key, "score": cluster.get("score")})
    if not kept and not dropped:
        raise NotWired("the docking has no partners in %s" % docking)
    kept.sort(key=lambda one: (NO_SCORE if one.get("score") is None else float(one["score"]),
                              one["name"]))
    for n, one in enumerate(kept, 1):
        one["rank"] = n
    partners = len({one.get("partner") for one in kept})
    return {"kind": "complexes", "count": len(kept), "source_kind": "complex",
            "designs": kept, "dropped": dropped,
            "what": "%d docked complex(es) from HADDOCK docking (%s), the best clusters of %d "
                    "partner(s), best score first across all of them%s"
                    % (len(kept), job.get("mode") or "", partners, _said(dropped))}


# --- into the preparation, and out of it ------------------------------------------------------
# The preparation stands on the link rather than at the end of one, so what feeds it is everything
# that leaves a structure: a fold's models, a designed or docked complex, a docking run's poses.
# Those are read by the very adapters that used to hand them to the simulation, unchanged, and the
# preparation's own adapter is what the simulation and the docking read instead.


def _poses_each(flow, edge):
    """How many poses of each peptide to offer, as the simulation card asks for them.

    Asked of the simulation card wherever it sits below this link, rather than of the card the
    link arrives at: a preparation stands between the two now, and reading the setting off that
    card - which has not got one - quietly offered one pose per peptide however many the
    simulation had been asked for.
    """
    seen, todo = set(), [edge.dst]
    while todo:
        at = todo.pop(0)
        if at in seen:
            continue
        seen.add(at)
        node = flow.node(at)
        if node is not None and node.card == "hopemd":
            try:
                return max(1, int(node.settings.get("poses_each") or 1))
            except (TypeError, ValueError):
                return 1
        todo.extend(e.dst for e in flow.out_of(at))
    return 1


def _md_wants(node):
    """How many of what arrives a simulation card will use.

    Everything arriving is ranked, and the card takes that many off the front, so that is how many
    are worth preparing: each preparation is minutes of a node, and preparing twenty structures
    for a card that will simulate five is fifteen of them spent for nothing.
    """
    try:
        return max(1, int(node.settings.get("top_n") or 10))
    except (TypeError, ValueError):
        return 10


def _how_many_below(flow, nid):
    """How many structures the cards below this preparation can use, at most.

    A docking takes one partner, a simulation takes the best few, and a preparation feeding both
    prepares enough for the hungrier of them.
    """
    want, seen, todo = 0, set(), [nid]
    while todo:
        at = todo.pop(0)
        if at in seen:
            continue
        seen.add(at)
        node = flow.node(at)
        if node is not None and node.card == "hopemd":
            want = max(want, _md_wants(node))
        elif node is not None and node.card in ("haddock",):
            want = max(want, 1)
        todo.extend(e.dst for e in flow.out_of(at))
    return want or 1


def _wanted(flow, edge):
    """How many structures this link should carry: everything, or what the cards below can use."""
    dst = flow.node(edge.dst)
    if dst is None or dst.card != "prepare":
        return None                         # not a link into a preparation: the old behaviour
    return _how_many_below(flow, edge.dst)


def _fold_to_prepare(flow, record, edge, flow_dir):
    """What a fold wrote, for the preparation to split and prepare molecule by molecule.

    More than the best one where the card below can use more: Boltz-2 can be asked for several
    samples of the same model, and comparing them is the reason to ask.
    """
    where = _rundir(record, edge.src)
    rows = _fold_rows(where)
    receptor, partner = _fold_split(rows)
    models, many = _folded_models(where, _wanted(flow, edge) or 1)
    designs = [{"name": "%s_model%d" % (flow.node(edge.src).id, m.get("rank") or n + 1),
                "path": m["path"], "receptor_chains": receptor, "partner_chains": partner,
                "rank": m.get("rank") or n + 1, "score": m.get("confidence"),
                "plddt": m.get("plddt")}
               for n, m in enumerate(models)]
    return {"kind": "complexes" if len(rows) > 1 else "structure", "count": len(designs),
            "source_kind": "complex", "designs": designs, "dropped": [],
            "what": "%d of the %d model(s) the fold wrote%s"
                    % (len(designs), many,
                       ", as a complex of %d chain(s) on %d" % (len(partner), len(receptor))
                       if partner else "")}


def _prepared_record(where):
    """What a preparation left behind: prepared.json, written by its own job."""
    path = os.path.join(where or "", "prepared.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as why:
        raise NotWired("the preparation left no record this could read in %s (%s). Its job's log "
                       "says which molecule it stopped on" % (where, str(why)[:120]))


def _prepared_structures(flow, record, edge):
    """(the prepared structures, best first, what was left behind, the card they came from)."""
    where = _rundir(record, edge.src)
    got = _prepared_record(where)
    node = flow.node(edge.src)
    out = []
    for one in got.get("structures") or []:
        if not one.get("path") or not os.path.isfile(one["path"]):
            continue
        out.append({"name": one.get("name") or "structure", "path": one["path"],
                    "receptor_chains": list(one.get("receptor_chains") or []),
                    "partner_chains": list(one.get("partner_chains") or []),
                    "score": one.get("score"), "rank": one.get("rank"),
                    "from": one.get("from", ""),
                    "charge": (one.get("charge") or {}).get("net_charge"),
                    "molecules": [{"chain": m.get("chain"), "kind": m.get("kind"),
                                   "charge": m.get("charge"),
                                   "protonation": (m.get("protonation") or {}).get("states") or {},
                                   "disulfides": m.get("disulfides") or []}
                                  for m in one.get("molecules") or []]})
    dropped = [(str(d[0]), str(d[1])) for d in got.get("dropped") or []
               if isinstance(d, (list, tuple)) and len(d) == 2]
    return out, dropped, node


def _prepared_to_hopemd(flow, record, edge, flow_dir):
    """Prepared structures, best first, each to be simulated as it stands.

    Nothing of the chemistry is decided here: every molecule was prepared on its own before this,
    at the flow's pH, and what is carried with the structures is the record of that - which is
    also what tells the simulation not to protonate the assembled complex again and undo it.
    """
    got, dropped, node = _prepared_structures(flow, record, edge)
    charges = ", ".join("%s %+d" % (one["name"], one["charge"])
                        for one in got[:3] if one.get("charge") is not None)
    return {"kind": "prepared", "count": len(got), "source_kind": "complex", "designs": got,
            "prepared": True, "prep": cards.prep_from(node.settings if node else {}),
            "ph": (node.settings if node else {}).get("ph"), "dropped": dropped,
            "what": "%d prepared structure(s), each molecule prepared on its own%s%s"
                    % (len(got), (", net charge " + charges) if charges else "", _said(dropped))}


def _prepared_to_dock(flow, record, edge, flow_dir):
    """One prepared structure, as the partner of a docking.

    One partner per link, as a docking takes them: where the preparation holds several - the
    samples of one fold - the best is the one docked, and the rest are there for the simulation."""
    got, dropped, node = _prepared_structures(flow, record, edge)
    if not got:
        return {"kind": "prepared", "count": 0, "dropped": dropped,
                "what": "the preparation prepared nothing%s" % _said(dropped)}
    dst = flow.node(edge.dst)
    pick = (dst.settings if dst else {}).get("partner_pick")
    try:
        rank = max(1, int(float(pick))) if pick not in (None, "") else 1
    except (TypeError, ValueError):
        rank = 1
    if rank > len(got):
        raise NotWired("the docking asks for prepared structure %d and the preparation handed "
                       "over %d; ask for one of 1 to %d" % (rank, len(got), len(got)))
    best = got[rank - 1]
    return {"kind": "prepared", "count": 1, "path": best["path"],
            "chains": best["receptor_chains"] + best["partner_chains"],
            "prepared": True, "dropped": dropped,
            "what": "%s, prepared molecule by molecule%s%s"
                    % (best["name"], "" if rank == 1 else " (rank %d of %d)" % (rank, len(got)),
                       _said(dropped))}


#: The docking cards, which take the target's receptor prepared rather than the target itself.
DOCKING = ("adcp", "haddock")

#: (the card it leaves, the socket, the card it arrives at) -> the adapter.
BY_JUNCTION = {
    # The design tools take the target as it is, cleaned; the docking cards take it through the
    # receptor's own preparation, below.
    ("target", "target", "bindcraft"): _target_to_anything,
    ("target", "target", "rfdiffusion"): _target_to_anything,
    ("target", "target", "rfd3"): _target_to_anything,
    ("target", "target", "boltzgen"): _target_to_anything,
    ("target", "target", "prepare_receptor"): _target_to_anything,
    ("bindcraft", "sequences", "adcp"): _bindcraft_to_adcp,
    # Every design tool this repository drives itself leaves the same record, so one pair of
    # adapters reads them all.
    ("rfdiffusion", "sequences", "adcp"): _designs_to_adcp,
    # RFdiffusion3 writes the same designs.json, so the same readers serve it and nothing
    # downstream had to learn a third format.
    ("rfd3", "sequences", "adcp"): _designs_to_adcp,
    ("boltzgen", "sequences", "adcp"): _designs_to_adcp,
    # Out of the preparation: the simulation takes every prepared structure, a docking takes the
    # best one as its partner.
    ("prepare", "prepared", "hopemd"): _prepared_to_hopemd,
    ("prepare", "prepared", "haddock"): _prepared_to_dock,
}

# Into the preparation, which is where everything that leaves a structure now goes: the same
# readers as before, because what they read has not changed - only what reads them next.
for _src, _port, _reads in (("bindcraft", "complexes", _bindcraft_to_hopemd),
                            ("rfdiffusion", "complexes", _designs_to_hopemd),
                            ("rfd3", "complexes", _designs_to_hopemd),
                            ("boltzgen", "complexes", _designs_to_hopemd),
                            ("haddock", "complexes", _haddock_to_hopemd),
                            ("adcp", "poses", _adcp_to_hopemd),
                            ("boltz2", "structure", _fold_to_prepare),
                            ("colabfold", "structure", _fold_to_prepare)):
    BY_JUNCTION[(_src, _port, "prepare")] = _reads

# The prepared receptor goes to every docking card its target feeds.
for _dock in DOCKING:
    BY_JUNCTION[("prepare_receptor", "receptor", _dock)] = _receptor_to_dock

#: The links a flow launched before the receptor's preparation existed still has: a target straight
#: into a docking card. They cannot be drawn now - the canvas puts the preparation in - and are not
#: in BY_JUNCTION, which is the table of what can be drawn. But a flow already running keeps the
#: links it was launched with (flow.with_prepare puts nothing in front of a card that has a state),
#: so its docking card is still carried the target as it always was, unprepared, and its tool
#: prepares the receptor itself as it always did. Read only by carry(), after BY_JUNCTION.
LEGACY = {("target", "target", _dock): _target_to_anything for _dock in DOCKING}

# A folded structure goes to two cards: the target, where the chains, the hotspots and the binder
# length are said, and a preparation, which is what stands between it and any docking or
# simulation. Written as a loop over the folding tools because the thing worth saying is that both
# of them hand in the same thing the same way.
for _folder in ("boltz2", "colabfold"):
    BY_JUNCTION[(_folder, "structure", "target")] = _fold_to_target
