"""What the form was given, what it would do, and queueing it.

By Aadhil Haq

Three things are asked of this module, in the order the page asks them:

* ``check``  - what is wrong with what has been typed, said field by field, with nothing queued.
  The page calls it on every change, so it reads no files and queues nothing.
* ``told``   - what the job would be told: the sequence, the settings and the script itself. Shown
  before Submit, because a GPU booking is a thing somebody should be able to read before they
  spend it.
* ``queue``  - phase one here on the login node, then phase two on the queue.

The phases are the whole shape of a run (hope_labs/ensemble/__init__.py): the alignment is
fetched where there is a network, and the sampling runs where there is a card and no network at
all. A run that reached for its alignment from the job would sit on a socket that cannot open,
holding a card, until its walltime ran out. That happened once, on the install's own check, which
is why the check is given an alignment too.
"""
from __future__ import annotations

import glob
import json
import os
import time

from . import MIN_SAMPLES, SERVES
from . import job as jobmod
from .. import structure as structuremod
from .. import images as imagesmod
from ..folding import sites as sitesmod
#: The same cleaner and the same run-folder rules the design and folding pages use. One name for
#: a run folder across the pages means Results reads them all the same way.
from ..pages.submit import run_home, safe_name

#: What may be copied out of a finished run, and what each means. The samples and the topology
#: are the ensemble itself; the shapes are the eight-or-so structures somebody puts in a slide.
COPIES = ("run", "ensemble", "shapes")


def fields(tool="bioemu", site=None):
    """Every control the form offers, as the page's rail reads it."""
    return [one.as_json() for one in jobmod.settings(tool, site)]


def _asked(tool, body, site):
    """The settings the job will be given: what was sent, held to what each setting allows."""
    out = {}
    for one in jobmod.settings(tool, site):
        value = body.get(one.key, one.default)
        if one.kind == "number" and str(value or "").strip() != "":
            try:
                value = int(float(value))
            except (TypeError, ValueError):
                value = one.default
            if one.least is not None:
                value = max(one.least, value)
            if one.most is not None:
                value = min(one.most, value)
        elif one.kind == "yesno":
            value = value not in (None, "", False, "false", "no", 0, "0")
        elif one.kind == "choice" and one.choices and value not in one.choices:
            value = one.default
        out[one.key] = value
    return out


#: What a structure may arrive as. An mmCIF is in the list because the lab's own folding runs
#: write one, and a person sampling a chain they have just folded should not have to convert it.
STRUCTURE_SUFFIXES = (".pdb", ".ent", ".cif", ".mmcif")
#: The most an uploaded structure may be. A protein is kilobytes; ten megabytes is a ribosome,
#: and a chain of one is not something this page can sample anyway.
UPLOAD_CAP = 10 * 1024 * 1024
#: Where a four-letter code is fetched from. The entry rather than an assembly, and the mmCIF
#: first: this page reads a sequence off the file, and an mmCIF carries every chain of a large
#: structure where a PDB runs out of columns. The design pages' own fetch does more than this
#: (hope_flow/drivers.py) because it has to hand a receptor to a model; this only has to read.
#: HOPE_LABS_RCSB names another server laid out the same way, which is how the page's browser test
#: fetches a code on a compute node, where there is no network to reach the real one.
RCSB = os.environ.get("HOPE_LABS_RCSB") or "https://files.rcsb.org/download/%s"


