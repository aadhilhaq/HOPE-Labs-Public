"""What a run's report says: the document the lab's report module lays out.

The report page (GET /report) is laid out by reportkit, the module the lab's tools share
(static/reportkit.js and reportkit.css); this file decides what one simulation's report says and
sends it as the document reportkit reads (GET /api/report). In order:

  Summary        what was simulated, and a verdict per replicate: did the pose hold, and is the
                 energy worth carrying forward
  The system     where the structure came from, what Prepare did to it, what was built
  The protocol   engine, force fields, box, the relaxation ladder, production, compute
  Did it hold    RMSD, RMSF, radius of gyration, buried surface, area per lipid, per replicate
  The interface  the contacts lost, the hydrogen bonds that held, the residue pairs that carry it
  MM-GBSA        per replicate, with the window, the stride and the spread
  The structure  the rendered snapshots, and the last frame in the viewer
  What it cost   the jobs, their wall time and ns/day against the estimate made at submit
  Methods,       the methods paragraph in journal form with its numbered references, then every
  Appendix       setting, the preparation log, the built system, provenance, the caveats

Every caption is written here from the run's own numbers; none is fixed text, and one that has
nothing to say says what is missing instead. Every number shown is one the run wrote: the series
and the means come from hopemd.analysis.summarise, read with its writing suppressed, so a report
only reads a run folder.

A simulation's report has to keep an observation and a statistic apart. One replicate is an
observation: the captions say so, and say that the ± they show is over frames inside that one run
and not a spread between runs. Where several replicates ran, the captions compare them and say
plainly when they disagree by more than any one of them can account for.
"""
from __future__ import annotations

import html
import json
import os
import re
import statistics
import time
from pathlib import Path

from .. import __version__, COPYRIGHT, VIEWER_REFERENCES
from ..brand import NAME
from ..spec import (CUTOFF_A, ENGINE_LABELS, Spec, mmgbsa_frames, mmgbsa_refused, mmgbsa_window,
                    part_kinds, run_chains, split_links)
from ..analysis.summarise import (contact_residues, hbond_counts, hbonds, split_ranges, summarise)
from ..numbering import solute_ids
from . import report as R

#: Where the views added from the viewer are kept, inside the run. A report writes nothing else
#: in a run folder.
FOLDER = "report"
IMAGE_MAX = 12 << 20

SMALL = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]

#: Amber's names for a residue's states, read as the residue itself.
AMBER_NAMES = {"HIE": "His", "HID": "His", "HIP": "His", "HSD": "His", "HSE": "His", "HSP": "His",
               "CYX": "Cys", "CYM": "Cys", "ASH": "Asp", "GLH": "Glu", "LYN": "Lys",
               "DA5": "DA", "DA3": "DA", "DC5": "DC", "DC3": "DC", "DG5": "DG", "DG3": "DG",
               "DT5": "DT", "DT3": "DT", "A5": "A", "A3": "A", "C5": "C", "C3": "C",
               "G5": "G", "G3": "G", "U5": "U", "U3": "U"}

OPTIONS = [
    {"key": "reps", "label": "Replicates shown", "type": "select", "default": "all",
     "choices": [["all", "all"]],
     "help": "one replicate on its own, or all of them side by side"},
    {"key": "top", "label": "Table rows", "type": "number",
     "default": 12, "least": 1, "most": 50,
     "help": "how many rows the contact and hydrogen-bond tables carry"},
    {"key": "hbonds", "label": "Hydrogen bonds", "type": "check", "default": True,
     "help": "the bonds across the interface that held, and how many the solute held per frame"},
    {"key": "byres", "label": "Contacts by residue", "type": "check", "default": False,
     "help": "which residue pairs carry the interface"},
    {"key": "pictures", "label": "Snapshots", "type": "check", "default": True,
     "help": "the frames saved from the run, and the 3D view added from the viewer"},
    {"key": "timing", "label": "What it cost", "type": "check", "default": True},
]


