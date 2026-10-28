"""The report of a whole flow: what went in, what each card made of it, and where it stopped.

By Aadhil Haq

Every other report in the lab writes up one run of one tool. This one writes up a chain of them,
and it is the only place a flow can be read end to end. So it deliberately does NOT re-tell what
each card's own report says: it says what went in, what each card produced, what was carried to
the next card, how many candidates survived each link, what the whole thing cost, and - the
question a flow is actually drawn to answer - where the chain lost what it lost.

The document is the contract's (CONTRACT.md), laid out by reportkit like every other. What it adds
over the self-contained `report.html` that hope_flow.report has written until now: captions from
the run's own numbers, numbered tables and figures, the funnel, a methods section spliced from the
cards that ran, and a verdict sentence for a flow that ended early. What it keeps from it: the
restraint not to duplicate a tool's own report, `_runs_side_by_side` for the simulation card, and
the file list.

Two things shape the design and are worth stating.

A flow report cannot call the cards' servers. On a cluster those pages are not running, so the
methods of each card are gathered by IMPORTING that tool's report module in this same process -
hope_labs.pages.runreport and hope_labs.folding.runreport - and asking
it for the document of that card's own run folder. A card whose module cannot be imported, or
whose run folder cannot be read, is named as such rather than left out silently: see methods().

And a card's run folder may not be readable at all - another user's run, or one cleaned up since.
The page has always said so for such a card and still does, and the rest of the report is printed
regardless, because the point of a flow report is that it can be sent to somebody with no account
on this cluster.

    python -m hope_flow.reportdata <flow folder>        the document, as JSON
"""
from __future__ import annotations

import json
import os
import sys
import time

from hope_labs import reporting as rk

from . import adapters, cards, flow as flowmod, report as pagereport, state as st

#: The bar's choices.
OPTIONS = [
    {"key": "top", "label": "Rows per table", "type": "number", "default": 12, "least": 1,
     "most": 100, "help": "How many rows of each card's own table the report shows"},
    {"key": "cards", "label": "Cards shown", "type": "select", "default": "all",
     "choices": [["all", "every card"], ["ran", "only the cards that ran"]],
     "help": "A flow of ten cards where three ran reads better with the seven left out"},
    {"key": "funnel", "label": "What became of the candidates", "type": "check", "default": True},
    {"key": "graph", "label": "The flow as it was drawn", "type": "check", "default": True},
    {"key": "files", "label": "Files worth keeping", "type": "check", "default": True},
    {"key": "cost", "label": "What it cost", "type": "check", "default": True},
    {"key": "methods", "label": "Methods of every card", "type": "check", "default": True},
    {"key": "appendix", "label": "Appendix", "type": "check", "default": True},
]

#: Each card's report module and the tool name to pass it, for the methods splice. A card not
#: named here has no report of its own yet, which the methods section says rather than implying
#: the card did nothing.
REPORTS = {
    "rfdiffusion": ("hope_labs.pages.runreport", "rfdiffusion"),
    "rfd3": ("hope_labs.pages.runreport", "rfd3"),
    "boltzgen": ("hope_labs.pages.runreport", "boltzgen"),
    "boltz2": ("hope_labs.folding.runreport", "boltz2"),
    "colabfold": ("hope_labs.folding.runreport", "colabfold"),
}

WANTED = {
    "rfdiffusion": ("designs.json",),
    "rfd3": ("designs.json",),
    "boltzgen": ("designs.json",),
}

#: How a card's state reads in a sentence.
SAID = {st.DONE: "finished", st.FAILED: "failed", st.STOPPED: "stopped",
        st.RUNNING: "still running", st.QUEUED: "queued", st.READY: "ready to start",
        st.WAITING: "still waiting"}

STATE_COLOUR = {st.DONE: "#2f9e6e", st.FAILED: "#c0392b", st.STOPPED: "#e8955a",
                st.RUNNING: "#1a56db", st.QUEUED: "#8d9cb3", st.READY: "#8d9cb3",
                st.WAITING: "#c8d0d8"}


