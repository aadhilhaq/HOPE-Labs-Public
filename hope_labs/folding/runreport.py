"""The report of a folding run: Boltz-2 or ColabFold, written up to be read.

By Aadhil Haq

The reader is whoever folded a complex to decide whether to trust it. The report answers four
questions from the run's own numbers: which sample is best and by how much, how confident each
chain and each chain pair is, where along the chains the confidence falls away, and whether the
alignment that fed the fold was a real one. It carries the two-phase record as well - what was
fetched on the login node, what ran on the GPU with no network - because that is what it takes to
repeat the fold.

The two tools are one report with two middles. Both pages are served by one module, queue one job
template and leave a submission.json of the same shape, so the title, the summary, the chains
table, the alignments table, the whole appendix and every option but the ranking are written once
here. What differs is what a sample knows about itself: Boltz-2 writes a confidence file with
per-chain and chain-pair ipTM, and ColabFold writes a scores file with one pTM and a mean pLDDT
and nothing per chain at all.

Two things this module will not do. It does not recompute a number the run already wrote, so the
report and the Runs screen cannot disagree; where it does compute one - the per-chain pLDDT means
and the aligned-error block means - it says so in the caption. And it writes nothing into a run
folder: a report only reads.

This runs in one stdlib-only process on a login node (see serve.py), which is why npz_array()
below reads a .npz with zipfile and struct rather than with numpy. The per-token pLDDT and the
aligned-error matrices are the one thing a folding run writes that is not text, and they are the
figures the reader came for.

    python -m hope_labs.folding.runreport boltz2 <run folder>     the document, as JSON
"""
from __future__ import annotations

import glob
import json
import os
import re
import struct
import sys
import time
import urllib.parse
import zipfile

from . import SERVES
from . import docs as folddocs
from . import runs as runsmod
from . import sites as sitesmod
from .. import reporting as rk

TOOL_NAME = {"boltz2": "Boltz-2", "colabfold": "ColabFold"}

#: The line above the title. Both tools fold; neither designs.
KICKER = "Structure prediction report"

#: AlphaFold's own pLDDT bands, in the page's own colours (web/app.js PLDDT), so a reader who has
#: seen a structure on the Runs screen sees the same scale here. Out of 100: both tools write the
#: B-factor column of a structure as a percentage, whatever the confidence files hold.
PLDDT_BANDS = ((90, 100, "#0053d6", "very high"), (70, 90, "#65cbf3", "confident"),
               (50, 70, "#ffdb13", "low"), (0, 50, "#ff7d45", "very low"))

#: What one of each kind of row is, and what its length is counted in. A ligand's length is heavy
#: atoms because that is what Boltz-2 counts as tokens for it, and the token axis of the pLDDT
#: and aligned-error arrays is counted the same way.
ENTITY = {
    "protein": ("protein", "residue"),
    "dna": ("DNA strand", "base"),
    "rna": ("RNA strand", "base"),
    "ligand": ("ligand", "heavy atom"),
    "ccd": ("ligand", "heavy atom"),
    "smiles": ("ligand", "heavy atom"),
    "ion": ("ion", "atom"),
}

#: Boltz-2's own mol_type, for a run whose submission.json was lost and whose kinds have to come
#: out of the record it wrote for itself.
MOL_TYPE = {0: "protein", 1: "dna", 2: "rna", 3: "ligand"}

#: The bar's choices. The same keys for both tools, with the words each tool's reader uses:
#: Boltz-2 draws diffusion samples and ColabFold runs AlphaFold models.
def _options(tool):
    thing = "Sample" if tool == "boltz2" else "Model"
    out = [
        {"key": "sample", "label": "%s shown in the figures" % thing, "type": "number",
         "default": 1, "least": 1, "most": 25,
         "help": "Which %s the figures and the chain-pair table are about; 1 is the best"
                 % thing.lower()},
        {"key": "samples", "label": "%ss in the table" % thing, "type": "number", "default": 25,
         "least": 1, "most": 25},
    ]
    if tool == "boltz2":
        out.append({"key": "prediction", "label": "Prediction", "type": "select", "default": "",
                    "choices": [],
                    "help": "A run folder written before HOPE Labs 0.8.3 can hold two "
                            "predictions under one record; this is which of them the report is "
                            "about"})
    out += [
        {"key": "pae", "label": "Include the aligned-error map", "type": "check", "default": True},
        {"key": "alignments", "label": "Include the alignments section", "type": "check",
         "default": True},
        {"key": "methods", "label": "Methods and references", "type": "check", "default": True},
        {"key": "appendix", "label": "Appendix", "type": "check", "default": True},
    ]
    return out


OPTIONS = {tool: _options(tool) for tool in SERVES}


# ----------------------------------------------------------------- reading a run

#: What a .npy header's dtype means, as a struct code. Boltz-2 writes float32; the rest are here
#: so that a file written another way is read rather than refused.
NPY_CODES = {"f2": "e", "f4": "f", "f8": "d", "i1": "b", "i2": "h", "i4": "i", "i8": "q",
             "u1": "B", "u2": "H", "u4": "I"}


def npz_array(path, key):
    """One array out of a .npz: a list of floats, or a list of rows of them.

    A .npz is a zip of .npy members, and a .npy is a short text header in front of raw numbers in
    the byte order the header names. Read here with the standard library because the page is one
    stdlib process and these two arrays - the per-token pLDDT and the aligned error - are the
    only things in a folding run that are not text. Anything this reader does not understand
    gives None, and the caption then says what is missing rather than showing a wrong picture.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            raw = archive.read(key if key.endswith(".npy") else key + ".npy")
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None
    if raw[:6] != b"\x93NUMPY" or len(raw) < 12:
        return None
    wide = 2 if raw[6] == 1 else 4
    size = int.from_bytes(raw[8:8 + wide], "little")
    head = raw[8 + wide:8 + wide + size].decode("latin-1")
    body = raw[8 + wide + size:]
    kind = re.search(r"'descr':\s*'([<>|=])(\w+)'", head)
    shape = re.search(r"'shape':\s*\(([^)]*)\)", head)
    if not kind or not shape or "'fortran_order': True" in head:
        return None
    code = NPY_CODES.get(kind.group(2))
    if not code:
        return None
    dims = [int(n) for n in re.findall(r"\d+", shape.group(1))] or [0]
    count = 1
    for one in dims:
        count *= one
    order = ">" if kind.group(1) == ">" else "<"
    want = count * struct.calcsize(code)
    if count <= 0 or len(body) < want:
        return None
    try:
        values = list(struct.unpack("%s%d%s" % (order, count, code), body[:want]))
    except struct.error:
        return None
    if len(dims) < 2:
        return values
    wide = dims[-1]
    return [values[at:at + wide] for at in range(0, count, wide)]


def nested_yaml(path, keep=()):
    """The top level of a YAML file and one level below it, which is all hparams.yaml needs.

    Boltz-2's hparams.yaml is what the model actually ran with, and the three blocks worth
    reading - predict_args, steering_args and the flags beside them - are plain keys and scalars
    two deep. Anything deeper is left out: a setting shown wrongly would be worse than one not
    shown, and the appendix says which file it came from.
    """
    out = {}
    try:
        with open(path, errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return {}
    block = None
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        deep = len(line) - len(line.lstrip())
        key, sep, value = line.strip().partition(":")
        if not sep or line.lstrip().startswith("-"):
            continue
        key, value = key.strip(), value.strip()
        if deep == 0:
            block = key if not value else None
            if value:
                out[key] = _scalar(value)
            elif not keep or key in keep:
                out[key] = {}
            continue
        if block and block in out and isinstance(out[block], dict) and value:
            out[block][key] = _scalar(value)
    return out


def _scalar(value):
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    if value.lower() in ("null", "none", "~", ""):
        return None
    got = rk.number(value)
    return got if got is not None and re.fullmatch(r"-?[\d.eE+]+", value) else value.strip("'\"")


def _q(value):
    return urllib.parse.quote(str(value or ""), safe="")


def _a(word):
    """"a protein", "an ion": the article a caption needs in front of a noun."""
    return ("an " if str(word)[:1].lower() in "aeiou" else "a ") + str(word)


def _said(value):
    """A setting as the appendix prints it: a whole number as a whole number.

    The YAML reader gives every number as a float, and "3.0 diffusion samples" is not what the
    tool was told.
    """
    if isinstance(value, bool) or value is None:
        return str(value)
    got = rk.number(value)
    if got is not None and float(got).is_integer() and not isinstance(value, str):
        return str(int(got))
    return str(value)


def _card_words(name):
    """The card as a sentence names it: "NVIDIA A100", not "NVIDIA A100-PCIE-40GB"."""
    return re.sub(r"-(PCIE|SXM\d*|NVL|PCIe)[-\d]*GB?", "", str(name or "")).strip()


#: What a ranking metric is called in prose. ColabFold writes the key it ranked by.
RANKED_BY = {"plddt": "mean pLDDT", "ptm": "pTM", "iptm": "ipTM",
             "multimer": "0.8 ipTM + 0.2 pTM"}


def _mean(values):
    good = [v for v in values if isinstance(v, (int, float))]
    return sum(good) / float(len(good)) if good else None


def _count_a3m(path):
    """The records in an A3M, query included, which is the alignment's depth."""
    found = 0
    try:
        with open(path, errors="replace") as handle:
            for line in handle:
                if line.startswith(">"):
                    found += 1
    except OSError:
        return None
    return found


def _count_csv(path):
    """The sequences in one of Boltz-2's alignment CSVs, the header not counted."""
    found = 0
    try:
        with open(path, errors="replace") as handle:
            for at, line in enumerate(handle):
                if at and line.strip():
                    found += 1
    except OSError:
        return None
    return found


