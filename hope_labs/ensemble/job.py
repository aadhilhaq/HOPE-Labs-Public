"""What the form offers, what the job is told, and the script that runs it.

The script has the shape the folding jobs have, and for the same reasons: the caches moved out
of the home folder, a short temporary folder, and a refusal to carry on when the node turns out
to have no usable card. What differs is that the tool runs inside its own image rather than from
an environment, so the line that does the work is built by hope_labs/images.py.
"""
from __future__ import annotations

import glob
import os
import re
import shlex
import sys

from . import MAX_LENGTH, MIN_SAMPLES, PROTEIN, SERVES, TYPICAL_MAX
from .. import images as imagesmod
from ..folding import sites as sitesmod
from ..folding.job import Setting, WALLTIME, unsafe_path, short_tmpdir  # noqa: F401

#: Where the lab's copies are, as hope_labs/tools.py has them. One setting moves them, the same
#: way hope_flow/drivers.py and the folding jobs read HOPEFLOW_LAB_ROOT.
LAB_ROOT = os.environ.get("HOPEFLOW_LAB_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

#: Where a run goes when nobody says otherwise: a person's own scratch rather than the group's, as
#: the folding pages do. An ensemble of a few hundred structures is tens of megabytes, and they
#: are somebody's working runs rather than the lab's record.
RUNS = {"bioemu": os.path.join(os.environ.get("SCRATCH", "/tmp"), "bioemu_runs")}

#: Where the tool is: one file, which is the whole point of it (hope_labs/images.py). The page is
#: handed this and reads the folder holding it off the path, so a copy of the image in somebody
#: else's scratch brings its own weights with it.
INSTALLS = {"bioemu": imagesmod.image_path(LAB_ROOT, "bioemu")}

#: What a job of this kind asks the scheduler for. The memory is what sampling a long sequence
#: needs on the processor side; the card's own memory is what bounds the length, and the page
#: says so rather than finding out at the end of a queue wait.
RESOURCES = {"bioemu": {"cpus": 4, "mem": 32}}

#: Longer than this and the path the tool opens its worker sockets on is too long for the
#: kernel, which fails inside a thread where nothing catches it. The folding jobs measured it.
TMPDIR_CAP = 75


class Refused(ValueError):
    """Something the form asked for that cannot be run, said as a sentence."""


def clean_sequence(text):
    """One protein sequence from whatever was pasted: FASTA, wrapped, spaced or numbered."""
    lines = [l.strip() for l in str(text or "").splitlines()]
    kept = [l for l in lines if not l.startswith(">")]
    return re.sub(r"[^A-Za-z]", "", "".join(kept)).upper()


def check_sequence(text):
    """(the sequence, what to say about it). Raises Refused on what cannot be sampled."""
    seq = clean_sequence(text)
    if not seq:
        raise Refused("paste a protein sequence first.")
    odd = sorted(set(seq) - PROTEIN)
    if odd:
        raise Refused(
            "this sequence holds %s, which is not one of the twenty amino acids. BioEmu samples "
            "one protein chain: it has no alphabet for DNA or RNA and no way to be given a "
            "ligand, so a sequence with anything else in it would be sampled into something "
            "that means nothing." % ", ".join(repr(c) for c in odd))
    if len(seq) < 5:
        raise Refused("a sequence of %d residues is too short to sample." % len(seq))
    if len(seq) > MAX_LENGTH:
        raise Refused(
            "%d residues is past what the cards here will hold: sampling grows with the square "
            "of the length, so a chain this long runs out of card memory rather than running "
            "slowly. The ceiling is %d." % (len(seq), MAX_LENGTH))
    notes = []
    if len(seq) > TYPICAL_MAX:
        notes.append(
            "%d residues is beyond what BioEmu was trained within (about %d), so these samples "
            "are an extrapolation rather than a prediction. Worth looking at; not worth "
            "reporting without saying so." % (len(seq), TYPICAL_MAX))
    return seq, notes


#: How many structures to draw, as three choices rather than a number to think about.
#:
#: The paragraph this replaces was doing the reader's arithmetic for them: "a few hundred, and the
#: cost is close to linear in this number". So the arithmetic is done here and the answer shown as
#: minutes. The number box stays beside them for somebody who wants 437.
#:
#: The minutes are per hundred samples of a short chain on an A100, measured on the installation's
#: own check and scaled: a run of 4 samples of ten residues took two minutes, almost all of it
#: loading the model, and the drawing itself is close to linear in the count.
PRESETS = (
    {"key": "look", "label": "A look", "samples": 100,
     "why": "Is it one shape or several?", "minutes": 4},
    {"key": "standard", "label": "Standard", "samples": 500,
     "why": "Populations you can quote.", "minutes": 18},
    {"key": "thorough", "label": "Thorough", "samples": 2000,
     "why": "The small populations too.", "minutes": 70},
)
#: Which one a number lands on, so a run queued at 500 comes back to the form with Standard lit
#: rather than with nothing lit and the number looking like somebody's odd choice.
PRESET_OF = {one["samples"]: one["key"] for one in PRESETS}


def minutes_for(samples, residues=0):
    """Roughly how long drawing this many samples takes, in minutes.

    A guess, said as one: the model takes about two minutes to load whatever is asked of it, and
    the drawing scales with the count and with the length. Shown on the form so that choosing a
    preset is choosing a cost, which is the only way a person can choose well.
    """
    samples = max(1, int(samples or 0))
    scale = 1.0 + max(0, int(residues or 0) - 50) / 250.0
    return max(3, int(round(2 + (samples / 100.0) * 3.2 * scale)))


def settings(tool, site=None):
    """Every control the form offers, in the order it shows them.

    Each one is a short label and, where it is worth saying, one line under it. The whole reason
    goes in `explain`, which the ? beside the control opens: the rail used to carry four
    paragraphs a reader had to get past before they could set anything.
    """
    if tool not in SERVES:
        raise Refused("%s is not a tool this page serves" % tool)
    site = site or sitesmod.here()
    return [
        Setting("num_samples", "How many structures", "number", 500,
                least=MIN_SAMPLES, most=10000, step=10,
                explain="A distribution, not a structure: ten tells you nothing about how wide "
                        "it is. BioEmu's own examples use a few hundred, and the cost is close "
                        "to linear in this number, so the presets carry a time each. Below %d "
                        "the page refuses: a population read off ten samples is noise."
                        % MIN_SAMPLES),
        Setting("model_name", "Model", "choice", "bioemu-v1.1",
                ("bioemu-v1.1", "bioemu-v1.0", "bioemu-v1.2"),
                notes={"bioemu-v1.1": "the paper's", "bioemu-v1.0": "the preprint's",
                       "bioemu-v1.2": "more training"},
                explain="v1.1 is what the published paper reports, so it is what a number taken "
                        "from here can be compared against. The others are kept because a run "
                        "made on one cannot be compared with a run made on another: if you "
                        "change this, change it for every run in the comparison."),
        Setting("alignments", "Alignment", "choice", "fetch", ("fetch", "file"),
                notes={"fetch": "search for it", "file": "an .a3m I have"},
                explain="BioEmu does not read a sequence directly: it reads the embeddings "
                        "AlphaFold2 makes of it, and those come from an alignment. It is "
                        "searched for on this login node because a GPU node here reaches no "
                        "network at all, and a job handed a bare sequence would sit retrying "
                        "the alignment server until its walltime ran out. Name your own .a3m to "
                        "skip the search, which is also what to do when the server is down."),
        Setting("msa_file", "Alignment file", "text", "", optional=True,
                placeholder="/scratch/you/thing.a3m",
                explain="Read only when Alignment is set to a file. It must end in .a3m: the "
                        "tool decides what a file is by its suffix, so a .fasta named here would "
                        "be read as a bare sequence."),
        Setting("filter_samples", "Drop unphysical samples", "yesno", True, advanced=True,
                explain="BioEmu's own filter, on by default in the tool as well. It removes "
                        "draws that are not physically sensible, a chain passing through itself "
                        "and the like. Turn it off only to see everything the model drew."),
        Setting("seed", "Seed", "number", "", advanced=True, optional=True,
                least=0, most=2 ** 31 - 1, step=1, placeholder="New each run",
                explain="The same number gives the same samples again. Worth setting for "
                        "anything that goes in a paper."),
        Setting("gpu", "GPU", "choice", sitesmod.AUTO, _card_menu(site),
                why="auto takes %s here" % (site.card() or "a GPU"),
                explain="Sampling holds the whole chain on the card, so a long sequence wants "
                        "the largest one there is. Left on auto the job asks for %s, the largest "
                        "%s has; the rest are there to ask for a smaller one on purpose, which "
                        "gets through the queue sooner."
                        % (site.card() or "a GPU", site.name)),
        Setting("walltime", "Time limit", "duration", "02:00:00",
                least=600, most=int(site.max_gpu_hours() * 3600), step=1800,
                explain="A few hundred samples of a small protein is minutes, and %s allows %g "
                        "hours at most on %s. A job killed at its walltime leaves nothing: the "
                        "samples are written only once every batch has been drawn, so round up "
                        "rather than cut it fine. Estimate fills this in from the form."
                        % (site.name, site.max_gpu_hours(), site.gpu_partition)),
        Setting("account", "Account", "text", "", optional=True, placeholder="Your default",
                explain="Left empty, the job is charged to your default account."),
        Setting("keep_files", "Keep every file", "yesno", False, advanced=True,
                explain="A finished run packs what its results no longer read, the per-batch "
                        "arrays the samples were written from and the embeddings made from the "
                        "alignment, into one archive in its folder. The samples, the topology, "
                        "everything under analysis/, the alignment and the log stay. "
                        "python -m hope_labs.ensemble.tidy RUN --undo puts the rest back."),
    ]


def _card_menu(site):
    return (sitesmod.AUTO,) + tuple(site.gpu_types())


def default_settings(tool, site=None):
    return {s.key: s.default for s in settings(tool, site)}


def _q(text):
    return shlex.quote(str(text))


HEAD = """#!/bin/bash
#SBATCH --job-name={name}
#SBATCH --partition={partition}
#SBATCH --gres={gres}
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task={cpus}
#SBATCH --mem={mem}G
#SBATCH --time={walltime}
#SBATCH --output={out}/slurm_%j.out
{account}
set -uo pipefail
umask 002
OUT={outq}

# Nothing may reach $HOME. The home folder on this cluster is 10,000 files, and the tool keeps
# its caches by the thousand; the image is given a home folder inside the run instead, which is
# also where the weights are linked from.
export XDG_CACHE_HOME="$OUT/cache" MPLCONFIGDIR="$OUT/cache/mpl" CUDA_CACHE_PATH="$OUT/cache/nv"
export MPLBACKEND=Agg
mkdir -p "$MPLCONFIGDIR" "$CUDA_CACHE_PATH"

# As the folding jobs do: the scheduler's own temporary folder where it is short enough, because
# a worker socket path over 107 characters fails in a thread where nothing catches it.
if [ -z "${{TMPDIR:-}}" ] || [ ! -d "${{TMPDIR:-}}" ] || [ "${{#TMPDIR}}" -gt {tmpcap} ]; then
  if mkdir -p "/tmp/hopeens.${{SLURM_JOB_ID:-$$}}" 2>/dev/null; then
    export TMPDIR="/tmp/hopeens.${{SLURM_JOB_ID:-$$}}"
  else
    export TMPDIR="$OUT/cache/tmp"
  fi
fi
mkdir -p "$TMPDIR"
unset PYTHONPATH PYTHONHOME
cd "$OUT" || exit 1

echo '--- node ---'
hostname
nvidia-smi -L 2>&1 | head -4 || echo 'no nvidia-smi on this node'

# The job asked for a card and was given one, so no card here is the driver disagreeing with the
# scheduler rather than a missing card. Sampling on the processor would spend the whole booking.
if ! nvidia-smi -L >/dev/null 2>&1; then
  echo
  echo 'STOPPING: there is no usable GPU on this node.'
  exit 1
fi
{home}
echo '--- sampling ---'
"""

TAIL = """
status=$?
echo '--- what was written ---'
found=$(ls -1 {lookdir}/{pattern} 2>/dev/null | wc -l)
echo "$found file(s) matching {pattern} in {lookshown}"
if [ "$found" -eq 0 ]; then
  echo
  echo 'STOPPING: the sampling finished and wrote nothing.'
  echo 'BioEmu writes its samples only once every batch has been drawn, so a job killed at its'
  echo 'walltime leaves none. The log above is the tool'"'"'s own.'
  exit 1
fi
echo '--- reading the ensemble ---'
{analyse} \\
  || echo 'the samples are there but could not be read; analysis/ is empty and out/ is whole'
{compare}{tidy}echo "ensemble: {name} finished with status $status"
exit $status
"""

#: The comparison, run in the same job once the samples have been read. It is allowed to fail
#: without taking the run with it: the ensemble is the result, and a comparison against a run
#: that turns out to hold a different chain is a sentence to read rather than a lost booking.
COMPARE_STEP = """echo '--- against {shown} ---'
{line} \\
  || echo 'the comparison could not be made; the ensemble itself is unaffected'
"""

#: Reading the ensemble, in the same job: how wide the spread is, how many shapes are in it and
#: how populated each one is. In the job rather than on the login node because it reads every
#: sample, and inside the image because that is where mdtraj is: this checkout is bound in and
#: put on the path, so the code being run is the code in the repository rather than a copy of it
#: pasted into a script.
ANALYZE = "%s"

#: The run tidying itself, once its samples have been found: only after a success, and left out
#: when the form says to keep every file (tidy.py says what goes and what stays).
TIDY = """# The run tidies itself, its samples being there (hope_labs.ensemble.tidy).
if [ "$status" -eq 0 ]; then
  PYTHONPATH={checkout} {python} -m hope_labs.ensemble.tidy "$OUT" \\
    || echo 'tidy: this run could not be tidied, and is as the tool left it'
fi
"""


def checkout():
    """This checkout, which the analysis is run out of and the tidy line calls into."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def analyse_line(root, run, structure=""):
    """The line that reads the ensemble, inside the image, with no card and no network.

    A structure, where one was given, is handed over with it: the samples are then measured
    against the pose the chain was in, which is the question somebody with a bound structure
    actually has. It was copied into the run folder at submit, so it needs no bind of its own.
    """
    here = checkout()
    argv = ["python", "-m", "hope_labs.ensemble.analyse", run]
    if structure:
        argv.append(structure)
    return imagesmod.line(imagesmod.exec_argv(
        root, "bioemu", argv, binds=[run, here], gpu=False,
        home=os.path.join(run, "cache", "home"), extra_env={"PYTHONPATH": here}))


def compare_line(root, run, other):
    """The line that puts this ensemble against another run, in the sampling job itself.

    In the same job rather than a second one: it reads frames on the processor for a few minutes,
    and a comparison that had to be queued separately is a comparison nobody runs. The other run
    is bound in read-only as far as this is concerned: nothing is ever written into it.
    """
    here = checkout()
    return imagesmod.line(imagesmod.exec_argv(
        root, "bioemu", ["python", "-m", "hope_labs.ensemble.compare", run, other],
        binds=[run, other, here], gpu=False, home=os.path.join(run, "cache", "home"),
        extra_env={"PYTHONPATH": here}))


#: A comparison against a simulation: a processor job, because it reads every frame of a
#: trajectory and nothing it does wants a card. Short and small, so it queues while the GPU
#: queue is busy. It names no partition: the scheduler offers a job every queue its --time fits,
#: and naming one that is shorter than the job asks for leaves it PENDING for ever instead of
#: failing. {partition} is filled with a whole "#SBATCH" line, or nothing.
def _cpu_partition_line(site):
    """The "#SBATCH --partition=" line for a small processor job, or "" for none.

    Grace's scheduler routes on --time (it answers Partition=short,medium,long), so naming a
    queue can only narrow the choice; naming one shorter than the job asks for is what leaves a
    job PENDING on Reason=PartitionTimeLimit until somebody notices. A site whose queues must be
    named sets HOPELABS_CPU_PARTITION."""
    import os
    name = os.environ.get("HOPELABS_CPU_PARTITION", "")
    return "#SBATCH --partition=%s\n" % name if name else ""


COMPARE = """#!/bin/bash
#SBATCH --job-name=ens_vs_md_{name}
{partition}#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=16G
#SBATCH --time=01:00:00
#SBATCH --output={out}/compare_%j.out
{account}
set -uo pipefail
umask 002
OUT={outq}
export XDG_CACHE_HOME="$OUT/cache" MPLCONFIGDIR="$OUT/cache/mpl" MPLBACKEND=Agg
mkdir -p "$MPLCONFIGDIR"
unset PYTHONPATH PYTHONHOME
cd "$OUT" || exit 1
echo '--- node ---'
hostname
echo '--- the ensemble against the simulation ---'
{home}
{line}
status=$?
echo "compare: finished with status $status"
exit $status
"""


def compare_script_text(root, run, md_run, name, site=None, account=""):
    """The whole comparison job, which is what the page shows and what sbatch is handed."""
    site = site or sitesmod.here()
    bad = unsafe_path(run) or unsafe_path(md_run)
    if bad:
        raise Refused("the run folders cannot be used: Slurm will not accept "
                      + ", ".join(repr(c) for c in bad) + " in a path.")
    here = checkout()
    home = os.path.join(run, "cache", "home")
    line = imagesmod.line(imagesmod.exec_argv(
        root, "bioemu", ["python", "-m", "hope_labs.ensemble.compare", run, md_run],
        binds=[run, md_run, here], gpu=False, home=home, extra_env={"PYTHONPATH": here}))
    return COMPARE.format(
        name=name, partition=_cpu_partition_line(site), out=run, outq=_q(run), line=line,
        account=("#SBATCH --account=%s" % str(account).strip() if str(account or "").strip()
                 else ""),
        home="\n".join(imagesmod.home_lines(root, "bioemu", home)))


def body(root, run, asked):
    """The one line that samples, and where to look for what it wrote."""
    out = os.path.join(run, "out")
    argv = ["python", "-m", "bioemu.sample",
            "--sequence", _sequence_argument(run, asked),
            "--num_samples", str(int(asked.get("num_samples") or 100)),
            "--output_dir", out,
            "--model_name", str(asked.get("model_name") or "bioemu-v1.1"),
            # Where the embeddings it makes are kept, so a second run of the same sequence does
            # not search for its alignment again. In the run folder rather than the home one,
            # which is where the tool puts it when nobody says otherwise.
            "--cache_embeds_dir", os.path.join(run, "cache", "embeds")]
    if not asked.get("filter_samples", True):
        argv += ["--filter_samples", "False"]
    if str(asked.get("seed") or "").strip():
        argv += ["--base_seed", str(int(asked["seed"]))]
    home = os.path.join(run, "cache", "home")
    line = imagesmod.line(imagesmod.exec_argv(root, "bioemu", argv, binds=[run], home=home))
    return line, out, "*.xtc", os.path.join("out", "*.xtc")


def _sequence_argument(run, asked):
    """What --sequence is given: the alignment where there is one, else the sequence itself.

    BioEmu takes an .a3m in the same argument as a bare sequence and prefers it, which is how a
    run made here never reaches for the alignment server from a node that cannot see it.
    """
    if str(asked.get("alignments") or "fetch") == "file" and str(asked.get("msa_file") or "").strip():
        return str(asked["msa_file"]).strip()
    fetched = os.path.join(run, "inputs", "alignment.a3m")
    return fetched if os.path.isfile(fetched) else str(asked.get("sequence") or "")


def script_text(tool, root, run, name, asked, site=None):
    """The whole job script, which is what the page shows and what sbatch is handed."""
    if tool not in SERVES:
        raise Refused("%s is not a tool this page serves" % tool)
    site = site or sitesmod.here()
    bad = unsafe_path(run)
    if bad:
        raise Refused(
            "the run folder cannot be used: Slurm parses #SBATCH --output itself and will not "
            "accept " + ", ".join(repr(c) for c in bad) + " in a path.\n  %s" % run)
    gpu = site.card(asked.get("gpu"))
    line, look, pattern, shown = body(root, run, asked)
    home = os.path.join(run, "cache", "home")
    head = HEAD.format(
        name="bioemu_%s" % name, partition=site.partition_for(gpu), gres=site.gres(gpu),
        cpus=RESOURCES[tool]["cpus"], mem=RESOURCES[tool]["mem"],
        walltime=str(asked.get("walltime") or "02:00:00"),
        out=run, outq=_q(run), tmpcap=TMPDIR_CAP,
        account=("#SBATCH --account=%s" % str(asked["account"]).strip()
                 if str(asked.get("account") or "").strip() else ""),
        home="\n".join(imagesmod.home_lines(root, "bioemu", home)))
    keep = asked.get("keep_files") not in (None, "", False, "false", "no", 0, "0")
    tidy = "" if keep else TIDY.format(checkout=_q(checkout()), python=_q(sys.executable))
    against = str(asked.get("against") or "").strip()
    compare = "" if not against else COMPARE_STEP.format(
        shown=os.path.basename(against.rstrip(os.sep)),
        line=compare_line(root, run, against))
    tail = TAIL.format(lookdir=_q(look), pattern=pattern, lookshown=shown, name=name,
                       analyse=analyse_line(root, run, str(asked.get("structure") or "")),
                       compare=compare, tidy=tidy)
    return head + line + tail


#: What is run on the login node to get the alignment: BioEmu's own client, so what is written
#: is what BioEmu would have fetched for itself, in the format it reads back.
FETCH = ("from bioemu.get_embeds import _get_a3m_string; "
         "open({out!r}, 'w').write(_get_a3m_string({seq!r}, None, None))")


def fetch_alignment(root, run, sequence, say=print, minutes=40):
    """Search for the sequence's alignment here, on the login node, and write it into the run.

    This is the whole reason a run has two phases. BioEmu makes its embeddings from an alignment
    it fetches from the ColabFold server the first time it sees a sequence, and a GPU node on
    this cluster reaches no server at all: the job would sit waiting on a socket that cannot
    open, for its whole booking, having already been given a card.

    Returns the path written. A failure here is raised rather than carried into the queue.
    """
    where = os.path.join(run, "inputs")
    os.makedirs(where, exist_ok=True)
    out = os.path.join(where, "alignment.a3m")
    if os.path.isfile(out) and os.path.getsize(out) > 0:
        say("the alignment is already in the run folder")
        return out
    argv = imagesmod.exec_argv(
        root, "bioemu", ["python", "-c", FETCH.format(out=out, seq=sequence)],
        binds=[run], gpu=False, home=os.path.join(run, "cache", "home"),
        # The one command of a run that is allowed out: everything after it runs with the
        # network shut, which is what makes a missing alignment a plain failure rather than a
        # job that hangs on a compute node.
        extra_env={"HF_HUB_OFFLINE": "0"})
    say("searching for the alignment (this is the slow part of a first run)")
    got = imagesmod.run(argv, root, timeout=minutes * 60)
    if got.returncode != 0 or not os.path.isfile(out) or not os.path.getsize(out):
        raise Refused("the alignment could not be fetched: %s"
                      % ((got.stdout or "") + (got.stderr or "")).strip()[-400:]
                      or "the search said nothing")
    say("alignment written: %s" % out)
    return out


def wrote(run):
    """What a finished run left: the samples as one file, and the topology beside them."""
    out = os.path.join(run, "out")
    return {"ensembles": sorted(glob.glob(os.path.join(out, "*.xtc"))),
            "topology": sorted(glob.glob(os.path.join(out, "*.pdb")))}
