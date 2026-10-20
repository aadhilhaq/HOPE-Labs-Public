"""A flow: the graph a person drew, and the order it has to run in.

The file is flow.json in the flow's own folder. It holds what was drawn and nothing of what
happened, so a flow can be read, copied and queued again without carrying one run's history into
the next. What happened is state.json beside it, written by the jobs as they go.
"""
from __future__ import annotations

import json
import re

from . import cards

NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
ACCOUNT_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class Issue(str):
    """A problem, as the sentence every caller has always read, carrying where it is.

    A str so that nothing that reads problems changes: they join, compare, print and serialise as
    they always did. What is new is `node` and `key` - the card the problem is on and the setting
    on that card - which is what lets the canvas take a person to the box rather than leave them to
    find it from the sentence. Either may be empty: a flow without a name is not on any card, and a
    card with nothing arriving is a card and not a box.
    """

    def __new__(cls, text, node="", key=""):
        made = super().__new__(cls, text)
        made.node = node or ""
        made.key = key or ""
        return made

    def where(self):
        return {"text": str(self), "node": self.node, "key": self.key}


class Node:
    """One card as it was placed: which card, what was filled in, and where it sits."""

    def __init__(self, nid, card, settings=None, at=(0, 0)):
        self.id = nid
        self.card = card
        self.settings = dict(settings or {})
        self.at = tuple(at)                      # where the canvas drew it; the runner ignores it

    @property
    def kind(self):
        return cards.BY_KEY.get(self.card)

    def as_json(self):
        return {"id": self.id, "card": self.card, "settings": self.settings,
                "at": list(self.at)}


class Edge:
    """One link: a port on one node to a port on another."""

    def __init__(self, src, src_port, dst, dst_port):
        self.src, self.src_port = src, src_port
        self.dst, self.dst_port = dst, dst_port

    def as_json(self):
        return {"from": self.src, "fromPort": self.src_port,
                "to": self.dst, "toPort": self.dst_port}


