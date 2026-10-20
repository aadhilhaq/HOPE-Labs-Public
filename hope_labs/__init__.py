"""HOPE Labs: one front for molecular design and simulation tools on a cluster.

The launcher signs in to the cluster once, then serves a page on this computer that lists every
tool there is, starts the one you pick on a login node, and opens it in a tab of its own.
The tools are untouched: each is the page it always was, reached through the same connection, so
a person approves Duo once instead of once a tool.
"""
import os
import sys

__version__ = "0.9.2"

#: What this build's server offers the page: the routes and the shapes they answer with. The page
#: files say which of these they need, and the two numbers are how a launcher decides whether it
#: can serve a newer page than the one it was built with.
#:
#: Raise PAGE_API when a route is added or an answer's shape changes. Raise PAGE_NEEDS only when
#: the page files stop working against an older server - adding a view that calls a new route does
#: that; rewording a sentence does not. Keeping them apart is what lets most changes reach people
#: who have an old launcher, instead of every change needing a new download.
#: 2: the page gained the file routes behind Files and Download, the report sheet, correcting a
#: flow that stopped, and the flow's own page. All four are routes this server answers and an
#: older one does not, so a launcher built before them keeps the page it was built with rather
#: than serving one whose buttons would answer 404.
#: 3: where HOPE Labs is on the cluster signed in to (/api/labs) and setting it up there the first
#: time (/api/labs/setup, /api/labs/use), with the Set up page and each tool's install in the
#: state. The page uses them only when the state carries them, so PAGE_NEEDS stays at 2 and an
#: older launcher still serves the current page.
#: 4: the structure viewer's surfaces, computed by MSMS on the cluster (/api/files/surface), the
#: page code that draws them (/static/msms.js), and "api" in the state, which says this number.
#: The page asks for a surface only when the state says 4 or more, and otherwise draws the
#: viewer's own as before, so PAGE_NEEDS stays at 2.
#: 5: a listing with no path opens on the person's own scratch, and says how far up the file
#: browser may go ("up" only where it may, and "top", the line shown where it stops). The page
#: offers Browse files only when the state says 5, so PAGE_NEEDS stays at 2.
PAGE_API = 5
PAGE_NEEDS = 2
APP = "HOPE Labs"
TAGLINE = "design, docking, folding and simulation, in one place"
BLURB = "Docking, design, folding and simulation on a cluster, from one sign-in."
LAB = "HOPE Lab"
AUTHORS = ("Aadhil Haq", "Samavath Mallawarachchi", "Lasan Manujitha")
PI = "Dr. Sandun Fernando"
POWERED_BY = "Powered by HOPE Lab"

CREDIT = ("A launcher for molecular design and simulation tools on a cluster; the methods "
          "each tool runs belong to their authors, and every run records what it "
          "used. %s." % POWERED_BY)

ACK = "Please acknowledge Texas A&M High Performance Research Computing (HPRC) for the computational resources."


def credit(sep=" · "):
    return CREDIT


#: The fallback for a bundle built without the LICENSE beside it. The copyright is stated in one
#: place, the licence, and read from there rather than retyped, so the bar along the bottom of a
#: page cannot say something the licence does not.
FALLBACK_COPYRIGHT = ("Copyright (c) 2026, Sandun Fernando, HOPE Lab, Texas A&M University. "
                      "Authored by Aadhil Haq, Samavath Mallawarachchi and Lasan Manujitha. "
                      "All rights reserved.")


def copyright_line():
    """Line 2 of LICENSE, which is the line carrying the copyright.

    The file sits at the root of the checkout, a folder above this one, and PyInstaller puts it
    at the root of the unpacked bundle; both are tried, as hub._web() does for the page's files.
    tests/test_docs.py compares the answer with FALLBACK_COPYRIGHT whenever the file is present,
    so the two cannot drift apart unnoticed.
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


#: The holder and the authors, kept apart: the lab holds the copyright, and the people who wrote
#: the thing are named beside it. LICENSE is the one place it is written down.
COPYRIGHT = copyright_line()
