"""The documentation each design page serves at /docs/, and the bar along its bottom.

By Aadhil Haq

The layout, the stylesheet and the bar come from hope_labs/docskit.py, which the docking
console, BindCraft2 and the launcher all share. What is here is
only what is ours: the sidebar, the colours, the copyright, and the credit line naming whose work
each run actually does.

A page is an HTML fragment in web/docs/<tool>/pages/<slug>.html: an <h1>, a lead paragraph, then
sections under <h2>. There are no screenshots. The page is read beside the documentation rather
than pictured in it, which is also why neither set of pages goes stale when a field moves.

The two tools get two sets of pages rather than one set with "or BoltzGen" in every sentence.
They are the same shape to run and nothing like each other to use: one diffuses a backbone and
then gives it a sequence, the other generates a binder against a target of any kind and folds it,
and the hotspots mean different things to them. A reader wants the one they are running.
"""
from __future__ import annotations

import glob
import os
import re

from .. import COPYRIGHT, __version__
from .. import docskit

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

#: The lab's statement, as every one of its tools makes it. It covers the page, this module and
#: the pages themselves: the code in front of each tool, and nothing of the tool. It comes out of
#: LICENSE at the root of the repository, the one place the line is written down.

#: The sidebar, in order: (slug, title, section). The same for both tools, because the questions
#: a person arrives with are the same ones in the same order.
NAV = [
    ("index", "Overview", "Start here"),
    ("getting-started", "Getting started", "Start here"),

    ("target", "The target and hotspots", "Designing a binder"),
    ("binder", "The binder", "Designing a binder"),
    ("resources", "Resources and submitting", "Designing a binder"),

    ("runs", "The Runs screen", "Results"),
    ("results", "What a run leaves", "Results"),

    ("messages", "What the messages mean", "Reference"),
    ("citing", "How to cite", "Reference"),
]

#: The page's own colours, from the :root block in web/style.css under the names the kit uses.
#: The same palette as the lab's other pages: the tools should read as kin, and a person moving
#: between them should not have to work out which one they are looking at from the colour.
LIGHT = {"bg": "#eef3f3", "card": "#ffffff", "line": "#d7e2e2", "fg": "#0f1719",
         "dim": "#5f6f71", "accent": "#0d5c6b", "accent2": "#e3eef0", "mark": "#c2603a"}
DARK = {"bg": "#0b1113", "card": "#141d1f", "line": "#22302f", "fg": "#e6eeef",
        "dim": "#94a6a8", "accent": "#5fb3c4", "accent2": "#16292e", "mark": "#e08a63"}


class About:
    """What one tool's documentation says about itself, and about whose work it runs."""

    def __init__(self, app, tagline, credits, upstream, fallback_version):
        self.app = app
        self.tagline = tagline
        self._credits = credits
        self.upstream = upstream
        #: The version to name when the install cannot be read. A literal rather than "unknown",
        #: so the bar still says something true of the lab's own copy.
        self.fallback_version = fallback_version

    def credits(self, version):
        return [line % version if "%s" in line else line for line in self._credits]


#: Each tool's own mark, in the 24-unit box the launcher's canvas draws its cards in, so a tool
#: looks the same on its card, in the Tools list and at the top of its own page. They were one
#: shared mark, which made two different tools read as the same one.
#:
#: The shapes say what the tool does: RFdiffusion's, points settling out of noise into a helix;
#: BoltzGen's, a binder taking shape against the arc of a target.
MARKS = {
    "rfdiffusion": ('<circle cx="4.5" cy="6" r="1.1" fill="%(a)s"/>'
                    '<circle cx="4" cy="12.5" r="1.1" fill="%(a)s"/>'
                    '<circle cx="6" cy="18" r="1.1" fill="%(a)s"/>'
                    '<path d="M10 18 C16 16, 10 13, 16 11 C10 9, 16 6, 13 5" fill="none" '
                    'stroke="%(b)s" stroke-width="2" stroke-linecap="round"/>'),
    # RFdiffusion3: the same free atoms as its tile, resolving out of the noise into a chain -
    # the one thing that separates it from RFdiffusion is that the atoms are the unit.
    "rfd3": ('<circle cx="4.5" cy="5.5" r="1.1" fill="%(a)s"/>'
             '<circle cx="4" cy="11.5" r="1.1" fill="%(a)s"/>'
             '<circle cx="5.5" cy="17.5" r="1.1" fill="%(a)s"/>'
             '<circle cx="11" cy="7" r="1.3" fill="%(b)s"/>'
             '<circle cx="15.5" cy="11" r="1.3" fill="%(b)s"/>'
             '<circle cx="12.5" cy="15.5" r="1.3" fill="%(b)s"/>'
             '<circle cx="18" cy="17" r="1.3" fill="%(b)s"/>'),
    "boltzgen": ('<path d="M6 3 C2.5 7, 2.5 17, 6 21" fill="none" stroke="%(a)s" '
                 'stroke-width="2" stroke-linecap="round"/>'
                 '<path d="M12 7.5 L17.5 10.5 L17.5 16 L12 19 L10 13.5 Z" fill="none" '
                 'stroke="%(b)s" stroke-width="2" stroke-linejoin="round"/>'),
}