class Report:
    """One flow's report, built from flow.json, state/, the logs and each card's own table."""

    def __init__(self, where, raw=None):
        if not os.path.isdir(where or ""):
            raise ValueError("no such flow folder")
        self.where = os.path.abspath(where)
        try:
            with open(os.path.join(self.where, "flow.json"), encoding="utf-8") as handle:
                self.raw_flow = handle.read()
        except OSError as why:
            raise ValueError("this folder holds no flow.json: %s" % why)
        self.doc = json.loads(self.raw_flow)
        self.flow = flowmod.Flow.from_json(self.raw_flow,
                                           started=st.with_state(self.where))
        self.record = st.State.read(self.where)
        self.summary_of = self.record.summary(self.flow)
        self.rows = self.summary_of["cards"]
        self.by_id = {r["id"]: r for r in self.rows}
        self.settings = {n["id"]: (n.get("settings") or {}) for n in self.doc.get("nodes") or []}
        self.opt = rk.options(OPTIONS, raw or {})
        self.shown = [r for r in self.rows
                      if self.opt["cards"] == "all" or r["state"] not in (st.WAITING, st.READY)]

    # ------------------------------------------------------------ reading the record

    def readable(self, row):
        """Whether this card's run folder can be read from here."""
        where = row.get("rundir") or ""
        return bool(where) and os.path.isdir(where) and os.access(where, os.R_OK)

    def carried(self):
        """What each link carried, best from the record and otherwise from the step job's log."""
        out, exact = [], True
        for row in self.rows:
            got = self.record.card(row["id"]).get("carried")
            if got:
                for one in got:
                    out.append(dict(one, _from="the flow's own record"))
                continue
            for line in self.log_lines(row):
                if not line.startswith("from "):
                    continue
                said, _, what = line[len("from "):].partition(": ")
                exact = False
                out.append({"from": said, "from_port": "", "to": row["id"], "to_port": "",
                            "kind": "", "what": what.strip(), "count": _leading_count(what),
                            "dropped": _dropped_from(what), "names": [],
                            "_from": "the step job's log"})
        return out, exact

    def log_lines(self, row, lines=400):
        """The lines of the one log that belongs to this card.

        The record's `step` field exists for exactly this: a card can have four logs
        to its name - still waiting, the failure, a cancellation, and another job finding the card
        already taken - and only the job the record names is the right one to read.
        """
        path = self.log_path(row)
        return rk.tail(path, lines).splitlines() if path else []

    def log_path(self, row):
        folder = os.path.join(self.where, "logs")
        step = str(row.get("step") or "")
        if step:
            named = os.path.join(folder, "%s-%s.out" % (row["card"], step))
            if os.path.isfile(named):
                return named
        found = sorted((os.path.getmtime(os.path.join(folder, n)),
                        os.path.join(folder, n))
                       for n in (os.listdir(folder) if os.path.isdir(folder) else [])
                       if n.startswith(row["card"] + "-") and n.endswith(".out"))
        return found[-1][1] if found else ""

    def why_it_stopped(self):
        """The sentence a flow that ended early owes the reader, from its own log.

        The record's note is empty for the card that actually failed in two of the three flows on
        this cluster - the note went on the card below it - so the reason is taken from the log of
        the card the record names as the step that ran.
        """
        for row in self.rows:
            if row["state"] not in (st.FAILED, st.STOPPED):
                continue
            if row.get("note"):
                return row, row["note"]
            for line in reversed(self.log_lines(row)):
                low = line.strip()
                if low.startswith("this card failed: "):
                    return row, low[len("this card failed: "):]
                if low.startswith("nothing arrived"):
                    return row, low
            return row, ""
        # A card may be stopped below one that failed without being marked itself.
        for row in self.rows:
            for line in reversed(self.log_lines(row)):
                if line.strip().startswith("this card failed: "):
                    return row, line.strip()[len("this card failed: "):]
        return None, ""

    # ------------------------------------------------------------ the document

    def build(self):
        from hope_labs import __version__ as labs_version
        stamp = self.doc.get("written_by") or {}
        doc = {
            "kicker": "Flow report",
            "title": self.summary_of["name"] or os.path.basename(self.where.rstrip(os.sep)),
            "subtitle": self.subtitle(),
            "byline": rk.byline("HOPE Labs", stamp.get("hope_labs") or labs_version,
                                commit=stamp.get("commit") or "",
                                submitted=_when(self.record.data.get("started"))),
            "path": self.where,
            "options": rk.echo(OPTIONS, self.opt),
            "notes": self.notes(),
            "sections": [s for s in (self.summary(), self.funnel(), self.cards_section(),
                                     self.survivors(), self.simulations(), self.cost(),
                                     self.files()) if s],
            "views_hint": "A flow has no viewer of its own: each card's own report is where a "
                          "pose or a structure is drawn, and this report links to them.",
            "appendix": self.appendix() if self.opt["appendix"] else [],
        }
        doc["methods"], doc["references"] = self.methods() if self.opt["methods"] else ("", [])
        return doc

    def subtitle(self):
        names = [r["name"] for r in self.rows]
        return "%s through %s" % (rk.e(self.summary_of["state"]),
                                  rk.e(" → ".join(names)))

    def notes(self):
        out = []
        where, why = self.why_it_stopped()
        if where is not None:
            out.append({"kind": "p", "tone": "warn", "html":
                        "This flow did not run to the end. It ended at <b>%s</b>%s, and the %s "
                        "below it never ran."
                        % (rk.e(where["name"]), ": " + rk.e(why) if why else "",
                           rk.many(len(self.below(where["id"])), "card"))})
        unreadable = [r for r in self.rows if r.get("rundir") and not self.readable(r)]
        if unreadable:
            out.append({"kind": "p", "tone": "caveat", "html":
                        "The run folder of %s is not readable from here, so this report shows "
                        "that card's record and not its numbers."
                        % rk.and_list([rk.e(r["name"]) for r in unreadable])})
        if self.summary_of.get("stalled"):
            out.append({"kind": "p", "tone": "warn", "html":
                        "Nothing of this flow is in the queue and it has not finished: it is "
                        "stalled rather than running."})
        return out

    def below(self, nid):
        """The cards downstream of this one, which are the ones a stop costs."""
        out, seen = [], {nid}
        stack = [nid]
        while stack:
            at = stack.pop()
            for edge in self.flow.out_of(at):
                if edge.dst not in seen:
                    seen.add(edge.dst)
                    out.append(edge.dst)
                    stack.append(edge.dst)
        return out

    # --- 1. summary

    def summary(self):
        blocks = [{"kind": "p", "html": self.summary_words()}, self.chain_table()]
        if self.opt["graph"]:
            got = self.graph_figure()
            if got:
                blocks.append(got)
        return {"id": "summary", "heading": "Summary", "label": "summary", "blocks": blocks}

    def summary_words(self):
        done = [r for r in self.rows if r["state"] == st.DONE]
        where, why = self.why_it_stopped()
        if not done:
            bits = ["None of the %s in this flow has finished"
                    % rk.many(len(self.rows), "card")]
        else:
            bits = ["%s of the %s in this flow finished: %s"
                    % (rk.cap(rk.words(len(done))), rk.many(len(self.rows), "card"),
                       rk.and_list([rk.e(r["name"]) for r in done]))]
        if where is not None:
            bits.append("the flow then ended at <b>%s</b>%s, and the %s below it never started"
                        % (rk.e(where["name"]),
                           ", where " + rk.e(why) if why else "",
                           rk.many(len(self.below(where["id"])), "card")))
            if where["state"] not in (st.FAILED, st.STOPPED):
                bits.append("that card's own record still reads %s, because the note went on the "
                            "card below it rather than on the one that failed"
                            % rk.e(SAID.get(where["state"], where["state"])))
        elif len(done) == len(self.rows):
            bits.append("every card ran to the end")
        else:
            waiting = [r for r in self.rows if r["state"] not in st.ENDED]
            if waiting:
                bits.append("%s %s"
                            % (rk.cap(rk.and_list([rk.e(r["name"]) for r in waiting])),
                               "is still to finish" if len(waiting) == 1 else "are still to "
                               "finish"))
        links, exact = self.carried()
        passed = [l for l in links if rk.whole(l.get("count")) is not None]
        if passed:
            last = passed[-1]
            bits.append("the last link to carry anything took %s to %s"
                        % (rk.count(rk.whole(last["count"]) or 0,
                                    _thing(last.get("kind") or "candidate")),
                           rk.e(self.by_id.get(last["to"], {}).get("name") or last["to"])))
        return rk.sentences(bits)

    def chain_table(self):
        rows = []
        for row in self.shown:
            rows.append({"card": row["name"], "tool": row["card"],
                         "state": {"html": "<span class='sp %s'>%s</span>"
                                           % (rk.e(row["state"]),
                                              rk.e(SAID.get(row["state"], row["state"])))},
                         "jobs": ", ".join(row["jobs"]) or "–",
                         "rundir": row.get("rundir") or "–",
                         "changed": _when(row.get("changed")),
                         "note": row.get("note") or ""})
            if row["state"] in (st.FAILED, st.STOPPED):
                rows[-1]["_class"] = "rk-mark"
        return {"kind": "table", "id": "chain",
                "columns": [{"key": "card", "label": "Card"},
                            {"key": "tool", "label": "Tool"},
                            {"key": "state", "label": "State",
                             "help": "The card's own record, written by the step job that ran it"},
                            {"key": "jobs", "label": "Jobs"},
                            {"key": "rundir", "label": "Run folder"},
                            {"key": "changed", "label": "Last written"},
                            {"key": "note", "label": "Note"}],
                "rows": rows, "caption": self.chain_caption(), "empty": "–"}

    def chain_caption(self):
        done = [r for r in self.rows if r["state"] == st.DONE]
        where, why = self.why_it_stopped()
        changed = [r.get("changed") for r in self.rows if r.get("changed")]
        if not done:
            bits = ["None of the %s has finished" % rk.many(len(self.rows), "card")]
        else:
            bits = ["%s of %s finished"
                    % (rk.cap(rk.words(len(done))), rk.many(len(self.rows), "card"))]
            with_dir = [r for r in done if r.get("rundir")]
            if with_dir:
                bits[-1] += (", %s leaving a run folder" % rk.words(len(with_dir)))
        if where is not None:
            bits.append("the flow ended at %s%s, and the %s below it never started"
                        % (rk.e(where["name"]), ", where " + rk.e(why) if why else "",
                           rk.many(len(self.below(where["id"])), "card")))
        if changed:
            bits.append("states are the cards' own record, last written %s"
                        % _when(max(changed), day=True))
        return rk.sentences(bits)

    def graph_figure(self):
        """The chain as it was drawn, which is the picture everybody asks for."""
        nodes = [n for n in self.doc.get("nodes") or [] if n.get("id")]
        if not nodes:
            return None
        svg = _graph_svg(nodes, self.doc.get("edges") or [], self.by_id, self.flow)
        if not svg:
            return None
        colors = sorted({SAID.get(self.by_id.get(n["id"], {}).get("state"), "")
                          for n in nodes} - {""})
        where, why = self.why_it_stopped()
        done = [n for n in nodes
                if self.by_id.get(n["id"], {}).get("state") == st.DONE]
        bits = ["%s drawn in one chain, %s of them finished: %s"
                % (rk.cap(rk.count(len(nodes), "card")), rk.words(len(done)),
                   rk.and_list([rk.e(self.by_id.get(n["id"], {}).get("name") or n["id"])
                                for n in nodes]))]
        if where is not None:
            bits.append("the flow ended at %s%s"
                        % (rk.e(where["name"]),
                           ", where " + rk.e(why) if why else ""))
        bits.append("each box is colored by the card's own state (%s) and each arrow is labeled "
                    "with what the link carries" % rk.and_list(colors))
        return {"kind": "figure", "id": "graph", "svg": svg, "caption": rk.sentences(bits)}

    # --- 2. the funnel, the new center of the report

    def funnel(self):
        if not self.opt["funnel"]:
            return None
        links, exact = self.carried()
        table = self.funnel_table(links, exact)
        return {"id": "funnel", "heading": "What became of the candidates", "label": "funnel",
                "blocks": [table]}

    def funnel_table(self, links, exact):
        rows = []
        for one in links:
            dropped = one.get("dropped") or []
            rows.append({
                "from": self.by_id.get(one.get("from"), {}).get("name") or one.get("from") or "",
                "to": self.by_id.get(one.get("to"), {}).get("name") or one.get("to") or "",
                "carries": _thing(one.get("kind") or "") or one.get("to_port") or "",
                "count": rk.whole(one.get("count")),
                "dropped": len(dropped),
                "why": "; ".join("%s (%s)" % (d[0], d[1]) if len(d) > 1 else str(d[0])
                                 for d in dropped) or "",
                "what": one.get("what") or "",
                "where": one.get("_from") or ""})
        return {"kind": "table", "id": "funnel",
                "columns": [{"key": "from", "label": "From"},
                            {"key": "to", "label": "To"},
                            {"key": "carries", "label": "Carries"},
                            {"key": "count", "label": "Passed on", "num": True, "digits": 0,
                             "help": "How many candidates the link handed to the next card"},
                            {"key": "dropped", "label": "Dropped", "num": True, "digits": 0,
                             "help": "A candidate deliberately left behind, with the reason"},
                            {"key": "why", "label": "Why"},
                            {"key": "what", "label": "What the link said it carried",
                             "help": "The step job's own words, which for a flow queued before "
                                     "the counts were recorded is all there is"},
                            {"key": "where", "label": "Counted from",
                             "help": "The flow's own record, or the step job's log for a flow "
                                     "queued before the record carried these counts"}],
                "rows": rows, "caption": self.funnel_caption(rows, exact), "empty": "–",
                "note": "" if exact else
                        "This flow was queued before the counts were written into its record, so "
                        "they are read back out of the step jobs' logs. A flow queued since "
                        "carries them exactly."}

    def funnel_caption(self, rows, exact):
        if not rows:
            where, why = self.why_it_stopped()
            if where is not None:
                return rk.missing(
                    "Nothing was carried anywhere in this flow: it %s at %s%s"
                    % (SAID.get(where["state"], where["state"]), rk.e(where["name"]),
                       ", because " + rk.e(why) if why else ""),
                    "the counts a link carries are written into the card's record when a link "
                    "runs, and no link here ran")
            return rk.missing(
                "No link in this flow has carried anything yet",
                "the counts appear here as each card finishes and hands its results on")
        counted = [r for r in rows if r["count"] is not None]
        bits = []
        if counted:
            first, last = counted[0], counted[-1]
            if len(counted) == 1:
                bits.append("The one link that ran carried %s from %s to %s"
                            % (rk.count(first["count"], _thing(first["carries"]) or "candidate"),
                               rk.e(first["from"]), rk.e(first["to"])))
            else:
                bits.append("The chain carried %s"
                            % rk.and_list(["%s to %s" % (
                                rk.count(r["count"], _thing(r["carries"]) or "candidate"),
                                rk.e(r["to"])) for r in counted]))
            empty = [r for r in counted if not r["count"]]
            if empty:
                bits.append("%s carried nothing, so %s never started"
                            % (rk.cap(rk.and_list([rk.e(r["from"]) + " to " + rk.e(r["to"])
                                                   for r in empty])),
                               rk.and_list([rk.e(r["to"]) for r in empty])))
        else:
            said = [r for r in rows if r.get("what")]
            if said:
                bits.append("%s ran, and %s how many candidates it carried"
                            % (rk.cap(rk.count(len(rows), "link")),
                               "it did not record" if len(rows) == 1
                               else "none of them recorded"))
                bits.append("what each one handed on is in its own words: %s"
                            % rk.and_list(["%s gave %s %s"
                                           % (rk.e(r["from"]), rk.e(r["to"]),
                                              rk.e(_first_clause(r["what"])))
                                           for r in said[:3]]))
            else:
                bits.append("No link in this flow records how many candidates it carried")
        dropped = [r for r in rows if r["dropped"]]
        if dropped:
            bits.append("%s were dropped on the way, %s"
                        % (rk.cap(rk.count(sum(r["dropped"] for r in dropped), "candidate")),
                           rk.and_list([rk.e(r["why"]) for r in dropped if r["why"]])
                           or "with no reason recorded"))
        else:
            bits.append("nothing was dropped at any link that ran")
        bits.append("counts are what each link handed on; a drop is a candidate deliberately "
                    "left behind, with its reason")
        return rk.sentences(bits)

    # --- 3. card by card

    def cards_section(self):
        subs = []
        for row in self.shown:
            subs.append(self.one_card(row))
        if not subs:
            return None
        return {"id": "cards", "heading": "Card by card", "label": "cards",
                "blocks": [], "sections": [s for s in subs if s]}

    def one_card(self, row):
        blocks = []
        given = self.settings.get(row["id"]) or {}
        defaults = _defaults(row["card"])
        changed = [(k, v) for k, v in sorted(given.items())
                   if k in defaults and defaults[k] != v]
        same = [k for k in sorted(given) if k in defaults and defaults[k] == v_of(given, k)]
        rows = [(rk.e(k), "%s <i>(the card's default is %s)</i>"
                 % (rk.e(_short(v)), rk.e(_short(defaults[k]))))
                for k, v in changed]
        if rows:
            blocks.append({"kind": "heading", "text": "What was changed from the card's defaults"})
            blocks.append(rk.facts(rows))
        elif given:
            blocks.append({"kind": "p", "tone": "caveat", "html":
                           "Every one of this card's %s was left at the card's own default."
                           % rk.count(len(given), "setting")})
        # A card that has not started has nothing of its own to say, and a heading with an empty
        # list under it reads as something missing from the report rather than as a card whose
        # turn has not come.
        if not (row.get("note") or row.get("jobs") or row.get("rundir")):
            blocks.append({"kind": "p", "tone": "caveat", "html": rk.e({
                st.WAITING: "Not started: it waits for the cards before it.",
                st.READY: "Not started: its turn comes when the cards before it finish.",
                st.STOPPED: "Stopped before it started.",
            }.get(row["state"], "Nothing yet."))})
        items = ["State: %s%s" % (rk.e(SAID.get(row["state"], row["state"])),
                                  ", " + rk.e(row["note"]) if row.get("note") else "")]
        if row["jobs"]:
            items.append("Jobs: %s" % rk.e(", ".join(row["jobs"])))
        if row.get("step"):
            items.append("The step job that ran the card: %s" % rk.e(row["step"]))
        if row.get("rundir"):
            items.append("Run folder: <code>%s</code>%s"
                         % (rk.e(row["rundir"]),
                            "" if self.readable(row) else
                            " — not readable from here, so this card's own numbers are not "
                            "in this report"))
        own = self.own_report(row)
        if own:
            items.append(own)
        blocks.append({"kind": "list", "items": items})
        table = self.card_table(row)
        if table:
            blocks.append(table)
        tail = self.card_log(row)
        if tail:
            blocks += tail
        return {"id": "card_%s" % row["id"], "heading": row["name"], "label": row["name"],
                "blocks": blocks}

    def own_report(self, row):
        """Where that card's own report is, rather than repeating it here."""
        if row["card"] not in REPORTS or not self.readable(row):
            return ""
        return ("This card's own report has the detail: open the %s page on its run folder and "
                "press <b>Report (PDF)</b>." % rk.e(row["name"]))

    def card_table(self, row):
        """The table that matters from this card's run folder, named rather than found."""
        if not self.readable(row):
            return None
        where = row["rundir"]
        for name in WANTED.get(row["card"], ()):
            path = os.path.join(where, name)
            if not os.path.isfile(path):
                continue
            if name.endswith(".json"):
                return self._designs_table(row, path)
            return self._tsv_table(row, path, name)
        found = pagereport._tables(where)
        if not found:
            return None
        name, head, body = found[0]
        return self._rows_table(row, name, head, body[:self.opt["top"]], len(body))

    def _tsv_table(self, row, path, name):
        import csv
        try:
            with open(path, newline="", errors="replace") as handle:
                sample = handle.read(4096)
                handle.seek(0)
                sep = "\t" if "\t" in sample.split("\n")[0] else ","
                got = list(csv.reader(handle, delimiter=sep))
        except OSError:
            return None
        if len(got) < 2:
            return None
        return self._rows_table(row, name, got[0], got[1:1 + self.opt["top"]], len(got) - 1)

    def _designs_table(self, row, path):
        got = rk.read_json(path, {}) or {}
        designs = (got.get("designs") or [])[:self.opt["top"]]
        if not designs:
            return None
        head = ["name", "score", "residues", "binder"]
        body = [[d.get("name") or "", d.get("score"),
                 len(str(d.get("sequence") or "")) or "",
                 ",".join(d.get("binder_chains") or [])] for d in designs]
        return self._rows_table(row, os.path.basename(path), head, body,
                                len(got.get("designs") or []))

    COLUMNS_SHOWN = 8

    def _rows_table(self, row, name, head, body, total):
        # Kept from the left, which is the order the tool itself put them in: a tool's own
        # identifying and ranking columns come first, and its working comes last.
        kept = list(range(len(head)))[:self.COLUMNS_SHOWN]
        out = []
        for line in body:
            out.append({"c%d" % i: (line[i] if i < len(line) and line[i] != "" else None)
                        for i in kept})
        if out:
            out[0]["_class"] = "rk-mark"
        left = len(head) - len(kept)
        note = "From <code>%s</code> in the run folder above." % rk.e(name)
        if left:
            note += (" %s of its %s columns are shown, from the left, so the table fits the "
                     "page; the other %s %s in that file."
                     % (rk.cap(rk.words(len(kept))), len(head),
                        rk.many(left, "column"), "is" if left == 1 else "are"))
        return {"kind": "table", "id": "t_%s" % row["id"],
                "columns": [{"key": "c%d" % i, "label": str(head[i])} for i in kept],
                "rows": out, "empty": "–",
                "caption": self.card_table_caption(
                    row, name, [head[i] for i in kept],
                    [[line[i] if i < len(line) else "" for i in kept] for line in body], total),
                "note": note}

    def card_table_caption(self, row, name, head, body, total):
        """A caption from the table's own first rows, not its file name."""
        if not body:
            return rk.missing("%s left no rows in %s" % (rk.e(row["name"]), rk.e(name)))
        # The first numeric column with values in it is the one worth a sentence.
        best = None
        for i, label in enumerate(head):
            values = [rk.number(line[i]) for line in body
                      if i < len(line) and rk.number(line[i]) is not None]
            if len(values) >= 2:
                best = (label, values)
                break
        bits = ["%s wrote %s%s"
                % (rk.e(row["name"]), rk.count(total, "row"),
                   ", of which the %s shown here" % rk.words(len(body))
                   if total > len(body) else "")]
        if best:
            label, values = best
            bits.append("their %s runs from %s to %s"
                        % (rk.e(str(label)), rk.num(min(values), 2), rk.num(max(values), 2)))
            first = [rk.number(body[0][i]) for i, l in enumerate(head) if str(l) == str(label)]
            if first and first[0] is not None:
                bits.append("the first row, which the card ranked best, is at %s"
                            % rk.num(first[0], 2))
        else:
            bits.append("no column of it is numeric, so there is nothing to compare across the "
                        "rows here")
        bits.append("the whole table is the file named under it")
        return rk.sentences(bits)

    def card_log(self, row):
        if row["state"] not in (st.FAILED, st.STOPPED):
            return []
        lines = self.log_lines(row, 25)
        if not lines:
            return []
        return [{"kind": "heading", "text": "The end of this card's own log"},
                {"kind": "pre", "text": "\n".join(lines)}]

    # --- 4. the survivors

    def survivors(self):
        rows, columns = self.survivor_rows()
        return {"id": "survivors", "heading": "The candidates that came through",
                "label": "survivors", "blocks": [
                    {"kind": "table", "id": "survivors", "columns": columns, "rows": rows,
                     "caption": self.survivor_caption(rows), "empty": "–",
                     "note": "Joined on the name each card gave the candidate. Nothing in a flow "
                             "records that a candidate renamed by one card is the same one the "
                             "card before it passed, so a renaming breaks this join rather than "
                             "following it."}]}

    def survivor_rows(self):
        """One row per candidate that reached the last card that ran.

        Joined on the name, because a name is all a flow carries: there is no lineage in the
        record saying that what went into a card as X came out of it as Y.
        """
        runs = self.record.card(self._md_card() or "").get("runs") or [] \
            if self._md_card() else []
        rows = []
        for one in runs[:self.opt["top"]]:
            rows.append({"name": one.get("name") or "", "rank": rk.whole(one.get("rank")),
                         "arrived": rk.number(one.get("score"))})
        columns = [{"key": "name", "label": "Candidate"},
                   {"key": "rank", "label": "Rank it arrived with", "num": True, "digits": 0},
                   {"key": "arrived", "label": "Score it arrived with", "num": True,
                    "digits": 2}]
        return rows, columns

    def _md_card(self):
        for row in self.rows:
            if row["card"] == "hopemd":
                return row["id"]
        return ""

    def survivor_caption(self, rows):
        if not rows:
            links, _ = self.carried()
            got = [l for l in links if rk.whole(l.get("count"))]
            where, why = self.why_it_stopped()
            if got:
                last = got[-1]
                return rk.missing(
                    "No candidate has come through every card of this flow, so there is nothing "
                    "to put side by side; the furthest any got is the %s %s carried to %s"
                    % (rk.count(rk.whole(last["count"]) or 0,
                                _thing(last.get("kind") or "candidate")),
                       rk.e(self.by_id.get(last["from"], {}).get("name") or last["from"]),
                       rk.e(self.by_id.get(last["to"], {}).get("name") or last["to"])),
                    "the simulation card is where a candidate's numbers at every stage are "
                    "gathered, and it has not run")
            return rk.missing(
                "Nothing came through this flow: %s"
                % (("it ended at %s" % rk.e(where["name"])) if where is not None
                   else "no card has passed anything on yet"),
                "a candidate appears here once it has reached the simulation card")
        best = rows[0]
        return rk.sentences([
            "%s came through to the last card, led by <b>%s</b>, which arrived ranked %s"
            % (rk.cap(rk.count(len(rows), "candidate")), rk.e(best["name"]),
               rk.num(best["rank"], 0) if best["rank"] is not None else "unrecorded"),
            "each column is the number the card named in its heading gave that candidate"])

    # --- 5. the simulations, kept from the page that exists

    def simulations(self):
        nid = self._md_card()
        if not nid:
            return None
        head, body = pagereport._runs_rows(self.record, nid)
        if not body:
            return None
        keys = ["c%d" % i for i in range(len(head))]
        rows = [dict(zip(keys, line)) for line in body]
        if rows:
            rows[0]["_class"] = "rk-mark"
        return {"id": "simulations", "heading": "The simulations side by side",
                "label": "simulations", "blocks": [
                    {"kind": "table", "id": "simulations",
                     "columns": [{"key": k, "label": str(h)} for k, h in zip(keys, head)],
                     "rows": rows, "empty": "–",
                     "caption": rk.sentences([
                         # The number, not "both": the reader has not been told how many
                         # structures reached the simulation card until this sentence says so.
                         "%s were simulated from this flow"
                         % rk.cap(rk.many(len(rows), "structure")),
                         "each row is one run of the simulation card, with the rank and the score "
                         "it arrived with and the numbers its own analysis wrote; means over the "
                         "replicates that finished"])}]}

    # --- 6. what it cost

    def cost(self):
        if not self.opt["cost"]:
            return None
        rows, total = [], 0.0
        for row in self.rows:
            for job in row["jobs"] + ([row["step"]] if row.get("step") else []):
                got = rk.sacct(job)
                seconds = _seconds(got.get("Elapsed", ""))
                if seconds:
                    total += seconds
                rows.append({"card": row["name"], "job": job,
                             "what": "the tool" if job in row["jobs"] else "the flow's own step",
                             "state": got.get("State") or "Slurm has forgotten it",
                             "elapsed": got.get("Elapsed") or "",
                             "partition": got.get("Partition") or ""})
        if not rows:
            return None
        return {"id": "cost", "heading": "What it cost", "label": "cost", "blocks": [
            {"kind": "table", "id": "cost",
             "columns": [{"key": "card", "label": "Card"}, {"key": "job", "label": "Job"},
                         {"key": "what", "label": "Which job",
                          "help": "A flow queues one short step job per card to wake it up, and "
                                  "the card's own tool job is the one that costs"},
                         {"key": "state", "label": "State"},
                         {"key": "elapsed", "label": "Elapsed"},
                         {"key": "partition", "label": "Partition"}],
             "rows": rows, "empty": "–", "caption": self.cost_caption(rows, total),
             "note": "Elapsed times come from Slurm's accounting, which forgets after a few "
                     "months; a job it has forgotten is shown as such rather than as zero."}]}

    def cost_caption(self, rows, total):
        steps = [r for r in rows if r["what"].startswith("the flow")]
        tools = [r for r in rows if not r["what"].startswith("the flow")]
        forgotten = [r for r in rows if not r["elapsed"]]
        bits = []
        if total:
            bits.append("The flow used %s of wall clock across %s"
                        % (_hours(total), rk.count(len(rows), "job")))
        else:
            bits.append("Slurm no longer remembers any of the %s this flow queued, so none of "
                        "their times can be shown" % rk.count(len(rows), "job"))
        if steps and tools:
            bits.append("%s of them %s the flow's own step jobs, which wake a card up and cost "
                        "almost nothing; the %s that cost anything %s the tools'"
                        % (rk.words(len(steps)), "is" if len(steps) == 1 else "are",
                           rk.many(len(tools), "job"), "is" if len(tools) == 1 else "are"))
        if forgotten and total:
            bits.append("%s of them %s been forgotten by the accounting"
                        % (rk.cap(rk.words(len(forgotten))),
                           "has" if len(forgotten) == 1 else "have"))
        return rk.sentences(bits)

    # --- 7. the files

    def files(self):
        if not self.opt["files"]:
            return None
        items = []
        for row in self.rows:
            if not row.get("rundir"):
                continue
            if not self.readable(row):
                items.append("<b>%s</b>: <code>%s</code> — not readable from here"
                             % (rk.e(row["name"]), rk.e(row["rundir"])))
                continue
            got = pagereport._files(row["rundir"])
            items.append("<b>%s</b>: <code>%s</code>%s"
                         % (rk.e(row["name"]), rk.e(row["rundir"]),
                            ", %s worth fetching" % rk.count(len(got), "file") if got else ""))
        if not items:
            return None
        return {"id": "files", "heading": "Where everything is", "label": "files",
                "blocks": [{"kind": "list", "items": items},
                           {"kind": "p", "tone": "caveat", "html":
                            "Each card's own report has that run's files in full; this is where "
                            "to look for them."}]}

    # ------------------------------------------------------------ the methods splice

    def methods(self):
        """The cards' own methods, in run order, with one reference list.

        The cards' servers are not running on a cluster, so each tool's report module is imported
        here and asked for the document of that card's run folder. A card whose module will not
        import, or whose folder cannot be read, is named rather than quietly left out: a methods
        section that silently dropped a step would be worse than a short one.
        """
        out, references, seen = [], [], {}
        out.append(self.own_methods_sentence())
        missing = []
        for row in self.rows:
            if row["state"] != st.DONE or not self.readable(row):
                continue
            where = REPORTS.get(row["card"])
            if not where:
                missing.append((row, "has no report of its own yet"))
                continue
            try:
                module = __import__(where[0], fromlist=["document"])
                doc = (module.document(where[1], row["rundir"])
                       if where[1] is not None else module.document(row["rundir"]))
            except Exception as why:                            # noqa: BLE001
                missing.append((row, "its report could not be read (%s)"
                                % type(why).__name__))
                continue
            if doc.get("error") or not doc.get("methods"):
                missing.append((row, doc.get("error") or "its report carries no methods"))
                continue
            text = doc["methods"]
            # The citations are renumbered once across the whole flow, and a reference two cards
            # both used - ff19SB, OPC, ProteinMPNN - appears once.
            mapping = {}
            for one in doc.get("references") or []:
                key = _ref_key(one.get("text") or "")
                if key not in seen:
                    seen[key] = len(references) + 1
                    references.append({"n": seen[key], "text": one["text"]})
                mapping[int(one["n"])] = seen[key]
            out.append("<b>%s.</b> %s" % (rk.e(row["name"]), _renumber(text, mapping)))
        if missing:
            out.append("Not every card's methods could be gathered here: %s. Each of those "
                       "cards' own reports has them."
                       % rk.and_list(["%s %s" % (rk.e(r["name"]), rk.e(why))
                                      for r, why in missing]))
        return "".join("<p>%s</p>" % p for p in out if p), references

    def own_methods_sentence(self):
        """The flow's own one sentence at the front, from flow.json."""
        from hope_labs import __version__ as labs_version
        bits = []
        target = next((r for r in self.rows if r["card"] == "target"), None)
        given = self.settings.get(target["id"]) if target else {}
        chain = ", ".join(str(x) for x in (given.get("chains") or [])) \
            if isinstance(given.get("chains"), list) else str(given.get("chains") or "")
        hot = given.get("hotspots")
        bits.append("The work was run as a HOPE Labs flow (%s, %s)"
                    % (rk.e("HOPE Labs " + labs_version),
                       "flow %s" % rk.e(self.summary_of["name"])))
        made = []
        if given.get("path"):
            made.append("the target %s%s%s"
                        % (rk.e(os.path.basename(str(given["path"]))),
                           " chain %s" % rk.e(chain) if chain else "",
                           " with hotspots %s" % rk.e(str(hot)) if hot else ""))
        for row in self.rows:
            if row["card"] == "target":
                continue
            made.append(rk.e(row["name"]))
        if made:
            bits.append("the chain was %s" % rk.and_list(made))
        return rk.sentences(bits)

    # ------------------------------------------------------------ the appendix

    def appendix(self):
        return [a for a in (self.appx_flow(), self.appx_settings(), self.appx_wrong(),
                            self.appx_provenance()) if a]

    def appx_flow(self):
        return {"heading": "The flow as it was drawn", "blocks": [
            {"kind": "pre", "text": self.raw_flow[:20000]}]}

    def appx_settings(self):
        blocks = []
        for row in self.rows:
            given = self.settings.get(row["id"]) or {}
            if not given:
                continue
            defaults = _defaults(row["card"])
            rows = []
            for key in sorted(given):
                value = given[key]
                if key in defaults and defaults[key] == value:
                    continue
                rows.append((rk.e(key), "%s <i>(default %s)</i>"
                             % (rk.e(_short(value)),
                                rk.e(_short(defaults.get(key, "none"))))
                             if key in defaults else rk.e(_short(value))))
            blocks.append({"kind": "heading", "text": row["name"]})
            blocks.append(rk.facts(rows) if rows else
                          {"kind": "p", "tone": "caveat",
                           "html": "Everything left at the card's defaults."})
        if not blocks:
            return None
        return {"heading": "Each card's settings against the card's defaults", "blocks": blocks}

    def appx_wrong(self):
        blocks = []
        for row in self.rows:
            if row["state"] not in (st.FAILED, st.STOPPED):
                continue
            items = ["Its record says: %s" % rk.e(row.get("note") or "nothing")]
            path = self.log_path(row)
            if path:
                items.append("The log the record names: <code>%s</code>"
                             % rk.e(os.path.relpath(path, self.where)))
            others = [n for n in (os.listdir(os.path.join(self.where, "logs"))
                                  if os.path.isdir(os.path.join(self.where, "logs")) else [])
                      if n.startswith(row["card"] + "-") and n.endswith(".out")]
            if len(others) > 1:
                items.append("This card has %s, from step jobs that woke it more than once; the "
                             "one above is the one its record names, and the only one that "
                             "describes what actually happened."
                             % rk.count(len(others), "log"))
            blocks.append({"kind": "heading", "text": row["name"]})
            blocks.append({"kind": "list", "items": items})
            if path:
                blocks.append({"kind": "pre", "text": rk.tail(path, 30)})
        if not blocks:
            return None
        return {"heading": "What went wrong", "blocks": blocks}

    def appx_provenance(self):
        settled = self.record.data.get("settled") or {}
        items = []
        for label, key in (("Python the step jobs used", "python"),
                           ("The lab root they imported from", "package_root"),
                           ("Account", "account"), ("Partition", "partition")):
            if settled.get(key):
                items.append("%s: <code>%s</code>" % (label, rk.e(settled[key])))
        if self.doc.get("runs_root"):
            items.append("Runs root: <code>%s</code>" % rk.e(self.doc["runs_root"]))
        claims = os.path.join(self.where, "claims")
        if os.path.isdir(claims):
            got = sorted(os.listdir(claims))
            items.append("The claims folder records which job took each card: %s"
                         % rk.e(", ".join(got) or "nothing claimed"))
        stamp = self.doc.get("written_by") or {}
        if stamp:
            items.append("Written by HOPE Labs %s%s"
                         % (rk.e(stamp.get("hope_labs") or "?"),
                            " (%s)" % rk.e(stamp.get("commit", "")[:9])
                            if stamp.get("commit") else ""))
        else:
            items.append("Nothing in <code>flow.json</code> records which HOPE Labs drew or "
                         "queued this flow: its <code>version</code> is the file format's, not "
                         "the software's. A flow queued since carries the stamp.")
        return {"heading": "Provenance", "blocks": [{"kind": "list", "items": items}]}