class Report:
    """One folding run's report. Built from the run folder and nothing else."""

    def __init__(self, tool, rundir, raw=None, install=""):
        if tool not in SERVES:
            raise ValueError("this report is for %s, not %r" % (" and ".join(SERVES), tool))
        if not os.path.isdir(rundir or ""):
            raise ValueError("no such run folder")
        self.tool = tool
        self.run = os.path.abspath(rundir)
        self.install = install
        self._msa_rows = None
        self._pae = None
        self.sub = rk.read_json(os.path.join(self.run, "submission.json"), {}) or {}
        self.ents = rk.read_json(os.path.join(self.run, "entities.json"), {}) or {}
        self.rows = list(self.sub.get("rows") or [])
        self.site = sitesmod.SITES.get(str(self.sub.get("site") or "").lower())
        self.preds = self._predictions()
        named = self._named_prediction()
        limits, choices = {}, [[p["name"], p["name"]] for p in self.preds]
        declared = [dict(one) for one in OPTIONS[tool]]
        for one in declared:
            if one["key"] == "prediction":
                one["choices"] = choices
                one["default"] = named
        self.declared = declared
        chosen = (raw or {}).get("prediction")
        self.pred = self._pick(chosen) or self._pick(named) or (self.preds[0] if self.preds
                                                                else None)
        if self.pred:
            limits = {"sample": max(1, len(self.pred["samples"])),
                      "samples": max(1, len(self.pred["samples"]))}
        self.limits = limits
        self.opt = rk.options(declared, raw or {}, limits)
        self.samples = (self.pred or {}).get("samples") or []
        self.shown = self.samples[:self.opt["samples"]]
        self.at = min(max(1, self.opt["sample"]), len(self.samples)) if self.samples else 0
        self.sample = self.samples[self.at - 1] if self.at else {}
        self.chains = self._chains() if self.pred else []

    # -------------------------------------------------------------- the predictions

    def _predictions(self):
        """Every prediction in this run folder, each with its own samples and its own files.

        Grouped by the folder the tool wrote rather than ranked across folders, because a folder
        from before commit ad60faa can hold two predictions of two different complexes under one
        record, and a ranking across them would compare two things that were never compared.
        """
        out = []
        if self.tool == "boltz2":
            for folder in sorted(glob.glob(os.path.join(self.run, "out", "boltz_results_*"))):
                name = os.path.basename(folder)[len("boltz_results_"):]
                out.append(self._boltz_prediction(name, folder))
        else:
            for folder in sorted(glob.glob(os.path.join(self.run, "fold", "*"))):
                if not os.path.isdir(folder):
                    continue
                if not glob.glob(os.path.join(folder, "*_scores_rank_*.json")):
                    continue
                out.append(self._colabfold_prediction(os.path.basename(folder), folder))
        return [one for one in out if one]

    def _boltz_prediction(self, name, folder):
        where = os.path.join(folder, "predictions", name)
        samples = []
        for path in sorted(glob.glob(os.path.join(where, "confidence_%s_model_*.json" % name))):
            got = re.search(r"_model_(\d+)\.json$", os.path.basename(path))
            if not got:
                continue
            at = int(got.group(1))
            scores = rk.read_json(path, {}) or {}
            structure = ""
            for suffix in ("cif", "pdb"):
                guess = os.path.join(where, "%s_model_%d.%s" % (name, at, suffix))
                if os.path.isfile(guess):
                    structure = guess
                    break
            plddt = npz_array(os.path.join(where, "plddt_%s_model_%d.npz" % (name, at)), "plddt")
            samples.append({
                "rank": at + 1, "at": at, "file": os.path.basename(structure or path),
                "path": structure, "scores": scores,
                "confidence": scores.get("confidence_score"),
                "plddt": self._percent(scores.get("complex_plddt")),
                "iplddt": self._percent(scores.get("complex_iplddt")),
                "ptm": scores.get("ptm"),
                "iptm": self._iptm(scores),
                "protein_iptm": self._pair_number(scores.get("protein_iptm")),
                "ligand_iptm": self._pair_number(scores.get("ligand_iptm")),
                "pde": scores.get("complex_pde"),
                "ipde": self._pair_number(scores.get("complex_ipde")),
                "pairs": scores.get("pair_chains_iptm") or {},
                "chains_ptm": scores.get("chains_ptm") or {},
                "plddt_tokens": [v * 100.0 for v in plddt] if plddt else [],
                "pae_file": os.path.join(where, "pae_%s_model_%d.npz" % (name, at)),
            })
        if not samples:
            return None
        return {"name": name, "kind": "boltz2", "folder": where, "root": folder,
                "samples": sorted(samples, key=lambda one: one["rank"]),
                "record": rk.read_json(os.path.join(folder, "processed", "records",
                                                    "%s.json" % name), {}) or {},
                "hparams": nested_yaml(os.path.join(folder, "lightning_logs", "version_0",
                                                    "hparams.yaml")),
                "input": os.path.join(self.run, "inputs", "%s.yaml" % name)}

    def _colabfold_prediction(self, name, folder):
        samples = []
        for path in sorted(glob.glob(os.path.join(folder, "*_scores_rank_*.json"))):
            base = os.path.basename(path)
            got = re.search(r"_scores_rank_(\d+)_(.*)\.json$", base)
            if not got:
                continue
            tail = got.group(2)
            scores = rk.read_json(path, {}) or {}
            model = re.search(r"_model_(\d+)", tail)
            seed = re.search(r"_seed_(\d+)", tail)
            structure = os.path.join(folder, base.replace("_scores_rank_", "_unrelaxed_rank_")
                                     .replace(".json", ".pdb"))
            plddt = [float(v) for v in (scores.get("plddt") or [])
                     if isinstance(v, (int, float))]
            samples.append({
                "rank": int(got.group(1)), "at": int(got.group(1)) - 1,
                "file": os.path.basename(structure), "path": structure if
                os.path.isfile(structure) else "",
                "scores": {k: v for k, v in scores.items() if k not in ("pae", "plddt")},
                "model": int(model.group(1)) if model else None,
                "seed": int(seed.group(1)) if seed else None,
                "plddt": round(_mean(plddt), 1) if plddt else None,
                "ptm": scores.get("ptm"), "iptm": scores.get("iptm"),
                "max_pae": scores.get("max_pae"),
                "plddt_tokens": plddt,
                "pae": scores.get("pae") or [],
            })
        if not samples:
            return None
        return {"name": name, "kind": "colabfold", "folder": folder, "root": folder,
                "samples": sorted(samples, key=lambda one: one["rank"]),
                "record": {}, "hparams": {},
                "config": rk.read_json(os.path.join(folder, "config.json"), {}) or {},
                "input": os.path.join(self.run, "inputs", "%s.fasta" % name)}

    def _named_prediction(self):
        """The prediction submission.json describes, which is the one the report opens on."""
        said = str(self.sub.get("results") or "")
        for one in self.preds:
            if said and os.path.basename(said.rstrip("/")) == one["name"]:
                return one["name"]
        for one in self.preds:
            if one["name"] == str(self.sub.get("name") or ""):
                return one["name"]
        return self.preds[0]["name"] if self.preds else ""

    def _pick(self, name):
        for one in self.preds:
            if one["name"] == name:
                return one
        return None

    @staticmethod
    def _percent(value):
        """Boltz-2's pLDDT is a fraction of one; the page shows a percentage and so does this."""
        got = rk.number(value)
        return None if got is None else round(got * 100.0, 1)

    @staticmethod
    def _pair_number(value):
        """A number Boltz-2 writes as 0.0 when it has nothing to say.

        protein_iptm, ligand_iptm and complex_ipde are each exactly zero when the complex has no
        such interface at all, and a column of 0.000 reads as a terrible score rather than as no
        score. Zero is passed back as absent, and the table shows a dash.
        """
        got = rk.number(value)
        return None if got is None or got == 0.0 else got

    @staticmethod
    def _iptm(scores):
        """Boltz-2's ipTM, or None for a single chain.

        Boltz-2 writes iptm: 0.0 for a one-chain prediction, which has no interface to score.
        The same correction is in runs._boltz_scores(), so the Runs screen and the report agree.
        """
        got = rk.number(scores.get("iptm"))
        if got is None:
            return None
        if not got and len(scores.get("chains_ptm") or {}) < 2:
            return None
        return got

    # -------------------------------------------------------------- the chains

    def _chains(self):
        """One row per chain: what it is, how long, and how confident in the reported sample.

        The chain ids come from the record Boltz-2 wrote (which is how the keys "0", "1", "2" of
        chains_ptm become A, B, C) and from submission.json otherwise. The token counts come from
        submission.json rows[].residues, not from the record: the record gives a ligand one
        residue where Boltz-2 gives it one token per heavy atom, and the pLDDT array is counted
        the tool's way.
        """
        by_chain = {}
        for row in self.rows:
            for at, chain in enumerate(row.get("chains") or []):
                by_chain[str(chain)] = (row, at + 1, len(row.get("chains") or []))
        listed = []
        record = ((self.pred or {}).get("record") or {}).get("chains") or []
        if record:
            for one in record:
                listed.append((str(one.get("chain_name") or ""), one))
        else:
            for chain in sorted(by_chain):
                listed.append((chain, {}))
        out, start = [], 0
        for index, (chain, got) in enumerate(listed):
            row, copy, copies = by_chain.get(chain, ({}, 1, 1))
            kind = str(row.get("kind") or MOL_TYPE.get(got.get("mol_type"), "protein"))
            noun, unit = ENTITY.get(kind, (kind, "residue"))
            length = rk.whole(row.get("residues"))
            if length is None:
                length = rk.whole(got.get("num_residues"))
            tokens = length or 0
            mods = [str(m) for m in (row.get("modifications") or [])]
            out.append({
                "id": chain, "index": index, "kind": kind, "noun": noun, "unit": unit,
                "name": str(row.get("name") or row.get("ccd") or ""),
                "length": length, "copy": copy, "copies": copies,
                "mods": mods, "row": rk.whole(row.get("index")),
                "start": start, "tokens": tokens,
                "ptm": self._pair_number((self.sample.get("chains_ptm") or {}).get(str(index))),
                "plddt": self._chain_plddt(start, tokens),
            })
            start += tokens
        return out

    def _chain_plddt(self, start, tokens):
        """The mean pLDDT of one chain in the reported sample: the report's own number.

        Both tools write pLDDT per token and neither writes it per chain, so this is the one
        number in the chains table the run did not write. The page shows nothing to disagree with
        it, and the caption says whose it is.
        """
        values = self.sample.get("plddt_tokens") or []
        if not values or not tokens or start + tokens > len(values):
            return None
        return round(_mean(values[start:start + tokens]), 1)

    def chain_cuts(self):
        """Where one chain ends and the next begins, along the token axis."""
        return [one["start"] for one in self.chains if one["start"]]

    def groups(self):
        """The chains gathered by the row they came from, in chain order.

        A caption talks about entities, not chains: "two copies of a 95-residue protein" is one
        thing with two chains, and the DNA strand beside it is another.
        """
        out, seen = [], {}
        for chain in self.chains:
            key = chain["row"] if chain["row"] is not None else chain["id"]
            if key not in seen:
                seen[key] = {"kind": chain["kind"], "noun": chain["noun"],
                             "unit": chain["unit"], "name": chain["name"],
                             "length": chain["length"], "chains": []}
                out.append(seen[key])
            seen[key]["chains"].append(chain)
        return out

    def group_words(self, group, with_length=True, with_chains=True):
        """One entity as a caption names it: "two copies of a 95-residue protein", "the 20-base
        DNA strand C". The row's own name is left out here and used in the methods, where there
        is room for it."""
        ids = [c["id"] for c in group["chains"]]
        length = ("%s-%s " % (rk.whole(group["length"]), group["unit"])
                  if with_length and group["length"] else "")
        if len(ids) > 1:
            return "%s copies of a %s%s" % (rk.words(len(ids)), length, group["noun"])
        return "the %s%s%s" % (length, group["noun"], " %s" % ids[0] if with_chains else "")

    def plain_words(self, group):
        """An entity named without a length, for a caption that has given it already: "the
        protein chains", "the DNA strand".

        The chain id comes back when another entity of this run is the same kind of thing, so
        that two DNA strands in one complex are not both "the DNA strand".
        """
        if len(group["chains"]) > 1:
            return "the %s chains" % group["noun"]
        if [g["noun"] for g in self.groups()].count(group["noun"]) > 1:
            # Two entities of the same kind: the chain id is the only thing that tells them
            # apart, and "the DNA strand B and the DNA strand C" is a mouthful for "chain B and
            # chain C". What each chain is stands in {{table:chains}}.
            return "chain %s" % group["chains"][0]["id"]
        return "the %s" % group["noun"]

    # -------------------------------------------------------------- the document

    def build(self):
        version = folddocs.upstream_version(self.tool, self.install or "")
        from .. import __version__ as labs_version
        if not self.preds or not self.samples:
            return {"error": "this run has written no structure yet, so there is nothing to "
                             "report: %s" % self._where_it_got_to()}
        doc = {
            "kicker": KICKER,
            "title": self.sub.get("name") or (self.pred or {}).get("name")
                     or os.path.basename(self.run.rstrip(os.sep)),
            "subtitle": self.subtitle(version),
            "byline": rk.byline("HOPE Labs", labs_version,
                                submitted=str(self.sub.get("submitted") or ""),
                                who=str(self.sub.get("user") or "")),
            "path": self.run,
            "options": rk.echo(self.declared, self.opt, self.limits, drop=self._drop_options()),
            "notes": self.notes(),
            "sections": [s for s in (self.summary(), self.folded(), self.samples_section(),
                                     self.interfaces(), self.residues(), self.structure(),
                                     self.alignments()) if s],
            "views_hint": "None yet. This page has no viewer of its own on the report's side: "
                          "the structure figure above is drawn by the report itself from the "
                          "run's own file, and the Runs screen's viewer has no Add to report "
                          "button yet.",
            "appendix": self.appendix() if self.opt["appendix"] else [],
        }
        doc["methods"], doc["references"] = (self.methods(version) if self.opt["methods"]
                                             else ("", []))
        return doc

    def _drop_options(self):
        """An option that cannot change anything is left off the bar rather than offered."""
        out = []
        if len(self.samples) < 2:
            out += ["sample", "samples"]
        if len(self.preds) < 2:
            out.append("prediction")
        if not self._pae_rows() and not self._pae_picture():
            out.append("pae")
        return out

    def _where_it_got_to(self):
        log = self._log()
        if not log:
            return "the job has written no log in the run folder either"
        return "the last thing %s says is %r" % (os.path.basename(log),
                                                 rk.tail(log, 1).strip()[:160] or "nothing")

    def subtitle(self, version):
        """What was folded, with what, in one line: the title is only the run's name."""
        groups = self.groups()
        what = (rk.and_list([self.group_words(g) for g in groups]) if len(groups) <= 3
                else rk.plural(len(self.chains), "chain"))
        return "%s, folded with %s %s into %s" % (
            rk.cap(what or "one chain"), rk.e(TOOL_NAME[self.tool]), rk.e(version),
            rk.count(len(self.samples), "sample" if self.tool == "boltz2" else "model"))

    def notes(self):
        """What has to be read before anything else."""
        out = []
        if len(self.preds) > 1:
            out.append({"kind": "p", "tone": "warn", "html":
                        "This run folder holds %s under one record: %s. They are different "
                        "complexes folded by two jobs into one folder, which a run queued today "
                        "cannot do. This report is about <b>%s</b>; the others are compared with "
                        "it in {{table:predictions}} and nothing else here mixes them."
                        % (rk.plural(len(self.preds), "prediction"),
                           rk.and_list([rk.e(p["name"]) for p in self.preds]),
                           rk.e((self.pred or {}).get("name")))})
        if str(self.sub.get("alignments") or "").startswith("single"):
            out.append({"kind": "p", "tone": "warn", "html":
                        "No alignment was fetched: every chain was folded from its sequence "
                        "alone. Both tools are markedly worse that way, and the confidence "
                        "numbers below are the model's own view of a fold it had no evolutionary "
                        "evidence for."})
        if self.sub.get("dry_run"):
            out.append({"kind": "p", "tone": "warn",
                        "html": "This run was queued as a dry run, so no job was submitted."})
        if not self.sub:
            out.append({"kind": "p", "tone": "caveat", "html":
                        "This run folder holds no <code>submission.json</code>, so nothing says "
                        "what was asked for or who asked: the chains, the lengths and the "
                        "settings below are read from what the tool itself wrote."})
        state = self._state()[0]
        if state and state not in ("COMPLETED", "DRY RUN"):
            out.append({"kind": "p", "tone": "warn", "html":
                        "The job ended %s, so this run may be a part of what was asked for. "
                        "What is reported below is what is on disk: %s."
                        % (rk.e(state.lower()),
                           rk.plural(len(self.samples), "sample"))})
        return out

    def _state(self):
        job = str(self.sub.get("job_id") or "")
        if not job:
            return ("DRY RUN" if self.sub.get("dry_run") else ""), ""
        return runsmod.finished_state(job)

    # -------------------------------------------------------------- 1. summary

    def summary(self):
        return {"id": "summary", "heading": "Summary", "label": "summary",
                "blocks": [{"kind": "p", "html": self.summary_words()},
                           rk.facts(self.summary_facts())]}

    def summary_words(self):
        groups = self.groups()
        best = self.samples[0] if self.samples else {}
        bits = ["%s folded %s into %s" % (
            rk.e(TOOL_NAME[self.tool]),
            (rk.and_list([self.group_words(g) for g in groups]) if len(groups) <= 3
             else "%s of %s" % (rk.count(len(self.chains), "chain"),
                                rk.count(len(groups), "kind"))) or "one chain",
            rk.count(len(self.samples), "sample" if self.tool == "boltz2" else "model"))]
        if self.tool == "boltz2":
            bits.append("the best, %s, has confidence score %s, pLDDT %s and %s"
                        % (self._sample_name(best), rk.num(best.get("confidence"), 3),
                           rk.num(best.get("plddt"), 1),
                           ("ipTM %s" % rk.num(best.get("iptm"), 3))
                           if best.get("iptm") is not None else
                           "pTM %s, with no ipTM because there is one chain"
                           % rk.num(best.get("ptm"), 3)))
        else:
            bits.append("the best, %s, has mean pLDDT %s and pTM %s%s"
                        % (self._sample_name(best), rk.num(best.get("plddt"), 1),
                           rk.num(best.get("ptm"), 2),
                           ", ipTM %s" % rk.num(best.get("iptm"), 2)
                           if best.get("iptm") is not None else ""))
        worst = [c for c in self.chains if c.get("plddt") is not None]
        if len(worst) > 1:
            low = min(worst, key=lambda c: c["plddt"])
            bits.append("the least confident chain is %s at mean pLDDT %s"
                        % (rk.e(low["id"]), rk.num(low["plddt"], 1)))
        said = str(self.sub.get("alignments") or "")
        if said:
            bits.append("the alignments were %s" % rk.e(said))
        return rk.sentences(bits)

    def _sample_name(self, sample):
        if self.tool == "boltz2":
            return "sample %s" % rk.whole(sample.get("rank"))
        return "model %s" % (rk.whole(sample.get("model"))
                             if sample.get("model") else rk.whole(sample.get("rank")))

    def summary_facts(self):
        rows = [("Tool", rk.e("%s %s" % (TOOL_NAME[self.tool],
                                         folddocs.upstream_version(self.tool, self.install or ""))
                              ) + " <i>(read from the install as this report was opened: no run "
                                  "file records it)</i>")]
        if len(self.preds) > 1:
            rows.append(("Prediction reported",
                         rk.e("%s, of the %s in this folder"
                              % ((self.pred or {}).get("name"),
                                 rk.plural(len(self.preds), "prediction")))))
        rows.append(("Chains", rk.e(", ".join(c["id"] for c in self.chains) or "not recorded")))
        tokens = sum(c["tokens"] for c in self.chains)
        if tokens:
            rows.append(("Tokens folded", rk.e("{:,}".format(tokens))))
        rows.append((("Samples written" if self.tool == "boltz2" else "Models written"),
                     rk.e(str(len(self.samples)))))
        rows.append(("Alignments", rk.e(str(self.sub.get("alignments") or "not recorded"))))
        job = str(self.sub.get("job_id") or "")
        if job:
            state, elapsed = self._state()
            rows.append(("Job", rk.e("%s%s" % (job, ", %s in %s" % (state, elapsed)
                                               if state else ""))))
        if self.site:
            rows.append(("Cluster", rk.e(self.site.name)))
        return rows

    # -------------------------------------------------------------- 2. what was folded

    def folded(self):
        return {"id": "folded", "heading": "What was folded", "label": "chains",
                "blocks": [self.chains_table()]}

    def chains_table(self):
        columns = [{"key": "chain", "label": "Chain"},
                   {"key": "entity", "label": "Entity"},
                   {"key": "length", "label": "Length"},
                   {"key": "copy", "label": "Copy"},
                   {"key": "mods", "label": "Modifications", "empty": "—"},
                   {"key": "msa", "label": "Alignment"}]
        if self.tool == "boltz2":
            columns.append({"key": "ptm", "label": "Chain pTM", "num": True, "digits": 3,
                            "empty": "—",
                            "help": "Boltz-2's pTM for this chain alone in the reported sample, "
                                    "0 to 1, higher is better; a single-atom chain has none"})
        columns.append({"key": "plddt", "label": "Mean pLDDT", "num": True, "digits": 1,
                        "empty": "—",
                        "help": "The mean of the reported sample's per-token pLDDT over this "
                                "chain, out of 100: the report's own number, not one the run "
                                "wrote"})
        rows = []
        means = [c["plddt"] for c in self.chains if c["plddt"] is not None]
        #: The row the caption is about: the least confident chain, when there is more than one
        #: and the means could be read at all.
        lowest = min(means) if means and len(self.chains) > 1 else None
        for chain in self.chains:
            named = ", ".join(x for x in (chain["kind"].upper() if chain["kind"] in ("dna", "rna")
                                          else chain["noun"], chain["name"]) if x)
            rows.append({"chain": chain["id"], "entity": named,
                         "length": "%s %s" % (rk.whole(chain["length"]),
                                              chain["unit"] + ("s" if chain["length"] != 1
                                                               else ""))
                                   if chain["length"] else "",
                         "copy": "%s of %s" % (chain["copy"], chain["copies"])
                                 if chain["copies"] > 1 else "1 of 1",
                         "mods": ", ".join(self._mod_words(m) for m in chain["mods"]),
                         "msa": self._msa_words(chain),
                         "ptm": chain["ptm"], "plddt": chain["plddt"],
                         "_class": "rk-mark" if lowest is not None
                                   and chain["plddt"] == lowest else ""})
        return {"kind": "table", "id": "chains", "columns": columns, "rows": rows,
                "caption": self.chains_caption(),
                "note": "The pLDDT means are the report's own, taken from the sample's "
                        "per-token array over each chain's own tokens; every other number here "
                        "is one the run wrote."}

    @staticmethod
    def _mod_words(mod):
        """"S20 SEP" as a caption says it: the code, at the position."""
        got = re.match(r"([A-Za-z]?)(\d+)\s+(\S+)", str(mod).strip())
        return "%s at %s" % (got.group(3), got.group(2)) if got else str(mod)

    def _msa_words(self, chain):
        if chain["kind"] not in ("protein",):
            return "none needed"
        depth = self._depth_for(chain)
        if depth:
            return rk.plural(depth, "sequence")
        said = str(self.sub.get("alignments") or "")
        if said.startswith("single"):
            return "single sequence"
        return said or "not recorded"

    def _depth_for(self, chain):
        """How deep the alignment that served this chain is, counted off the file."""
        for name, depth, chains, _how, _file in self._alignment_rows():
            if chain["id"] in chains:
                return depth
        return None

    def chains_caption(self):
        groups = self.groups()
        if not groups:
            return rk.missing("this run records no chains at all",
                              "submission.json rows[] and the tool's own record are both absent")
        bits = ([self._few_chains(groups)] if len(groups) <= 3
                else [self._many_chains(groups)])
        ions = [c for c in self.chains if c["ptm"] is None and c["tokens"] == 1]
        if ions and self.tool == "boltz2":
            bits.append("%s %s no pTM at all - Boltz-2 writes 0.0 for a single-atom chain - and "
                        "%s a dash"
                        % (rk.cap(rk.and_list(["chain %s" % rk.e(c["id"]) for c in ions])),
                           "carry" if len(ions) > 1 else "carries",
                           "show" if len(ions) > 1 else "shows"))
        mods = [c for c in self.chains if c["mods"]]
        if mods:
            bits.append("%s %s modified"
                        % (rk.cap(rk.and_list(["%s of chain %s"
                                               % (rk.count(len(c["mods"]), "residue"),
                                                  rk.e(c["id"])) for c in mods])),
                           "are" if sum(len(c["mods"]) for c in mods) > 1 else "is"))
        if self.tool == "boltz2":
            bits.append("higher is better for both, pTM from 0 to 1 and pLDDT out of 100")
        else:
            bits.append("higher is better, out of 100; ColabFold writes no per-chain pTM, so "
                        "that column is not here")
        return rk.sentences(bits)

    def _few_chains(self, groups):
        """The chains of a complex of two or three entities, named one by one."""
        first = groups[0]
        if self.tool == "boltz2":
            said = "%s %s with chain pTM %s" % (self._group_subject(first, True),
                                                "fold" if len(first["chains"]) > 1 else "folds",
                                                self._group_ptm(first))
            said += "".join(" and %s with %s" % (self._group_subject(g, False),
                                                 self._group_ptm(g)) for g in groups[1:])
        else:
            said = "%s has mean pLDDT %s in %s" % (self._group_subject(first, True),
                                                   self._group_plddt(first),
                                                   self._sample_name(self.sample))
            said += "".join(" and %s %s" % (self._group_subject(g, False),
                                            self._group_plddt(g)) for g in groups[1:])
        low = self._lowest_group(groups)
        # Only where it says something the clause before it did not: with two entities of one
        # chain each, their two means have just been given and naming the lower one again is
        # noise. With a pair of chains in one entity it is the point of the caption.
        tell = low is not None and (len(groups) > 2
                                   or any(len(g["chains"]) > 1 for g in groups))
        if tell:
            rest = [g for g in groups if g is not low]
            said += (", though %s's mean pLDDT is lower, %s against %s"
                     % (self.plain_words(low), self._group_plddt(low),
                        rk.and_list([self._group_plddt(g) for g in rest])))
        elif len(groups) == 1 and self.tool == "boltz2" and first["chains"][0]["plddt"]:
            said += ", and its mean pLDDT is %s" % self._group_plddt(first)
        return said

    def _many_chains(self, groups):
        """A complex of more than three entities, where naming each one is unreadable.

        The two ends of each measure, and how many chains there are: a reader who wants the
        middle has the table under the caption.
        """
        ptms = [c for c in self.chains if c["ptm"] is not None]
        means = [c for c in self.chains if c["plddt"] is not None]
        bits = ["Of the %s of %s" % (rk.count(len(self.chains), "chain"),
                                     rk.count(len(groups), "kind"))]
        if ptms and self.tool == "boltz2":
            best, worst = max(ptms, key=lambda c: c["ptm"]), min(ptms, key=lambda c: c["ptm"])
            bits.append("chain %s has the best pTM, %s, and chain %s the weakest, %s"
                        % (rk.e(best["id"]), rk.num(best["ptm"], 3),
                           rk.e(worst["id"]), rk.num(worst["ptm"], 3)))
        if means:
            best = max(means, key=lambda c: c["plddt"])
            worst = min(means, key=lambda c: c["plddt"])
            bits.append("while the mean pLDDT runs from %s in chain %s down to %s in the %s %s"
                        % (rk.num(best["plddt"], 1), rk.e(best["id"]),
                           rk.num(worst["plddt"], 1), worst["noun"], rk.e(worst["id"])))
        return ", ".join(bits)

    def _group_subject(self, group, first):
        """The entity as the subject of a clause, capitalised when it opens the caption."""
        ids = [c["id"] for c in group["chains"]]
        if len(ids) > 1:
            body = "chains %s and %s, %s" % (rk.e(ids[0]), rk.e(ids[-1]),
                                             self.group_words(group, with_chains=False))
        else:
            body = self.group_words(group, with_chains=True)
        return rk.cap(body) if first else body

    def _group_ptm(self, group):
        values = [c["ptm"] for c in group["chains"] if c["ptm"] is not None]
        if not values:
            return "no pTM of its own"
        shown = sorted({rk.num(v, 3) for v in values})
        if len(shown) == 1:
            return shown[0] + (" each" if len(values) > 1 else "")
        return rk.and_list(shown)

    def _group_plddt(self, group):
        values = [c["plddt"] for c in group["chains"] if c["plddt"] is not None]
        if not values:
            return "no pLDDT the report could read"
        return rk.and_list([rk.num(v, 1) for v in values])

    @staticmethod
    def _lowest_group(groups):
        best = None
        for group in groups:
            values = [c["plddt"] for c in group["chains"] if c["plddt"] is not None]
            if not values:
                continue
            mean = _mean(values)
            if best is None or mean < best[0]:
                best = (mean, group)
        if best is None:
            return None
        others = [g for g in groups if g is not best[1]
                  and any(c["plddt"] is not None for c in g["chains"])]
        return best[1] if others else None

    # -------------------------------------------------------------- 3. the samples

    def samples_section(self):
        blocks = [self.samples_table()]
        if len(self.preds) > 1:
            blocks.append(self.predictions_table())
        return {"id": "samples",
                "heading": "Samples and confidence" if self.tool == "boltz2"
                           else "Models and confidence",
                "label": "samples", "blocks": blocks}

    def samples_table(self):
        if self.tool == "boltz2":
            return self._boltz_samples_table()
        return self._colabfold_samples_table()

    def _boltz_samples_table(self):
        chemical = any(c["kind"] in ("ligand", "ccd", "smiles", "ion") for c in self.chains)
        proteins = len([c for c in self.chains if c["kind"] == "protein"])
        columns = [{"key": "rank", "label": "Sample", "num": True, "digits": 0},
                   {"key": "file", "label": "File"},
                   {"key": "confidence", "label": "Confidence", "num": True, "digits": 3,
                    "help": "Boltz-2's own ranking: 0.8 x complex pLDDT + 0.2 x ipTM, or pTM "
                            "for one chain"},
                   {"key": "plddt", "label": "pLDDT", "num": True, "digits": 1,
                    "help": "The complex pLDDT out of 100, higher is better"},
                   {"key": "iplddt", "label": "Interface pLDDT", "num": True, "digits": 1,
                    "empty": "—"},
                   {"key": "ptm", "label": "pTM", "num": True, "digits": 3},
                   {"key": "iptm", "label": "ipTM", "num": True, "digits": 3, "empty": "—",
                    "help": "A single chain has no interface, so Boltz-2's 0.0 is shown as a "
                            "dash rather than as a score"}]
        if proteins > 1:
            columns.append({"key": "protein_iptm", "label": "Protein ipTM", "num": True,
                            "digits": 3, "empty": "—"})
        if chemical:
            columns.append({"key": "ligand_iptm", "label": "Ligand ipTM", "num": True,
                            "digits": 3, "empty": "—"})
        columns += [{"key": "pde", "label": "PDE (Å)", "num": True, "digits": 2,
                     "help": "The predicted distance error, lower is better"},
                    {"key": "ipde", "label": "Interface PDE (Å)", "num": True, "digits": 2,
                     "empty": "—"}]
        rows = []
        for one in self.shown:
            rows.append({"rank": one["rank"], "file": one["file"],
                         "confidence": one.get("confidence"), "plddt": one.get("plddt"),
                         "iplddt": one.get("iplddt"), "ptm": one.get("ptm"),
                         "iptm": one.get("iptm"),
                         "protein_iptm": one.get("protein_iptm"),
                         "ligand_iptm": one.get("ligand_iptm"),
                         "pde": one.get("pde"), "ipde": one.get("ipde"),
                         "_class": "rk-mark" if one["rank"] == self.at else ""})
        return {"kind": "table", "id": "samples", "columns": columns, "rows": rows,
                "caption": self._boltz_samples_caption(),
                "note": self._samples_note()}

    def _samples_note(self):
        bits = []
        if len(self.shown) < len(self.samples):
            bits.append("%s of %s shown" % (len(self.shown), len(self.samples)))
        if self.at:
            bits.append("the figures below are %s" % self._sample_name(self.sample))
        return "; ".join(rk.cap(b) for b in bits) + "." if bits else ""

    def _boltz_samples_caption(self):
        if not self.samples:
            return rk.missing("no sample has a confidence file yet")
        best = self.samples[0]
        bits = []
        if len(self.samples) == 1:
            said = ("The one sample scores %s for confidence, with pLDDT %s and pTM %s"
                    % (rk.num(best.get("confidence"), 3), rk.num(best.get("plddt"), 1),
                       rk.num(best.get("ptm"), 3)))
            said += "; with one sample there is nothing to compare it with"
            if best.get("iptm") is None:
                said += ", and with one chain there is no ipTM"
            bits.append(said)
            if str(self.sub.get("alignments") or "").startswith("single"):
                bits.append("It was folded from the sequence alone")
            return rk.sentences(bits)
        rest = self.samples[1:]
        bits.append("Sample %s has the best confidence score, %s, ahead of %s %s"
                    % (rk.whole(best["rank"]), rk.num(best.get("confidence"), 3),
                       "samples" if len(rest) > 1 else "sample",
                       rk.and_list(["%s (%s)" % (rk.whole(one["rank"]),
                                                 rk.num(one.get("confidence"), 3))
                                    for one in rest])))
        tail = ""
        iptms = [one for one in self.samples if one.get("iptm") is not None]
        if iptms:
            top = max(iptms, key=lambda one: one["iptm"])
            if top["rank"] != best["rank"] and best.get("iptm") is not None:
                tail = ("though sample %s has the highest ipTM, %s against %s"
                        % (rk.whole(top["rank"]), rk.num(top["iptm"], 3),
                           rk.num(best["iptm"], 3)))
        ipdes = [one["ipde"] for one in self.samples if one.get("ipde") is not None]
        if ipdes:
            import math
            cut = math.ceil(max(ipdes) * 100.0) / 100.0
            said = "%s put the interface PDE below %s Å" % (
                "all %s" % rk.words(len(self.samples)) if len(self.samples) > 2 else "both",
                rk.num(cut, 2))
            tail = (tail + "; " + said) if tail else rk.cap(said)
        if tail:
            bits.append(tail)
        bits.append("higher is better for every column except the two PDEs, where lower is "
                    "better")
        return rk.sentences(bits)

    def _colabfold_samples_table(self):
        columns = [{"key": "rank", "label": "Rank", "num": True, "digits": 0},
                   {"key": "model", "label": "AlphaFold model", "num": True, "digits": 0,
                    "empty": "—",
                    "help": "Which of AlphaFold's five parameter sets this model came from"},
                   {"key": "seed", "label": "Seed", "num": True, "digits": 0, "empty": "—"},
                   {"key": "plddt", "label": "Mean pLDDT", "num": True, "digits": 1,
                    "help": "The mean of the per-residue pLDDT ColabFold wrote, out of 100"},
                   {"key": "ptm", "label": "pTM", "num": True, "digits": 2}]
        if any(one.get("iptm") is not None for one in self.samples):
            columns.append({"key": "iptm", "label": "ipTM", "num": True, "digits": 2,
                            "empty": "—"})
        columns.append({"key": "max_pae", "label": "Maximum PAE (Å)", "num": True, "digits": 1,
                        "empty": "—", "help": "Lower is better"})
        rows = [{"rank": one["rank"], "model": one.get("model"), "seed": one.get("seed"),
                 "plddt": one.get("plddt"), "ptm": one.get("ptm"), "iptm": one.get("iptm"),
                 "max_pae": one.get("max_pae"),
                 "_class": "rk-mark" if one["rank"] == self.at else ""}
                for one in self.shown]
        return {"kind": "table", "id": "samples", "columns": columns, "rows": rows,
                "caption": self._colabfold_samples_caption(), "note": self._samples_note()}

    def _colabfold_samples_caption(self):
        if not self.samples:
            return rk.missing("no model has a scores file yet")
        config = (self.pred or {}).get("config") or {}
        by = str(config.get("rank_by") or "")
        best = self.samples[0]
        measure = RANKED_BY.get(by, "mean pLDDT")
        #: Which number ColabFold ranked by, where it wrote that number at all. It writes the
        #: mean pLDDT, the pTM and the ipTM, but not the combined multimer score, so a run
        #: ranked by that is reported on the two numbers it does hold rather than on a score
        #: this report would have had to work out for itself.
        key, digits = {"plddt": ("plddt", 1), "": ("plddt", 1), "ptm": ("ptm", 2),
                       "iptm": ("iptm", 2)}.get(by, (None, None))
        bits = []
        if len(self.samples) == 1:
            bits.append("The one model has mean pLDDT %s and pTM %s, with nothing to compare it "
                        "with" % (rk.num(best.get("plddt"), 1), rk.num(best.get("ptm"), 2)))
        elif key and rk.number(best.get(key)) is not None:
            rest = self.samples[1:]
            bits.append("Model %s ranks first on %s, %s, ahead of %s"
                        % (rk.whole(best.get("model") or best["rank"]), measure,
                           rk.num(best.get(key), digits),
                           rk.and_list(["model %s (%s)"
                                        % (rk.whole(one.get("model") or one["rank"]),
                                           rk.num(one.get(key), digits)) for one in rest])))
            ptms = [one for one in self.samples if rk.number(one.get("ptm")) is not None]
            tail = ""
            if ptms and key != "ptm":
                top = max(ptms, key=lambda one: one["ptm"])
                if top["rank"] != best["rank"]:
                    tail = ("though model %s has the higher pTM, %s against %s"
                            % (rk.whole(top.get("model") or top["rank"]),
                               rk.num(top["ptm"], 2), rk.num(best.get("ptm"), 2)))
            said = ("ColabFold ranked by %s because the run is one chain" % measure
                    if len([c for c in self.chains if c["kind"] == "protein"]) < 2
                    else "ColabFold ranked by %s" % measure)
            tail = (tail + "; " + said) if tail else rk.cap(said)
            bits.append(tail)
        else:
            rest = self.samples[1:]
            bits.append("Model %s ranks first, with ipTM %s and mean pLDDT %s, ahead of %s"
                        % (rk.whole(best.get("model") or best["rank"]),
                           rk.num(best.get("iptm"), 2), rk.num(best.get("plddt"), 1),
                           rk.and_list(["model %s (%s and %s)"
                                        % (rk.whole(one.get("model") or one["rank"]),
                                           rk.num(one.get("iptm"), 2),
                                           rk.num(one.get("plddt"), 1)) for one in rest])))
            bits.append("ColabFold ranked them by %s, which it does not write out" % measure)
        bits.append("higher is better; pLDDT out of 100, pTM and ipTM from 0 to 1")
        return rk.sentences(bits)

    # T4: the predictions of a folder that holds more than one

    def predictions_table(self):
        columns = [{"key": "name", "label": "Prediction"},
                   {"key": "samples", "label": "Samples", "num": True, "digits": 0},
                   {"key": "confidence", "label": "Confidence", "num": True, "digits": 3},
                   {"key": "plddt", "label": "pLDDT", "num": True, "digits": 1},
                   {"key": "iptm", "label": "ipTM", "num": True, "digits": 3, "empty": "—"},
                   {"key": "pair", "label": "Partner pair ipTM", "num": True, "digits": 3,
                    "empty": "—",
                    "help": "The first chain's pair ipTM to the last in that prediction's best "
                            "sample, which for these runs is the target's row against the "
                            "partner's column. The matrix is not symmetric: the other direction "
                            "is in the chain-pair table"},
                   {"key": "spread", "label": "ipTM across samples", "empty": "—"}]
        rows = []
        for pred in self.preds:
            best = pred["samples"][0] if pred["samples"] else {}
            iptms = [one.get("iptm") for one in pred["samples"] if one.get("iptm") is not None]
            rows.append({"name": pred["name"], "samples": len(pred["samples"]),
                         "confidence": best.get("confidence"), "plddt": best.get("plddt"),
                         "iptm": best.get("iptm"), "pair": self._partner_pair(pred, best),
                         "spread": "%s to %s" % (rk.num(min(iptms), 3), rk.num(max(iptms), 3))
                                   if len(iptms) > 1 else "",
                         "_class": "rk-mark" if pred is self.pred else ""})
        return {"kind": "table", "id": "predictions", "columns": columns, "rows": rows,
                "caption": self.predictions_caption(),
                "note": "<code>submission.json</code> describes %s only; the others' settings "
                        "are read from their own "
                        "<code>lightning_logs/version_0/hparams.yaml</code>, and their inputs "
                        "from <code>inputs/</code>."
                        % rk.e(self._named_prediction())}

    @staticmethod
    def _partner_pair(pred, sample):
        """The first chain's pair ipTM to the last: the target's row against the partner's
        column, which for these runs is the target holding the partner.

        One direction of an asymmetric matrix, so the caption and the column help both say
        which: the other direction is in {{table:pairs}} for the prediction being reported.
        """
        pairs = sample.get("pairs") or {}
        keys = sorted(pairs, key=lambda k: rk.whole(k) if rk.whole(k) is not None else 0)
        if len(keys) < 2:
            return None
        return rk.number((pairs.get(keys[0]) or {}).get(keys[-1]))

    def predictions_caption(self):
        with_iptm = [p for p in self.preds if p["samples"]
                     and p["samples"][0].get("iptm") is not None]
        if len(with_iptm) < 2:
            return rk.sentences([
                "This folder holds %s, %s"
                % (rk.count(len(self.preds), "prediction"),
                   rk.and_list(["%s with %s"
                                % (rk.e(p["name"]), rk.plural(len(p["samples"]), "sample"))
                                for p in self.preds])),
                "they are different complexes, so nothing here ranks one against another"])
        best = max(with_iptm, key=lambda p: p["samples"][0]["iptm"])
        other = [p for p in with_iptm if p is not best]
        bits = ["%s has the better ipTM, %s against %s"
                % (rk.e(best["name"]), rk.num(best["samples"][0]["iptm"], 3),
                   rk.and_list(["%s for %s" % (rk.num(p["samples"][0]["iptm"], 3),
                                               rk.e(p["name"])) for p in other]))]
        mine = [one["iptm"] for one in best["samples"] if one.get("iptm") is not None]
        if len(mine) > 1 and other:
            them = [one["iptm"] for one in other[0]["samples"] if one.get("iptm") is not None]
            if len(them) > 1 and (max(them) - min(them)) > (max(mine) - min(mine)):
                said = ("and its %s samples agree (%s to %s) where %s's spread from %s to %s"
                        % (rk.words(len(mine)), rk.num(min(mine), 3), rk.num(max(mine), 3),
                           rk.e(other[0]["name"]), rk.num(min(them), 3), rk.num(max(them), 3)))
                weak = [(one["rank"], self._partner_pair(other[0], one))
                        for one in other[0]["samples"]]
                weak = [(at, v) for at, v in weak if v is not None and v < 0.5]
                if weak and len(self.chains) > 1:
                    said += (", chain %s's pair ipTM to its %s falling to %s in %s %s"
                             % (rk.e(self.chains[0]["id"]), self.chains[-1]["noun"],
                                rk.and_list([rk.num(v, 3) for _at, v in weak]),
                                "samples" if len(weak) > 1 else "sample",
                                rk.and_list([str(rk.whole(at)) for at, _v in weak])))
                bits.append(said)
        tops = [p for p in self.preds if p["samples"]
                and rk.number(p["samples"][0].get("confidence")) is not None]
        if tops:
            byconf = max(tops, key=lambda p: p["samples"][0]["confidence"])
            if byconf is not best:
                said = ("%s's best sample still has the higher confidence score, %s against %s"
                        % (rk.e(byconf["name"]), rk.num(byconf["samples"][0]["confidence"], 3),
                           rk.num(best["samples"][0]["confidence"], 3)))
                if (rk.number(byconf["samples"][0].get("plddt")) or 0) > \
                        (rk.number(best["samples"][0].get("plddt")) or 0):
                    said += ", from its higher pLDDT"
                bits.append(said)
        counts = {len(p["samples"]) for p in self.preds}
        bits.append("higher is better; best sample of each, %s"
                    % ("%s each" % rk.count(list(counts)[0], "sample") if len(counts) == 1
                       else rk.and_list(["%s for %s"
                                         % (rk.plural(len(p["samples"]), "sample"),
                                            rk.e(p["name"])) for p in self.preds])))
        return rk.sentences(bits)

    # -------------------------------------------------------------- 4. chains and interfaces

    def interfaces(self):
        blocks = []
        matrix = self.pairs_table()
        if matrix:
            blocks.append(matrix)
        blocks.append({"kind": "p", "tone": "caveat", "html": self._contacts_words()})
        if not blocks:
            return None
        return {"id": "interfaces", "heading": "Chains and interfaces", "label": "interfaces",
                "blocks": blocks}

    def _contacts_words(self):
        if len(self.chains) < 2:
            return ("This is one chain, so there is no interface to report: the ipTM is absent "
                    "and the interface pLDDT and interface PDE are the whole chain's. What a "
                    "run of one chain can be read for is the confidence along it, below.")
        if self.tool == "colabfold":
            return ("No chain-pair table is shown because ColabFold writes none: it reports one "
                    "pTM and, for a complex, one ipTM, and nothing per chain or per pair. "
                    "Interface residues are not listed either: nothing in this page computes "
                    "contacts, so a list here would be the report's own work rather than the "
                    "run's.")
        return ("Interface residues are not listed: nothing in the folding page computes "
                "contacts, so the only interface evidence this run holds is the ipTM, interface "
                "pLDDT and interface PDE above. A list of residues within 4.5 Å would have to be "
                "computed from the structure by both the page and the report, or the two would "
                "disagree.")

    def pairs_table(self):
        if self.tool != "boltz2" or len(self.chains) < 2:
            return None
        pairs = self.sample.get("pairs") or {}
        if not pairs:
            return None
        ids = [c["id"] for c in self.chains]
        columns = [{"key": "chain", "label": "Chain"}]
        for chain in self.chains:
            columns.append({"key": "c%d" % chain["index"], "label": chain["id"], "num": True,
                            "digits": 3, "empty": "—"})
        rows = []
        for chain in self.chains:
            row = {"chain": chain["id"]}
            line = pairs.get(str(chain["index"])) or {}
            for other in self.chains:
                row["c%d" % other["index"]] = self._pair_number(
                    line.get(str(other["index"])))
            rows.append(row)
        return {"kind": "table", "id": "pairs", "columns": columns, "rows": rows,
                "caption": self.pairs_caption(),
                "note": "From %s, and not symmetric: it is shown as Boltz-2 wrote it. Chain ids "
                        "are the record's (<code>processed/records/%s.json</code>), which is how "
                        "the keys 0, 1, 2 become %s."
                        % (rk.e("confidence_%s_model_%s.json"
                                % ((self.pred or {}).get("name"), self.sample.get("at"))),
                           rk.e((self.pred or {}).get("name")), rk.e(", ".join(ids)))}

    def pairs_caption(self):
        pairs = self.sample.get("pairs") or {}
        cells = []
        for a in self.chains:
            for b in self.chains:
                if a is b:
                    continue
                got = self._pair_number((pairs.get(str(a["index"])) or {}).get(str(b["index"])))
                if got is not None:
                    cells.append((got, a, b))
        if not cells:
            return rk.missing("this sample's confidence file holds no chain-pair ipTM",
                              "pair_chains_iptm is the key it would be under")
        top = max(cells, key=lambda one: one[0])
        _value, first, second = top
        same_row = first["row"] is not None and first["row"] == second["row"]
        bits = []
        if same_row:
            bits.append("The %s %s chains pair with ipTM %s"
                        % (rk.words(len([c for c in self.chains if c["row"] == first["row"]])),
                           first["noun"], rk.num(top[0], 3)))
        else:
            bits.append("Chains %s and %s pair most strongly, ipTM %s"
                        % (rk.e(first["id"]), rk.e(second["id"]), rk.num(top[0], 3)))
        inside = {first["id"], second["id"]}
        others = [c for c in self.chains if c["id"] not in inside]
        if len(others) > 2:
            # Naming eight chains one by one is a paragraph, not a caption: the two ends of the
            # matrix and the count, with the matrix itself under it.
            rest = [one for one in cells if one[1]["id"] not in inside
                    or one[2]["id"] not in inside]
            weakest = min(rest, key=lambda one: one[0]) if rest else None
            bits.append("and the other %s pair with them between %s and %s%s"
                        % (rk.plural(len(others), "chain"),
                           rk.num(min(one[0] for one in rest), 3),
                           rk.num(max(one[0] for one in rest), 3),
                           ", the weakest %s against %s"
                           % (rk.e(weakest[1]["id"]), rk.e(weakest[2]["id"]))
                           if weakest else ""))
            bits.append("higher is better, each cell the row chain against the column chain, "
                        "the diagonal each chain's pTM")
            return rk.sentences(bits)
        for chain in others:
            onto = [c for c in self.chains if c["id"] in inside]
            theirs = [self._pair_number((pairs.get(str(c["index"])) or {})
                                        .get(str(chain["index"]))) for c in onto]
            ours = [self._pair_number((pairs.get(str(chain["index"])) or {})
                                      .get(str(c["index"]))) for c in onto]
            if any(v is not None for v in theirs):
                bits.append("and the %s %s sits on them at %s, or %s read from %s's side"
                            % (chain["noun"], rk.e(chain["id"]),
                               rk.and_list(["%s from %s" % (rk.num(v, 3), rk.e(c["id"]))
                                            for v, c in zip(theirs, onto) if v is not None]),
                               rk.and_list([rk.num(v, 3) for v in ours if v is not None]),
                               rk.e(chain["id"])))
        bits.append("higher is better, each cell the row chain against the column chain, the "
                    "diagonal each chain's pTM")
        return rk.sentences(bits)

    # -------------------------------------------------------------- 5. along the chains

    def residues(self):
        blocks = [b for b in (self.plddt_figure(), self.pae_figure()) if b]
        if not blocks:
            return None
        if not (self.sample.get("plddt_tokens") or []):
            # No figure can be drawn from an array that is not there, and a figure with nothing
            # in it is worse than a sentence saying which file is missing.
            blocks.insert(0, {"kind": "p", "tone": "caveat", "html": rk.missing(
                "the per-token pLDDT of %s could not be read, so there is no figure of the "
                "confidence along the chains" % rk.e(self.sample.get("file") or "this sample"),
                "Boltz-2 writes it to <code>plddt_&lt;name&gt;_model_&lt;k&gt;.npz</code> beside "
                "the structure and ColabFold to the <code>plddt</code> list of its scores "
                "file")})
        return {"id": "residues", "heading": "Confidence along the chains", "label": "residues",
                "blocks": blocks}

    def plddt_figure(self):
        values = self.sample.get("plddt_tokens") or []
        if len(values) < 2:
            return None
        marks = [(c["start"], c["id"]) for c in self.chains if c["tokens"]]
        svg = rk.value_chart(values, x_label="token" if self.tool == "boltz2" else "residue",
                             y_label="pLDDT", mean_at=_mean(values),
                             bands=[(low, high, colour)
                                    for low, high, colour, _name in PLDDT_BANDS],
                             digits=0, y_range=(0, 100),
                             marks=marks if len(marks) > 1 else ())
        return {"kind": "figure", "id": "plddt", "svg": svg, "caption": self.plddt_caption()}

    def plddt_caption(self):
        values = self.sample.get("plddt_tokens") or []
        bands = ("bands are AlphaFold's: very high above 90, confident from 70, low from 50")
        if not values:
            return rk.missing("no per-token pLDDT was read for this sample")
        groups = self.groups()
        #: Boltz-2's axis is tokens (a residue, or one heavy atom of a ligand) and ColabFold's
        #: is residues. Each is called what its own tool calls it.
        unit = "token" if self.tool == "boltz2" else "residue"
        bits = []
        if len(self.chains) > 1 and len(groups) <= 3:
            first = groups[0]
            said = "%s %s pLDDT %s on average" % (rk.cap(self.plain_words(first)),
                                                  "hold" if len(first["chains"]) > 1
                                                  else "holds", self._group_plddt(first))
            said += "".join(" and %s %s" % (self.plain_words(g), self._group_plddt(g))
                            for g in groups[1:])
            bits.append(said + ", the lowest %s at %s" % (unit, rk.num(min(values), 1)))
        elif len(self.chains) > 1:
            means = [c for c in self.chains if c["plddt"] is not None]
            worst = min(means, key=lambda c: c["plddt"]) if means else None
            bits.append("The %s run from %s down to %s, the chain means from %s to %s%s"
                        % (rk.plural(len(values), unit), rk.num(max(values), 1),
                           rk.num(min(values), 1),
                           rk.num(max(c["plddt"] for c in means), 1) if means else "none",
                           rk.num(min(c["plddt"] for c in means), 1) if means else "none",
                           ", the weakest chain %s" % rk.e(worst["id"]) if worst else ""))
        else:
            good = [v for v in values if v > 90]
            poor = [(at + 1, v) for at, v in enumerate(values) if v < 70]
            said = "%s of the %s are above 90" % (len(good), rk.plural(len(values), unit))
            if poor:
                runs = self._runs([at for at, _v in poor])
                where = ", ".join("%s to %s" % (a, b) if a != b else str(a) for a, b in runs)
                said += (" and %s, %s, fall below 70, the lowest at %s"
                         % (rk.words(len(poor)), where,
                            rk.num(min(v for _at, v in poor), 1)))
            else:
                said += " and none falls below 70, the lowest at %s" % rk.num(min(values), 1)
            bits.append(said)
        bits.append(bands)
        return rk.sentences(bits)

    @staticmethod
    def _runs(numbers):
        """Consecutive positions as ranges, which is how a caption names a frayed loop."""
        out = []
        for at in sorted(numbers):
            if out and at == out[-1][1] + 1:
                out[-1][1] = at
            else:
                out.append([at, at])
        return [(a, b) for a, b in out]

    def _pae_rows(self):
        """The aligned-error matrix of the reported sample, read once.

        Boltz-2 writes it to a .npz beside the structure and ColabFold into its scores file. It
        is the largest thing this report reads, and it is asked for twice: once to decide
        whether the tick box on the bar can change anything, once to draw it.
        """
        if self._pae is None:
            self._pae = ((npz_array(self.sample.get("pae_file") or "", "pae") or [])
                         if self.tool == "boltz2" else (self.sample.get("pae") or []))
        return self._pae

    def _pae_picture(self):
        """ColabFold's own aligned-error plot, which it draws for every ranked model at once."""
        if self.tool != "colabfold" or not self.pred:
            return ""
        got = os.path.join(self.pred["folder"], "%s_pae.png" % self.pred["name"])
        return got if os.path.isfile(got) else ""

    def pae_figure(self):
        if not self.opt["pae"]:
            return None
        picture = self._pae_picture()
        rows = self._pae_rows()
        if picture:
            return {"kind": "figure", "id": "pae", "png": self._url(picture),
                    "caption": self.pae_caption(rows, drawn=True)}
        if not rows:
            return None
        small, step = self._shrink(rows)
        png = rk.grey_png(small, low=0, high=30,
                          scale=max(1, int(600 / max(1, len(small)))),
                          lines=[int(c / step) for c in self.chain_cuts()])
        if not png:
            return None
        return {"kind": "figure", "id": "pae", "png": png,
                "caption": self.pae_caption(rows, drawn=False, step=step)}

    @staticmethod
    def _shrink(rows, most=600):
        """The matrix as a picture no larger than it needs to be, by block mean.

        A thousand-token complex is a million numbers, and the picture a reader wants is the one
        they can see the blocks in. The caption says the step when there is one.
        """
        size = len(rows)
        if size <= most:
            return rows, 1
        step = int(size / most) + 1
        out = []
        for y in range(0, size - step + 1, step):
            line = []
            for x in range(0, size - step + 1, step):
                block = [rows[y + dy][x + dx] for dy in range(step) for dx in range(step)]
                line.append(sum(block) / float(len(block)))
            out.append(line)
        return out, step

    def pae_caption(self, rows, drawn=False, step=1):
        if drawn:
            best = self.samples[0] if self.samples else {}
            mean = None
            if rows:
                mean = _mean([v for line in rows for v in line])
            bits = []
            if mean is not None:
                bits.append("The best model's aligned error averages %s Å and reaches %s Å at "
                            "worst" % (rk.num(mean, 1), rk.num(best.get("max_pae"), 1)))
            else:
                bits.append("The best model's aligned error reaches %s Å at worst"
                            % rk.num(best.get("max_pae"), 1))
            bits.append("drawn by ColabFold itself for %s side by side, best first"
                        % rk.count(len(self.samples), "model"))
            bits.append("lower is better, rows the aligned residue, columns the residue scored")
            return rk.sentences(bits)
        if not rows:
            return rk.missing("this sample's aligned-error matrix could not be read",
                              "Boltz-2 writes it to pae_&lt;name&gt;_model_&lt;k&gt;.npz")
        reading = ("lower is better, rows the aligned residue, columns the residue scored, "
                   "0 to 30 Å dark to pale%s"
                   % (", each pixel the mean of a %s by %s block" % (step, step)
                      if step > 1 else ""))
        inside, across = self._pae_blocks(rows)
        bits = []
        if across and len(across) <= 2:
            every = [v for values in across.values() for v in values]
            low, high = min(every), max(every)
            bits.append("The expected error between %s averages %s"
                        % (rk.and_list(sorted(across)),
                           ("%s to %s Å" % (rk.num(low, 1), rk.num(high, 1)))
                           if high - low >= 0.05 else "%s Å" % rk.num(low, 1)))
        elif across:
            ranked = sorted(across.items(), key=lambda one: _mean(one[1]))
            bits.append("The expected error is lowest between %s, %s Å on average, and highest "
                        "between %s, %s Å, over %s of entities"
                        % (ranked[0][0], rk.num(_mean(ranked[0][1]), 1),
                           ranked[-1][0], rk.num(_mean(ranked[-1][1]), 1),
                           rk.count(len(across), "pairing")))
        else:
            flat = [v for line in rows for v in line]
            bits.append("The expected error averages %s Å across the chain and reaches %s Å at "
                        "worst" % (rk.num(_mean(flat), 1), rk.num(max(flat), 1)))
        if inside:
            bits.append("against %s between %s; %s"
                        % (rk.and_list(["%s Å" % rk.num(_mean(v), 1)
                                        for v in inside.values()]),
                           rk.and_list(list(inside)), reading))
        else:
            bits.append(rk.cap(reading))
        bits.append("the %s are the report's own; the run wrote the matrix"
                    % ("block means" if inside or across else "averages"))
        return rk.sentences(bits)

    def _pae_blocks(self, rows):
        """The mean aligned error within one entity and between two, as the caption names them.

        Each is the report's own arithmetic over the matrix the run wrote, which the caption
        says. A chain against itself is left out: the diagonal is not the question.
        """
        groups, inside, across = self.groups(), {}, {}
        for at, group in enumerate(groups):
            same = []
            for one in group["chains"]:
                for two in group["chains"]:
                    if one is two:
                        continue
                    got = self._block(rows, one, two)
                    if got is not None:
                        same.append(got)
            if same:
                inside["the %s %s chains"
                       % (rk.words(len(group["chains"])), group["noun"])] = same
            for other in groups[at + 1:]:
                both = []
                for one in group["chains"]:
                    for two in other["chains"]:
                        for first, second in ((one, two), (two, one)):
                            got = self._block(rows, first, second)
                            if got is not None:
                                both.append(got)
                if both:
                    across["%s and %s" % (self.plain_words(group),
                                          self.plain_words(other))] = both
        return inside, across

    def _block(self, rows, one, two):
        """The mean of the matrix over one chain's tokens against another's."""
        if not rows or not one["tokens"] or not two["tokens"]:
            return None
        if one["start"] + one["tokens"] > len(rows) or \
                two["start"] + two["tokens"] > len(rows):
            return None
        total, seen = 0.0, 0
        for y in range(one["start"], one["start"] + one["tokens"]):
            line = rows[y]
            for x in range(two["start"], two["start"] + two["tokens"]):
                total += line[x]
                seen += 1
        return total / seen if seen else None

    # -------------------------------------------------------------- 6. the structure

    def structure(self):
        figure = self.structure_figure()
        if not figure:
            return None
        return {"id": "structure", "heading": "The best structure", "label": "structure",
                "blocks": [figure]}

    def structure_figure(self):
        path = self.sample.get("path") or ""
        if not path or not os.path.isfile(path):
            return None
        layers = [{"sel": {}, "style": {"cartoon": {"color": "#b9c3cf"}}}]
        #: Banded by the B-factor column, which both tools write as a percentage whatever their
        #: confidence files hold, in the bands and colours web/app.js colours the viewer by. An
        #: integer range is the one numeric selection 3Dmol's own selector understands.
        for low, high, colour, _name in reversed(PLDDT_BANDS):
            layers.append({"sel": {"b": "%d-%d" % (low, high)},
                           "style": {"cartoon": {"color": colour}}})
        sticky = [c["id"] for c in self.chains
                  if c["kind"] in ("dna", "rna", "ligand", "ccd", "smiles", "ion")]
        for low, high, colour, _name in reversed(PLDDT_BANDS):
            if sticky:
                layers.append({"sel": {"chain": sticky, "b": "%d-%d" % (low, high)},
                               "style": {"stick": {"radius": 0.16, "color": colour}},
                               "add": True})
            layers.append({"sel": {"hetflag": True, "b": "%d-%d" % (low, high)},
                           "style": {"stick": {"radius": 0.18, "color": colour}}, "add": True})
        layers.append({"sel": {"elem": "H"}, "style": {}})
        structure = {"url": self._url(path),
                     "format": "cif" if path.lower().endswith(".cif") else "pdb",
                     "layers": layers, "zoom": 1.06}
        groups = self.groups()
        if len(groups) > 1 and not path.lower().endswith(".cif"):
            structure["side_on"] = [[c["id"] for c in groups[0]["chains"]],
                                    [c["id"] for g in groups[1:] for c in g["chains"]]]
        return {"kind": "figure", "id": "best", "structure": structure,
                "caption": self.structure_caption()}

    def structure_caption(self):
        bits = []
        name = self._sample_name(self.sample)
        if self.tool == "boltz2":
            head = ("%s, the best by confidence score (%s)" % (rk.cap(name),
                                                               rk.num(self.sample.get(
                                                                   "confidence"), 3))
                    if self.at == 1 else
                    "%s, confidence score %s against %s for the best"
                    % (rk.cap(name), rk.num(self.sample.get("confidence"), 3),
                       rk.num(self.samples[0].get("confidence"), 3)))
        else:
            head = "%s, pLDDT %s and pTM %s" % (rk.cap(name), rk.num(self.sample.get("plddt"), 1),
                                                rk.num(self.sample.get("ptm"), 2))
        groups = self.groups()
        if len(groups) == 2 and self.sample.get("iptm") is not None:
            target = groups[0]
            head += (", with %s across %s at ipTM %s"
                     % (self.group_words(groups[-1], with_length=False),
                        "the %s dimer" % target["noun"] if len(target["chains"]) == 2
                        else self.plain_words(target),
                        rk.num(self.sample.get("iptm"), 3)))
        elif len(groups) > 2 and self.sample.get("iptm") is not None:
            head += (", holding its %s together at ipTM %s"
                     % (rk.count(len(self.chains), "chain"),
                        rk.num(self.sample.get("iptm"), 3)))
        bits.append(head)
        bits.append("coloured by pLDDT in AlphaFold's bands, blue above 90 to orange below 50, "
                    "as the Runs screen colours it")
        if len(groups) > 1 and (self.sample.get("path") or "").lower().endswith(".cif"):
            bits.append("the view is 3Dmol's own framing: turning an interface side on needs the "
                        "PDB format, and this run wrote mmCIF")
        return rk.sentences(bits)

    def _url(self, path):
        return "/api/download?path=%s&file=%s" % (_q(self.run),
                                                  _q(os.path.relpath(path, self.run)))

    # -------------------------------------------------------------- 7. the alignments

    def _alignment_rows(self):
        """One row per alignment file phase one left: (name, depth, chains, how, file).

        Counted once: the depth is the file's line count, and the chains table asks for it per
        chain.
        """
        if self._msa_rows is not None:
            return self._msa_rows
        out = []
        for path in runsmod.alignments(self.run):
            name = os.path.basename(path)
            if path.lower().endswith(".csv"):
                depth = _count_csv(path)
                chains = self._chains_for_msa(path)
                how = "fetched on the login node"
            else:
                depth = _count_a3m(path)
                chains = [c["id"] for c in self.chains if c["kind"] == "protein"]
                how = "fetched on the login node"
            out.append((name, depth, chains, how, os.path.relpath(path, self.run)))
        self._msa_rows = out
        return out

    def _chains_for_msa(self, path):
        """Which chains an alignment serves, read off the input the job was given."""
        told = (self.pred or {}).get("input") or ""
        try:
            with open(told, errors="replace") as handle:
                text = handle.read()
        except OSError:
            return [c["id"] for c in self.chains if c["kind"] == "protein"]
        out = []
        for block in text.split("- protein:")[1:]:
            ids = re.search(r"id:\s*\[([^\]]*)\]", block)
            msa = re.search(r"msa:\s*(\S+)", block)
            if msa and os.path.basename(msa.group(1)) == os.path.basename(path) and ids:
                out += [one.strip().strip("'\"") for one in ids.group(1).split(",")]
        return out or [c["id"] for c in self.chains if c["kind"] == "protein"]

    def alignments(self):
        if not self.opt["alignments"]:
            return None
        blocks = [self.alignments_table()]
        coverage = self.coverage_figure()
        if coverage:
            blocks.append(coverage)
        return {"id": "alignments", "heading": "Alignments (phase one)", "label": "alignments",
                "blocks": blocks}

    def alignments_table(self):
        columns = [{"key": "name", "label": "Alignment"},
                   {"key": "chains", "label": "Chains served"},
                   {"key": "depth", "label": "Sequences", "num": True, "digits": 0,
                    "empty": "—", "help": "The query included"},
                   {"key": "file", "label": "File"},
                   {"key": "how", "label": "How it was made"}]
        rows, served = [], set()
        for name, depth, chains, how, where in self._alignment_rows():
            rows.append({"name": name, "chains": ", ".join(chains), "depth": depth,
                         "file": where, "how": how})
            served |= set(chains)
        for group in self.groups():
            ids = [c["id"] for c in group["chains"]]
            if group["kind"] == "protein":
                if not [one for one in ids if one in served]:
                    rows.append({"name": "none", "chains": ", ".join(ids), "depth": 1,
                                 "file": "",
                                 "how": "single sequence: folded from the sequence alone"})
            else:
                rows.append({"name": "none needed", "chains": ", ".join(ids), "depth": None,
                             "file": "",
                             "how": "%s takes no alignment" % _a(group["noun"])})
        return {"kind": "table", "id": "alignments", "columns": columns, "rows": rows,
                "empty": "This run left no alignment file.",
                "caption": self.alignments_caption()}

    def alignments_caption(self):
        found = self._alignment_rows()
        others = [g for g in self.groups() if g["kind"] != "protein"]
        if not found:
            proteins = [c for c in self.chains if c["kind"] == "protein"]
            who = rk.and_list(["chain %s" % rk.e(c["id"]) for c in proteins]) or "the protein"
            return rk.sentences([
                "No alignment was fetched: %s %s folded from its sequence alone, which the page "
                "warns gives markedly worse structures"
                % (who, "were" if len(proteins) > 1 else "was"),
                "this run folder holds no alignment file at all"])
        bits = []
        config = (self.pred or {}).get("config") or {}
        name, depth, chains, _how, where = found[0]
        if self.tool == "colabfold":
            mode = str(config.get("msa_mode") or "")
            bits.append("The alignment holds %s, query included, from ColabFold's MMseqs2 search "
                        "of %s, fetched on the login node with "
                        "<code>colabfold_batch --msa-only</code>"
                        % (rk.plural(depth or 0, "sequence"),
                           "UniRef30 and the environmental databases (<code>%s</code>)" % rk.e(mode)
                           if "env" in mode else "UniRef30 (<code>%s</code>)" % rk.e(mode)))
        else:
            bits.append("The %s's alignment holds %s, fetched from the ColabFold MSA server on "
                        "the login node before the job was queued, and serves %s"
                        % ("protein" if len(found) == 1 else "first protein",
                           rk.plural(depth or 0, "sequence"),
                           rk.and_list(["chain%s %s" % ("s" if len(chains) > 1 else "",
                                                        rk.and_list([rk.e(c) for c in chains]))])
                           if chains else "the protein chains"))
            if len(found) > 1:
                bits.append("and the other %s hold %s"
                            % (rk.plural(len(found) - 1, "alignment"),
                               rk.and_list([rk.plural(d or 0, "sequence")
                                            for _n, d, _c, _h, _f in found[1:]])))
        if others:
            bits.append("%s %s none"
                        % (rk.and_list([self.plain_words(g) for g in others]),
                           "take" if len(others) > 1 or len(others[0]["chains"]) > 1
                           else "takes"))
        return rk.sentences(bits)

    def coverage_figure(self):
        if self.tool != "colabfold" or not self.pred:
            return None
        path = os.path.join(self.pred["folder"], "%s_coverage.png" % self.pred["name"])
        if not os.path.isfile(path):
            return None
        found = self._alignment_rows()
        depth = found[0][1] if found else None
        length = sum(c["tokens"] for c in self.chains)
        return {"kind": "figure", "id": "coverage", "png": self._url(path),
                "caption": rk.sentences([
                    "The %s cover the %s query along its length, as ColabFold drew it"
                    % (rk.plural(depth or 0, "aligned sequence"),
                       "%s-residue" % length if length else ""),
                    "colour is sequence identity to the query"])}

    # -------------------------------------------------------------- the methods

    def methods(self, version):
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

        paragraphs = (self._boltz_methods(version, cite) if self.tool == "boltz2"
                      else self._colabfold_methods(version, cite))
        references = [{"n": n, "text": refs[key]} for n, key in enumerate(order, 1)]
        return "".join("<p>%s</p>" % p for p in paragraphs if p and p.strip()), references

    def _references(self):
        out = {
            "3dmol": "Rego, N. and Koes, D. 3Dmol.js: molecular visualization with WebGL. "
                     "Bioinformatics 31, 1322-1324 (2015). doi:10.1093/bioinformatics/btu829",
            "hprc": "Texas A&M High Performance Research Computing, which provided the "
                    "computing resources this work used.",
        }
        if self.tool == "boltz2":
            out.update({
                "boltz2": "Passaro, S., Corso, G., Wohlwend, J., Reveiz, M., Thaler, S., "
                          "Somnath, V. R., Getz, N., Portnoi, T., Roy, J., Stark, H., "
                          "Kwabi-Addo, D., Beaini, D., Jaakkola, T. and Barzilay, R. Boltz-2: "
                          "towards accurate and efficient binding affinity prediction. bioRxiv "
                          "(2025). doi:10.1101/2025.06.14.659707",
                "boltz1": "Wohlwend, J., Corso, G., Passaro, S., Getz, N., Reveiz, M., "
                          "Leidal, K., Swiderski, W., Atkinson, L., Portnoi, T., Chinn, I., "
                          "Silterra, J., Jaakkola, T. and Barzilay, R. Boltz-1: democratizing "
                          "biomolecular interaction modeling. bioRxiv (2024). "
                          "doi:10.1101/2024.11.19.624167",
                "colabfold": "Mirdita, M., Schütze, K., Moriwaki, Y., Heo, L., Ovchinnikov, S. "
                             "and Steinegger, M. ColabFold: making protein folding accessible "
                             "to all. Nature Methods 19, 679-682 (2022). "
                             "doi:10.1038/s41592-022-01488-1",
                "mmseqs2": "Steinegger, M. and Söding, J. MMseqs2 enables sensitive protein "
                           "sequence searching for the analysis of massive data sets. Nature "
                           "Biotechnology 35, 1026-1028 (2017). doi:10.1038/nbt.3988 (the "
                           "search behind the alignment server; not in the tool's own citation "
                           "list.)",
            })
        else:
            out.update({
                "colabfold": "Mirdita, M., Schütze, K., Moriwaki, Y., Heo, L., Ovchinnikov, S. "
                             "and Steinegger, M. ColabFold: making protein folding accessible "
                             "to all. Nature Methods 19, 679-682 (2022). "
                             "doi:10.1038/s41592-022-01488-1",
                "alphafold": "Jumper, J. et al. Highly accurate protein structure prediction "
                             "with AlphaFold. Nature 596, 583-589 (2021). "
                             "doi:10.1038/s41586-021-03819-2",
                "multimer": "Evans, R. et al. Protein complex prediction with "
                            "AlphaFold-Multimer. bioRxiv (2021). doi:10.1101/2021.10.04.463034",
                "mmseqs2": "Mirdita, M., Steinegger, M. and Söding, J. MMseqs2 desktop and "
                           "local web server app for fast, interactive sequence searches. "
                           "Bioinformatics 35, 2856-2858 (2019). "
                           "doi:10.1093/bioinformatics/bty1057",
                "uniclust": "Mirdita, M., von den Driesch, L., Galiez, C., Martin, M. J., "
                            "Söding, J. and Steinegger, M. Uniclust databases of clustered and "
                            "deeply annotated protein sequences and alignments. Nucleic Acids "
                            "Research 45, D170-D176 (2017). doi:10.1093/nar/gkw1081",
                "mgnify": "Mitchell, A. L. et al. MGnify: the microbiome analysis resource in "
                          "2020. Nucleic Acids Research 48, D570-D578 (2019). "
                          "doi:10.1093/nar/gkz1035",
            })
            for key, mark in (("mmseqs2", "Mirdita2019"), ("uniclust", "Mirdita2017"),
                              ("mgnify", "Mitchell2019")):
                if self._bibtex() and mark not in self._bibtex():
                    out.pop(key, None)
        return out

    def _bibtex(self):
        """The citation keys ColabFold wrote for this exact configuration, or ""."""
        if self.tool != "colabfold" or not self.pred:
            return ""
        path = os.path.join(self.pred["folder"], "cite.bibtex")
        try:
            with open(path, errors="replace") as handle:
                return handle.read()
        except OSError:
            return ""

    def _fetched(self):
        return str(self.sub.get("alignments") or "").startswith("fetch")

    def _card(self):
        """The card the job ran on: what was asked for, confirmed by the log."""
        plan = self.sub.get("plan") or {}
        facts = self._log_facts()
        if facts.get("card"):
            return facts["card"]
        gpu = str(plan.get("gpu") or "")
        memory = rk.whole(plan.get("card_memory"))
        if not gpu:
            return ""
        return "%s%s" % (gpu.upper(), " (%s GB)" % memory if memory else "")

    def _card_said(self):
        """The card as a methods sentence names it: one NVIDIA A100 (40 GB), or the GPU."""
        card = self._card()
        return "one %s" % _card_words(card) if card else "the GPU"

    def _network_note(self):
        if self.site and self.site.notes.get("network"):
            return self.site.notes["network"]
        return ""

    def _phase_words(self, cite, keys=(), tail=""):
        """The two-phase paragraph, which is the one thing about these runs that has to be said
        for the fold to be repeatable: a login node fetches the alignment and the compute node
        the prediction runs on has no network at all.

        A run folded from the sequence alone has no phase one, so it gets the variant sentence
        rather than a phase it did not have. The alignment server is cited from inside that
        decision, not before it: a reference asked for and then not printed would be numbered
        in the list with nothing pointing at it.
        """
        found = self._alignment_rows()
        proteins = len([c for c in self.chains if c["kind"] == "protein"])
        why = ("because the compute nodes of the %s cluster have no network"
               % rk.e(self.site.name if self.site else "this"))
        if not self._fetched() or not found:
            return ("No alignment was fetched: the chain%s %s folded from %s sequence alone. "
                    "The prediction ran on %s%s."
                    % ("s" if proteins > 1 else "", "were" if proteins > 1 else "was",
                       "their" if proteins > 1 else "its", self._card_said(),
                       ", on a compute node, which on the %s cluster has no network at all"
                       % rk.e(self.site.name if self.site else "this")
                       if self._network_note() else " with no network"))
        return ("The work ran in two phases, %s: on the login node, before the job was queued, "
                "%s of %s %s fetched from the ColabFold MMseqs2 server%s; the prediction then "
                "ran on %s with no network%s."
                % (why,
                   "the protein's alignment" if len(found) == 1
                   else "%s alignments" % rk.words(len(found)),
                   rk.and_list([rk.plural(d or 0, "sequence")
                                for _n, d, _c, _h, _f in found]),
                   "was" if len(found) == 1 else "were", cite(*keys), self._card_said(),
                   ", " + tail if tail else ""))

    def _boltz_methods(self, version, cite):
        pred = self.pred or {}
        hparams = pred.get("hparams") or {}
        ran = hparams.get("predict_args") or {}
        steering = hparams.get("steering_args") or {}
        groups = self.groups()
        said = rk.and_list([self._methods_entity(g) for g in groups])
        stop = "," if groups and groups[-1]["name"] else ""
        out = ["%s%s were folded together with %s %s%s."
               % (rk.cap(said) if said else "The chains", stop, rk.e(TOOL_NAME[self.tool]),
                  rk.e(version), cite("boltz2", "boltz1"))
               if len(groups) > 1 else
               "%s%s was folded with %s %s%s."
               % (rk.cap(said) if said else "One chain", stop, rk.e(TOOL_NAME[self.tool]),
                  rk.e(version), cite("boltz2", "boltz1"))]
        out.append(self._phase_words(cite, ("colabfold", "mmseqs2"),
                                     "from that alignment and the weights already installed"))
        settings = []
        for key, word in (("recycling_steps", "recycling step"),
                          ("sampling_steps", "sampling step"),
                          ("diffusion_samples", "diffusion sample")):
            if rk.whole(ran.get(key)) is not None:
                settings.append(rk.plural(rk.whole(ran[key]), word))
        asked = self.sub.get("asked") or {}
        third = []
        if settings:
            third.append("Boltz-2 used %s" % rk.and_list(settings))
            extra = []
            if not steering.get("physical_guidance_update") and not asked.get("use_potentials"):
                extra.append("steering potentials")
            if not str(asked.get("seed") or ""):
                extra.append("a fixed seed")
            if extra:
                third.append(", without %s" % rk.and_list(extra, "or"))
            if hparams.get("use_kernels") is False:
                third.append(", and without the CUDA kernels")
            third.append(".")
        else:
            third.append("What Boltz-2 ran with could not be read: this prediction has no "
                         "<code>hparams.yaml</code> beside it, so the settings in the appendix "
                         "are the ones the form asked for.")
        ranking = ("0.8 times the complex pLDDT plus 0.2 times the %s"
                   % ("ipTM" if len(self.chains) > 1 else "pTM"))
        third.append(" The samples were ranked by Boltz-2's confidence score, %s." % ranking)
        if any(c["kind"] in ("ligand", "ccd", "smiles", "ion") for c in self.chains):
            third.append(" Ligand ipTM is reported for the chemical chains.")
        out.append("".join(third))
        last = []
        if self.structure_figure():
            last.append("Structures were drawn with 3Dmol.js%s." % cite("3dmol"))
        if self.site:
            last.append(" The work ran on %s%s." % (rk.e(self.site.name), cite("hprc")))
        if last:
            out.append("".join(last))
        return out

    def _methods_entity(self, group):
        ids = [c["id"] for c in group["chains"]]
        named = ", %s" % rk.e(group["name"]) if group["name"] else ""
        if len(ids) > 1:
            return ("%s copies of a %s-%s %s%s (chains %s)"
                    % (rk.words(len(ids)), rk.whole(group["length"]), group["unit"],
                       group["noun"], named, rk.and_list([rk.e(i) for i in ids])))
        return ("a %s-%s %s%s (chain %s)"
                % (rk.whole(group["length"]), group["unit"], group["noun"], named,
                   rk.e(ids[0])))

    def _colabfold_methods(self, version, cite):
        config = (self.pred or {}).get("config") or {}
        groups = self.groups()
        said = rk.and_list([self._methods_entity(g) for g in groups])
        weights = str(config.get("model_type") or "")
        out = ["%s %s folded with %s %s%s, using the %s weights%s, which the lab's install has "
               "in full for offline use and asks for a single chain as well."
               % (rk.cap(said) if said else "One chain",
                  "were" if len(self.chains) > 1 else "was",
                  rk.e(TOOL_NAME[self.tool]), rk.e(config.get("version") or version),
                  cite("colabfold"),
                  "AlphaFold-Multimer v3" if "multimer_v3" in weights else rk.e(weights),
                  cite("alphafold", "multimer"))]
        found = self._alignment_rows()
        mode = str(config.get("msa_mode") or "")
        if self._fetched() and found:
            why = ("because the compute nodes of the %s cluster have no network"
                   % rk.e(self.site.name if self.site else "this"))
            out.append("The work ran in two phases, %s: on the login node, before the job was "
                       "queued, <code>colabfold_batch --msa-only</code> fetched an alignment of "
                       "%s from the ColabFold MMseqs2 server, searching %s%s; the prediction "
                       "then ran on %s with no network."
                       % (why, rk.plural(found[0][1] or 0, "sequence"),
                          "UniRef30 and the environmental databases"
                          if "env" in mode else "UniRef30",
                          cite("mmseqs2", "uniclust", "mgnify"), self._card_said()))
        else:
            out.append(self._phase_words(cite))
        third = []
        models = rk.whole(config.get("num_models"))
        recycles = rk.whole(config.get("num_recycles"))
        seeds = rk.whole(config.get("num_seeds"))
        if models:
            third.append("%s models were run with %s and %s"
                         % (rk.cap(rk.words(models)),
                            rk.plural(recycles or 0, "recycle"),
                            rk.plural(seeds or 0, "seed")))
            if rk.whole(config.get("random_seed")) is not None:
                third.append(" (random seed %s)" % rk.whole(config["random_seed"]))
            leave = []
            if not config.get("use_templates"):
                leave.append("templates")
            if not rk.whole(config.get("num_relax")):
                leave.append("relaxation")
            if leave:
                third.append(", without %s" % rk.and_list(leave, "or"))
            third.append(", and ranked by %s."
                         % RANKED_BY.get(str(config.get("rank_by") or ""), "mean pLDDT"))
        else:
            third.append("What ColabFold ran with could not be read: this prediction has no "
                         "<code>config.json</code> beside it.")
        out.append("".join(third))
        last = []
        if self.structure_figure():
            last.append("Structures were drawn with 3Dmol.js%s." % cite("3dmol"))
        if self.site:
            last.append(" The work ran on %s%s." % (rk.e(self.site.name), cite("hprc")))
        if last:
            out.append("".join(last))
        return out

    # -------------------------------------------------------------- the appendix

    def appendix(self):
        return [a for a in (self.appx_settings(), self.appx_told(), self.appx_phase_one(),
                            self.appx_phase_two(), self.appx_software(),
                            self.appx_files()) if a]

    def appx_settings(self):
        asked = self.sub.get("asked") or {}
        hparams = (self.pred or {}).get("hparams") or {}
        ran = dict(hparams.get("predict_args") or {})
        config = (self.pred or {}).get("config") or {}
        pairs = {"recycling_steps": ("recycling_steps", "num_recycles"),
                 "sampling_steps": ("sampling_steps", None),
                 "diffusion_samples": ("diffusion_samples", None),
                 "num_models": (None, "num_models"),
                 "num_recycle": (None, "num_recycles"),
                 "num_seeds": (None, "num_seeds"),
                 "rank": (None, "rank_by")}
        rows = []
        for key in sorted(asked):
            value = asked[key]
            shown = rk.e(json.dumps(value) if isinstance(value, (dict, list)) else str(value))
            mine, theirs = pairs.get(key, (None, None))
            got = ran.get(mine) if mine else (config.get(theirs) if theirs else None)
            if got is None and theirs:
                got = config.get(theirs)
            same = (rk.number(got) is not None and rk.number(got) == rk.number(value)) or \
                str(got) == str(value)
            if got is not None and not same:
                shown += " <i>(asked); %s (as run)</i>" % rk.e(_said(got))
            elif got is not None:
                shown += " <i>(asked and as run)</i>"
            rows.append((rk.e(key), shown))
        if not rows:
            rows.append(("Nothing recorded", "<i>this run folder holds no "
                                             "<code>submission.json</code></i>"))
        blocks = [rk.facts(rows)]
        if ran or config:
            blocks.append({"kind": "heading", "text": "What the tool itself recorded"})
            said = ran or {k: v for k, v in config.items()
                           if k in ("num_models", "num_recycles", "num_seeds", "rank_by",
                                    "msa_mode", "pair_mode", "model_type", "use_templates",
                                    "num_relax", "random_seed", "max_seq", "max_extra_seq",
                                    "version", "host_url")}
            blocks.append(rk.facts([(rk.e(k), rk.e(_said(said[k]))) for k in sorted(said)]))
        return {"heading": "Settings, as asked and as run", "blocks": blocks}

    def appx_told(self):
        told = runsmod._told(self.run)
        #: The input of the prediction being reported, which in a folder holding two is not the
        #: first one by name: runs._told() takes the first, and this report is about one of them.
        mine = (self.pred or {}).get("input") or ""
        if os.path.isfile(mine):
            try:
                with open(mine, errors="replace") as handle:
                    told = dict(told, what=os.path.relpath(mine, self.run),
                                text=handle.read()[:20000])
            except OSError:
                pass
        blocks = []
        if told.get("text"):
            blocks.append({"kind": "heading", "text": "The input, %s" % told.get("what")})
            blocks.append({"kind": "pre", "text": told["text"][:8000]})
        if told.get("command"):
            blocks.append({"kind": "heading", "text": "The command the job ran"})
            blocks.append({"kind": "pre", "text": told["command"]})
        inputs = sorted(glob.glob(os.path.join(self.run, "inputs", "*")))
        if len(inputs) > 1:
            blocks.append({"kind": "p", "tone": "caveat", "html":
                           "This run folder holds %s and one job script: %s. The input shown "
                           "above is the first of them, and the command is the one the script "
                           "holds, which is %s's."
                           % (rk.plural(len(inputs), "input"),
                              rk.and_list([rk.e(os.path.basename(one)) for one in inputs]),
                              rk.e(self._named_prediction()))})
        if not blocks:
            return None
        return {"heading": "What the tool was told", "blocks": blocks}

    def appx_phase_one(self):
        rows, found = [], self._alignment_rows()
        for name, depth, chains, _how, where in found:
            rows.append((rk.e(name), rk.e("%s, for %s (%s)"
                                          % (rk.plural(depth or 0, "sequence"),
                                             ", ".join(chains) or "the protein", where))))
            when = os.path.join(self.run, where)
            if os.path.exists(when):
                rows.append(("Written", rk.e(time.strftime("%Y-%m-%d %H:%M",
                                                           time.localtime(
                                                               os.path.getmtime(when))))
                             + " <i>(the file's own time: the run records no phase-one "
                               "timing)</i>"))
        if not found:
            rows.append(("No alignment", "This run fetched none: every chain was folded from "
                                         "its sequence alone, or takes no alignment. There was "
                                         "no phase one."))
        config = (self.pred or {}).get("config") or {}
        if config.get("host_url"):
            rows.append(("Server", rk.e(config["host_url"])))
        elif found:
            rows.append(("Server", "<code>https://api.colabfold.com</code> <i>(the helper's "
                                   "default; the run does not record it)</i>"))
        if self._network_note():
            rows.append(("Why two phases", rk.e(self._network_note())))
        return {"heading": "Phase one, on the login node", "blocks": [rk.facts(rows)]}

    def _log(self):
        """This run's own Slurm log: the job submission.json names, where there is one.

        runs.log_path() takes the newest, which is right for the Runs screen. A folder holding
        two jobs holds two logs written in the same minute, and the one this report is about is
        the one whose job id the record carries: taking the newest puts another job's node and
        card in the appendix.
        """
        job = str(self.sub.get("job_id") or "")
        mine = os.path.join(self.run, "slurm_%s.out" % job)
        if job and os.path.isfile(mine):
            return mine
        return runsmod.log_path(self.run)

    def _log_facts(self):
        """The few lines of the Slurm log that are facts rather than noise."""
        log = self._log()
        if not log:
            return {}
        try:
            with open(log, errors="replace") as handle:
                lines = handle.read().splitlines()
        except OSError:
            return {}
        out = {}
        for at, line in enumerate(lines):
            if line.startswith("temporary folder:"):
                out["tmp"] = line.split(":", 1)[1].strip()
            if line.strip() == "--- node ---" and at + 1 < len(lines):
                out["node"] = lines[at + 1].strip()
            got = re.match(r"(NVIDIA [^,]+),\s*(\d+)\s*MiB", line.strip())
            if got:
                out["card"] = "%s (%s GB)" % (got.group(1),
                                              int(round(int(got.group(2)) / 1024.0)))
            if line.strip() == "--- what was written ---" and at + 1 < len(lines):
                out["written"] = lines[at + 1].strip()
            got = re.search(r"Predicting DataLoader 0: 100%\|[^|]*\|\s*\d+/\d+ \[([\d:]+)<",
                            line)
            if got:
                out["predicted"] = got.group(1)
        out["log"] = os.path.relpath(log, self.run)
        return out

    def _colabfold_times(self):
        path = os.path.join((self.pred or {}).get("folder") or "", "log.txt")
        out = []
        try:
            with open(path, errors="replace") as handle:
                for line in handle:
                    got = re.search(r"(\S+_model_\d+_seed_\d+) took ([\d.]+)s", line)
                    if got:
                        out.append((got.group(1), got.group(2)))
        except OSError:
            return []
        return out

    def appx_phase_two(self):
        job = str(self.sub.get("job_id") or "")
        plan = self.sub.get("plan") or {}
        facts = self._log_facts()
        rows = []
        if job:
            state, elapsed = self._state()
            rows.append(("Job id", rk.e(job)))
            if state:
                rows.append(("Slurm's record", rk.e("%s%s" % (state,
                                                              ", %s elapsed" % elapsed
                                                              if elapsed else ""))))
            else:
                rows.append(("Slurm's record", "<i>Slurm no longer remembers this job: "
                                               "accounting forgets after a few months, which is "
                                               "why the folding job should write its own "
                                               "finished.json</i>"))
        for label, key in (("Node", "node"), ("Card", "card"),
                           ("Temporary folder", "tmp"), ("What was written", "written")):
            if facts.get(key):
                rows.append((label, rk.e(facts[key])))
        if facts.get("predicted"):
            rows.append(("Prediction time", rk.e(facts["predicted"])
                         + " <i>(the log's last progress line)</i>"))
        for name, took in self._colabfold_times():
            rows.append((rk.e(name), rk.e("%s s" % took)))
        for label, key in (("Partition asked", "partition"), ("GPU asked", "gres"),
                           ("Cores", "cpus"), ("Memory (GB)", "mem"), ("Walltime", "walltime")):
            if plan.get(key):
                rows.append((label, rk.e(plan[key])))
        blocks = [rk.facts(rows)] if rows else []
        log = self._log()
        if log:
            blocks.append({"kind": "heading", "text": "The end of %s"
                                                      % os.path.relpath(log, self.run)})
            blocks.append({"kind": "pre", "text": rk.tail(log, 20)})
        if not blocks:
            return None
        return {"heading": "Phase two, the GPU job", "blocks": blocks}

    def appx_software(self):
        from .. import __version__ as labs_version
        config = (self.pred or {}).get("config") or {}
        hparams = (self.pred or {}).get("hparams") or {}
        items = []
        version = folddocs.upstream_version(self.tool, self.install or "")
        if config.get("version"):
            items.append("%s %s, as the run itself recorded it (<code>config.json</code>)"
                         % (TOOL_NAME[self.tool], rk.e(config["version"])))
        else:
            items.append("%s %s, read from the install as this report was opened rather than "
                         "from the run: no file in this run folder records the version it ran, "
                         "so a run read a year from now would be labelled with whatever is "
                         "installed then" % (TOOL_NAME[self.tool], rk.e(version)))
        if config.get("model_type"):
            items.append("Weights: %s, from the install's own <code>params</code> folder"
                         % rk.e(config["model_type"]))
        if hparams:
            items.append("The model ran %s the CUDA kernels"
                         % ("with" if hparams.get("use_kernels") else "without"))
        for label, key in (("Install", "install"), ("Cluster", "site")):
            if self.sub.get(key):
                items.append("%s: <code>%s</code>" % (label, rk.e(self.sub[key])))
        told = runsmod._told(self.run)
        got = re.search(r"--cache\s+(\S+)", told.get("command") or "")
        if got:
            items.append("Weights and component cache: <code>%s</code>" % rk.e(got.group(1)))
        if self.sub.get("submitted") or self.sub.get("user"):
            items.append("Submitted %s%s" % (rk.e(self.sub.get("submitted") or "on an "
                                                  "unrecorded date"),
                                             " by " + rk.e(self.sub["user"])
                                             if self.sub.get("user") else ""))
        items.append("HOPE Labs %s, which is this page and this report; the run folder records "
                     "no version of its own" % rk.e(labs_version))
        return {"heading": "Software and provenance", "blocks": [{"kind": "list",
                                                                  "items": items}]}

    def appx_files(self):
        names = []
        folders = [self.run, os.path.join(self.run, "inputs")]
        if self.pred:
            folders.append(self.pred["folder"])
        for folder in folders:
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
                 "The run folder, its inputs and the prediction folder hold %s between them, "
                 "the largest %s"
                 % (rk.plural(len(names), "file"), rk.e(max(names, key=lambda p: p[1])[0])),
                 "the caches the job wrote for itself, and the alignment server's temporary "
                 "files, are not listed"])}]}


