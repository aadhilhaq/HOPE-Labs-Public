"""MM-GBSA and binder strain for every design of a binder-design run.

By Aadhil Haq

RFdiffusion3, RFD4-Proteina and BoltzGen each hand back a binder placed on its target with every
atom drawn, and none of them says what the placement costs or gains in a force field. This puts
the two numbers on each design that the docking tools already carry, measured the same way:

    MM-GBSA        the complex after a short minimization with the target held, scored once by
                   MMPBSA.py: ff14SB, mbondi2 radii, GB-OBC(II) (igb 5) at 0.15 M salt
    binder strain  the binder alone in that frame, minus the same binder relaxed alone from it
                   (hope_labs/strain.py, the lab's one definition of a strain)

Both are for comparing the designs of one run with each other. Neither is a binding free energy,
neither reorders anything, and a design that could not be measured is left without a number and
says why, never given a zero, which would read as a design that cost nothing.

RFdiffusion's designs are not measured at all. RFdiffusion writes the binder as a backbone - every
residue a glycine of four atoms - and keeps ProteinMPNN's sequence beside it, so there are no side
chains to score. Building them first was tried on the four designs of a real run, and the strain
came out between 184 and 261 kcal/mol against 21 to 89 for the full-atom tools: the number
measured the building, not the design. So those designs say so, in the words BACKBONE_ONLY holds.

HOW ONE DESIGN IS MEASURED, as the prototype settled it on fourteen full-atom designs of three
tools (MM-GBSA -42 to -127 kcal/mol, strain 21 to 89, 10 to 14 minutes of one core each):

  1. The complex is read as the tool wrote it, PDB or mmCIF, by OpenMM's own readers, which are in
     every HOPE-MD environment; gemmi is not.
  2. Protein chains and heavy atoms only, the target's chains first and the binder's last, which
     is the order MMPBSA.py and the strain module both expect. A stretch of a chain with a hole in
     it is written as two, so tleap does not draw a bond across the hole, and two cysteines whose
     sulfurs touch are bonded as a disulfide rather than left to push each other apart.
  3. PDBFixer adds the heavy atoms a residue is missing. It is not asked to build missing loops:
     a loop the tool did not draw is not part of the design.
  4. tleap builds the complex, the target and the binder with ff14SB and mbondi2.
  5. sander minimizes the complex for 1000 steps (500 of steepest descent) in GB-OBC(II), with the
     target's heavy atoms held at 5 kcal/mol/A^2, so the binder settles onto a target that stays
     where the tool put it.
  6. MMPBSA.py scores that one frame, igb 5 at 0.15 M, and its DELTA TOTAL is the MM-GBSA.
  7. The strain module measures the binder on the same frame.

The work is done in a temporary folder, because the inode quota rather than the space is what
runs out on scratch, and only what the numbers can be checked against is kept, in
scores/<design>/: the three topologies, the minimized frame, MMPBSA.py's input and output, and
the strain's own record (KEEP). The summary is scores.json beside designs.json, keyed by design;
designs.json itself is never written here, because the flows read it.

A design is measured again only when it has no current score: none at all, one from another
VERSION of this protocol, one made from a different structure file (the fingerprint), or one that
failed for a reason that could pass (no AmberTools found, a job cut short). A design that failed on
its own account - tleap refused it, the minimization blew up - keeps its reason and is not tried
again, so running this twice costs nothing the second time.

    python -m hope_labs.designscores RUN [RUN ...] [--workers N] [--force]

RUN is a run folder or the design job's folder inside it. It runs in HOPE-MD's environment, on a
compute node: the design pages queue it as a job (queue()), chained behind the design job for a new
run and on its own for a run made before this existed.

The reading half of this module - read_scores, cell, state, the job's text - is the standard
library only, because the design pages and the reports import it in a process that has nothing
else. OpenMM, PDBFixer and the vendored strain module are imported only where a design is measured.
"""
from __future__ import annotations

import argparse
import glob
import gzip
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

#: The protocol's version. Raising it measures every design again, rather than mixing two
#: protocols in one column.
VERSION = 1

#: The tools whose designs are measured: each writes the binder with every atom.
SCORED = ("rfd3", "rfd4", "boltzgen")
#: The tools whose designs are a backbone and nothing else, and what is shown where their numbers
#: would be.
BACKBONE = ("rfdiffusion",)
BACKBONE_ONLY = "backbone only: no side chains to measure"
#: What an empty cell says on a design nothing has measured yet.
NOT_YET = "not measured yet"
#: What a value says when the structure it was measured on carries another sequence than the one
#: the table shows beside it.
OTHER_SEQUENCE = ("measured on the structure as the tool wrote it, whose binder carries the "
                  "tool's own sequence rather than the one shown here")

#: The files, beside designs.json.
SUMMARY = "scores.json"
FOLDER = "scores"
JOBS = "jobs.json"
SCRIPT = "scores.sbatch"
#: The prefix every kept file of a design shares, which is how the strain module finds them.
PREFIX = "design"
#: What a design's folder keeps once it is measured; everything else the tools wrote goes with the
#: temporary folder, MMPBSA.py's _MMPBSA_ files included.
KEEP = tuple(PREFIX + tail for tail in ("_com.parm7", "_rec.parm7", "_lig.parm7", "_min.rst7",
                                         "_mmpbsa.in", "_mmgbsa.dat", "_strain.json"))

#: The minimization and the scoring, as step 5 and 6 above give them.
MIN_STEPS = 1000
MIN_STEEPEST = 500
RESTRAINT = 5.0
IGB = 5
SALT = 0.150

#: Two residues whose C and N are further apart than this are not bonded: the chain has a hole.
PEPTIDE_BOND = 2.0
#: Two cysteine sulfurs closer than this are a disulfide (2.05 A when bonded).
DISULFIDE = 2.5