def read_structure(body, staging, say=print):
    """A structure, however the form offers it, and the chains in it with their sequences.

    Three ways in, and all three end in a file this page holds: a path on the cluster, a file sent
    up from the browser, and a four-character code fetched here on the login node. The file is
    kept, because a run given a structure measures its samples against it (analyse.py).
    """
    kind = (body.get("kind") or "path").strip()
    os.makedirs(staging, exist_ok=True)
    if kind == "path":
        path = os.path.abspath(os.path.expanduser(str(body.get("path") or "").strip()))
        if not path:
            raise jobmod.Refused("name a structure to read.")
        if not os.path.isfile(path):
            raise jobmod.Refused("there is no file at %s." % path)
    elif kind == "file":
        import base64
        raw = base64.b64decode(body.get("data") or "", validate=False)
        if not raw:
            raise jobmod.Refused("that file arrived empty.")
        if len(raw) > UPLOAD_CAP:
            raise jobmod.Refused(
                "that file is %.0f MB, and %d MB is the most this page takes. Put it in your "
                "scratch and give the path instead." % (len(raw) / 1e6, UPLOAD_CAP // 1024 // 1024))
        given = os.path.basename(str(body.get("filename") or "structure.pdb"))
        suffix = os.path.splitext(given)[1].lower()
        if suffix not in STRUCTURE_SUFFIXES:
            raise jobmod.Refused("a structure is read from %s; %s is not one of them."
                                 % (", ".join(STRUCTURE_SUFFIXES), suffix or "a file with no suffix"))
        path = os.path.join(staging, safe_name(os.path.splitext(given)[0], "structure") + suffix)
        with open(path, "wb") as handle:
            handle.write(raw)
    elif kind == "pdb":
        code = str(body.get("id") or "").strip().upper()
        if len(code) != 4 or not code.isalnum():
            raise jobmod.Refused("a PDB code is four characters, like 1YCR.")
        path = _fetch_code(code, staging, say=say)
    elif kind == "run":
        path = _structure_of_run(str(body.get("path") or "").strip())
    else:
        raise jobmod.Refused("a structure comes from a path, a file, a PDB code or a run, "
                             "not %r." % kind)
    try:
        chains = structuremod.read(path)
    except structuremod.Unreadable as why:
        raise jobmod.Refused(str(why))
    pick = structuremod.chain_named(chains, "")
    return {
        "path": path,
        "name": safe_name(os.path.splitext(os.path.basename(path))[0], "structure"),
        "chains": chains,
        "chain": (pick or {}).get("id", ""),
        "sequence": (pick or {}).get("sequence", ""),
        "why_not": {one["id"]: structuremod.sampleable(one) for one in chains},
    }


def _fetch_code(code, staging, say=print):
    """One RCSB entry, fetched here on the login node, as mmCIF and then as PDB."""
    import urllib.error
    import urllib.request
    last = ""
    for suffix in (".cif", ".pdb"):
        where = os.path.join(staging, code.lower() + suffix)
        try:
            say("fetching %s from the RCSB" % (code + suffix))
            with urllib.request.urlopen(RCSB % (code + suffix), timeout=60) as answer:
                raw = answer.read()
            if raw:
                with open(where, "wb") as handle:
                    handle.write(raw)
                return where
        except (urllib.error.URLError, OSError, ValueError) as why:
            last = str(why)
    raise jobmod.Refused(
        "%s could not be fetched from the RCSB (%s). This page is on a login node, which is the "
        "one machine here that can reach it; if the code is right, try again, or download the "
        "file and give its path." % (code, last or "no reason given"))


def _structure_of_run(run):
    """The structure a previous run left, for sampling the same chain again or a variant of it.

    The structure browser's own answer first (hope_labs/picker.py), so the run a person pressed
    "Use this run's structure" on gives the file the browser named: for one of this page's runs
    the structure it was given, and for a HOPE-MD run its complex. Then the older search, for a
    folder laid out some other way.
    """
    run = os.path.abspath(os.path.expanduser(run or ""))
    if not os.path.isdir(run):
        raise jobmod.Refused("there is no run folder at %s." % run)
    from .. import picker as pickermod
    found = pickermod.run_structure(run)
    if found:
        return found["structure"]
    for rel in (os.path.join("analysis", "ensemble.pdb"), os.path.join("out", "topology.pdb"),
                os.path.join("inputs", "given.pdb"), os.path.join("inputs", "given.cif"),
                "stripped.complex.prmtop"):
        path = os.path.join(run, rel)
        if os.path.isfile(path) and not path.endswith(".prmtop"):
            return path
    found = sorted(glob.glob(os.path.join(run, "**", "*.pdb"), recursive=True))[:1]
    if found:
        return found[0]
    raise jobmod.Refused("that run holds no structure this page can read.")


def molecules_from(body):
    """The molecules this form is asking for, as a list, however the form sent them.

    One card is one molecule and one molecule is one job: a sequence and the variants of it are
    sampled separately and compared afterwards, which is the only honest way to ask what a
    mutation does to a chain's flexibility. The folding pages take a table of chains the same way
    and for the same reason.

    A body carrying a bare `sequence` and no molecules is one molecule, because that is what a
    flow's card sends (hope_flow/drivers.py) and what every run queued before this existed sent.
    Normalizing here rather than in two places is what keeps the driver working untouched.
    """
    got = body.get("molecules")
    if isinstance(got, list) and got:
        return [one for one in got if isinstance(one, dict)]
    return [{"name": body.get("name") or "", "sequence": body.get("sequence") or "",
             "structure": body.get("structure") or "", "chain": body.get("chain") or "",
             "mutations": body.get("mutations") or ""}]


def read_molecule(one, n=0):
    """(what it is, what is wrong with it) for one molecule card.

    A structure is read here rather than trusted: the page shows the chains it found and which
    one it would sample, and this is the same reading, so what is submitted is what was shown.
    The sequence a structure gives can still be mutated, which is what makes a variant a variant
    rather than a second thing to paste.
    """
    bad = {}
    said = []
    name = safe_name(one.get("name") or "", "")
    sequence = str(one.get("sequence") or "")
    structure = str(one.get("structure") or "").strip()
    chain = str(one.get("chain") or "").strip()
    first, from_chain = 1, None
    if structure:
        if not os.path.isfile(structure):
            bad["structure"] = "there is no file at %s." % structure
        else:
            try:
                chains = structuremod.read(structure)
            except structuremod.Unreadable as why:
                bad["structure"] = str(why)
                chains = []
            if chains:
                from_chain = structuremod.chain_named(chains, chain)
                wrong = structuremod.sampleable(from_chain)
                if wrong:
                    bad["chain"] = wrong
                else:
                    # The chain's own sequence, not whatever is in the box: a structure was given
                    # so that the page reads the sequence rather than the person.
                    sequence = from_chain["sequence"]
                    first = from_chain["first"]
                    if from_chain["gaps"]:
                        # Said rather than refused: nothing on the form can fill a gap, and the
                        # sentence says what will be sampled. Refused, it was a Submit that
                        # would not press with nothing marked to say why.
                        said.append("chain %s has %d gap(s) in it. The sequence is sampled as "
                                    "the residues that are there, with nothing filled in, so the "
                                    "samples are of a chain with those holes."
                                    % (from_chain["id"], from_chain["gaps"]))
    mutations, did, wrong = [], [], []
    if str(one.get("mutations") or "").strip():
        if not sequence:
            bad["mutations"] = "there is no sequence to mutate yet."
        else:
            sequence, did, wrong = structuremod.mutate(sequence, first, one["mutations"])
            mutations = did
            if wrong:
                bad["mutations"] = " ".join(wrong)
    notes = []
    if "sequence" not in bad:
        try:
            sequence, notes = jobmod.check_sequence(sequence)
        except jobmod.Refused as why:
            bad["sequence"] = str(why)
    notes = said + list(notes)
    return {
        "name": name or _name_from(sequence),
        "sequence": sequence,
        "residues": len(sequence),
        "structure": structure,
        "chain": (from_chain or {}).get("id", chain),
        "first": first,
        "mutations": mutations,
        "notes": notes,
        "what": ("chain %s of %s" % ((from_chain or {}).get("id", chain),
                                     os.path.basename(structure)) if structure
                 else "a pasted sequence"),
    }, bad


def check(tool, body, roots=(), site=None, install=""):
    """What is wrong with this form, said field by field. Nothing is queued and nothing is read.

    Returns {"problems": [sentences], "fields": {key: {field: sentence}}, "name": ..., "plan": ...}
    in the shape the page's rail and the Submit button already read on the other pages. A
    molecule's own refusals are under its number, as the folding pages do it, so each card can
    show what is wrong with it rather than the page showing one sentence for all of them.
    """
    if tool not in SERVES:
        return {"problems": ["%s is not a tool this page serves" % tool], "fields": {}}
    site = site or sitesmod.here()
    problems, fields_bad, notes = [], {}, []
    molecules, bad_molecules = [], {}
    for n, one in enumerate(molecules_from(body)):
        read, bad = read_molecule(one, n)
        molecules.append(read)
        notes += read["notes"]
        if bad:
            bad_molecules[str(n)] = bad
    if bad_molecules:
        fields_bad["molecules"] = bad_molecules
        # The first molecule's own trouble is also reported against the sequence box, because a
        # single-molecule form has no card number to show it against.
        first_bad = bad_molecules.get("0") or {}
        for key in ("sequence", "structure", "chain", "mutations"):
            if key in first_bad and len(molecules) == 1:
                fields_bad[key if key != "structure" else "sequence"] = first_bad[key]
    seen = {}
    for n, one in enumerate(molecules):
        if one["sequence"] and one["sequence"] in seen:
            fields_bad.setdefault("molecules", {}).setdefault(str(n), {})["sequence"] = (
                "this is the same sequence as %s, so the two would be the same run twice. Give "
                "it a mutation, or take it off." % (molecules[seen[one["sequence"]]]["name"]))
        elif one["sequence"]:
            seen[one["sequence"]] = n
    sequence = molecules[0]["sequence"] if molecules else ""

    asked = _asked(tool, body, site)
    if str(asked.get("alignments")) == "file":
        where = str(asked.get("msa_file") or "").strip()
        if not where:
            fields_bad["msa_file"] = ("name the .a3m to read, or set Alignment back to searching "
                                      "for one.")
        elif not os.path.isfile(where):
            fields_bad["msa_file"] = "there is no file at %s." % where
        elif not where.endswith(".a3m"):
            fields_bad["msa_file"] = ("BioEmu reads an alignment as .a3m, and names it by its "
                                      "suffix: %s would be read as a sequence."
                                      % os.path.basename(where))
    # The number as it was typed rather than as the job will get it. _asked holds every number to
    # what its own setting allows, so a 2 here is a 10 by the time the job sees it; read back off
    # that, the page would quietly sample ten times what was asked for and say nothing.
    try:
        wanted = int(float(body.get("num_samples", asked.get("num_samples"))))
    except (TypeError, ValueError):
        wanted = 0
    if wanted and wanted < MIN_SAMPLES:
        fields_bad["num_samples"] = ("%d samples says nothing about a distribution; %d is the "
                                     "least worth asking for." % (wanted, MIN_SAMPLES))

    install = install or jobmod.INSTALLS[tool]
    if not os.path.isfile(install):
        problems.append("BioEmu's image is not at %s, so nothing can be queued from this page. "
                        "Open Set up and install it." % install)

    against = str(body.get("against") or "").strip()
    if against and not os.path.isdir(against):
        fields_bad["against"] = "there is no run folder at %s." % against

    # The name the run's folder will have: the Run name box, or the first molecule's own name (a
    # structure's file name, which a set of variants is then kept under), or one made from the
    # sequence. The box was read only for a set of molecules, so for one it did nothing.
    first_name = molecules[0]["name"] if molecules and molecules[0]["name"] else ""
    name = safe_name(body.get("name") or "", first_name or _name_from(sequence))
    return {"problems": problems, "fields": fields_bad, "name": name, "notes": notes,
            "sequence": sequence, "asked": asked, "molecules": molecules, "against": against,
            "plan": plan(tool, sequence, asked, site, molecules=molecules, against=against)}


def _name_from(sequence):
    """A name for a run nobody named: the length and the first few residues, which is enough to
    tell two runs apart in a list and short enough to read in one."""
    return "ens_%d_%s" % (len(sequence), (sequence[:6] or "seq").lower())


def plan(tool, sequence, asked, site=None, molecules=(), against=""):
    """What this run will do, in the words the page shows above Submit.

    Each phase twice over. ``step``, ``detail`` and ``time`` are the run plan's one line: a name a
    newcomer knows, where it happens in plain words, and roughly how long. ``what``, ``where``,
    ``why`` and ``cost`` are the same phase said in full, for the tooltip, the record a run keeps
    and the report. The one that costs a booking is kept apart from the one that does not: the
    alignment is a minute on a login node and the sampling is a card for as long as it takes.
    """
    site = site or sitesmod.here()
    gpu = site.card(asked.get("gpu"))
    card = (gpu or "GPU").upper()
    single = str(asked.get("alignments") or "fetch") == "file"
    molecules = list(molecules) or [{"sequence": sequence or "", "residues": len(sequence or "")}]
    many = len(molecules) > 1
    samples = int(asked.get("num_samples") or 0)
    longest = max([one.get("residues") or 0 for one in molecules] or [0])
    each = jobmod.minutes_for(samples, longest)
    drawing = ["draw %d structures of %s and read what they hold"
               % (samples, one.get("name") or "the sequence") for one in molecules]
    return {
        "phases": [
            {"step": "Alignment",
             "detail": ("read from your .a3m file" if single
                        else "fetched now, on this login node"),
             "time": "no wait" if single else ("about 1 min each" if many else "about 1 min"),
             "where": "here, on the login node",
             "what": ("read the alignment you named" if single else
                      "search for the alignment of %s"
                      % ("this sequence" if len(molecules) == 1
                         else "each of the %d sequences" % len(molecules))),
             "why": "BioEmu makes its embeddings from an alignment, and a GPU node on %s reaches "
                    "no network at all." % site.name,
             "cost": "a minute or two each, before anything is queued"},
            {"step": "Sampling",
             "detail": ("%s structures, queued on one %s" % (format(samples, ","), card)
                        if not many else
                        "%d jobs of %s structures, queued on %s" % (len(molecules),
                                                                   format(samples, ","), card)),
             "time": "about %d min%s" % (each, " each" if many else ""),
             "where": "the %s queue, on %s" % (site.partition_for(gpu), gpu or "a GPU"),
             "what": (drawing[0] if len(molecules) == 1
                      else "draw %d structures of each of the %d molecules, in a job each"
                           % (samples, len(molecules))),
             "why": "sampling is the whole cost of a run; reading the samples afterwards is "
                    "seconds, and happens in the same job so nothing is queued twice.",
             "cost": "about %d min each &middot; %d job(s) &middot; about %d min of %s time"
                     % (each, len(molecules), each * len(molecules), gpu or "GPU")},
        ] + ([{"step": "Comparison",
               "detail": "with %s, in the same job" % os.path.basename(against.rstrip(os.sep)),
               "time": "a few min",
               "where": "the same job, when the sampling is read",
               "what": "put each ensemble against %s" % os.path.basename(against),
               "why": "which sampled shapes that run visited, whether the two agree about which "
                      "residues move, and how far their spreads overlap.",
               "cost": "a few minutes of processor time, no card"}] if against else []),
        "residues": longest,
        "preset_minutes": {str(one["samples"]): jobmod.minutes_for(one["samples"], longest)
                           for one in jobmod.PRESETS},
        "samples": samples,
        "molecules": len(molecules),
        "jobs": len(molecules),
        "minutes": each * len(molecules),
        "walltime": str(asked.get("walltime") or ""),
        "card": gpu or "",
        "against": os.path.basename(against) if against else "",
    }


def told(tool, sequence, name, asked, root, run="", site=None):
    """What the job would be told, for the box above Submit: the sequence and the whole script.

    The script is built for the folder the run would go in, so what is shown is what will be
    queued rather than something like it.
    """
    run = run or os.path.join(jobmod.RUNS[tool], "%s_%s" % (name, time.strftime("%Y%m%d-%H%M")))
    try:
        script = jobmod.script_text(tool, root, run, name, dict(asked, sequence=sequence), site)
    except jobmod.Refused as why:
        script = "this run cannot be written: %s" % why
    return {"what": "the sequence, as it will be sampled",
            "text": "%s\n%s\n" % (">%s" % name, sequence),
            "command": script}


def run_folder(root, name):
    """Where this submission goes: the root, the name, and the minute it was asked for."""
    stamp = time.strftime("%Y%m%d-%H%M")
    where = os.path.join(root, "%s_%s" % (name, stamp))
    n = 2
    while os.path.exists(where):
        where = os.path.join(root, "%s_%s_%d" % (name, stamp, n))
        n += 1
    return where


def queue(tool, body, roots, install="", site=None, dry_run=False, say=print):
    """Phase one and then phase two, once per molecule: the alignment here, the sampling queued.

    One molecule is one run folder and one job. They are queued one after another and each is
    told about the others (set.json), so a finished run can be put against its own variants as
    well as against a simulation.

    Returns the first run's record under "run", as every caller already reads, and all of them
    under "runs": a form with one molecule on it behaves exactly as it did.
    """
    site = site or sitesmod.here()
    install = install or jobmod.INSTALLS[tool]
    root = imagesmod.root_of(install)
    checked = check(tool, body, roots, site, install)
    if checked["problems"] or checked["fields"]:
        return {"problems": checked["problems"], "fields": checked["fields"]}

    asked, molecules = checked["asked"], checked["molecules"]
    against = checked["against"]
    base = (body.get("out") or "").strip()
    # One name for the submission, and one folder per molecule under it when there is more than
    # one: a set of variants is read together, so they are kept together.
    label = safe_name(body.get("name") or "", checked["name"])
    folders = []
    for n, one in enumerate(molecules):
        name = (label if len(molecules) == 1
                else safe_name(one["name"] or "", "%s_%d" % (label, n + 1)))
        if base:
            folders.append(run_home(os.path.join(base, name) if len(molecules) > 1 else base,
                                    name)[0])
        elif len(molecules) > 1:
            folders.append(os.path.join(run_folder(roots[0], label), name))
        else:
            folders.append(run_folder(roots[0], name))

    queued, problems = [], []
    for n, (one, where) in enumerate(zip(molecules, folders)):
        name = os.path.basename(where.rstrip(os.sep))
        os.makedirs(os.path.join(where, "inputs"), exist_ok=True)
        os.makedirs(os.path.join(where, "cache"), exist_ok=True)
        if len(molecules) > 1:
            say("molecule %d of %d: %s" % (n + 1, len(molecules), one["name"]))

        # The sequence as it will be sampled, beside the run. A folder of samples a year later is
        # worth nothing without it, and the alignment in inputs/ is the tool's file rather than
        # the person's: it carries thousands of other sequences and not this run's name.
        with open(os.path.join(where, "sequence.fasta"), "w", encoding="utf-8") as handle:
            handle.write(">%s\n%s\n" % (one["name"], one["sequence"]))
        # The structure it came from, copied in rather than pointed at: a run that is read next
        # year must not depend on a file somebody has since tidied away, and the samples are
        # measured against it (analyse.py).
        kept_structure = ""
        if one["structure"] and os.path.isfile(one["structure"]):
            import shutil
            kept_structure = os.path.join(where, "inputs",
                                          "given" + os.path.splitext(one["structure"])[1].lower())
            shutil.copy2(one["structure"], kept_structure)

        alignment = ""
        if str(asked.get("alignments")) == "file":
            alignment = str(asked.get("msa_file") or "").strip()
            say("  the alignment you named is read as it stands: %s" % alignment)
        elif dry_run:
            say("  dry run: no alignment was fetched, so the script names the sequence itself")
        else:
            say("  phase one: the alignment, here on the login node")
            try:
                alignment = jobmod.fetch_alignment(root, where, one["sequence"], say=say)
            except jobmod.Refused as why:
                problems.append("%s: %s" % (one["name"], why))
                continue

        say("  phase two: the job, which runs with no network at all")
        told_job = dict(asked, sequence=one["sequence"], structure=kept_structure,
                        against=against)
        script = jobmod.script_text(tool, root, where, name, told_job, site)
        job_id = ""
        if dry_run:
            with open(os.path.join(where, "job.sbatch"), "w", encoding="utf-8") as handle:
                handle.write(script)
            say("  dry run: the script is written and nothing was queued")
        else:
            job_id = _submit(where, script, say=say)

        kept = {
            "tool": tool, "name": one["name"], "user": os.environ.get("USER") or "",
            "submitted": time.strftime("%Y-%m-%d %H:%M:%S"),
            "job_id": job_id, "dry_run": bool(dry_run), "path": where,
            "site": site.key, "install": install, "root": root,
            "sequence": one["sequence"], "residues": one["residues"],
            "structure": kept_structure, "chain": one["chain"],
            "mutations": one["mutations"], "from": one["what"],
            "asked": asked, "against": against,
            # Said as it happened, which on a dry run is that nothing was fetched.
            "alignments": ("not fetched: dry run" if dry_run and not alignment else
                           ("named: %s" % alignment if str(asked.get("alignments")) == "file"
                            else "fetched")),
            "input": "sequence.fasta",
            "results": os.path.join("analysis", "analysis.json"),
            "set": label if len(molecules) > 1 else "",
            "plan": checked["plan"],
        }
        with open(os.path.join(where, "submission.json"), "w", encoding="utf-8") as handle:
            json.dump(kept, handle, indent=2)
            handle.write("\n")
        queued.append(kept)

    # Each run told about the others, written after they all exist so every one of them names
    # every other. This is what lets a finished variant be put against its wild type without
    # anybody having to remember which folder that was.
    if len(queued) > 1:
        for one in queued:
            others = [{"name": o["name"], "path": o["path"], "job_id": o["job_id"],
                       "residues": o["residues"], "mutations": o["mutations"]}
                      for o in queued if o["path"] != one["path"]]
            with open(os.path.join(one["path"], "set.json"), "w", encoding="utf-8") as handle:
                json.dump({"set": label, "submitted": one["submitted"], "others": others},
                          handle, indent=2)
                handle.write("\n")
    if not queued:
        return {"problems": problems or ["nothing was queued"], "fields": {}}
    if problems:
        say("not everything was queued: " + "; ".join(problems))
    return {"run": queued[0], "runs": queued, "problems": problems}


def _submit(run, script, say=print):
    """Write the script into the run and queue it. The job's own folder is its working folder, so
    a relative path in the log is relative to the run."""
    import subprocess
    path = os.path.join(run, "job.sbatch")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(script)
    done = subprocess.run(["sbatch", "--parsable", path], capture_output=True, text=True, cwd=run)
    if done.returncode != 0:
        raise jobmod.Refused((done.stderr or done.stdout).strip() or "sbatch refused the job")
    job_id = done.stdout.strip().split(";")[0]
    say("queued as job %s" % job_id)
    return job_id


#: What each of those copies holds, as paths inside the run. "run" is the folder itself.
PARTS = {
    "ensemble": ("out/samples.xtc", "out/topology.pdb", "analysis/analysis.json",
                 "analysis/ensemble.pdb", "analysis/rg.dat", "analysis/rmsf.dat",
                 "sequence.fasta", "submission.json"),
    "shapes": ("analysis/ensemble.pdb", "analysis/analysis.json"),
}


def copy_out(run, dest, what="ensemble", say=print):
    """Copy a finished run, or part of it, to a folder on the cluster.

    On the cluster rather than through the browser, because the lab's work stays on Grace.
    Refused into home for the reason that is always the reason here: home is 10 GB and 10,000
    files on this cluster, and a run's caches alone are more files than anybody wants there.
    """
    import shutil
    if what not in COPIES:
        raise jobmod.Refused("there is nothing called %r to copy" % what)
    run = os.path.abspath(run)
    dest = os.path.abspath(os.path.expanduser(str(dest or "").strip()))
    if not str(dest or "").strip():
        raise jobmod.Refused("give a folder to copy into")
    home = os.path.realpath(os.path.expanduser("~"))
    if os.path.realpath(dest) == home or os.path.realpath(dest).startswith(home + os.sep):
        raise jobmod.Refused("home is 10 GB and 10,000 files on this cluster: copy it into your "
                             "scratch instead")
    if os.path.realpath(dest) == run or os.path.realpath(dest).startswith(run + os.sep):
        raise jobmod.Refused("that folder is inside the run, so copying it there would copy it "
                             "into itself")
    try:
        os.makedirs(dest, exist_ok=True)
    except OSError as why:
        raise jobmod.Refused("that folder cannot be made: %s" % why)

    name = os.path.basename(run.rstrip(os.sep))
    if what == "run":
        target = os.path.join(dest, name)
        if os.path.exists(target):
            raise jobmod.Refused("%s is there already: copy it somewhere else, or move that one "
                                 "aside" % target)
        shutil.copytree(run, target)
        say("copied the whole run to %s" % target)
        return {"where": target, "files": sum(len(f) for _d, _s, f in os.walk(target))}

    target = os.path.join(dest, "%s_%s" % (name, what))
    copied = 0
    for part in PARTS[what]:
        source = os.path.join(run, part)
        if not os.path.isfile(source):
            continue
        into = os.path.join(target, os.path.dirname(part))
        os.makedirs(into, exist_ok=True)
        shutil.copy2(source, os.path.join(into, os.path.basename(part)))
        copied += 1
    if not copied:
        raise jobmod.Refused("this run has written no samples yet, so there is nothing to copy")
    say("copied %d file%s to %s" % (copied, "" if copied == 1 else "s", target))
    return {"where": target, "files": copied}
