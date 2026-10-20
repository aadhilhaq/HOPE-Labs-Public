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
    runs = [r for r in (record.card(nid).get("runs") or []) if isinstance(r, dict)]
    if not runs:
        return ""
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
    # Every run, not the first twelve: this is the comparison the flow was drawn to make, and a
    # table that stops halfway down a batch of twenty is the wrong half.
    cells = "".join("<th>%s</th>" % _esc(c) for c in head)
    body = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % _esc(c) for c in row) for row in rows)
    return ("<h3>its %d run%s, side by side</h3><table><thead><tr>%s</tr></thead>"
            "<tbody>%s</tbody></table>"
            % (len(rows), "" if len(rows) == 1 else "s", cells, body))


def _mean_of(values):
    """The mean of what the replicates said, with how many said it, or an empty cell."""
    if not values:
        return ""
    mean = sum(float(v) for v in values) / len(values)
    return "%.1f (%d rep%s)" % (mean, len(values), "" if len(values) == 1 else "s")


def build(where):
    """The page, as text. `where` is a flow's own folder."""
    where = os.path.abspath(str(where))
    with open(os.path.join(where, "flow.json"), encoding="utf-8") as fh:
        flow = Flow.from_json(fh.read(), started=st.with_state(where))
    record = st.State.read(where)
    summary = record.summary(flow)
    rows = summary["cards"] if isinstance(summary, dict) else summary

    out = ["<!doctype html><html lang='en'><head><meta charset='utf-8'>",
           "<meta name='viewport' content='width=device-width,initial-scale=1'>",
           "<title>%s</title><style>%s</style></head><body><main>" % (_esc(flow.name or "flow"),
                                                                      CSS)]
    done = sum(1 for r in rows if r["state"] == "done")
    out.append("<h1>%s</h1>" % _esc(flow.name or "a flow"))
    out.append("<p class='lead'>%d of %d cards finished. Written %s from %s</p>"
               % (done, len(rows), time.strftime("%Y-%m-%d %H:%M"), _esc(where)))

    out.append("<h2>The chain</h2><div class='card'><p>")
    out.append(" &rarr; ".join("%s <span class='sp %s'>%s</span>"
                               % (_esc(r["name"]), _esc(r["state"]), _esc(r["state"]))
                               for r in rows))
    out.append("</p></div>")

    for row in rows:
        out.append("<h2>%s <span class='sp %s'>%s</span></h2>"
                   % (_esc(row["name"]), _esc(row["state"]), _esc(row["state"])))
        out.append("<div class='card'>")
        # A card that has not started has nothing of its own to say, and an empty box under its
        # name read as something missing from the page.
        if not (row.get("note") or row.get("jobs") or row.get("rundir")):
            out.append("<p class='files'>%s</p>" % {
                "waiting": "Not started: it waits for the cards before it.",
                "ready": "Not started: its turn comes when the cards before it finish.",
                "stopped": "Stopped before it started."}.get(row["state"], "Nothing yet."))
        if row.get("note"):
            out.append("<p>%s</p>" % _esc(row["note"]))
        bits = []
        if row.get("jobs"):
            bits.append("job%s %s" % ("s" if len(row["jobs"]) > 1 else "",
                                      " ".join(_esc(j) for j in row["jobs"])))
        if row.get("rundir"):
            bits.append("<code>%s</code>" % _esc(row["rundir"]))
        if bits:
            out.append("<p class='files'>%s</p>" % " &middot; ".join(bits))

        if row["card"] == "hopemd":
            out.append(_runs_side_by_side(record, row["id"]))
        where_run = row.get("rundir") or ""
        if where_run and os.path.isdir(where_run):
            for name, head, table in _tables(where_run)[:2]:
                out.append(_table_html(name, head, table))
            got = _files(where_run)
            if got:
                out.append("<h3>Files worth keeping</h3><p class='files'>")
                out.append("<br>".join("<b>%s</b> &nbsp; %s" % (_esc(n), _size(s))
                                       for n, s in got))
                out.append("</p>")
            own = os.path.join(where_run, "export", "report.html")
            if os.path.isfile(own):
                out.append("<p class='files'>This tool writes its own report, which is fuller "
                           "than anything here: <b>export/report.html</b> in the folder above.</p>")
        elif where_run:
            out.append("<p class='files'>Its folder is not readable from here.</p>")
        out.append("</div>")

    out.append("<div class='foot'>Written by HOPE-flow from the flow's own record. Each tool's "
               "own documentation says how to cite it; this page is not citable. A figure made "
               "in HOPE Labs' structure viewer cites 3Dmol.js (Rego and Koes, Bioinformatics "
               "2015), and one that shows a surface cites MSMS as well (Sanner, Olson and "
               "Spehner, Biopolymers 1996).</div>")
    out.append("</main></body></html>")
    return "\n".join(out)


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
