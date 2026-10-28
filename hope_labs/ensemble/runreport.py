"""The report of an ensemble run: what was sampled, what shapes came out, how wide the spread is,
and how it stands against a simulation.

By Aadhil Haq

What the report says is here; how it looks is reportkit's, the module the lab's tools share
(hope_labs/static/reportkit.js and reportkit.css). GET /api/report answers one JSON document, as
every other report of ours does, so the page, the bar and the print layout are the same ones a
reader has already met on the folding reports.

Every caption here states what the figure shows and what can be read off it. None of them is a
title, and none of them says "an ensemble of structures": that is the heading's job.

One thing is deliberately absent. There is no figure with the sample number along the bottom.
The samples are independent draws and their order means nothing, so a line across them would be
read as a protein moving in time, which is exactly the mistake this page exists to avoid.

    python -m hope_labs.ensemble.runreport <run folder> [opt.key=value]
"""
from __future__ import annotations

import glob
import json
import os
import sys

from . import SERVES
from . import docs as ensdocs
from . import runs as runsmod
from .. import reporting as rk

#: The line above the title. The tool samples; it does not fold and it does not simulate.
KICKER = "Sampled equilibrium ensemble"

#: The bar's choices.
OPTIONS = [
    {"key": "shapes", "label": "Shapes in the table", "type": "number", "default": 12,
     "least": 1, "most": 40,
     "help": "How many of the groups the samples fall into to list, most populated first"},
    {"key": "residues", "label": "Include the per residue figures", "type": "check",
     "default": True},
    {"key": "comparison", "label": "Include the comparison with a simulation", "type": "check",
     "default": True},
    {"key": "methods", "label": "Methods and references", "type": "check", "default": True},
    {"key": "appendix", "label": "Appendix", "type": "check", "default": True},
]

#: The colors the two series are drawn in where both appear, taken from reportkit's own palette
#: so that the report's figures and its tables agree.
SAMPLED, SIMULATED = rk.SERIES[0], rk.SERIES[1]