def document(tool, rundir, raw=None, install=""):
    """The whole report of a folding run, or the one thing a reader can act on if it cannot be
    written."""
    try:
        return Report(tool, rundir, raw or {}, install=install).build()
    except Exception as exc:                                        # noqa: BLE001
        return {"error": "the report could not be written: %s: %s"
                         % (type(exc).__name__, exc)}


def page_for(token, rundir, tool, autoprint=False):
    """The report page for one folding run, with this tool's own mark and store key.

    3Dmol comes from the copy this page vendors, and only then from the pinned CDN: a report
    opened through a tunnel to a compute node has no internet, and the structure figure is drawn
    in the browser.
    """
    from .. import __version__ as labs_version
    return rk.page(token, rundir, labs_version, store="%s.report" % tool, autoprint=autoprint,
                   favicon='<link rel="icon" type="image/svg+xml" href="%s">'
                           % folddocs.favicon(tool),
                   three_dmol="/static/vendor/3Dmol-min.js")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2 or argv[0] not in SERVES:
        print("usage: python -m hope_labs.folding.runreport {%s} <run folder> [opt.key=value]"
              % "|".join(SERVES), file=sys.stderr)
        return 2
    raw = {}
    for one in argv[2:]:
        key, _, value = one.partition("=")
        raw[key[4:] if key.startswith("opt.") else key] = value
    print(json.dumps(document(argv[0], argv[1], raw), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
