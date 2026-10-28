"""The built-in documentation, served at /docs/ by the launcher's own page.

By Aadhil Haq

A page is an HTML fragment in hope_labs/web/docs/pages/<slug>.html: an <h1>, a lead paragraph,
then sections under <h2>. Screenshots are in hope_labs/web/docs/img/, taken of the running page
by tools/docs_screenshots.py, so they show what the page really looks like. Both live under web/
rather than beside this module because web/ is the folder every build carries into the
executable, so the docs travel with it without a line of their own in the build.
"""
import os

from . import APP, __version__
from . import COPYRIGHT as _COPYRIGHT
from . import docskit
from .hub import WEB

HERE = os.path.join(WEB, "docs")

#: The sidebar, in order: (slug, title, section).
NAV = [
    ("index", "Overview", "Start here"),
    ("getting-started", "Getting started", "Start here"),

    ("tools", "The Tools page", "Using HOPE Labs"),
    ("windows", "A tool's tab", "Using HOPE Labs"),
    ("flows", "Flows", "Using HOPE Labs"),
    ("results", "Results", "Using HOPE Labs"),
    ("signing-out", "Signing out", "Using HOPE Labs"),

    ("set-up", "Setting up on a cluster", "Installing the tools"),
    ("own-installs", "Installing by hand", "Installing the tools"),

    ("how-it-works", "How it works", "Reference"),
    ("troubleshooting", "When something goes wrong", "Reference"),
    ("citing", "How to cite", "Reference"),
]

#: The holder and the authors, kept apart: the lab holds the copyright, and the people who wrote
#: the thing are named beside it. Read out of LICENSE at the root of the repository rather than
#: written here, so the bar along the bottom of a page cannot say something the license does not.
#: adopt() may replace it with a newer launcher's line, which is why it is a name and not a call.
COPYRIGHT = _COPYRIGHT

#: Who does the work, for the bar along the bottom. Every tool the launcher starts is named with
#: whoever makes it, and so are the engines inside them: most of the tools are not the
#: lab's, and almost none of the science is. It is long, and the bar rolls it rather than cutting
#: it off, because the answer to a credit that does not fit is not to drop the credit.
CREDITS = [
    'SSH by <a href="https://www.paramiko.org/" target="_blank" rel="noopener">Paramiko</a>',
    "the page of HOPE-MD, and the launcher's own pages, by the HOPE Lab",
    'backbones by <a href="https://github.com/RosettaCommons/RFdiffusion" target="_blank"'
    ' rel="noopener">RFdiffusion</a> and sequences by ProteinMPNN, by the Institute for Protein'
    ' Design, University of Washington',
    'binders by <a href="https://github.com/HannesStark/boltzgen" target="_blank"'
    ' rel="noopener">BoltzGen</a>, by Hannes St&auml;rk and colleagues',
    "folding by AlphaFold 2 and ColabFold, and by Boltz-2",
    # NAMD's license (University of Illinois, non-commercial) makes this acknowledgment and
    # this link a condition of use, in any electronic document reporting NAMD results, so it
    # is set out rather than folded into the list of engines.
    "simulation by Amber, OpenMM, GROMACS, NAMD or Desmond",
    'NAMD was developed by the Theoretical and Computational Biophysics Group in the Beckman '
    'Institute for Advanced Science and Technology at the '
    '<a href="https://www.ks.uiuc.edu/Research/namd/" target="_blank" rel="noopener">'
    'University of Illinois at Urbana-Champaign</a>',
    "preparation by PDBFixer and pdb2pqr with PROPKA; MM-GBSA by AmberTools or Prime",
    # The viewer under Results and on a flow's cards: 3Dmol.js draws, and MSMS computes the
    # surfaces. MSMS's terms ask published work using it to cite Sanner, Olson and Spehner,
    # Biopolymers 1996, which How to cite gives in full.
    'structures shown by <a href="https://3dmol.csb.pitt.edu/" target="_blank" rel="noopener">'
    '3Dmol.js</a>, by Nicholas Rego and David Koes, with surfaces by MSMS, by Michel Sanner, '
    'Arthur Olson and Jean-Claude Spehner',
    "computing by Texas A&amp;M HPRC",
]

#: The page's own colors, from the :root block in web/style.css, under the names the kit uses.
#: The marks take the page's second color, the rust of the fourth square in the logo.
LIGHT = {"bg": "#eef3f3", "card": "#ffffff", "line": "#d7e2e2", "fg": "#0f1719",
         "dim": "#5f6f71", "accent": "#0d5c6b", "accent2": "#e3eef0", "mark": "#c2603a"}
DARK = {"bg": "#0b1113", "card": "#141d1f", "line": "#22302f", "fg": "#e6eeef",
        "dim": "#94a6a8", "accent": "#5fb3c4", "accent2": "#16292e", "mark": "#e08a63"}

#: Everything the shared layout needs to know about these docs.
SITE = docskit.Site(
    app=APP,
    tagline="design, folding and simulation, from one sign in",
    version=__version__,
    copyright=COPYRIGHT,
    nav=NAV,
    root=HERE,
    credits=CREDITS,
    # The page has no light or dark switch of its own; it follows the computer, and so do these.
    theme_key="",
    light=LIGHT,
    dark=DARK,
    shots="",
    # How to cite is a page here, opened in a tab of its own by app.js so the launcher's page
    # is not navigated away from.
    cite_href="/docs/citing.html",
)


def render(slug):
    """One page in the shared layout, or None when there is no such page."""
    return docskit.render(SITE, slug)


def respond(path):
    """(status, content type, body, location) for a request under /docs/."""
    return docskit.respond(SITE, path)


def creditbar():
    """The bar along the bottom of the launcher's page."""
    return docskit.creditbar_html(SITE, "/docs/index.html", cite_id="citebar")


def site():
    """What the bar says, as data, so an install can tell a launcher built before it.

    The bar is written here rather than in the page, so it did not travel when the page did: a
    launcher went on stating a copyright and a list of credits from the day it was built. Both
    change - a tool is added, a holder is restated - and neither is worth a download.
    """
    return {"rules": 1, "copyright": COPYRIGHT, "credits": list(CREDITS),
            "tagline": SITE.tagline, "acknowledge": ACK if "ACK" in globals() else ""}


def adopt(doc):
    """Take the bar's words from an install. Returns what changed, or "".

    Words only. Nothing here decides anything, so there is no rule to be newer than: the worst a
    bad one can do is read oddly, and anything unreadable leaves this launcher's own in place.
    """
    global COPYRIGHT, CREDITS
    try:
        if not isinstance(doc, dict):
            return ""
        said = str(doc.get("copyright") or "").strip()
        credits = [str(c) for c in (doc.get("credits") or []) if str(c).strip()]
        if not said and not credits:
            return ""
    except Exception:                                           # noqa: BLE001
        return ""
    changed = []
    if said and said != COPYRIGHT:
        COPYRIGHT = said
        SITE.copyright = said
        changed.append("the copyright")
    if credits and credits != CREDITS:
        CREDITS = credits
        SITE.credits = list(credits)
        changed.append("%d credits" % len(credits))
    if doc.get("tagline"):
        SITE.tagline = str(doc["tagline"])
    return ", ".join(changed)


def audit():
    """Everything wrong with the pages, as a list of sentences."""
    return docskit.audit(SITE)
