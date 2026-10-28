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


def _said(dropped):
    if not dropped:
        return ""
    return "; %d left behind (%s)" % (len(dropped), "; ".join("%s: %s" % d for d in dropped[:3]))


# --- structures into the simulation -----------------------------------------------------------


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


# --- into the preparation, and out of it ------------------------------------------------------
# The preparation stands on the link rather than at the end of one, so what feeds it is everything
# that leaves a structure: a fold's models, a designed or docked complex, a docking run's poses.
# Those are read by the very adapters that used to hand them to the simulation, unchanged, and the
# preparation's own adapter is what the simulation and the docking read instead.


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

    The simulation takes the best few, and a preparation feeding more than one of them prepares
    enough for the hungriest.
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
        todo.extend(e.dst for e in flow.out_of(at))
    return want or 1


def _wanted(flow, edge):
    """How many structures this link should carry: everything, or what the cards below can use."""
    dst = flow.node(edge.dst)
    if dst is None or dst.card != "prepare":
        return None                         # not a link into a preparation: the old behavior
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


def _shapes_to_prepare(flow, record, edge, flow_dir):
    """The shapes a sampled ensemble holds, one structure each, for the preparation to prepare.

    The run writes its shapes as one file of several models, which is the right thing to look at
    and the wrong thing to hand on: every molecule below a flow is prepared individually, so each
    model is written out here as a structure of its own, named by the shape it is. Most populated
    first, and as many as the card asked to hand on.

    What leaves here is a single chain rather than a complex: BioEmu samples one protein chain, so
    there is nothing for it to be a complex of. The share of the samples each shape holds goes
    with it as the score, which is what the simulation below ranks by.
    """
    where = _rundir(record, edge.src)
    try:
        from hope_labs.ensemble import runs as runsmod
    except ImportError as why:
        raise NotWired("the ensemble page is not importable from this install, so the shapes this "
                       "run sampled cannot be read: %s" % why)
    got = runsmod.analysis(where)
    found = os.path.join(where, "analysis", "ensemble.pdb")
    if not got or not os.path.isfile(found):
        raise NotWired("the run in %s has no shapes to hand on: its samples were not read. Its "
                       "job's log says what happened" % where)
    node = flow.node(edge.src)
    shapes = [one for one in ((got.get("states") or {}).get("states") or [])
              if one.get("rank") in set(got.get("shown") or [])]
    wanted = max(1, int(node.settings.get("shapes_out") or 1))
    models = _split_models(found, os.path.join(flow_dir, "inputs", node.id), node.id)
    designs = []
    for n, path in enumerate(models[:wanted]):
        shape = shapes[n] if n < len(shapes) else {}
        designs.append({"name": "%s_shape%d" % (node.id, shape.get("rank") or n + 1),
                        "path": path, "receptor_chains": [], "partner_chains": [],
                        "rank": shape.get("rank") or n + 1, "score": shape.get("share"),
                        "samples": shape.get("samples")})
    if not designs:
        raise NotWired("the shapes in %s could not be read as structures" % found)
    dropped = []
    if len(models) > len(designs):
        dropped.append("%d further shape(s) are in the run and this card hands on %d"
                       % (len(models) - len(designs), len(designs)))
    return {"kind": "structure", "count": len(designs), "source_kind": "structure",
            "designs": designs, "dropped": dropped,
            "what": "%d of the %d shape(s) this ensemble holds, most populated first"
                    % (len(designs), len(models))}


def _shapes_to_target(flow, record, edge, flow_dir):
    """The most populated shape, for the target card to take as its own.

    A fold hands the target the model it says is its best; a sampled ensemble hands it the
    arrangement the chain spends most of its time in, which is the same claim made the only way
    an ensemble can make it. The shapes behind it stay in the run, and the card's own note says
    what share this one holds, because a target that is 40 per cent of a spread is a different
    thing to design against than one that is 95.

    Nothing is copied here. The target card's own driver copies it into the flow's inputs, exactly
    as it copies a file somebody named.
    """
    got = _shapes_to_prepare(flow, record, edge, flow_dir)
    best = got["designs"][0]
    return {"kind": "structure", "count": 1, "path": best["path"], "dropped": got["dropped"],
            "what": "%s, the most populated of %d shape(s)%s"
                    % (best["name"], got["count"],
                       ", holding %.0f%% of the samples" % (100 * best["score"])
                       if best.get("score") else "")}


def _split_models(path, into, name):
    """One file per MODEL of a multi-model PDB, in order, written under `into`.

    Written out rather than handed over as one file because everything below a flow prepares one
    molecule at a time: a file of eight models is one molecule to a reader and eight structures to
    a preparation, and the preparation is the one that matters here.
    """
    os.makedirs(into, exist_ok=True)
    out, lines = [], []
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("MODEL"):
                    lines = []
                elif line.startswith("ENDMDL"):
                    if lines:
                        out.append(_write_model(into, name, len(out) + 1, lines))
                    lines = []
                elif line.startswith(("ATOM", "HETATM", "TER")):
                    lines.append(line)
    except OSError as why:
        raise NotWired("the shapes could not be read: %s" % why)
    if lines and not out:
        # A file with no MODEL records at all is one structure, which is what a run that found a
        # single shape writes.
        out.append(_write_model(into, name, 1, lines))
    return out