class Flow:
    """The whole graph, with the questions the runner asks of it."""

    def __init__(self, name="", nodes=(), edges=(), runs_root=""):
        self.name = name
        self.nodes = list(nodes)
        self.edges = list(edges)
        self.runs_root = runs_root

    # ---- reading and writing ---------------------------------------------
    @classmethod
    def from_json(cls, text, repair=True, started=()):
        """The flow this document draws, with its preparations put in.

        `repair` is how the preparation cards get onto the links that need them - a structure into
        a docking card or the simulation, a target into a docking card - wherever the document
        came from: the canvas, a file written by hand, a flow saved before the card existed. One
        place rather than four, because a path that skips the preparation is a bug and the four
        would not have stayed in step. Switched off only to read a document exactly as it was
        written.

        `started` names the cards that already have a state, in a flow that is running or has run:
        nothing is put in front of one of those (see with_prepare).
        """
        raw = json.loads(text) if isinstance(text, str) else dict(text)
        # A flow written by hand can lack a key, and a bare KeyError reached the page as one word -
        # 'fromPort' - with nothing to say what was wrong or where.
        try:
            nodes = [Node(n["id"], n["card"], n.get("settings"), n.get("at", (0, 0)))
                     for n in raw.get("nodes", [])]
        except (KeyError, TypeError) as why:
            raise ValueError("a card in this flow has no %s" % why)
        try:
            edges = [Edge(e["from"], e["fromPort"], e["to"], e["toPort"])
                     for e in raw.get("edges", [])]
        except (KeyError, TypeError) as why:
            raise ValueError("a link in this flow has no %s: a link names from, fromPort, to and "
                             "toPort" % why)
        made = cls(raw.get("name", ""), nodes, edges, raw.get("runs_root", ""))
        return with_prepare(made, started) if repair else made

    def as_json(self):
        return {"version": 1, "name": self.name, "runs_root": self.runs_root,
                "nodes": [n.as_json() for n in self.nodes],
                "edges": [e.as_json() for e in self.edges]}

    def dumps(self):
        return json.dumps(self.as_json(), indent=2, sort_keys=False) + "\n"

    # ---- the graph -------------------------------------------------------
    def node(self, nid):
        return next((n for n in self.nodes if n.id == nid), None)

    def into(self, nid):
        """The edges arriving at this node."""
        return [e for e in self.edges if e.dst == nid]

    def out_of(self, nid):
        return [e for e in self.edges if e.src == nid]

    def upstream(self, nid):
        """The ids this node waits for, in the order they were drawn."""
        seen = []
        for e in self.into(nid):
            if e.src not in seen:
                seen.append(e.src)
        return seen

    def sources(self):
        """The nodes nothing arrives at: where a flow starts."""
        return [n for n in self.nodes if not self.into(n.id)]

    def order(self):
        """Every node, each after the ones it waits for.

        Raises when the graph has a cycle, naming the nodes still waiting, because a cycle drawn
        by hand is otherwise a flow that queues nothing and says nothing about why.
        """
        done, out = set(), []
        left = list(self.nodes)
        while left:
            ready = [n for n in left if all(u in done for u in self.upstream(n.id))]
            if not ready:
                raise ValueError("these cards wait on each other: "
                                 + ", ".join(sorted(n.id for n in left)))
            for n in ready:
                done.add(n.id)
                out.append(n)
            left = [n for n in left if n.id not in done]
        return out

    def target_of(self, nid):
        """The target card feeding this node, however far upstream it sits.

        Every tool needs the receptor and the site, and only the target card holds them, so each
        node follows its edges back to one. A flow with two targets is refused by check(); this
        returns the first it reaches.
        """
        seen, edge = set(), list(self.upstream(nid))
        while edge:
            nid2 = edge.pop(0)
            if nid2 in seen:
                continue
            seen.add(nid2)
            node = self.node(nid2)
            if node is not None and node.card == "target":
                return node
            edge.extend(self.upstream(nid2))
        return None

    def feeds_target(self, nid):
        """Whether this node's work reaches the target card, however far downstream it sits.

        The mirror of target_of, for the few cards that come before the target rather than after
        it. A folding card has no target of its own and should not be asked for one - what it has
        instead is a target to hand its structure to, and a fold joined to nothing is still a card
        that would never run.
        """
        seen, edge = set(), [e.dst for e in self.out_of(nid)]
        while edge:
            nid2 = edge.pop(0)
            if nid2 in seen:
                continue
            seen.add(nid2)
            node = self.node(nid2)
            if node is not None and node.card == "target":
                return True
            edge.extend(e.dst for e in self.out_of(nid2))
        return False

    def warnings(self):
        """What is worth saying about this flow, that does not stop it running.

        Kept apart from check() on purpose. A problem is a flow that cannot run; a warning is one
        that can, and that somebody should look at first. Mixing the two means either that Launch
        is shut for something survivable, or that a real refusal is read as advice.
        """
        target = next((n for n in self.nodes if n.card == "target"), None)
        settings = target.settings if target else {}
        out = _unnamed_chains(self, target)
        for e in self.edges:
            src, dst = self.node(e.src), self.node(e.dst)
            if src is None or dst is None or src.kind is None or dst.kind is None:
                continue
            said = cards.link_warning(src.card, e.src_port, dst.card, e.dst_port, settings)
            if said:
                out.append("%s to %s: %s" % (src.kind.name, dst.kind.name, said))
        return out

    # ---- what is wrong with it -------------------------------------------
    def check(self):
        """Everything wrong with this flow, as sentences. Empty means it can be queued."""
        bad = []
        # Kept aside rather than put straight in, because what follows stops at the first sign the
        # cards cannot be read and a missing name is no such sign. A flow drawn and not yet named
        # listed one thing to fix, and six more the moment a name was typed.
        named = []
        if not NAME_OK.match(self.name or ""):
            named.append(Issue("the flow needs a name of letters, digits, dot, dash or underscore",
                               key="flowname"))
        ids = [n.id for n in self.nodes]
        if len(set(ids)) != len(ids):
            bad.append("two cards share an id")
        if not self.nodes:
            bad.append("there is nothing on the canvas")

        # A flow runs against one target, and a flow with no card aimed at a site needs none at
        # all: a co-fold prepared and simulated is a whole flow, and the receptor it is aimed at
        # is the thing it folded. What makes a target card necessary is a card that cannot work
        # without the receptor and the site, and every one of those has a target socket it will
        # not start without - so that is where the flow is refused, in one sentence naming the
        # card rather than two about the canvas.
        targets = [n for n in self.nodes if n.card == "target"]
        if len(targets) > 1:
            bad.append("there is more than one target, and a flow runs against one")

        for n in self.nodes:
            if n.kind is None:
                bad.append(Issue("%s is not a card this launcher knows" % n.id, node=n.id))
            # it goes into every job script's header as written, so a space or a line break
            # would make a script the scheduler cannot read
            account = str((n.settings or {}).get("account") or "")
            if account and not ACCOUNT_OK.match(account):
                bad.append(Issue("%s: the account to charge is letters, digits, _ . and - only"
                                 % n.id, node=n.id, key="account"))
        if bad:
            return named + bad             # the rest reads the cards, so stop while they are sound
        bad = named                        # first in the list, because it is first on the screen

        settings = targets[0].settings if targets else {}
        for e in self.edges:
            src, dst = self.node(e.src), self.node(e.dst)
            if src is None or dst is None:
                bad.append("a link joins a card that is not on the canvas")
                continue
            why = cards.link_refused(src.card, e.src_port, dst.card, e.dst_port, settings)
            if why:
                bad.append(Issue("%s to %s: %s" % (src.kind.name, dst.kind.name, why),
                                 node=dst.id))

        drawn = set()
        for e in self.edges:
            at = (e.src, e.src_port, e.dst, e.dst_port)
            if at in drawn:
                bad.append("the same link is drawn twice, from %s to %s"
                           % (self.node(e.src).kind.name, self.node(e.dst).kind.name))
            drawn.add(at)

        for n in self.nodes:
            for port in n.kind.inputs:
                arriving = [e for e in self.into(n.id) if e.dst_port == port.key]
                if port.required and not arriving:
                    bad.append(Issue("%s has nothing arriving at %s" % (n.kind.name, port.label),
                                     node=n.id))
                # A port that takes one thing and is given several is not a flow anybody can run:
                # the tool would be told two receptors, or two sets of peptides, with nothing to
                # say which. The canvas refuses the second while it is drawn; this is for a flow
                # that reached the cluster as a file.
                if not port.many and len(arriving) > 1:
                    bad.append(Issue("%s takes one thing at %s, and %d arrive"
                                     % (n.kind.name, port.label, len(arriving)), node=n.id))
            # Joined to the target, one way or another: the cards after it because they are given
            # its receptor and its site; a card in front of it - a fold whose structure it takes -
            # because a card joined to nothing would never run; and a card feeding one that is
            # joined, which is a fold handing HADDOCK docking its partner while the target hands it
            # the receptor.
            if targets:
                # Joined to the target, one way or another, which is a question about the drawing
                # rather than about the direction of any one link: the cards after it take its
                # receptor and its site, a fold in front of it hands it a structure, and a fold
                # that hands a docking its partner reaches it through that docking. Asked as
                # "is there a path to the target card", because every way of writing it as a few
                # special cases has been one card short of the drawings people make - a fold
                # feeding a docking was called unjoined, and then so was one feeding the
                # preparation that feeds it.
                if n.card != "target" and not self._joined(n.id, targets[0].id):
                    bad.append(Issue("%s is not joined to the target" % n.kind.name, node=n.id))
            # No target card to be joined to, so the rule is the reason behind that one: a card
            # joined to nothing would never run. One card on its own is a flow - a fold queued
            # through the canvas is a reasonable thing to draw - so this is only said where there
            # is something it could have been joined to.
            elif len(self.nodes) > 1 and not self.into(n.id) and not self.out_of(n.id):
                bad.append(Issue("%s is joined to nothing, so nothing would reach it and nothing "
                                 "would come of it" % n.kind.name, node=n.id))
            if not n.kind.ready:
                bad.append(Issue("%s cannot be started by a flow yet%s"
                                 % (n.kind.name, (": " + n.kind.note) if n.kind.note else ""),
                                 node=n.id))
            bad.extend(_unanswered(n))
            bad.extend(_folding_problems(n))
            bad.extend(_simulation_problems(n))

        bad.extend(_prepare_problems(self))

        try:
            self.order()
        except ValueError as why:
            bad.append(str(why))

        if len(targets) == 1:
            # Only when there is exactly one. Asking these of two cards would print one set of
            # complaints per card about a flow whose fault is that it has two of them.
            bad.extend(Issue(text, node=targets[0].id, key=key) for text, key in
                       _target_problems(targets[0], bool(self.into(targets[0].id)),
                                        self.site_cards()))
        return bad

    def _joined(self, nid, to):
        """Whether there is a path of links between these two cards, in either direction."""
        seen, todo = set(), [nid]
        while todo:
            at = todo.pop(0)
            if at == to:
                return True
            if at in seen:
                continue
            seen.add(at)
            todo.extend(e.dst for e in self.out_of(at))
            todo.extend(e.src for e in self.into(at))
        return False

    def site_cards(self):
        """The cards in this flow that are aimed at a site, which is what hotspots are for.

        Asked of each card and of what was filled in on it, so a docking told to search the whole
        surface counts as blind and a docking driven by the interface does not.
        """
        return [n for n in self.nodes
                if n.kind is not None and cards.aims_at_site(n.kind, n.settings)]