# ------------------------------------------------------------------ small helpers

def v_of(given, key):
    return given.get(key)


def _defaults(card):
    """The card's own defaults, so a reader sees what was changed rather than forty rows."""
    kind = cards.KINDS.get(card)
    out = {}
    for field in (getattr(kind, "settings", None) or []):
        name = getattr(field, "key", None) or (field.get("key") if isinstance(field, dict)
                                               else None)
        if name is None:
            continue
        out[name] = getattr(field, "default", None) if not isinstance(field, dict) \
            else field.get("default")
    return out


def _short(value):
    got = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
    return got if len(got) <= 90 else got[:87] + "\u2026"


def _thing(kind):
    """What a link carries, as a caption names it."""
    return {"sequences": "peptide", "poses": "pose", "structure": "structure",
            "structures": "structure", "target": "target", "receptor": "receptor",
            "designs": "design"}.get(str(kind or ""), str(kind or ""))


def _first_clause(text):
    """The readable head of a step job's line, for a caption that has only prose to work from."""
    got = str(text or "").split(",")[0].strip()
    return got if len(got) <= 70 else got[:67] + "\u2026"


def _leading_count(text):
    import re
    got = re.match(r"\s*(\d+)\b", str(text or ""))
    return int(got.group(1)) if got else None


def _dropped_from(text):
    """"(2 dropped: too long)" out of the prose a step job printed."""
    import re
    got = re.search(r"\((\d+)\s+dropped:?\s*([^)]*)\)", str(text or ""))
    if not got:
        return []
    return [["%s dropped" % got.group(1), got.group(2).strip() or "no reason recorded"]]


