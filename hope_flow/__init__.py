"""HOPE-flow: the lab's tools, drawn as a graph and run as one.

A person draws a flow on the launcher's canvas - a target, the designs to make from it, where
those are docked, and what is simulated - and this queues the whole of it on the cluster. Each
card is the tool it always was, started the way its own page starts it; what is new is the
junctions between them, and that nobody has to be at the keyboard when one is reached.

Nothing of this stays running while a flow does. Every card is submitted with a dependency on
the one before it, so a flow that has been launched outlives the launcher, the laptop and the
connection that queued it.
"""

import os
import sys

__version__ = "0.1.0"
APP = "HOPE-flow"
LAB = "HOPE Lab"
AUTHORS = ("Aadhil Haq",)
PI = "Dr. Sandun Fernando"

#: The fallback for a bundle built without the LICENSE beside it.
FALLBACK_COPYRIGHT = ("Copyright (c) 2026, Sandun Fernando, HOPE Lab, Texas A&M University. Authored by Aadhil Haq. All rights reserved.")


def copyright_line():
    """Line 2 of LICENSE at the root of the repository, which carries the copyright.

    The same few lines as hope_labs.copyright_line, repeated rather than imported: this package
    runs on its own on the cluster, where a card's step job imports it and nothing else, and a
    flow that cannot start because the launcher's package moved would be a poor trade for saving
    a dozen lines.
    """
    beside = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for root in (beside, getattr(sys, "_MEIPASS", "")):
        if not root:
            continue
        try:
            with open(os.path.join(root, "LICENSE"), encoding="utf-8") as fh:
                fh.readline()
                line = fh.readline().strip()
        except OSError:
            continue
        if line:
            return line
    return FALLBACK_COPYRIGHT


COPYRIGHT = copyright_line()