#: How long each program may take on one design before it is given up on, in seconds.
TIMEOUTS = {"tleap": 600, "sander": 3600, "mmpbsa": 1800}

#: One line recorded with every score, saying how it was made.
METHOD = ("single-structure MM-GBSA: ff14SB with mbondi2 radii, %d steps of minimization in "
          "GB-OBC(II) with the target's heavy atoms held at %g kcal/mol/A^2, then MMPBSA.py on "
          "that frame, igb %d at %g M; binder strain by hope_labs/strain.py on the same frame"
          % (MIN_STEPS, RESTRAINT, IGB, SALT))

#: The two columns, as every table that shows them heads them. One wording for the design page,
#: its report and a flow's report, so the three cannot describe one number three ways.
LABELS = {"mmgbsa": "MM-GBSA (kcal/mol)", "binder_strain": "Binder strain (kcal/mol)"}
HELP = {
    "mmgbsa": "Single-structure MM-GBSA of the designed complex after a short minimization with "
              "the target held: ff14SB, GB-OBC(II), 0.15 M salt. For comparing designs within a "
              "run; it is not a binding free energy.",
    "binder_strain": "What the binder pays to hold its designed shape: its energy in the complex, "
                     "minus the same binder relaxed on its own from that shape (implicit solvent, "
                     "GB-OBC(II)). Lower is easier. It grows with size, so compare designs of "
                     "similar length. Not part of any ranking.",
}

#: The one measure the Add measures dialog offers on the design pages, in its words.
MEASURE = {"key": "scores", "name": "MM-GBSA and binder strain",
           "what": "Each design's binding energy after a short minimization, and what its binder "
                   "pays to hold its shape. About a quarter of an hour of one core a design."}

#: What the scoring job asks Slurm for. The cores are shared out one design each, so a run of
#: fewer designs than this asks for fewer; the walltime is two quarter-hours a design over the
#: cores, and half an hour for the job itself (walltime()).
MOST_CORES = 16
MINUTES_A_DESIGN = 15
MINUTES_OVER = 30
GB_A_CORE = 2

#: The states a scoring job has not come back from.
LIVE = ("PENDING", "RUNNING", "CONFIGURING", "COMPLETING", "REQUEUED", "RESIZING", "SUSPENDED")

#: The amino acids ff14SB has a residue for, under the names PDBFixer knows them by; the
#: protonation and bonding variants are given back as their parent so tleap chooses as it always
#: does (HIS as HIE) and the disulfides are found from the structure.
AMINO = {"ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE", "LEU", "LYS", "MET",
         "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL"}
AS_PARENT = {"HID": "HIS", "HIE": "HIS", "HIP": "HIS", "HSD": "HIS", "HSE": "HIS", "HSP": "HIS",
             "CYX": "CYS", "CYM": "CYS", "ASH": "ASP", "GLH": "GLU", "LYN": "LYS", "MSE": "MET"}
ONE = {"ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "CYX": "C", "GLN": "Q",
       "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K", "MET": "M",
       "PHE": "F", "PRO": "P", "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V"}
#: The atoms a residue has with no side chain at all.
BACKBONE_ATOMS = {"N", "CA", "C", "O", "OXT"}


class Final(Exception):
    """A design that cannot be measured on its own account. Its reason is kept, and it is not
    tried again until its structure changes."""


# ================================================================================ reading

def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def design_folder(path):
    """The folder a run's designs.json is in, or will be: the design job's own folder.

    A run queued from a design page keeps it one level down, in the job's folder; a flow's card
    folder is that folder itself. A run whose job has not written designs.json yet is found by its
    job script instead, so a scoring job can be queued behind it.
    """
    path = os.path.abspath(os.path.expanduser(path or ""))
    if os.path.isfile(os.path.join(path, "designs.json")):
        return path
    for marker in ("designs.json", "job.sbatch"):
        found = sorted(glob.glob(os.path.join(path, "*", marker)))
        found = [f for f in found if os.path.basename(os.path.dirname(f)) != FOLDER]
        if found:
            return os.path.dirname(found[0])
    return path


def read_designs(folder):
    return _read_json(os.path.join(folder, "designs.json"), {}) or {}


def read_scores(folder):
    """{design name: its entry} from scores.json, or {} where nothing has been measured."""
    got = _read_json(os.path.join(folder, SUMMARY), {}) or {}
    return {k: v for k, v in got.items() if isinstance(v, dict)} if isinstance(got, dict) else {}


def tool_of(folder, designs=None):
    """The tool that made the run, from designs.json, or from the page's record above it."""
    designs = read_designs(folder) if designs is None else designs
    tool = str(designs.get("tool") or "")
    if not tool:
        for at in (folder, os.path.dirname(folder)):
            said = _read_json(os.path.join(at, "submission.json"), {}) or {}
            tool = str(said.get("tool") or "") if isinstance(said, dict) else ""
            if tool:
                break
    return tool