def _when(stamp, day=False):
    try:
        stamp = int(stamp)
    except (TypeError, ValueError):
        return ""
    got = time.localtime(stamp)
    return ("%d %s" % (got.tm_mday, time.strftime("%B %Y", got)) if day
            else time.strftime("%Y-%m-%d %H:%M", got))


def _seconds(elapsed):
    parts = str(elapsed or "").split(":")
    try:
        if len(parts) == 3:
            head, mins, secs = parts
            days, _, hours = head.partition("-")
            if hours:
                return (int(days) * 86400 + int(hours) * 3600 + int(mins) * 60 + float(secs))
            return int(days) * 3600 + int(mins) * 60 + float(secs)
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
    except ValueError:
        return 0.0
    return 0.0


def _hours(seconds):
    if seconds < 90:
        return "%.0f s" % seconds
    if seconds < 5400:
        return "%.0f min" % (seconds / 60.0)
    return "%.1f h" % (seconds / 3600.0)


def _ref_key(text):
    """Two cards citing the same work should cite it once: matched on the first author and the
    year, which is what survives two tools writing the same reference differently."""
    import re
    author = re.split(r"[,.]", str(text or "").strip())[0].lower()
    year = re.search(r"\((\d{4})\)", str(text or ""))
    return "%s|%s" % (author, year.group(1) if year else "")


