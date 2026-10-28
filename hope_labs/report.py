"""What a person says went wrong, with the facts that would otherwise have to be asked for.

By Aadhil Haq

A run that fails leaves its reason in four places: the card's note, Slurm's accounting, the job's
own log, and the settings it was given. Somebody reporting it has none of those to hand, so what
usually arrives is "the flow failed", and the next three messages are the author asking for a job
id, a folder and a log. This gathers all four at the moment of the report, writes the lot into the
reports folder beside the HOPE Labs install, and hands the same text back for the person
to copy wherever they like.

Nothing leaves the cluster. The report is a file under REPORTS, beside the install, and the
person sees the whole of it before it is written: a report is about their own work, and what it
quotes from their run folder is theirs, so they read it first rather than finding out afterwards.

    python3 -m hope_labs.report failure '{"folder": "...", "said": "..."}'
"""
import json
import os
import pwd
import re
import socket
import subprocess
import sys
import time

#: Where reports land: beside the HOPE Labs install, where whoever looks after it reads them, or
#: HOPELABS_REPORTS.
REPORTS = os.environ.get("HOPELABS_REPORTS") or os.path.join(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "hope_labs_reports")

#: How much of a job's log goes in. Enough to carry a traceback and the lines around it, little
#: enough that a report stays readable and a run's whole output is not copied into it.
LOG_LINES = 80

#: The kinds of report, and the one line each puts at the top so a reader knows what they opened.
KINDS = {
    "failure": "A run that did not finish",
    "feedback": "Feedback on HOPE Labs",
    "idea": "Something that would help",
}


def _ran(line, seconds=25):
    """A short command's output, or an empty string. Never raises: a report is best effort."""
    try:
        out = subprocess.run(line, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             timeout=seconds)
        return out.stdout.decode("utf-8", "replace").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _who():
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return os.environ.get("USER") or "unknown"


def _jobs(ids):
    """Slurm's own account of each job: what became of it, for how long, and why it stopped."""
    ids = [str(j) for j in ids if str(j).strip().isdigit()]
    if not ids:
        return ""
    return _ran("sacct -j %s --format=JobID%%14,JobName%%26,State%%14,Elapsed,ExitCode,"
                "MaxRSS,NodeList%%16 2>&1" % ",".join(ids))