def fingerprint(design):
    """What a score was measured on: the structure file's bytes and the chains it was split by.
    None when the file cannot be read."""
    h = hashlib.sha1()
    try:
        with open(str(design.get("path") or ""), "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                h.update(block)
    except OSError:
        return None
    h.update(json.dumps([list(design.get("binder_chains") or []),
                         list(design.get("target_chains") or []), VERSION]).encode("utf-8"))
    return h.hexdigest()


def stamp(design):
    """The structure file's size and time, and the chains: what is compared first, because a page
    reading thirty designs every half minute should not read thirty structures to do it."""
    try:
        st = os.stat(str(design.get("path") or ""))
    except OSError:
        return None
    return [st.st_size, int(st.st_mtime), list(design.get("binder_chains") or []),
            list(design.get("target_chains") or [])]


def fresh(entry, design):
    """Whether this entry was measured on this design as it is now. A design whose structure file
    has gone keeps the score it had: nothing could measure it again, and the number was made from
    the file while it was there."""
    if not isinstance(entry, dict) or entry.get("version") != VERSION:
        return False
    if not os.path.isfile(str(design.get("path") or "")):
        return True
    if entry.get("stamp") and entry.get("stamp") == stamp(design):
        return True
    return entry.get("fingerprint") == fingerprint(design)


def current(entry, design):
    """Whether this entry still describes this design, so measuring it again would add nothing:
    fresh, and not a failure that could pass."""
    return fresh(entry, design) and not entry.get("retry")


def cell(design, entry, tool, busy=""):
    """The two values of one design as a table shows them, with the reason where there is none.

    {"mmgbsa": kcal/mol or None, "binder_strain": kcal/mol or None, "score_why": the reason a
    value is missing, or ""}. A missing value is None, never 0.
    """
    if tool in BACKBONE:
        return {"mmgbsa": None, "binder_strain": None, "score_why": BACKBONE_ONLY}
    if not fresh(entry, design):
        if not os.path.isfile(str(design.get("path") or "")):
            why = "its structure file is not in the run any more"
        elif isinstance(entry, dict) and entry.get("version") == VERSION:
            why = "its structure has changed since it was measured"
        else:
            why = NOT_YET
        return {"mmgbsa": None, "binder_strain": None, "score_why": busy or why}
    dg, strain = _value(entry.get("mmgbsa")), _value(entry.get("binder_strain"))
    why = str(entry.get("why") or "")
    if not why and (dg is None or strain is None):
        why = NOT_YET
    if busy and (dg is None or strain is None) and (entry.get("retry") or not why):
        why = busy
    out = {"mmgbsa": dg, "binder_strain": strain, "score_why": why}
    # The numbers are of the structure as the tool wrote it. RFdiffusion3's carries the model's
    # own sequence while designs.json leads with ProteinMPNN's, so a table showing the one beside
    # numbers measured on the other says so rather than leaving it to be found out.
    said, scored = str(design.get("sequence") or ""), str(entry.get("sequence") or "")
    if scored and said and scored != said:
        out["score_note"] = OTHER_SEQUENCE
    return out


def _value(got):
    if got is None or isinstance(got, bool):
        return None
    try:
        got = float(got)
    except (TypeError, ValueError):
        return None
    return got if math.isfinite(got) else None


def recorded_jobs(folder):
    """[{jobid, adds, ...}] of every scoring job ever queued on this run, newest last."""
    got = _read_json(os.path.join(folder, FOLDER, JOBS), {}) or {}
    return [j for j in (got.get("jobs") or []) if isinstance(j, dict) and j.get("jobid")]


def live_jobs(ids):
    """{job id: state} of these jobs that are still queued or running, from squeue.

    None when squeue cannot be asked, which a caller treats as nothing known to be running: a page
    that refused every run because the scheduler was slow to answer would refuse for nothing.
    """
    ids = sorted({str(j) for j in ids if str(j or "").strip()})
    if not ids:
        return {}
    try:
        done = subprocess.run(["squeue", "-h", "-j", ",".join(ids), "-o", "%i|%T"],
                              capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    out = {}
    for line in done.stdout.splitlines():
        bits = line.strip().split("|")
        if len(bits) == 2 and bits[1].strip().startswith(LIVE):
            out[bits[0].split("_")[0]] = bits[1].strip()
    return out


def jobs_on(folder, live=None):
    """[{jobid, adds}] of the scoring jobs on this run that are queued or running now. `live` is
    live_jobs()' answer, passed in by a caller asking about many runs at once."""
    mine = recorded_jobs(folder)
    if live is None:
        live = live_jobs([j["jobid"] for j in mine]) or {}
    return [{"jobid": str(j["jobid"]), "adds": list(j.get("adds") or [MEASURE["key"]]),
             "after": str(j.get("after") or "")}
            for j in mine if str(j["jobid"]) in live]


def state(folder, tool=None, live=None, busy=""):
    """What a run has of the two measures, what it lacks, and what stops it being given them.

    Read, never computed. `busy` is the reason the run cannot take a job now, from its own design
    job ("its design job is still running"); a scoring job on it is found here.

        designs   how many designs it has          scored    how many carry both numbers
        lacks     how many could still be measured  cannot    why none can ("" otherwise)
        blocked   why nothing can be added now ("" otherwise)
        jobs      the scoring jobs on it now        finished  the day designs.json was written
    """
    designs_file = os.path.join(folder, "designs.json")
    record = read_designs(folder)
    tool = tool or tool_of(folder, record)
    designs = [d for d in (record.get("designs") or []) if isinstance(d, dict)]
    out = {"tool": tool, "designs": len(designs), "scored": 0, "lacks": 0, "cannot": "",
           "blocked": "", "jobs": jobs_on(folder, live), "finished": ""}
    try:
        out["finished"] = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(designs_file)))
    except OSError:
        pass
    if out["jobs"]:
        out["blocked"] = "a job is already on this run"
    elif busy:
        out["blocked"] = busy
    if tool in BACKBONE:
        out["cannot"] = out["blocked"] = BACKBONE_ONLY
        return out
    if not os.path.isfile(designs_file):
        out["blocked"] = out["blocked"] or "it has no designs yet"
        return out
    if not designs:
        out["blocked"] = out["blocked"] or "it wrote no designs"
        return out
    scores = read_scores(folder)
    gone = 0
    for one in designs:
        entry = scores.get(str(one.get("name") or ""))
        if current(entry, one):
            if _value(entry.get("mmgbsa")) is not None and \
                    _value(entry.get("binder_strain")) is not None:
                out["scored"] += 1
        elif os.path.isfile(str(one.get("path") or "")):
            out["lacks"] += 1
        else:
            gone += 1
    if not out["lacks"] and gone == len(designs):
        out["cannot"] = "its design files are no longer in the run"
    return out


# ================================================================================ the job