def _renumber(html, mapping):
    """A card's [n] citations renumbered into the flow's one list."""
    import re

    def swap(found):
        numbers = []
        for part in found.group(1).split(","):
            part = part.strip()
            if part.isdigit() and int(part) in mapping:
                numbers.append(str(mapping[int(part)]))
        return " [%s]" % ", ".join(numbers) if numbers else ""

    return re.sub(r"\s\[([\d,\s]+)\]", swap, str(html or ""))


def _graph_svg(nodes, edges, by_id, flow):
    """The chain as the canvas draws it, in SVG so it prints vector.

    Drawn at the width the figure is given and pinned there, so the words in it are the size
    they are written at. They were 9.5, 10 and 11.5, and the label on an arrow was wider than
    the gap it sat in: it ran over the boxes on both sides of it. The boxes are therefore laid
    out around the type - a gap wide enough for its label, a box tall enough for two lines of
    it - rather than the type being squeezed into a layout that was drawn for smaller words.
    """
    at = {}
    for node in nodes:
        place = node.get("at") or [0, 0]
        at[node["id"]] = (float(place[0]), float(place[1]))
    if not at:
        return ""
    xs = [p[0] for p in at.values()]
    ys = [p[1] for p in at.values()]
    width, box_w, box_h = rk.FIG, 150, 56
    span_x = (max(xs) - min(xs)) or 1.0
    span_y = (max(ys) - min(ys)) or 1.0

    # What each arrow says, and how much room it needs between the two boxes it joins.
    says = {}
    for edge in edges:
        port = edge.get("fromPort") or edge.get("src_port") or ""
        if port:
            says[id(edge)] = _thing(port) + "s"
    need = max([rk._wide(t) + 14 for t in says.values()] or [0])
    # The margin at the ends of the row is what buys the gaps between the boxes: it is given up,
    # down to a quarter of what it was, until every label fits beside the box it belongs to.
    steps = sorted({abs(at[e["id"]][0] - at[f["id"]][0])
                    for e in nodes for f in nodes
                    if e["id"] in at and f["id"] in at and abs(at[e["id"]][0] - at[f["id"]][0]) > 1e-6}
                   ) or [span_x]
    room = (width - box_w - (need + box_w) * span_x / steps[0]) / 2.0
    pad = int(max(18, min(70, room)))

    scale = (width - 2 * pad - box_w) / span_x
    height = int(span_y * min(scale, 0.9) + 2 * pad + box_h)
    place = {nid: (pad + (x - min(xs)) * scale,
                   pad + (y - min(ys)) * min(scale, 0.9))
             for nid, (x, y) in at.items()}
    out = [rk._open(width, height)]
    for edge in edges:
        src, dst = edge.get("from") or edge.get("src"), edge.get("to") or edge.get("dst")
        if src not in place or dst not in place:
            continue
        x1, y1 = place[src][0] + box_w, place[src][1] + box_h / 2
        x2, y2 = place[dst][0], place[dst][1] + box_h / 2
        mid = (x1 + x2) / 2
        out.append('<path d="M%.1f,%.1f C%.1f,%.1f %.1f,%.1f %.1f,%.1f" fill="none" '
                   'stroke="%s" stroke-width="1.4"/>'
                   % (x1, y1, mid, y1, mid, y2, x2, y2, rk.PALE))
        out.append('<polygon points="%.1f,%.1f %.1f,%.1f %.1f,%.1f" fill="%s"/>'
                   % (x2, y2, x2 - 7, y2 - 4, x2 - 7, y2 + 4, rk.PALE))
        label = says.get(id(edge))
        if label:
            # Whatever room is left, the label stays inside it: a flow drawn with its cards
            # almost touching gets a shortened word rather than one written over a box. The
            # caption says what every arrow carries, so the word is never the only copy.
            fits = max(3, int((abs(x2 - x1) - 8) / (0.62 * rk.LABEL)))
            if len(label) > fits:
                label = label[:fits - 1] + "\u2026"
            out.append(rk._text((x1 + x2) / 2, (y1 + y2) / 2 - 8, label, "middle",
                                rk.LABEL, rk.PALE))
    for node in nodes:
        nid = node["id"]
        if nid not in place:
            continue
        x, y = place[nid]
        row = by_id.get(nid) or {}
        color = STATE_COLOUR.get(row.get("state"), rk.GRID)
        out.append('<rect x="%.1f" y="%.1f" width="%d" height="%d" rx="7" fill="#ffffff" '
                   'stroke="%s" stroke-width="2"/>' % (x, y, box_w, box_h, color))
        out.append(rk._text(x + box_w / 2, y + 23, row.get("name") or nid, "middle",
                            rk.LABEL + 1, rk.INK, bold=True))
        out.append(rk._text(x + box_w / 2, y + 42, SAID.get(row.get("state"),
                                                            row.get("state") or ""),
                            "middle", rk.LABEL, color))
    out.append("</svg>")
    return "".join(out)


def document(where, raw=None):
    """The whole report of a flow, or the one thing a reader can act on if it cannot be written."""
    try:
        return Report(where, raw or {}).build()
    except Exception as exc:                                    # noqa: BLE001
        return {"error": "the report could not be written: %s: %s"
                         % (type(exc).__name__, exc)}


def page_for(token, where, autoprint=False):
    """The report page for one flow."""
    from hope_labs import __version__ as labs_version
    return rk.page(token, where, labs_version, store="flow.report", autoprint=autoprint,
                   three_dmol="/static/vendor/3Dmol-min.js")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("python -m hope_flow.reportdata <flow folder>")
        return 2
    print(json.dumps(document(argv[0]), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
