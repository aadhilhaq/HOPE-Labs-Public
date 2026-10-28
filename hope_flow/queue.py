"""Slurm, as a flow uses it: one small job per card, each waiting on the one before it.

A card's step job is not the work. It is the few minutes that read what the card before it left,
write what this card needs, start the tool, and queue the next card's step job behind the tool it
just started. The work itself is whatever the tool submits, unchanged, under the person's own
account.

Nothing of the launcher is alive while this happens. That is the point of doing it this way
rather than watching from a login node: a flow that has been queued survives the laptop being
closed, the launcher being shut and the connection going away.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time

#: Where a step job runs. It reads a few files and calls sbatch, so it wants a short queue and
#: very little of anything else. The partition is a setting because this is the one line that
#: differs between the clusters the lab uses.
STEP_PARTITION = os.environ.get("HOPEFLOW_PARTITION", "short")
STEP_TIME = "00:30:00"
STEP_CPUS = 2
STEP_MEM = "8G"

JOBID = re.compile(r"(\d+)")

#: How many times a submit is tried before a flow is left stopped, and how long between. Slurm on
#: a busy login node answers nothing now and then; a card that cannot queue stops everything
#: behind it, so it is worth a few seconds to find out whether it meant it.
TRIES = 3
PAUSE = 3.0

#: How long one of those attempts waits for an answer. Named rather than written into the call
#: because it is the one thing a test of a slow controller has to be able to shorten.
ANSWER_IN = 120

#: The states a job has not come back from. One list, read by everything that asks the question -
#: whether a card's work is still going, whether a step job is still on its way, what there is
#: left to cancel - because the three copies of it had already begun to differ from one another.
LIVE = ("PENDING", "RUNNING", "COMPLETING", "CONFIGURING", "REQUEUED", "RESIZING", "SUSPENDED")


def available():
    return shutil.which("sbatch") is not None


def submit(script, cwd, dependency="", name=""):
    """Queue a script and give back its job id.

    `dependency` is a whole Slurm dependency expression, not a bare id, because a card with two
    tracks arriving at it waits on both and the caller is the one that knows.
    """
    cmd = ["sbatch", "--parsable"]
    if dependency:
        cmd.append("--dependency=" + dependency)
    if name:
        cmd.append("--job-name=" + name)
    cmd.append(str(script))

    # Tried more than once, because an sbatch that answers nothing at all is usually the
    # controller being busy for a moment rather than anything wrong with the job. A flow stops
    # dead when one card will not queue, and the card after it never gets its turn, so one
    # hiccup costs a person their whole chain; three seconds of waiting is the cheaper mistake.
    # A real refusal says the same thing each time and is reported on the last attempt.
    last = ""
    for attempt in range(TRIES):
        try:
            run = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                                 timeout=ANSWER_IN)
        except (OSError, subprocess.SubprocessError) as why:
            # A controller that says nothing for two minutes is the same hiccup as one that
            # answers nothing, and deserves the same second try. Left to be raised, it came out of
            # the step job as a traceback rather than as a sentence: the card being queued was
            # never recorded, and the cards after it never got a step job of their own.
            last = "sbatch did not answer (%s)" % why
        else:
            found = JOBID.search(run.stdout or "")
            if found and run.returncode == 0:
                return found.group(1)
            # Grace's sbatch can exit 0, print nothing, and put the reason on stderr, so neither
            # the status nor an empty stdout is enough on its own and stderr is never thrown away:
            # the one time this happened the person was told "printed no job id:" and nothing else.
            last = ((run.stderr or "") + " " + (run.stdout or "")).strip() \
                or "it printed nothing at all, and exited %d" % run.returncode
        if attempt + 1 < TRIES:
            time.sleep(PAUSE)
    raise RuntimeError("sbatch would not queue %s after %d attempts: %s"
                       % (os.path.basename(str(script)), TRIES, last))


def step_script(flow_dir, node_id, python, package_root, account="", partition=""):
    """The job that runs one card: adapters in, tool out, the next card queued behind it.

    It carries the caches into scratch as every job of the lab's does. A step job that writes
    into a home folder would be a small thing each time and a filled quota by the end of a week.
    """
    where = os.path.join(str(flow_dir), "logs")
    lines = [
        "#!/bin/bash",
        "#SBATCH --job-name=flow-%s" % node_id,
        "#SBATCH --partition=%s" % (partition or STEP_PARTITION),
        "#SBATCH --time=%s" % STEP_TIME,
        "#SBATCH --ntasks=1",
        "#SBATCH --cpus-per-task=%d" % STEP_CPUS,
        "#SBATCH --mem=%s" % STEP_MEM,
        "#SBATCH --output=%s/%s-%%j.out" % (where, node_id),
    ]
    if account:
        lines.append("#SBATCH --account=%s" % account)
    lines += [
        "",
        "# Caches into scratch, never a home folder.",
        'export XDG_CACHE_HOME="${SCRATCH:-/tmp}/.cache/hopeflow"',
        'export MPLCONFIGDIR="$XDG_CACHE_HOME/matplotlib"',
        'export CUDA_CACHE_PATH="$XDG_CACHE_HOME/nv"',
        'mkdir -p "$XDG_CACHE_HOME"',
        "",
        "set -u",
        'PYTHONPATH=%s exec %s -m hope_flow.cli step %s %s'
        % (_q(package_root), _q(python), _q(str(flow_dir)), _q(node_id)),
        "",
    ]
    return "\n".join(lines)


def _q(text):
    """A shell word, quoted once and properly."""
    text = str(text)
    if re.match(r"^[A-Za-z0-9_@%+=:,./-]+$", text):
        return text
    return "'" + text.replace("'", "'\\''") + "'"


def states(job_ids):
    """{job id: what Slurm says}, for jobs that may already have left the queue.

    squeue knows the ones still about; sacct knows the ones that have finished. Asking both and
    preferring squeue is what every tool in the lab does, because sacct lags a little behind a
    job that has only just ended.
    """
    out, tasks = {}, {}
    ids = [str(j) for j in job_ids if str(j).strip()]
    if not ids:
        return out
    joined = ",".join(ids)
    for cmd, split in (
            (["sacct", "-n", "-X", "-P", "-o", "JobID,State", "-j", joined], "|"),
            (["squeue", "-h", "-o", "%i %T", "-j", joined], " ")):
        try:
            run = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            continue
        if run.returncode != 0:
            continue
        for line in (run.stdout or "").splitlines():
            bits = line.strip().split(split)
            if len(bits) >= 2 and bits[0].strip():
                jid = bits[0].strip().split(".")[0]
                out[jid] = bits[1].strip().split()[0]
                base = jid.split("_")[0]
                if base != jid:
                    tasks.setdefault(base, {})[jid] = out[jid]
    # Neither command ever answers about an array job itself: both answer about its tasks, as
    # 42_0, 42_1, or 42_[0-5] for an array that has not started, while a card holds the one id it
    # was given. So the tasks are added up to it - and only where nothing answered about that id
    # directly, which is what a job that is not an array does.
    #
    # Left out, this was the quietest way a finished flow could read as a running one for ever:
    # every simulation job is an array over the replicates, so a card whose runs had ended days
    # ago was still waiting for jobs that nothing would ever say had finished.
    for base, got in tasks.items():
        out.setdefault(base, _together(got.values()))
    return out


def _together(states):
    """What an array's tasks add up to, the cautious way round.

    Anything still to come outweighs anything that went wrong, which outweighs the tasks that
    finished. Read the other way round, a run would be called done while half of its replicates
    were still in the queue.
    """
    states = [s for s in states if s]
    live = [s for s in states if s.startswith(LIVE)]
    if live:
        return live[0]
    bad = [s for s in states if not s.startswith("COMPLETED")]
    return bad[0] if bad else "COMPLETED"


def all_done(job_ids, seen=None):
    """(finished, failed): whether every job has ended, and the ones that did not end well.

    `seen` is what states() already said about these jobs, for a caller asking more than one
    question of the same answer: each asking is two commands on a login node.
    """
    return ended(states(job_ids) if seen is None else seen, job_ids)


def ended(seen, job_ids):
    """The same question, asked of an answer already in hand.

    Apart so that a flow can be read in one pair of calls rather than one pair a card. Asked card
    by card, a flow of five sent twenty squeue and sacct calls down a login node for every reading
    of it, and the page reads a flow that is running every twenty seconds. A controller slow enough
    to time out did so once per card, which is what made the one reading somebody was waiting for
    take longer than the launcher would wait.
    """
    bad, pending = [], False
    for jid in (str(j) for j in job_ids):
        state = seen.get(jid, "")
        if not state:
            pending = True                      # not in either answer yet: too soon to say
        elif state.startswith(LIVE):
            pending = True
        elif not state.startswith("COMPLETED"):
            bad.append((jid, state))
    return (not pending, bad)


#: What Slurm calls a job that is still waiting for a node: held, pending, or put back in the
#: queue after the node under it failed. Anything else has had a node, or has ended.
WAITING_FOR_A_NODE = ("PENDING", "REQUEUED", "REQUEUE_HOLD", "REQUEUE_FED", "RESV_DEL_HOLD")


def started(job_ids, seen=None):
    """Whether any of these jobs has had a node: it is running now, or it has already ended.

    A card's jobs are queued the moment its tool is started, and on a busy partition they can wait
    a day for a GPU. Until one of them has a node the card is queued, not running - and saying
    "running for nine hours" of a job that has spent them in the queue sends somebody looking for
    output that does not exist yet. A job Slurm has not heard of yet has not started either.
    """
    seen = states(job_ids) if seen is None else seen
    for jid in (str(j) for j in job_ids):
        state = seen.get(jid, "")
        if state and not state.startswith(WAITING_FOR_A_NODE):
            return True
    return False