def walltime(designs, cores):
    """Two quarter-hours a design over the cores, and half an hour more, as HH:MM:00."""
    minutes = int(math.ceil(2.0 * max(1, designs) * MINUTES_A_DESIGN / max(1, cores)))
    minutes += MINUTES_OVER
    return "%02d:%02d:00" % (minutes // 60, minutes % 60)


def cores_for(designs):
    return max(1, min(MOST_CORES, int(designs or 1)))


def job_text(folder, designs, hopemd, labs, account="", name=""):
    """The scoring job. HOPE-MD's environment does the work, through this module.

    No partition is named. The walltime chooses it: on Grace the scheduler offers a CPU job every
    queue its walltime fits, so naming one could only narrow that or wait in the wrong place.
    """
    cores = cores_for(designs)
    run = os.path.join(folder, FOLDER)
    label = re.sub(r"[^A-Za-z0-9_.-]", "_", name or os.path.basename(os.path.dirname(folder))
                   or "run")[:40]
    lines = ["#!/bin/bash",
             "#SBATCH --job-name=scores_%s" % label,
             "#SBATCH --ntasks=1",
             "#SBATCH --cpus-per-task=%d" % cores,
             "#SBATCH --mem=%dG" % max(8, GB_A_CORE * cores),
             "#SBATCH --time=%s" % walltime(designs, cores),
             "#SBATCH --output=%s" % os.path.join(run, "scores_%j.out")]
    if account:
        lines.append("#SBATCH --account=%s" % re.sub(r"[^A-Za-z0-9_.-]", "", str(account)))
    lines += [
        "set -uo pipefail",
        "umask 002",
        "# One thread a process: the designs are shared out over the cores, one each, and",
        "# OpenMM and the BLAS under sander would otherwise start a thread a core for each one.",
        "export OMP_NUM_THREADS=1 OPENMM_CPU_THREADS=1 PYTHONDONTWRITEBYTECODE=1",
        "# The work goes in the scheduler's own folder on the node, and only what is kept",
        "# reaches the run: the inode quota on scratch is what runs out first.",
        'if [ -z "${TMPDIR:-}" ] || [ ! -d "${TMPDIR:-}" ]; then',
        '  export TMPDIR="/tmp/hopescores.${SLURM_JOB_ID:-$$}"; mkdir -p "$TMPDIR"',
        "fi",
        "cd %s || { echo 'HOPE-MD is not at %s, and its environment is what scores the designs'; "
        "exit 3; }" % (shlex.quote(hopemd), hopemd.replace("'", "")),
        "if [ -f ./activate.sh ]; then . ./activate.sh; fi",
        'PY="${HOPEMD_PYTHON:-python3}"',
        "export PYTHONPATH=%s" % shlex.quote(labs),
        "echo \"--- node $(hostname), $(date) ---\"",
        '"$PY" -m hope_labs.designscores %s --workers "${SLURM_CPUS_PER_TASK:-1}"'
        % shlex.quote(folder),
        "status=$?",
        'echo "scores: finished with status $status"',
        "exit $status",
        ""]
    return "\n".join(lines)


def expected(folder, plan=None):
    """How many designs a run will have, for a job queued before they exist: the plan's count,
    times its batches where it has them (RFdiffusion3)."""
    plan = plan if plan is not None else (_read_json(os.path.join(folder, "plan.json"), {}) or {})
    try:
        n = int(plan.get("designs") or 0) * max(1, int(plan.get("batches") or 1))
    except (TypeError, ValueError):
        n = 0
    return n or len(read_designs(folder).get("designs") or []) or 8


class Refused(RuntimeError):
    """A scoring job that will not be queued, with the reason a person reads."""


JOBID = re.compile(r"(\d+)")


def queue(folder, hopemd, labs, after="", account="", designs=None, dry=False, name=""):
    """Write the scoring job into the run and queue it; give back what was queued.

    {"jobid", "adds", "walltime", "script", "after"}, with jobid "" on a dry run, which writes the
    script and queues nothing. `after` is the design job it waits for: afterok, so a design job
    that fails takes its scoring job with it rather than leaving it waiting for ever.
    """
    if not os.path.isdir(hopemd):
        raise Refused("HOPE-MD is not installed at %s, and its environment is what scores the "
                      "designs" % hopemd)
    n = designs or expected(folder)
    where = os.path.join(folder, FOLDER)
    try:
        os.makedirs(where, exist_ok=True)
        script = os.path.join(where, SCRIPT)
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(job_text(folder, n, hopemd, labs, account=account, name=name))
    except OSError as why:
        raise Refused("the job could not be written into the run (%s)" % why.strerror)
    told = {"jobid": "", "adds": [MEASURE["key"]], "walltime": walltime(n, cores_for(n)),
            "script": script, "after": str(after or "")}
    if dry:
        return dict(told, dry=True)
    cmd = ["sbatch", "--parsable"]
    if after:
        cmd += ["--dependency=afterok:%s" % after, "--kill-on-invalid-dep=yes"]
    try:
        done = subprocess.run(cmd + [script], cwd=where, capture_output=True, text=True,
                              timeout=120)
    except (OSError, subprocess.SubprocessError) as why:
        raise Refused("sbatch did not answer (%s)" % why)
    found = JOBID.search(done.stdout or "")
    if done.returncode != 0 or not found:
        raise Refused("sbatch refused the job: %s"
                      % (((done.stderr or "") + (done.stdout or "")).strip()[-300:]
                         or "it said nothing"))
    told["jobid"] = found.group(1)
    remember(folder, told)
    return told


def remember(folder, job):
    """Add a queued job to the run's record of its scoring jobs."""
    path = os.path.join(folder, FOLDER, JOBS)
    got = _read_json(path, {}) or {}
    jobs = [j for j in (got.get("jobs") or []) if isinstance(j, dict)]
    jobs.append({"jobid": job["jobid"], "adds": job.get("adds") or [MEASURE["key"]],
                 "after": job.get("after") or "", "queued": time.strftime("%Y-%m-%d %H:%M:%S")})
    _write_json(path, {"jobs": jobs[-20:]})


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)