def mark(tool, teal="var(--teal)", rust="var(--rust)"):
    """The tool's mark as the body of a 24-unit svg, in the page's two colours."""
    body = MARKS.get(tool)
    if not body:
        return ('<rect x="5" y="5" width="14" height="14" rx="3" fill="none" stroke="%s" '
                'stroke-width="2"/>' % teal)
    return body % {"a": teal, "b": rust}


def favicon(tool):
    """The same mark as a data URI, for the tab. The colours are spelled out: a data URI has no
    stylesheet to read a variable from."""
    from urllib.parse import quote
    body = mark(tool, "#0d5c6b", "#c2603a")
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">%s</svg>' % body)
    return "data:image/svg+xml," + quote(svg, safe="")


ABOUT = {
    "rfdiffusion": About(
        app="RFdiffusion on Grace",
        tagline="the lab's page for diffusing a binder onto a target",
        credits=[
            'backbones by <a href="https://github.com/RosettaCommons/RFdiffusion" target="_blank"'
            ' rel="noopener">RFdiffusion</a> %s, by the Institute for Protein Design, '
            'University of Washington',
            "sequences by ProteinMPNN, by Justas Dauparas and colleagues",
            "computing by Texas A&amp;M HPRC",
        ],
        upstream="https://github.com/RosettaCommons/RFdiffusion",
        fallback_version="1.1.0"),
    "rfd3": About(
        app="RFdiffusion3 on Grace",
        tagline="the lab's page for designing a binder atom by atom",
        credits=[
            'designs by <a href="https://github.com/RosettaCommons/foundry" target="_blank"'
            ' rel="noopener">RFdiffusion3</a> %s, by the Institute for Protein Design, '
            'University of Washington',
            "sequences by MPNN, which ships in the same package",
            "computing by Texas A&amp;M HPRC",
        ],
        upstream="https://github.com/RosettaCommons/foundry",
        fallback_version="1.0.0"),
    "boltzgen": About(
        app="BoltzGen on Grace",
        tagline="the lab's page for generating a binder against a target",
        credits=[
            'binders by <a href="https://github.com/HannesStark/boltzgen" target="_blank"'
            ' rel="noopener">BoltzGen</a> %s, by Hannes St&auml;rk and colleagues',
            "folding and ranking by BoltzGen, which re-folds with Boltz-2",
            "computing by Texas A&amp;M HPRC",
        ],
        upstream="https://github.com/HannesStark/boltzgen",
        fallback_version="0.3.2"),
}


def upstream_version(tool, install):
    """The tool's own version, read from the install rather than retyped here.

    After the install is updated the bar should name what it actually runs, which is the whole
    point of putting a version on a page: somebody reading a run's provenance a year later wants
    the number that was true that day.
    """
    about = ABOUT[tool]
    try:
        if tool == "rfdiffusion":
            with open(os.path.join(install, "setup.py"), encoding="utf-8") as handle:
                found = re.search(r"version\s*=\s*['\"]([^'\"]+)['\"]", handle.read())
            return found.group(1) if found else about.fallback_version
        # The other two are wheels in an environment, so the version is the one thing in that
        # environment's layout that states it: the name of the dist-info folder.
        package = "rc_foundry" if tool == "rfd3" else "boltzgen"
        for folder in glob.glob(os.path.join(install, "lib", "python*", "site-packages",
                                             package + "-*.dist-info")):
            found = re.search(re.escape(package) + r"-([^/]+)\.dist-info$", folder)
            if found:
                return found.group(1)
    except OSError:
        pass
    return about.fallback_version


#: The kit's footer ends by saying every screenshot was made by the tool named in `shots`. These
#: pages carry none, so the sentence is taken out of the rendered page. docskit.py itself stays
#: the other interfaces' copy, byte for byte, so the copies can still be compared.
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
        """The bar along the bottom of the page."""
        return docskit.creditbar_html(self.site, "/docs/index.html")

    def audit(self):
        """Everything wrong with these pages, as a list of sentences."""
        return docskit.audit(self.site)
