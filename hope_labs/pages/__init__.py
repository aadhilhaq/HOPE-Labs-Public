"""The pages for the design tools that had none: RFdiffusion, RFdiffusion3, BoltzGen and
RFD4-Proteina.

By Aadhil Haq

Several of the lab's tools are a web page already and HOPE Labs only starts them. These were not.
They were installed, and a flow was the only way the lab ran them, which meant drawing a
graph to do one thing. The page they were missing is here, in the launcher's own repository
rather than in the tool it drives, for two reasons: the code that queues them is here
(hope_flow/drivers.py) and a page must call that code rather than a copy of it, and none of the
installs is ours to put a server inside.

One page serves them all. They are the same shape, as hope_flow/cards.py says of them: a target
in, a binder placed on it out. What differs between them is their card, so the form is drawn from
the card and the page itself knows nothing about any one tool. That is also what keeps the page
and the canvas offering the same settings with the same defaults, because there is one description
of them and both read it.

The one part that is per tool is the preview, which says what the job will be told: each tool has
its own contig grammar and its own way of naming a hotspot, so submit.preview branches on the tool
rather than between two of them.

How different those grammars are is worth one example. RFdiffusion3 and RFD4-Proteina both write a
contig of a binder, a break and the target's residues, and they disagree about the one case every
crystal structure presents: RFdiffusion3 wants a break between two stretches of a gapped chain and
RFD4-Proteina refuses a contig that has one. Neither is wrong, and nothing but a branch per tool
can say both.

    python -m hope_labs.pages.serve rfdiffusion --host 127.0.0.1 --port 8130

It serves on a login node, where the queue and the RCSB are, and prints the line the launcher
reads to know it is up (hope_labs/tools.py, URL_LINE). --dry-run writes the run folder, fetches
the target and writes the job script without queueing it, which is how the page is worked on
without asking for an A100 for twelve hours.
"""
from __future__ import annotations

#: The tools this page can serve, which is the one thing a caller has to name. Each is a card in
#: hope_flow/cards.py and a tool in hope_labs/tools.py under the same key, and the page refuses
#: anything else rather than drawing an empty form from a card that is not there.
SERVES = ("rfdiffusion", "rfd3", "boltzgen", "rfd4")

#: Settings a tool's card offers that its submitter does not read yet, so the form leaves them
#: out. The form is otherwise the card, field for field, and a control that changed nothing would
#: be worse than one that is not offered: somebody would set it, watch a twelve-hour run, and
#: conclude the setting did nothing to the science rather than nothing at all.
#:
#: Empty, and worth keeping empty. The one entry it held, rfdiffusion.mpnn_relax, was a setting
#: no driver passed on; it has been taken off the card instead, which is the right place to fix
#: it - hiding it here would have left it on the canvas doing nothing.
CARD_ONLY = {
    "rfdiffusion": (),
    "rfd3": (),
    "boltzgen": (),
    "rfd4": (),
}