def with_prepare(flow, started=()):
    """The flow with a preparation on every link that needs one, and none left with nothing to do.

    Before any docking and before any simulation, every receptor and
    every partner is prepared on its own. A predicted or docked structure is coordinates, not
    chemistry - its histidines have no state, its termini are not settled, its disulfides are
    unmeasured - and the tools below took it as if it were. So the cards that do that work are not
    cards anybody has to remember to add: they belong to the links, they are put in here, and
    `Card.fixed` is what stops the canvas offering to take them out again.

    Two of them, put in two ways. Prepare stands on the one link it was put on. Prepare receptor
    is one per target and shared: every docking card a target feeds is given the receptor that one
    card prepared, so a flow that docks with two tools against the same target prepares that
    target once, and both are docked against the same structure.

    Done as the flow is read rather than as it is drawn, so a flow written by hand, queued from a
    file, or saved before these cards existed is the same flow as one drawn today. Idempotent: a
    flow that already has its preparations is returned with the same nodes and the same ids, so
    reading and writing a flow is still the identity it has always been.

    Nothing is put in front of a card in `started`, the cards that already have a state. Those are
    a flow that is running, or has run, and its cards' step jobs are queued behind one another as
    it was launched: a card put in between two of them now would never be queued by anybody, and
    the card below it would wait for it for ever. Such a link is left as it was drawn and carried
    as it always was (adapters.LEGACY).
    """
    started = set(started or ())
    bridges = {c.key: c for c in cards.preparations()}
    if not bridges:
        return flow                 # a catalogue without the cards: nothing to put in, and no
                                    # pretending otherwise
    # First, the ones left with nothing to do. A preparation stood on a link, so when the card
    # above it or below it is taken off the canvas the preparation goes with the link rather than
    # sitting there as a card nothing reaches.
    for node in list(flow.nodes):
        if node.card in bridges and node.id not in started \
                and not (flow.into(node.id) and flow.out_of(node.id)):
            flow.nodes.remove(node)
            flow.edges = [e for e in flow.edges if e.src != node.id and e.dst != node.id]
    ph = _flow_ph(flow)
    for edge in list(flow.edges):
        src, dst = flow.node(edge.src), flow.node(edge.dst)
        if src is None or dst is None or dst.id in started:
            continue
        key = cards.prepared_between(src.card, edge.src_port, dst.card, edge.dst_port)
        prep = bridges.get(key)
        if prep is None:
            continue
        sock, gives = prep.inputs[0].key, prep.outputs[0].key
        # The receptor card of this target, if the target already has one: it is shared.
        nid = _receptor_card_of(flow, edge.src, edge.src_port, sock) \
            if key == cards.PREPARE_RECEPTOR_KEY else ""
        if not nid:
            nid = _free_id(flow, key)
            settings = {f.key: f.default for f in prep.settings if f.default is not None}
            if ph is not None:
                settings["ph"] = ph     # one flow, one pH: the first card's answer is the flow's
            flow.nodes.append(Node(nid, key, settings, at=_place_on_link(flow, src, dst)))
            flow.edges.append(Edge(edge.src, edge.src_port, nid, sock))
        flow.edges.remove(edge)
        flow.edges.append(Edge(nid, gives, edge.dst, edge.dst_port))
    return flow


def _receptor_card_of(flow, src, src_port, sock):
    """The Prepare receptor card this target's socket already feeds, or ""."""
    for edge in flow.out_of(src):
        node = flow.node(edge.dst)
        if edge.src_port == src_port and edge.dst_port == sock and node is not None \
                and node.card == cards.PREPARE_RECEPTOR_KEY:
            return node.id
    return ""


