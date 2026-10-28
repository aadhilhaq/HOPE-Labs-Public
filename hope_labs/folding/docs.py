"""The documentation each folding page serves at /docs/, and the bar along its bottom.

By Aadhil Haq

The layout, the stylesheet and the bar come from hope_labs/docskit.py, which every one of the
lab's interfaces shares. The colors and the lab's copyright come from the design pages'
documentation (hope_labs/pages/docs.py), imported rather than restated: there is one palette and
one statement, and four pages that read as kin.

The two tools get two sets of pages. They are the same shape to run and not the same thing to
use: one folds protein, DNA, RNA and a ligand together from a YAML, the other folds protein
chains from a FASTA record and has only one model type that works offline here. A reader arrives
wanting the one they are running.

Whose work a run does is in the credit line, with the version read off the install rather than
retyped, so a run's provenance a year later names the version that was true that day.
"""
from __future__ import annotations

import glob
import os
import re

from .. import __version__
from .. import docskit
#: The lab's statement and the pages' palette, from the design pages. One copy of each.
from ..pages.docs import COPYRIGHT, DARK, LIGHT

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

#: The sidebar, in order: (slug, title, section). The same for both tools, because the questions
#: a person arrives with are the same ones in the same order.
NAV = [
    ("index", "Overview", "Start here"),
    ("getting-started", "Getting started", "Start here"),

    ("entities", "The sequences", "Setting up a run"),
    ("alignments", "The two phases", "Setting up a run"),
    ("resources", "Resources and submitting", "Setting up a run"),

    ("runs", "The Runs screen", "Results"),
    ("results", "What a run leaves", "Results"),
    ("viewing", "Looking at a structure", "Results"),

    ("messages", "What the messages mean", "Reference"),
    ("citing", "How to cite", "Reference"),
]


class About:
    """What one tool's documentation says about itself, and about whose work it runs."""

    def __init__(self, app, tagline, credits, upstream, fallback_version, dist):
        self.app = app
        self.tagline = tagline
        self._credits = credits
        self.upstream = upstream
        #: The version to name when the install cannot be read. A literal rather than "unknown",
        #: so the bar still says something true of the lab's own copy.
        self.fallback_version = fallback_version
        #: The distribution whose dist-info folder states the version. Both tools are wheels in
        #: an environment, and the name of that folder is the one thing in the layout that says
        #: which version is installed.
        self.dist = dist

    def credits(self, version):
        return [line % version if "%s" in line else line for line in self._credits]


#: Each tool's own mark, in the 24-unit box the launcher's canvas draws its cards in, so a tool
#: looks the same on its card, in the Tools list and at the top of its own page.
#:
#: The shapes say what the tool does. Boltz-2's: a helix and a strand brought together with a
#: small molecule between them, which is the one thing it does that the others do not. The
#: ColabFold mark is a chain settling into a fold, drawn as one line because that is what it
#: takes and what it gives back.
MARKS = {
    "boltz2": ('<path d="M4.5 4 C8 7, 8 13, 4.5 16 C8 18, 8 20, 6.5 21" fill="none" '
               'stroke="%(a)s" stroke-width="2" stroke-linecap="round"/>'
               '<path d="M19 4 L19 20" fill="none" stroke="%(a)s" stroke-width="2" '
               'stroke-linecap="round"/>'
               '<circle cx="12" cy="12" r="2.6" fill="none" stroke="%(b)s" '
               'stroke-width="2"/>'),
    "colabfold": ('<path d="M4 20 C4 15, 9 16, 9 12 C9 8, 4 9, 5 4" fill="none" '
                  'stroke="%(a)s" stroke-width="2" stroke-linecap="round"/>'
                  '<path d="M13 4 C18 6, 18 10, 14 11 C19 13, 19 18, 13.5 20" fill="none" '
                  'stroke="%(b)s" stroke-width="2" stroke-linecap="round"/>'),
}


def mark(tool, teal="var(--teal)", rust="var(--rust)"):
    """The tool's mark as the body of a 24-unit svg, in the page's two colors."""
    body = MARKS.get(tool)
    if not body:
        return ('<rect x="5" y="5" width="14" height="14" rx="3" fill="none" stroke="%s" '
                'stroke-width="2"/>' % teal)
    return body % {"a": teal, "b": rust}


def favicon(tool):
    """The same mark as a data URI, for the tab. The colors are spelled out: a data URI has no
    stylesheet to read a variable from."""
    from urllib.parse import quote
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">%s</svg>'
           % mark(tool, "#0d5c6b", "#c2603a"))
    return "data:image/svg+xml," + quote(svg, safe="")


