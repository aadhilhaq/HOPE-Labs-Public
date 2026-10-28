"""The documentation the ensemble page serves at /docs/, and the bar along its bottom.

By Aadhil Haq

The layout, the stylesheet and the bar come from hope_labs/docskit.py, which every one of the
lab's interfaces shares. The colors and the lab's copyright come from the design pages'
documentation (hope_labs/pages/docs.py), imported rather than restated: there is one palette and
one statement, and every page of ours reads as kin.

One tool, so one set of pages. The sidebar follows the questions a reader arrives with, and two
of them are this page's alone: what the shapes in an ensemble are and are not, and how to put one
against a simulation of the same chain.

Whose work a run does is in the credit line, with the version read off the image rather than
retyped, so a run's provenance a year later names the version that was true that day.
"""
from __future__ import annotations

import os
import re

from .. import __version__
from .. import docskit
#: The lab's statement and the pages' palette, from the design pages. One copy of each.
from ..pages.docs import COPYRIGHT, DARK, LIGHT

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

#: The sidebar, in order: (slug, title, section).
NAV = [
    ("index", "Overview", "Start here"),
    ("getting-started", "Getting started", "Start here"),

    ("sequence", "The sequence", "Setting up a run"),
    ("phases", "The two phases", "Setting up a run"),
    ("resources", "Resources and submitting", "Setting up a run"),

    ("runs", "The Runs screen", "Results"),
    ("results", "What a run leaves", "Results"),
    ("shapes", "The shapes, and what they are not", "Results"),
    ("comparing", "Against a simulation", "Results"),

    ("messages", "What the messages mean", "Reference"),
    ("citing", "How to cite", "Reference"),
]


class About:
    """What the documentation says about itself, and about whose work it runs."""

    def __init__(self, app, tagline, credits, upstream, fallback_version):
        self.app = app
        self.tagline = tagline
        self._credits = credits
        self.upstream = upstream
        #: The version to name when the image cannot be read. A literal rather than "unknown", so
        #: the bar still says something true of the lab's own copy.
        self.fallback_version = fallback_version

    def credits(self, version):
        return [line % version if "%s" in line else line for line in self._credits]


#: The tool's mark, in the 24-unit box the launcher's canvas draws its cards in, so it looks the
#: same on its card, in the Tools list and at the top of its own page.
#:
#: The shape says what the tool does: one chain drawn three times over, the same fold in three
#: arrangements, which is an ensemble and is nothing any of the other marks could be mistaken for.
MARKS = {
    "bioemu": ('<path d="M4 19 C7 13, 10 16, 12 10 C14 5, 17 7, 20 5" fill="none" '
               'stroke="%(a)s" stroke-width="2" stroke-linecap="round" opacity="0.45"/>'
               '<path d="M4 19 C8 15, 9 12, 12 11 C15 10, 16 6, 20 5" fill="none" '
               'stroke="%(a)s" stroke-width="2" stroke-linecap="round" opacity="0.7"/>'
               '<path d="M4 19 C6 16, 11 15, 12 12 C13 9, 18 8, 20 5" fill="none" '
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


ABOUT = {
    "bioemu": About(
        app="BioEmu on Grace",
        tagline="the lab's page for sampling the structures a protein takes, from its sequence",
        credits=[
            'structures by <a href="https://github.com/microsoft/bioemu" target="_blank"'
            ' rel="noopener">BioEmu</a> %s, by Sarah Lewis, Tim Hempel, Jos&eacute; Jim&eacute;'
            'nez-Luna and colleagues at Microsoft Research, under the MIT license',
            "alignments from the ColabFold MSA server, by Milot Mirdita and colleagues",
            "embeddings from AlphaFold 2, by DeepMind, with weights under CC BY 4.0",
            'viewing by <a href="https://3dmol.csb.pitt.edu/" target="_blank" rel="noopener">'
            '3Dmol.js</a>, by Nicholas Rego and David Koes',
            'reading the samples by <a href="https://www.mdtraj.org/" target="_blank" '
            'rel="noopener">MDTraj</a>, by Robert McGibbon and colleagues',
            "computing by Texas A&amp;M HPRC",
        ],
        upstream="https://github.com/microsoft/bioemu",
        fallback_version="1.4.1"),
}


def upstream_version(tool, install):
    """The tool's own version, read off the image rather than retyped here.

    A tool that comes as a container image carries its version in the image's own name, which Set
    up wrote when it pulled it (hope_labs/images.py). Read from the name rather than by running
    the image: starting a container to ask it its version costs seconds on every page start, and
    the name is the same answer.
    """
    found = re.search(r"_([0-9][0-9A-Za-z.\-]*)\.sif$", os.path.basename(str(install or "")))
    return found.group(1) if found else ABOUT[tool].fallback_version


#: The kit's footer ends by saying every screenshot was made by the tool named in `shots`. These
#: pages carry none, so the sentence is taken out of the rendered page, exactly as the design
#: pages do it. docskit.py itself stays the other interfaces' copy, byte for byte.
def _shots_sentence(app):
    return "\nEvery screenshot is of %s itself, made by <code></code>." % app


class Docs:
    """The documentation: the site, and the three things the server asks of it."""

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
        """The bar along the bottom of the page: one still line and Credits (hope_labs/footer.py).
        The documentation keeps the kit's own bar."""
        from .. import footer
        return footer.bar(self.site, "/docs/index.html")

    def audit(self):
        """Everything wrong with these pages, as a list of sentences."""
        return docskit.audit(self.site)
