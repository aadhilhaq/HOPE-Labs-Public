"""The built-in documentation, served at /docs/ by the page's own server.

By Aadhil Haq

A page is an HTML fragment in hopemd/web/docs/pages/<slug>.html: an <h1>, a lead paragraph, then
sections under <h2>. They live under web/, beside the page they describe. They are text and tables
and show no screenshots, so the sentence the kit's footer gives a tool's screenshots is left out of
every page sent.
"""
from __future__ import annotations

import html
import os
import re

from . import COPYRIGHT, CREDITS, __version__
from . import docskit
from .brand import NAME, TAGLINE

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "docs")

#: The sidebar, in order: (slug, title, section).
NAV = [
    ("index", "Overview", "Start here"),
    ("getting-started", "Getting started", "Start here"),

    ("sources", "Sources", "Setting up a run"),
    ("prepare", "Prepare", "Setting up a run"),
    ("engines", "Engines and force fields", "Setting up a run"),
    ("membranes", "Membranes", "Setting up a run"),
    ("protocol", "Protocol and compute", "Setting up a run"),
    ("analysis", "Analysis and MM-GBSA", "Setting up a run"),

    ("runs", "The Runs screen", "Watching a run"),
    ("reports", "Reports and media", "Watching a run"),

    ("run-folder", "The run folder", "Reference"),
    ("troubleshooting", "When something goes wrong", "Reference"),
    ("citing", "How to cite", "Reference"),
]

#: The page's own colors, from the :root blocks of web/style.css (light, and the dark one the
#: computer's setting or the page's button chooses), under the names the kit uses. The marks take
#: the rust, the color of the thing being simulated; code takes --wait, as the page's logs do.
LIGHT = {"bg": "#eef3f3", "card": "#ffffff", "line": "#d7e2e2", "fg": "#0f1719", "dim": "#5f6f71",
         "accent": "#0d5c6b", "accent2": "#e3eef0", "code": "#e9f0f0", "mark": "#c2603a"}
DARK = {"bg": "#0b1113", "card": "#141d1f", "line": "#22302f", "fg": "#e6eeef", "dim": "#94a6a8",
        "accent": "#5fb3c4", "accent2": "#16292e", "code": "#1a2527", "mark": "#e08a63"}

#: Everything the shared layout needs to know about these docs.
SITE = docskit.Site(
    app=NAME,
    tagline=TAGLINE,
    version=__version__,
    copyright=COPYRIGHT,
    nav=NAV,
    root=HERE,
    # the kit takes the credits as HTML; the package keeps them as text
    credits=[html.escape(c) for c in CREDITS],
    # The key app.js keeps the light or dark choice under, so the docs open in the theme the page
    # is in: they are served from the same address.
    theme_key="hopemd.theme",
    light=LIGHT,
    dark=DARK,
    shots="",
    # The bar's How to cite opens the page's own dialog rather than a page.
    cite_href="#",
)

#: the kit's footer says who took the screenshots; these pages have none
_SHOTS = re.compile(r"\s*Every screenshot is of [^<]* made by <code>[^<]*</code>\.")


def _without_shots(page: str) -> str:
    return _SHOTS.sub("", page)


def render(slug: str) -> str | None:
    """One page in the shared layout, or None when there is no such page."""
    page = docskit.render(SITE, slug)
    return None if page is None else _without_shots(page)


def respond(path: str) -> tuple[int, str, bytes, str | None]:
    """(status, content type, body, location) for a request under /docs/."""
    status, kind, body, location = docskit.respond(SITE, path)
    if status == 200 and kind.startswith("text/html"):
        body = _without_shots(body.decode("utf-8")).encode("utf-8")
    return status, kind, body, location


def audit() -> list[str]:
    """Everything wrong with the pages, as a list of sentences."""
    return docskit.audit(SITE)
