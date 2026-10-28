"""The report of a design run, written up to be read: RFdiffusion, RFdiffusion3, BoltzGen or
RFD4-Proteina.

By Aadhil Haq

They are one report with a middle each. They are served by one page, queued by one job template
and leave the same designs.json, so the title, the byline, the summary, the first columns of the
ranking, the pose figure, the whole appendix and every option but the tool's own are written once
here and shared. What differs is what a run knows about a design: RFdiffusion has a pLDDT
trajectory and a ProteinMPNN score, RFdiffusion3 has geometry and no confidence at all, BoltzGen
has a wide metrics table it filtered and ranked itself, and RFD4-Proteina has no number of any
kind, because its run is composed without the oracles that would score one. That last case is the
reason several captions here say what is missing rather than leaving a column empty.

Two things this module will not do. It does not recompute a number the run already wrote, so the
report and the Runs screen cannot disagree. And it writes nothing into a run folder: a report only
reads.

This runs in one stdlib-only process on a login node (see serve.py), so everything read here is
read with json, csv and re. That is why a run's pLDDT summary has to be written into designs.json
by the helper, which runs inside SE3nv where numpy is: see plddt_of() and the sentence it writes
when a run predates that.

    python -m hope_labs.pages.runreport rfdiffusion <run folder>     the document, as JSON
"""
from __future__ import annotations

import csv
import glob
import json
import os
import re
import sys

from .. import reporting as rk
from . import docs as pagedocs
from . import runs as runsmod

#: The bar's choices, per tool. Everything but the tool's own is shared, in the same order, so
#: somebody who ran two of these against one target finds the same bar on both reports.
COMMON_TAIL = [
    {"key": "pose", "label": "Draw the top design", "type": "check", "default": True,
     "help": "The best design on the target, with the hotspots marked"},
    {"key": "methods", "label": "Methods and references", "type": "check", "default": True},
    {"key": "appendix", "label": "Appendix", "type": "check", "default": True},
]

OPTIONS = {
    "rfdiffusion": [
        {"key": "top", "label": "Designs shown", "type": "number", "default": 10, "least": 1,
         "most": 50, "help": "How many designs, from the top of the ranking, the tables show"},
        {"key": "trace", "label": "pLDDT trajectory", "type": "select", "default": "shown",
         "choices": [["all", "every design"], ["shown", "the designs shown"], ["none", "leave out"]],
         "help": "A run of two hundred designs makes an unreadable figure; the designs shown is "
                 "the useful middle"},
        {"key": "allseqs", "label": "Every ProteinMPNN sequence", "type": "check",
         "default": False,
         "help": "Every sequence sampled for each backbone, not only the one kept"},
    ] + COMMON_TAIL,
    "rfd3": [
        {"key": "top", "label": "Designs shown", "type": "number", "default": 10, "least": 1,
         "most": 64},
        {"key": "geometry", "label": "Geometry table", "type": "check", "default": True,
         "help": "The model's whole metrics block, with the scope of each column"},
        {"key": "allseqs", "label": "Every ProteinMPNN sequence", "type": "check",
         "default": False},
        {"key": "rfd3seq", "label": "The model's own sequence", "type": "check", "default": True,
         "help": "Somebody ordering from this report may want one sequence per design and no "
                 "ambiguity about which"},
    ] + COMMON_TAIL,
    "boltzgen": [
        {"key": "top", "label": "Designs shown", "type": "number", "default": 10, "least": 1,
         "most": 100},
        {"key": "set", "label": "Which designs", "type": "select", "default": "selected",
         "choices": [["selected", "the diverse selection"], ["top10", "the quality top ten"],
                     ["all", "every design"]],
         "help": "BoltzGen hands back two sets with different meanings, and somebody choosing "
                 "what to order needs to know which they are looking at"},
        {"key": "filters", "label": "Filter table and figure", "type": "check", "default": True},
        {"key": "population", "label": "Compare with every design", "type": "check",
         "default": True,
         "help": "Reads the whole of all_designs_metrics.csv, which at ten thousand designs is a "
                 "large file"},
        {"key": "sequences", "label": "Sequences to order", "type": "check", "default": True},
    ] + COMMON_TAIL,
    "rfd4": [
        {"key": "top", "label": "Designs shown", "type": "number", "default": 10, "least": 1,
         "most": 100},
        {"key": "conditions", "label": "What the model was told", "type": "check", "default": True,
         "help": "The contig and each condition, as the spec the run was driven by states them"},
        {"key": "sequences", "label": "Sequences to order", "type": "check", "default": True,
         "help": "RFD4-Proteina designs the sequence with the backbone, so this is its own"},
    ] + COMMON_TAIL,
}

#: The line above the title, per tool.
KICKER = {"rfdiffusion": "Binder design report", "rfd3": "Binder design report",
          "boltzgen": "Binder design report", "rfd4": "Binder design report"}

TOOL_NAME = {"rfdiffusion": "RFdiffusion", "rfd3": "RFdiffusion3", "boltzgen": "BoltzGen",
             "rfd4": "RFD4-Proteina"}

#: AlphaFold's own bands, which is what a reader has in their head when they see a pLDDT. These
#: are fractions because RFdiffusion writes pLDDT as a fraction of one.
PLDDT_BANDS = ((0.90, 1.0, "#1a56db"), (0.70, 0.90, "#2f9e6e"), (0.50, 0.70, "#e8955a"))


# ----------------------------------------------------------------- reading a run

def _fasta_samples(path):
    """ProteinMPNN's sequences for one backbone, as rows.

    The first record is the backbone's own sequence with ProteinMPNN's score for it, and the rest
    carry sample, score, global_score and seq_recovery in the header. The same regex the design
    helper uses to pick the best is the reader here, without the "keep the lowest" step.
    """
    try:
        with open(path, errors="replace") as handle:
            text = handle.read()
    except OSError:
        return [], {}
    rows, head = [], {}
    for block in text.split(">")[1:]:
        line, _, rest = block.partition("\n")
        fields = dict(re.findall(r"(\w+)=([^,\s]+)", line))
        sequence = "".join(rest.split())
        if "sample" not in fields:
            head = dict(fields, sequence=sequence,
                        model=(fields.get("model_name") or ""),
                        fixed=re.search(r"fixed_chains=\[([^\]]*)\]", line))
            head["fixed"] = re.sub(r"['\s]", "", head["fixed"].group(1)) if head["fixed"] else ""
            head["sequence"] = sequence
            continue
        rows.append({"sample": rk.whole(fields.get("sample")),
                     "score": rk.number(fields.get("score")),
                     "global": rk.number(fields.get("global_score")),
                     "recovery": rk.number(fields.get("seq_recovery")),
                     "sequence": sequence, "length": len(sequence)})
    return rows, head


def flat_yaml(path, allowed=None):
    """A narrow reader for the flat ``key: value`` files the page's Python cannot parse properly.

    It understands one level of plain keys, scalars and inline lists, and nothing else: anything
    it does not understand it refuses by leaving it out and saying so in ``_unread``, because
    printing a threshold that was overridden would be worse than printing none.
    """
    out, unread = {}, []
    try:
        with open(path, errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return {}, ["the file could not be read"]
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1] in (" ", "\t", "-"):              # nesting, which this reader does not do
            unread.append(line.strip()[:60])
            continue
        key, sep, value = line.partition(":")
        if not sep:
            unread.append(line.strip()[:60])
            continue
        key, value = key.strip(), value.strip()
        if allowed is not None and key not in allowed:
            continue
        if not value:
            unread.append(key)
            continue
        if value.startswith("[") and value.endswith("]"):
            out[key] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
        elif value.lower() in ("true", "false"):
            out[key] = value.lower() == "true"
        elif value.lower() in ("null", "none", "~"):
            out[key] = None
        else:
            got = rk.number(value)
            out[key] = got if got is not None and re.fullmatch(r"-?[\d.eE+]+", value) else \
                value.strip("'\"")
    return out, unread


def _csv_rows(path):
    try:
        with open(path, newline="", errors="replace") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        return []


def _column(row, *names):
    """The first of these columns the run actually wrote, which is how a CSV whose column names
    moved between versions is still read."""
    for name in names:
        if name in row and str(row[name]).strip() != "":
            return rk.number(row[name]) if rk.number(row[name]) is not None else row[name]
    return None


