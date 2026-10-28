"""The parts every HOPE Labs report is built from: the page, the module, the words, the charts.

By Aadhil Haq

* ``page()``, the ``/report`` page, and ``static_file()``, which serves the module out of
  ``hope_labs/static`` by name and refuses everything else;
* the words and the numbers - ``e()``, ``num()``, ``plural()``, ``words()``, ``and_list()`` -
  which must write a number exactly as ``reportkit.js`` writes it, because a caption and the table
  beneath it are read together;
* ``options()`` and ``echo()``, the bar's own choices, held to their range and said back;
* the charts, drawn as SVG here rather than in the browser so they stay vector in the PDF: a line
  per series, a value per residue, stacked fractions, a ranked bar, a scatter and a histogram;
* ``grey_png()``, a matrix as a PNG, written with zlib because the page's Python has no
  matplotlib and a login node should not grow one;
* ``job_block()`` and ``byline()``, the appendix rows every tool owes the reader.

There is one copy of ``reportkit.js`` and ``reportkit.css`` in this checkout, in
``hope_labs/static``, and all four servers serve it from there. One copy rather than one per page:
the files are a module whose copies are held byte for byte against the other lab checkouts, and
four copies inside one repository would be four chances to drift where the test can only say that
one of them differs. ``<checkout>/*/static/<name>`` is also the path every sibling tool's test
globs for, so a copy here is compared by all of them and a copy under ``pages/web`` would be
compared by none.

Nothing here reads a run folder or writes one. A report only reads.
"""
from __future__ import annotations

import html
import json
import math
import os
import re
import struct
import subprocess
import zlib

#: Where the one copy of the module lives, and the only names served from it. A fixed list, so no
#: path from a browser is ever joined onto this folder.
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC_FILES = {"reportkit.js": "application/javascript; charset=ascii",
                "reportkit.css": "text/css; charset=ascii"}


def static_file(name):
    """(bytes, content type) for one of STATIC_FILES, or (None, None)."""
    kind = STATIC_FILES.get(str(name or ""))
    if not kind:
        return None, None
    try:
        with open(os.path.join(STATIC, name), "rb") as handle:
            return handle.read(), kind
    except OSError:
        return None, None


# ----------------------------------------------------------------- the page

#: THE REPORT OF A RUN: a page to read and to save as a PDF. reportkit lays it out from
#: GET /api/report; the page is light whatever the interface's theme, as paper is.
#:
#: 3Dmol is loaded from the copy this checkout vendors and only then from the CDN, pinned. That
#: order is the point: a report opened through a tunnel to a compute node has no internet, and the
#: structure figures are drawn in the browser.
PAGE = r"""<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Report</title>
__FAVICON__
<link rel="stylesheet" href="/static/reportkit.css?v=__VERSION__">
<div id=rk-bar></div>
<main id=rk-doc><p class=rk-why>Reading the run&hellip;</p></main>
<script src="__THREEDMOL__"></script>
<script>
if (typeof $3Dmol === "undefined")
  document.write('<scr' + 'ipt src="https://cdnjs.cloudflare.com/ajax/libs/'
    + '3Dmol/2.5.5/3Dmol-min.js"><\/scr' + 'ipt>');
</script>
<script src="/static/reportkit.js?v=__VERSION__"></script>
<script>
ReportKit.start({api: '/api/report', token: __TOKEN__, params: {rundir: __RUNDIR__},
                 store: '__STORE__', name: 'Report', autoprint: __PRINT__});
</script>
"""


def page(token, rundir, version, store, autoprint=False, favicon="",
         three_dmol="/static/vendor/3Dmol-min.js"):
    """The report page for one run, carrying the token and the run.

    Both go in as JSON with ``<`` escaped, so a run folder whose name holds a quote or a closing
    tag cannot end the script it is carried in.
    """
    quoted = lambda v: json.dumps(str(v or "")).replace("<", "\\u003c")      # noqa: E731
    return (PAGE.replace("__TOKEN__", quoted(token))
                .replace("__RUNDIR__", quoted(rundir))
                .replace("__PRINT__", "true" if autoprint else "false")
                .replace("__VERSION__", str(version or ""))
                .replace("__STORE__", str(store or "hopelabs.report"))
                .replace("__THREEDMOL__", three_dmol or "/static/vendor/3Dmol-min.js")
                .replace("__FAVICON__", favicon or ""))


