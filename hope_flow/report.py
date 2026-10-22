"""A flow's results on one page, because a flow's results are in five folders.

By Aadhil Haq

Each tool writes its own account of its own run, and each is good. What nobody had was the whole
of a flow: the chain as it was drawn, what each card did, the tables it left and where its files
are. Reading that meant opening five folders and knowing what each tool calls things.

So this walks the flow's record and writes report.html beside it. It is deliberately not clever
about any one tool: it shows the cards in the order they ran, the tables each one left with their
first rows, and the files worth fetching with their sizes. A tool that writes its own report is
named rather than repeated - HOPE-MD's export/report.html is better than anything that could be
rebuilt here from the same numbers.

The page is self-contained: no stylesheet, no script, no image it does not carry. That is what
lets the launcher fetch it and open it in a tab, and what lets it be sent to somebody who has no
account on the cluster.

    python3 -m hope_flow.report <flowdir>        # writes <flowdir>/report.html
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time

from . import state as st
from .flow import Flow

#: Rows of a table to show. Enough to see the shape of a ranking and what came top; the table
#: itself is beside it for anyone who wants the rest.
ROWS = 12

#: Columns, likewise. A metric panel is forty columns wide and unreadable at that width on a page.
COLUMNS = 12

#: Files worth naming even though the page cannot carry them: what somebody would actually want
#: out of a run folder.
WORTH_NAMING = (".mp4", ".pdb", ".cif", ".png", ".svg", ".pdf", ".tsv", ".csv", ".html")

CSS = """
:root{--ground:#f5f8f8;--card:#fff;--ink:#101819;--dim:#5f6f71;--line:#dce5e5;--teal:#0d5c6b;
 --rust:#c2603a;--good:#146b47;--wait:#eef4f4}
@media (prefers-color-scheme:dark){:root{--ground:#0d1315;--card:#151d1f;--ink:#e7eeef;
 --dim:#93a4a6;--line:#233032;--teal:#5fb3c4;--rust:#e08a63;--good:#4cbf94;--wait:#101a1c}}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);font:14px/1.6 "IBM Plex Sans",
 -apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1000px;margin:0 auto;padding:28px 20px 64px}
h1{font-size:23px;margin:0 0 4px}
h2{font-size:17px;margin:30px 0 8px;padding-top:14px;border-top:1px solid var(--line)}
h3{font-size:13px;margin:16px 0 4px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em}
.lead{color:var(--dim);margin:0 0 18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;
 margin:12px 0}
.sp{display:inline-block;font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;
 padding:2px 7px;border-radius:999px;background:var(--wait);color:var(--dim);font-weight:600}
.sp.done{background:#e3f1ea;color:var(--good)}
.sp.failed,.sp.stopped{background:#f9ebe5;color:var(--rust)}
.sp.running{background:#e3eef0;color:var(--teal)}
.sp.queued{background:#f6eedf;color:#9a6a12}
@media (prefers-color-scheme:dark){.sp.done{background:#15291f}
 .sp.failed,.sp.stopped{background:#2a1e19}.sp.running{background:#16292e}
 .sp.queued{background:#2a2414;color:#d9a54a}}
code,pre{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;font-size:11.5px}
pre{background:var(--wait);border:1px solid var(--line);border-radius:7px;padding:9px 11px;
 overflow:auto;white-space:pre-wrap;word-break:break-word}
table{border-collapse:collapse;width:100%;font-size:11.5px;margin-top:4px}
th,td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line);
 white-space:nowrap;font-variant-numeric:tabular-nums}
th{color:var(--dim);font-weight:600}
.files{font-size:11.5px;color:var(--dim)}
.files b{color:var(--ink);font-weight:600}
.foot{margin-top:34px;padding-top:12px;border-top:1px solid var(--line);color:var(--dim);
 font-size:11.5px}
@media print{body{background:#fff}.card{break-inside:avoid}}
"""


def _esc(text):
    return html.escape(str(text if text is not None else ""))


def _size(n):
    for at, name in ((1 << 30, "GB"), (1 << 20, "MB"), (1 << 10, "kB")):
        if n >= at:
            return "%.1f %s" % (n / at, name)
    return "%d B" % n


def _tables(where):
    """The delimited tables at the top of a run folder, newest first, with their rows."""
    out = []
    try:
        names = sorted(os.listdir(where))
    except OSError:
        return out
    for name in names:
        if not name.lower().endswith((".tsv", ".csv")):
            continue
        path = os.path.join(where, name)
        sep = "\t" if name.lower().endswith(".tsv") else ","
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = [next(fh) for _ in range(ROWS + 1)]
        except StopIteration:
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            continue
        rows = [l.rstrip("\n").split(sep) for l in lines if l.strip()]
        if len(rows) > 1:
            out.append((name, rows[0][:COLUMNS], [r[:COLUMNS] for r in rows[1:]]))
    return out


def _files(where):
    """What is in a run folder that somebody would want out of it, with sizes."""
    out = []
    for here, _dirs, names in os.walk(where):
        if here[len(where):].count(os.sep) > 2:
            continue
        for name in names:
            if not name.lower().endswith(WORTH_NAMING):
                continue
            path = os.path.join(here, name)
            try:
                out.append((os.path.relpath(path, where), os.path.getsize(path)))
            except OSError:
                continue
        if len(out) > 400:
            break
    out.sort(key=lambda row: -row[1])
    return out[:40]


def _table_html(name, head, rows):
    cells = "".join("<th>%s</th>" % _esc(c) for c in head)
    body = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % _esc(c) for c in row) for row in rows)
    return ("<h3>%s, first %d rows</h3><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>"
            % (_esc(name), len(rows), cells, body))


def _runs_side_by_side(record, nid):
    """The simulation card's runs as one table: what each was, and what came of it.

    A simulation card is one run per structure, so a flow of twenty runs left twenty folders and
    twenty reports and nothing that put them next to each other - which is the comparison the
    whole flow was drawn to make. The rows are the card's own record of what it queued (the
    structure, the score it arrived with, anything it was told to differ in) and each run's own
    summary.json, which is where its binding energy and its interface RMSD are.
    """
    head, rows = _runs_rows(record, nid)
    if not rows:
        return ""
    # Every run, not the first twelve: this is the comparison the flow was drawn to make, and a
    # table that stops halfway down a batch of twenty is the wrong half.
    cells = "".join("<th>%s</th>" % _esc(c) for c in head)
    body = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % _esc(c) for c in row) for row in rows)
    return ("<h3>its %d run%s, side by side</h3><table><thead><tr>%s</tr></thead>"
            "<tbody>%s</tbody></table>"
            % (len(rows), "" if len(rows) == 1 else "s", cells, body))


def _runs_rows(record, nid):
    """(the headings, the rows) of the simulation card's runs.

    Separated from the HTML above so the flow's report can lay the same numbers out as a proper
    table, with a caption, while `python3 -m hope_flow.report` keeps writing the page it always
    has.
    """
    runs = [r for r in (record.card(nid).get("runs") or []) if isinstance(r, dict)]
    if not runs:
        return [], []
    head = ["structure", "rank", "arrived with", "its own settings", "MM-GBSA kcal/mol",
            "interface RMSD Å", "run"]
    rows = []
    for one in runs:
        got = {}
        try:
            with open(os.path.join(str(one.get("path") or ""), "analysis", "summary.json"),
                      encoding="utf-8") as fh:
                got = json.load(fh)
        except (OSError, ValueError):
            got = {}
        reps = [r for r in (got.get("replicates") or {}).values() if isinstance(r, dict)]
        energies = [r["mmgbsa"].get("mean") for r in reps
                    if isinstance(r.get("mmgbsa"), dict) and r["mmgbsa"].get("mean") is not None]
        rmsd = [r["rmsd_iface"].get("mean") for r in reps
                if isinstance(r.get("rmsd_iface"), dict)
                and r["rmsd_iface"].get("mean") is not None]
        mine = one.get("settings") or {}
        rows.append([
            str(one.get("name") or os.path.basename(str(one.get("path") or "")) or "a run"),
            "" if one.get("rank") is None else str(one["rank"]),
            "" if one.get("score") is None else "%g" % float(one["score"]),
            ", ".join("%s %s" % (k, v) for k, v in sorted(mine.items())) or "as the card",
            _mean_of(energies), _mean_of(rmsd),
            os.path.basename(str(one.get("path") or "")),
        ])
    return head, rows


def _mean_of(values):
    """The mean of what the replicates said, with how many said it, or an empty cell."""
    if not values:
        return ""
    mean = sum(float(v) for v in values) / len(values)
    return "%.1f (%d rep%s)" % (mean, len(values), "" if len(values) == 1 else "s")


#: The states' own colours, which the page this replaces carried and a reader reads before any
#: word on the page. reportkit's stylesheet knows nothing of a flow's states, so they are added
#: after it rather than by editing the shared module, which is copied and never edited.
STATE_CSS = """
.sp{display:inline-block;padding:0 .45em;border-radius:3px;font-size:.92em;
    border:1px solid currentColor}
.sp.done{color:#1d6b4b}
.sp.failed{color:#9b2c20}
.sp.stopped{color:#9a5b23}
.sp.running{color:#14459e}
.sp.queued{color:#5b6777}
.sp.ready{color:#5b6777}
.sp.waiting{color:#7a838f}
@media print{.sp{border-color:#888}}
"""


def render(doc, css):
    """The contract's document as one self-contained page, laid out as reportkit lays it out.

    A flow's report cannot be served the way every other report in the lab is. The pages that
    serve a report run on a login node beside the run folder they read; the launcher that draws a
    flow runs on the person's own computer and reaches the cluster over SSH, so there is no server
    next to a flow's folder to answer /api/report. And the one property this page has always had
    and must keep is that it can be sent to somebody with no account on the cluster at all.

    So the document is laid out here, on the cluster, into static HTML that carries reportkit's own
    stylesheet inline. That buys the numbering, the captions, the print rules, the A4 page breaks
    and the tables held whole on a page - everything reportkit does to a document that does not
    need a server. What it cannot buy is the bar: the zoom and the section tick boxes change the
    document, and changing it needs the server that is not there. The options are therefore fixed
    when the page is written, and Save as PDF is the browser's own print.

    If a flow page is ever served from a login node, this should become a thin wrapper around
    reportkit proper, exactly as the tools' own reports are.
    """
    nums = {"table": {}, "figure": {}}

    def number_blocks(blocks):
        for block in blocks or []:
            if not isinstance(block, dict):
                continue
            kind = block.get("kind")
            if kind in ("table", "figure") and block.get("id"):
                nums[kind][block["id"]] = len(nums[kind]) + 1

    def walk(sections):
        for one in sections or []:
            number_blocks(one.get("blocks"))
            walk(one.get("sections"))

    walk(doc.get("sections"))
    number_blocks(doc.get("notes"))
    for one in doc.get("appendix") or []:
        number_blocks(one.get("blocks"))

    def refs(html):
        """{{table:id}} and {{figure:id}} become that block's number, as reportkit resolves them."""
        def swap(found):
            kind, name = found.group(1), found.group(2)
            got = nums.get(kind, {}).get(name)
            return ("%s %d" % (kind.capitalize(), got) if got
                    else "a %s not in this report" % kind)
        return re.sub(r"\{\{(table|figure):([^}]+)\}\}", swap, str(html or ""))

    def cell(column, row):
        value = row.get(column["key"])
        if isinstance(value, dict) and "html" in value:
            return refs(value["html"])
        if value is None or value == "":
            return _esc(column.get("empty") or row.get("_empty") or "")
        if column.get("num"):
            try:
                digits = int(column.get("digits", 1))
                out = "%.*f" % (digits, float(value))
                if abs(float(value)) >= 10000:
                    whole, _, part = out.lstrip("-").partition(".")
                    out = (("-" if float(value) < 0 else "") + "{:,}".format(int(whole))
                           + ("." + part if part else ""))
                return _esc(out.replace("-", "\u2212"))
            except (TypeError, ValueError):
                pass
        return _esc(value)

    def table(block):
        columns = block.get("columns") or []
        rows = block.get("rows") or []
        keep = " rk-keep" if len(rows) <= 40 else ""
        head = "".join('<th class="%s"%s>%s</th>'
                       % ("rk-num" if c.get("num") else "",
                          ' title="%s"' % _esc(c["help"]) if c.get("help") else "",
                          _esc(c.get("label") or c.get("key")))
                       for c in columns)
        body = []
        for row in rows:
            body.append('<tr class="%s">%s</tr>'
                        % (_esc(row.get("_class") or ""),
                           "".join('<td class="%s">%s</td>'
                                   % ("rk-num" if c.get("num") else "", cell(c, row))
                                   for c in columns)))
        if not body:
            body.append('<tr><td colspan=%d class=rk-why>%s</td></tr>'
                        % (max(1, len(columns)), _esc(block.get("empty")
                                                      or "Nothing to show.")))
        got = nums["table"].get(block.get("id"))
        caption = ('<caption><b>Table %d.</b> %s</caption>' % (got, refs(block.get("caption")))
                   if got and block.get("caption") else "")
        return ('<div class=rk-tablebox><table class="rk-table%s">%s<thead><tr>%s</tr></thead>'
                '<tbody>%s</tbody></table>%s</div>'
                % (keep, caption, head, "".join(body),
                   '<p class=rk-note>%s</p>' % refs(block["note"]) if block.get("note") else ""))

    def figure(block):
        body = ""
        if block.get("svg"):
            body = '<div class=rk-svg>%s</div>' % block["svg"]
        elif block.get("png"):
            body = '<img class=rk-png alt="" src="%s">' % _esc(block["png"])
        got = nums["figure"].get(block.get("id"))
        caption = ('<figcaption><b>Figure %d.</b> %s</figcaption>'
                   % (got, refs(block.get("caption")))
                   if got and block.get("caption") else "")
        return '<figure class="rk-figure rk-keep">%s%s</figure>' % (body, caption)

    def block_html(block):
        if not isinstance(block, dict):
            return "<p>%s</p>" % refs(block)
        kind = block.get("kind")
        if kind == "p":
            return '<p class="%s">%s</p>' % ("rk-" + _esc(block["tone"]) if block.get("tone")
                                             else "", refs(block.get("html")))
        if kind == "heading":
            return "<h4 class=rk-h4>%s</h4>" % _esc(block.get("text"))
        if kind == "list":
            tag = "ol" if block.get("ordered") else "ul"
            return "<%s class=rk-list>%s</%s>" % (
                tag, "".join("<li>%s</li>" % refs(i) for i in block.get("items") or []), tag)
        if kind == "pre":
            return "<pre class=rk-pre>%s</pre>" % _esc(block.get("text"))
        if kind == "facts":
            return ('<table class="rk-facts rk-keep">%s</table>'
                    % "".join("<tr><th>%s</th><td>%s</td></tr>" % (_esc(k), refs(v))
                              for k, v in block.get("rows") or []))
        if kind == "table":
            return table(block)
        if kind == "figure":
            return figure(block)
        return ""

    def section_html(one, number, level):
        kids = one.get("sections") or []
        tag = "h2" if level == 2 else "h3"
        return ('<section class="rk-section%s" id="rk-s-%s"><%s>%s %s</%s>%s%s</section>'
                % (" rk-newpage" if one.get("newpage") else "", _esc(one.get("id")), tag,
                   number, _esc(one.get("heading")), tag,
                   "".join(block_html(b) for b in one.get("blocks") or []),
                   "".join(section_html(k, "%s.%d" % (number, i + 1), 3)
                           for i, k in enumerate(kids))))

    out = ["<!doctype html><html lang='en'><head><meta charset='utf-8'>",
           "<meta name='viewport' content='width=device-width,initial-scale=1'>",
           "<title>%s</title>" % _esc(doc.get("title") or "flow"),
           "<style>%s%s</style>" % (css, STATE_CSS),
           "</head><body><main id=rk-doc>"]
    out.append('<section class=rk-title>%s<h1>%s</h1>%s%s%s</section>'
               % ('<div class=rk-kicker>%s</div>' % _esc(doc["kicker"]) if doc.get("kicker")
                  else "", _esc(doc.get("title") or "Report"),
                  '<p class=rk-subtitle>%s</p>' % doc["subtitle"] if doc.get("subtitle") else "",
                  '<p class=rk-meta>%s</p>' % _esc(doc["byline"]) if doc.get("byline") else "",
                  '<p class=rk-meta><code>%s</code></p>' % _esc(doc["path"]) if doc.get("path")
                  else ""))
    for block in doc.get("notes") or []:
        out.append(block_html(block))
    count = 0
    for one in doc.get("sections") or []:
        count += 1
        out.append(section_html(one, str(count), 2))
    if doc.get("methods"):
        count += 1
        out.append('<section class=rk-section><h2>%d Methods</h2><div class=rk-methods>%s</div>'
                   % (count, refs(doc["methods"])))
        if doc.get("references"):
            out.append('<ol class=rk-refs>%s</ol>'
                       % "".join("<li>%s</li>" % _esc(r.get("text"))
                                 for r in doc["references"]))
        out.append("</section>")
    for i, one in enumerate(doc.get("appendix") or []):
        out.append('<section class="rk-section rk-newpage"><h2>Appendix %s %s</h2>%s</section>'
                   % (chr(ord("A") + i), _esc(one.get("heading")),
                      "".join(block_html(b) for b in one.get("blocks") or [])))
    # A figure somebody takes out of this page and puts in their own writing has to be citable,
    # and the viewer's own page is not where they will look for it. Said here, as the flow's
    # page said it before this document replaced it.
    out.append("<div class='rk-foot'>Written by HOPE-flow from the flow's own record. Each "
               "tool's own documentation says how to cite it; this page is not citable. A "
               "figure made in HOPE Labs' structure viewer cites 3Dmol.js (Rego and Koes, "
               "Bioinformatics 2015), and one that shows a surface cites MSMS as well "
               "(Sanner, Olson and Spehner, Biopolymers 1996).</div>")
    out.append("</main></body></html>")
    return "\n".join(out)


def build(where):
    """The page, as text. `where` is a flow's own folder.

    The document is reportdata's; the layout is reportkit's stylesheet and render() above. Kept as
    build() because the launcher's POST /api/flow/report and `python3 -m hope_flow.report` both
    call write(), and both must keep working.
    """
    from hope_labs import reporting as _rk

    from . import reportdata
    where = os.path.abspath(str(where))
    # Raised, not rendered: a folder with no flow.json in it is a tool's own run folder or a
    # mistyped path, and `python3 -m hope_flow.report` and the launcher both report that to the
    # person rather than writing them a page about it.
    with open(os.path.join(where, "flow.json"), encoding="utf-8") as fh:
        fh.read(1)
    doc = reportdata.document(where)
    if doc.get("error"):
        return ("<!doctype html><meta charset='utf-8'><title>flow</title>"
                "<p>%s</p>" % _esc(doc["error"]))
    try:
        with open(os.path.join(_rk.STATIC, "reportkit.css"), encoding="ascii") as fh:
            css = fh.read()
    except OSError:
        css = CSS
    return render(doc, css)


def write(where):
    """Put report.html in the flow's folder. Returns its path."""
    text = build(where)
    path = os.path.join(os.path.abspath(str(where)), "report.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def main(argv):
    if len(argv) < 2:
        print("usage: python3 -m hope_flow.report <flowdir>")
        return 2
    print(write(argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
