"""Reading a target, checking a form, and queueing a design run.

By Aadhil Haq

Everything here goes through hope_flow: the target is read by the target card's own driver, the
form is checked by the flow's own check(), and the run is queued by the tool's own driver. A run
queued from this page and a run queued from the canvas are therefore the same run, refused for
the same reasons and landing in the same folder, because there is one piece of code that does it
and this is not a second copy of it.

The page builds a flow of two cards, a target and the tool on it, and queues it card by card the
way hope_flow/run.py does: the target first, so the structure is in the run folder, then the
adapter that carries it across, then the tool. The difference from a flow is only that nobody had
to draw it.

What is here and not in hope_flow is the part a flow cannot do, because a flow is checked on a
laptop where the structure is not: looking inside the target. The chains, whether a hotspot is a
residue that exists, and what the hotspots become once converted are all read off the file, by
the tool's own parser, so what the page shows is what the job will see.
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import time

from hope_flow import adapters, cards, drivers
from hope_flow import flow as flowmod
from hope_flow import state as flowstate
from hope_flow.helpers import boltzgen_design, rfd3_design, rfdiffusion_design

from . import CARD_ONLY, SERVES

#: The most a structure may be when it is uploaded through the page. A receptor is a few hundred
#: kilobytes; anything near this is a trajectory or a mistake, and reading it would only waste the
#: login node. A file too large for this is given as a path instead, which costs nothing.
UPLOAD_CAP = 24 * 1024 * 1024

#: The parser each tool's job uses to read a structure. The three are the same function in three
#: files and will stay that way, since each runs under its own tool's Python; taking the tool's
#: own is what makes the chains on the page the chains the job will find, rather than nearly them.
PARSER = {"rfdiffusion": rfdiffusion_design, "rfd3": rfd3_design,
          "boltzgen": boltzgen_design}

#: Walltimes Slurm takes, from `man sbatch`. Checked here because the form is where a mistake is
#: cheap: sbatch's own complaint arrives after the run folder has been written.
WALLTIME = re.compile(r"^(\d+|\d+:\d{1,2}|\d{1,3}:\d{1,2}:\d{1,2}|"
                      r"\d+-\d{1,2}(:\d{1,2}){0,2})$")

#: What the `gpu` partition allows. A job asking for longer is refused at submission, which is
#: worth saying while the box is being typed in rather than afterwards.
MAX_DAYS = 4


class Refused(ValueError):
    """Something the page will not do, said in terms the person can act on."""


def safe_name(text, fallback="run"):
    """A name a run folder and a flow can both carry.

    hope_flow refuses a flow whose name is not letters, digits, dot, dash or underscore
    (flow.NAME_OK), so a target called "3 d_struct (1).pdb" is cut back to one rather than
    refused after everything else has been filled in.
    """
    out = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or "")).strip("_.-")
    out = re.sub(r"_{2,}", "_", out)[:48].strip("_.-")
    return out if re.match(r"^[A-Za-z0-9]", out or "") else fallback


def card_of(tool):
    if tool not in SERVES:
        raise Refused("this page serves %s, not %r" % (" and ".join(SERVES), tool))
    card = cards.BY_KEY.get(tool)
    if card is None:
        raise Refused("%s is not a card this install has" % tool)
    return card


def fields(tool):
    """The form's fields, which are the tool's card minus what its submitter does not read.

    Returned as the card returns them, defaults and all, so there is nothing here for the page
    and the canvas to disagree about: one description, read twice.
    """
    skip = CARD_ONLY.get(tool, ())
    return [s.as_json() for s in card_of(tool).settings if s.key not in skip]


def target_fields():
    """The target card's own fields, for the boxes above the form."""
    return [s.as_json() for s in cards.TARGET.settings]


def default_of(fs, key):
    return next((f["default"] for f in fs if f["key"] == key), None)


# --- the target --------------------------------------------------------------------------------

def _target_node(settings):
    return flowmod.Node("target", "target", settings)