def _simulation_problems(node):
    """What the simulation card's picker can be wrong about, before anything is queued.

    One run per structure and the person picks which, so the two ways of picking are the two
    things to check: a name to pick by, and per-structure settings that can be read. A name that
    answers to nothing can only be found once the structures are there, and the run says so then;
    a setting this card does not have, or a number outside what it allows, is wrong while
    somebody is still looking at the box.
    """
    if node.card != "hopemd":
        return []
    s = node.settings
    bad = []
    if str(s.get("pick") or "rank").strip().lower() == "name" \
            and not cards.names_asked(s.get("names")):
        bad.append(Issue("%s: name the structures to simulate, or pick them by rank instead"
                         % node.kind.name, node=node.id, key="names"))
    _overrides, said = cards.per_structure(node.kind, s.get("overrides"))
    for why in said:
        bad.append(Issue("%s, per structure: %s" % (node.kind.name, why),
                         node=node.id, key="overrides"))
    bad.extend(_md_refuses(node, s))
    # And the same of every structure given its own settings: a run told to do something the
    # engine will not do is refused here rather than by the twentieth job of a batch.
    for name, mine in sorted(_overrides.items()):
        for why in _md_refuses(node, dict(s, **mine)):
            bad.append(Issue("%s, %s: %s" % (node.kind.name, name,
                                             str(why).split(": ", 1)[-1]),
                             node=node.id, key="overrides"))
    return bad


def _md_refuses(node, s):
    """What HOPE-MD would refuse this card for, asked of the card instead.

    Its own compatibility matrix, copied into cards.py and held against it by a test: the engine
    filters the force fields, the force field filters the waters, two engines fix the timestep,
    and the frame count has to come to a save interval the engine can write and MM-GBSA can use.
    Every one of these was a flow that drew clean, queued, and was refused by the tool at the far
    end of the queue - and Desmond was refused every time, so a flow could not run it at all.
    """
    bad = []
    name = node.kind.name
    engine = str(s.get("engine") or "amber").strip()
    if engine not in cards.MD_PROTEIN_FF:
        return []                          # not an engine: _unanswered has said so already
    at = lambda key: dict(node=node.id, key=key)                        # noqa: E731
    protein = cards.md_protein_ff(s)
    water = cards.md_water(s)
    if protein not in cards.MD_PROTEIN_FF[engine]:
        bad.append(Issue("%s: %s does not load %s; it loads %s"
                         % (name, engine, protein,
                            " or ".join(cards.MD_PROTEIN_FF[engine])), **at("protein")))
    elif water not in cards.MD_WATER.get(protein, ()):
        bad.append(Issue("%s: %s is not fitted for %s; it takes %s"
                         % (name, protein, water,
                            " or ".join(cards.MD_WATER.get(protein, ()))), **at("water")))
    elif engine in cards.MD_THREE_SITE_ONLY and cards.MD_WATER_SITES.get(water, 3) == 4:
        bad.append(Issue("%s: %s cannot run %s, a four-site water, from the Amber topology; use "
                         "TIP3P here, or run it on Amber, OpenMM or GROMACS"
                         % (name, engine, water), **at("water")))
    if s.get("nucleic") and engine in cards.MD_NUCLEIC_FF \
            and s["nucleic"] not in cards.MD_NUCLEIC_FF[engine]:
        bad.append(Issue("%s: %s does not take %s%s"
                         % (name, engine, s["nucleic"],
                            "; it types DNA and RNA with its own OPLS and takes no choice"
                            if not cards.MD_NUCLEIC_FF[engine] else
                            "; it takes " + " or ".join(cards.MD_NUCLEIC_FF[engine])),
                         **at("nucleic")))
    fixed = cards.MD_FIXED_TIMESTEP.get(engine)
    # An empty box is the engine's own, which on an engine that fixes its timestep is that.
    step = _number_or(s.get("timestep_fs"),
                      fixed if fixed is not None else cards.MD_TIMESTEPS[1])
    if fixed is not None and step is not None and abs(step - fixed) > 1e-9:
        bad.append(Issue("%s: %s runs its RESPA scheme, so the timestep is %g fs, not %g"
                         % (name, engine, fixed, step), **at("timestep_fs")))
    elif fixed is None and step is not None and step not in cards.MD_TIMESTEPS:
        bad.append(Issue("%s: the timestep is %s fs, or %g fs with hydrogen mass repartitioning, "
                         "and not %g" % (name, " or ".join("%g" % t
                                                           for t in cards.MD_TIMESTEPS[:-1]),
                                         cards.MD_TIMESTEPS[-1], step), **at("timestep_fs")))
    # The frames, as the interval they come to: a whole number of steps, and no longer than the
    # stride MM-GBSA takes its frames at.
    ns = _number_or(s.get("length_ns"), None)
    frames = _number_or(s.get("frames"), None)
    save = cards.save_interval_ps(ns, frames) if ns and frames else None
    if save is not None:
        use = fixed if fixed is not None else (step or cards.MD_TIMESTEPS[1])
        if abs((save * 1000) % use) > 1e-6:
            # What to type instead, rather than the arithmetic: a frame count is a round number
            # and the one above it that works is the answer somebody wants.
            bad.append(Issue("%s: %g frames of %g ns is one every %g ps, which is not a whole "
                             "number of %g fs steps. %s"
                             % (name, frames, ns, save, use, _frames_instead(ns, use, frames)),
                             **at("frames")))
        elif save > cards.MD_MMGBSA_STRIDE_PS and s.get("mmgbsa", True):
            bad.append(Issue("%s: %g frames of %g ns is one every %g ps, and MM-GBSA takes a "
                             "frame every %g ps, so it would have none to score. Keep %d frames "
                             "or more, or switch MM-GBSA off"
                             % (name, frames, ns, save, cards.MD_MMGBSA_STRIDE_PS,
                                int(ns * 1000.0 / cards.MD_MMGBSA_STRIDE_PS)), **at("frames")))
    return bad