def _log(rundir):
    """The tail of the newest log in a run folder, with its name, or a line saying there is none."""
    if not rundir or not os.path.isdir(rundir):
        return "", "no run folder to read" if rundir else ""
    newest, when = "", 0
    for here, _dirs, files in os.walk(rundir):
        # Only the top two levels: a design run holds thousands of files further down, and the
        # log that says why a job stopped is never one of them.
        if here[len(rundir):].count(os.sep) > 1:
            continue
        for name in files:
            if not re.search(r"\.(out|err|log)$", name):
                continue
            path = os.path.join(here, name)
            try:
                age = os.path.getmtime(path)
            except OSError:
                continue
            if age > when:
                newest, when = path, age
    if not newest:
        return "", "no .out, .err or .log in the run folder"
    try:
        with open(newest, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as why:
        return newest, "could not be read: %s" % why
    return newest, "".join(lines[-LOG_LINES:]).rstrip()


def _step_log(folder, row):
    """The log of the job that started a flow's card, from the flow's own logs folder.

    That is where a card that failed before its tool ever ran left its reason - an input that did
    not arrive, a refusal from sbatch - and such a card has no run folder to look in. The job the
    record names first, since a card woken more than once has several; else the newest of them.
    """
    where = os.path.join(folder or "", "logs")
    nid = str(row.get("id") or row.get("card") or "")
    if not folder or not nid or not os.path.isdir(where):
        return "", ""
    mine = []
    for name in os.listdir(where):
        got = re.match(r"%s-(\d+)\.(out|err)$" % re.escape(nid), name)
        if got:
            mine.append((int(got.group(1)), name))
    step = str(row.get("step") or "")
    pick = [n for j, n in mine if str(j) == step] or [n for _j, n in sorted(mine, reverse=True)]
    if not pick:
        return "", ""
    path = os.path.join(where, pick[0])
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as why:
        return path, "could not be read: %s" % why
    return path, "".join(lines[-LOG_LINES:]).rstrip()


def _versions(install):
    """What was running: the checkout's commit, and the launcher's own version if it said one."""
    out = []
    head = _ran("cd %s && git log -1 --format='%%h %%cd' --date=format:%%Y-%%m-%%d 2>/dev/null"
                % install)
    if head:
        out.append("cluster checkout: %s (%s)" % (head, install))
    return out


def build(kind, said, contact="", context=None):
    """The report, as text. `context` is what the page knows and the cluster does not."""
    context = dict(context or {})
    kind = kind if kind in KINDS else "feedback"
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    install = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    out = ["# %s" % KINDS[kind], ""]
    out.append("%s, by %s on %s" % (now, _who(), socket.gethostname()))
    if contact.strip():
        out.append("Reply to: %s" % contact.strip())
    out.append("")

    out.append("## What they said")
    out.append("")
    out.append(said.strip() or "(nothing was typed)")
    out.append("")

    where = context.get("folder") or ""
    cards = context.get("cards") or []
    if where or cards:
        out.append("## The flow")
        out.append("")
        if context.get("name"):
            out.append("- name: %s" % context["name"])
        if where:
            out.append("- folder: %s" % where)
        if context.get("overall"):
            out.append("- overall: %s" % context["overall"])
        for row in cards:
            line = "- %s: %s" % (row.get("card") or "?", row.get("state") or "?")
            if row.get("jobs"):
                line += ", jobs %s" % " ".join(str(j) for j in row["jobs"])
            if row.get("note"):
                line += " - %s" % row["note"]
            out.append(line)
        out.append("")

    ids = [j for row in cards for j in (row.get("jobs") or [])]
    table = _jobs(ids)
    if table:
        out += ["## What Slurm says", "", "```", table, "```", ""]

    # The log of the card being asked about: the one the person pressed Report this on, else the
    # first that failed, else the first that stopped, else the last one that ran. Only one,
    # because a report with five logs in it is a report nobody reads. It used to be the last card
    # that failed, so a flow with two failures quoted the one nobody had asked about; and a card
    # that failed before its tool ran has no run folder, so its report had no log at all, though
    # the reason was in the flow's own logs folder all along.
    about = context.get("about") or ""
    worst = ([r for r in cards if about and (r.get("id") or r.get("card")) == about]
             or [r for r in cards if r.get("state") == "failed"]
             or [r for r in cards if r.get("state") == "stopped"]
             or [r for r in cards if r.get("rundir")][-1:])
    if worst:
        path, tail = _log(worst[0].get("rundir") or "")
        if not path and where:
            got = _step_log(where, worst[0])
            if got[0]:
                path, tail = got
        if path or tail:
            out.append("## The last %d lines of %s" % (LOG_LINES, path or "its log"))
            out += ["", "```", tail or "(empty)", "```", ""]

    settings = context.get("settings")
    if settings:
        out += ["## What the card was given", "", "```",
                json.dumps(settings, indent=2, sort_keys=True)[:4000], "```", ""]

    out.append("## Where it was running")
    out.append("")
    for line in _versions(install) + [
            "launcher: %s" % (context.get("launcher") or "not said"),
            "page: %s" % (context.get("page") or "not said"),
            "cluster: %s" % (context.get("cluster") or socket.gethostname())]:
        out.append("- %s" % line)
    out.append("")
    return "\n".join(out)


def write(text, kind="feedback"):
    """Put the report in the reports folder. Returns the path, or raises OSError saying why."""
    os.umask(0o002)
    if not os.path.isdir(REPORTS):
        os.makedirs(REPORTS, 0o2775)
    name = "%s-%s-%s.md" % (time.strftime("%Y%m%d-%H%M%S"), _who(), kind)
    path = os.path.join(REPORTS, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    try:
        os.chmod(path, 0o664)
    except OSError:
        pass
    return path


def main(argv):
    if len(argv) < 2:
        print(json.dumps({"problem": "usage: report <kind> <json on stdin or argv[2]>"}))
        return 2
    kind = argv[1]
    raw = argv[2] if len(argv) > 2 else sys.stdin.read()
    try:
        asked = json.loads(raw or "{}")
    except ValueError as why:
        print(json.dumps({"problem": "that request was not JSON: %s" % why}))
        return 2
    text = build(kind, asked.get("said", ""), asked.get("contact", ""), asked.get("context"))
    if asked.get("preview"):
        print(json.dumps({"text": text}))
        return 0
    try:
        path = write(text, kind)
    except OSError as why:
        print(json.dumps({"problem": "the report could not be written: %s" % why, "text": text}))
        return 1
    print(json.dumps({"path": path, "text": text}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
