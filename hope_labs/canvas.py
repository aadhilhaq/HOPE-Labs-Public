"""What the canvas asks the launcher for: the cards, the checking, and the flow to start from.

The canvas draws; it decides nothing. Which links are legal and what is wrong with a flow are
answered here, out of hope_flow's cards.py and flow.py - the very files the runner reads on the
cluster, not copies of them. So a flow that draws clean on the laptop is a flow the cluster agrees
with, and a refusal reads the same in both places because there is only one wording of it.

The refusal table is the part worth explaining. A link is refused while it is being dragged, and
one round trip per candidate socket would make the drag crawl, so the whole table of what may
reach what is worked out here and handed over with the check: forty-odd sentences, recomputed
whenever anything on the canvas changes. The binder length lives in the target's settings and the
length refusal depends on it, which is why the table is a function of the flow rather than a
constant served once.
"""
from __future__ import annotations

from hope_flow import cards, flow

#: What the Launch button gets until the part of HOPE-flow that queues a flow is installed. It is
#: a sentence rather than a code because it reaches the person as it stands.
RUNNER_MISSING = "the runner is not installed yet"


def catalogue():
    """Every card, their sockets and their settings, for the palette and the panel."""
    return cards.catalogue()


def target_settings(doc):
    """What the target card was filled in with, which is what the length refusal is measured by.

    A flow with no target, or with more than one, is refused by check() in its own words; this
    takes the first so that the sockets still say something useful while one is being drawn.
    """
    for node in (doc or {}).get("nodes") or []:
        if isinstance(node, dict) and node.get("card") == "target":
            got = node.get("settings")
            return dict(got) if isinstance(got, dict) else {}
    return {}


def refusals(settings=None):
    """{"card:port>card:port": why it cannot be drawn, or ""} for every pair of sockets."""
    table = {}
    for out_card in cards.CARDS:
        for out_port in out_card.outputs:
            for in_card in cards.CARDS:
                for in_port in in_card.inputs:
                    table["%s:%s>%s:%s" % (out_card.key, out_port.key, in_card.key, in_port.key)] = \
                        cards.link_refused(out_card.key, out_port.key, in_card.key, in_port.key,
                                           settings)
    return table


def check(doc, done=()):
    """What is wrong with this flow, what is worth saying about it, and what may be linked to what.

    Problems and warnings are kept apart, and so is what the page does with them: a problem shuts
    Launch, a warning does not.

    `done` is the cards that have already run, in a flow being corrected: nothing is put in front
    of one of those, since a card that has run keeps the links it ran with (run.edit).
    """
    doc = doc or {}
    made = flow.Flow.from_json(doc, started=[str(d) for d in (done or ()) if d])
    found = made.check()
    # `problems` stays the sentences, as every reader has had them; `issues` is the same list
    # with where each one is, which is what lets the page take a person to the box.
    issues = [p.where() if isinstance(p, flow.Issue) else {"text": str(p), "node": "", "key": ""}
              for p in found]
    out = {"problems": [str(p) for p in found], "issues": issues,
            "warnings": made.warnings(), "refusals": refusals(target_settings(doc))}
    out.update(mended(doc, made))
    return out


def mended(doc, made):
    """What reading this flow put right, for the canvas to draw: {"flow": ..., "added": [...]}.

    Reading a flow is what puts a preparation on every link that carries a structure into the
    simulation (hope_flow.flow.with_prepare), and the canvas has to be shown
    the result rather than left drawing a link that the cluster will read differently. Nothing
    comes back where nothing changed, which is every check after the first.
    """
    drawn = made.as_json()
    sent = doc.get("nodes") or []
    ids = {n.get("id") for n in sent if isinstance(n, dict)}
    now = {n["id"] for n in drawn["nodes"]}
    added = [n["id"] for n in drawn["nodes"] if n["id"] not in ids]
    links = {(e.get("from"), e.get("fromPort"), e.get("to"), e.get("toPort"))
             for e in doc.get("edges") or [] if isinstance(e, dict)}
    mine = {(e["from"], e["fromPort"], e["to"], e["toPort"]) for e in drawn["edges"]}
    if added or (ids - now) or links != mine:
        return {"flow": drawn, "added": added}
    return {}


def example():
    """The flow to start from: a complex folded from two sequences, prepared, then simulated.

    It is a whole flow rather than a shape to fill in - two real sequences - because a person
    starting from an example is finding out what the canvas does, and a flow that will not launch
    until six boxes are filled teaches them nothing. The two are MDM2 and the p53 peptide that
    binds it, the textbook protein-peptide complex, and both sequences are public ones from the
    PDB: fold them together and the peptide lands in the groove.

    There is no target card, and the flow needs none. A target card holds the receptor and the
    site for the cards that aim at one, and nothing here aims at a site: the receptor this flow is
    about is the thing it folded. Flow.check() says so itself.

    The settings a flow will not start without are filled in with modest numbers rather than
    ambitious ones, since this is the flow somebody will press Launch on first.
    """
    # One column between the fold and the simulation, left empty for the preparation: the card the
    # canvas puts on that link goes into the space left for it.
    doc = flow.Flow(name="example", nodes=[
        flow.Node("fold", "boltz2", {
            "sequence": "ETLVRPKPLLLKLLKSVGAQKDTYTMKEVLFYLGQYIMTKRLYDEKQQHIVYCSNDLL"
                        "GDLFGVPSFSVKEHRKIYTMIYRNLVV",
            "entity": "protein",
            "sequence_2": "ETFSDLWKLLPEN", "entity_2": "protein",
            "alignments": "fetch", "diffusion_samples": 1}, at=(16, 160)),
        # Modest on purpose: an example a person presses Launch on should not be the one that
        # commits a week of a card. One structure arrives, so one is simulated, once. The numbers
        # are theirs to raise once they have read the plan.
        flow.Node("hopemd", "hopemd", {"top_n": 1, "engine": "amber", "length_ns": 20,
                                       "replicates": 1}, at=(772, 160)),
    ], edges=[
        flow.Edge("fold", "structure", "hopemd", "structures"),
    ])
    # The link into the simulation carries a structure, so it gets its preparation, exactly as it
    # would if somebody had drawn the link themselves. Written here rather than placed by hand
    # above, so the example is the flow as the canvas means it.
    return flow.with_prepare(doc).as_json()


def launch(flows, doc, runs=""):
    """Queue this flow on the cluster, through the connection the launcher already holds.

    What is checked here is checked again by the runner before anything is queued: this page is
    not the authority on whether a flow can run, and a flow can reach the cluster as a file
    without ever passing through here.
    """
    bad = flow.Flow.from_json(doc or {}).check()
    if bad:
        raise RuntimeError("this flow cannot be queued yet: " + "; ".join(bad))
    return flows.launch(doc, runs)


def plan(flows, doc, runs=""):
    """What this flow would do, worked out on the cluster, before anything is queued."""
    return flows.plan(doc, runs)