def _frames_instead(ns, step_fs, asked):
    """The nearest frame count that works, as a sentence saying what to type instead.

    A round interval first: a frame every 200 ps is an answer somebody can read, and a frame
    every 167.224 ps is arithmetic that happens to pass. Only if there is none nearby does this
    offer one of those.
    """
    for whole in (True, False):
        for away in range(0, 400):
            for frames in (asked - away, asked + away):
                if frames < 1:
                    continue
                save = cards.save_interval_ps(ns, frames)
                if save is None or save > cards.MD_MMGBSA_STRIDE_PS:
                    continue
                if whole and abs(save - round(save)) > 1e-9:
                    continue
                if abs((save * 1000) % step_fs) < 1e-6:
                    return "Keep %d frames instead, which is one every %g ps" % (frames, save)
    return "Keep a number that divides %g ns into whole steps" % ns


def _number_or(value, fallback):
    if value in (None, ""):
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _prepare_problems(flow):
    """What the preparations in a flow can be wrong about.

    Four things, and none is a box somebody filled in wrongly. One pH for the flow, because two
    halves of one complex titrated at two pHs is not a system anybody meant, and a docking tool's
    own pH box is held to it for the same reason: the receptor it is given was prepared at the
    flow's. A link that carries a structure into a docking card or the simulation, or a target into
    a docking card, with no preparation on it, which cannot be drawn and cannot be read in
    (with_prepare puts one there) but can be written by hand - and which is the one failure these
    cards exist to prevent. And one prepared receptor per target, which is what lets every docking
    of one target be docked against the same structure.
    """
    bad = []
    said = {}
    for node in flow.nodes:
        if not _prepares(node):
            continue
        ph = node.settings.get("ph")
        if ph in (None, ""):
            continue
        said[node.id] = ph
    values = sorted({_ph_text(v) for v in said.values()})
    if len(values) > 1:
        first = next(n.id for n in flow.nodes if n.id in said)
        for nid, ph in said.items():
            if nid == first or _ph_text(ph) == _ph_text(said[first]):
                continue
            bad.append(Issue("the preparations in this flow are at different pHs (%s): one flow "
                             "has one pH, because the molecules of one complex are titrated "
                             "together" % ", ".join(values), node=nid, key="ph"))
    ph = _flow_ph(flow)
    if ph is not None:
        for node in flow.nodes:
            if _prepares(node) or node.kind is None:
                continue
            mine = node.settings.get("ph")
            if mine in (None, "") or not any(f.key == "ph" for f in node.kind.settings):
                continue
            if _ph_text(mine) != _ph_text(ph):
                bad.append(Issue("%s: its pH is %s and this flow's is %s, which is what its "
                                 "receptor is prepared at. One flow has one pH: set it to %s, or "
                                 "change it on the preparation"
                                 % (node.kind.name, _ph_text(mine), _ph_text(ph), _ph_text(ph)),
                                 node=node.id, key="ph"))
    for edge in flow.edges:
        src, dst = flow.node(edge.src), flow.node(edge.dst)
        if src is None or dst is None:
            continue
        key = cards.prepared_between(src.card, edge.src_port, dst.card, edge.dst_port)
        if key == cards.PREPARE_RECEPTOR_KEY:
            bad.append(Issue("%s goes straight into %s, with nothing preparing its receptor: a "
                             "receptor is prepared once, at the flow's pH, before anything is "
                             "docked against it. Open this flow on the canvas, which puts the "
                             "preparation in" % (src.kind.name, dst.kind.name), node=dst.id))
        elif key:
            bad.append(Issue("%s goes straight into %s, with nothing preparing it: every "
                             "structure is split into its molecules and prepared before it is "
                             "docked or simulated. Open this flow on the canvas, which puts the "
                             "preparation in" % (src.kind.name, dst.kind.name), node=dst.id))
    fed = {}
    for edge in flow.edges:
        dst = flow.node(edge.dst)
        if dst is not None and dst.card == cards.PREPARE_RECEPTOR_KEY:
            fed.setdefault(edge.src, []).append(dst.id)
    for src, ids in fed.items():
        if len(ids) > 1:
            name = flow.node(src).kind.name if flow.node(src) and flow.node(src).kind else src
            bad.append(Issue("%s is prepared by %d Prepare receptor cards: one target has one "
                             "prepared receptor, which every docking of it shares. Take the "
                             "links into all but one off, and link its docking cards from that one"
                             % (name, len(ids)), node=ids[1]))
    return bad


def _prepares(node):
    """Whether this node is one of the cards that prepare."""
    return node.kind is not None and node.kind.fixed and node.card in \
        {c.key for c in cards.preparations()}


def _ph_text(value):
    """A pH as it is compared and said: 7.4 whether it was typed as 7.4, "7.4" or 7.40."""
    try:
        return "%g" % float(value)
    except (TypeError, ValueError):
        return str(value)


def _flow_ph(flow):
    """The pH the flow is already prepared at, or None where nothing says.

    Either preparation's: the receptor and the complexes of one flow are protonated at one pH. A
    preparation put in beside one that is already there takes its pH, so that adding a second
    track to a flow does not quietly titrate half of it at 7.4 and the other half at 6.5.
    """
    for node in flow.nodes:
        if _prepares(node) and node.settings.get("ph") not in (None, ""):
            return node.settings["ph"]
    return None


def _free_id(flow, key):
    """An id of this card's own name that nothing on the canvas is using, as the canvas makes one."""
    if flow.node(key) is None:
        return key
    n = 2
    while flow.node("%s-%d" % (key, n)) is not None:
        n += 1
    return "%s-%d" % (key, n)