class Report:
    """One run, read once, written out as the document reportkit lays out."""

    def __init__(self, tool, rundir, raw, install=""):
        self.tool = tool
        self.run = os.path.abspath(rundir)
        self.install = install
        self.sub = runsmod.read_json(os.path.join(self.run, "submission.json"), {}) or {}
        self.got = runsmod.analysis(self.run)
        self.shapes = (self.got.get("states") or {}).get("states") or []
        self.comparisons = self._comparisons()
        self.limits = {"shapes": max(1, len(self.shapes))}
        self.declared = OPTIONS
        self.opt = rk.options(self.declared, raw, self.limits)

    # -------------------------------------------------------------- reading

    def _comparisons(self):
        out = []
        for path in sorted(glob.glob(os.path.join(self.run, "compare", "*", "compare.json"))):
            doc = runsmod.read_json(path, {}) or {}
            if doc:
                out.append(doc)
        return out

    def _column(self, name, which):
        """One column of a two-column data file, as a list of numbers."""
        path = os.path.join(self.run, "analysis", name)
        values = []
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("#") or not line.strip():
                        continue
                    parts = line.split()
                    if len(parts) > which:
                        values.append(float(parts[which]))
        except (OSError, ValueError):
            return []
        return values

    def _state(self):
        job = str(self.sub.get("job_id") or "")
        if not job:
            return ("DRY RUN" if self.sub.get("dry_run") else ""), ""
        return runsmod.finished_state(job)

    # -------------------------------------------------------------- the document

    def build(self):
        version = ensdocs.upstream_version(self.tool, self.install or "")
        from .. import __version__ as labs_version
        if not self.got or not self.shapes:
            return {"error": "this run has not been read yet, so there is nothing to report: %s"
                             % self._where_it_got_to()}
        doc = {
            "kicker": KICKER,
            "title": self.sub.get("name") or os.path.basename(self.run.rstrip(os.sep)),
            "subtitle": self.subtitle(version),
            "byline": rk.byline("HOPE Labs", labs_version,
                                submitted=str(self.sub.get("submitted") or ""),
                                who=str(self.sub.get("user") or "")),
            "path": self.run,
            "options": rk.echo(self.declared, self.opt, self.limits, drop=self._drop_options()),
            "notes": self.notes(),
            "sections": [s for s in (self.summary(), self.shapes_section(), self.spread(),
                                     self.residues(), self.structure(), self.comparison())
                         if s],
            "views_hint": "None yet. The structure figure above is drawn by the report itself "
                          "from the run's own file.",
            "appendix": self.appendix() if self.opt["appendix"] else [],
        }
        doc["methods"], doc["references"] = (self.methods(version) if self.opt["methods"]
                                            else ("", []))
        return doc

    def _drop_options(self):
        """An option that cannot change anything is left off the bar rather than offered."""
        out = []
        if not self.comparisons:
            out.append("comparison")
        if not self._rmsf():
            out.append("residues")
        return out

    def _where_it_got_to(self):
        state, _ = self._state()
        counted = runsmod.counts(self.run)
        if counted["samples"]:
            return ("the job %s with %s on disk and nothing read from them"
                    % ((state or "stopped").lower(), rk.count(counted["samples"], "sample")))
        return "the job %s and wrote no samples" % (state or "stopped").lower()

    def subtitle(self, version):
        asked = self.sub.get("asked") or {}
        state, _ = self._state()
        bits = ["%s residues" % (self.got.get("residues") or self.sub.get("residues") or 0),
                "%s drawn" % rk.count(self.got.get("samples") or 0, "sample"),
                "BioEmu %s, %s" % (version, asked.get("model_name") or "bioemu-v1.1")]
        if asked.get("seed"):
            bits.append("seed %s" % asked["seed"])
        if state and state.upper() != "COMPLETED":
            bits.append("the job ended %s" % state.lower())
        return "; ".join(bits)

    def notes(self):
        """What a reader has to be told before they read anything else."""
        out = [{"kind": "p", "tone": "caveat", "html": rk.e(
            "An ensemble is not a trajectory. These structures are independent draws from the "
            "distribution the model learned, so their order carries nothing: no rate, no "
            "pathway and no time axis can be read off them. What they do carry is how often a "
            "shape occurs.")}]
        state, _ = self._state()
        if state and state.upper() not in ("COMPLETED", "DRY RUN"):
            out.append({"kind": "p", "tone": "warn", "html": rk.e(
                "The job ended %s, so these numbers are of the samples that were written before "
                "it did." % state.lower())})
        residues = self.got.get("residues") or 0
        if residues > 500:
            out.append({"kind": "p", "tone": "warn", "html": rk.e(
                "At %d residues this chain is beyond what BioEmu was trained within, about 500, "
                "so these samples are an extrapolation rather than a prediction." % residues)})
        asked = self.sub.get("asked") or {}
        if asked.get("filter_samples") in (False, "false", "no", 0, "0"):
            out.append({"kind": "p", "tone": "warn", "html": rk.e(
                "The tool's own filter for unphysical samples was turned off for this run, so "
                "the spread includes draws it would otherwise have discarded.")})
        if str(self.sub.get("alignments") or "").startswith("named"):
            out.append({"kind": "p", "tone": "caveat", "html": rk.e(
                "The alignment was given rather than searched for: %s."
                % self.sub["alignments"][len("named: "):])})
        return out

    # -------------------------------------------------------------- 1. summary

    def summary(self):
        return {"id": "summary", "heading": "Summary", "label": "summary",
                "blocks": [{"kind": "p", "html": rk.e(runsmod.headline(self.run, self.got))},
                           {"kind": "p", "html": self.summary_words()},
                           rk.facts(self.summary_facts())]}

    def summary_words(self):
        spread = self.got.get("spread") or {}
        biggest = self.shapes[0] if self.shapes else {}
        bits = ["%s of %s fell into %s at a cut of %s nm"
                % (rk.count(self.got.get("samples") or 0, "sample"),
                   "%d residues" % (self.got.get("residues") or 0),
                   rk.count(len(self.shapes), "shape"),
                   rk.num((self.got.get("states") or {}).get("cut_nm"), 2))]
        bits.append("the most populated holds %s of them"
                    % rk.pc(biggest.get("share") or 0))
        bits.append("the samples lie %s from the mean shape and their radius of gyration is %s nm"
                    % (rk.pm(spread.get("from_mean_mean"), spread.get("from_mean_sd"), 2),
                       rk.pm(spread.get("rg_mean"), spread.get("rg_sd"), 2)))
        widest = (self.got.get("states") or {}).get("widest_nm")
        if widest:
            bits.append("the two furthest apart differ by %s nm" % rk.num(widest, 2))
        return rk.e(rk.cap(rk.sentences(bits)))

    def summary_facts(self):
        asked = self.sub.get("asked") or {}
        state, elapsed = self._state()
        rows = [
            ("Sequence", "%d residues" % (self.got.get("residues") or 0)),
            ("Samples asked for", str(asked.get("num_samples") or "")),
            ("Samples drawn", str(self.got.get("samples") or 0)),
            ("Model", str(asked.get("model_name") or "bioemu-v1.1")),
            ("Alignment", str(self.sub.get("alignments") or "")),
            ("Unphysical samples dropped",
             "yes" if asked.get("filter_samples") not in (False, "false", "no", 0, "0") else "no"),
            ("Seed", str(asked.get("seed") or "a new one each run")),
            ("Job", "%s%s" % (self.sub.get("job_id") or "not queued",
                              " (%s%s)" % (state, ", %s" % elapsed if elapsed else "")
                              if state else "")),
        ]
        return [(label, value) for label, value in rows if str(value).strip()]

    # -------------------------------------------------------------- 2. the shapes

    def shapes_section(self):
        rows, columns = [], [
            {"key": "rank", "label": "Shape"},
            {"key": "share", "label": "Share of samples", "kind": "pc"},
            {"key": "samples", "label": "Samples"},
            {"key": "spread", "label": "Width (nm)", "digits": 2},
            {"key": "sample", "label": "Representative sample"},
            {"key": "shown", "label": "In the figure"},
        ]
        shown = set(self.got.get("shown") or [])
        for one in self.shapes[:self.opt["shapes"]]:
            rows.append({"rank": one.get("rank"), "share": one.get("share"),
                         "samples": one.get("samples"), "spread": one.get("spread_nm"),
                         "sample": one.get("frame"),
                         "shown": "yes" if one.get("rank") in shown else "no"})
        blocks = [{"kind": "table", "id": "shapes", "columns": columns, "rows": rows,
                   "caption": self.shapes_caption()}]
        if len(self.shapes) > self.opt["shapes"]:
            blocks.append({"kind": "p", "tone": "caveat", "html": rk.e(
                "%d further shapes hold the rest of the samples; raise Shapes in the table to "
                "list them." % (len(self.shapes) - self.opt["shapes"]))})
        return {"id": "shapes", "heading": "The shapes the samples fall into", "label": "shapes",
                "blocks": blocks}

    def shapes_caption(self):
        cut = (self.got.get("states") or {}).get("cut_nm")
        stride = (self.got.get("states") or {}).get("stride") or 1
        bits = ["Samples grouped by alpha carbon deviation after alignment, by complete linkage "
                "cut at %s nm, each group's share of the samples being an estimate of how much "
                "of its time the chain spends in that arrangement" % rk.num(cut, 2),
                "the representative is the sample closest to all the others in its own group, "
                "which is a structure the model drew rather than an average of several"]
        if stride > 1:
            bits.append("every %s sample was used to build the distances, the matrix being "
                        "square in the number of samples" % rk.words(stride))
        return rk.cap(rk.sentences(bits))

    # -------------------------------------------------------------- 3. the spread

    def spread(self):
        rg = self._column("rg.dat", 1)
        far = self._column("rg.dat", 2)
        blocks = []
        if len(rg) >= 3:
            blocks.append({"kind": "figure", "id": "rg",
                           "svg": rk.histogram(rg, axis="radius of gyration (nm)", bins=24,
                                               digits=2),
                           "caption": self.rg_caption(rg)})
        if len(far) >= 3:
            blocks.append({"kind": "figure", "id": "frommean",
                           "svg": rk.histogram(far, axis="distance from the mean shape (nm)",
                                               bins=24, digits=2),
                           "caption": self.far_caption(far)})
        if not blocks:
            return None
        return {"id": "spread", "heading": "How wide the spread is", "label": "spread",
                "blocks": blocks}

    def rg_caption(self, rg):
        spread = self.got.get("spread") or {}
        return rk.cap(rk.sentences([
            "How compact each sample is, over every sample drawn",
            "a single narrow peak is one arrangement held throughout; two peaks are a chain that "
            "opens and closes, and the shapes table says what share sits in each",
            "the mean is %s nm" % rk.pm(spread.get("rg_mean"), spread.get("rg_sd"), 2)]))

    def far_caption(self, far):
        spread = self.got.get("spread") or {}
        return rk.cap(rk.sentences([
            "How far each sample sits from the average of all of them, which is the plainest "
            "measure of how much this chain moves",
            "the mean is %s nm" % rk.pm(spread.get("from_mean_mean"),
                                        spread.get("from_mean_sd"), 2),
            "the average shape itself is not shown anywhere: averaging a spread of structures "
            "gives something flatter than any of them"]))

    # -------------------------------------------------------------- 4. the residues

    def _rmsf(self):
        return self._column("rmsf.dat", 1)

    def residues(self):
        if not self.opt["residues"]:
            return None
        rmsf = self._rmsf()
        if len(rmsf) < 2:
            return None
        mean = sum(rmsf) / len(rmsf)
        blocks = [{"kind": "figure", "id": "rmsf",
                   "svg": rk.value_chart(rmsf, x_label="residue", y_label="fluctuation (nm)",
                                         mean_at=mean, digits=2),
                   "caption": rk.cap(rk.sentences([
                       "How far each residue sits from where it sits in the mean shape, across "
                       "every sample, with the chain's own mean of %s nm dashed" % rk.num(mean, 2),
                       "a loop or a terminus shows as a few residues far above that line while "
                       "the rest hold still, and a chain that is loose everywhere has no such "
                       "peak at all"]))}]
        parts = (self.got.get("secondary") or {}).get("per_residue") or []
        if parts:
            # Two lines along the chain rather than a bar per residue: a stacked bar is read down
            # the page, and three hundred of them is a figure nobody can see at once.
            series = [{"name": "helix",
                       "points": [(one["residue"], one["helix"]) for one in parts]},
                      {"name": "sheet",
                       "points": [(one["residue"], one["sheet"]) for one in parts]}]
            blocks.append({"kind": "figure", "id": "secondary",
                           "svg": rk.line_chart(series, x_label="residue",
                                                y_label="share of the samples", digits=2,
                                                y_range=(0, 1)),
                           "caption": rk.cap(rk.sentences([
                               "What share of the samples holds each residue as helix or as "
                               "sheet, read with DSSP from the backbone the model writes, the "
                               "rest being neither",
                               "a residue held as helix in nine samples out of ten is reliably "
                               "structured; one split evenly between helix and neither is the "
                               "interesting kind of residue"]))})
        return {"id": "residues", "heading": "Which residues move", "label": "residues",
                "blocks": blocks}

    # -------------------------------------------------------------- 5. the structure

    def structure(self):
        where = runsmod.ensemble_file(self.run)
        if not where:
            return None
        shown = [one for one in self.shapes if one.get("rank") in set(self.got.get("shown") or [])]
        return {"id": "structure", "heading": "The shapes themselves", "label": "structure",
                "blocks": [{"kind": "figure", "id": "ensemble",
                            "structure": self._url(where),
                            "caption": rk.cap(rk.sentences([
                                "The representative of each shape, aligned onto one another, %s "
                                "in all" % rk.count(len(shown) or 1, "structure"),
                                "they are drawn overlaid rather than one after another on "
                                "purpose: stepped through, a spread of structures reads as a film "
                                "of a protein moving, and it is nothing of the kind"]))}]}

    def _url(self, path):
        return "/api/download?path=%s&file=%s" % (self.run, os.path.relpath(path, self.run))

    # -------------------------------------------------------------- 6. the comparison

    def comparison(self):
        if not self.opt.get("comparison") or not self.comparisons:
            return None
        blocks = []
        for doc in self.comparisons:
            sim = doc.get("simulation") or {}
            blocks.append({"kind": "heading",
                           "text": "Against %s" % (sim.get("name") or "a simulation")})
            blocks.append({"kind": "p", "html": rk.e(doc.get("headline") or "")})
            columns = [{"key": "rank", "label": "Shape"},
                       {"key": "share", "label": "Share of samples", "kind": "pc"},
                       {"key": "closest", "label": "Closest frame (nm)", "digits": 2},
                       {"key": "visited", "label": "Visited"}]
            rows = [{"rank": one.get("rank"), "share": one.get("share"),
                     "closest": one.get("closest_nm"),
                     "visited": "yes" if one.get("visited") else "no"}
                    for one in doc.get("shapes") or []]
            blocks.append({"kind": "table", "id": "visited_%s" % (sim.get("name") or "sim"),
                           "columns": columns, "rows": rows,
                           "caption": rk.cap(rk.sentences([
                               "For each sampled shape, the closest the simulation came to it, "
                               "and whether that is within %s nm" % rk.num(doc.get("cut_nm"), 2),
                               "a shape the simulation missed was either not visited in the %s "
                               "read or is not a shape at all, and a few hundred nanoseconds has "
                               "not visited everything"
                               % rk.count(sim.get("frames_read") or 0, "frame")]))})
            pairs = doc.get("residues") or {}
            if (pairs.get("ensemble_rmsf") or []) and (pairs.get("simulation_rmsf") or []):
                series = [{"name": "sampled",
                           "points": list(enumerate(pairs["ensemble_rmsf"], 1))},
                          {"name": "simulated",
                           "points": list(enumerate(pairs["simulation_rmsf"], 1))}]
                blocks.append({"kind": "figure", "id": "rmsf_%s" % (sim.get("name") or "sim"),
                               "svg": rk.line_chart(series, x_label="residue",
                                                    y_label="fluctuation (nm)", digits=2),
                               "caption": rk.cap(rk.sentences([
                                   "The fluctuation of each residue in each method, over the %d "
                                   "residues they share" % (pairs.get("width") or 0),
                                   "the two curves rising and falling together is the agreement "
                                   "worth having, and their correlation is %s"
                                   % rk.num(pairs.get("correlation"), 2)]))})
            spread = doc.get("spread") or {}
            blocks.append(rk.facts([
                ("Radius of gyration, sampled",
                 "%s nm" % rk.pm(spread.get("ensemble_rg_mean"), spread.get("ensemble_rg_sd"), 2)),
                ("Radius of gyration, simulated",
                 "%s nm" % rk.pm(spread.get("simulation_rg_mean"),
                                 spread.get("simulation_rg_sd"), 2)),
                ("Radius of gyration, overlap", rk.pc(spread.get("rg_overlap") or 0)),
                ("Frames read", "%s of %s in the run"
                 % (sim.get("frames_read"), sim.get("frames_in_run"))),
                ("Read from", str(sim.get("read") or "")),
                ("Simulation", str(sim.get("run") or "")),
            ]))
            if sim.get("partial_match"):
                blocks.append({"kind": "p", "tone": "warn", "html": rk.e(
                    "The chain matched in part rather than exactly: one sequence contains the "
                    "other. The per residue numbers are over what they share.")})
        return {"id": "comparison", "heading": "Against a simulation", "label": "comparison",
                "blocks": blocks}

    # -------------------------------------------------------------- the appendix

    def appendix(self):
        out = []
        told = runsmod._told(self.run)
        if told.get("command"):
            out.append({"kind": "heading", "text": "The line that sampled"})
            out.append({"kind": "pre", "text": told["command"]})
        if told.get("what"):
            out.append({"kind": "heading", "text": "The alignment, %s" % told["what"]})
            out.append({"kind": "pre", "text": (told.get("text") or "")[:4000]})
        sequence = self.got.get("sequence") or self.sub.get("sequence") or ""
        if sequence:
            out.append({"kind": "heading", "text": "The sequence, read back out of the samples"})
            out.append({"kind": "pre", "text": sequence})
        told_slurm = rk.sacct(str(self.sub.get("job_id") or ""))
        if told_slurm:
            out.append({"kind": "heading", "text": "What Slurm remembers of the job"})
            out.append(rk.facts(sorted(told_slurm.items())))
        log = runsmod.log_path(self.run)
        if log:
            out.append({"kind": "heading", "text": "The end of the job's log"})
            out.append({"kind": "pre", "text": rk.tail(log, 60)})
        return out

    # -------------------------------------------------------------- methods

    def methods(self, version):
        asked = self.sub.get("asked") or {}
        cut = (self.got.get("states") or {}).get("cut_nm")
        text = (
            "Structures were sampled with BioEmu %s (%s) from the sequence's ColabFold alignment, "
            "%s draws in one job on one NVIDIA A100 class card, inside the tool's own container "
            "image with the model hub offline. The samples were read with MDTraj: the radius of "
            "gyration and the alpha carbon deviation from the mean of the aligned coordinates "
            "were computed for every sample, the samples were grouped by pairwise alpha carbon "
            "deviation after alignment using complete linkage cut at %s nm, each group's "
            "representative being the sample of least total distance to the others in its group, "
            "and secondary structure was assigned with DSSP. Reported shares are the fraction of "
            "samples in each group and are estimates of equilibrium populations, not of rates."
            % (rk.e(version), rk.e(asked.get("model_name") or "bioemu-v1.1"),
               rk.e(str(self.got.get("samples") or 0)), rk.num(cut, 2)))
        references = [
            "Lewis, S. et al. Scalable emulation of protein equilibrium ensembles with "
            "generative deep learning. Science 389, eadv9817 (2025). doi:10.1126/science.adv9817",
            "Mirdita, M. et al. ColabFold: making protein folding accessible to all. Nature "
            "Methods 19, 679 to 682 (2022). doi:10.1038/s41592-022-01488-1",
            "Jumper, J. et al. Highly accurate protein structure prediction with AlphaFold. "
            "Nature 596, 583 to 589 (2021). doi:10.1038/s41586-021-03819-2",
            "McGibbon, R. T. et al. MDTraj: A Modern Open Library for the Analysis of Molecular "
            "Dynamics Trajectories. Biophysical Journal 109, 1528 to 1532 (2015). "
            "doi:10.1016/j.bpj.2015.08.015",
        ]
        if self.comparisons:
            text += (" Each sampled shape was compared with the simulation named above by the "
                     "least alpha carbon deviation to any frame read, and the per residue "
                     "fluctuations of the two were correlated over the residues they share.")
        return text, references