# ================================================================================ measuring

def amber_home():
    """AmberTools' folder: $AMBERHOME, this Python's own environment, or wherever sander is.

    HOPE-MD's environment on Grace is a venv over the conda environment that holds AmberTools, so
    the environment below the venv is looked in as well as the venv itself.
    """
    for cand in (os.environ.get("AMBERHOME"), sys.prefix, getattr(sys, "base_prefix", ""),
                 os.path.dirname(os.path.dirname(os.path.realpath(sys.executable)))):
        if cand and os.path.isfile(os.path.join(cand, "bin", "sander")):
            return cand
    found = shutil.which("sander")
    return os.path.dirname(os.path.dirname(os.path.realpath(found))) if found else None


def safe(name):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(name or "design"))[:120] or "design"


def _run(cmd, cwd, env, timeout):
    try:
        done = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, universal_newlines=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("%s did not finish in %d minutes" % (os.path.basename(cmd[0]),
                                                               timeout // 60))
    return done.returncode, done.stdout or ""


def _input_file(path, work):
    """The structure as a file OpenMM can open: a .gz is unpacked into the work folder first."""
    if not path.lower().endswith(".gz"):
        return path
    plain = os.path.join(work, os.path.basename(path)[:-3])
    with gzip.open(path, "rb") as src, open(plain, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return plain


def _read_complex(path):
    """[(chain id, [(residue name, [(atom name, element, (x, y, z) in A)])])] in file order, the
    hydrogens left out. A chain id that comes twice (a PDB split by TER) is one chain."""
    from openmm.app import PDBFile, PDBxFile
    from openmm.unit import angstrom
    low = path.lower()
    try:
        got = PDBxFile(path) if low.endswith((".cif", ".mmcif")) else PDBFile(path)
    except Exception as why:                                     # noqa: BLE001
        raise Final("OpenMM could not read the structure (%s)" % str(why)[:200])
    xyz = got.getPositions().value_in_unit(angstrom)
    chains, order = {}, []
    for chain in got.topology.chains():
        if chain.id not in chains:
            chains[chain.id] = []
            order.append(chain.id)
        for res in chain.residues():
            atoms = []
            for atom in res.atoms():
                symbol = atom.element.symbol if atom.element is not None else atom.name[:1]
                if symbol in ("H", "D"):
                    continue
                p = xyz[atom.index]
                atoms.append((atom.name, symbol, (p[0], p[1], p[2])))
            if atoms:
                chains[chain.id].append((res.name, atoms))
    return [(cid, chains[cid]) for cid in order]


def _dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _atom(res, name):
    for n, _e, p in res[1]:
        if n == name:
            return p
    return None


def _segments(residues):
    """A chain's residues cut where the chain has a hole: C of one and N of the next too far apart
    to be bonded, or either missing."""
    out, cur = [], []
    for res in residues:
        if cur:
            c, n = _atom(cur[-1], "C"), _atom(res, "N")
            if c is None or n is None or _dist(c, n) > PEPTIDE_BOND:
                out.append(cur)
                cur = []
        cur.append(res)
    if cur:
        out.append(cur)
    return out


def _pdb_line(serial, name, resname, chain, resseq, xyz, element):
    shown = name if len(name) == 4 else " " + name
    return ("ATOM  %5d %-4s %3s %s%4d    %8.3f%8.3f%8.3f  1.00  0.00          %2s\n"
            % (serial % 100000, shown[:4], resname[:3], chain, resseq % 10000, xyz[0], xyz[1],
               xyz[2], element[:2].upper()))


def _write_pdb(path, groups):
    """groups: [(chain letter, [segment: [(resname, atoms)]])], a TER after every segment, the
    residues numbered from 1 across the file - the numbering tleap gives them."""
    lines, serial, number = [], 1, 0
    for letter, segments in groups:
        for seg in segments:
            for resname, atoms in seg:
                number += 1
                for name, element, xyz in atoms:
                    lines.append(_pdb_line(serial, name, resname, letter, number, xyz, element))
                    serial += 1
            lines.append("TER\n")
    with open(path, "w") as handle:
        handle.write("".join(lines) + "END\n")


def _read_pdb(path):
    """[(chain letter, [(resname, atoms)])] per segment of a PDB, split at TER and at a change of
    chain, hydrogens left out. The reader for PDBFixer's own output, which keeps the ids it was
    given."""
    segs, cur, last = [], None, None
    with open(path) as handle:
        for line in handle:
            if line.startswith("TER") or line.startswith("END"):
                if cur and cur[1]:
                    segs.append(cur)
                cur, last = None, None
                continue
            if not line.startswith(("ATOM", "HETATM")):
                continue
            name = line[12:16].strip()
            element = (line[76:78].strip() or name[:1]).upper()
            if element in ("H", "D"):
                continue
            chain, key = line[21], (line[22:27], line[17:20])
            if cur is None or cur[0] != chain:
                if cur and cur[1]:
                    segs.append(cur)
                cur, last = (chain, []), None
            if key != last:
                cur[1].append((line[17:20].strip(), []))
                last = key
            cur[1][-1][1].append((name, element, (float(line[30:38]), float(line[38:46]),
                                                  float(line[46:54]))))
    if cur and cur[1]:
        segs.append(cur)
    return segs


def _backbone_only(residues):
    return all(name in BACKBONE_ATOMS for _r, atoms in residues for name, _e, _p in atoms)


def build(design, work):
    """Write com.pdb, rec.pdb and lig.pdb for tleap, and give back what was found on the way.

    {"target": residues, "binder": residues, "sequence": the binder's, "breaks": holes, "bonds":
    [(i, j)] disulfides in the complex's numbering, "dropped": what was left out}. Raises Final for
    a design that cannot be measured as it stands.
    """
    path = _input_file(str(design.get("path") or ""), work)
    chains = _read_complex(path)
    held = [c for c, _r in chains]
    binder = [str(c) for c in (design.get("binder_chains") or [])]
    if not binder:
        raise Final("designs.json does not say which chain is the binder")
    missing = [c for c in binder if c not in held]
    if missing:
        raise Final("the binder chain %s is not in the structure" % ", ".join(missing))
    target = [str(c) for c in (design.get("target_chains") or []) if str(c) not in binder] or \
        [c for c in held if c not in binder]
    missing = [c for c in target if c not in held]
    if missing:
        raise Final("the target chain %s is not in the structure" % ", ".join(missing))
    by_id = dict(chains)
    dropped = []

    def protein(cid):
        keep, other = [], 0
        for name, atoms in by_id[cid]:
            name = AS_PARENT.get(name, name)
            if name in AMINO:
                if name == "MET":
                    atoms = [("SD", "S", p) if n == "SE" else (n, e, p) for n, e, p in atoms]
                keep.append((name, atoms))
            else:
                other += 1
        if other:
            dropped.append("%d non-protein residue%s of chain %s" % (other, "" if other == 1
                                                                      else "s", cid))
        return keep

    binder_res = {c: protein(c) for c in binder}
    if not any(binder_res.values()):
        raise Final("the binder is not a protein, so ff14SB has nothing to score")
    if _backbone_only([r for c in binder for r in binder_res[c]]):
        raise Final(BACKBONE_ONLY)
    target_res = {}
    for c in target:
        got = protein(c)
        if got:
            target_res[c] = got
        else:
            dropped.append("chain %s, which is not a protein" % c)
    if not target_res:
        raise Final("the target has no protein chain to score the binder against")

    # Letters for the files: the design's own where it has one character each, so the minimized
    # complex reads against the design, and a free letter where it does not.
    used, letters = set(), {}
    for c in list(target_res) + binder:
        letter = c if len(c) == 1 and c not in used else next(
            L for L in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
            if L not in used)
        used.add(letter)
        letters[c] = letter
    groups = [(letters[c], _segments(target_res[c])) for c in target_res] + \
             [(letters[c], _segments(binder_res[c])) for c in binder]
    breaks = sum(len(segs) - 1 for _l, segs in groups)
    raw = os.path.join(work, "raw.pdb")
    _write_pdb(raw, groups)

    # PDBFixer completes the residues. findMissingResidues is asked and then emptied: it is what
    # tells addMissingAtoms which terminal atoms are wanted, and a loop the tool did not draw is
    # not the design's to have.
    from pdbfixer import PDBFixer
    from openmm.app import PDBFile
    fixer = PDBFixer(filename=raw)
    fixer.findMissingResidues()
    fixer.missingResidues = {}
    fixer.findMissingAtoms()
    fixer.addMissingAtoms()
    fixed = os.path.join(work, "fixed.pdb")
    with open(fixed, "w") as handle:
        PDBFile.writeFile(fixer.topology, fixer.positions, handle, keepIds=True)
    segs = _read_pdb(fixed)
    mine = {letters[c] for c in binder}
    rec = [s for s in segs if s[0] not in mine]
    lig = [s for s in segs if s[0] in mine]
    if not rec or not lig:
        raise Final("PDBFixer gave back a structure without the %s"
                    % ("target" if not rec else "binder"))

    # Disulfides, from where the sulfurs are. Numbered as tleap numbers the complex: the target's
    # residues first, from 1, then the binder's.
    flat = [(i + 1, res, s[0] in mine)
            for i, (res, s) in enumerate((r, s) for s in rec + lig for r in s[1])]
    sulfurs = [(i, _atom(res, "SG"), is_binder) for i, res, is_binder in flat
               if res[0] == "CYS" and _atom(res, "SG") is not None]
    bonds, bonded = [], set()
    for a in range(len(sulfurs)):
        for b in range(a + 1, len(sulfurs)):
            (i, p, bi), (j, q, bj) = sulfurs[a], sulfurs[b]
            if i in bonded or j in bonded or _dist(p, q) > DISULFIDE:
                continue
            if bi != bj:
                raise Final("a disulfide joins the binder to the target, so the two are one "
                            "molecule and there is no binding energy to measure")
            bonds.append((i, j))
            bonded.update((i, j))

    def renamed(groups_, offset):
        out, n = [], offset
        for letter, residues in groups_:
            seg = []
            for name, atoms in residues:
                n += 1
                seg.append(("CYX" if n in bonded else name, atoms))
            out.append((letter, [seg]))
        return out

    rec_g, lig_g = renamed(rec, 0), renamed(lig, sum(len(s[1]) for s in rec))
    _write_pdb(os.path.join(work, "com.pdb"), rec_g + lig_g)
    _write_pdb(os.path.join(work, "rec.pdb"), rec_g)
    _write_pdb(os.path.join(work, "lig.pdb"), lig_g)
    n_target = sum(len(s[1]) for s in rec)
    return {"target": n_target, "binder": sum(len(s[1]) for s in lig),
            "sequence": "".join(ONE.get(name, "X") for s in lig for name, _a in s[1]),
            "breaks": breaks, "bonds": bonds, "dropped": dropped}


LEAP = """source leaprc.protein.ff14SB
set default PBRadii mbondi2
com = loadpdb com.pdb
rec = loadpdb rec.pdb
lig = loadpdb lig.pdb
{bonds}saveamberparm com {p}_com.parm7 {p}_com.rst7
saveamberparm rec {p}_rec.parm7 {p}_rec.rst7
saveamberparm lig {p}_lig.parm7 {p}_lig.rst7
quit
"""

MIN_IN = """the designed complex, minimized with the target held
&cntrl
  imin=1, maxcyc={steps}, ncyc={steepest},
  igb={igb}, cut=999.0, ntb=0,
  ntr=1, restraintmask=':1-{target}&!@H=', restraint_wt={weight},
/
"""

MMPBSA_IN = """single frame
&general
   startframe=1, endframe=1, interval=1, verbose=2, keep_files=0,
/
&gb
   igb={igb}, saltcon={salt:.3f},
/
"""


def _leap_bonds(bonds, n_target):
    lines = []
    for i, j in bonds:
        lines.append("bond com.%d.SG com.%d.SG\n" % (i, j))
        if j <= n_target:
            lines.append("bond rec.%d.SG rec.%d.SG\n" % (i, j))
        elif i > n_target:
            lines.append("bond lig.%d.SG lig.%d.SG\n" % (i - n_target, j - n_target))
    return "".join(lines)


def _leap_error(log):
    for line in log.splitlines():
        if re.search(r"FATAL|Could not|Failed|does not have a type|is not a known", line):
            return line.strip()[:200]
    return (log.strip().splitlines() or ["it said nothing"])[-1][:200]


def measure(design, folder, home, old=None, force=False):
    """One design measured: its entry for scores.json. Never raises."""
    from . import strain as strainmod
    t0 = time.time()
    name = str(design.get("name") or "design")
    posedir = os.path.join(folder, FOLDER, safe(name))
    fp = fingerprint(design)
    entry = {"version": VERSION, "method": METHOD, "fingerprint": fp, "stamp": stamp(design),
             "mmgbsa": None,
             "binder_strain": None, "why": None, "retry": False, "folder": os.path.relpath(
                 posedir, folder), "file": str(design.get("path") or "")}
    env = dict(os.environ, AMBERHOME=home, OMP_NUM_THREADS="1")
    seconds = {}
    try:
        reuse = (not force and isinstance(old, dict) and old.get("version") == VERSION
                 and old.get("fingerprint") == fp and _value(old.get("mmgbsa")) is not None
                 and all(os.path.isfile(os.path.join(posedir, f)) for f in KEEP[:-1]))
        if reuse:
            # Measured before, and only the strain went wrong for a reason that could pass: the
            # complex is the one already kept, so only the strain is asked again.
            for key in ("mmgbsa", "target_residues", "binder_residues", "sequence", "breaks",
                        "disulfides", "dropped"):
                if key in old:
                    entry[key] = old[key]
        else:
            work = tempfile.mkdtemp(prefix="designscore_")
            try:
                t = time.time()
                got = build(design, work)
                seconds["build"] = round(time.time() - t, 1)
                entry.update(target_residues=got["target"], binder_residues=got["binder"],
                             sequence=got["sequence"], breaks=got["breaks"],
                             disulfides=len(got["bonds"]), dropped=got["dropped"])
                with open(os.path.join(work, "leap.in"), "w") as handle:
                    handle.write(LEAP.format(p=PREFIX, bonds=_leap_bonds(got["bonds"],
                                                                         got["target"])))
                t = time.time()
                _rc, log = _run([os.path.join(home, "bin", "tleap"), "-f", "leap.in"], work, env,
                                TIMEOUTS["tleap"])
                seconds["tleap"] = round(time.time() - t, 1)
                parms = [os.path.join(work, PREFIX + t) for t in ("_com.parm7", "_rec.parm7",
                                                                  "_lig.parm7")]
                if not all(os.path.isfile(p) and os.path.getsize(p) for p in parms):
                    raise Final("tleap could not build it: %s" % _leap_error(log))
                n_com, n_rec, n_lig = (strainmod.natom(p) for p in parms)
                if not (n_com and n_rec and n_lig) or n_rec + n_lig != n_com:
                    raise Final("tleap's target and binder hold %s + %s atoms against %s in the "
                                "complex" % (n_rec, n_lig, n_com))
                held = strainmod.residues(parms[1]) or got["target"]
                with open(os.path.join(work, "min.in"), "w") as handle:
                    handle.write(MIN_IN.format(steps=MIN_STEPS, steepest=MIN_STEEPEST, igb=IGB,
                                               target=held, weight=RESTRAINT))
                t = time.time()
                _run([os.path.join(home, "bin", "sander"), "-O", "-i", "min.in",
                      "-p", PREFIX + "_com.parm7", "-c", PREFIX + "_com.rst7",
                      "-ref", PREFIX + "_com.rst7", "-r", PREFIX + "_min.rst7", "-o", "min.out",
                      "-inf", "min.mdinfo"], work, env, TIMEOUTS["sander"])
                seconds["minimize"] = round(time.time() - t, 1)
                # The frame is sander's own restart, NetCDF as sander writes it by default and as
                # MMPBSA.py and cpptraj read it, so it is judged by the minimization's own last
                # word rather than read here.
                frame = os.path.join(work, PREFIX + "_min.rst7")
                if not os.path.isfile(frame) or not os.path.getsize(frame):
                    raise Final("the minimization left no frame to score")
                last = strainmod._final(os.path.join(work, "min.out"))
                if last is None or not math.isfinite(last[1]):
                    raise Final("the minimization did not finish with a finite energy")
                entry["minimized"] = round(last[1], 2)
                with open(os.path.join(work, PREFIX + "_mmpbsa.in"), "w") as handle:
                    handle.write(MMPBSA_IN.format(igb=IGB, salt=SALT))
                t = time.time()
                _rc, log = _run([os.path.join(home, "bin", "MMPBSA.py"), "-O",
                                 "-i", PREFIX + "_mmpbsa.in", "-cp", PREFIX + "_com.parm7",
                                 "-rp", PREFIX + "_rec.parm7", "-lp", PREFIX + "_lig.parm7",
                                 "-y", PREFIX + "_min.rst7", "-o", PREFIX + "_mmgbsa.dat"],
                                work, env, TIMEOUTS["mmpbsa"])
                seconds["mmgbsa"] = round(time.time() - t, 1)
                try:
                    with open(os.path.join(work, PREFIX + "_mmgbsa.dat")) as handle:
                        dat = handle.read()
                except OSError:
                    dat = ""
                m = re.search(r"DELTA TOTAL\s+(-?\d+\.\d+)", dat)
                if not m:
                    raise Final("MMPBSA.py gave no binding energy: %s"
                                % (log.strip().splitlines() or ["it said nothing"])[-1][:200])
                entry["mmgbsa"] = round(float(m.group(1)), 2)
                # What is kept, and nothing else: a folder made again starts empty, so a stale
                # strain from a structure since replaced is not left beside the new frame.
                if os.path.isdir(posedir):
                    shutil.rmtree(posedir)
                os.makedirs(posedir)
                for keep in KEEP[:-1]:
                    shutil.copyfile(os.path.join(work, keep), os.path.join(posedir, keep))
            finally:
                shutil.rmtree(work, ignore_errors=True)
        t = time.time()
        got = strainmod.measure(posedir, prefix=PREFIX, home=home, env=env, force=force)
        seconds["strain"] = round(time.time() - t, 1)
        if got.get("strain") is not None:
            entry["binder_strain"] = round(float(got["strain"]), 2)
            entry["moved"] = got.get("moved")
        else:
            entry["why"] = "binder strain not measured: %s" % (got.get("why") or "no reason given")
            # The strain module writes its record whenever sander ran to the end, a refusal
            # included; with none written, the cause was the machine's, and it is tried again.
            entry["retry"] = not os.path.isfile(os.path.join(posedir, PREFIX + "_strain.json"))
    except Final as why:
        entry["why"] = str(why)
    except Exception as why:                                      # noqa: BLE001
        entry["why"] = "not measured: %s" % (str(why)[:300] or type(why).__name__)
        entry["retry"] = True
    entry["seconds"] = dict(seconds, total=round(time.time() - t0, 1))
    entry["scored"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return entry


def _measure_one(args):
    return args[0], measure(*args[1:])


def _say(*what):
    # Flushed, so a job's log shows each design as it finishes rather than all of them at the end.
    print(*what, flush=True)


def score(path, workers=1, force=False, say=_say):
    """Measure every design of a run that has no current score. Gives back (measured, failed).

    scores.json is written after each design, so a job that runs out of walltime keeps what it
    finished, and the next job starts from there.
    """
    folder = design_folder(path)
    record = read_designs(folder)
    designs = [d for d in (record.get("designs") or []) if isinstance(d, dict)]
    if not os.path.isfile(os.path.join(folder, "designs.json")):
        say("%s: no designs.json, so there is nothing to measure" % folder)
        return 0, 0
    tool = tool_of(folder, record)
    if tool in BACKBONE:
        say("%s: %d design(s) not measured: %s" % (folder, len(designs), BACKBONE_ONLY))
        return 0, 0
    home = amber_home()
    if not home:
        say("%s: AmberTools was not found (no sander beside this Python, on PATH or in "
            "$AMBERHOME), so nothing was measured" % folder)
        return 0, len(designs)
    scores = read_scores(folder)
    todo = []
    for one in designs:
        name = str(one.get("name") or "")
        if not name:
            continue
        if not force and current(scores.get(name), one):
            continue
        if not os.path.isfile(str(one.get("path") or "")):
            say("  %s: not measured: its structure file is not in the run" % name)
            continue
        todo.append((name, one, folder, home, scores.get(name), force))
    say("%s: %d design(s), %d to measure, %d at a time"
        % (folder, len(designs), len(todo), max(1, min(workers, len(todo) or 1))))
    if not todo:
        return 0, 0
    os.makedirs(os.path.join(folder, FOLDER), exist_ok=True)
    measured = failed = 0

    def took(name, entry):
        nonlocal measured, failed
        scores[name] = entry
        names = {str(d.get("name") or "") for d in designs}
        _write_json(os.path.join(folder, SUMMARY),
                    {k: v for k, v in scores.items() if k in names})
        if entry.get("mmgbsa") is not None and entry.get("binder_strain") is not None:
            measured += 1
            say("  %s: MM-GBSA %.2f, binder strain %.2f kcal/mol (%.0f s)"
                % (name, entry["mmgbsa"], entry["binder_strain"], entry["seconds"]["total"]))
        else:
            failed += 1
            say("  %s: %s%s" % (name, entry.get("why"),
                                " (tried again next time)" if entry.get("retry") else ""))

    if workers <= 1 or len(todo) == 1:
        for job in todo:
            took(*_measure_one(job))
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        with ProcessPoolExecutor(max_workers=min(workers, len(todo))) as pool:
            futures = [pool.submit(_measure_one, job) for job in todo]
            for done in as_completed(futures):
                took(*done.result())
    return measured, failed


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m hope_labs.designscores",
        description="MM-GBSA and binder strain for every design of a binder-design run.")
    parser.add_argument("runs", nargs="+", help="a run folder, or the design job's folder in one")
    parser.add_argument("--workers", type=int,
                        default=int(os.environ.get("SLURM_CPUS_PER_TASK") or 1),
                        help="designs measured at once, one core each")
    parser.add_argument("--force", action="store_true",
                        help="measure again designs that already have a current score")
    args = parser.parse_args(argv)
    # One thread a process, whoever started this: the designs are shared out over the cores.
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("OPENMM_CPU_THREADS", "1")
    retry = 0
    for run in args.runs:
        score(run, workers=max(1, args.workers), force=args.force)
        folder = design_folder(run)
        retry += sum(1 for e in read_scores(folder).values() if e.get("retry"))
    # A design that may yet be measured makes the job a failure, so the run's page offers to add
    # the measures again rather than reading as finished.
    return 1 if retry else 0


if __name__ == "__main__":
    sys.exit(main())