#: The canvas's own measures: a card is 214 px wide (hope_labs/web/style.css, .node), the palette
#: lays cards out 252 px apart across and 176 down (hope_labs/web/flow.js, addCard), and the
#: tallest card face - two sockets in, one out - is about 150 px. A card put in by itself is placed
#: with these, so that it lands where a person would have put it rather than over anything.
CARD_W, PITCH_X, PITCH_Y, CARD_H = 214, 252, 176, 150


def _place_on_link(flow, src, dst):
    """Where a card put on a link is drawn: in the space between the two cards it joins.

    Midway, where there is room for a card there. Where there is not - two cards side by side, as
    the palette lays them out - the card goes one place along from the card above it, and the card
    below it moves along by as much, with everything after it, so that nothing is drawn over
    anything: drawn midway, the preparation between a fold and the simulation covered the
    simulation's socket and all of its name but "MD". And if another card already stands on the
    spot, it goes a row up or down, wherever is clear.
    """
    try:
        sx, sy = int(src.at[0]), int(src.at[1])
        dx, dy = int(dst.at[0]), int(dst.at[1])
    except (TypeError, IndexError, ValueError):
        return (0, 0)
    # The gap between the two cards' edges, which a card fits in with the palette's own spacing
    # around it when it is a whole pitch wide.
    room = dx - (sx + CARD_W)
    if room >= PITCH_X:
        x = sx + CARD_W + (room - CARD_W) // 2
    else:
        x = sx + PITCH_X
        _move_along(flow, dst, x + PITCH_X - dx, keep={src.id})
    y = max(0, (sy + dy) // 2)
    for step in (0, -1, 1, -2, 2, 3, 4, 5, 6):
        here = y + step * PITCH_Y // 2
        if here >= 0 and not _overlaps(flow, x, here):
            return (x, here)
    return (x, y)


def _move_along(flow, first, by, keep=()):
    """Move a card `by` pixels to the right, with every card after it and every card that moving
    them would put one of them on top of; the rest of the drawing stays where it was put."""
    if by <= 0:
        return
    moved, todo = set(), [first.id]
    while todo:
        nid = todo.pop(0)
        node = flow.node(nid)
        if nid in moved or nid in keep or node is None:
            continue
        try:
            node.at = (int(node.at[0]) + by, int(node.at[1]))
        except (TypeError, IndexError, ValueError):
            continue
        moved.add(nid)
        todo.extend(e.dst for e in flow.out_of(nid))
        todo.extend(other.id for other in flow.nodes
                    if other.id not in moved and other.id != nid
                    and _over(other, node.at[0], node.at[1]))


def _over(node, x, y):
    """Whether a card drawn at (x, y) would lie over this one."""
    try:
        nx, ny = int(node.at[0]), int(node.at[1])
    except (TypeError, IndexError, ValueError):
        return False
    return abs(nx - x) < CARD_W + 8 and abs(ny - y) < CARD_H


def _overlaps(flow, x, y):
    """Whether a card drawn at (x, y) would lie over a card already there."""
    return any(_over(node, x, y) for node in flow.nodes)


def _unanswered(node):
    """The settings this card will not start without, that nobody has filled in.

    A default is not an answer for these: they are the few where a wrong value costs a day of a
    cluster and the right one is nobody's to guess. The canvas shows the default in the box, so
    all a person has to do is agree with it - but they have to agree with it.
    """
    bad = []
    for field in node.kind.settings:
        if not field.needed:
            continue
        value = node.settings.get(field.key)
        at = dict(node=node.id, key=field.key)
        if value in (None, ""):
            bad.append(Issue("%s needs %s" % (node.kind.name, field.label.lower()), **at))
            continue
        if field.kind == "number":
            try:
                # Only where the field has no bounds of its own to quote. With them, the next
                # check says the same thing and says what the number may be instead, and the
                # list read as two separate mistakes with two Fix buttons onto the one box.
                if float(value) <= 0 and field.least is None:
                    bad.append(Issue("%s: %s should be more than nothing"
                                     % (node.kind.name, field.label.lower()), **at))
            except (TypeError, ValueError):
                bad.append(Issue("%s: %s should be a number"
                                 % (node.kind.name, field.label.lower()), **at))
        elif field.choices and str(value) not in field.choices:
            bad.append(Issue("%s: %r is not one of the choices for %s"
                             % (node.kind.name, value, field.label.lower()), **at))
    bad.extend(_out_of_bounds(node))
    return bad


def _effective(node, field):
    """A setting's bounds once its varies rules have been applied, as the canvas applies them."""
    least, most = field.least, field.most
    for on, cases in field.varies or ():
        said = node.settings.get(on)
        # By the value itself for the cards this launcher was built with, and by the catalogue's
        # own spelling of it for cards read back from the cluster, where every key is a string.
        got = cases.get(said)
        if got is None:
            got = cases.get(cards.case_key(said))
        if got:
            least = got.get("least", least)
            most = got.get("most", most)
    return least, most


def _out_of_bounds(node):
    """Numbers the card says what they may be, given something else.

    The canvas's arrows stay inside the bounds, but a number can be typed, pasted, or carried in
    a flow saved before the bounds existed - and the first anyone heard of 1e9 replicas would
    otherwise be the job. Empty is left alone: on these fields it means the tool's own.
    """
    bad = []
    for field in node.kind.settings:
        if field.kind == "duration":
            bad.extend(_duration_problems(node, field))
            continue
        if field.kind != "number":
            continue
        value = node.settings.get(field.key)
        if value in (None, ""):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            if not field.needed:
                bad.append(Issue("%s: %s should be a number"
                                 % (node.kind.name, field.label.lower()),
                                 node=node.id, key=field.key))
            continue
        # A field with no default is one where empty means the tool's own, and before it was
        # empty that was spelt 0 - every flow saved until 6 October says 0 there, and the drivers
        # have always skipped it. It is the same answer, so it is not a number out of range.
        if field.default is None and number == 0:
            continue
        least, most = _effective(node, field)
        if least is not None and number < least:
            bad.append(Issue("%s: %s is %g, and the least it can be is %g"
                             % (node.kind.name, field.label.lower(), number, least),
                             node=node.id, key=field.key))
        elif most is not None and number > most:
            bad.append(Issue("%s: %s is %g, and the most it can be is %g"
                             % (node.kind.name, field.label.lower(), number, most),
                             node=node.id, key=field.key))
    return bad


#: A walltime as Slurm takes it, and as the card's arrows write it.
WALLTIME = re.compile(r"^(?:(\d+)-)?(\d+):([0-5]\d):([0-5]\d)$")


def walltime_seconds(text):
    """A walltime in seconds, or None where it is not one."""
    found = WALLTIME.match(str(text or "").strip())
    if not found:
        return None
    days, hours, minutes, seconds = (int(x or 0) for x in found.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def as_walltime(seconds):
    """Seconds as hh:mm:ss, or d-hh:mm:ss past a day. The canvas writes them the same way."""
    seconds = int(seconds)
    clock = "%02d:%02d:%02d" % (seconds % 86400 // 3600, seconds % 3600 // 60, seconds % 60)
    return ("%d-%s" % (seconds // 86400, clock)) if seconds >= 86400 else clock


def _duration_problems(node, field):
    """A walltime typed into the box rather than stepped to with the arrows.

    The arrows only ever write one Slurm takes, and the bounds are on the card; the box itself is
    free text and nothing read it. So "4 hours" and nine hundred of them both drew clean, passed
    Launch, and were refused by the queue - which is the one place a flow should not be finding
    out it is wrong. Empty is left alone: on these it means the tool's own allowance.
    """
    value = node.settings.get(field.key)
    if value in (None, ""):
        return []
    at = dict(node=node.id, key=field.key)
    seconds = walltime_seconds(value)
    if seconds is None:
        return [Issue("%s: %s should be a time, written as hh:mm:ss, or d-hh:mm:ss past a day"
                      % (node.kind.name, field.label.lower()), **at)]
    if field.least is not None and seconds < field.least:
        return [Issue("%s: %s is %s, and the least it can be is %s"
                      % (node.kind.name, field.label.lower(), as_walltime(seconds),
                         as_walltime(field.least)), **at)]
    if field.most is not None and seconds > field.most:
        return [Issue("%s: %s is %s, and the most it can be is %s"
                      % (node.kind.name, field.label.lower(), as_walltime(seconds),
                         as_walltime(field.most)), **at)]
    return []


def _unnamed_chains(flow, target):
    """Said when a fold hands in more chains than the target names.

    Every tool below the target takes its chains as given and falls back to the first one when the
    box is empty, so a homo-oligomer folded as A and B with the chains box left alone is designed
    against A alone. That is a reasonable thing to ask for and a poor thing to get by accident,
    and a flow that does it runs perfectly well - so it is said here rather than refused.
    """
    if target is None or (target.settings.get("chains") or "").strip():
        return []
    many = 0
    for edge in flow.into(target.id):
        node = flow.node(edge.src)
        if node is None or not cards.folds_a_structure(node.kind):
            continue
        for row in cards.fold_entities(node.kind, node.settings):
            try:
                many += max(1, int(row["copies"] or 1))
            except (TypeError, ValueError):
                many += 1
    if many < 2:
        return []
    return ["the fold makes %d chains and the target names none, so the design and the docking "
            "will aim at the first of them, A. Name the chains on the target if more than one is "
            "meant." % many]


def _target_problems(node, arriving, site_cards=()):
    """What is missing or impossible on the target card itself, as (sentence, setting) pairs.

    `arriving` is whether a folding card is handing it a structure. Three ways to get one and the
    card has to name exactly one of them: a flow set to fetch a PDB id with a fold wired into it
    would fetch the id and quietly throw the fold away, and a flow set to take a folded structure
    with nothing folding one would wait for a file that was never coming.

    `site_cards` are the cards in the flow that are aimed at a site, and they are the whole of
    why the hotspots are ever asked for.
    """
    s = node.settings
    bad = []
    source = (s.get("source") or "rcsb").strip()
    if source not in ("rcsb", "file", "folded"):
        bad.append(("the target's source should be rcsb, file or folded", 'source'))
    elif source == "folded" and not arriving:
        bad.append(("the target is set to take a folded structure and nothing is folding one for "
                   "it: draw a fold into it, or give it a PDB id or a file instead", 'source'))
    elif source != "folded" and arriving:
        bad.append(("a structure is being folded for the target and the target is set to %s "
                   "instead, so the fold would be thrown away. Set where the structure comes from "
                   "to the folded one, or take the link off"
                   % ("fetch a PDB id" if source == "rcsb" else "read a file"), 'source'))
    elif source == "rcsb":
        if not re.match(r"^[0-9A-Za-z]{4}$", (s.get("pdb_id") or "").strip()):
            bad.append(("the target needs a four-character PDB id", 'pdb_id'))
    elif source == "file":
        if not (s.get("path") or "").strip():
            bad.append(("the target needs a structure file", 'path'))

    # The site, whichever of the three the structure came from, and only where something in the
    # flow aims at one. Hotspots have no default and will not acquire one: a design tool reads an
    # empty list as "anywhere on the surface", which is a legitimate campaign and a far larger
    # one, and reaching it by leaving a box alone is how a week of a cluster goes missing. Where
    # nothing aims at a site the question does not arise, and the card that would have been
    # refused for it - a blind docking, a co-fold, a simulation - runs as drawn.
    if site_cards and not (s.get("hotspots") or "").strip():
        names = sorted({n.kind.name for n in site_cards})
        bad.append(("the target needs hotspots: the residues a binder should touch. %s %s aimed "
                    "at a site" % (", ".join(names), "is" if len(names) == 1 else "are"),
                    'hotspots'))
    lo, hi = s.get("binder_min"), s.get("binder_max")
    try:
        lo, hi = int(lo), int(hi)
        if lo < 4 or hi < lo:
            bad.append(("the binder length should be a range, shortest first, of at least 4 residues", 'binder_min'))
    except (TypeError, ValueError):
        bad.append(("the binder length should be two numbers", 'binder_min'))
    return bad


def _folding_problems(node):
    """The sequence on a card that folds one, read the way the tool will read it.

    Read here, and not left to the tool, because neither tool refuses a letter it does not know:
    both map it to their own unknown residue and fold the result, so a lower-case sequence comes
    back as a chain of nothing and a T in an RNA chain comes back as a confident structure of
    something else. The folding page makes the same check with that tool's own tables when the
    flow is launched; this one is so the canvas can refuse it while somebody is still typing, on a
    laptop where those tables are not installed.
    """
    if not cards.folds_a_structure(node.kind):
        return []
    bad, chains = [], 0
    for row in cards.fold_entities(node.kind, node.settings):
        bad.extend(_fold_row_problems(node, row))
        try:
            chains += max(1, int(row["copies"] or 1))
        except (TypeError, ValueError):
            chains += 1
    # Every copy of every chain is a letter out of one alphabet, and there is no twenty-seventh.
    if chains > cards.FOLD_MAX_CHAINS:
        bad.append(Issue("%s: %d chains in all, and %d is the most one fold can name - a "
                         "structure's chains are lettered A to Z"
                         % (node.kind.name, chains, cards.FOLD_MAX_CHAINS),
                         node=node.id, key="copies"))
    return bad


def _fold_row_problems(node, row):
    """One chain of a folding card, read the way the tool will read it."""
    name = node.kind.name
    if row["index"]:
        # Named, because a co-fold has three boxes per chain and "a letter that has no place in an
        # RNA chain" over two of them is a card nobody can put right.
        name = "%s's %s chain" % (name, ("second", "third")[row["index"] - 1])
    key = lambda base: base + row["suffix"]                             # noqa: E731
    kind = row["kind"]
    if kind not in cards.FOLD_LETTERS:
        return [Issue("%s cannot fold %r: it takes %s"
                      % (name, kind, ", ".join(cards.FOLD_LABELS[k] for k in cards.FOLD_LETTERS)),
                      node=node.id, key=key("entity"))]
    letters, unknown = cards.FOLD_LETTERS[kind]
    bad = []
    s = {"sequence": row["sequence"], "copies": row["copies"]}
    # Upper-cased before it is read, which is what the folding page does to it
    # (submit.clean_sequence) and therefore what the tool will see. Case is not a distinction
    # either tool makes, so a sequence pasted out of a database in lower case is a sequence, not a
    # mistake - and refusing it here while the page quietly folded it correctly would be the
    # canvas and the page disagreeing about the same sequence.
    seq = re.sub(r"\s+", "", str(s.get("sequence") or "")).upper()
    # An empty box is _unanswered's to complain about, and it already has: the sequence is one of
    # the few settings a flow will not start without. Saying it twice would be two complaints
    # about one empty box.
    if seq:
        odd = sorted(set(seq) - set(letters + unknown))
        if "-" in odd:
            # Its own sentence, because a gap is the one wrong character somebody has a reason to
            # have typed - it comes out of an alignment - and the one that stops the job outright
            # rather than being folded as something else.
            bad.append("%s: take the gaps out. A - is not a residue, and it stops the job rather "
                       "than being ignored: paste the sequence, not a row of an alignment" % name)
            odd = [c for c in odd if c != "-"]
        if odd:
            says = ("%s: %s %s no place in %s, which is made of %s%s"
                    % (name, ", ".join(repr(c) for c in odd[:6]),
                       "has" if len(odd) == 1 else "have", cards.FOLD_LABELS[kind], letters,
                       (" and %s for an unknown one" % unknown) if unknown else ""))
            # The two worth naming, because each is a sequence the tool would fold rather than
            # refuse, and each comes back looking like a structure of what was asked for.
            if kind == "rna" and "T" in odd:
                says += ". A T in an RNA chain becomes N without a word said"
            elif kind == "dna" and "U" in odd:
                says += ". A U in a DNA strand becomes an unknown base without a word said"
            bad.append(says)
        elif len(seq) < cards.FOLD_SHORTEST:
            bad.append("%s: %d residue%s is shorter than anything worth folding; %d is the least"
                       % (name, len(seq), "" if len(seq) == 1 else "s", cards.FOLD_SHORTEST))
        elif len(seq) > cards.FOLD_LONGEST[kind]:
            bad.append("%s: %d residues, and %d is the most %s can be here"
                       % (name, len(seq), cards.FOLD_LONGEST[kind], cards.FOLD_LABELS[kind]))
    try:
        copies = int(s.get("copies") or 1)
    except (TypeError, ValueError):
        bad.append(Issue("%s: copies should be a whole number" % name,
                         node=node.id, key=key("copies")))
        copies = 1
    if copies < 1 or copies > cards.FOLD_MAX_COPIES:
        bad.append(Issue("%s: between 1 and %d copies. Each copy is another chain, and there "
                         "are only so many chain names to hand out" % (name, cards.FOLD_MAX_COPIES),
                         node=node.id, key=key("copies")))
    # Everything above that is not already placed is about the sequence, the one box it can be.
    return [b if isinstance(b, Issue) else Issue(b, node=node.id, key=key("sequence"))
            for b in bad]