def _write_model(into, name, n, lines):
    where = os.path.join(into, "%s_shape%d.pdb" % (name, n))
    with open(where, "w", encoding="utf-8") as handle:
        handle.writelines(lines)
        handle.write("END\n")
    return where


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


#: The docking cards, which take the target's receptor prepared rather than the target itself.
DOCKING = ()

#: (the card it leaves, the socket, the card it arrives at) -> the adapter.
BY_JUNCTION = {
    ("target", "target", "rfdiffusion"): _target_to_anything,
    ("target", "target", "rfd3"): _target_to_anything,
    ("target", "target", "rfd4"): _target_to_anything,
    ("target", "target", "boltzgen"): _target_to_anything,
    ("target", "target", "prepare_receptor"): _target_to_anything,
    # Out of the preparation: the simulation takes every prepared structure, a docking takes the
    # best one as its partner.
    ("prepare", "prepared", "hopemd"): _prepared_to_hopemd,
}

# Into the preparation, which is where everything that leaves a structure now goes: the same
# readers as before, because what they read has not changed - only what reads them next.
for _src, _port, _reads in (
                            ("rfdiffusion", "complexes", _designs_to_hopemd),
                            ("rfd3", "complexes", _designs_to_hopemd),
                            ("rfd4", "complexes", _designs_to_hopemd),
                            ("boltzgen", "complexes", _designs_to_hopemd),
                            ("boltz2", "structure", _fold_to_prepare),
                            ("colabfold", "structure", _fold_to_prepare),
                            ("bioemu", "shapes", _shapes_to_prepare)):
    BY_JUNCTION[(_src, _port, "prepare")] = _reads


# Into the optimizer. What it wants is a complex of an antibody with its antigen, and nothing
# upstream can promise that: a design tool's complex is a binder on a target and a fold's is
# whatever chains it was given. So the same readers hand over the same files, and whether there
# is an antibody in them is settled by ANARCI at the optimizer's first stage, which either finds
# a variable domain or stops and says it did not.
for _src, _port, _reads in (
                            ("rfdiffusion", "complexes", _designs_to_hopemd),
                            ("rfd3", "complexes", _designs_to_hopemd),
                            ("rfd4", "complexes", _designs_to_hopemd),
                            ("boltzgen", "complexes", _designs_to_hopemd),
                            ("boltz2", "structure", _fold_to_prepare)):
    pass


# The prepared receptor goes to every docking card its target feeds.
for _dock in DOCKING:
    pass

#: The links a flow launched before the receptor's preparation existed still has: a target straight
#: into a docking card. They cannot be drawn now - the canvas puts the preparation in - and are not
#: in BY_JUNCTION, which is the table of what can be drawn. But a flow already running keeps the
#: links it was launched with (flow.with_prepare puts nothing in front of a card that has a state),
#: so its docking card is still carried the target as it always was, unprepared, and its tool
#: prepares the receptor itself as it always did. Read only by carry(), after BY_JUNCTION.
def _file_said(flow, record, edge):
    """The file a structure card copied into the flow, or why it is not there."""
    node = flow.node(edge.src)
    got = record.card(node.id).get("path") or ""
    if not got or not os.path.isfile(got):
        raise NotWired("the structure this card copied is not in the flow's folder: %r" % got)
    return node, got


def _file_to_target(flow, record, edge, flow_dir):
    """A structure in hand, for the target card to take as its own.

    Nothing is copied here. The target card's own driver copies whatever reaches it into the
    flow's inputs, exactly as it copies a file somebody named on it, so this hands over the path
    and lets that one rule do the copying.
    """
    _node, got = _file_said(flow, record, edge)
    return {"kind": "structure", "count": 1, "path": got, "dropped": [],
            "what": "%s, the structure this flow was given" % os.path.basename(got)}


def _file_to_prepare(flow, record, edge, flow_dir):
    """A structure in hand, for the preparation below it.

    Handed on whole, with no receptor and no partner named: which chains bind which is not
    something a file says, and guessing it here would split a structure on its chain order. An
    empty pair of chain lists is already how the rest of this module says "all of it", so the
    preparation prepares the file as one molecule - and where a receptor and a partner do have to
    be prepared apart from each other, the target card is the card that names chains.
    """
    node, got = _file_said(flow, record, edge)
    return {"kind": "structure", "count": 1, "source_kind": "file", "dropped": [],
            "designs": [{"name": node.id, "path": got, "rank": 1,
                         "receptor_chains": [], "partner_chains": []}],
            "what": "%s, prepared whole" % os.path.basename(got)}


BY_JUNCTION[("structurefile", "structure", "target")] = _file_to_target
BY_JUNCTION[("structurefile", "structure", "prepare")] = _file_to_prepare

LEGACY = {("target", "target", _dock): _target_to_anything for _dock in DOCKING}

# A folded structure goes to two cards: the target, where the chains, the hotspots and the binder
# length are said, and a preparation, which is what stands between it and any docking or
# simulation. Written as a loop over the folding tools because the thing worth saying is that both
# of them hand in the same thing the same way.
for _folder in ("boltz2", "colabfold"):
    BY_JUNCTION[(_folder, "structure", "target")] = _fold_to_target

# A sampled ensemble goes to the same two cards, and says the same two things: the shapes, for
# the preparation and the simulation below it, and the most populated of them for the target,
# which is the arrangement anything designed against this chain should be designed against.
BY_JUNCTION[("bioemu", "shapes", "target")] = _shapes_to_target