# ------------------------------------------------------- the words and the numbers

def e(text):
    """A value as it may go into the HTML of a caption, a cell or a list item."""
    return html.escape(str(text if text is not None else ""), quote=True)


def num(value, digits=1):
    """A number as the report prints it: fixed decimals, a true minus sign, thousands grouped
    from 10,000 up, exactly as reportkit.js prints it. A test runs the two against each other."""
    if value is None or value == "":
        return ""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return e(value)
    out = "%.*f" % (digits, value)
    if abs(value) >= 10000:
        whole, _, part = out.lstrip("-").partition(".")
        out = ("-" if value < 0 else "") + "{:,}".format(int(whole)) + ("." + part if part else "")
    return out.replace("-", "−")


def signed(value, digits=1):
    """A number with its sign kept, so a positive energy reads as one."""
    got = number(value)
    if got is None:
        return ""
    return ("+" if got > 0 else "") + num(got, digits)


def number(value):
    """A number, or None where the run left the cell empty."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def whole(value):
    got = number(value)
    return None if got is None else int(round(got))


def pc(value, digits=0):
    """A fraction as a percentage, for a caption that reads better that way."""
    got = number(value)
    return "" if got is None else num(got * 100.0, digits)


def plural(count, word, many=None):
    """A count and its noun: "one design", "12 designs", "3 swarms"."""
    count = int(count)
    return "%s %s" % ("{:,}".format(count), word if count == 1 else (many or word + "s"))


SMALL = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve"]


def count(number_of, word, many=None):
    """A count and its noun, the small ones written out: "both designs", "three residues".

    These captions read as prose, and "All 2 designs" is not how anybody writes it.
    """
    try:
        number_of = int(number_of)
    except (TypeError, ValueError):
        return plural(0, word, many)
    if number_of == 2:
        return "both %s" % (many or word + "s")
    if 0 <= number_of < len(SMALL):
        return "%s %s" % (SMALL[number_of], word if number_of == 1 else (many or word + "s"))
    return plural(number_of, word, many)


def many(number_of, word, plural_word=None):
    """A count and its noun for a slot that already has "the" in front of it.

    count() writes "both designs", which is right at the head of a clause and wrong after "the".
    This writes the small numbers as words and never says "both": "the two cards", "the one job".
    """
    try:
        number_of = int(number_of)
    except (TypeError, ValueError):
        return plural(0, word, plural_word)
    if 0 <= number_of < len(SMALL):
        return "%s %s" % (SMALL[number_of],
                          word if number_of == 1 else (plural_word or word + "s"))
    return plural(number_of, word, plural_word)


def words(count):
    """A small count written out, which is how these captions say the small ones."""
    try:
        count = int(count)
    except (TypeError, ValueError):
        return str(count)
    return SMALL[count] if 0 <= count < len(SMALL) else "{:,}".format(count)


def and_list(items, join="and"):
    items = [str(x) for x in items if str(x)]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return "%s %s %s" % (", ".join(items[:-1]), join, items[-1])


def cap(sentence):
    """A clause capitalised for the middle of a caption.

    A clause opening with a word that carries its own capitals - ipTM, pTM, pLDDT, a sequence, a
    column name in code - is left exactly as it is: "IpTM" is not a word, and a design called
    design_0 is not a sentence to be tidied.
    """
    if not sentence or sentence[0] in "<−0123456789":
        return sentence
    head = sentence.split(" ", 1)[0]
    if any(c.isupper() for c in head[1:]) or "_" in head:
        return sentence
    return sentence[0].upper() + sentence[1:]


#: A clause opening with one of these continues the sentence before it rather than starting a new
#: one. Without this a caption builder that appends "and of the site residues Arg103 by 17" gets
#: "And of the site residues Arg103 by 17." as a sentence of its own, which is not English.
JOINERS = ("and ", "but ", "or ", "nor ", "so ", "yet ", "though ", "although ", "while ",
           "whereas ", "including ", "led by ", "with ", "against ", "whose ", "which ",
           "because ", "since ", "as ")


def sentences(bits):
    """The house caption: the result first, then only what is needed to read it.

    Each bit is a sentence, capitalised, unless it opens with a connective, in which case it
    continues the one before it after a comma.
    """
    bits = [b for b in bits if b and str(b).strip()]
    if not bits:
        return ""
    out = cap(bits[0]).rstrip(". ")
    for bit in bits[1:]:
        bit = str(bit).strip()
        plain = re.sub(r"<[^>]+>", "", bit).lstrip()
        if plain.lower().startswith(JOINERS):
            out += ", " + bit.rstrip(". ")
        else:
            out += ". " + cap(bit).rstrip(". ")
    return out + "."


def pm(mean, sd, digits=1):
    """A mean and its spread, where the run wrote both."""
    if mean is None:
        return ""
    return num(mean, digits) + ("" if sd is None else " ± " + num(sd, digits))


def read_json(path, default=None):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {} if default is None else default


# ------------------------------------------------------------------ the bar

def options(declared, raw, limits=None):
    """The options as asked for, held to what they may be.

    ``limits`` narrows a number's ``most`` to what this run actually has, so a run of two designs
    does not offer fifty.
    """
    out = {}
    for one in declared:
        default = one.get("default")
        got = (raw or {}).get(one["key"], default)
        if one["type"] == "number":
            try:
                got = int(float(got))
            except (TypeError, ValueError):
                got = default
            most = (limits or {}).get(one["key"], one.get("most"))
            got = max(one.get("least", 1), min(most if most else one.get("most", got), got))
        elif one["type"] == "check":
            got = got if isinstance(got, bool) else str(got).lower() in ("1", "true", "yes", "on")
        elif one["type"] == "select":
            allowed = [c[0] for c in one.get("choices") or []]
            got = got if got in allowed else default
        out[one["key"]] = got
    return out


def echo(declared, chosen, limits=None, drop=()):
    """The options as the bar shows them, each saying in ``value`` what was used."""
    out = []
    for one in declared:
        if one["key"] in drop:
            continue
        one = dict(one, value=chosen.get(one["key"]))
        most = (limits or {}).get(one["key"])
        if most:
            one["most"] = most
            if one.get("default") is not None and one["type"] == "number":
                one["default"] = min(most, one["default"])
        out.append(one)
    return out


# ------------------------------------------------------------------ the charts
#
# Drawn here as SVG rather than in the browser, so they stay vector in the PDF and a report saved
# to a PDF is the same picture as the one on the screen. The palette is the lab's, and every chart
# is backed with white so it prints on paper rather than on the page's own color.

INK = "#1f2933"
GRID = "#c8d0d8"
MARK = "#1a56db"
PALE = "#8d9cb3"
WARM = "#e8955a"
SERIES = ("#1a56db", "#e8955a", "#2f9e6e", "#9b5bb8", "#c0392b", "#117a8b", "#8d9cb3")


#: The sheet a report is laid out on is A4 with 15 mm margins (reportkit.css: #rk-doc), so a
#: figure is 180 mm wide whatever the window is: 680 CSS pixels. A chart is drawn in that many
#: user units and pinned there, which makes one user unit one pixel on the screen and one 96th
#: of an inch on paper, and a label's size in units the size it is drawn at.
#:
#: The pin is what carries this into the PDF and onto a narrower window. Without it the drawing
#: is stretched to whatever box it lands in - across the page, inside a half-width panel, on a
#: sheet of paper 184 mm wide between its margins - and the labels are stretched with it, which
#: is how a chart written at 11 came to be drawn at 5.8 px in a panel.
FIG = 680

#: Nothing the lab draws is under 13 px. A chart's furniture is set at the floor: the smallest
#: the lab allows, which leaves the most room for the data.
LABEL = 13


def _open(width, height):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
            'style="max-width:%dpx;margin-inline:auto" '
            'font-family="system-ui,Helvetica,Arial,sans-serif" font-size="%d">'
            '<rect width="%d" height="%d" fill="#ffffff"/>'
            % (width, height, width, LABEL, width, height))


def _wide(says, size=LABEL):
    """About how wide a label is drawn, in pixels. Digits and lower case average a little under
    two thirds of the size in this sans-serif, and a margin worked out from that is never the
    reason a label is clipped."""
    return 0.62 * size * len(str(says))


def _fit(says, room, size=LABEL):
    """`says`, shortened with an ellipsis until it is drawn no wider than `room` pixels.

    A label too long for the space it is given used to run off the edge of the drawing rather
    than be shortened: a filter named designfolding-filter_rmsd was drawn from x=-39 on a
    640-unit chart, so its first third was not on the picture at all, and a rank label on a
    scatter ran off the right. Shortened is a label a reader can still use; clipped is not.
    """
    says = str(says if says is not None else "")
    if len(says) <= 1 or _wide(says, size) <= room:
        return says
    keep = int(room / (0.62 * size)) - 1
    return (says[:keep].rstrip() + "\u2026") if keep >= 1 else "\u2026"


def _gutter(labels, least, size=LABEL):
    """The room a left-hand axis needs for its widest label, and never less than `least`.

    Worked out rather than fixed, because the labels are the run's own numbers: a gutter that
    fits "0.10" does not fit "-1234.5", and the axis is where a chart runs out of room first.
    """
    widest = max([_wide(one, size) for one in labels] or [0.0])
    return int(max(float(least), widest + 10.0))


def _text(x, y, says, anchor="start", size=LABEL, fill=INK, bold=False):
    return ('<text x="%.1f" y="%.1f" text-anchor="%s" font-size="%s" fill="%s"%s>%s</text>'
            % (x, y, anchor, size, fill, ' font-weight="600"' if bold else "", e(says)))


def _step(span):
    """A tick step a person would choose for this span."""
    span = max(float(span or 0), 1e-9)
    base = 10 ** math.floor(math.log10(span / 4.0))
    for k in (1, 2, 5, 10):
        if span / (k * base) <= 6:
            return k * base
    return base * 10


def _ticks(low, high):
    step = _step(high - low)
    first = math.floor(low / step) * step
    out, at = [], first
    while at <= high + step * 0.001:
        if at >= low - step * 0.001:
            out.append(at)
        at += step
    return out


def line_chart(series, x_label="", y_label="", digits=2, y_range=None):
    """One line per series over a shared x axis: a trajectory, where it settled and where it did
    not. ``series`` is [{name, points: [(x, y)]}]; nothing to draw gives an empty string."""
    series = [s for s in series or [] if len(s.get("points") or []) > 1]
    if not series:
        return ""
    xs = [p[0] for s in series for p in s["points"]]
    ys = [p[1] for s in series for p in s["points"]]
    x0, x1 = min(xs), max(xs)
    y0, y1 = (y_range if y_range else (min(ys), max(ys)))
    if y1 - y0 < 1e-9:
        y0, y1 = y0 - 0.5, y1 + 0.5
    yticks, xticks = _ticks(y0, y1), _ticks(x0, x1)
    width, height, bottom, top = 640, 300, 44, 18
    left = _gutter([num(at, digits) for at in yticks], 56)
    right = 16 if len(series) < 2 else 120
    sx = lambda v: left + (width - left - right) * (v - x0) / float(x1 - x0 or 1)     # noqa: E731
    sy = lambda v: height - bottom - (height - bottom - top) * (v - y0) / float(y1 - y0)  # noqa: E731
    out = [_open(width, height)]
    for at in yticks:
        y = sy(at)
        out.append('<line x1="%d" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="0.6"/>'
                   % (left, y, width - right, y, GRID))
        out.append(_text(left - 6, y + 5, num(at, digits), "end", LABEL, PALE))
    for at in xticks:
        out.append(_text(sx(at), height - bottom + 17, num(at, 0), "middle", LABEL, PALE))
    for n, one in enumerate(series):
        color = SERIES[n % len(SERIES)]
        path = " ".join("%s%.1f,%.1f" % ("M" if i == 0 else "L", sx(x), sy(y))
                        for i, (x, y) in enumerate(one["points"]))
        out.append('<path d="%s" fill="none" stroke="%s" stroke-width="1.8"/>' % (path, color))
        if len(series) > 1:
            # 18 to a row, not 16: a 13 px name needs the room, and two names touching is worse
            # than a legend one row taller.
            out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="1.8"/>'
                       % (width - right + 8, top + 7 + n * 18, width - right + 26,
                          top + 7 + n * 18, color))
            out.append(_text(width - right + 31, top + 12 + n * 18,
                             _fit(one.get("name") or "", width - (width - right + 31) - 4),
                             "start", LABEL, INK))
    out.append('<line x1="%d" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s"/>'
               % (left, height - bottom, width - right, height - bottom, INK))
    out.append(_text((left + width - right) / 2.0, height - 7, x_label, "middle", LABEL, PALE))
    mid = (height - bottom + top) / 2.0
    out.append('<text x="15" y="%.1f" text-anchor="middle" font-size="%d" fill="%s" '
               'transform="rotate(-90 15 %.1f)">%s</text>'
               % (mid, LABEL, PALE, mid, e(_fit(y_label, height - bottom - top))))
    out.append("</svg>")
    return "".join(out)


def value_chart(values, x_label="", y_label="", mean_at=None, bands=(), digits=1, y_range=None,
                marks=()):
    """One value per position along a chain: where a terminus frays or a loop never settled.

    ``bands`` shades thresholds behind the line ([(from, to, color)]); ``mean_at`` draws the mean
    as a dashed rule; ``marks`` draws a labeled vertical rule, which is how the chains of a
    complex are separated.
    """
    values = [v for v in (values or [])]
    if len(values) < 2:
        return ""
    y0, y1 = (y_range if y_range else (min(values), max(values)))
    if y1 - y0 < 1e-9:
        y0, y1 = y0 - 0.5, y1 + 0.5
    yticks = _ticks(y0, y1)
    width, height, bottom, top = 640, 250, 42, 16
    left = _gutter([num(at, digits) for at in yticks], 56)
    sx = lambda i: left + (width - left - 16) * i / float(max(len(values) - 1, 1))   # noqa: E731
    sy = lambda v: height - bottom - (height - bottom - top) * (v - y0) / float(y1 - y0)  # noqa: E731
    out = [_open(width, height)]
    for low, high, color in bands or ():
        low, high = max(low, y0), min(high, y1)
        if high > low:
            out.append('<rect x="%d" y="%.1f" width="%.1f" height="%.1f" fill="%s" '
                       'fill-opacity="0.18"/>'
                       % (left, sy(high), width - left - 16, sy(low) - sy(high), color))
    for at in yticks:
        y = sy(at)
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="0.6"/>'
                   % (left, y, width - 16, y, GRID))
        out.append(_text(left - 6, y + 5, num(at, digits), "end", LABEL, PALE))
    path = " ".join("%s%.1f,%.1f" % ("M" if i == 0 else "L", sx(i), sy(v))
                    for i, v in enumerate(values))
    out.append('<path d="%s" fill="none" stroke="%s" stroke-width="1.5"/>' % (path, MARK))
    if mean_at is not None:
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="1.1" '
                   'stroke-dasharray="5 3"/>' % (left, sy(mean_at), width - 16, sy(mean_at), INK))
    for at, label in marks or ():
        if 0 < at < len(values):
            out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
                       'stroke-width="0.9"/>' % (sx(at), top, sx(at), height - bottom, PALE))
        where = sx(min(at, len(values) - 1))
        room = min(where - left, width - 16 - where) * 2.0
        out.append(_text(where, top + 11, _fit(label, max(room, 30)), "middle", LABEL, PALE))
    out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s"/>'
               % (left, height - bottom, width - 16, height - bottom, INK))
    for at in (0, len(values) // 2, len(values) - 1):
        out.append(_text(sx(at), height - bottom + 17, str(at + 1), "middle", LABEL, PALE))
    out.append(_text((left + width - 16) / 2.0, height - 7, x_label, "middle", LABEL, PALE))
    mid = (height - bottom + top) / 2.0
    out.append('<text x="15" y="%.1f" text-anchor="middle" font-size="%d" fill="%s" '
               'transform="rotate(-90 15 %.1f)">%s</text>'
               % (mid, LABEL, PALE, mid, e(_fit(y_label, height - bottom - top))))
    out.append("</svg>")
    return "".join(out)


def stacked_chart(rows, parts, note_key=None, axis=""):
    """One bar per row, split into fractions that sum to one: a composition, read down the page.

    ``rows`` is [{name, <part key>: fraction, <note_key>: anything}]; ``parts`` is
    [(key, label, color)].
    """
    rows = [r for r in rows or []]
    if not rows:
        return ""
    width, right, row_h = 640, 92, 32
    left = min(int(width * 0.4), _gutter([r.get("name") or "" for r in rows], 150))
    height = 34 + len(rows) * row_h + 24
    bar = width - left - right
    out = [_open(width, height)]
    for n, (_, label, color) in enumerate(parts):
        out.append('<rect x="%.1f" y="8" width="11" height="11" fill="%s"/>'
                   % (left + n * 104, color))
        out.append(_text(left + n * 104 + 16, 19, _fit(label, 104 - 20), "start", LABEL, INK))
    for i, row in enumerate(rows):
        y = 34 + i * row_h
        out.append(_text(left - 8, y + 15, _fit(row.get("name") or "", left - 8), "end", LABEL))
        at = float(left)
        for key, _, color in parts:
            got = number(row.get(key)) or 0.0
            run = bar * max(0.0, min(1.0, got))
            if run > 0.3:
                out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="17" fill="%s" '
                           'fill-opacity="0.85"/>' % (at, y, run, color))
            at += run
        if note_key and row.get(note_key) is not None:
            out.append(_text(left + bar + 8, y + 15,
                             _fit(row[note_key], right - 12), "start", LABEL, INK))
    out.append('<line x1="%d" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s"/>'
               % (left, height - 22, left + bar, height - 22, INK))
    # The end ticks are anchored into the bar rather than centred on its ends, so each stays
    # inside the drawing and the axis label after the last one has room: centred, "1.0" reached
    # twelve pixels past the bar and sat on top of the axis label eight pixels after it.
    for at, anchor in ((0.0, "start"), (0.5, "middle"), (1.0, "end")):
        out.append(_text(left + bar * at, height - 7, num(at, 1), anchor, LABEL, PALE))
    if axis:
        out.append(_text(left + bar + 8, height - 7, _fit(axis, right - 10), "start", LABEL, PALE))
    out.append("</svg>")
    return "".join(out)


def ranked_chart(points, axis="", digits=1, root=False, mark_key="colour", top=60):
    """One bar per thing, best first: which swarm, which filter, which round.

    ``points`` is [{name, v, color, note, dim}]. ``root`` draws the bars on a square-root scale,
    which keeps a 400-to-6 narrowing readable; the values are printed, which is what is read off
    it.
    """
    points = [p for p in points or [] if number(p.get("v")) is not None][:top]
    if not points:
        return ""
    highest = max(abs(number(p["v"])) for p in points) or 1.0
    width, right, row_h = 640, 86, 24
    # Up to two fifths of the chart for the names, which fits about thirty characters, and the
    # bar keeps the rest. Past that a name is shortened, because a bar nobody can compare is
    # worse than a name nobody can read in full.
    left = min(int(width * 0.4), _gutter([p.get("name") or "" for p in points], 132))
    height = 10 + len(points) * row_h + 26
    bar = width - left - right
    out = [_open(width, height)]
    for i, point in enumerate(points):
        y = 8 + i * row_h
        got = abs(number(point["v"]))
        run = bar * (math.sqrt(got / highest) if root else got / highest)
        color = point.get(mark_key) or MARK
        out.append('<rect x="%d" y="%.1f" width="%.1f" height="14" fill="%s" fill-opacity="%s"/>'
                   % (left, y, max(run, 1.2), color, "0.35" if point.get("dim") else "0.8"))
        out.append(_text(left - 8, y + 14, _fit(point.get("name") or "", left - 8), "end",
                          LABEL))
        says = "%s%s" % (num(point["v"], digits),
                         "  " + str(point["note"]) if point.get("note") else "")
        # The value is written at the end of its own bar, so the room left for it is whatever is
        # between there and the edge - not the `right` margin, which only the longest bar uses.
        out.append(_text(left + run + 7, y + 14, _fit(says, width - (left + run + 7) - 4),
                         "start", LABEL, INK))
    out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s"/>'
               % (left, height - 20, left + bar, height - 20, INK))
    if axis:
        out.append(_text(left, height - 5, _fit(axis, width - left - 4), "start", LABEL, PALE))
    out.append("</svg>")
    return "".join(out)


def scatter_chart(points, x_label="", y_label="", digits=2):
    """One point per thing on two measures, the chosen ones marked and the first labeled: whether
    what was kept sits in a cluster or is the only point in a desert.

    ``points`` is [{x, y, name, pick, label}].
    """
    points = [p for p in points or []
              if number(p.get("x")) is not None and number(p.get("y")) is not None]
    if len(points) < 2:
        return ""
    xs = [number(p["x"]) for p in points]
    ys = [number(p["y"]) for p in points]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    if x1 - x0 < 1e-9:
        x0, x1 = x0 - 0.5, x1 + 0.5
    if y1 - y0 < 1e-9:
        y0, y1 = y0 - 0.5, y1 + 0.5
    yticks, xticks = _ticks(y0, y1), _ticks(x0, x1)
    width, height, bottom, top = 620, 320, 44, 18
    left = _gutter([num(at, digits) for at in yticks], 58)
    sx = lambda v: left + (width - left - 18) * (v - x0) / float(x1 - x0)         # noqa: E731
    sy = lambda v: height - bottom - (height - bottom - top) * (v - y0) / float(y1 - y0)  # noqa: E731
    out = [_open(width, height)]
    for at in yticks:
        y = sy(at)
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="0.6"/>'
                   % (left, y, width - 18, y, GRID))
        out.append(_text(left - 6, y + 5, num(at, digits), "end", LABEL, PALE))
    for at in xticks:
        out.append(_text(sx(at), height - bottom + 17, num(at, digits), "middle", LABEL, PALE))
    for point in points:
        picked = bool(point.get("pick"))
        out.append('<circle cx="%.1f" cy="%.1f" r="%s" fill="%s" opacity="%s"><title>%s</title>'
                   "</circle>"
                   % (sx(number(point["x"])), sy(number(point["y"])), "4.2" if picked else "2.6",
                      MARK if picked else PALE, "0.85" if picked else "0.5",
                      e(point.get("name") or "")))
        if point.get("label"):
            # Written to the right of its point, unless there is no room there: a label beside a
            # point near the right edge used to be drawn straight off the drawing.
            at_x, at_y = sx(number(point["x"])), sy(number(point["y"]))
            room = width - 18 - (at_x + 7)
            if _wide(point["label"]) <= room:
                out.append(_text(at_x + 7, at_y - 6, point["label"], "start", LABEL, INK))
            else:
                out.append(_text(at_x - 7, at_y - 6,
                                 _fit(point["label"], at_x - 7 - left), "end", LABEL, INK))
    out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s"/>'
               % (left, height - bottom, width - 18, height - bottom, INK))
    out.append(_text((left + width - 18) / 2.0, height - 7, x_label, "middle", LABEL, PALE))
    mid = (height - bottom + top) / 2.0
    out.append('<text x="15" y="%.1f" text-anchor="middle" font-size="%d" fill="%s" '
               'transform="rotate(-90 15 %.1f)">%s</text>'
               % (mid, LABEL, PALE, mid, e(_fit(y_label, height - bottom - top))))
    out.append("</svg>")
    return "".join(out)


def histogram(values, axis="", bins=24, keep=None, digits=0):
    """How a measure is spread over everything, with the kept range marked."""
    values = [number(v) for v in values or [] if number(v) is not None]
    if len(values) < 3:
        return ""
    low, high = min(values), max(values)
    if high - low < 1e-9:
        high = low + 1.0
    counts = [0] * bins
    for got in values:
        counts[min(bins - 1, int((got - low) / (high - low) * bins))] += 1
    width, height, bottom, top = 620, 230, 42, 16
    most = max(counts) or 1
    left = _gutter(["{:,}".format(most)], 48)
    step = (width - left - 16) / float(bins)
    out = [_open(width, height)]
    if keep and len(keep) == 2:
        a = left + (width - left - 16) * (max(keep[0], low) - low) / float(high - low)
        b = left + (width - left - 16) * (min(keep[1], high) - low) / float(high - low)
        out.append('<rect x="%.1f" y="%d" width="%.1f" height="%.1f" fill="%s" '
                   'fill-opacity="0.16"/>' % (a, top, max(b - a, 1.5), height - bottom - top, MARK))
    for i, got in enumerate(counts):
        tall = (height - bottom - top) * got / float(most)
        if tall > 0:
            out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" '
                       'fill-opacity="0.75"/>'
                       % (left + i * step, height - bottom - tall, max(step - 1.2, 1.0), tall,
                          PALE))
    for at in _ticks(low, high):
        x = left + (width - left - 16) * (at - low) / float(high - low)
        out.append(_text(x, height - bottom + 17, num(at, digits), "middle", LABEL, PALE))
    out.append(_text(left - 6, top + 9, "{:,}".format(most), "end", LABEL, PALE))
    out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s"/>'
               % (left, height - bottom, width - 16, height - bottom, INK))
    out.append(_text((left + width - 16) / 2.0, height - 7, _fit(axis, width - 8), "middle",
                      LABEL, PALE))
    out.append("</svg>")
    return "".join(out)


# ------------------------------------------------------------------ a matrix as a picture

def grey_png(rows, low=None, high=None, scale=3, lines=()):
    """A matrix as a PNG data URI, dark where the value is small.

    Written with zlib rather than drawn with matplotlib: the page is one stdlib process on a login
    node, and a predicted-aligned-error map is a picture of numbers, not a chart. ``lines`` draws
    a rule across and down at each index, which is how the chains of a complex are separated.
    """
    if not rows or not rows[0]:
        return ""
    size = len(rows)
    low = min(min(r) for r in rows) if low is None else low
    high = max(max(r) for r in rows) if high is None else high
    span = float(high - low) or 1.0
    scale = max(1, int(scale))
    edge = set(int(at) for at in lines or ())
    out = bytearray()
    for y in range(size):
        line = bytearray()
        for x in range(size):
            got = (float(rows[y][x]) - low) / span
            shade = int(255 * max(0.0, min(1.0, got)))
            if x in edge or y in edge:
                shade, pixel = 0, (40, 86, 219)
            else:
                pixel = (shade, shade, min(255, shade + 18))
            line += bytes(pixel) * scale
        for _ in range(scale):
            out += b"\x00" + line

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    side = size * scale
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(out), 6))
           + chunk(b"IEND", b""))
    import base64
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


# ------------------------------------------------------------------ the appendix every tool owes

def byline(app, version, commit="", written="", submitted="", who=""):
    """Who made this report, with what, and who ran the thing it is about."""
    import time
    bits = ["%s %s%s" % (app, version or "version not recorded",
                         " (%s)" % commit[:9] if commit else "")]
    bits.append("report written %s" % (written or time.strftime("%d %B %Y")))
    if submitted or who:
        bits.append("run submitted %s%s" % (submitted[:16] or "on a date it did not record",
                                            " by " + who if who else ""))
    return "; ".join(bits)


def sacct(job_id, fields="JobID,State,Elapsed,TotalCPU,ReqMem,Partition,NodeList"):
    """What Slurm still remembers of a job, or {}. It forgets after a few months, which is why
    every tool here is asked to write its own finished.json as well."""
    if not str(job_id or "").strip():
        return {}
    try:
        got = subprocess.run(["sacct", "-j", str(job_id), "-n", "-P", "-o", fields],
                             capture_output=True, text=True, timeout=25)
    except (OSError, subprocess.SubprocessError):
        return {}
    for line in (got.stdout or "").splitlines():
        parts = line.split("|")
        if len(parts) == len(fields.split(",")) and "." not in parts[0]:
            return dict(zip(fields.split(","), parts))
    return {}


def tail(path, lines=60, cap=200_000):
    """The last lines of a file, without reading a long log from its beginning."""
    try:
        size = os.path.getsize(path)
        with open(path, errors="replace") as handle:
            handle.seek(max(0, size - cap))
            text = handle.read()
    except OSError:
        return ""
    if size > cap:
        text = text.split("\n", 1)[-1]
    return "\n".join(text.splitlines()[-lines:])


def facts(rows):
    """An unnumbered two-column block: the settings, as they were asked and as they ran."""
    return {"kind": "facts", "rows": [[label, value] for label, value in rows if label]}


def missing(what, fix=""):
    """A caption for a figure or table with no result to state: what is absent, and what would
    fill it. The rule asks for this rather than a title."""
    return sentences([what, fix]) if fix else sentences([what])