# ---- writing numbers and words -------------------------------------------------------------
def e(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def num(v, d=1, sign=False):
    """A number as the report prints it: fixed decimals, a true minus sign, thousands grouped
    from 10,000 up, as reportkit.js does."""
    if v is None or v == "":
        return ""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return e(v)
    s = "%.*f" % (d, v)
    if abs(v) >= 10000:
        a, _, b = s.lstrip("-").partition(".")
        s = ("-" if v < 0 else "") + "{:,}".format(int(a)) + ("." + b if b else "")
    if sign and v > 0:
        s = "+" + s
    return re.sub(r"^-", "−", s)


def pm(m, sd, d=1):
    if m is None:
        return ""
    return num(m, d) + ("" if sd is None else " ± " + num(sd, d))


def count(n, word, plural=None):
    """'one replicate', '12 frames': small numbers as words, as prose has them."""
    w = SMALL[n] if 0 <= n < len(SMALL) else num(n, 0)
    return "%s %s" % (w, word if n == 1 else (plural or word + "s"))


def words(n):
    return SMALL[n] if 0 <= n < len(SMALL) else num(n, 0)


def and_list(items):
    items = [str(i) for i in items if i not in (None, "")]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def sentence(s):
    """A clause made into a sentence: the first letter up, everything else as it was. str.capitalize
    lowercases the rest, which turns Å into å and Leu164 into leu164."""
    s = str(s or "")
    if re.match(r"^r\d", s):
        return s                       # a replicate's name: r1, not R1
    return s[:1].upper() + s[1:] if s else s


def pct(f, d=0):
    return "" if f is None else num(f * 100.0, d) + " %"


def res_name(resn, n):
    """'LEU', 7 -> 'Leu7'; Amber's state names read as the residue itself."""
    r = str(resn or "?").upper()
    r = AMBER_NAMES.get(r, r)
    return "%s%s" % (r[:1].upper() + r[1:].lower() if len(r) == 3 else r, n)


def atom_words(part):
    """A hydrogen-bond partner in words: "Leu7 O", or "A:7 O" where the build kept the ids."""
    if not part:
        return ""
    name = part.get("id") or res_name(part.get("resname"), part.get("resi"))
    return "%s %s" % (name, part.get("atom"))


def hms(seconds):
    """Seconds as the timing table writes them: 22:19, or 1:02:25 past the hour."""
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return ""
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def _who(rundir):
    try:
        import pwd
        return pwd.getpwuid(os.stat(str(rundir)).st_uid).pw_name
    except (ImportError, KeyError, OSError):
        return ""


def _json_file(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return None


def options(raw, reps):
    """The options as asked for, held to what they may be. `reps` are the replicates the run
    has numbers for, so the picker offers only those."""
    out = {}
    for o in OPTIONS:
        d = o.get("default")
        v = (raw or {}).get(o["key"], d)
        if o["type"] == "number":
            try:
                v = int(float(v))
            except (TypeError, ValueError):
                v = d
            v = max(o["least"], min(o["most"], v))
        elif o["type"] == "check":
            v = str(v).lower() in ("1", "true", "yes", "on") if not isinstance(v, bool) else v
        elif o["type"] == "select":
            allowed = ["all"] + [str(k) for k in reps]
            v = str(v) if str(v) in allowed else d
        out[o["key"]] = v
    return out


class Report:
    """One run's report: the document reportkit lays out. It reads the run folder and writes
    nothing in it."""

    def __init__(self, rundir, raw_options=None):
        self.rundir = Path(rundir)
        self.spec = Spec.from_json((self.rundir / "spec.json").read_text())
        self.prep = _json_file(self.rundir / "build" / "prep_report.json") or {}
        self.system = _json_file(self.rundir / "build" / "system.json")
        self.ids_json = _json_file(self.rundir / "jobs" / "ids.json") or {}
        try:
            # read, never write: a stored summary is the analysis job's and the MM-GBSA job's to
            # rewrite, not the reader's
            self.summary = summarise(self.rundir, write=False)
        except Exception:                                      # noqa: BLE001  a bad .dat must not take the report down
            self.summary = _json_file(self.rundir / "analysis" / "summary.json") or {"replicates": {}}
        self.reps = dict(self.summary.get("replicates") or {})
        self.ran = R.ladder_as_ran(self.rundir, self.spec)
        if self.ran:
            # describe what ran, not what the spec asked for: the protocol table and the methods
            # paragraph both read these two
            self.spec.protocol.heat_ps, self.spec.protocol.equil_ps = self.ran
        self.half = R.restraints_were_half(self.rundir, self.spec)
        self.opt = options(raw_options, self.keys())
        s = self.spec.system
        self.partner = s.ligand_class in ("protein", "nucleic_acid")
        self.has_partner = s.ligand_class != "none"
        self.mem = R.membrane_of(self.spec, self.system)
        self.rec_trace = R.trace_words(part_kinds(s, "rec"))
        # what to call the other half, in the words the page uses: a peptide is a peptide, a
        # protein or a nucleic acid is the partner, a small molecule is the ligand
        self.lig_word = {"peptide": "peptide", "protein": "partner", "nucleic_acid": "partner",
                         "small_molecule": "ligand"}.get(s.ligand_class, "")
        self._spec_raw = _json_file(self.rundir / "spec.json") or {}
        self.ids = solute_ids(self.rundir, self._spec_raw)
        self.rec_range, self.lig_range = split_ranges(self.rundir, self._spec_raw)
        self.refusal = mmgbsa_refused(s) if self.has_partner else ""

    # ---- small readings --------------------------------------------------------------------
    def keys(self):
        """The replicates the run has numbers for, in order."""
        return sorted(self.reps, key=lambda k: int(k) if str(k).isdigit() else 0)

    def shown(self):
        """The replicates this report shows: all of them, or the one asked for."""
        want = self.opt["reps"]
        return [k for k in self.keys() if want == "all" or str(k) == str(want)]

    def rep(self, k):
        return self.reps.get(k) or {}

    def one(self, k, key, field="mean"):
        d = self.rep(k).get(key)
        return None if not isinstance(d, dict) else d.get(field)

    def many(self, key, field="mean"):
        """[(replicate, value)] of one measure over the replicates shown, the ones that have it."""
        return [(k, self.one(k, key, field)) for k in self.shown() if self.one(k, key, field) is not None]

    def mm(self, k):
        d = self.rep(k).get("mmgbsa")
        return d if isinstance(d, dict) else None

    def mms(self):
        return [(k, self.mm(k)) for k in self.shown() if self.mm(k)]

    def nrep(self):
        return len(self.shown())

    def one_replicate(self):
        """True when this report rests on a single replicate: there is then no spread between
        runs, and the captions say so rather than imply error bars they have not got."""
        return self.nrep() == 1

    def over_frames(self):
        """The words that follow a ± in this report: what the spread is over."""
        return ("the standard deviation over the frames scored inside that one replicate, not a "
                "spread between replicates" if self.one_replicate()
                else "the standard deviation over the frames scored in each replicate, not between them")

    def contacts_word(self):
        return "interface" if self.partner else "docking"

    def window(self):
        """(from_ns, to_ns, stride_ps, frames) of the MM-GBSA slice the spec asked for."""
        a0, a1, st = mmgbsa_window(self.spec)
        return a0, a1, st, mmgbsa_frames(self.spec)

    def media(self, rel):
        from urllib.parse import quote
        return "/api/media?name=%s&path=%s" % (quote(self.rundir.name), quote(rel))

    def snaps(self, k, mode="dry"):
        """The first and last frame of a replicate as the snapshot job rendered them, or []."""
        out = []
        for when in ("first", "last"):
            rel = "export/snap_r%s_%s_%s.png" % (k, when, mode)
            if (self.rundir / rel).is_file():
                out.append((when, rel))
        return out if len(out) == 2 else []

    def tidied(self):
        """The tidy archive of a tidied run, by name, or ""   (its small files are packed, so the
        report says so rather than report them missing)."""
        try:
            got = sorted(p.name for p in (self.rundir / "archive").glob("tidy_*.tar.gz"))
        except OSError:
            return ""
        return got[-1] if got else ""

    def software(self):
        """What each job's engine said its version was (jobs/software.txt, one tab-separated line
        per job), newest last. Empty for a run submitted before the jobs recorded it."""
        out = []
        try:
            lines = (self.rundir / "jobs" / "software.txt").read_text(errors="replace").splitlines()
        except OSError:
            return out
        for line in lines:
            parts = line.rstrip().split("\t")
            if len(parts) >= 3 and parts[2].strip():
                row = (parts[0].strip(), parts[1].strip(), parts[2].strip())
                if row not in out:
                    out.append(row)
        return out

    # ---- the document ----------------------------------------------------------------------
    def build(self):
        spec = self.spec
        who = _who(self.rundir)
        at = ""
        for entry in (spec.rendered_by or []):
            if entry.get("at"):
                at = str(entry["at"])[:16]
                break
        doc = {
            "kicker": "%s report" % NAME,
            "title": spec.name or self.rundir.name,
            "subtitle": self.subtitle(),
            "byline": "%s %s, report written %s; run set up %s%s" % (
                NAME, __version__, time.strftime("%Y-%m-%d"),
                at or "on a date it did not record", " by " + who if who else ""),
            "path": str(self.rundir),
            "options": self.option_list(),
            "notes": self.notes(),
            "sections": [s for s in (self.summary_section(), self.system_section(), self.protocol_section(),
                                     self.stability_section(), self.interface_section(), self.energy_section(),
                                     self.pictures_section(), self.timing_section()) if s],
            "views": self.views(),
            "views_hint": ("None yet. Open the run's trajectory, set the view up as you want it and press "
                           "Add to report on the viewer's bar."),
            "appendix": self.appendix(),
        }
        doc["methods"], doc["references"] = self.methods()
        return doc

    def option_list(self):
        out = []
        for o in OPTIONS:
            o = dict(o, value=self.opt[o["key"]])
            if o["key"] == "reps":
                o["choices"] = [["all", "all (%s)" % len(self.keys())]] if len(self.keys()) > 1 else [["all", "all"]]
                o["choices"] += [[str(k), "r%s" % k] for k in self.keys()]
            if o["key"] == "top":
                o["help"] = "how many rows of the contact and hydrogen-bond tables to print"
            out.append(o)
        return out

    def subtitle(self):
        spec, s = self.spec, self.spec.system
        pro = spec.protocol
        what = R.subject(spec)
        bits = "%s, %s in %s" % (e(what), e(ENGINE_LABELS.get(spec.engine, spec.engine)),
                                 count(pro.replicates, "replicate"))
        bits += " of %s ns" % num(pro.length_ns, 0)
        if self.mem:
            bits += ", in a %s bilayer" % e(self.mem["lipid"])
        return bits

    def notes(self):
        """What has to be read before the first section."""
        out = []
        if not self.reps:
            out.append({"kind": "p", "tone": "warn", "html":
                        "No analysis has run yet, so this report carries the system, the protocol and the "
                        "methods only: the results sections appear once the analysis job has written its files."})
        elif len(self.keys()) < (self.spec.protocol.replicates or 1):
            out.append({"kind": "p", "tone": "warn", "html":
                        "%s of the %s asked for %s numbers so far; the rest of the run is still to come."
                        % (count(len(self.keys()), "replicate").capitalize(),
                           count(self.spec.protocol.replicates, "replicate"),
                           "has" if len(self.keys()) == 1 else "have")})
        if self.ran:
            out.append({"kind": "p", "tone": "warn", "html":
                        "This run's inputs were rendered by an earlier version of HOPE-MD, which cut the "
                        "relaxation ladder 1000-fold on this engine: it heated for %s ps and equilibrated for "
                        "%s ps, not the %s and %s ps the spec asked for. Everything below describes what ran."
                        % (num(self.ran[0], 3), num(self.ran[1], 3),
                           num(self.spec.protocol.heat_ps, 0), num(self.spec.protocol.equil_ps, 0))})
        if self.refusal:
            out.append({"kind": "p", "tone": "warn", "html": e(self.refusal)})
        if self.one_replicate():
            out.append({"kind": "p", "tone": "warn", "html":
                        ("One replicate ran, so every number below is a single observation and not a statistic. "
                         "Nothing here has a spread between replicates: the ± shown is over the frames of "
                         "this one run, and a second replicate could land anywhere."
                         if (self.spec.protocol.replicates or 1) == 1 and len(self.keys()) <= 1 else
                         "This report shows one replicate, r%s, so every number below is a single observation and "
                         "not a statistic: the ± shown is over the frames of that one run. The run's own "
                         "result is its %s replicates together, which the bar's replicate picker shows."
                         % (e(self.shown()[0]), words(len(self.keys()))))})
        if self.spec.system.source.kind == "alphafold" and (self.spec.system.meta or {}).get("server"):
            from ..importers.complex import SERVER_NOTE
            out.append({"kind": "p", "tone": "warn", "html": e(SERVER_NOTE[0].upper() + SERVER_NOTE[1:] + ".")})
        arch = self.tidied()
        if arch:
            out.append({"kind": "p", "tone": "caveat", "html":
                        "This run has been tidied: its stage logs, intermediate restarts and build scratch are "
                        "packed into archive/%s, so anything this report says is missing may be in there. Undo "
                        "on the Runs screen unpacks it." % e(arch)})
        return out

    # ---- 1 Summary -------------------------------------------------------------------------
    def summary_section(self):
        spec, pro = self.spec, self.spec.protocol
        blocks = [{"kind": "p", "html": self.what_ran()}]
        if self.shown():
            blocks.append({"kind": "list", "items": [self.verdict(k) for k in self.shown()]})
            blocks.append(self.headline_table())
        else:
            blocks.append({"kind": "p", "tone": "warn", "html":
                           "Nothing has been measured yet. The analysis job writes analysis/r&lt;i&gt;_*.dat at "
                           "the end of the graph; until it has run there is no RMSD, no contact count and no "
                           "binding energy to report."})
        return {"id": "summary", "heading": "Summary", "label": "summary", "blocks": blocks}

    def what_ran(self):
        spec, pro, s = self.spec, self.spec.protocol, self.spec.system
        out = "%s was %s" % (R.subject(spec).capitalize(), R.source_sentence(spec))
        out += ". It ran for %s ns in %s with %s" % (
            num(pro.length_ns, 0), count(pro.replicates, "replicate"),
            e(ENGINE_LABELS.get(spec.engine, spec.engine)))
        out += " at %s K and %s bar, with coordinates saved every %s ps" % (
            num(pro.temperature_K, 0), num(pro.pressure_bar, 0), num(pro.save_ps, 0))
        out += "."
        if self.has_partner and not self.refusal and spec.analysis.get("mmgbsa", True):
            a0, a1, st, nf = self.window()
            out += (" The binding energy quoted below is MM-GBSA over %s to %s ns of %s, every %s ps."
                    % (num(a0, 0), num(a1, 0),
                       "the run" if (self.spec.protocol.replicates or 1) == 1 else "each replicate", num(st, 0)))
        return out

    def verdict(self, k):
        """One replicate in a sentence: did the receptor settle, did the partner stay, what held."""
        bits = []
        rec = self.rep(k).get("rmsd_rec") or {}
        if rec:
            bits.append("the receptor holds at %s Å mean %s RMSD (%s Å at most)"
                        % (num(rec.get("mean"), 2), e(self.rec_trace), num(rec.get("max"), 2)))
        lig = self.rep(k).get("rmsd_lig") or {}
        if lig and self.has_partner:
            m = lig.get("mean")
            moved = m is not None and m > 4.0
            bits.append("the %s %s (%s Å mean, %s Å at most)"
                        % (e(self.lig_word), "has left its docked pose" if moved else "stays where it was put",
                           num(m, 2), num(lig.get("max"), 2)))
        c = self.rep(k).get("contacts") or {}
        if c:
            bits.append("%s of %s first-frame contacts survive more than %s of the frames"
                        % (num(c.get("kept"), 0), num(c.get("total"), 0), pct(c.get("threshold"))))
        m = self.mm(k)
        if m:
            bits.append("MM-GBSA ΔG %s kcal/mol over %s frames"
                        % (pm(m.get("mean"), m.get("std")), num(m.get("frames"), 0)))
        apl = self.rep(k).get("area_per_lipid") or {}
        if apl:
            bits.append("the bilayer settles at %s Å² per lipid" % num(apl.get("mean"), 1))
        if not bits:
            return "<b>r%s</b>: nothing measured; the analysis wrote no files for this replicate." % e(k)
        return "<b>r%s</b>: %s." % (e(k), and_list(bits))

    def headline_table(self):
        cols = [{"key": "rep", "label": "Replicate"},
                {"key": "ns", "label": "Production (ns)", "num": True, "digits": 0},
                {"key": "rec", "label": "Receptor RMSD, mean (Å)", "num": True, "digits": 2,
                 "help": "%s RMSD after fitting the receptor to the first frame; lower is steadier" % self.rec_trace},
                {"key": "rec_max", "label": "max (Å)", "num": True, "digits": 2}]
        if self.has_partner:
            cols += [{"key": "lig", "label": "%s RMSD, mean (Å)" % self.lig_word.capitalize(), "num": True, "digits": 2,
                      "help": "measured in the receptor's frame, with no fit of its own"},
                     {"key": "lig_max", "label": "max (Å)", "num": True, "digits": 2}]
        if any(self.one(k, "rmsd_iface") is not None for k in self.shown()):
            cols.append({"key": "iface", "label": "Interface RMSD (Å)", "num": True, "digits": 2})
        if any(self.one(k, "bsa") is not None for k in self.shown()):
            cols.append({"key": "bsa", "label": "Buried surface (Å²)", "num": True, "digits": 0,
                         "help": "receptor + partner − complex Connolly surfaces; higher is more interface"})
        if any(self.one(k, "area_per_lipid") is not None for k in self.shown()):
            cols.append({"key": "apl", "label": "Area per lipid (Å²)", "num": True, "digits": 1})
        if any(self.rep(k).get("contacts") for k in self.shown()):
            cols.append({"key": "contacts", "label": "Contacts kept"})
        if self.mms():
            cols += [{"key": "mm", "label": "MM-GBSA ΔG (kcal/mol)", "num": True, "digits": 1,
                      "help": "lower is better; the mean over the frames scored in that replicate"},
                     {"key": "sd", "label": "SD", "num": True, "digits": 1},
                     {"key": "frames", "label": "Frames", "num": True, "digits": 0}]
        rows = []
        for k in self.shown():
            c = self.rep(k).get("contacts") or {}
            m = self.mm(k)
            rows.append({"rep": "r%s" % k, "ns": self.spec.protocol.length_ns,
                         "rec": self.one(k, "rmsd_rec"), "rec_max": self.one(k, "rmsd_rec", "max"),
                         "lig": self.one(k, "rmsd_lig"), "lig_max": self.one(k, "rmsd_lig", "max"),
                         "iface": self.one(k, "rmsd_iface"), "bsa": self.one(k, "bsa"),
                         "apl": self.one(k, "area_per_lipid"),
                         "contacts": ("%s of %s" % (num(c["kept"], 0), num(c["total"], 0))) if c else "",
                         "mm": (m or {}).get("mean"), "sd": (m or {}).get("std"),
                         "frames": (m or {}).get("frames")})
        return {"kind": "table", "id": "headline", "caption": self.headline_caption(),
                "columns": cols, "rows": rows, "note": self.headline_note()}

    def headline_caption(self):
        mms, recs = self.mms(), self.many("rmsd_rec")
        ligs = self.many("rmsd_lig")
        if not recs and not mms:
            return ("Nothing to compare: the analysis wrote no RMSD and no binding energy for %s, so this table "
                    "has only the lengths the run was asked for." % and_list("r%s" % k for k in self.shown()))
        bits = []
        if len(mms) > 1:
            spread = max(m["mean"] for _, m in mms) - min(m["mean"] for _, m in mms)
            worst = max((m.get("std") or 0.0) for _, m in mms)
            lead = "The %s replicates do not agree" if spread > 2 * worst else "The %s replicates agree"
            bits.append((lead % words(len(mms))) + ": MM-GBSA ΔG is %s kcal/mol"
                        % and_list("%s ± %s" % (num(m["mean"], 1), num(m.get("std"), 1)) for _, m in mms))
        elif mms:
            k, m = mms[0]
            bits.append("The one replicate gives an MM-GBSA ΔG of %s kcal/mol over %s frames"
                        % (pm(m["mean"], m.get("std")), num(m.get("frames"), 0)))
        cs = [(k, self.rep(k).get("contacts")) for k in self.shown() if self.rep(k).get("contacts")]
        if cs:
            bits.append("the %s contacts kept are %s"
                        % (self.contacts_word(),
                           and_list("%s of %s" % (num(c["kept"], 0), num(c["total"], 0)) for _, c in cs)))
        if recs:
            steady = all(v is not None and v < 3.5 for _, v in recs)
            bits.append("the receptor %s (%s RMSD %s Å mean)"
                        % ("stays put" if steady else "moves further than a settled fold would",
                           e(self.rec_trace), and_list(num(v, 2) for _, v in recs)))
        if ligs and self.has_partner:
            gone = [k for k, v in ligs if v is not None and v > 4.0]
            if gone and len(gone) < len(ligs):
                bits.append("the %s leaves its pose in %s and holds in %s"
                            % (e(self.lig_word), and_list("r%s" % k for k in gone),
                               and_list("r%s" % k for k, _ in ligs if k not in gone)))
            elif gone:
                bits.append("the %s leaves its pose%s (%s Å mean)"
                            % (e(self.lig_word), "" if len(ligs) == 1 else " in every replicate",
                               and_list(num(v, 2) for _, v in ligs)))
        cap = sentence("; ".join(bits)) + "."
        if mms:
            a0, a1, st, _ = self.window()
            cap += (" Lower ΔG is better; ± is %s, over the %s to %s ns of %s taken every %s ps."
                    % (self.over_frames(), num(a0, 0), num(a1, 0),
                       "the run" if self.one_replicate() else "each replicate", num(st, 0)))
        else:
            cap += " Lower RMSD is steadier."
        if len(mms) > 1:
            spread = max(m["mean"] for _, m in mms) - min(m["mean"] for _, m in mms)
            worst = max((m.get("std") or 0.0) for _, m in mms)
            if spread > 2 * worst:
                cap += (" The %s kcal/mol between the replicates is larger than any one replicate's own spread, "
                        "so the number to carry forward is the mean of the three, %s kcal/mol, not the best of "
                        "them." % (num(spread, 0), num(statistics.fmean(m["mean"] for _, m in mms), 1)))
        if self.one_replicate() and len(self.keys()) > 1:
            cap += (" Only r%s is shown, one replicate of %s: these are its own numbers and not the run's result, "
                    "which is the %s together." % (e(self.shown()[0]), words(len(self.keys())),
                                                   words(len(self.keys()))))
        elif self.one_replicate() and not mms:
            # with an energy the caption has already said what the ± is over; without one, this is
            # the only place the row's standing is stated
            cap += " One replicate ran, so this row is an observation and not a statistic."
        return cap

    def headline_note(self):
        if self.one_replicate():
            return ("One replicate: these are observations, not statistics. A single run cannot say how much of "
                    "what it shows would come back in another, which is what replicates are for.")
        return None

    # ---- 2 The system ----------------------------------------------------------------------
    def system_section(self):
        s = self.spec.system
        rows = [["Source", e(R.source_sentence(self.spec))]]
        if s.chains_found:
            rows.append(["Chains", ", ".join(
                "%s %s, %s residues%s" % (e(c["id"]), e(c["kind"]), num(c["residues"], 0),
                                          " (%s missing)" % num(c["missing"], 0) if c.get("missing") else "")
                for c in s.chains_found)])
        if s.receptor_chains or s.partner_chains:
            rows.append(["Split", "receptor %s%s" % (
                e(", ".join(s.receptor_chains or [])) or "—",
                ", partner " + e(", ".join(s.partner_chains)) if s.partner_chains else ", no partner")])
        rows.append(["Ligand class", e(s.ligand_class)])
        if s.hetero_found:
            rows.append(["Hetero groups in the file", ", ".join(
                "%s × %s (%s)" % (e(h["resname"]), num(h["count"], 0), e(h["class"])) for h in s.hetero_found)])
        if (s.tm_annotation or {}).get("summary"):
            rows.append(["Transmembrane", e(s.tm_annotation["summary"])])
        if s.confidence.get("iptm") is not None:
            rows.append(["AlphaFold confidence", "ipTM %s, pTM %s%s" % (
                num(s.confidence["iptm"], 2), num(s.confidence.get("ptm"), 2),
                ", a doubtful interface before anything ran" if s.confidence["iptm"] < 0.6 else "")])
        if s.breaks:
            capped = set(s.capped or [])
            rows.append(["Chain breaks", e(", ".join(s.breaks)) + (
                " (capped with NME and ACE, so no gap ends in charged termini)" if capped else
                " (each piece ended with TER, keeping its own termini)")])
        if split_links(s):
            rows.append(["Covalent link", e(", ".join(split_links(s)))
                         + ": a disulfide joins the receptor to the partner, so the two were simulated as one "
                           "molecule and no binding energy was taken"])
        if self.mem:
            where = ""
            if self.mem.get("orientation") == "opm":
                where = ", OPM orientation" + (", planes at ±%s Å" % num(self.mem["opm_half_thickness_A"], 1)
                                               if self.mem.get("opm_half_thickness_A") is not None else "")
            rows.append(["Bilayer", "%s, %s lipids, %s Å of water above and below, center z = %s Å%s%s" % (
                e(self.mem["lipid"]), num(self.mem["lipids"], 0), num(self.mem["padding_A"], 0),
                num(self.mem["center_z_A"], 0), ", box %s Å" % e(str(self.mem["box"])) if self.mem.get("box") else "",
                where)])
        if self.system:
            rows.append(["Built system", "%s atoms, %s waters, %s %s, box %s Å%s" % (
                num(self.system.get("atoms"), 0), num(self.system.get("waters"), 0),
                num(self.system.get("ion_pairs"), 0),
                "MgCl₂ units" if self.spec.box.salt == "MgCl2" else "%s pairs" % e(self.spec.box.salt),
                e(str(self.system.get("box", "—"))), ", hydrogen mass repartitioned" if self.system.get("hmr") else "")])
        blocks = [{"kind": "facts", "rows": rows}]
        if self.prep.get("log"):
            blocks.append({"kind": "p", "html": "What Prepare did, in its own words: " + e(R.prep_sentences(self.prep, self.spec))})
        elif not self.prep:
            blocks.append({"kind": "p", "tone": "caveat", "html":
                           "The structure went into the build as it was imported: no Prepare stage ran, so no "
                           "loops were modeled and nothing was minimized first."})
        return {"id": "system", "heading": "The system", "label": "system", "blocks": blocks}

    # ---- 3 The protocol --------------------------------------------------------------------
    def protocol_section(self):
        spec, pro, box, ff = self.spec, self.spec.protocol, self.spec.box, self.spec.forcefield
        site = spec.site
        rows = [["Engine", "%s, loaded with %s" % (e(ENGINE_LABELS.get(spec.engine, spec.engine)),
                                                   e(site.module_block(spec.engine).replace("\n", " ; ")))]]
        sw = self.software()
        if sw:
            rows.append(["Engine version, as it reported itself",
                         "; ".join("%s (%s)" % (e(v), e(stage or key)) for key, stage, v in sw)])
        rows.append(["Force field and water", "%s%s, %s%s" % (
            e(ff.protein), ", nucleic acid %s" % e(ff.nucleic) if ff.nucleic else "", e(ff.water),
            ", ligand %s" % e(ff.ligand) if ff.ligand else "")])
        rows.append(["Box and salt", ("%s bilayer, %s Å of water above and below, semi-isotropic pressure "
                                      "coupling at zero surface tension" % (e(self.mem["lipid"]), num(self.mem["padding_A"], 0))
                                      if self.mem else
                                      "%s, %s Å padding" % (e(box.shape), num(box.padding_A, 0)))
                     + ", %s mM %s" % (num(box.salt_mM, 0), e(box.salt))])
        ladder = "heat %s ps, then %s ps of NPT in four stages (%s → 1 → 0.1 → 0 kcal/mol/Å²)" % (
            num(pro.heat_ps, 0), num(pro.equil_ps, 0), num(pro.restraint_kcal, 0))
        if spec.engine == "desmond" and self.mem:
            ladder = "Schrödinger's membrane relaxation protocol for Desmond, about 0.5 ns"
        elif spec.engine == "desmond":
            ladder = "100 ps of Brownian dynamics at 10 K, then " + ladder
        else:
            ladder = "%s minimization steps, then " % num(pro.minimise_steps, 0) + ladder
        if self.half:
            ladder += " (the restraints had the form ½k·d², half as strong as the same k in Amber's k·d²)"
        rows.append(["Relaxation ladder", ladder])
        rows.append(["Production", "%s ns × %s, %s K, %s bar, %s fs time step%s, frames every %s ps%s" % (
            num(pro.length_ns, 0), num(pro.replicates, 0), num(pro.temperature_K, 0), num(pro.pressure_bar, 0),
            num(pro.timestep_fs, 0), " with hydrogen mass repartitioning" if spec.hmr else "",
            num(pro.save_ps, 0), ", seed %s" % num(pro.seed, 0) if pro.seed >= 0 else ", a new random seed each replicate")])
        rows.append(["Compute", "%s, %s, partition %s" % (
            e(site.name), "a CPU node" if spec.on_cpu else e(spec.gpu.upper() or "GPU"),
            e(spec.compute.partition or site.cpu_partition) if spec.on_cpu else e(spec.gpu_partition))])
        rows.append(["Analysis", "cpptraj%s; cut-off %s Å for the fit's neighbors" % (
            ", then MMPBSA.py for the binding energy" if self.mms() else "", num(CUTOFF_A, 0))])
        return {"id": "protocol", "heading": "The protocol", "label": "protocol",
                "blocks": [{"kind": "facts", "rows": rows}]}

    # ---- 4 Did it hold ---------------------------------------------------------------------
    def stability_section(self):
        if not self.shown():
            return None
        blocks = []
        f = self.rmsd_figure()
        if f:
            blocks.append(f)
        f = self.rmsf_figure()
        if f:
            blocks.append(f)
        f = self.bsa_figure()
        if f:
            blocks.append(f)
        f = self.apl_figure()
        if f:
            blocks.append(f)
        f = self.rg_figure()
        if f:
            blocks.append(f)
        if not blocks:
            return None
        return {"id": "stability", "heading": "Did it hold", "label": "stability", "blocks": blocks}

    def series(self, key):
        """[{x, y, col, name}] of one measure over the replicates shown."""
        out = []
        for i, k in enumerate(self.shown()):
            d = self.rep(k).get(key)
            if isinstance(d, dict) and d.get("values"):
                out.append({"x": d.get("ns") or list(range(len(d["values"]))), "y": d["values"],
                            "col": R.COLOURS[i % len(R.COLOURS)], "name": "r%s" % k})
        return out

    def rmsd_figure(self):
        rec, lig = self.series("rmsd_rec"), self.series("rmsd_lig")
        if not rec and not lig:
            return None
        # The lines are gathered first and drawn second, because how wide each panel is drawn
        # is what a chart is laid out from, and that is not known until the figure knows how
        # many columns it has.
        iface = self.series("rmsd_iface")
        drawn = [(rec, "ns", "Å", "receptor %s RMSD, fitted on the receptor" % e(self.rec_trace))] if rec else []
        if lig and self.has_partner:
            drawn.append((lig, "ns", "Å", "%s RMSD in the receptor's frame, no fit of its own" % e(self.lig_word)))
        if iface:
            drawn.append((iface, "ns", "Å", "interface RMSD after the same fit"))
        columns = min(len(drawn), 2) or 1
        wide = R.panel_width(columns)
        panels = [{"svg": R.plot(one, xl, yl, wide), "label": label} for one, xl, yl, label in drawn]
        return {"kind": "figure", "id": "rmsd", "caption": self.rmsd_caption(),
                "panels": panels, "columns": columns}

    def rmsd_caption(self):
        recs, ligs = self.many("rmsd_rec"), self.many("rmsd_lig")
        if not recs and not ligs:
            return "Nothing was plotted: no RMSD file was written for the replicates shown."
        ns = num(self.spec.protocol.length_ns, 0)
        bits = []
        if recs:
            hi = max(self.one(k, "rmsd_rec", "max") or 0 for k, _ in recs)
            bits.append("the receptor averages %s Å %s RMSD over the %s ns and never exceeds %s Å"
                        % (and_list(num(v, 1) for _, v in recs), e(self.rec_trace), ns, num(hi, 1)))
        if ligs and self.has_partner:
            gone = [(k, v) for k, v in ligs if v is not None and v > 4.0]
            held = [(k, v) for k, v in ligs if (k, v) not in gone]
            if gone and held:
                bits.append("the %s leaves its docked pose in %s (%s Å mean, %s Å at most) and holds in %s (%s Å)"
                            % (e(self.lig_word), and_list("r%s" % k for k, _ in gone),
                               and_list(num(v, 1) for _, v in gone),
                               num(max(self.one(k, "rmsd_lig", "max") or 0 for k, _ in gone), 1),
                               and_list("r%s" % k for k, _ in held), and_list(num(v, 1) for _, v in held)))
            elif gone:
                bits.append("the %s leaves its docked pose%s (%s Å mean, %s Å at most)"
                            % (e(self.lig_word), "" if len(ligs) == 1 else " in every replicate",
                               and_list(num(v, 1) for _, v in gone),
                               num(max(self.one(k, "rmsd_lig", "max") or 0 for k, _ in gone), 1)))
            elif all(v is not None and v < 3.0 for _, v in held):
                bits.append("the %s keeps its docked pose (%s Å mean)"
                            % (e(self.lig_word), and_list(num(v, 1) for _, v in held)))
            else:
                bits.append("the %s drifts from its docked pose without leaving it (%s Å mean)"
                            % (e(self.lig_word), and_list(num(v, 1) for _, v in held)))
        ifs = self.many("rmsd_iface")
        if ifs:
            bits.append("the interface itself moves by %s Å" % and_list(num(v, 1) for _, v in ifs))
        cap = sentence("; ".join(bits)) + "."
        cap += (" Fitted on the receptor; the %s is measured in that frame without a fit of its own."
                % (e(self.lig_word) or "solute") if self.has_partner else " Fitted on the first frame.")
        if self.one_replicate():
            cap += " One line, one replicate: the spread an envelope would show is not available here."
        else:
            cap += " One line per replicate, %s." % and_list("r%s" % k for k in self.shown())
        return cap

    def rmsf_figure(self):
        rows = [(k, self.rep(k).get("rmsf")) for k in self.shown() if (self.rep(k).get("rmsf") or {}).get("values")]
        if not rows:
            return None
        series = [{"x": d["residue"], "y": d["values"], "col": R.COLOURS[i % len(R.COLOURS)],
                   "name": "r%s" % k, "fill": i == 0} for i, (k, d) in enumerate(rows)]
        return {"kind": "figure", "id": "rmsf", "caption": self.rmsf_caption(rows),
                "svg": R.plot(series, "residue", "Å")}

    def rmsf_caption(self, rows):
        k, d = rows[0]
        med = d.get("median")
        peak = d.get("peak") or {}
        n = len(d.get("residue") or [])
        flat = [v for v in d["values"] if v is not None]
        above = sum(1 for v in flat if v > 2.0)
        settled = med is not None and med < 1.0 and (not n or above <= n * 0.1)
        cap = "Fluctuation is %s at %s Å median across the %s residues traced" % (
            "flat" if settled else "uneven", num(med, 2), num(n, 0))
        if peak.get("value") is not None:
            named = str(peak.get("residue") or "")
            by_topology = named.startswith(":")
            cap += ", and highest at %s, %s Å" % (e(named), num(peak["value"], 2))
            if by_topology:
                cap += (", numbered as the topology does because this run was built before the structure's own "
                        "ids were kept")
        else:
            cap += ", and the file records no peak residue, so only the median can be read from it"
        cap += ". %s per residue, over the whole %s ns%s." % (
            e(self.rec_trace), num(self.spec.protocol.length_ns, 0),
            " of r%s; the other replicates are the further lines" % e(k) if len(rows) > 1 else " of the one replicate")
        if above:
            cap += " %s %s above 2 Å." % (count(above, "residue").capitalize(), "is" if above == 1 else "are")
        return cap

    def bsa_figure(self):
        series = self.series("bsa")
        if not series:
            return None
        return {"kind": "figure", "id": "bsa", "caption": self.bsa_caption(), "svg": R.plot(series, "ns", "Å²")}

    def bsa_caption(self):
        rows = [(k, self.rep(k).get("bsa")) for k in self.shown() if self.rep(k).get("bsa")]
        if not rows:
            return "No buried surface was written for the replicates shown."
        bits = []
        for k, d in rows:
            mean, first, lo = d.get("mean"), d.get("first"), d.get("min")
            if mean is not None and mean < 200:
                bits.append("r%s buries almost nothing (%s Å² on average, from %s Å² in the first "
                            "frame): the two came apart" % (e(k), num(mean, 0), num(first, 0)))
            else:
                bits.append("r%s buries %s Å² on average, from %s Å² in the first frame, and never "
                            "falls below %s Å²" % (e(k), num(mean, 0), num(first, 0), num(lo, 0)))
        cap = sentence("; ".join(bits)) + "."
        apart = [(k, d) for k, d in rows if (d.get("min") or 0) < 0]
        if apart:
            cap += (" The surface goes below zero in %s (%s Å² at the least), which three Connolly surfaces do "
                    "when the two are no longer touching at all: that is the measure running out, not a negative "
                    "interface." % (and_list("r%s" % k for k, _ in apart),
                                    num(min(d["min"] for _, d in apart), 0)))
        grew = [d for _, d in rows if d.get("mean") is not None and d.get("first") is not None
                and d["mean"] > d["first"]]
        if len(grew) == len(rows) and rows[0][1].get("mean", 0) > 200:
            cap += " The interface neither grows nor peels away."
        cap += " Receptor plus partner minus complex Connolly surfaces; higher is more interface."
        cap += (" One replicate, so this is one observation of it."
                if self.one_replicate() else " One line per replicate.")
        return cap

    def apl_figure(self):
        series = self.series("area_per_lipid")
        if not series:
            return None
        return {"kind": "figure", "id": "apl", "caption": self.apl_caption(), "svg": R.plot(series, "ns", "Å²")}

    def apl_caption(self):
        rows = [(k, self.rep(k).get("area_per_lipid")) for k in self.shown() if self.rep(k).get("area_per_lipid")]
        if not rows:
            return "No box series was written, so the area per lipid cannot be read."
        lip = self.summary.get("lipid") or (self.mem or {}).get("lipid") or "lipid"
        n = self.summary.get("lipids") or (self.mem or {}).get("lipids")
        bits = ["r%s settles at %s Å², from %s Å² in the first frame"
                % (e(k), num(d.get("mean"), 1), num((d.get("values") or [None])[0], 1)) for k, d in rows]
        return (sentence(and_list(bits)) + ". This is the box's x·y over half of the %s %s the build placed, "
                "so the solute's own footprint is counted in it and the number reads high by that: a bare patch of "
                "%s is nearer 66 Å².%s" % (num(n, 0), e(lip), e(lip),
                                            " One replicate, so this is one observation of it."
                                            if self.one_replicate() else ""))

    def rg_figure(self):
        series = self.series("rg")
        if not series:
            return None
        return {"kind": "figure", "id": "rg", "caption": self.rg_caption(), "svg": R.plot(series, "ns", "Å")}

    def rg_caption(self):
        rows = self.many("rg")
        if not rows:
            return "No radius of gyration was written for the replicates shown."
        spread = max(v for _, v in rows) - min(v for _, v in rows)
        cap = "The solute's radius of gyration is %s Å" % and_list(num(v, 2) for _, v in rows)
        cap += (", the same to within %s Å across the replicates" % num(spread, 2) if len(rows) > 1
                else " over the one replicate")
        cap += ", so nothing swelled or collapsed; mass-weighted over the solute."
        return cap

    # ---- 5 The interface -------------------------------------------------------------------
    def interface_section(self):
        if not self.has_partner or not self.shown():
            return None
        blocks = []
        t = self.lost_table()
        if t:
            blocks.append(t)
        if self.opt["hbonds"]:
            t = self.hbond_table()
            if t:
                blocks.append(t)
                f = self.hbond_count_figure()
                if f:
                    blocks.append(f)
        if self.opt["byres"]:
            t = self.contact_res_table()
            if t:
                blocks.append(t)
        if not blocks:
            return None
        return {"id": "interface", "heading": "The interface", "label": "interface", "blocks": blocks}

    def lost_table(self):
        rows, have = [], False
        for k in self.shown():
            c = self.rep(k).get("contacts") or {}
            for x in (c.get("lost") or [])[:self.opt["top"]]:
                have = True
                rows.append({"contact": x.get("contact"), "frac": x.get("fraction"), "rep": "r%s" % k,
                             "topology": x.get("topology") or ""})
        if not have:
            return None
        cols = [{"key": "contact", "label": "Contact"},
                {"key": "frac", "label": "Fraction of frames present", "num": True, "digits": 2},
                {"key": "rep", "label": "Replicate"}]
        if any(r["topology"] for r in rows):
            cols.append({"key": "topology", "label": "As cpptraj named it"})
        return {"kind": "table", "id": "contacts_lost", "caption": self.lost_caption(), "columns": cols,
                "rows": rows, "note": ("Heavy-atom pairs within %s Å in the first frame. The analysis keeps the "
                                       "twelve least kept of %s, so a longer list is not available."
                                       % (num(4.5, 1),
                                          "the run" if self.one_replicate() else "each replicate"))}

    def lost_caption(self):
        parts = []
        named_by_topology = False
        for k in self.shown():
            c = self.rep(k).get("contacts") or {}
            if not c:
                continue
            lost = c.get("lost") or []
            if any(not x.get("topology") and str(x.get("contact", "")).startswith(":") for x in lost):
                named_by_topology = True
            if not lost:
                parts.append("in r%s every one of the %s first-frame contacts survives more than %s of the frames"
                             % (e(k), num(c.get("total"), 0), pct(c.get("threshold"))))
                continue
            worst = min(lost, key=lambda x: x.get("fraction") or 0)
            best = max(lost, key=lambda x: x.get("fraction") or 0)
            parts.append("of r%s's %s first-frame contacts, %s survive more than %s of the frames, and the %s least "
                         "kept run from %s (%s) to %s (%s)"
                         % (e(k), num(c.get("total"), 0), num(c.get("kept"), 0), pct(c.get("threshold")),
                            words(len(lost)), e(worst.get("contact")), pct(worst.get("fraction")),
                            e(best.get("contact")), pct(best.get("fraction"))))
        if not parts:
            return "No contact file was written for the replicates shown, so nothing can be said about what was lost."
        cap = sentence("; ".join(parts)) + "."
        kept = [(k, self.rep(k).get("contacts")) for k in self.shown() if self.rep(k).get("contacts")]
        if kept and all((c["kept"] / max(1, c["total"])) < 0.25 for _, c in kept):
            cap += (" Under a quarter of the first frame's contacts are left in any replicate, so the pose the "
                    "docking gave was not held.")
        elif kept and all(min(x.get("fraction") or 0 for x in (c.get("lost") or [{"fraction": 1}])) >= 0.45
                          for _, c in kept):
            cap += (" None of the lost contacts drops below %s, so nothing of the core interface went away "
                    "altogether." % pct(min(min((x.get("fraction") or 0) for x in (c.get("lost") or [{"fraction": 1}]))
                                            for _, c in kept)))
        if named_by_topology:
            cap += (" Numbered as the topology numbers its residues, because this run was built before the "
                    "structure's own ids were kept.")
        else:
            cap += " Each residue by its chain, number and insertion code in the structure."
        return cap

    def hbond_data(self, k):
        save_ps = float(self.summary.get("save_ps") or self.spec.protocol.save_ps or 20.0)
        hb = hbonds(self.rundir / "analysis" / ("r%s_hbond_avg.dat" % k), self.rec_range, self.lig_range, self.ids)
        hc = hbond_counts(self.rundir / "analysis" / ("r%s_hbond_count.dat" % k), save_ps)
        return hb, hc

    def hbond_table(self):
        rows, seen = [], []
        for k in self.shown():
            hb, _ = self.hbond_data(k)
            if not hb:
                continue
            crossing = [r for r in hb["rows"] if r["crossing"]] if hb["split"] else hb["rows"]
            seen.append((k, hb, crossing))
            for r in crossing[:self.opt["top"]]:
                rows.append({"acceptor": atom_words(r["acceptor"]), "donorh": atom_words(r["donorh"]),
                             "donor": atom_words(r["donor"]), "frac": r["frac"], "dist": r["dist"],
                             "angle": r["angle"], "rep": "r%s" % k})
        if not seen:
            return None
        cols = [{"key": "acceptor", "label": "Acceptor"},
                {"key": "donorh", "label": "Donor H"},
                {"key": "donor", "label": "Donor"},
                {"key": "frac", "label": "Fraction of frames", "num": True, "digits": 2},
                {"key": "dist", "label": "Mean distance (Å)", "num": True, "digits": 2,
                 "help": "over the frames in which the bond is present"},
                {"key": "angle", "label": "Mean angle (°)", "num": True, "digits": 0}]
        if len(self.shown()) > 1:
            cols.append({"key": "rep", "label": "Replicate"})
        note = ("cpptraj's hbond runs over the whole solute, so most of what it finds is a chain holding itself; "
                "this table keeps only the pairs whose acceptor and donor lie on opposite sides of the "
                "receptor/partner split.")
        if not any(hb["split"] for _, hb, _ in seen):
            note = ("The run does not record where the receptor ends and the partner begins, so these are every "
                    "hydrogen bond of the solute, its own internal ones included.")
        return {"kind": "table", "id": "hbonds", "caption": self.hbond_caption(seen), "columns": cols,
                "rows": rows, "note": note,
                "empty": "No hydrogen bond crosses the interface in the frames analyzed."}

    def hbond_caption(self, seen):
        bits = []
        for k, hb, crossing in seen:
            if not crossing:
                bits.append("r%s holds none at all across the interface, of the %s pairs the solute holds somewhere"
                            % (e(k), num(hb["total"], 0)))
                continue
            top = crossing[:3]
            held = [r for r in crossing if r["frac"] > 0.5]
            most = and_list("%s to %s at %s Å (%s of frames)"
                            % (atom_words(r["acceptor"]), atom_words(r["donor"]), num(r["dist"], 2), pct(r["frac"]))
                            for r in top)
            bits.append("r%s holds %s across the interface in more than half its frames, of %s pairs seen at all, and "
                        "the %s most held %s %s"
                        % (e(k), count(len(held), "bond") if held else "none", num(hb["total"], 0),
                           words(len(top)), "is" if len(top) == 1 else "are", most))
        if not bits:
            return ("No hydrogen-bond file was written for the replicates shown: the analysis job writes "
                    "analysis/r&lt;i&gt;_hbond_avg.dat, and this run has none.")
        cap = sentence("; ".join(bits)) + "."
        cap += (" Fractions are of that replicate's frames; distances and angles are means over the frames in "
                "which the bond is present, so a bond held in few frames still shows a short distance.")
        if self.ids is None:
            cap += " Residues are numbered as the topology numbers them."
        return cap

    def hbond_count_figure(self):
        series, rows = [], []
        for i, k in enumerate(self.shown()):
            _, hc = self.hbond_data(k)
            if hc:
                rows.append((k, hc))
                series.append({"x": hc["ns"], "y": hc["values"], "col": R.COLOURS[i % len(R.COLOURS)],
                               "name": "r%s" % k})
        if not series:
            return None
        bits = ["r%s holds %s hydrogen bonds on average, between %s and %s, having started at %s"
                % (e(k), num(d["mean"], 0), num(d["min"], 0), num(d["max"], 0), num(d["first"], 0))
                for k, d in rows]
        cap = (sentence("; ".join(bits)) + ". These are every hydrogen bond of the solute, the chains' own "
               "included, so the count is a measure of the fold holding together rather than of the interface; "
               "the interface's own bonds are in {{table:hbonds}}.")
        return {"kind": "figure", "id": "hbond_count", "caption": cap, "svg": R.plot(series, "ns", "bonds")}

    def contact_res_table(self):
        rows, seen = [], []
        for k in self.shown():
            got = contact_residues(self.rundir / "analysis" / ("r%s_contact_res.dat" % k),
                                   self.rec_range, self.lig_range, self.ids)
            if not got:
                continue
            crossing = [r for r in got if r["crossing"]] or got
            seen.append((k, crossing))
            for r in crossing[:self.opt["top"]]:
                rows.append({"rec": r["rec_id"] or ":%s" % r["rec"], "lig": r["lig_id"] or ":%s" % r["lig"],
                             "total": r["total"], "contacts": r["contacts"], "rep": "r%s" % k})
        if not seen:
            return None
        cols = [{"key": "rec", "label": "Receptor residue"},
                {"key": "lig", "label": "%s residue" % self.lig_word.capitalize()},
                {"key": "total", "label": "Total fraction", "num": True, "digits": 2,
                 "help": "the occupancies of the pair's atom contacts added up, so it exceeds 1"},
                {"key": "contacts", "label": "Atom pairs", "num": True, "digits": 0}]
        if len(self.shown()) > 1:
            cols.append({"key": "rep", "label": "Replicate"})
        return {"kind": "table", "id": "contact_res", "caption": self.contact_res_caption(seen),
                "columns": cols, "rows": rows}

    def contact_res_caption(self, seen):
        bits = []
        for k, got in seen:
            top = got[:3]
            bits.append("in r%s, %s carry most of the interface: %s"
                        % (e(k), count(len(top), "residue pair"),
                           and_list("%s with %s (%s over %s atom pairs)"
                                    % (r["rec_id"] or ":%s" % r["rec"], r["lig_id"] or ":%s" % r["lig"],
                                       num(r["total"], 2), num(r["contacts"], 0)) for r in top)))
        cap = sentence("; ".join(bits)) + "."
        cap += (" Total fraction adds up each atom pair's occupancy, so a pair held by several contacts exceeds 1; "
                "it is not a probability.")
        if self.ids is None:
            cap += " Residues are the topology's numbers, this run having been built before the structure's ids were kept."
        return cap

    # ---- 6 MM-GBSA ------------------------------------------------------------------------
    def energy_section(self):
        if not self.has_partner:
            return None
        if self.refusal:
            return {"id": "energy", "heading": "MM-GBSA", "label": "energy", "blocks": [
                {"kind": "p", "tone": "warn", "html": e(self.refusal)},
                {"kind": "p", "html":
                 "No binding energy is reported for this run, and none should be quoted from it. A split that no "
                 "bond crosses can be scored."}]}
        if not self.mms():
            if not self.spec.analysis.get("mmgbsa", True):
                return {"id": "energy", "heading": "MM-GBSA", "label": "energy", "blocks": [
                    {"kind": "p", "tone": "caveat", "html":
                     "MM-GBSA was switched off when this run was set up, so there is no binding energy. The Runs "
                     "screen's MM-GBSA… button scores a finished run without rerunning it."}]}
            return {"id": "energy", "heading": "MM-GBSA", "label": "energy", "blocks": [
                {"kind": "p", "tone": "warn", "html":
                 "The binding energy has not been computed yet: no analysis/r&lt;i&gt;_mmgbsa.dat has been written. "
                 "The MM-GBSA job is the last of the graph, and until it finishes the pose's stability above is all "
                 "this report can say."}]}
        return {"id": "energy", "heading": "MM-GBSA", "label": "energy",
                "blocks": [self.mm_table(), self.mm_chart()]}

    def mm_table(self):
        a0, a1, st, want = self.window()
        rows = []
        for k in self.shown():
            m = self.mm(k)
            rows.append({"rep": "r%s" % k, "frames": (m or {}).get("frames"), "dg": (m or {}).get("mean"),
                         "sd": (m or {}).get("std"), "sem": (m or {}).get("sem"),
                         "window": "%s–%s ns, every %s ps" % (num(a0, 0), num(a1, 0), num(st, 0))})
        foot = []
        got = [m for _, m in self.mms()]
        if len(got) > 1:
            foot.append({"rep": "mean of the replicates", "frames": sum(m.get("frames") or 0 for m in got),
                         "dg": statistics.fmean(m["mean"] for m in got),
                         "sd": statistics.stdev([m["mean"] for m in got]) if len(got) > 1 else None,
                         "sem": None, "window": "", "_class": "rk-mark"})
        return {"kind": "table", "id": "mmgbsa", "caption": self.mm_caption(),
                "columns": [{"key": "rep", "label": "Replicate"},
                            {"key": "frames", "label": "Frames scored", "num": True, "digits": 0},
                            {"key": "dg", "label": "ΔG (kcal/mol)", "num": True, "digits": 1,
                             "help": "lower is better"},
                            {"key": "sd", "label": "SD", "num": True, "digits": 1},
                            {"key": "sem", "label": "SEM", "num": True, "digits": 2},
                            {"key": "window", "label": "Window"}],
                "rows": rows, "foot": foot,
                "note": ("The SD and the SEM of a replicate's row are over its own frames. The last row's SD is "
                         "over the replicates' means, which is the spread that matters when the number is quoted."
                         if len(got) > 1 else
                         "The SD and the SEM are over this replicate's frames. With one replicate there is no "
                         "spread between runs to report.")}

    def mm_caption(self):
        got = self.mms()
        a0, a1, st, want = self.window()
        if not got:
            return "No binding energy was written for the replicates shown."
        frames = [m.get("frames") for _, m in got if m.get("frames")]
        head = ("Scored over %s to %s ns of each replicate, every %s ps" % (num(a0, 0), num(a1, 0), num(st, 0))
                if len(got) > 1 else
                "Scored over %s to %s ns, every %s ps" % (num(a0, 0), num(a1, 0), num(st, 0)))
        if frames:
            head += (", %s frames each: " % num(frames[0], 0) if len(set(frames)) == 1 and len(got) > 1
                     else ", %s frames: " % num(frames[0], 0))
        else:
            head += ": "
        head += and_list("%s ± %s" % (num(m["mean"], 1), num(m.get("std"), 1)) for _, m in got) + " kcal/mol"
        head += " (mean ± SD; SEM %s)." % and_list(num(m.get("sem"), 2) for _, m in got)
        if len(got) > 1:
            means = [m["mean"] for _, m in got]
            spread = max(means) - min(means)
            worst = max((m.get("std") or 0.0) for _, m in got)
            if spread > 2 * worst:
                head += (" The %s kcal/mol spread between replicates is larger than any one replicate's own SD, so "
                         "the mean of the %s, %s kcal/mol, is the number to quote, not the best of them."
                         % (num(spread, 0), words(len(got)), num(statistics.fmean(means), 1)))
            else:
                head += (" The replicates lie within %s kcal/mol of each other, inside their own SDs, so their mean "
                         "of %s kcal/mol carries the result." % (num(spread, 1), num(statistics.fmean(means), 1)))
        else:
            head += (" One replicate: the ± is over this run's frames and is not a spread between runs, which "
                     "only a second replicate could give.")
        if frames and want and frames[0] < want:
            head += (" %s frames were scored where the window asks for %s, so the job stopped short or the "
                     "trajectory did." % (num(frames[0], 0), num(want, 0)))
        return head

    def mm_chart(self):
        got = self.mms()
        # the bar of a lone replicate carries the words on the figure itself, so that an error bar
        # cannot be read across runs when there is only one
        label = "r%s (one replicate)" if self.one_replicate() else "r%s"
        points = [{"name": label % k, "v": m["mean"], "sd": m.get("std")} for k, m in got]
        if len(got) > 1:
            means = [m["mean"] for _, m in got]
            points.append({"name": "mean of %s" % words(len(got)), "v": statistics.fmean(means),
                           "sd": statistics.stdev(means), "dim": True})
        cap = self.mm_chart_caption()
        return {"kind": "figure", "id": "mmgbsa_chart", "caption": cap,
                "bars": {"axis": "MM-GBSA ΔG (kcal/mol); lower is better", "digits": 1, "points": points}}

    def mm_chart_caption(self):
        got = self.mms()
        if len(got) == 1:
            k, m = got[0]
            return ("One bar, one replicate: %s kcal/mol with its error bar at ± %s, the SD over the %s frames "
                    "scored inside r%s. There is no second replicate to put beside it, so the bar shows how much "
                    "the energy moved from frame to frame and not how repeatable it is."
                    % (num(m["mean"], 1), num(m.get("std"), 1), num(m.get("frames"), 0), e(k)))
        means = [m["mean"] for _, m in got]
        return ("The replicates' error bars are the SD over each one's own frames (± %s), while the bars' own "
                "positions differ by %s kcal/mol: the run-to-run spread is the larger of the two, and the pale bar "
                "at the foot is the mean of the %s with the SD of their means, %s kcal/mol, which is the error to "
                "quote."
                % (and_list(num(m.get("std"), 1) for _, m in got), num(max(means) - min(means), 1),
                   words(len(got)), num(statistics.stdev(means), 1)))

    # ---- 7 The structure as it ended -------------------------------------------------------
    def pictures_section(self):
        if not self.opt["pictures"] or not self.shown():
            return None
        blocks = []
        for k in self.shown():
            dry = self.snaps(k, "dry")
            if dry:
                blocks.append({"kind": "figure", "id": "snaps_r%s" % k, "caption": self.snaps_caption(k),
                               "columns": 2,
                               "panels": [{"png": self.media(rel),
                                           "label": "%s frame" % ("first" if when == "first" else "last")}
                                          for when, rel in dry]})
            wet = self.snaps(k, "water")
            if wet:
                blocks.append({"kind": "figure", "id": "snaps_water_r%s" % k,
                               "caption": self.water_caption(k), "columns": 2,
                               "panels": [{"png": self.media(rel),
                                           "label": "%s frame, with water and ions" % when}
                                          for when, rel in wet]})
        f = self.pose_figure()
        if f:
            blocks.append(f)
        if not blocks:
            return None
        if not any(self.snaps(k) for k in self.shown()):
            blocks.insert(0, {"kind": "p", "tone": "caveat", "html":
                              "The snapshot job has not run for this run, so there are no rendered stills; the view "
                              "below is drawn from the strided trajectory instead. Render the snapshots with "
                              "<code>python3 -m hopemd.export.snapshots &lt;run&gt;</code>."})
        return {"id": "pictures", "heading": "The structure as it ended", "label": "pictures",
                "newpage": True, "blocks": blocks}

    def snaps_caption(self, k):
        iface = self.one(k, "rmsd_iface")
        lig = self.one(k, "rmsd_lig")
        ns = num(self.spec.protocol.length_ns, 0)
        if iface is not None and iface < 3.0:
            what = ("the pose in the last frame is the pose in the first, which is what the %s Å interface RMSD "
                    "says in numbers" % num(iface, 1))
        elif lig is not None and lig > 4.0:
            what = ("the %s is not where it started: its RMSD averages %s Å over the run"
                    % (e(self.lig_word) or "partner", num(lig, 1)))
        else:
            what = "no interface RMSD was written, so the two frames are all there is to compare"
        colors = ("Receptor gray, %s rust, the interface teal" % e(self.lig_word) if self.has_partner
                   else "The solute in the page's colors, each chain its own")
        return ("r%s at 0 and at %s ns, the solute alone: %s. %s; the same orientation in both, rendered by POV-Ray "
                "from the trajectory." % (e(k), ns, what, colors))

    def water_caption(self, k):
        box = (self.system or {}).get("box")
        waters = (self.system or {}).get("waters")
        return ("The same two frames of r%s with the box as it was simulated: %s waters%s around the solute, water "
                "pale blue and ions amber. The box is what the barostat relaxed to, so its edges move between the "
                "two frames." % (e(k), num(waters, 0) if waters else "the",
                                 " in %s Å" % e(str(box)) if box else ""))

    def view_chains(self, path):
        """The chain ids the viewer's trajectory carries. cpptraj writes it from the topology, and an
        Amber topology has no chains at all, so this is usually empty; a Desmond run keeps the
        structure's own. What is there decides how the figure marks the partner."""
        got = set()
        try:
            with open(path, errors="replace") as fh:
                for line in fh:
                    if line.startswith(("ATOM", "HETATM")) and len(line) > 21:
                        got.add(line[21].strip())
                    elif line.startswith("ENDMDL"):
                        break
        except OSError:
            return set()
        return {c for c in got if c}

    def pose_figure(self):
        k = next((k for k in self.shown() if (self.rundir / "analysis" / ("r%s_view.pdb" % k)).is_file()), None)
        if k is None:
            return None
        s = self.spec.system
        rel = "analysis/r%s_view.pdb" % k
        have = self.view_chains(self.rundir / rel)
        rec_ch = [c for c in (s.receptor_chains or []) if c in have]
        lig_ch = [c for c in (s.partner_chains or []) if c in have]
        layers = [{"sel": {}, "style": {"cartoon": {"color": "#b9c9c9"}}}]
        if self.has_partner:
            # the partner as the Runs viewer marks it: by its chains where the topology kept them,
            # else by its residues in the topology's own numbering, which is what the page reads
            if lig_ch:
                sel = {"chain": lig_ch}
            elif self.lig_range:
                # the page's own form (app.js runSel): every residue number listed, which is what
                # 3Dmol reads without ambiguity
                sel = {"resi": list(range(self.lig_range[0], self.lig_range[1] + 1))}
            else:
                sel = None
            if sel is not None:
                layers.append({"sel": sel, "add": True,
                               "style": {"cartoon": {"color": "#c2603a"},
                                         "stick": {"radius": 0.16, "colorscheme": "orangeCarbon"}}})
        layers.append({"sel": {"elem": "H"}, "style": {}})
        spec = {"url": self.media_file(rel), "format": "pdb", "layers": layers}
        if rec_ch and lig_ch:
            # the interface seen side on; only where the chains are there to turn it by
            spec["side_on"] = [rec_ch, lig_ch]
        return {"kind": "figure", "id": "pose3d", "caption": self.pose_caption(k), "structure": spec}

    def media_file(self, rel):
        from urllib.parse import quote
        return "/api/file?name=%s&path=%s" % (quote(self.rundir.name), quote(rel))

    def pose_caption(self, k):
        stride = int((self.spec.analysis.get("viewer_stride") or 20))
        save_ps = float(self.summary.get("save_ps") or self.spec.protocol.save_ps or 20.0)
        iface = self.rep(k).get("rmsd_iface") or {}
        lig = self.rep(k).get("rmsd_lig") or {}
        bits = []
        if iface.get("values"):
            vals = iface["values"]
            bits.append("the last frame's interface RMSD is %s Å against %s Å early on"
                        % (num(vals[-1], 1), num(vals[1] if len(vals) > 1 else vals[0], 1)))
        elif lig.get("values"):
            vals = lig["values"]
            bits.append("the %s's RMSD ends at %s Å, having started at %s Å"
                        % (e(self.lig_word) or "partner", num(vals[-1], 1), num(vals[0], 1)))
        else:
            rec = self.rep(k).get("rmsd_rec") or {}
            if rec.get("values"):
                bits.append("the %s RMSD ends at %s Å, having averaged %s Å over the run"
                            % (e(self.rec_trace), num(rec["values"][-1], 1), num(rec.get("mean"), 1)))
            else:
                bits.append("no RMSD series was written for this replicate, so the picture is all there is")
        colors = ("Receptor gray, %s rust" % e(self.lig_word) if self.has_partner else "The solute gray")
        return ("r%s as it ended: %s. %s; the cartoon is the last frame of the strided trajectory the viewer reads, "
                "every %s saved frame, that is every %s ps."
                % (e(k), and_list(bits), colors, num(stride, 0), num(stride * save_ps, 0)))

    # ---- 8 What it cost --------------------------------------------------------------------
    def timing_section(self):
        if not self.opt["timing"]:
            return None
        try:
            from ..timing import timing as _timing
            tm = _timing(self.rundir)
        except Exception:                                      # noqa: BLE001 accounting down, a read-only checkout
            tm = None
        if not tm or not tm.get("jobs"):
            return None
        rows = []
        for j in tm["jobs"]:
            dev = j.get("gpu") or ("%s CPU" % j.get("cpus") if j.get("cpus") else "")
            rows.append({"stage": j.get("key"), "job": j.get("job"), "state": (j.get("state") or "").lower(),
                         "node": j.get("node"), "partition": j.get("partition"), "device": dev,
                         "work": (num(j.get("steps"), 0) + " steps" if j.get("steps") else
                                  num(j.get("frames"), 0) + " frames" if j.get("frames") else ""),
                         "elapsed": j.get("elapsed"), "nsday": j.get("ns_per_day")})
        return {"id": "timing", "heading": "What it cost", "label": "cost", "blocks": [
            {"kind": "table", "id": "timing", "caption": self.timing_caption(tm),
             "columns": [{"key": "stage", "label": "Stage"}, {"key": "job", "label": "Job"},
                         {"key": "state", "label": "State"}, {"key": "node", "label": "Node"},
                         {"key": "partition", "label": "Partition"}, {"key": "device", "label": "Device"},
                         {"key": "work", "label": "Steps or frames"},
                         {"key": "elapsed", "label": "Wall time"},
                         {"key": "nsday", "label": "ns/day", "num": True, "digits": 1}],
             "rows": rows}]}

    def timing_caption(self, tm):
        est = tm.get("estimate") or {}
        prod = [j for j in tm["jobs"] if str(j.get("key", "")).startswith("prod_s") and j.get("ns_per_day")]
        measured = statistics.fmean(j["ns_per_day"] for j in prod) if prod else None
        bits = ["the whole graph took %s of wall time and %s of node time"
                % (e(tm.get("wall") or "—"), e(tm.get("busy") or "—"))]
        if prod:
            dev = prod[0].get("gpu") or ("%s CPUs" % prod[0].get("cpus") if prod[0].get("cpus") else "the node")
            bits.append("%s of it production on %s at %s ns/day"
                        % (and_list(j.get("elapsed") for j in prod[:3]), e(dev), num(measured, 0)))
            if est.get("ns_per_day"):
                bits.append("against the %s ns/day estimated at submit%s"
                            % (num(est["ns_per_day"], 0), " (%s)" % e(est.get("note")) if est.get("note") else ""))
        after = []
        for key, what in (("analysis", "analysis"), ("mmgbsa", "MM-GBSA")):
            mine = [j for j in tm["jobs"] if j.get("key") == key and j.get("elapsed_s")]
            if not mine:
                continue
            after.append("%s of %s%s" % (hms(sum(j["elapsed_s"] for j in mine)), what,
                                         " over %s" % count(len(mine), "job") if len(mine) > 1 else ""))
        if after:
            bits.append("then %s" % and_list(after))
        cap = sentence(and_list(bits)) + "."
        if est.get("walltime"):
            cap += " Each segment asked for %s of walltime." % e(est["walltime"])
        if not tm.get("complete", True):
            cap += " Some jobs have not finished, so the totals are of what has run so far."
        return cap

    # ---- Methods ---------------------------------------------------------------------------
    def methods(self):
        meth, keys = R.methods(self.spec, self.prep, self.system, self.summary,
                               R.build_protonation(self.rundir), self.half, R.build_seeded(self.rundir),
                               ran_mmgbsa=any(self.mm(k) for k in self.keys()) if self.reps else None)
        if not self.reps:
            meth = ("This run has no analysis yet, so this paragraph describes the run as it was set up, not what "
                    "ran. " + meth)
        refs = [{"n": i + 1, "text": "%s: %s" % (R.REFERENCES[k][0], R.REFERENCES[k][1])}
                for i, k in enumerate(keys)]
        # the contract's methods is HTML: & < > are escaped, and quotes are left as they read,
        # since the paragraph is prose a person copies out of the report
        out = html.escape(meth, quote=False)
        if self.spec.engine == "namd":
            # NAMD's license asks for this sentence, in these words, in anything reporting work it
            # did. It is a license term, not a citation, and the reference stands as well.
            out += (" <b>Acknowledgment, which NAMD's license requires.</b> "
                    + html.escape(R.NAMD_ACKNOWLEDGEMENT, quote=False))
        return out, refs

    # ---- Appendix --------------------------------------------------------------------------
    def appendix(self):
        out = [{"heading": "Settings, as spec.json names them", "blocks": [self.settings_table()]}]
        prep_blocks = self.prep_blocks()
        if prep_blocks:
            out.append({"heading": "Preparation", "blocks": prep_blocks})
        if self.system:
            out.append({"heading": "The built system", "blocks": [
                {"kind": "facts", "rows": [[e(k), e(json.dumps(v) if isinstance(v, (dict, list)) else v)]
                                           for k, v in sorted(self.system.items())]}]})
        out.append({"heading": "Provenance", "blocks": self.provenance_blocks()})
        caveats = self.caveat_items()
        if caveats:
            out.append({"heading": "Caveats, each only where it is true",
                        "blocks": [{"kind": "list", "items": caveats}]})
        out.append({"heading": "Jobs, modules and software", "blocks": self.jobs_blocks()})
        out.append({"heading": "The run folder", "blocks": self.files_blocks()})
        out.append({"heading": "If a figure from the viewer is used", "blocks": [
            {"kind": "p", "html": "These apply only to a figure taken from the viewer, not to the run itself."},
            {"kind": "list", "items": ["%s, for %s: %s doi:%s" % (e(n), e(w), e(r), e(d))
                                       for n, w, r, d in VIEWER_REFERENCES]}]})
        return out

    def settings_table(self):
        raw = self._spec_raw
        rows = []
        for group in ("system", "forcefield", "box", "protocol", "compute", "analysis"):
            got = raw.get(group)
            if not isinstance(got, dict):
                continue
            for k, v in got.items():
                if group == "system" and k in ("residue_index", "chains_found", "hetero_found", "gaps_found",
                                               "prep_report", "meta"):
                    continue                           # long and already said above, each in its own words
                rows.append({"field": "%s.%s" % (group, k),
                             "value": json.dumps(v) if isinstance(v, (dict, list)) else ("" if v is None else str(v))})
        rows.append({"field": "site", "value": "%s (%s)" % (self.spec.site.name, self.spec.compute.site)})
        return {"kind": "table", "id": "settings", "caption": self.settings_caption(rows),
                "columns": [{"key": "field", "label": "Field"}, {"key": "value", "label": "Value"}],
                "rows": rows}

    def settings_caption(self, rows):
        pro = self.spec.protocol
        return ("Every field of spec.json that the run was rendered from, %s of them, named as the file names them: "
                "the preset was %s, which set %s ns × %s at a %s fs step with frames every %s ps. Anything the "
                "person changed after the preset is in this table and nowhere else."
                % (num(len(rows), 0), e(pro.preset or "none"), num(pro.length_ns, 0), num(pro.replicates, 0),
                   num(pro.timestep_fs, 0), num(pro.save_ps, 0)))

    def prep_blocks(self):
        p = self.prep
        if not p:
            return [{"kind": "p", "tone": "caveat", "html":
                     "No Prepare stage ran: build/prep_report.json is not there, so the structure went into the "
                     "build as the import left it."}]
        out = []
        if p.get("log"):
            out.append({"kind": "pre", "text": "\n".join(str(x) for x in p["log"])})
        rows = []
        for key, label in (("clashes", "Clashes"), ("breaks", "Breaks"), ("split", "Split"),
                           ("started", "Started"), ("finished", "Finished")):
            if p.get(key) not in (None, "", [], {}):
                v = p[key]
                if key == "clashes" and isinstance(v, dict):
                    rows.append([label, "%s before, %s after" % (num(v.get("before"), 0), num(v.get("after"), 0))])
                else:
                    rows.append([label, e(json.dumps(v) if isinstance(v, (dict, list)) else v)])
        if rows:
            out.append({"kind": "facts", "rows": rows})
        return out

    def provenance_blocks(self):
        from ..provenance import describe
        items = []
        for entry in (self.spec.rendered_by or []):
            items.append("%s (%s, %s)%s" % (e(describe(entry)), e(entry.get("step", "a stage it did not name")),
                                            e(entry.get("at", "a time it did not record")),
                                            ", with the tree modified" if entry.get("modified") else ""))
        if not items:
            items.append("The commit that rendered this run was not recorded: it was submitted before HOPE-MD kept it.")
        who = _who(self.rundir)
        items.append("The run folder belongs to %s." % (e(who) if who else "an account that cannot be read"))
        items.append("Where the structure came from: %s." % e(R.source_sentence(self.spec)))
        return [{"kind": "list", "items": items}]

    def caveat_items(self):
        out = []
        if self.ran:
            out.append("This run's inputs heated for %s ps and equilibrated for %s ps, not the %s and %s ps the spec "
                       "asks for: the renderer of the day divided picoseconds by 2 instead of 0.002."
                       % (num(self.ran[0], 3), num(self.ran[1], 3), num(self.spec.protocol.heat_ps, 0),
                          num(self.spec.protocol.equil_ps, 0)))
        if self.half:
            out.append("The restraints of the ladder had the form ½k·d² on this engine, so the weights "
                       "shown held the solute half as strongly as the same k does in Amber's k·d².")
        if not R.build_seeded(self.rundir) and self.spec.protocol.seed >= 0:
            out.append("The spec fixes a seed, but the build placed its ions before the seed was passed to tleap, so "
                       "the starting system is not reproducible from the seed alone.")
        prot = R.build_protonation(self.rundir)
        if prot and prot[0] != "propka":
            out.append("PDB2PQR was not available to the build, so the receptor took tleap's default protonation "
                       "states (HIE, charged Asp, Glu, Lys and Arg) rather than states for pH %s."
                       % num(self.spec.system.ph, 1))
        if self.ids is None and self.reps:
            out.append("The residues of this run's topology cannot be named as the structure names them (the build "
                       "kept no numbering, or the engine's topology carries its own ids), so every contact and "
                       "fluctuation above is numbered as the topology numbers it.")
        if self.one_replicate():
            out.append("This report rests on one replicate. Nothing in it has a spread between replicates, and the "
                       "± it shows is over frames inside that one run: a single replicate is an "
                       "observation, not a statistic, and a protocol that asks for three is not answered by one.")
        if not self.software():
            out.append("The engine's own version was not recorded by this run's jobs: the references were checked "
                       "against the module lines the jobs loaded, which are in the next section, and not against "
                       "what the program reported of itself.")
        return out

    def jobs_blocks(self):
        rows = [[e(k), e(v)] for k, v in sorted((self.ids_json.get("jobs") or {}).items())]
        out = []
        if rows:
            out.append({"kind": "facts", "rows": rows})
        else:
            out.append({"kind": "p", "tone": "caveat", "html": "jobs/ids.json records no job ids for this run."})
        out.append({"kind": "heading", "text": "The module lines the job scripts loaded"})
        out.append({"kind": "pre", "text": self.spec.site.module_block(self.spec.engine)})
        sw = self.software()
        if sw:
            out.append({"kind": "heading", "text": "What each job's engine said its version was"})
            out.append({"kind": "facts", "rows": [["%s, %s" % (e(key), e(stage or "a job it did not name")), e(v)]
                                                  for key, stage, v in sw]})
        return out

    def files_blocks(self):
        out = []
        try:
            readme = (self.rundir / "README.txt").read_text(errors="replace")
        except OSError:
            readme = ""
        if readme:
            out.append({"kind": "pre", "text": readme.strip()})
        arch = self.tidied()
        out.append({"kind": "p", "html": (
            "The run has been tidied: its small files are packed into archive/%s and are not on disk separately, so "
            "anything this report calls missing may be in there." % e(arch)) if arch else
            "The run has not been tidied: every file the jobs wrote is still where they wrote it."})
        return out

    # ---- views -----------------------------------------------------------------------------
    def views(self):
        out = []
        for it in load_items(self.rundir):
            out.append({"id": it.get("id"), "caption": self.view_caption(it), "own": it.get("own") or "",
                        "png": "/api/report/image?name=%s&id=%s" % (self.rundir.name, it.get("id")), "blocks": []})
        return out

    def view_caption(self, it):
        """A view's caption is the server's, written from the run: which replicate, which frame, and
        what the numbers say of it. The reader's own words go under it."""
        k = str(it.get("rep") or "")
        frame = it.get("frame")
        bits = []
        if k and self.rep(k):
            lig = self.one(k, "rmsd_lig")
            rec = self.one(k, "rmsd_rec")
            if rec is not None:
                bits.append("the receptor averages %s Å %s RMSD over r%s" % (num(rec, 2), e(self.rec_trace), e(k)))
            if lig is not None and self.has_partner:
                bits.append("the %s %s Å" % (e(self.lig_word), num(lig, 2)))
        where = "r%s" % k if k else "the trajectory"
        if frame not in (None, ""):
            when = ", frame %s of the strided view" % num(frame, 0)
        elif str(it.get("what") or "").strip():
            when = ", %s" % e(str(it["what"]).strip())
        else:
            when = ""
        if not bits:
            return ("A view of %s%s, added from the viewer. The run has no numbers for it yet, so there is nothing "
                    "to read beside it." % (e(where), when))
        return "A view of %s%s: %s." % (e(where), when, and_list(bits))


# ---- the document, and the page it is drawn on ---------------------------------------------
def document(rundir, raw_options=None):
    """The report of one run, as the report page reads it (GET /api/report)."""
    try:
        return Report(rundir, raw_options or {}).build()
    except FileNotFoundError:
        return {"error": "no such run, or it has no spec.json"}
    except Exception as exc:                                   # noqa: BLE001
        return {"error": "the report could not be written: %s" % exc}


#: The report of a run: a page to read and to save as a PDF. reportkit, the module the lab's tools
#: share, lays it out from GET /api/report. The page is light whatever the interface's theme, as
#: paper is.
PAGE = r"""<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Report</title>
<link rel="stylesheet" href="/static/reportkit.css?v=__VERSION__">
<div id=rk-bar></div>
<main id=rk-doc><p class=rk-why>Reading the run&hellip;</p></main>
<script src="/static/vendor/3Dmol-min.js"></script>
<script src="/static/reportkit.js?v=__VERSION__"></script>
<script>
ReportKit.start({api: '/api/report', token: __TOKEN__, params: {name: __NAME__},
                 store: 'hopemd.report', edit: '/api/report/edit',
                 name: 'Report', autoprint: __PRINT__});
</script>
"""


def page(token, name, autoprint=False):
    """The report page for one run, carrying the token and the run.

    Both are written in with json.dumps and `<` escaped, so a run whose name holds a quote or a
    closing tag cannot end the script."""
    js = lambda v: json.dumps(str(v or "")).replace("<", "\\u003c")        # noqa: E731
    return (PAGE.replace("__TOKEN__", js(token))
                .replace("__NAME__", js(name))
                .replace("__PRINT__", "true" if autoprint else "false")
                .replace("__VERSION__", __version__))


#: A page that stands on its own: the module, the stylesheet and the document in one file, for
#: `python3 -m hopemd.export.report <rundir>`. The module fetches its document, so the one URL it
#: asks for is answered from the file itself; the bar's own options need the page server, which the
#: status line says.
OFFLINE = r"""<!doctype html><html lang=en><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
__CSS__
</style>
<div id=rk-bar></div>
<main id=rk-doc><p class=rk-why>Reading the run&hellip;</p></main>
<script>
var RK_DOC = __DOC__;
(function(){
  var real = window.fetch;
  window.fetch = function(u){
    if (String(u).indexOf('hopemd:report') === 0)
      return Promise.resolve({status: 200, json: function(){ return Promise.resolve(RK_DOC); }});
    return real.apply(window, arguments);
  };
})();
</script>
<script>
__JS__
</script>
<script>
ReportKit.start({api: 'hopemd:report', params: {}, store: 'hopemd.report.file', name: 'Report'});
</script>
</html>
"""


def offline_page(rundir, raw_options=None):
    """The whole report in one file: no server, no network. The structure figures and the pictures
    need the page server, so they are left out and the sections say so."""
    doc = document(rundir, raw_options)
    static = Path(__file__).resolve().parent.parent / "static"
    css = (static / "reportkit.css").read_text()
    js = (static / "reportkit.js").read_text()
    doc = _without_served(doc)
    doc["ready"] = ("This is the report as a file on its own. Press Save as PDF to print it; the options on the bar "
                    "that need the run read again are only on the page server's /report.")
    return (OFFLINE.replace("__CSS__", css).replace("__JS__", js)
                   .replace("__TITLE__", html.escape(str(doc.get("title") or "Report")))
                   .replace("__DOC__", json.dumps(doc).replace("</", "<\\/")))


def _without_served(doc):
    """The document with the blocks that need the page server taken out: a file on its own cannot
    fetch a structure or a picture from a run folder."""
    def keep(b):
        if b.get("kind") != "figure":
            return True
        if b.get("structure") or b.get("png"):
            return False
        if b.get("panels") and any(p.get("png") or p.get("structure") for p in b["panels"]):
            return False
        return True

    dropped = 0
    for sec in doc.get("sections") or []:
        before = len(sec.get("blocks") or [])
        sec["blocks"] = [b for b in (sec.get("blocks") or []) if keep(b)]
        dropped += before - len(sec["blocks"])
        if not sec["blocks"] and sec.get("id") == "pictures":
            sec["blocks"] = [{"kind": "p", "tone": "caveat", "html":
                              "The snapshots and the 3D view are drawn from the run folder, which a file on its own "
                              "cannot read: open the report from the page (Runs, then Report) to see them."}]
    doc.pop("views", None)
    doc.pop("views_hint", None)
    return doc


# ---- the views added from the viewer, kept in the run's report folder -----------------------
def _folder(rundir):
    return Path(rundir) / FOLDER


def load_items(rundir):
    got = _json_file(_folder(rundir) / "items.json")
    items = (got or {}).get("items")
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def save_items(rundir, items):
    d = _folder(rundir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "items.json").write_text(json.dumps({"items": items}, indent=1))


def add_item(rundir, body):
    """A view from the viewer: its PNG into the run's report folder, its place at the end."""
    data = str((body or {}).get("image") or "")
    m = re.match(r"^data:image/png;base64,([A-Za-z0-9+/=\s]+)$", data)
    if not m:
        return {"error": "the view did not arrive as a PNG"}
    import base64
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", m.group(1)), validate=True)
    except Exception:                                          # noqa: BLE001
        return {"error": "the view's picture could not be read"}
    if not raw or len(raw) > IMAGE_MAX:
        return {"error": "the view's picture is too large to keep"}
    items = load_items(rundir)
    n = max([int(i.get("id") or 0) for i in items] or [0]) + 1
    d = _folder(rundir)
    d.mkdir(parents=True, exist_ok=True)
    (d / ("view_%d.png" % n)).write_bytes(raw)
    items.append({"id": n, "file": "view_%d.png" % n, "own": "",
                  "rep": str((body or {}).get("rep") or ""), "frame": (body or {}).get("frame"),
                  "what": str((body or {}).get("what") or "")[:400], "at": time.strftime("%Y-%m-%d %H:%M")})
    save_items(rundir, items)
    return {"ok": True, "id": n}


def edit_item(rundir, body):
    """A view's own words changed, the view moved, or taken out."""
    body = body or {}
    items = load_items(rundir)
    try:
        n = int(body.get("id"))
    except (TypeError, ValueError):
        return {"error": "no such view"}
    at = next((i for i, it in enumerate(items) if int(it.get("id") or 0) == n), None)
    if at is None:
        return {"error": "no such view"}
    if body.get("remove"):
        gone = items.pop(at)
        try:
            (_folder(rundir) / str(gone.get("file") or "")).unlink()
        except OSError:
            pass
    elif body.get("move"):
        step = -1 if int(body["move"]) < 0 else 1
        to = max(0, min(len(items) - 1, at + step))
        items.insert(to, items.pop(at))
    else:
        items[at]["own"] = str(body.get("own") or "")[:2000]
    save_items(rundir, items)
    return {"ok": True}


def item_image(rundir, n):
    """A view's picture, as bytes, or None."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    it = next((i for i in load_items(rundir) if int(i.get("id") or 0) == n), None)
    if not it:
        return None
    p = _folder(rundir) / str(it.get("file") or "")
    if p.name != os.path.basename(str(it.get("file") or "")) or not p.is_file():
        return None
    try:
        return p.read_bytes()
    except OSError:
        return None