#: The two viewers the Runs screen draws a structure with, credited wherever a structure is
#: shown. Both are vendored into web/vendor rather than fetched: the page is reached over a
#: forwarded port, and a page that needed a content delivery network would show an empty box.
VIEWER_CREDITS = [
    'viewing by <a href="https://3dmol.csb.pitt.edu/" target="_blank" rel="noopener">3Dmol.js'
    '</a>, by Nicholas Rego and David Koes',
    'and <a href="https://molstar.org/" target="_blank" rel="noopener">Mol*</a>, by David Sehnal '
    'and colleagues',
]

ABOUT = {
    "boltz2": About(
        app="Boltz-2 on Grace",
        tagline="the lab's page for folding a complex of chains and ligands",
        credits=[
            'structures by <a href="https://github.com/jwohlwend/boltz" target="_blank"'
            ' rel="noopener">Boltz-2</a> %s, by Jeremy Wohlwend, Gabriele Corso, Saro Passaro '
            'and colleagues, under the MIT license',
            "alignments from the ColabFold MSA server, by Milot Mirdita and colleagues",
        ] + VIEWER_CREDITS + [
            "computing by Texas A&amp;M HPRC",
        ],
        upstream="https://github.com/jwohlwend/boltz",
        fallback_version="2.2.1",
        dist="boltz"),
    "colabfold": About(
        app="ColabFold on Grace",
        tagline="the lab's page for folding protein chains and complexes",
        credits=[
            'structures by <a href="https://github.com/sokrypton/ColabFold" target="_blank"'
            ' rel="noopener">ColabFold</a> %s, by Milot Mirdita, Konstantin Sch&uuml;tze, '
            'Yoshitaka Moriwaki, Lim Heo, Sergey Ovchinnikov and Martin Steinegger, under the '
            'MIT license',
            "folding by AlphaFold 2 and AlphaFold-Multimer, by DeepMind, with weights under "
            "CC BY 4.0",
        ] + VIEWER_CREDITS + [
            "computing by Texas A&amp;M HPRC",
        ],
        upstream="https://github.com/sokrypton/ColabFold",
        fallback_version="1.6.2",
        dist="colabfold"),
}


def upstream_version(tool, install):
    """The tool's own version, read from the install rather than retyped here.

    Both tools are wheels in an environment, so the version is the name of the dist-info folder.
    Read that way rather than by importing the package: importing Boltz-2 means importing torch,
    which took five minutes on this cluster, and a page cannot start by waiting for that.
    """
    about = ABOUT[tool]
    try:
        for folder in glob.glob(os.path.join(install, "lib", "python*", "site-packages",
                                             "%s-*.dist-info" % about.dist)):
            found = re.search(r"%s-([^/]+)\.dist-info$" % re.escape(about.dist), folder)
            if found:
                return found.group(1)
    except OSError:
        pass
    return about.fallback_version


#: The kit's footer ends by saying every screenshot was made by the tool named in `shots`. These
#: pages carry none, so the sentence is taken out of the rendered page, exactly as the design
#: pages do it. docskit.py itself stays the other interfaces' copy, byte for byte.
def _shots_sentence(app):
    return "\nEvery screenshot is of %s itself, made by <code></code>." % app


class Docs:
    """One tool's documentation: the site, and the three things the server asks of it."""

    def __init__(self, tool, install=""):
        about = ABOUT[tool]
        self.tool = tool
        self.app = about.app
        self.version = upstream_version(tool, install or "")
        self.site = docskit.Site(
            app=about.app,
            tagline=about.tagline,
            # The version of the page, which is the launcher's: the page ships with HOPE Labs and
            # is released with it. The tool's own version is in the credit line beside it, where
            # it cannot be mistaken for ours.
            version=__version__,
            copyright=COPYRIGHT,
            nav=NAV,
            root=os.path.join(WEB, "docs", tool),
            credits=about.credits(self.version),
            theme_key="hldesigntheme",
            light=LIGHT,
            dark=DARK,
            shots="",
            cite_href="/docs/citing.html")
        self._shots = _shots_sentence(about.app)

    def render(self, slug):
        page = docskit.render(self.site, slug)
        return None if page is None else page.replace(self._shots, "")

    def respond(self, path):
        """(status, content type, body, location) for a request under /docs/."""
        status, kind, body, location = docskit.respond(self.site, path)
        if status == 200 and kind.startswith("text/html"):
            body = body.replace(self._shots.encode("utf-8"), b"")
        return status, kind, body, location

    def creditbar(self):
        """The bar along the bottom of the form: one still line and Credits (hope_labs/footer.py).

        The documentation keeps the kit's own bar. On a form, a line of credits rolling past the
        edges of the window while somebody fills the form in is the thing being got rid of."""
        from .. import footer
        return footer.bar(self.site, "/docs/index.html")

    def audit(self):
        """Everything wrong with these pages, as a list of sentences."""
        return docskit.audit(self.site)