class Report:
    """One design run's report. Built from the run folder and nothing else."""

    def __init__(self, tool, rundir, raw=None, install=""):
        if not os.path.isdir(rundir or ""):
            raise ValueError("no such run folder")
        self.tool = tool
        self.run = os.path.abspath(rundir)
        self.job = _job_dir(self.run)
        self.install = install
        self.designs_file = os.path.join(self.job, "designs.json")
        self.record = rk.read_json(self.designs_file, {}) or {}
        self.designs = list(self.record.get("designs") or [])
        self.plan = rk.read_json(self._beside("plan.json"), {}) or {}
        self.sub = rk.read_json(os.path.join(self.run, "submission.json"), {}) or {}
        self.flow = rk.read_json(os.path.join(self.run, "flow.json"), {}) or {}
        self.card = rk.read_json(os.path.join(self.run, "state", tool + ".json"), {}) or {}
        self.metrics = {}                      # rfd3: the per-design metrics JSON
        if tool == "rfd3":
            for one in self.designs:
                self.metrics[one.get("name")] = rk.read_json(
                    self._beside(os.path.join("rfd3", "%s.json" % one.get("name"))), {}) or {}
        self.csv_name, self.csv, self.all_csv = "", [], []
        if tool == "boltzgen":
            self._boltzgen_tables()
        limits = {"top": max(1, len(self.designs)) if self.designs else None}
        self.opt = rk.options(OPTIONS[tool], raw or {},
                              {k: v for k, v in limits.items() if v})
        self.limits = {k: v for k, v in limits.items() if v}
        self.ranked = self._ranked()
        self.shown = self.ranked[:self.opt["top"]]

    def _beside(self, name):
        """A file the job wrote, wherever this run keeps it.

        A run queued from the page or from a flow keeps plan.json and the tool's output inside the
        design job's own folder; a run queued by hand keeps plan.json at the top of the run folder
        and the output below it. Both are real runs and the report opens both, so each file is
        looked for in the job folder and then beside it.
        """
        here = os.path.join(self.job, name)
        if os.path.exists(here):
            return here
        there = os.path.join(self.run, name)
        return there if os.path.exists(there) else here

    # -------------------------------------------------------------- the numbers

    def _boltzgen_tables(self):
        """The metrics CSV BoltzGen actually left, named rather than guessed.

        The file carries the budget in its name (final_designs_metrics_30.csv is
        final_designs_metrics_<budget>.csv), so it is globbed and the one read is said in the
        report rather than hard-coded at the CLI's default.
        """
        folder = os.path.join(self.job, "final_ranked_designs")
        found = sorted(glob.glob(os.path.join(folder, "final_designs_metrics_*.csv")))
        if not found:
            found = sorted(glob.glob(os.path.join(folder, "all_designs_metrics.csv")))
        if found:
            self.csv_name = os.path.relpath(found[0], self.run)
            self.csv = _csv_rows(found[0])
        every = os.path.join(folder, "all_designs_metrics.csv")
        self.all_csv = _csv_rows(every) if os.path.isfile(every) else []

    def _ranked(self):
        """The designs, best first, on the number the run itself ranked them by.

        ProteinMPNN's score for the design tools that use it (lower is better) and BoltzGen's own
        final_rank, which is the only thing its designs.json carries.
        """
        rows = []
        for one in self.designs:
            rows.append(dict(one, _score=rk.number(one.get("score"))))
        if self.tool == "boltzgen":
            return sorted(rows, key=lambda r: (r["_score"] is None, r["_score"] or 0))
        return sorted(rows, key=lambda r: (r["_score"] is None, r["_score"] or 0))

    def plddt_of(self, design):
        """The pLDDT summary of one design, as the design helper now writes it into designs.json.

        It can only come from there. The trajectory lives in rfdiffusion/<name>.trb, a pickle of
        numpy arrays, and this process cannot open it: the page is stdlib only. The helper runs
        inside SE3nv, where numpy is, so it writes the summary as it goes. A run made before that
        has no block here and the report says so rather than leaving a column mysteriously empty.
        """
        got = design.get("confidence") or {}
        if not isinstance(got, dict) or not got:
            return {}
        return got

    def have_plddt(self):
        return any(self.plddt_of(d) for d in self.designs)

    def residues_of(self, design):
        got = self.plddt_of(design).get("designed_length")
        return rk.whole(got) if got else len(str(design.get("sequence") or "")) or None

    def samples_of(self, name):
        rows, head = _fasta_samples(
            os.path.join(self.job, "proteinmpnn", str(name), "seqs", "%s.fa" % name))
        return rows, head

    def mpnn_model(self):
        for one in self.designs:
            _, head = self.samples_of(one.get("name"))
            if head.get("model"):
                return head["model"]
        return ""

    @staticmethod
    def _by_number(name):
        """A residue label sorted as a person reads it: A54 before A123, not after it."""
        got = re.match(r"([A-Za-z]*)(\d*)", str(name))
        return (got.group(1), int(got.group(2) or 0))

    def hotspots(self):
        """The hotspots as the person gave them, in the target's own numbering."""
        if self.tool == "rfd3":
            spec = (self.metrics.get((self.designs or [{}])[0].get("name")) or {})
            asked = ((spec.get("specification") or {}).get("select_hotspots") or {})
            if asked:
                return list(asked)
            given = rk.read_json(self._beside("rfd3_input.json"), {}) or {}
            return list(((given.get("design") or {}).get("select_hotspots") or {}))
        raw = str(self.plan.get("hotspots") or "")
        chain = str(self.plan.get("chains") or "A").split(",")[0].strip() or "A"
        out = []
        for part in raw.replace(";", ",").split(","):
            part = part.strip()
            if not part:
                continue
            out.append(part if part[0].isalpha() else chain + part)
        return out

    def renumbered(self):
        """The target's own numbering against the output's, for the hotspots only.

        This is the one thing a reader cannot work out for themselves, and both tools record it:
        RFdiffusion3 writes diffused_index_map, and RFdiffusion writes the pair of index lists the
        helper now folds into designs.json.
        """
        out = {}
        if self.tool == "rfd3":
            best = (self.shown or self.designs or [{}])[0].get("name")
            got = (self.metrics.get(best) or {}).get("diffused_index_map") or {}
            for name in self.hotspots():
                if name in got:
                    out[name] = got[name]
            return out
        for design in (self.shown or self.designs):
            got = (design.get("confidence") or {}).get("hotspot_map") or {}
            for name in self.hotspots():
                if name in got:
                    out[name] = got[name]
            if out:
                break
        return out

    # -------------------------------------------------------------- the document

    def build(self):
        version = pagedocs.upstream_version(self.tool, self.install or "") or ""
        from .. import __version__ as labs_version
        if not self.designs:
            return {"error": "this run has written no designs.json yet, so there is nothing to "
                             "report: %s" % self._where_it_got_to()}
        doc = {
            "kicker": KICKER[self.tool],
            "title": self.sub.get("name") or os.path.basename(self.run.rstrip(os.sep)),
            "subtitle": self.subtitle(),
            "byline": rk.byline("HOPE Labs", labs_version,
                                submitted=str(self.sub.get("submitted") or ""),
                                who=str(self.sub.get("user") or "")),
            "path": self.run,
            "options": rk.echo(OPTIONS[self.tool], self.opt, self.limits,
                               drop=self._drop_options()),
            "notes": self.notes(),
            "sections": [s for s in (self.summary(), self.designs_section(),
                                     self.middle(), self.population(), self.sequences(),
                                     self.pose(), self.asked()) if s],
            "views_hint": "None yet. This page has no viewer of its own: open the design in the "
                          "launcher's Results view, and the structure figure below is drawn by "
                          "the report itself from the run's own file.",
            "appendix": self.appendix() if self.opt["appendix"] else [],
        }
        doc["methods"], doc["references"] = self.methods(version) if self.opt["methods"] \
            else ("", [])
        return doc

    def _drop_options(self):
        """An option that cannot change anything is left off the bar rather than offered."""
        out = []
        if self.tool == "rfdiffusion" and not self.have_plddt():
            out.append("trace")
        if self.tool == "boltzgen" and not self.all_csv:
            out.append("population")
        return out

    def _where_it_got_to(self):
        log = runsmod.log_path(self.run)
        if not log:
            return "the design job has written no log in %s either" % os.path.basename(self.job)
        return "the last thing its log says is %r" % (rk.tail(log, 1).strip()[:160] or "nothing")

    def subtitle(self):
        target = (self.sub.get("target") or {}).get("pdb_id") or \
            os.path.basename(str(self.plan.get("target") or "")) or "a target"
        chains = str(self.plan.get("chains") or "")
        return "%s against %s%s" % (
            rk.e(TOOL_NAME[self.tool]), rk.e(target),
            " chain%s %s" % ("" if len(chains.split(",")) == 1 else "s", rk.e(chains))
            if chains else "")

    def notes(self):
        """What must be read before anything else."""
        out = []
        if self.tool in ("rfdiffusion", "rfd3", "rfd4"):
            out.append({"kind": "p", "tone": "warn", "html":
                        "Nothing in this run independently confirms that any of these designs "
                        "folds as drawn. This install runs no refolding step: there is no "
                        "AlphaFold initial guess and no Rosetta relaxation, so the only "
                        "assessment below is the design model's own. " + (
                            "The pLDDT values are RFdiffusion's, from its structure module "
                            "during sampling."
                            if self.tool == "rfdiffusion" else
                            "RFD4-Proteina was run with no scoring composed at all, which is "
                            "what keeps a design run off the refolding oracles: there is no "
                            "number here to rank these designs by, and fitting one is the next "
                            "step rather than something this run withheld."
                            if self.tool == "rfd4" else
                            "RFdiffusion3 reports no confidence of any kind: the geometry "
                            "figures below say whether a backbone is sound, not whether it "
                            "binds.")})
        if self.tool == "rfd3" and not any(
                (self.metrics.get(d.get("name")) or {}) for d in self.designs):
            out.append({"kind": "p", "tone": "warn", "html":
                        "No design in this run has its metrics JSON beside it, so the geometry "
                        "table and figure are empty: only designs.json could be read."})
        if self.sub.get("dry_run"):
            out.append({"kind": "p", "tone": "warn",
                        "html": "This run was queued as a dry run, so no job was submitted."})
        if self.tool == "rfdiffusion" and not self.have_plddt():
            out.append({"kind": "p", "tone": "caveat", "html":
                        "This run predates the design helper writing its pLDDT summary into "
                        "<code>designs.json</code>, so the confidence column and the two pLDDT "
                        "figures are left out. The numbers exist in "
                        "<code>rfdiffusion/&lt;design&gt;.trb</code>, which is a pickle of numpy "
                        "arrays this page cannot open; a run queued since carries them."})
        return out

    # -------------------------------------------------------------- 1. summary

    def summary(self):
        blocks = [{"kind": "p", "html": self.summary_words()}]
        blocks.append(rk.facts(self.summary_facts()))
        return {"id": "summary", "heading": "Summary", "label": "summary", "blocks": blocks}

    def summary_words(self):
        asked = rk.whole(self.plan.get("designs"))
        got = len(self.designs)
        lo, hi = rk.whole(self.plan.get("binder_min")), rk.whole(self.plan.get("binder_max"))
        hot = self.hotspots()
        bits = []
        bits.append("%s was asked for %s against %s, and wrote %s"
                    % (rk.e(TOOL_NAME[self.tool]),
                       "a binder of %s to %s residues" % (lo, hi) if lo and hi else "a binder",
                       rk.e(self.target_words()),
                       rk.count(got, "design")
                       + ("" if got == asked or not asked
                          else " of the %s asked for" % asked)))
        if hot:
            bits.append("the %s named as hotspots were %s"
                        % (rk.count(len(hot), "residue"), rk.and_list([rk.e(h) for h in hot])))
        best = self.shown[0] if self.shown else None
        # Only where something scored them. RFD4-Proteina's run composes no scoring at all, so
        # every design's score is null and a "best" sentence would be an ordering invented by
        # whatever order the files came back in.
        if best is not None and best.get("_score") is not None:
            bits.append("the best by %s is <b>%s</b> at %s"
                        % (self.measure(), rk.e(best.get("name")),
                           rk.num(best["_score"], 4 if self.tool != "boltzgen" else 0)))
        elif best is not None and self.tool == "rfd4":
            bits.append("nothing has ranked them: this run composed no scoring, so the designs "
                        "are listed in the order they were written")
        return rk.sentences(bits)

    def target_words(self):
        target = (self.sub.get("target") or {}).get("pdb_id") or \
            os.path.basename(str(self.plan.get("target") or "")) or "the target"
        chains = str(self.plan.get("chains") or "")
        return "%s%s" % (target, " chain %s" % chains if chains else "")

    def target_name(self):
        """The target as a caption names it: the entry, without the file's extension, so a
        possessive reads as one ("1YCR's A63") rather than as a path."""
        target = (self.sub.get("target") or {}).get("pdb_id") or \
            os.path.splitext(os.path.basename(str(self.plan.get("target") or "")))[0]
        return target or "the target"

    def measure(self):
        if self.tool == "boltzgen":
            return "BoltzGen's own ranking"
        # RFD4-Proteina ranks nothing: its designs come back unscored, because the run is
        # composed without the refolding oracles. Named rather than borrowed, so no caption
        # claims a ProteinMPNN score that was never computed.
        if self.tool == "rfd4":
            return "no measure"
        return "ProteinMPNN score"

    def summary_facts(self):
        rows = [("Tool", rk.e("%s %s" % (TOOL_NAME[self.tool],
                                         pagedocs.upstream_version(self.tool, self.install or "")
                                         or "(version read from the install, not the run)")))]
        rows.append(("Target", rk.e(str(self.plan.get("target") or "not recorded"))))
        rows.append(("Designs written", rk.e(rk.plural(len(self.designs), "design"))))
        if self.tool == "rfd3":
            sound = [d for d in self.designs
                     if (self.metrics.get(d.get("name")) or {}).get("metrics")
                     and not rk.whole((self.metrics[d["name"]]["metrics"]).get("n_chainbreaks"))
                     and not self._clashes(d)]
            rows.append(("Free of breaks and clashes",
                         rk.e("%s of %s" % (len(sound), len(self.designs)))))
        if self.tool == "boltzgen" and self.all_csv:
            passed = [r for r in self.all_csv if str(r.get("pass_filters", "")).lower()
                      in ("true", "1", "yes")]
            rows.append(("Passed every filter",
                         rk.e("%s of %s" % (len(passed), len(self.all_csv)))))
        job = str(self.sub.get("job_id") or "")
        if job:
            state, elapsed = runsmod.finished_state(job)
            rows.append(("Job", rk.e("%s%s" % (job, ", %s in %s" % (state, elapsed)
                                               if state else ""))))
        return rows

    # -------------------------------------------------------------- 2. the ranking

    def designs_section(self):
        return {"id": "designs", "heading": "The designs", "label": "designs",
                "blocks": [self.ranking_table()]}

    def _shared_columns(self):
        return [{"key": "name", "label": "Design"},
                {"key": "score", "label": self.score_label(), "num": True,
                 "digits": 4 if self.tool != "boltzgen" else 0,
                 "help": self.score_help()},
                {"key": "residues", "label": "Residues", "num": True, "digits": 0,
                 "help": "The designed chain only"},
                {"key": "binder", "label": "Binder chain"},
                {"key": "target", "label": "Target chains"}]

    def score_label(self):
        if self.tool == "boltzgen":
            return "Rank"
        return "Score" if self.tool == "rfd4" else "ProteinMPNN score"

    def score_help(self):
        if self.tool == "boltzgen":
            return "BoltzGen's own final rank over its scaled per-metric ranks; 1 is best"
        if self.tool == "rfd4":
            return ("Empty for every design, and not an omission: this run composed no scoring, "
                    "so nothing has refolded or ranked them. Fold them to get a number")
        return ("ProteinMPNN's score for the designed chain, lower is better; it says how well "
                "the sequence suits the backbone and nothing about affinity")

    def ranking_table(self):
        columns = self._shared_columns()
        rows = []
        for design in self.shown:
            row = {"name": design.get("name") or "",
                   "score": design["_score"],
                   "residues": self.residues_of(design),
                   "binder": ",".join(design.get("binder_chains") or []),
                   "target": ",".join(design.get("target_chains") or [])}
            row.update(self._tool_columns(design))
            if design is self.shown[0]:
                row["_class"] = "rk-mark"
            rows.append(row)
        columns += self._tool_column_specs()
        columns = [c for c in columns
                   if any(r.get(c["key"]) not in (None, "") for r in rows)]
        return {"kind": "table", "id": "ranking", "columns": columns, "rows": rows,
                "caption": self.ranking_caption(), "empty": "–",
                "note": self.ranking_note()}

    def ranking_note(self):
        if self.tool == "rfd3":
            return ("<i>Chain breaks</i>, <i>Max CA step</i> and <i>Clashes</i> are the model's "
                    "own geometry figures and are not a confidence: a sound backbone may still "
                    "not bind.")
        if self.tool == "rfdiffusion" and self.have_plddt():
            return ("<i>Binder pLDDT</i> is RFdiffusion's own, at the last diffusion step and "
                    "over the designed residues only.")
        return ""

    def _tool_columns(self, design):
        if self.tool == "rfdiffusion":
            got = self.plddt_of(design)
            return {"plddt": got.get("final_mean"), "seconds": got.get("seconds")}
        if self.tool == "rfd3":
            block = (self.metrics.get(design.get("name")) or {}).get("metrics") or {}
            return {"breaks": rk.whole(block.get("n_chainbreaks")),
                    "castep": rk.number(block.get("max_ca_deviation")),
                    "clashes": self._clashes(design),
                    "helix": rk.number(block.get("helix_fraction"))}
        # Nothing per design beyond the shared columns. Said explicitly rather than falling into
        # BoltzGen's branch below, which would look this design up in a metrics CSV that a
        # RFD4-Proteina run never writes.
        if self.tool == "rfd4":
            return {}
        row = self._csv_for(design)
        return {"iptm": _column(row, "design_to_target_iptm", "design_iptm"),
                "minpae": _column(row, "min_design_to_target_pae", "min_interaction_pae"),
                "ptm": _column(row, "design_ptm"),
                "rmsd": _column(row, "filter_rmsd", "rmsd"),
                "sasa": _column(row, "delta_sasa_refolded", "delta_sasa_original"),
                "hbonds": _column(row, "plip_hbonds_refolded", "plip_hbonds"),
                "passed": _column(row, "num_filters_passed"),
                "quality": _column(row, "quality_score")}

    def _tool_column_specs(self):
        if self.tool == "rfdiffusion":
            return [{"key": "plddt", "label": "Binder pLDDT", "num": True, "digits": 3,
                     "help": "RFdiffusion's own, 0 to 1, higher is better: the last diffusion "
                             "step, designed residues only",
                     "empty": "not recorded"},
                    {"key": "seconds", "label": "Diffusion (s)", "num": True, "digits": 1}]
        if self.tool == "rfd3":
            return [{"key": "breaks", "label": "Chain breaks", "num": True, "digits": 0,
                     "help": "0 is wanted"},
                    {"key": "castep", "label": "Max CA step (Å)", "num": True, "digits": 2,
                     "help": "How far the largest CA-CA step departs from the standard 3.8 "
                             "Å spacing; lower is better"},
                    {"key": "clashes", "label": "Clashes", "num": True, "digits": 0,
                     "help": "Backbone and side-chain inter-residue clashes together; 0 is "
                             "wanted"},
                    {"key": "helix", "label": "Helix", "num": True, "digits": 2,
                     "help": "Fraction of the designed chain assigned as helix"}]
        if self.tool == "rfd4":
            return []
        return [{"key": "iptm", "label": "iPTM to target", "num": True, "digits": 3,
                 "help": "Higher is better; the model's own confidence in the interface, not a "
                         "measure of affinity"},
                {"key": "minpae", "label": "Min PAE (Å)", "num": True, "digits": 2,
                 "help": "Lower is better"},
                {"key": "ptm", "label": "pTM", "num": True, "digits": 3},
                {"key": "rmsd", "label": "Refold RMSD (Å)", "num": True, "digits": 2,
                 "help": "Lower is better; backbone, when inverse folding ran"},
                {"key": "sasa", "label": "ΔSASA (Å²)", "num": True, "digits": 0,
                 "help": "Higher is better"},
                {"key": "hbonds", "label": "H-bonds", "num": True, "digits": 0},
                {"key": "passed", "label": "Filters passed", "num": True, "digits": 0},
                {"key": "quality", "label": "Quality", "num": True, "digits": 3}]

    def _clashes(self, design):
        block = (self.metrics.get(design.get("name")) or {}).get("metrics") or {}
        back = rk.whole(block.get("n_clashing.interresidue_clashes_w_backbone"))
        side = rk.whole(block.get("n_clashing.interresidue_clashes_w_sidechain"))
        if back is None and side is None:
            return None
        return (back or 0) + (side or 0)

    def _csv_for(self, design):
        want = str(design.get("name") or "")
        for row in self.csv:
            if str(row.get("id") or "") == want:
                return row
        return {}

    def ranking_caption(self):
        rows = self.shown
        if not rows:
            return rk.missing(
                "No design could be ranked: this run's designs.json carries none",
                "the design job's log says where it stopped")
        lead = rows[0]
        bits = []
        if self.tool == "boltzgen":
            return self._boltzgen_ranking_caption()
        if len(rows) == 1:
            bits.append("<b>%s</b> is the only design in this run, scoring %s over %s residues"
                        % (rk.e(lead.get("name")), rk.num(lead["_score"], 4),
                           self.residues_of(lead)))
        else:
            second = rows[1]
            bits.append("<b>%s</b> has the better ProteinMPNN score, %s over %s residues, ahead "
                        "of %s (%s, %s residues); lower is better"
                        % (rk.e(lead.get("name")), rk.num(lead["_score"], 4),
                           self.residues_of(lead), rk.e(second.get("name")),
                           rk.num(second["_score"], 4), self.residues_of(second)))
        if self.tool == "rfdiffusion":
            bits.append(self._plddt_clause(rows))
        else:
            bits.append(self._geometry_clause(rows))
        return rk.sentences(bits)

    def _plddt_clause(self, rows):
        got = [(r, self.plddt_of(r).get("final_mean")) for r in rows]
        got = [(r, v) for r, v in got if rk.number(v) is not None]
        if not got:
            return ("No confidence figure is recorded in this run, so the ranking rests on the "
                    "sequence alone, and neither the fold nor the interface has been checked")
        values = [rk.number(v) for _, v in got]
        if len(values) > 1 and max(values) - min(values) < 0.02:
            return ("%s end the diffusion within %s of each other on pLDDT, %s, so the "
                    "ranking rests on the sequence rather than the fold, and neither has been "
                    "refolded independently"
                    % (rk.cap(rk.count(len(values), "design")),
                       rk.num(max(values) - min(values), 3),
                       rk.and_list([rk.num(v, 3) for v in values])))
        return ("pLDDT over the designed residues runs from %s to %s at the last diffusion step, "
                "higher being better, and is RFdiffusion's own rather than an independent refold"
                % (rk.num(min(values), 3), rk.num(max(values), 3)))

    def _geometry_clause(self, rows):
        sound, breaks, clashes = [], [], []
        for row in rows:
            block = (self.metrics.get(row.get("name")) or {}).get("metrics") or {}
            if not block:
                continue
            nb = rk.whole(block.get("n_chainbreaks")) or 0
            nc = self._clashes(row) or 0
            breaks.append(nb)
            clashes.append(nc)
            if not nb and not nc:
                sound.append(row)
        if not breaks:
            return ("No design has its metrics JSON beside it, so nothing can be said here about "
                    "chain breaks or clashes")
        helix = [(r, rk.number(((self.metrics.get(r.get("name")) or {}).get("metrics")
                                or {}).get("helix_fraction")))
                 for r in rows]
        helix = [(r, v) for r, v in helix if v is not None]
        tail = ""
        if len(helix) > 1:
            best = max(helix, key=lambda p: p[1])
            worst = min(helix, key=lambda p: p[1])
            tail = (", so the choice between them is the sequence and the fold they adopt: %s is "
                    "%s per cent helix in %s elements, %s %s per cent in %s"
                    % (rk.e(best[0].get("name")), rk.pc(best[1]),
                       rk.whole(((self.metrics.get(best[0].get("name")) or {}).get("metrics")
                                 or {}).get("num_ss_elements")),
                       rk.e(worst[0].get("name")), rk.pc(worst[1]),
                       rk.whole(((self.metrics.get(worst[0].get("name")) or {}).get("metrics")
                                 or {}).get("num_ss_elements"))))
        if not any(breaks) and not any(clashes):
            return ("%s are free of chain breaks and clashes%s"
                    % (rk.cap(rk.count(len(breaks), "design")), tail))
        return ("%s of the %s are free of chain breaks and clashes; %s carry a break and %s a "
                "clash%s" % (len(sound), rk.count(len(breaks), "design"),
                             sum(1 for b in breaks if b), sum(1 for c in clashes if c), tail))

    def _boltzgen_ranking_caption(self):
        rows = self.shown
        if not self.csv:
            return rk.missing(
                "The %s in this run have a rank and nothing else: BoltzGen's metrics CSV is not "
                "in the run folder" % rk.plural(len(rows), "design"),
                "it is written by the filtering step, so a run whose folding step did not run "
                "has none")
        lead, row = rows[0], self._csv_for(rows[0])
        iptm = _column(row, "design_to_target_iptm", "design_iptm")
        pae = _column(row, "min_design_to_target_pae", "min_interaction_pae")
        sasa = _column(row, "delta_sasa_refolded", "delta_sasa_original")
        if iptm is None:
            return rk.missing(
                "The %s shown carry a rank but no confidence columns, so nothing here says how "
                "well any of them binds" % rk.plural(len(rows), "design"),
                "the columns come from the refolding step, which this run did not run")
        bits = ["Rank %s (<b>%s</b>) reaches an iPTM to the target of %s%s%s"
                % (rk.num(lead["_score"], 0), rk.e(lead.get("name")), rk.num(iptm, 3),
                   " with a minimum PAE of %s Å" % rk.num(pae, 2) if pae is not None else "",
                   " and buries %s Å² on refolding" % rk.num(sasa, 0)
                   if sasa is not None else "")]
        if len(rows) > 1:
            other = self._csv_for(rows[1])
            bits[-1] += (", against %s for rank %s"
                         % (rk.num(_column(other, "design_to_target_iptm", "design_iptm"), 3),
                            rk.num(rows[1]["_score"], 0)))
        passed = [r for r in rows
                  if rk.whole(_column(self._csv_for(r), "num_filters_passed")) is not None]
        bits.append("iPTM, ΔSASA and the hydrogen-bond count higher is better, PAE and both "
                    "RMSDs lower, and iPTM is the model's own confidence rather than a measure "
                    "of affinity")
        if passed:
            most = max(rk.whole(_column(self._csv_for(r), "num_filters_passed")) for r in passed)
            every = [r for r in passed
                     if rk.whole(_column(self._csv_for(r), "num_filters_passed")) == most]
            bits.append("%s of the %s shown passed all %s filters"
                        % (len(every), rk.plural(len(rows), "design"), most))
        return rk.sentences(bits)

    # -------------------------------------------------------------- 3. the middle

    def middle(self):
        """The section that is this tool's own: a trajectory, a geometry block or a funnel."""
        if self.tool == "rfdiffusion":
            return self.diffusion()
        if self.tool == "rfd3":
            return self.geometry()
        if self.tool == "rfd4":
            # Nothing of its own goes here. RFD4-Proteina's run reports no per-design numbers at
            # all - no confidence, no geometry block, no filter funnel - and the one thing worth
            # reading about it, what the model was told to hold in place, is stated in "What the
            # tool was asked for" below, beside the contig it was stated with.
            return None
        return self.filters()

    # --- RFdiffusion: how the designs converged

    def diffusion(self):
        if not self.have_plddt():
            return {"id": "diffusion", "heading": "How the designs converged",
                    "label": "diffusion", "blocks": [
                        {"kind": "p", "tone": "caveat", "html":
                         "This run records no pLDDT trajectory. RFdiffusion writes it only into "
                         "<code>rfdiffusion/&lt;design&gt;.trb</code>, a pickle of numpy arrays "
                         "that the page cannot open; the design helper now writes the summary "
                         "into <code>designs.json</code> as it goes, so a run queued since "
                         "carries the figures that belong here."}]}
        blocks = []
        if self.opt["trace"] != "none":
            blocks.append(self.trace_figure())
        blocks.append(self.residue_figure())
        return {"id": "diffusion", "heading": "How the designs converged", "label": "diffusion",
                "blocks": [b for b in blocks if b]}

    def trace_figure(self):
        which = self.designs if self.opt["trace"] == "all" else self.shown
        series = []
        for design in which:
            steps = self.plddt_of(design).get("steps") or []
            if len(steps) > 1:
                series.append({"name": design.get("name") or "",
                               "points": [(i + 1, rk.number(v)) for i, v in enumerate(steps)
                                          if rk.number(v) is not None]})
        svg = rk.line_chart(series, x_label="Diffusion step", y_label="pLDDT (designed residues)",
                            digits=2, y_range=(0.0, 1.0))
        if not svg:
            return None
        return {"kind": "figure", "id": "plddt_trace", "svg": svg,
                "caption": self.trace_caption(series)}

    def trace_caption(self, series):
        if not series:
            return rk.missing("No design in this run has a pLDDT trajectory recorded")
        firsts = [s["points"][0][1] for s in series]
        lasts = [s["points"][-1][1] for s in series]
        steps = len(series[0]["points"])
        lengths = [self.residues_of(d) for d in (self.designs
                                                 if self.opt["trace"] == "all" else self.shown)]
        lengths = [str(x) for x in lengths if x]
        bits = []
        if len(series) == 1:
            bits.append("The design rises from a pLDDT of %s at the first of %s steps to %s at "
                        "the last" % (rk.num(firsts[0], 3), steps, rk.num(lasts[0], 3)))
        else:
            bits.append("%s rise from a pLDDT of about %s at the first of %s steps to %s at "
                        "the last (%s)"
                        % (rk.cap(rk.count(len(series), "design")), rk.num(min(firsts), 2),
                           steps, rk.num(min(lasts), 2),
                           rk.and_list(["%s %s to %s" % (rk.e(s["name"]),
                                                         rk.num(s["points"][0][1], 3),
                                                         rk.num(s["points"][-1][1], 3))
                                        for s in series[:3]])))
        above = None
        for one in series:
            for at, value in one["points"]:
                if value >= 0.90:
                    above = at if above is None else max(above, at)
                    break
        if above:
            bits.append("%s above 0.90 by step %s"
                        % ("both are" if len(series) == 2 else
                           "the design is" if len(series) == 1 else "every one is", above))
        bits.append("higher is better, and each line is the mean over that design's %s designed "
                    "residues, the target's excluded"
                    % (rk.and_list(lengths) if lengths else "own"))
        return rk.sentences(bits)

    def residue_figure(self):
        if not self.shown:
            return None
        best = self.shown[0]
        got = self.plddt_of(best)
        values = [rk.number(v) for v in (got.get("residues") or [])
                  if rk.number(v) is not None]
        if len(values) < 2:
            return None
        mean = rk.number(got.get("final_mean"))
        svg = rk.value_chart(values, x_label="Residue of the designed chain", y_label="pLDDT",
                             mean_at=mean, bands=PLDDT_BANDS, digits=2, y_range=(0.0, 1.0))
        if not svg:
            return None
        return {"kind": "figure", "id": "plddt_residues", "svg": svg,
                "caption": self.residue_caption(best, values, mean)}

    def residue_caption(self, design, values, mean):
        lowest = min(values)
        where = values.index(lowest) + 1
        above = sum(1 for v in values if v >= 0.98)
        return rk.sentences([
            "<b>%s</b>'s weakest residue finishes at %s, at position %s, and its first residue "
            "at %s" % (rk.e(design.get("name")), rk.num(lowest, 3), where, rk.num(values[0], 3)),
            "%s of its %s positions are at 0.98 or above, for a mean of %s"
            % (above, len(values), rk.num(mean, 3) if mean is not None else "–"),
            "higher is better, and the dashed line is that mean"])

    # --- RFdiffusion3: the geometry block

    def geometry(self):
        if not self.opt["geometry"]:
            return None
        blocks = [b for b in (self.geometry_table(), self.ss_figure()) if b]
        if not blocks:
            return None
        return {"id": "geometry", "heading": "The geometry of each design", "label": "geometry",
                "blocks": blocks}

    #: The metrics block mixes scopes field by field and nothing in the run says so, so the scope
    #: travels in the column label and in the caption. Printing alanine_content as the design's
    #: alanine content would be wrong by the size of the target.
    GEOM = (("helix", "Helix", "helix_fraction", 2, "Fraction of the designed chain"),
            ("sheet", "Sheet", "sheet_fraction", 2, "Fraction of the designed chain"),
            ("loop", "Loop", "loop_fraction", 2, "Fraction of the designed chain"),
            ("elements", "Elements", "num_ss_elements", 0,
             "Disjoint helices and sheets in the designed chain"),
            ("rg", "Rg (Å)", "radius_of_gyration", 1, "The designed chain"),
            ("castep", "Max CA step (Å)", "max_ca_deviation", 2,
             "Departure from the standard 3.8 Å CA-CA spacing; lower is better"),
            ("breaks", "Chain breaks", "n_chainbreaks", 0, "0 is wanted"),
            ("clash_b", "Clashes (backbone)", "n_clashing.interresidue_clashes_w_backbone", 0, ""),
            ("clash_s", "Clashes (side chain)", "n_clashing.interresidue_clashes_w_sidechain", 0,
             ""),
            ("ala", "Alanine (complex)", "alanine_content", 3,
             "Fraction of the WHOLE complex, as the model computes it, not of the design"),
            ("gly", "Glycine (complex)", "glycine_content", 3,
             "Fraction of the whole complex"),
            ("residues", "Residues (complex)", "num_residues", 0,
             "Every residue of the complex, design and target together"))

    def geometry_table(self):
        rows = []
        for design in self.shown:
            block = (self.metrics.get(design.get("name")) or {}).get("metrics") or {}
            if not block:
                continue
            row = {"name": design.get("name") or ""}
            for key, _, source, _, _ in self.GEOM:
                row[key] = rk.number(block.get(source))
            if not rows:
                row["_class"] = "rk-mark"
            rows.append(row)
        columns = [{"key": "name", "label": "Design"}] + [
            dict({"key": key, "label": label, "num": True, "digits": digits},
                 **({"help": help_text} if help_text else {}))
            for key, label, _, digits, help_text in self.GEOM]
        return {"kind": "table", "id": "geometry", "columns": columns, "rows": rows,
                "caption": self.geometry_caption(rows), "empty": "–",
                "note": "The scope of each column is not cosmetic: secondary structure and the "
                        "radius of gyration are computed on the diffused region alone, and the "
                        "composition and the residue count over the whole complex, as the model "
                        "computes them."}

    def geometry_caption(self, rows):
        if not rows:
            return rk.missing(
                "No design in this run has a metrics JSON beside it, so none of the geometry "
                "can be shown",
                "the model writes one per design next to its structure")
        helixes = [(r["name"], r.get("helix"), r.get("elements"), r.get("rg")) for r in rows
                   if r.get("helix") is not None]
        clean = [r for r in rows if not (r.get("breaks") or 0)
                 and not ((r.get("clash_b") or 0) + (r.get("clash_s") or 0))]
        bits = []
        if helixes:
            best = max(helixes, key=lambda p: p[1])
            worst = min(helixes, key=lambda p: p[1])
            bits.append("%s"
                        % ("%s are helical bundles with no chain break and no clash"
                           % rk.cap(rk.count(len(rows), "design"))
                           if len(clean) == len(rows) and best[1] > 0.5 else
                           "%s of the %s are free of breaks and clashes"
                           % (len(clean), rk.count(len(rows), "design"))))
            if best[0] != worst[0]:
                bits[-1] += (": <b>%s</b> is %s per cent helix in %s elements, %s %s per cent in "
                             "%s, with radii of gyration of %s and %s Å over the designed "
                             "residues"
                             % (rk.e(best[0]), rk.pc(best[1]), rk.whole(best[2]),
                                rk.e(worst[0]), rk.pc(worst[1]), rk.whole(worst[2]),
                                rk.num(best[3], 1), rk.num(worst[3], 1)))
            else:
                bits[-1] += (": %s per cent helix in %s elements, radius of gyration %s Å"
                             % (rk.pc(best[1]), rk.whole(best[2]), rk.num(best[3], 1)))
        alas = [r.get("ala") for r in rows if r.get("ala") is not None]
        residues = [rk.whole(r.get("residues")) for r in rows if r.get("residues")]
        if alas and residues:
            bits.append("the alanine fractions, %s per cent, are over all %s residues of the "
                        "complex rather than the design alone, as the model computes them"
                        % (rk.and_list([rk.pc(a, 1) for a in alas[:3]]), max(residues)))
        return rk.sentences(bits)

    def ss_figure(self):
        rows = []
        for design in self.shown:
            block = (self.metrics.get(design.get("name")) or {}).get("metrics") or {}
            if not block:
                continue
            rows.append({"name": design.get("name") or "",
                         "helix": rk.number(block.get("helix_fraction")) or 0.0,
                         "sheet": rk.number(block.get("sheet_fraction")) or 0.0,
                         "loop": rk.number(block.get("loop_fraction")) or 0.0,
                         "elements": "%s element%s" % (
                             rk.whole(block.get("num_ss_elements")),
                             "" if rk.whole(block.get("num_ss_elements")) == 1 else "s")})
        svg = rk.stacked_chart(rows, [("helix", "Helix", "#1a56db"),
                                      ("sheet", "Sheet", "#2f9e6e"),
                                      ("loop", "Loop", "#8d9cb3")], note_key="elements",
                               axis="fraction of the designed chain")
        if not svg:
            return None
        return {"kind": "figure", "id": "ss_bars", "svg": svg,
                "caption": self.ss_caption(rows)}

    def ss_caption(self, rows):
        if not rows:
            return rk.missing("No design has a metrics JSON, so there is no composition to draw")
        sheet = [r for r in rows if r["sheet"] > 0.005]
        best = max(rows, key=lambda r: r["helix"])
        worst = min(rows, key=lambda r: r["helix"])
        bits = []
        if not sheet:
            bits.append(("No sheet or strand appears in %s"
                     % ("either design" if len(rows) == 2
                        else "any of the %s" % rk.count(len(rows), "design")))
                    if len(rows) > 1 else "The design has no sheet at all")
        else:
            bits.append("%s of the %s carry some sheet, the most %s per cent"
                        % (len(sheet), rk.plural(len(rows), "design"),
                           rk.pc(max(r["sheet"] for r in sheet))))
        if best["name"] != worst["name"]:
            bits[-1] += (": <b>%s</b> is %s per cent helix and %s per cent loop in %s, %s %s and "
                         "%s per cent in %s"
                         % (rk.e(best["name"]), rk.pc(best["helix"]), rk.pc(best["loop"]),
                            best["elements"], rk.e(worst["name"]), rk.pc(worst["helix"]),
                            rk.pc(worst["loop"]), worst["elements"]))
        else:
            bits[-1] += (": %s per cent helix and %s per cent loop in %s"
                         % (rk.pc(best["helix"]), rk.pc(best["loop"]), best["elements"]))
        bits.append("the bars are fractions of the designed chain, assigned by P-SEA from the CA "
                    "trace")
        return rk.sentences(bits)

    # --- BoltzGen: what the filters cost, and the population

    def filters(self):
        blocks = []
        if self.opt["filters"]:
            blocks += [b for b in (self.filter_table(), self.filter_figure()) if b]
        if not blocks:
            return None
        return {"id": "filters", "heading": "What the filters cost", "label": "filters",
                "blocks": blocks}

    #: filter.py's own defaults, the fallback when the run's resolved configuration cannot be
    #: read. Printing a threshold that was overridden would be worse than printing none, so the
    #: report says which of the two it used.
    FILTER_DEFAULTS = {"has_x": ("≤", 0), "filter_rmsd": ("≤", 2.5),
                       "filter_rmsd_design": ("≤", 2.5),
                       "designfolding-filter_rmsd": ("≤", 2.5),
                       "CYS_fraction": ("≤", 0), "ALA_fraction": ("≤", 0.3),
                       "GLY_fraction": ("≤", 0.3), "GLU_fraction": ("≤", 0.3),
                       "LEU_fraction": ("≤", 0.3), "VAL_fraction": ("≤", 0.3)}

    def _filter_counts(self):
        """One row per hard filter: how many designs passed it, of how many."""
        if not self.all_csv:
            return [], 0
        names = [k for k in (self.all_csv[0] or {})
                 if k.startswith("pass_") and k.endswith("_filter")]
        out = []
        for key in names:
            feature = key[len("pass_"):-len("_filter")]
            passed = sum(1 for r in self.all_csv
                         if str(r.get(key, "")).strip().lower() in ("true", "1", "yes"))
            way, limit = self.FILTER_DEFAULTS.get(feature, ("", None))
            out.append({"filter": feature, "way": way,
                        "limit": limit, "passed": passed, "of": len(self.all_csv)})
        every = sum(1 for r in self.all_csv
                    if str(r.get("pass_filters", "")).strip().lower() in ("true", "1", "yes"))
        return out, every

    def filter_table(self):
        rows, every = self._filter_counts()
        if not rows:
            return None
        where, unread = self._filter_config()
        for row in rows:
            got = where.get(row["filter"])
            if got is not None:
                row["limit"] = rk.number(got)
                row["source"] = "the run's own configuration"
            else:
                row["source"] = "filter.py's default"
        rows.append({"filter": "every filter", "way": "", "limit": None, "passed": every,
                     "of": len(self.all_csv), "source": "", "_class": "rk-mark"})
        return {"kind": "table", "id": "filters",
                "columns": [{"key": "filter", "label": "Filter"},
                            {"key": "way", "label": "Direction"},
                            {"key": "limit", "label": "Threshold", "num": True, "digits": 2},
                            {"key": "passed", "label": "Designs passed", "num": True,
                             "digits": 0},
                            {"key": "of", "label": "Of", "num": True, "digits": 0},
                            {"key": "source", "label": "Threshold from",
                             "help": "Which file the threshold was read from; a default is "
                                     "printed only where the run's own configuration could not "
                                     "be read"}],
                "rows": rows, "caption": self.filter_caption(rows[:-1], every),
                "note": ("A design that fails a filter is pushed to the bottom of the ranking "
                         "rather than deleted." + (
                             " %d %s of the run's filtering configuration %s not "
                             "understood by this report's narrow reader and %s left out."
                             % (len(unread), "line" if len(unread) == 1 else "lines",
                                "was" if len(unread) == 1 else "were",
                                "was" if len(unread) == 1 else "were")
                             if unread else ""))}

    def _filter_config(self):
        path = self._beside(os.path.join("config", "filtering.yaml"))
        if not os.path.isfile(path):
            return {}, []
        return flat_yaml(path)

    def filter_caption(self, rows, every):
        if not rows:
            return rk.missing("Nothing in this run records which filters were applied")
        total = rows[0]["of"]
        worst = min(rows, key=lambda r: r["passed"])
        bits = ["Of %s, %s passed every filter"
                % (rk.many(total, "design"), rk.words(every) if every < 13 else every)]
        bits[-1] += (", and the narrowest single filter is <b>%s</b>, which %s passed"
                     % (rk.e(worst["filter"]), rk.num(worst["passed"], 0)))
        bits.append("lower is better for every RMSD and composition threshold, and a design that "
                    "fails any one of them is ranked last rather than removed")
        if not every:
            bits.append("With nothing through every filter, the thresholds or the target need "
                        "revisiting rather than the ranking reading")
        return rk.sentences(bits)

    def filter_figure(self):
        rows, every = self._filter_counts()
        if not rows:
            return None
        points = [{"name": r["filter"], "v": r["passed"]} for r in rows]
        points.append({"name": "every filter", "v": every, "colour": rk.WARM})
        svg = rk.ranked_chart(points, axis="designs passed, of %s" % len(self.all_csv), digits=0)
        if not svg:
            return None
        worst = min(rows, key=lambda r: r["passed"])
        return {"kind": "figure", "id": "filter_bars", "svg": svg,
                "caption": rk.sentences([
                    "%s of %s pass every filter, the bar in the warm color; the narrowest "
                    "single filter is %s at %s"
                    % (rk.words(every), rk.many(len(self.all_csv), "design"),
                       rk.e(worst["filter"]), rk.num(worst["passed"], 0)),
                    "the bars are counts, read in the order the filtering step applies them"])}

    def population(self):
        """The selected designs against the whole population they were chosen from."""
        if self.tool != "boltzgen" or not self.opt.get("population") or not self.all_csv:
            return None
        blocks = [b for b in (self.population_figure(), self.sasa_figure()) if b]
        if not blocks:
            return None
        return {"id": "population", "heading": "The selected designs against every design",
                "label": "population", "blocks": blocks}

    def _picked(self):
        return {str(r.get("id") or "") for r in self.csv}

    def population_figure(self):
        picked, points = self._picked(), []
        best = str(self.shown[0].get("name")) if self.shown else ""
        for row in self.all_csv:
            x = _column(row, "design_to_target_iptm", "design_iptm")
            y = _column(row, "min_design_to_target_pae", "min_interaction_pae")
            if x is None or y is None:
                continue
            name = str(row.get("id") or "")
            points.append({"x": x, "y": y, "name": name, "pick": name in picked,
                           "label": "rank 1" if name == best else ""})
        svg = rk.scatter_chart(points, x_label="iPTM to the target, higher is better",
                               y_label="minimum PAE (Å), lower is better", digits=2)
        if not svg:
            return None
        chosen = [p for p in points if p["pick"]]
        return {"kind": "figure", "id": "population", "svg": svg,
                "caption": self.population_caption(points, chosen)}

    def population_caption(self, points, chosen):
        if not chosen:
            return rk.missing(
                "None of the %s in the metrics table could be matched to the selected set by its "
                "id, so nothing is marked" % rk.plural(len(points), "design"))
        xs = [p["x"] for p in chosen]
        ys = [p["y"] for p in chosen]
        allx = [p["x"] for p in points]
        ally = [p["y"] for p in points]
        return rk.sentences([
            "The %s selected sit at an iPTM of %s to %s and a minimum PAE of %s to %s Å, "
            "against %s to %s and %s to %s Å over all %s"
            % (rk.words(len(chosen)), rk.num(min(xs), 3), rk.num(max(xs), 3), rk.num(min(ys), 2),
               rk.num(max(ys), 2), rk.num(min(allx), 3), rk.num(max(allx), 3),
               rk.num(min(ally), 2), rk.num(max(ally), 2), rk.many(len(points), "design")),
            "higher iPTM and lower PAE are better, and the labeled point is rank 1"])

    def sasa_figure(self):
        values, picked = [], self._picked()
        kept = []
        for row in self.all_csv:
            got = _column(row, "delta_sasa_refolded", "delta_sasa_original")
            if got is None:
                continue
            values.append(got)
            if str(row.get("id") or "") in picked:
                kept.append(got)
        svg = rk.histogram(values, axis="buried area ΔSASA (Å²), higher is better",
                           keep=(min(kept), max(kept)) if kept else None, digits=0)
        if not svg:
            return None
        middle = sorted(values)[len(values) // 2]
        return {"kind": "figure", "id": "sasa_hist", "svg": svg,
                "caption": rk.sentences([
                    "The selected designs bury %s to %s Å², against a median of %s "
                    "Å² over all %s"
                    % (rk.num(min(kept), 0) if kept else "–",
                       rk.num(max(kept), 0) if kept else "–", rk.num(middle, 0),
                       rk.many(len(values), "design")),
                    "higher is better, the marked band is the selected range, and buried area is "
                    "the figure that separates a real interface from a confident touch"])}

    # -------------------------------------------------------------- 4. sequences

    def sequences(self):
        if self.tool == "boltzgen":
            return self.order_section()
        if self.tool == "rfd4":
            return self.rfd4_sequences()
        rows, columns = self.sequence_rows()
        if not rows:
            return None
        return {"id": "sequences", "heading": "The sequences", "label": "sequences",
                "blocks": [{"kind": "table", "id": "sequences", "columns": columns,
                            "rows": rows, "caption": self.sequence_caption(rows),
                            "empty": "–",
                            "note": "The sequence handed on to the rest of the lab is "
                                    "ProteinMPNN's lowest-scoring one for each backbone."}]}

    def rfd4_sequences(self):
        """The designed sequences, read out of designs.json.

        There is no sequence file to read and no second model to compare against: RFD4-Proteina
        designs the sequence along with the backbone, so each design has exactly one and it is
        the one recorded. That makes this table shorter than the others on purpose - a column for
        a ProteinMPNN score would be a column that could never be filled.
        """
        if not self.opt.get("sequences"):
            return None
        rows = []
        for design in self.shown:
            sequence = str(design.get("sequence") or "")
            if not sequence:
                continue
            rows.append({"name": design.get("name") or "",
                         "binder": ",".join(design.get("binder_chains") or []),
                         "length": len(sequence),
                         "sequence": sequence,
                         "_class": "rk-mark" if design is self.shown[0] else ""})
        if not rows:
            return {"id": "sequences", "heading": "The sequences", "label": "sequences",
                    "blocks": [{"kind": "p", "html": rk.missing(
                        "No design in this run carries a sequence",
                        "the helper reads each design's own chain out of the structure it "
                        "wrote, into designs.json")}]}
        return {"id": "sequences", "heading": "The sequences", "label": "sequences",
                "blocks": [{"kind": "table", "id": "sequences",
                            "columns": [{"key": "name", "label": "Design"},
                                        {"key": "binder", "label": "Binder chain"},
                                        {"key": "length", "label": "Length", "num": True,
                                         "digits": 0},
                                        {"key": "sequence", "label": "Sequence"}],
                            "rows": rows,
                            # rk.count rather than rk.plural everywhere else, but not here: it
                            # writes two as "both", and "The both sequences" is not a sentence.
                            "caption": "%s, as RFD4-Proteina designed them. Nothing has scored "
                                       "or refolded them, so the order is the order they were "
                                       "written in and the first row is marked only because it "
                                       "is first."
                                       % rk.cap(rk.plural(len(rows), "sequence")),
                            "empty": "–"}]}

    def sequence_rows(self):
        rows = []
        for design in self.shown:
            name = design.get("name")
            samples, head = self.samples_of(name)
            if self.tool == "rfd3" and self.opt.get("rfd3seq") and head.get("sequence"):
                rows.append({"name": name, "source": "RFdiffusion3",
                             "score": rk.number(head.get("score")),
                             "global": rk.number(head.get("global_score")),
                             "recovery": None,
                             "length": len(head["sequence"]),
                             "sequence": head["sequence"], "_class": "rk-dim"})
            kept = sorted([s for s in samples if s["score"] is not None],
                          key=lambda s: s["score"])[:1]
            for sample in (samples if self.opt["allseqs"] else kept):
                rows.append({"name": name,
                             "source": "sample %s" % sample["sample"],
                             "score": sample["score"], "global": sample["global"],
                             "recovery": sample["recovery"], "length": sample["length"],
                             "sequence": sample["sequence"],
                             "_class": "rk-mark" if sample in kept else ""})
        columns = [{"key": "name", "label": "Design"},
                   {"key": "source", "label": "Source"},
                   {"key": "score", "label": "Score", "num": True, "digits": 4,
                    "help": "ProteinMPNN's score for the designed chain; lower is better"},
                   {"key": "global", "label": "Global score", "num": True, "digits": 4,
                    "help": "The whole complex rather than the designed chain; lower is better"},
                   {"key": "recovery", "label": "Recovery", "num": True, "digits": 3,
                    "help": self.recovery_help()},
                   {"key": "length", "label": "Length", "num": True, "digits": 0},
                   {"key": "sequence", "label": "Sequence"}]
        return rows, columns

    def recovery_help(self):
        if self.tool == "rfd3":
            return ("The fraction of RFdiffusion3's own sequence for that backbone which "
                    "ProteinMPNN kept")
        return ("The fraction of the backbone's own sequence kept. RFdiffusion writes a "
                "poly-glycine backbone, so this says nothing about the design")

    def sequence_caption(self, rows):
        by_design = {}
        for row in rows:
            by_design.setdefault(row["name"], []).append(row)
        if self.tool == "rfd3":
            return self._rfd3_sequence_caption(by_design)
        bits = []
        pairs = [(name, sorted([r["score"] for r in got if r["score"] is not None]))
                 for name, got in by_design.items()]
        pairs = [(n, s) for n, s in pairs if s]
        if not pairs:
            return rk.missing("No ProteinMPNN sequence file could be read for any design",
                              "they are written to proteinmpnn/<design>/seqs/<design>.fa")
        if self.opt["allseqs"] and any(len(s) > 1 for _, s in pairs):
            bits.append("ProteinMPNN's sequences score %s"
                        % rk.and_list(["%s for %s" % (rk.and_list([rk.num(v, 4) for v in s]),
                                                      rk.e(n)) for n, s in pairs[:3]]))
            close = [(n, s) for n, s in pairs if len(s) > 1 and s[1] - s[0] < 0.1]
            if close:
                bits.append("so in %s the kept sequence is barely ahead of the one discarded, by "
                            "%s or less; lower is better"
                            % ("every case" if len(close) == len(pairs) else
                               "%s of the %s" % (len(close), rk.plural(len(pairs), "design")),
                               rk.num(max(s[1] - s[0] for _, s in close), 3)))
            else:
                bits.append("lower is better")
        else:
            bits.append("The kept sequence for each design scores %s; lower is better"
                        % rk.and_list(["%s (%s)" % (rk.num(s[0], 4), rk.e(n))
                                       for n, s in pairs[:4]]))
        recoveries = [r["recovery"] for r in rows if r["recovery"] is not None]
        if recoveries:
            bits.append("the recovery column, %s to %s per cent, is against RFdiffusion's "
                        "poly-glycine backbone and says nothing about any of these designs"
                        % (rk.pc(min(recoveries), 0), rk.pc(max(recoveries), 0)))
        return rk.sentences(bits)

    def _rfd3_sequence_caption(self, by_design):
        pairs = []
        for name, got in by_design.items():
            own = [r for r in got if r["source"] == "RFdiffusion3"]
            mpnn = sorted([r for r in got if r["source"] != "RFdiffusion3"
                           and r["score"] is not None], key=lambda r: r["score"])
            if mpnn:
                pairs.append((name, mpnn[0], own[0] if own else None))
        if not pairs:
            return rk.missing("No ProteinMPNN sequence file could be read for any design")
        bits = []
        first = pairs[0]
        if first[2] is not None and first[2]["score"] is not None:
            bits.append("ProteinMPNN scores its own best sequence for <b>%s</b> at %s against %s "
                        "for the sequence RFdiffusion3 wrote for the same backbone"
                        % (rk.e(first[0]), rk.num(first[1]["score"], 4),
                           rk.num(first[2]["score"], 4)))
            if first[1]["recovery"] is not None:
                bits[-1] += ", and keeps %s per cent of it" % rk.pc(first[1]["recovery"])
            if len(pairs) > 1:
                rest = pairs[1]
                bits.append("for %s the figures are %s against %s%s"
                            % (rk.e(rest[0]), rk.num(rest[1]["score"], 4),
                               rk.num(rest[2]["score"], 4) if rest[2] else "–",
                               ", with %s per cent kept" % rk.pc(rest[1]["recovery"])
                               if rest[1]["recovery"] is not None else ""))
        else:
            bits.append("ProteinMPNN's best sequence for <b>%s</b> scores %s"
                        % (rk.e(first[0]), rk.num(first[1]["score"], 4)))
        bits.append("lower is better, and the sequence handed on is ProteinMPNN's, with the "
                    "model's own kept beside it")
        return rk.sentences(bits)

    def order_section(self):
        """BoltzGen: what would actually be ordered."""
        if not self.opt.get("sequences") or not self.csv:
            return None
        rows = []
        for design in self.shown:
            row = self._csv_for(design)
            if not row:
                continue
            chain = str(row.get("designed_chain_sequence") or "")
            segment = str(row.get("designed_sequence") or "")
            rows.append({"name": design.get("name"), "rank": design["_score"],
                         "designed": _column(row, "num_design"),
                         "order": chain,
                         "segment": segment if segment and segment != chain else "",
                         "ala": _column(row, "ALA_fraction"),
                         "liabilities": row.get("liability_details") or ""})
        if not rows:
            return None
        columns = [{"key": "name", "label": "Design"},
                   {"key": "rank", "label": "Rank", "num": True, "digits": 0},
                   {"key": "designed", "label": "Designed residues", "num": True, "digits": 0},
                   {"key": "order", "label": "Sequence to order",
                    "help": "The full sequence of the chain holding the designed residues, "
                            "which is what the tool recommends for synthesis"},
                   {"key": "segment", "label": "Designed segment"},
                   {"key": "ala", "label": "Alanine", "num": True, "digits": 3},
                   {"key": "liabilities", "label": "Liabilities"}]
        columns = [c for c in columns if any(r.get(c["key"]) not in (None, "") for r in rows)]
        lengths = [len(r["order"]) for r in rows if r["order"]]
        return {"id": "sequences", "heading": "What to order", "label": "to order", "blocks": [
            {"kind": "table", "id": "sequences", "columns": columns, "rows": rows,
             "caption": rk.sentences([
                 "The %s selected are %s to %s residues long"
                 % (rk.many(len(rows), "design"), min(lengths), max(lengths)) if lengths else
                 "The %s selected carry no sequence column in this run's metrics table"
                 % rk.many(len(rows), "design"),
                 "the column to order from is the full chain sequence, which is what the tool "
                 "itself recommends for synthesis, and it differs from the designed segment "
                 "where the protocol kept part of a scaffold"]),
             "empty": "–"}]}

    # -------------------------------------------------------------- 5. the pose

    def pose(self):
        if not self.opt["pose"] or not self.shown:
            return None
        figure = self.pose_figure()
        if not figure:
            return None
        return {"id": "pose", "heading": "The best design on the target", "label": "pose",
                "blocks": [figure]}

    def pose_figure(self):
        design = self.shown[0]
        path = str(design.get("path") or "")
        if not path or not os.path.isfile(path):
            return None
        # A compressed design cannot be drawn: the viewer is handed a URL and reads what comes
        # back as text, with nothing in front of it to decompress a Zstandard stream.
        # RFD4-Proteina writes plain CIF unless a run asks for compression, so this is the rare
        # case rather than the usual one, and a missing figure with no explanation is worse than
        # none at all.
        if path.lower().endswith((".zst", ".gz")):
            return None
        binder = (design.get("binder_chains") or ["A"])
        target = (design.get("target_chains") or ["B"])
        marked = self.renumbered()
        sticks = {}
        for name, mapped in marked.items():
            got = re.match(r"([A-Za-z]*)(\d+)", str(mapped))
            if got:
                sticks.setdefault(got.group(1) or target[0], []).append(int(got.group(2)))
        layers = [{"sel": {}, "style": {"cartoon": {"color": "#b9c3cf"}}},
                  {"sel": {"chain": binder[0]},
                   "style": {"cartoon": {"color": "#e8955a"}}}]
        for chain, numbers in sorted(sticks.items()):
            layers.append({"sel": {"chain": chain, "resi": sorted(numbers)}, "add": True,
                           "style": {"stick": {"radius": 0.18,
                                               "colorscheme": "yellowCarbon"}}})
        layers.append({"sel": {"elem": "H"}, "style": {}})
        labels = [{"sel": {"chain": re.match(r"([A-Za-z]*)", str(v)).group(1) or target[0],
                           "resi": int(re.search(r"(\d+)", str(v)).group(1)), "atom": "CA"},
                   "text": str(k)}
                  for k, v in sorted(marked.items()) if re.search(r"\d", str(v))]
        structure = {"url": "/api/download?path=%s&file=%s"
                            % (_q(self.run), _q(os.path.relpath(path, self.run))),
                     "format": "cif" if path.lower().endswith(".cif") else "pdb",
                     "side_on": [list(target), list(binder)],
                     "layers": layers, "labels": labels, "zoom": 1.05}
        return {"kind": "figure", "id": "pose", "structure": structure,
                "caption": self.pose_caption(design, marked)}

    def pose_caption(self, design, marked):
        residues = self.residues_of(design)
        binder = ",".join(design.get("binder_chains") or [])
        bits = []
        if marked:
            bits.append("The %s-residue design <b>%s</b> (chain %s) sits on %s's %s, which the "
                        "output renumbers %s"
                        % (residues, rk.e(design.get("name")), rk.e(binder),
                           rk.e(self.target_name()),
                           rk.and_list([rk.e(k) for k in sorted(marked, key=self._by_number)]),
                           rk.and_list([rk.e(marked[k]) for k in sorted(marked, key=self._by_number)])))
            bits.append("the hotspots are drawn as sticks and labeled with the target's own "
                        "numbering, and the mapping between the two is the run's own")
        else:
            hot = self.hotspots()
            bits.append("The %s-residue design <b>%s</b> (chain %s) as the tool wrote it, on the "
                        "target" % (residues, rk.e(design.get("name")), rk.e(binder)))
            bits.append("the %s named as hotspots (%s) could not be marked: nothing in this run "
                        "records the target's own numbering against the output's, so the "
                        "positions cannot be matched"
                        % (rk.count(len(hot), "residue"),
                           rk.and_list([rk.e(h) for h in hot])) if hot else
                        "no hotspot was given, so none is marked")
        bits.append("no contact count is shown because neither tool writes one: nothing in a run "
                    "folder says whether a design actually touches the residues it was aimed at")
        return rk.sentences(bits)

    # -------------------------------------------------------------- 6. what was asked

    def asked(self):
        rows = self.asked_rows()
        if not rows:
            return None
        blocks = [{"kind": "table", "id": "asked",
                   "columns": [{"key": "field", "label": "Field"},
                               {"key": "value", "label": "Value"}],
                   # Rows are keyed by the columns' own keys: a table of pairs is still a table,
                   # and reportkit reads a row by key rather than by position.
                   "rows": [{"field": label, "value": {"html": value}}
                            for label, value in rows],
                   "caption": self.asked_caption()}]
        if self.tool == "boltzgen":
            blocks += self.hotspot_proof()
        return {"id": "asked", "heading": "What the tool was asked for", "label": "asked",
                "blocks": blocks}

    def asked_rows(self):
        rows = [("Target file", str(self.plan.get("target") or "not recorded")),
                ("Chains kept", str(self.plan.get("chains") or "not recorded"))]
        hot = self.hotspots()
        rows.append(("Hotspots as given", rk.and_list(hot) if hot else "none given"))
        lo, hi = rk.whole(self.plan.get("binder_min")), rk.whole(self.plan.get("binder_max"))
        if lo or hi:
            rows.append(("Binder length asked for", "%s to %s residues" % (lo, hi)))
        if self.tool == "rfdiffusion":
            resolved = self._resolved()
            if resolved.get("contig"):
                rows.append(("Contig, as resolved", str(resolved["contig"])))
            lengths = [self.residues_of(d) for d in self.designs]
            lengths = [str(x) for x in lengths if x]
            if lengths:
                rows.append(("Length sampled per design", rk.and_list(sorted(set(lengths)))))
            if resolved.get("timesteps"):
                rows.append(("Diffusion timesteps, as run", str(resolved["timesteps"])))
        if self.tool == "rfd3":
            best = (self.shown or [{}])[0].get("name")
            spec = (self.metrics.get(best) or {}).get("specification") or {}
            extra = spec.get("extra") or {}
            given = rk.read_json(self._beside("rfd3_input.json"), {}) or {}
            design = given.get("design") or {}
            if spec.get("contig") or design.get("contig"):
                rows.append(("Contig asked", str(spec.get("contig") or design.get("contig"))))
            if extra.get("sampled_contig"):
                rows.append(("Contig sampled", str(extra["sampled_contig"])))
            asked_hot = spec.get("select_hotspots") or design.get("select_hotspots") or {}
            if asked_hot:
                rows.append(("Hotspots, as sent", ", ".join(
                    "%s (%s)" % (k, v) for k, v in sorted(asked_hot.items()))))
            for label, key in (("Origin strategy", "infer_ori_strategy"),
                               ("Anti-loop conditioning", "is_non_loopy")):
                got = spec.get(key, design.get(key))
                if got is not None:
                    rows.append((label, str(got)))
            for label, key in (("Timesteps", "num_timesteps"), ("Step scale", "step_scale"),
                               ("Classifier-free guidance", "cfg")):
                if self.plan.get(key) is not None:
                    rows.append((label, str(self.plan[key])))
            for label, key in (("Tokens in", "num_tokens_in"), ("Atoms", "num_atoms"),
                               ("Chains in", "num_chains")):
                if extra.get(key) is not None:
                    rows.append((label, str(extra[key])))
        if self.tool == "boltzgen":
            for label, key in (("Protocol", "protocol"), ("Designs asked for", "designs"),
                               ("Folding step", "fold")):
                if self.plan.get(key) is not None:
                    rows.append((label, str(self.plan[key])))
            if self.csv_name:
                rows.append(("Metrics table read", self.csv_name))
        if self.tool == "rfd4":
            rows += self._rfd4_rows()
        return [(label, rk.e(value)) for label, value in rows]

    def _rfd4_rows(self):
        """The contig and the conditions, as the spec the run was driven by states them.

        Read from the spec rather than rebuilt from the plan: the spec is what the tool was given,
        and a row worked out a second way here could differ from it without anybody noticing. The
        helper writes a JSON copy beside the YAML for exactly this reason.
        """
        if not self.opt.get("conditions"):
            return []
        spec = rk.read_json(self._beside("rfd4_spec.json"), {}) or {}
        design = (spec.get("binder") or next(iter(spec.values()), {})) if spec else {}
        if not isinstance(design, dict):
            return []
        rows = []
        if design.get("contig"):
            rows.append(("Contig asked", str(design["contig"])))
        #: What each condition does, in the words the documentation uses for it, because the name
        #: alone says nothing to a reader who has not read that documentation.
        means = {"C_CRDGRP": "target backbone held rigid as one group, side chains free",
                 "C_SEQ": "the target's own sequence revealed",
                 "C_HOT": "the residues the binder must reach"}
        for name, entries in sorted((design.get("conditions") or {}).items()):
            picks = [str(pick.get("select") or "") for value in (entries or {}).values()
                     for pick in (value or []) if isinstance(pick, dict)]
            rows.append(("%s (%s)" % (name, means.get(name, "condition")),
                         "; ".join(p for p in picks if p) or "set"))
        for label, key in (("Designs asked for", "designs"), ("Sampling steps", "nsteps"),
                           ("Recycling", "recycling"), ("Designs per batch", "batch_size")):
            if self.plan.get(key) not in (None, ""):
                rows.append((label, str(self.plan[key])))
        # What the tool resolved, where it differs from or adds to what it was asked. The seed is
        # the one that matters: it is drawn by the tool when none was given.
        done = self.record.get("resolved") or {}
        if done.get("seed") not in (None, ""):
            rows.append(("Seed, as run", "%s%s" % (done["seed"],
                                                   " (drawn by the model)"
                                                   if self.plan.get("seed") in (None, "")
                                                   else "")))
        if done.get("nsteps") not in (None, "") and str(done["nsteps"]) != str(
                self.plan.get("nsteps") or ""):
            rows.append(("Sampling steps, as run", str(done["nsteps"])))
        if done.get("checkpoint"):
            rows.append(("Model loaded from", str(done["checkpoint"])))
        return rows

    def _resolved(self):
        """What RFdiffusion actually ran with, as the helper now records it.

        The trap this closes: plan.json says ``"diffuser_T": 0``, which the helper reads as "do
        not pass the flag", so the real number is RFdiffusion's own default and a methods sentence
        written from plan.json alone would print "0 timesteps". The resolved values live in the
        .trb config, which this page cannot read, so the helper copies them into designs.json.
        """
        got = self.record.get("resolved") or {}
        if got:
            return got
        for design in self.designs:
            block = design.get("confidence") or {}
            if block.get("contig") or block.get("timesteps"):
                return {"contig": block.get("contig"), "timesteps": block.get("timesteps"),
                        "checkpoint": block.get("checkpoint"), "card": block.get("card")}
        return {}

    def asked_caption(self):
        hot = self.hotspots()
        lo, hi = rk.whole(self.plan.get("binder_min")), rk.whole(self.plan.get("binder_max"))
        lengths = sorted({self.residues_of(d) for d in self.designs
                          if self.residues_of(d)})
        bits = []
        if lo and hi:
            bits.append("The run asked for a binder of %s to %s residues against %s"
                        % (lo, hi, rk.e(self.target_words())))
            if lengths:
                bits[-1] += (" and sampled %s"
                             % (("%s residues for every design" % lengths[0]) if len(lengths) == 1
                                else "%s residues" % rk.and_list([str(x) for x in lengths])))
        elif lengths:
            bits.append("The designs came out at %s residues against %s"
                        % (rk.and_list([str(x) for x in lengths]), rk.e(self.target_words())))
        else:
            bits.append("Nothing in this run records the binder length that was asked for")
        if hot:
            bits.append("the %s named as hotspots were %s"
                        % (rk.count(len(hot), "residue"), rk.and_list([rk.e(h) for h in hot])))
        resolved = self._resolved()
        if self.tool == "rfdiffusion" and resolved.get("timesteps") and \
                rk.whole(self.plan.get("diffuser_T")) == 0:
            bits.append("the %s timesteps are RFdiffusion's own default: <code>plan.json</code> "
                        "records 0, which the helper reads as 'do not pass the flag'"
                        % resolved["timesteps"])
        return rk.sentences(bits)

    def hotspot_proof(self):
        """BoltzGen converts the hotspots to positions in the chain and proves it before the run;
        the proof is worth naming rather than quoting."""
        log = runsmod.log_path(self.run)
        lines = [l for l in rk.tail(log, 400).splitlines() if "hotspot" in l.lower()] if log \
            else []
        if not lines:
            return []
        proof = self._beside("checked")
        return [{"kind": "list", "items": [rk.e(l.strip()) for l in lines[:6]]
                 + ([rk.e("The conversion was proved by writing the specification out as a "
                          "structure and reading the marked residues back: %s"
                          % os.path.relpath(proof, self.run))]
                    if os.path.isdir(proof) else [])}]

    # -------------------------------------------------------------- the methods

    def methods(self, version):
        """The methods, written from what this run did, one sentence per source.

        A step that did not run gets no sentence and no citation: the sentences come off one by
        one as the run has less to say, so a part run gives a short methods section rather than a
        wrong one.
        """
        refs, order = self._references(), []

        def cite(*keys):
            numbers = []
            for key in keys:
                if key not in refs:
                    continue
                if key not in order:
                    order.append(key)
                numbers.append(order.index(key) + 1)
            return " [%s]" % ", ".join(str(n) for n in sorted(set(numbers))) if numbers else ""

        paragraphs = (self._rfdiffusion_methods(version, cite) if self.tool == "rfdiffusion"
                      else self._rfd3_methods(version, cite) if self.tool == "rfd3"
                      else self._rfd4_methods(version, cite) if self.tool == "rfd4"
                      else self._boltzgen_methods(version, cite))
        references = [{"n": n, "text": refs[key]} for n, key in enumerate(order, 1)]
        html = "".join("<p>%s</p>" % p for p in paragraphs if p and p.strip())
        return html, references

    def _references(self):
        common = {
            "mpnn": "Dauparas, J. et al. Robust deep learning based protein sequence design "
                    "using ProteinMPNN. Science 378, 49-56 (2022). doi:10.1126/science.add2187",
            "hprc": "Texas A&M High Performance Research Computing, which provided the "
                    "computing resources this work used.",
            "3dmol": "Rego, N. and Koes, D. 3Dmol.js: molecular visualization with WebGL. "
                     "Bioinformatics 31, 1322-1324 (2015). "
                     "doi:10.1093/bioinformatics/btu829",
        }
        if self.tool == "rfdiffusion":
            common.update({
                "rfd": "Watson, J. L. et al. De novo design of protein structure and function "
                       "with RFdiffusion. Nature 620, 1089-1100 (2023). "
                       "doi:10.1038/s41586-023-06415-8",
                            })
        elif self.tool == "rfd4":
            common.update({
                "rfd4": "Corley, N., Didi, K., Kazman, M. W., Brent, R. I. and others. "
                        "Biomolecular design through latent reasoning with RFD4-Proteina. "
                        "Preprint (2026). "
                        "https://rfd4-proteina.org/assets/paper/RFD4-Proteina.pdf",
                "atomworks": "Corley, N. et al. Accelerating biomolecular modeling with "
                             "AtomWorks and RF3. bioRxiv (2025). (It reads the target, converts "
                             "it to the annotated CIF the model consumes and completes its "
                             "missing atoms.)",
                "image": "The run was made from the lab's own container image of "
                         "RFD4-Proteina, built from the project's published Dockerfile: "
                         "ghcr.io/aadhilhaq/hope-labs-rfd4-proteina.",
            })
        elif self.tool == "rfd3":
            common.update({
                "rfd3": "Butcher, J. et al. Atomic functional site scaffolding in biopolymers "
                        "with RFdiffusion3. Science (15 October 2026). "
                        "doi:10.1126/science.aeh2982",
                "atomworks": "Corley, N. et al. Accelerating biomolecular modeling with "
                             "AtomWorks and RF3. bioRxiv (2025).",
                "psea": "Labesse, G., Colloc'h, N., Pothier, J. and Mornon, J. P-SEA: a new "
                        "efficient assignment of secondary structure from C-alpha trace of "
                        "proteins. Bioinformatics 13, 291-295 (1997). "
                        "doi:10.1093/bioinformatics/13.3.291",
            })
        else:
            common.update({
                "boltzgen": "Starck, H. et al. BoltzGen: toward universal binder design. "
                            "bioRxiv (2025). doi:10.1101/2025.11.20.689494",
                "boltz2": "Wohlwend, J., Corso, G., Passaro, S. et al. Boltz-2: towards "
                          "accurate and efficient binding affinity prediction. bioRxiv (2025). "
                          "(Not in the tool's own citation list; BoltzGen asks only for its own "
                          "reference.)",
                "biotite": "Kunzmann, P. and Hamacher, K. Biotite: a unifying open source "
                           "computational biology framework in Python. BMC Bioinformatics 19, "
                           "346 (2018). (Not in the tool's own citation list; it computes the "
                           "hydrogen bonds the plip_hbonds column reports.)",
            })
        return common

    def _rfdiffusion_methods(self, version, cite):
        resolved = self._resolved()
        chains = str(self.plan.get("chains") or "")
        hot = self.hotspots()
        target = os.path.basename(str(self.plan.get("target") or "")) or "the target"
        out = []
        out.append(
            "Binder backbones were designed with RFdiffusion %s%s against chain%s %s of %s, "
            "whose residues were kept verbatim%s."
            % (rk.e(version or "(version read from the install, not recorded in the run)"),
               cite("rfd", "readme"), "" if len(chains.split(",")) == 1 else "s",
               rk.e(chains or "A"), rk.e(target),
               "; the contig was <code>%s</code>, asking for a single diffused chain separated "
               "from the target by a chain break" % rk.e(resolved["contig"])
               if resolved.get("contig") else ""))
        if hot:
            out.append(
                "Residues %s were given as hotspots, which the complex checkpoint is trained to "
                "interpret as interface residues the binder must contact%s."
                % (rk.and_list([rk.e(h) for h in hot]), cite("rfd")))
        timesteps = resolved.get("timesteps")
        noise = self.plan.get("noise_scale")
        out.append(
            "%s were sampled%s%s%s."
            % (rk.cap(rk.count(len(self.designs), "design")),
               " over %s diffusion timesteps" % timesteps if timesteps else "",
               " from the %s checkpoint" % rk.e(resolved["checkpoint"])
               if resolved.get("checkpoint") else "",
               ", with both denoiser noise scales set to zero as RFdiffusion's own binder-design "
               "example does, which favors quality over diversity" + cite("readme")
               if rk.number(noise) == 0 else ""))
        seqs = rk.whole(self.plan.get("seqs_per_backbone"))
        model = self.mpnn_model()
        if seqs:
            out.append(
                "Each backbone's designed chain was then given %s by ProteinMPNN%s%s at sampling "
                "temperature 0.1 with seed 37 and the target chain held fixed, and the "
                "lowest-scoring sequence was kept for each."
                % (rk.plural(seqs, "sequence"), cite("mpnn"),
                   " (model %s)" % rk.e(model) if model else ""))
        times = [self.plddt_of(d).get("seconds") for d in self.designs]
        times = [rk.number(t) for t in times if rk.number(t) is not None]
        if times:
            out.append("Diffusion took %s s per design%s."
                       % (rk.and_list([rk.num(t, 1) for t in times]),
                          " on one %s" % rk.e(resolved["card"]) if resolved.get("card") else ""))
        out.append(
            "No independent refolding or relaxation was performed: the pLDDT values reported are "
            "RFdiffusion's own, from the structure module during sampling, and no AlphaFold or "
            "Rosetta step was run on the designs. RFdiffusion builds directly on the "
            "architecture and trained parameters of RoseTTAFold, whose authors its own notes ask "
            "be thanked; the notes give no reference for the acknowledgment, so none is listed "
            "here.")
        if self.opt["pose"]:
            out.append("Structures were drawn with 3Dmol.js%s. Computing resources were provided "
                       "by Texas A&amp;M High Performance Research Computing%s."
                       % (cite("3dmol"), cite("hprc")))
        else:
            out.append("Computing resources were provided by Texas A&amp;M High Performance "
                       "Research Computing%s." % cite("hprc"))
        return out

    def _rfd3_methods(self, version, cite):
        best = (self.shown or [{}])[0].get("name")
        spec = (self.metrics.get(best) or {}).get("specification") or {}
        extra = spec.get("extra") or {}
        settings = (self.metrics.get(best) or {}).get("inference_settings") or {}
        checkpoint = (self.metrics.get(best) or {}).get("ckpt_path") or ""
        hot = spec.get("select_hotspots") or {}
        chains = str(self.plan.get("chains") or "")
        target = os.path.basename(str(self.plan.get("target") or "")) or "the target"
        out = []
        out.append(
            "Binders were designed with RFdiffusion3 (rc-foundry %s)%s against chain%s %s of %s. "
            "The input specification asked for a binder separated from the target by a chain "
            "break, as the contig <code>%s</code>%s."
            % (rk.e(version or "version read from the install, not recorded in the run"),
               cite("rfd3", "atomworks"), "" if len(chains.split(",")) == 1 else "s",
               rk.e(chains or "A"), rk.e(target), rk.e(spec.get("contig") or "not recorded"),
               "; %s were sampled" % rk.plural(
                   len({self.residues_of(d) for d in self.designs if self.residues_of(d)}) and
                   max(self.residues_of(d) for d in self.designs if self.residues_of(d)) or 0,
                   "residue") if any(self.residues_of(d) for d in self.designs) else ""))
        if hot:
            out.append(
                "Residues %s were selected as hotspots, whole-residue, and the origin token was "
                "%s, which grows the binder off the face asked for.%s"
                % (rk.and_list([rk.e(k) for k in sorted(hot, key=self._by_number)]),
                   "inferred from them rather than from the target's center of mass"
                   if spec.get("infer_ori_strategy") == "hotspots" else
                   "placed by the %s strategy" % rk.e(spec.get("infer_ori_strategy")),
                   " The anti-loop conditioning flag was set." if spec.get("is_non_loopy")
                   else ""))
        out.append(
            "%s were generated in one diffusion pass%s%s%s%s."
            % (rk.cap(rk.count(len(self.designs), "design")),
               " of %s timesteps" % rk.whole(self.plan.get("num_timesteps"))
               if self.plan.get("num_timesteps") else "",
               " with a step scale of %s" % self.plan.get("step_scale")
               if self.plan.get("step_scale") is not None else "",
               " and classifier-free guidance %s"
               % ("on" if self.plan.get("cfg") else "off")
               if self.plan.get("cfg") is not None else "",
               ", from checkpoint <code>%s</code> in the model's %s inference mode"
               % (rk.e(os.path.basename(checkpoint)), rk.e(settings.get("mode") or "default"))
               if checkpoint else ""))
        seqs = rk.whole(self.plan.get("seqs_per_backbone"))
        if seqs:
            out.append(
                "Each design's designed chain was redesigned by ProteinMPNN%s - the lab's own "
                "install rather than the re-implementation shipped in the same package, whose "
                "documentation asks that the original be used for anything being compared until "
                "it has been validated against it - with %s per backbone at temperature 0.1, "
                "seed 37 and the target chain held fixed; the lowest-scoring sequence was kept "
                "and the model's own sequence retained beside it."
                % (cite("mpnn"), rk.plural(seqs, "sequence")))
        if self.opt["geometry"]:
            out.append(
                "The geometry figures quoted are the model's own, computed on the designed "
                "region for secondary structure and the radius of gyration and over the whole "
                "complex for composition; secondary structure is assigned by P-SEA from the "
                "C-alpha trace%s." % cite("psea"))
        out.append(
            "No confidence or interface score was computed: RFdiffusion3's design run reports "
            "geometry only, and the designs were not refolded. The seed is recorded as "
            "<code>null</code> in this run, so the sampling cannot be reproduced exactly from "
            "its own record."
            if (self.metrics.get(best) or {}).get("seed") in (None, "") else
            "No confidence or interface score was computed: RFdiffusion3's design run reports "
            "geometry only, and the designs were not refolded.")
        tail = "Computing resources were provided by Texas A&amp;M High Performance Research " \
               "Computing%s." % cite("hprc")
        out.append(("Structures were drawn with 3Dmol.js%s. " % cite("3dmol") if self.opt["pose"]
                    else "") + tail)
        return out

    def _rfd4_methods(self, version, cite):
        spec = rk.read_json(self._beside("rfd4_spec.json"), {}) or {}
        design = spec.get("binder") or next(iter(spec.values()), {}) if spec else {}
        design = design if isinstance(design, dict) else {}
        conditions = design.get("conditions") or {}
        chains = str(self.plan.get("chains") or "")
        hot = self.hotspots()
        target = os.path.basename(str(self.plan.get("target") or "")) or "the target"
        out = []
        out.append(
            "Binders were designed with RFD4-Proteina %s%s against chain%s %s of %s. The target "
            "was converted to an annotated mmCIF by AtomWorks%s, which is the only form the "
            "model's condition spec reads, and the design problem was stated as the contig "
            "<code>%s</code>: a variable-length binder in a chain of its own, separated from the "
            "target by a chain break."
            % (rk.e(version or "version read from the image's own name"),
               cite("rfd4", "image"), "" if len(chains.split(",")) == 1 else "s",
               rk.e(chains or "A"), rk.e(target), cite("atomworks"),
               rk.e(design.get("contig") or "not recorded")))
        told = []
        if "C_CRDGRP" in conditions:
            told.append("the target's backbone atoms were placed in a single coordinate group, "
                        "which retains their geometry relative to one another while leaving "
                        "their side chains free")
        if "C_SEQ" in conditions:
            told.append("the target's own sequence was revealed")
        if "C_HOT" in conditions and hot:
            told.append("residues %s were marked as hotspots the binder must reach, by the "
                        "numbering the input structure carries"
                        % rk.and_list([rk.e(one) for one in sorted(hot, key=self._by_number)]))
        if told:
            out.append(rk.cap("%s. Backbone-only conditioning on the target is the authors' own "
                              "recommendation for a protein-protein run, in preference to "
                              "revealing its side chains, which biases the binder toward "
                              "resembling the target." % "; ".join(told)))
        else:
            out.append(
                "No hotspots were given, so the binder was free to grow anywhere on the target.")
        out.append(
            "%s were generated%s%s%s. Scoring was not composed into the run, so no design was "
            "refolded and none carries a confidence or interface score; the sequence quoted for "
            "each is the model's own, designed with the backbone rather than by a separate "
            "sequence-design step."
            % (rk.cap(rk.count(len(self.designs), "design")),
               " in batches of %s" % rk.whole(self.plan.get("batch_size"))
               if rk.whole(self.plan.get("batch_size")) else "",
               " over %s sampling steps" % rk.whole(self.plan.get("nsteps"))
               if self.plan.get("nsteps") else "",
               " with the %s recycling schedule" % rk.e(self.plan.get("recycling"))
               if self.plan.get("recycling") else ""))
        # The seed the tool used, which is not always the seed it was asked for: given none it
        # draws one and records it in its own metrics table, so a run made without a seed can
        # still be repeated. Saying otherwise from the plan alone would be wrong.
        drawn = (self.record.get("resolved") or {}).get("seed")
        if drawn not in (None, ""):
            out.append(
                "The sampling seed was <code>%s</code>%s, so the run can be repeated exactly.%s"
                % (rk.e(drawn),
                   ", which the model drew for itself and recorded"
                   if self.plan.get("seed") in (None, "") else "",
                   " The model was loaded from <code>%s</code>."
                   % rk.e((self.record.get("resolved") or {}).get("checkpoint"))
                   if (self.record.get("resolved") or {}).get("checkpoint") else ""))
        elif self.plan.get("seed") in (None, ""):
            out.append("No seed was set and none was recorded, so the sampling cannot be "
                       "reproduced exactly from this run's own record.")
        tail = "Computing resources were provided by Texas A&amp;M High Performance Research " \
               "Computing%s." % cite("hprc")
        out.append(("Structures were drawn with 3Dmol.js%s. " % cite("3dmol") if self.opt["pose"]
                    else "") + tail)
        return out

    def _boltzgen_methods(self, version, cite):
        where, _ = self._filter_config()
        out = []
        out.append(
            "Binders were generated with BoltzGen %s%s under the %s protocol, against chain%s "
            "%s of %s."
            % (rk.e(version or "version read from the install, not recorded in the run"),
               cite("boltzgen"), rk.e(str(self.plan.get("protocol") or "default")),
               "" if len(str(self.plan.get("chains") or "").split(",")) == 1 else "s",
               rk.e(str(self.plan.get("chains") or "A")),
               rk.e(os.path.basename(str(self.plan.get("target") or "")) or "the target")))
        out.append(
            "%s were generated, then inverse folded, refolded with the target present and "
            "scored%s."
            % (rk.cap(rk.count(rk.whole(self.plan.get("designs")) or len(self.designs),
                                "design")),
               cite("boltz2")))
        if self.csv:
            out.append(
                "Designs were filtered on refolding RMSD, on refolding of the design alone, on "
                "ambiguity and cysteine content and on composition caps, then ranked on the "
                "worst of their scaled per-metric ranks and reduced to a diverse set by a "
                "lazy-greedy selection; the thresholds were read from %s."
                % ("the run's own resolved configuration" if where else
                   "the filtering step's own defaults, this run's resolved configuration not "
                   "being readable"))
        if any(_column(self._csv_for(d), "plip_hbonds_refolded", "plip_hbonds") is not None
               for d in self.shown):
            out.append(
                "Hydrogen bonds and salt bridges were counted on the refolded structures. "
                "Despite the column names, these are not PLIP's: no PLIP is installed or "
                "imported anywhere in the package. The hydrogen bonds come from biotite's "
                "geometric criterion after hydrogens are added with hydride, and a salt bridge "
                "is a charged-atom pair between 0.5 and 5.5 angstroms across the interface%s."
                % cite("biotite"))
        out.append("Computing resources were provided by Texas A&amp;M High Performance "
                   "Research Computing%s." % cite("hprc"))
        return out

    # -------------------------------------------------------------- the appendix

    def appendix(self):
        return [a for a in (self.appx_settings(), self.appx_sampling(), self.appx_mapping(),
                            self.appx_provenance(), self.appx_versions(), self.appx_job(),
                            self.appx_files()) if a]

    def appx_settings(self):
        rows = []
        resolved = self._resolved()
        for key in sorted(self.plan):
            asked = self.plan[key]
            value = rk.e(json.dumps(asked) if isinstance(asked, (dict, list)) else str(asked))
            if key == "diffuser_T" and resolved.get("timesteps") and rk.whole(asked) == 0:
                value += (" <i>(asked); %s (as run, RFdiffusion's own default)</i>"
                          % rk.e(resolved["timesteps"]))
            if key == "ckpt" and not str(asked or "") and resolved.get("checkpoint"):
                value = ("<i>(not given); %s (as run)</i>" % rk.e(resolved["checkpoint"]))
            rows.append((rk.e(key), value))
        blocks = [rk.facts(rows)]
        if self.tool == "rfd3":
            given = self._beside("rfd3_input.json")
            if os.path.isfile(given):
                blocks.append({"kind": "heading",
                               "text": "The specification the model was given"})
                blocks.append({"kind": "pre", "text": rk.tail(given, 60)})
        if self.tool == "boltzgen":
            spec = sorted(glob.glob(os.path.join(self.job, "*.yaml")))
            if spec:
                blocks.append({"kind": "heading", "text": "The specification, as written"})
                blocks.append({"kind": "pre", "text": rk.tail(spec[0], 80)})
        return {"heading": "Settings, as asked and as run", "blocks": blocks}

    def appx_sampling(self):
        if self.tool != "rfd3":
            return None
        best = (self.shown or [{}])[0].get("name")
        block = self.metrics.get(best) or {}
        if not block:
            return None
        rows = [("Checkpoint", rk.e(block.get("ckpt_path") or "not recorded")),
                ("Seed", rk.e(block.get("seed")) if block.get("seed") not in (None, "")
                 else "<i>not recorded: this run cannot be reproduced exactly from its own "
                      "record</i>")]
        for key, value in sorted((block.get("inference_settings") or {}).items()):
            rows.append((rk.e(key), rk.e(value)))
        return {"heading": "Sampling", "blocks": [rk.facts(rows)]}

    def appx_mapping(self):
        marked = self.renumbered()
        if not marked:
            return None
        return {"heading": "The target's numbering against the output's", "blocks": [
            {"kind": "table", "id": "mapping",
             "columns": [{"key": "given", "label": "As given"},
                         {"key": "output", "label": "In the output"}],
             "rows": [{"given": k, "output": marked[k]}
                      for k in sorted(marked, key=self._by_number)],
             "caption": rk.sentences([
                 "The %s named as hotspots arrive in the output as %s"
                 % (rk.count(len(marked), "residue"),
                    rk.and_list([rk.e(marked[k]) for k in sorted(marked, key=self._by_number)])),
                 "shown for the hotspots only; the run records the mapping for every kept "
                 "residue"])}]}

    def appx_provenance(self):
        items = []
        target = self.sub.get("target") or {}
        for label, key in (("Target as given", "path"), ("PDB entry", "pdb_id"),
                           ("Chains asked for", "chains"), ("Hotspots asked for", "hotspots"),
                           ("Binder asked for", "binder")):
            if target.get(key):
                items.append("%s: %s" % (label, rk.e(
                    ", ".join(str(x) for x in target[key])
                    if isinstance(target[key], list) else target[key])))
        copy = os.path.join(self.run, "inputs", "target.pdb")
        if os.path.isfile(copy):
            items.append("The copy that was actually read: <code>%s</code>"
                         % rk.e(os.path.relpath(copy, self.run)))
        if self.sub.get("user") or self.sub.get("submitted"):
            items.append("Submitted %s%s" % (rk.e(self.sub.get("submitted") or "on an "
                                                  "unrecorded date"),
                                             " by " + rk.e(self.sub["user"])
                                             if self.sub.get("user") else ""))
        if self.sub.get("dry_run"):
            items.append("Queued as a dry run: no job was submitted")
        if self.flow.get("name"):
            items.append("Queued as part of the flow %s" % rk.e(self.flow["name"]))
        if self.card.get("state"):
            items.append("The card's own record: %s%s"
                         % (rk.e(self.card["state"]),
                            ", " + rk.e(self.card["note"]) if self.card.get("note") else ""))
        if not items:
            items.append("This run folder holds no <code>submission.json</code>, so nothing "
                         "records how the target arrived or who asked for it: it was queued by "
                         "hand rather than from the page.")
        return {"heading": "Where the target came from", "blocks": [{"kind": "list",
                                                                     "items": items}]}

    def appx_versions(self):
        from .. import __version__ as labs_version
        stamp = self.record.get("versions") or {}
        items = []
        got = pagedocs.upstream_version(self.tool, self.install or "")
        if stamp.get("tool"):
            items.append("%s %s, as the run itself recorded it"
                         % (TOOL_NAME[self.tool], rk.e(stamp["tool"])))
        elif got:
            items.append("%s %s, read from the install as this report was opened rather than "
                         "from the run: a run read a year from now would be labeled with "
                         "whatever is installed then"
                         % (TOOL_NAME[self.tool], rk.e(got)))
        model = self.mpnn_model()
        if model:
            items.append("ProteinMPNN model %s" % rk.e(model))
        resolved = self._resolved()
        if resolved.get("checkpoint"):
            items.append("Checkpoint %s" % rk.e(resolved["checkpoint"]))
        items.append("HOPE Labs %s" % rk.e(stamp.get("hope_labs") or labs_version))
        for label, key in (("Install", self.tool), ("ProteinMPNN", "proteinmpnn"),
                           ("Environment", "se3nv_python")):
            if self.plan.get(key):
                items.append("%s: <code>%s</code>" % (label, rk.e(self.plan[key])))
        return {"heading": "Versions", "blocks": [{"kind": "list", "items": items}]}

    def appx_job(self):
        job = str(self.sub.get("job_id") or "")
        plan = self.sub.get("plan") or {}
        rows = []
        if job:
            rows.append(("Job id", rk.e(job)))
            got = rk.sacct(job)
            if got:
                for label, key in (("Slurm state", "State"), ("Elapsed", "Elapsed"),
                                   ("Node", "NodeList"), ("Partition", "Partition"),
                                   ("Memory asked", "ReqMem")):
                    if got.get(key):
                        rows.append((label, rk.e(got[key])))
            else:
                rows.append(("Slurm's record", "<i>Slurm no longer remembers this job: "
                             "accounting forgets after a few months, which is why the design "
                             "job should write its own finished.json</i>"))
        for label, key in (("Partition asked", "partition"), ("Cores", "cpus"),
                           ("Memory", "memory"), ("Walltime", "time"), ("GPU", "gpu")):
            if plan.get(key):
                rows.append((label, rk.e(plan[key])))
        done = rk.read_json(self._beside("done.json"), {}) or {}
        for label, key in (("Finished", "finished"), ("Status the job reported", "status"),
                           ("Seconds", "seconds")):
            if done.get(key) is not None:
                rows.append((label, rk.e(done[key])))
        blocks = [rk.facts(rows)] if rows else []
        log = runsmod.log_path(self.run)
        if log:
            blocks.append({"kind": "heading", "text": "The end of %s"
                           % os.path.relpath(log, self.run)})
            blocks.append({"kind": "pre", "text": rk.tail(log, 25)})
        if not blocks:
            return None
        return {"heading": "The job", "blocks": blocks}

    def appx_files(self):
        names = []
        for folder in (self.run, self.job):
            if not os.path.isdir(folder):
                continue
            for name in sorted(os.listdir(folder)):
                full = os.path.join(folder, name)
                if os.path.isfile(full):
                    names.append((os.path.relpath(full, self.run), os.path.getsize(full)))
        if not names:
            return None
        return {"heading": "The files of this run", "blocks": [
            {"kind": "table", "id": "files",
             "columns": [{"key": "file", "label": "File"},
                         {"key": "size", "label": "Bytes", "num": True, "digits": 0}],
             "rows": [{"file": name, "size": size} for name, size in names],
             "caption": rk.sentences([
                 "The run folder and the design job's own folder hold %s between them, the "
                 "largest %s"
                 % (rk.plural(len(names), "file"),
                    rk.e(max(names, key=lambda p: p[1])[0])),
                 "caches the job wrote for itself are not listed"])}]}


def _job_dir(run):
    """The folder holding the design job's own output, for any run this page can be shown.

    runs.design_dir() finds it by the job script the driver writes, which is right for a run
    queued from the page or from a flow. A run queued by hand - which is what the real runs on
    this cluster are - puts job.sbatch at the top of the run folder or leaves none at all, so the
    folder holding designs.json is looked for as well. The report has to open those runs: they
    are the only ones that exist.
    """
    byjob = runsmod.design_dir(run)
    if os.path.isfile(os.path.join(byjob, "designs.json")):
        return byjob
    for depth in ("designs.json", os.path.join("*", "designs.json"),
                  os.path.join("*", "*", "designs.json")):
        found = sorted(glob.glob(os.path.join(run, depth)))
        if found:
            return os.path.dirname(found[0])
    return byjob


def _q(value):
    import urllib.parse
    return urllib.parse.quote(str(value or ""), safe="")


def document(tool, rundir, raw=None, install=""):
    """The whole report of a design run, or the one thing a reader can act on if it cannot be
    written."""
    try:
        return Report(tool, rundir, raw or {}, install=install).build()
    except Exception as exc:                                        # noqa: BLE001
        return {"error": "the report could not be written: %s: %s"
                         % (type(exc).__name__, exc)}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1].strip())
        return 2
    print(json.dumps(document(argv[0], argv[1]), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())


def page_for(token, rundir, tool, autoprint=False):
    """The report page for one design run, with this tool's own favicon and store key.

    The store key is the tool's own so somebody who reads an RFdiffusion report and then a
    BoltzGen one does not find the first one's chosen sections on the second.
    """
    from .. import __version__ as labs_version
    from . import docs as pagedocs
    return rk.page(token, rundir, labs_version, store="%s.report" % tool, autoprint=autoprint,
                   favicon='<link rel="icon" type="image/svg+xml" href="%s">'
                           % pagedocs.favicon(tool))