def read_target(tool, body, staging):
    """Read a target the three ways the form offers it, and say what it holds.

    The fetch, the copy and every refusal are the target card's driver, called here exactly as
    the runner calls it. The structure is kept in the page's own folder and handed to the run as
    a file when the form is submitted, rather than fetched again: the RCSB answering twice is two
    files, and a person who has looked at the chains of one should get that one.
    """
    kind = (body.get("kind") or "pdb").strip()
    where = os.path.join(staging, secrets.token_hex(4))
    os.makedirs(where, exist_ok=True)
    pdb_id = ""
    if kind == "pdb":
        pdb_id = (body.get("id") or "").strip().upper()
        settings = {"source": "rcsb", "pdb_id": pdb_id}
        name = pdb_id.lower()
    elif kind == "file":
        raw = base64.b64decode(body.get("data") or "", validate=False)
        if not raw:
            raise Refused("that file arrived empty")
        if len(raw) > UPLOAD_CAP:
            raise Refused("that file is %.0f MB, and %d MB is the most the page takes. Put it in "
                          "your scratch and give the path instead"
                          % (len(raw) / 1e6, UPLOAD_CAP // 1024 // 1024))
        given = os.path.basename(body.get("filename") or "target.pdb")
        if not given.lower().endswith(".pdb"):
            raise Refused("the structure has to be a .pdb file: every tool here reads the "
                          "target by PDB columns, so an mmCIF would be copied in and then found "
                          "empty")
        path = os.path.join(where, "uploaded.pdb")
        with open(path, "wb") as handle:
            handle.write(raw)
        settings = {"source": "file", "path": path}
        name = os.path.splitext(given)[0]
    elif kind == "path":
        given = os.path.abspath(os.path.expanduser((body.get("path") or "").strip()))
        settings = {"source": "file", "path": given}
        name = os.path.splitext(os.path.basename(given))[0]
    else:
        raise Refused("a target comes from the RCSB, a file or a path, not %r" % kind)

    node = _target_node(settings)
    one = flowmod.Flow(name="target", nodes=[node])
    record = flowstate.State(where)
    try:
        drivers.submit(node, one, {}, where, record, say=lambda *_a: None)
    except drivers.NotWired as why:
        raise Refused(str(why))
    path = record.card("target").get("path") or ""
    if not os.path.isfile(path):
        raise Refused("the target could not be read into the page's folder")

    chains = PARSER[tool].chains_of(path)
    if not chains:
        raise Refused("no amino-acid chain was found in that file. Every tool here reads a "
                      "target by its ATOM records, so a structure with none is not a target any "
                      "of them can aim at")
    return {"name": safe_name(name, "target"), "path": path, "pdb_id": pdb_id,
            "chains": [{"id": chain, "n": len(numbers), "first": numbers[0], "last": numbers[-1]}
                       for chain, numbers in sorted(chains.items())],
            "residues": sum(len(numbers) for numbers in chains.values())}


def _chains_in(tool, path, asked):
    """{chain: [residue numbers]} for the chains asked for, and the ones that are not there."""
    present = PARSER[tool].chains_of(path)
    wanted = [c.strip() for c in (asked or "").split(",") if c.strip()]
    if not wanted:
        wanted = sorted(present)
    keep = {c: present[c] for c in wanted if c in present}
    return keep, [c for c in wanted if c not in present], present


def preview(tool, path, asked_chains, hotspots, binder_min, binder_max):
    """What the job will be told about the target, worked out the way the job works it out.

    This is the one thing the page can say that a flow drawn on a laptop cannot, and for BoltzGen
    it is the whole reason the page is worth having: its hotspots are positions in the chain, not
    the numbering the structure uses, and getting that wrong does not fail. It designs against a
    different patch of the surface for twelve hours.
    """
    keep, absent, _present = _chains_in(tool, path, asked_chains)
    out = {"chains": sorted(keep), "absent": absent, "missing": [], "refused": [], "lines": []}
    if not keep:
        return out
    if tool == "rfdiffusion":
        named = rfdiffusion_design.residues(hotspots, sorted(keep)[0])
        out["missing"] = [one for one in named
                          if int(one[1:]) not in keep.get(one[0], [])]
        # RFdiffusion has nowhere to put an insertion code, so a hotspot carrying one is refused
        # against the box rather than dropped on the way into a job that would then aim a residue
        # away from the site. Its own words for it, from its own helper, and BoltzGen's below are
        # the same words: RFdiffusion3 is the one of the three that keeps a code.
        out["refused"] = rfdiffusion_design.coded_why(hotspots)
        out["contig"] = rfdiffusion_design.contig(keep, int(binder_min), int(binder_max))
        out["hotspots"] = named
        out["lines"] = ["contigmap.contigs=%s" % out["contig"]]
        if named:
            out["lines"].append("ppi.hotspot_res=[%s]" % ",".join(named))
    elif tool == "rfd3":
        # RFdiffusion3's own contig and its own hotspot list, from its own helper, not
        # BoltzGen's: the preview would show positions in the chain, which RFD3 does not use,
        # and no contig at all.
        #
        # Its spans come from chains_of and segments rather than its own spans_of: that one reads
        # the structure with biotite, which the launcher's environment has not got, and these two
        # are written against PDB columns for exactly this reason. The same stretches either way.
        asked_order = [c.strip() for c in (asked_chains or "").split(",") if c.strip()]
        order = [c for c in (asked_order or sorted(keep)) if c in keep]
        spans = {c: rfd3_design.segments(keep[c]) for c in order}
        named = rfd3_design.hotspots_of({"hotspots": hotspots, "chains": ",".join(order)})
        out["missing"], out["refused"] = rfd3_design.absent(named, spans)
        out["contig"] = rfd3_design.contig_of(
            {"binder_min": int(binder_min), "binder_max": int(binder_max), "target": path},
            order, spans)
        out["hotspots"] = sorted(named)
        out["lines"] = ["contig: %s" % out["contig"]]
        if named:
            out["lines"].append('select_hotspots: %s, each as a whole residue ("ALL")'
                                % ", ".join(sorted(named)))
            out["lines"].append("infer_ori_strategy: hotspots, so the binder grows off the face "
                                "they are on")
        else:
            out["lines"].append("infer_ori_strategy: com, so the binder may grow anywhere on "
                                "the target")
    else:
        # BoltzGen has nowhere to put an insertion code either: its binding residues are
        # positions in the chain, and the box is read here as a chain, a number and a range, so
        # a coded hotspot was dropped on the way into a twelve-hour run aimed at the residue of
        # the same number. Its own words for it, from its own helper, which are RFdiffusion's.
        out["refused"] = boltzgen_design.coded_why(hotspots)
        asked = boltzgen_design.wanted(hotspots, sorted(keep)[0])
        binding = {}
        for chain, numbers in sorted(asked.items()):
            if chain not in keep:
                out["missing"] += ["%s%d" % (chain, n) for n in numbers]
                continue
            got, absent_here = boltzgen_design.positions(keep[chain], numbers)
            out["missing"] += ["%s%d" % (chain, n) for n in absent_here]
            if got:
                binding[chain] = got
                out["lines"].append(
                    "chain %s: %s in the file are positions %s (the chain is numbered %d to %d)"
                    % (chain, ",".join(str(n) for n in sorted(set(numbers))),
                       ",".join(str(p) for p in got), keep[chain][0], keep[chain][-1]))
        out["binding"] = binding
        out["sequence"] = "%d..%d" % (int(binder_min), int(binder_max))
        out["lines"].append('the binder is asked for as "%s"' % out["sequence"])
    return out


# --- the form ----------------------------------------------------------------------------------

def _settings_from(tool, body):
    """The tool card's settings, as the form sent them, in the types the card says they are."""
    out = {}
    for field in fields(tool):
        key, kind = field["key"], field["kind"]
        if key not in body:
            continue
        value = body[key]
        if kind == "yesno":
            out[key] = bool(value) if not isinstance(value, str) else value.lower() in (
                "1", "true", "yes", "on")
        elif kind == "number":
            # None is what a field means by "whatever the tool would do", and the form carries it
            # as one when it was filled from the defaults. str() would make it the word None,
            # which is not a number and is not empty either, so it would reach the driver as text.
            text = "" if value is None else str(value).strip()
            if text == "":
                # A field whose default is "whatever the tool would do" has no number to fall
                # back to, and says so with no default at all. Empty is how that is carried
                # everywhere else - the canvas stores it, the drivers skip it - so it is what
                # goes here too, rather than a null that later has to be guarded against.
                out[key] = field["default"] if field["default"] is not None else ""
                continue
            try:
                out[key] = int(text) if re.match(r"^-?\d+$", text) else float(text)
            except ValueError:
                # Left as the text it was: the flow's own check says what is wrong with it, in
                # the same words it would use for a card filled in on the canvas.
                out[key] = text
        else:
            out[key] = str(value).strip()
    return out


def _target_settings(body):
    """The target card's settings for a form that has already read its structure."""
    return {"source": "file", "path": (body.get("path") or "").strip(),
            "chains": (body.get("chains") or "").strip(),
            "hotspots": (body.get("hotspots") or "").strip(),
            "binder_min": _int(body.get("binder_min"), 70),
            "binder_max": _int(body.get("binder_max"), 100)}


def _int(value, fallback):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def build(tool, body):
    """The flow this form describes: a target, the tool, and the link between them.

    A flow of two cards rather than one, because the target is a card: fetching the structure,
    copying it into the run and refusing a bad id are all the target card's driver, and the page
    has no business doing any of it a second way.
    """
    settings = _target_settings(body)
    target = _target_node(settings)
    node = flowmod.Node(tool, tool, _settings_from(tool, body))
    name = safe_name(body.get("name") or "", "") or safe_name(
        os.path.splitext(os.path.basename(settings["path"]))[0], "run")
    one = flowmod.Flow(name=name, nodes=[target, node],
                       edges=[flowmod.Edge("target", "target", tool, "target")])
    return one, target, node


def check(tool, body, roots=()):
    """What is wrong with the form, what Slurm will be asked for, and what is worth saying.

    The problems are the flow's own check(), word for word, so the page refuses what the canvas
    refuses. The per-field messages beside them are the page's alone: they are the checks that
    need the structure open, which is the one thing a flow drawn on a laptop cannot do.
    """
    card_of(tool)
    path = (body.get("path") or "").strip()
    bad, notes = {}, []
    nothing = {"problems": [], "plan": {}, "notes": notes, "preview": {}}
    if not path:
        # Said against the target box rather than in the list of problems: the box is where a
        # person is looking, and the flow's own words for it name a file when they have not got
        # as far as choosing how to give one.
        bad["target"] = "give a target first: a PDB id, a .pdb file or a path on the cluster"
        return dict(nothing, fields=bad)
    if not os.path.isfile(path):
        bad["target"] = "the structure is no longer at %s: read it again" % path
        return dict(nothing, fields=bad)

    # Every number on the form, not only the ones the flow will not start without. The flow
    # checks those; an optional one with a typo in it would otherwise reach the driver, which
    # calls int() on it and fails in a way nobody can act on.
    for field in fields(tool):
        if field["kind"] != "number":
            continue
        value = body.get(field["key"], "")
        # None is the answer a field gives when empty means "whatever the tool would do", and it
        # arrives here as one when the form was filled from the defaults. str() would turn it
        # into the word None and refuse it as not a number.
        text = "" if value is None else str(value).strip()
        if text and not re.match(r"^-?\d+(\.\d+)?$", text):
            bad[field["key"]] = "%s wants a number" % field["label"].lower()
    for key in ("binder_min", "binder_max"):
        if not re.match(r"^\d+$", str(body.get(key, "")).strip()):
            bad[key] = "a residue count, as a whole number"
    if bad.get("binder_min") or bad.get("binder_max"):
        # Nothing below can be worked out without them: the contig map, the specification and
        # the plan all carry the length, so there is nothing honest to show until they are numbers.
        return dict(nothing, fields=bad)

    lo, hi = _int(body.get("binder_min"), 70), _int(body.get("binder_max"), 100)
    one, _target, node = build(tool, body)
    problems = one.check()
    try:
        seen = preview(tool, path, body.get("chains"), body.get("hotspots"), lo, hi)
    except SystemExit as why:
        # A helper refuses a target it cannot describe by raising SystemExit, which is how it
        # says so to the job it was written for: a target in more stretches of resolved residues
        # than RFdiffusion3 has chain letters, a chain numbered backwards. Read on the page, that
        # is an answer about the target rather than a reason to end the request.
        bad["target"] = str(why)
        return dict(nothing, fields=bad)
    if seen["absent"]:
        bad["chains"] = "the target has no chain %s" % ", ".join(seen["absent"])
    elif not seen["chains"]:
        bad["chains"] = "name at least one chain of the target"
    if seen.get("refused"):
        # The tool's own words for a hotspot it will not take, said against the box the number
        # was typed into rather than left for a job twelve hours later to say.
        bad["hotspots"] = " ".join(seen["refused"][:8])
    elif seen["missing"]:
        bad["hotspots"] = ("the target has no residue %s. Hotspots are residue numbers of this "
                           "structure, which is numbered as the chips above say"
                           % ", ".join(seen["missing"][:8]))

    walltime = str(body.get("walltime") or "").strip()
    if walltime and not WALLTIME.match(walltime):
        bad["walltime"] = "a walltime Slurm takes: 12:00:00, or 2-00:00:00 for two days"
    elif "-" in walltime and _int(walltime.split("-")[0], 0) > MAX_DAYS:
        bad["walltime"] = "the gpu partition allows %d days at most" % MAX_DAYS

    out = (body.get("out") or "").strip()
    if out:
        home = os.path.realpath(os.path.expanduser("~"))
        full = os.path.realpath(os.path.abspath(os.path.expanduser(out)))
        if full == home or full.startswith(home + os.sep):
            bad["out"] = ("home is 10 GB and 10 000 files on Grace, and one design run writes "
                          "more than that: put the run in your scratch")
        elif roots and not any(full == os.path.realpath(r)
                               or full.startswith(os.path.realpath(r) + os.sep) for r in roots):
            # Allowed, because somebody naming a folder usually means it. Said, because the Runs
            # screen reads only the folders the page was started with, so a run put outside them
            # is queued and then invisible here, which is a worse surprise than this sentence.
            notes.append("that folder is outside the ones this page reads, so the run will be "
                         "queued but will not appear on the Runs screen")

    gpu, partition = drivers.gpu_asked(node.settings)
    plan = {"gpu": gpu, "partition": partition,
            "cpus": drivers.DESIGN_RESOURCES[tool]["cpus"],
            "mem": drivers.DESIGN_RESOURCES[tool]["mem"],
            "walltime": walltime or "12:00:00",
            "account": str(body.get("account") or "").strip(),
            "designs": _int(body.get("designs"), default_of(fields(tool), "designs") or 10)}

    if hi - lo > 60:
        notes.append("a binder length as wide as %d to %d residues makes designs that are hard "
                     "to compare with each other" % (lo, hi))
    if tool == "boltzgen" and gpu == "a40":
        notes.append("BoltzGen wants 40 GB of card and an a40 has 48, so it fits, but its "
                     "analysis step has been reported running out at a hundred designs")
    # The driver refuses this too, which is what stops a flow; said here as well so that it is
    # said against the menu it was chosen from rather than after the target has been fetched.
    if tool == "boltzgen" and str(body.get("protocol") or "") == "protein-small_molecule":
        bad["protocol"] = ("this protocol designs against the target's own ligand, and a design "
                           "run is given the receptor cleaned to its polymer residues, so there "
                           "is no ligand left to design against")
    if tool == "rfd3":
        passes = _int(body.get("batches"), 1)
        if passes > 1:
            notes.append("%d designs in all: %d in each pass, over %d passes"
                         % (plan["designs"] * passes, plan["designs"], passes))
    if tool == "rfdiffusion":
        sequences = plan["designs"] * _int(body.get("seqs_per_backbone"), 8)
        if sequences > 400:
            notes.append("that is %d sequences to design, and ProteinMPNN is run once per "
                         "backbone: a walltime longer than the default is worth setting"
                         % sequences)
    return {"problems": problems, "fields": bad, "plan": plan, "notes": notes, "preview": seen}


# --- queueing ----------------------------------------------------------------------------------

def run_folder(root, name):
    """Where this submission goes: the run root, the name, and the minute it was asked for.

    The minute rather than a counter, because a person submitting the same target twice in an
    afternoon should be able to tell the two apart in a folder listing a month later.
    """
    stamp = time.strftime("%Y%m%d-%H%M")
    where = os.path.join(root, "%s_%s" % (name, stamp))
    n = 2
    while os.path.exists(where):
        where = os.path.join(root, "%s_%s_%d" % (name, stamp, n))
        n += 1
    return where


def run_home(out, name):
    """(the folder a run given the Run folder `out` is written to, what to say about it).

    The folder itself, when no run is in it yet. When one already is - two runs given one folder,
    which is how a person keeps a batch together - the new run goes in a folder of its own inside
    it, named after the run. Written straight into the same folder, the second run's record took
    the place of the first's: both jobs were in the queue and the Runs screen showed one.
    """
    full = os.path.abspath(os.path.expanduser(out))
    held = ""
    for marker in ("submission.json", "flow.json"):
        path = os.path.join(full, marker)
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    held = str(json.load(handle).get("name") or "") or "a run"
            except (OSError, ValueError, AttributeError):
                held = "a run"
            break
    if not held:
        return full, ""
    inner, n = os.path.join(full, name), 2
    while os.path.exists(inner):
        inner = os.path.join(full, "%s_%d" % (name, n))
        n += 1
    return inner, ("%s already holds %s, so this run goes in a folder of its own inside it: %s"
                   % (full, held if held == "a run" else "the run " + held, inner))


def queue(tool, body, roots, dry_run=False, say=print):
    """Queue the run, card by card, the way hope_flow/run.py queues a flow.

    Returns what was written down beside the run. The order is the runner's: the target card
    first, which puts the structure in the run's own folder, then the adapter that carries it
    across, then the tool's driver, which writes the job and queues it.
    """
    checked = check(tool, body, roots)
    if checked["problems"] or checked["fields"]:
        return {"problems": checked["problems"], "fields": checked["fields"]}

    where = (body.get("out") or "").strip()
    one, target, node = build(tool, body)
    where = run_home(where, one.name)[0] if where else run_folder(roots[0], one.name)
    os.makedirs(where, exist_ok=True)
    node.settings["runs"] = where
    record = flowstate.State(where)
    with open(os.path.join(where, "flow.json"), "w", encoding="utf-8") as handle:
        handle.write(one.dumps())

    # Everything but sbatch, when it is a dry run: the structure is fetched, the plan is written
    # and the job script with it, so the page can be proved without asking for an A100 for twelve
    # hours. The driver owns the flag; this only puts it back, so a dry run cannot leave the
    # server unable to queue anything afterwards.
    was, drivers.DRY_RUN = drivers.DRY_RUN, bool(dry_run)
    try:
        drivers.submit(target, one, {}, where, record, say=say)
        carried = adapters.carry(one, record, one.edges[0], where)
        job, jobs = drivers.submit(node, one, {"target": [carried]}, where, record, say=say)
        jobs = [j for j in jobs if j]
        record.set(node.id, state=flowstate.QUEUED, rundir=job, jobs=jobs,
                   note="dry run: the job was written and not queued" if dry_run else "")
    except drivers.NotWired as why:
        record.set(node.id, state=flowstate.FAILED, note=str(why)[:400])
        record.write()
        raise Refused(str(why))
    finally:
        drivers.DRY_RUN = was
    record.write()

    kept = {
        "tool": tool, "name": one.name, "user": os.environ.get("USER") or "",
        "submitted": time.strftime("%Y-%m-%d %H:%M:%S"),
        "job_id": jobs[0] if jobs else "", "dry_run": bool(dry_run),
        "path": where, "rundir": job,
        "target": {"path": carried["path"], "pdb_id": (body.get("pdb_id") or "").strip().upper(),
                   "chains": carried["chains"], "hotspots": carried["hotspots"],
                   "binder": [carried["binder_min"], carried["binder_max"]]},
        "asked": dict(node.settings, **{"runs": where}),
        "plan": checked["plan"],
    }
    with open(os.path.join(where, "submission.json"), "w", encoding="utf-8") as handle:
        json.dump(kept, handle, indent=2)
        handle.write("\n")
    return {"run": kept}