def document(tool, rundir, raw=None, install=""):
    """The whole report of an ensemble run, or the one thing a reader can act on if it cannot be
    written."""
    try:
        return Report(tool, rundir, raw or {}, install=install).build()
    except Exception as exc:                                        # noqa: BLE001
        return {"error": "the report could not be written: %s: %s"
                         % (type(exc).__name__, exc)}


def page_for(token, rundir, tool, autoprint=False):
    """The report page for one run, with the tool's own mark and store key.

    3Dmol comes from the copy the folding page vendors, and only then from the pinned CDN: a
    report opened through a tunnel to a compute node has no internet, and the structure figure is
    drawn in the browser.
    """
    from .. import __version__ as labs_version
    return rk.page(token, rundir, labs_version, store="%s.report" % tool, autoprint=autoprint,
                   favicon='<link rel="icon" type="image/svg+xml" href="%s">'
                           % ensdocs.favicon(tool),
                   three_dmol="/static/vendor/3Dmol-min.js")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: python -m hope_labs.ensemble.runreport <run folder> [opt.key=value]",
              file=sys.stderr)
        return 2
    raw = {}
    for one in argv[1:]:
        key, _, value = one.partition("=")
        raw[key[4:] if key.startswith("opt.") else key] = value
    print(json.dumps(document(SERVES[0], argv[0], raw), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
