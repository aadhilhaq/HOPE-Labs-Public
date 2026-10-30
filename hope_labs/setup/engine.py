"""Installing a tool into the HOPE Labs folder, step by step, in a process that outlives the page.

By Aadhil Haq

An install is a list of steps (recipes.py says which), run in order by one process started on the
login node and left running there: closing the browser, the launcher or the laptop does not stop
it. Each step can tell whether it has already been done, so an install that was stopped half way,
by a dropped connection, a login node rebooted or a person pressing Stop, carries on from where it
got to when it is started again, rather than downloading eight gigabytes twice.

What the page reads, and nothing else, is the state file and the log:

    <folder>/setup/<tool>.json        where the install has got to, refreshed every few seconds
    <folder>/setup/logs/<tool>.log    everything every step printed, in order

The state carries a heartbeat. A process that stops without saying so (killed with the login
node, say) leaves a state that claims to be installing; the heartbeat going stale is how the page
knows it is not, on whichever login node it is asked from.

Nothing an install runs may write to the person's home folder. Home is small on every cluster the
lab has used, and conda, pip and torch all keep caches there by default, by the thousand files. So
HOME itself is pointed into the setup folder for everything an install runs, and each cache is
named outright as well.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from . import labs_root, setup_dir
from . import profile as profilemod

#: How often a running install says it is alive, and how long a silence means it has stopped.
BEAT = 15
STALE = 180

#: The states an install can be in, as the page shows them.
ABSENT, PRESENT, INSTALLING, CHECKING, READY, FAILED, STOPPED, REMOVING = (
    "absent", "present", "installing", "checking", "ready", "failed", "stopped", "removing")
BUSY = (INSTALLING, CHECKING, REMOVING)


class Failed(RuntimeError):
    """A step that could not be done, in a sentence a person can act on."""


class Stopped(RuntimeError):
    """The person asked for the install to stop."""


# ---------------------------------------------------------------------------- the files

def state_path(root, key):
    return os.path.join(setup_dir(root), key + ".json")


def log_path(root, key):
    return os.path.join(setup_dir(root), "logs", key + ".log")


def stop_path(root, key):
    return os.path.join(setup_dir(root), key + ".stop")


def read_state(root, key):
    try:
        with open(state_path(root, key), encoding="utf-8") as handle:
            doc = json.load(handle)
        if isinstance(doc, dict):
            return doc
    except (OSError, ValueError):
        pass
    return {}


def write_state(root, key, doc):
    path = state_path(root, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = "%s.%d.tmp" % (path, os.getpid())
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(doc, handle, indent=2)
    os.replace(temp, path)


def alive(doc):
    """Whether a state that says it is busy has a process behind it that is still beating."""
    return (doc.get("state") in BUSY
            and time.time() - float(doc.get("beat") or 0) < STALE)


def status(root, recipe):
    """What the page shows for one tool: its state, with a dead install called stopped."""
    doc = dict(read_state(root, recipe.key))
    where = recipe.where(root)
    if not doc:
        doc = {"tool": recipe.key, "state": PRESENT if os.path.exists(where) else ABSENT}
        if doc["state"] == PRESENT:
            doc["detail"] = ("there is already something at %s that Set up did not put there, "
                             "so Set up leaves it alone" % where)
    elif doc.get("state") in BUSY and not alive(doc):
        removing = doc.get("state") == REMOVING
        doc["state"] = STOPPED
        doc["detail"] = ("the removal stopped part way; Remove finishes it" if removing else
                         "the install stopped without finishing; Install carries on from here")
    doc["install"] = where
    return doc


def log_tail(root, key, since=0, most=200000):
    """(the log from byte `since`, where it ends), so the page asks only for what is new."""
    path = log_path(root, key)
    try:
        size = os.path.getsize(path)
    except OSError:
        return "", 0
    since = max(0, min(int(since or 0), size))
    if size - since > most:
        since = size - most
    with open(path, "rb") as handle:
        handle.seek(since)
        text = handle.read().decode("utf-8", "replace")
    return text, size


# ---------------------------------------------------------------------------- one install

class Context:
    """What a step is given: the folder, the cluster, a log, and a way to run things."""

    def __init__(self, root, recipe, account="", action="install"):
        self.root = root
        self.recipe = recipe
        self.account = account
        self.action = action
        self.profile = profilemod.load(root)
        os.makedirs(os.path.dirname(log_path(root, recipe.key)), exist_ok=True)
        self._log = open(log_path(root, recipe.key), "a", encoding="utf-8", buffering=1)
        self.state = read_state(root, recipe.key)
        self._last_beat = 0.0

    # --- saying
    def say(self, text):
        for line in str(text).splitlines() or [""]:
            self._log.write("%s  %s\n" % (time.strftime("%H:%M:%S"), line))

    def beat(self, detail=None, force=False, **more):
        """Refresh the state: the heartbeat, and what is being done now."""
        if detail is not None:
            self.state["detail"] = detail
        self.state.update(more)
        now = time.time()
        if force or now - self._last_beat >= 2:
            self.state["beat"] = now
            write_state(self.root, self.recipe.key, self.state)
            self._last_beat = now
        if os.path.exists(stop_path(self.root, self.recipe.key)):
            raise Stopped("stopped when asked")

    # --- where things go
    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def env(self):
        """The environment every command of an install runs in: nothing reaches home."""
        home = self.path("setup", "home")
        cache = self.path("setup", "cache")
        for folder in (home, cache):
            os.makedirs(folder, exist_ok=True)
        env = dict(os.environ)
        for name in ("PYTHONPATH", "PYTHONHOME", "CONDA_PREFIX", "CONDA_DEFAULT_ENV",
                     "VIRTUAL_ENV", "PIP_REQUIRE_VIRTUALENV"):
            env.pop(name, None)
        env.update(HOME=home, XDG_CACHE_HOME=cache, PIP_CACHE_DIR=os.path.join(cache, "pip"),
                   PIP_DISABLE_PIP_VERSION_CHECK="1", PYTHONNOUSERSITE="1",
                   MAMBA_ROOT_PREFIX=self.path("tools", "mamba"),
                   CONDA_PKGS_DIRS=self.path("tools", "mamba", "pkgs"),
                   MAMBA_NO_BANNER="1", TMPDIR=env.get("TMPDIR") or "/tmp")
        # The container runtime's download cache and its scratch, for the tools installed as an
        # image. Both are large, and both go under the HOPE Labs folder rather than in the home
        # folder, where the runtime puts them when nobody says otherwise.
        from .. import images as imagesmod
        env.update(imagesmod.runtime_env(self.root))
        proxy = profilemod.proxy(self.root)
        if proxy:
            for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
                env[name] = proxy
        return env

    # --- doing
    def sh(self, argv, what="", timeout=None, cwd=None):
        """Run one command with everything it prints going into the log. Raises Failed.

        Its output is read by a thread of its own and handed over a queue, so this one wakes
        every second whether or not anything was printed: pip spends minutes unpacking without a
        word, and a heartbeat that waited for a line would go quiet long enough for the page to
        call a working install stopped, and a Stop would wait for the next line to be noticed.
        """
        import queue
        import threading
        self.say("$ " + " ".join(argv))
        self.beat(what or os.path.basename(argv[0]), force=True)
        started = time.time()
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, env=self.env(), cwd=cwd or self.root,
                                    text=True, errors="replace", bufsize=1)
        except OSError as why:
            raise Failed("%s could not be started: %s" % (argv[0], why))
        lines = queue.Queue()

        def read():
            for line in proc.stdout:
                lines.put(line)
            lines.put(None)

        threading.Thread(target=read, daemon=True).start()
        last = []
        try:
            while True:
                try:
                    line = lines.get(timeout=1)
                except queue.Empty:
                    line = ""
                if line is None:
                    break
                line = line.rstrip()
                if line:
                    self.say("  " + line[:500])
                    last = (last + [line])[-12:]
                self.beat()
                if timeout and time.time() - started > timeout:
                    proc.kill()
                    raise Failed("%s took more than %d minutes and was stopped"
                                 % (what or argv[0], timeout // 60))
        except Stopped:
            proc.kill()
            raise
        code = proc.wait()
        if code != 0:
            raise Failed("%s failed (status %d): %s"
                         % (what or argv[0], code, " / ".join(last[-3:])[-600:]))
        return code

    def fetch(self, url, dest, what="", expect_at_least=0):
        """Download to a file, carrying on from where an earlier attempt stopped.

        Downloads of several gigabytes over a login node's connection do get cut, and Boltz-2's
        own downloader writes straight to the final name and then trusts whatever is there. So the
        file is written as <dest>.part, carried on with a Range request, checked against the
        length the server gave, and only then given its name.
        """
        part = dest + ".part"
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        what = what or os.path.basename(dest)
        for attempt in range(1, 6):
            have = os.path.getsize(part) if os.path.exists(part) else 0
            headers = {"User-Agent": "hope-labs-setup"}
            if have:
                headers["Range"] = "bytes=%d-" % have
            try:
                answer = urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                                timeout=60)
            except urllib.error.HTTPError as why:
                if why.code == 416 and have:            # the part is already the whole file
                    break
                self.say("  %s: the server said %s; trying again" % (what, why))
                time.sleep(5 * attempt)
                continue
            except Exception as why:                            # noqa: BLE001
                self.say("  %s: %s; trying again" % (what, why))
                time.sleep(5 * attempt)
                continue
            with answer:
                resumed = answer.status == 206
                length = int(answer.headers.get("Content-Length") or 0)
                total = (have + length) if resumed else length
                if not resumed:
                    have = 0
                self.say("  %s: %s%s" % (what, "carrying on at %.2f GB of " % (have / 1e9)
                                         if resumed else "", _gb(total)))
                try:
                    with open(part, "ab" if resumed else "wb") as out:
                        while True:
                            chunk = answer.read(1 << 20)
                            if not chunk:
                                break
                            out.write(chunk)
                            have += len(chunk)
                            self.beat("%s: %s of %s" % (what, _gb(have), _gb(total)))
                except Stopped:
                    raise
                except Exception as why:                        # noqa: BLE001
                    self.say("  %s: cut off at %s (%s); carrying on" % (what, _gb(have), why))
                    time.sleep(3 * attempt)
                    continue
            if total and have < total:
                self.say("  %s: %s of %s arrived; carrying on" % (what, _gb(have), _gb(total)))
                continue
            break
        size = os.path.getsize(part) if os.path.exists(part) else 0
        if size < max(1, expect_at_least):
            raise Failed("%s did not download: %s arrived" % (what, _gb(size)))
        os.replace(part, dest)
        self.say("  %s: %s, done" % (what, _gb(size)))
        return dest

    def close(self):
        try:
            self._log.close()
        except OSError:
            pass


def _gb(n):
    return "%.2f GB" % (n / 1e9) if n >= 5e7 else "%.1f MB" % (n / 1e6)


def run(root, recipe, action="install", account=""):
    """Run an install, or only its check, or a removal, to the end. Returns the final state."""
    if action == "remove":
        return removal(root, recipe)
    key = recipe.key
    previous = read_state(root, key)
    if alive(previous) and previous.get("pid") != os.getpid():
        raise Failed("%s is already being installed (by %s on %s, started %s)" % (
            recipe.name, previous.get("by", "someone"), previous.get("host", "a login node"),
            time.strftime("%H:%M", time.localtime(previous.get("started") or 0))))
    if not previous:
        for where in recipe.claims(root):
            if os.path.exists(where):
                raise Failed("there is already something at %s that Set up did not put there. Set "
                             "up leaves it alone: move it aside, or remove it, to install here."
                             % where)
    try:
        os.remove(stop_path(root, key))
    except OSError:
        pass
    ctx = Context(root, recipe, account=account, action=action)
    done = dict(previous.get("steps") or {})
    ctx.state = {"tool": key, "name": recipe.name, "action": action,
                 "state": CHECKING if action == "check" else INSTALLING,
                 "started": time.time(), "beat": time.time(), "pid": os.getpid(),
                 "host": socket.gethostname(), "by": os.environ.get("USER") or "",
                 "steps": done, "step": "", "detail": "starting",
                 "receipt": previous.get("receipt") or {}, "check": previous.get("check") or {}}
    # A GPU check already in the queue is waited for rather than asked again.
    if previous.get("gpucheck_job"):
        ctx.state["gpucheck_job"] = previous["gpucheck_job"]
    ctx.beat(force=True)
    ctx.say("")
    ctx.say("=== %s %s in %s, by %s on %s" % (
        "Checking" if action == "check" else "Installing", recipe.name, root,
        ctx.state["by"], ctx.state["host"]))
    steps = recipe.steps()
    if action == "check":
        steps = [s for s in steps if s.name == "check"]
    try:
        for step in steps:
            ctx.state["step"] = step.name
            ctx.beat(step.title, force=True)
            if action != "check" and step.name != "check" and not step.always \
                    and step.done(ctx):
                ctx.say("--- %s: already done" % step.title)
                done.setdefault(step.name, {"done": time.time(), "took": 0})
                continue
            ctx.say("--- %s" % step.title)
            began = time.time()
            step.run(ctx)
            if not step.always and not step.done(ctx):
                raise Failed("%s finished but did not leave what it should have" % step.title)
            done[step.name] = {"done": time.time(), "took": round(time.time() - began)}
            ctx.state["steps"] = done
            ctx.beat(force=True)
        ctx.state.update(state=READY, step="", detail="installed and checked",
                         finished=time.time(), problem="")
        ctx.say("=== %s is ready" % recipe.name)
    except Stopped:
        ctx.state.update(state=STOPPED, detail="stopped when asked; Install carries on from here",
                         finished=time.time())
        ctx.say("=== stopped when asked")
    except Failed as why:
        ctx.state.update(state=FAILED, problem=str(why), detail=str(why), finished=time.time())
        ctx.say("=== FAILED: %s" % why)
    except Exception as why:                                    # noqa: BLE001
        import traceback
        ctx.say(traceback.format_exc())
        ctx.state.update(state=FAILED, problem="the installer hit an error: %s" % why,
                         detail="the installer hit an error", finished=time.time())
    finally:
        ctx.state["beat"] = time.time()
        write_state(root, key, ctx.state)
        ctx.close()
        try:
            os.remove(stop_path(root, key))
        except OSError:
            pass
    return ctx.state


def removal(root, recipe):
    """Take a tool Set up installed off this cluster: its folders, then its record.

    Only what Set up put there, and only inside the HOPE Labs folder. A tool Set up has no record
    of installing is left alone, as Install leaves alone a folder it did not make, and a path
    that resolves outside the folder (through a link, say) is refused rather than followed. The
    package manager and its download cache stay, because every other tool here uses them.

    Returns the state the page then shows: absent, with the files and the room it gave back.
    """
    key = recipe.key
    previous = read_state(root, key)
    if not previous:
        raise Failed("Set up did not install %s here, so it leaves it alone" % recipe.name)
    if alive(previous) and previous.get("pid") != os.getpid():
        raise Failed("%s is being %s; stop that first" % (
            recipe.name, {INSTALLING: "installed", CHECKING: "checked",
                          REMOVING: "removed"}.get(previous.get("state"), "worked on")))
    home = os.path.realpath(root)
    paths = []
    for path in recipe.owns(root):
        real = os.path.realpath(path)
        if real == home or not real.startswith(home + os.sep):
            raise Failed("%s is not inside %s, so Set up will not remove it" % (path, root))
        if os.path.lexists(path):
            paths.append(path)
    try:
        os.remove(stop_path(root, key))
    except OSError:
        pass
    ctx = Context(root, recipe, action="remove")
    ctx.state = {"tool": key, "name": recipe.name, "action": "remove", "state": REMOVING,
                 "started": time.time(), "beat": time.time(), "pid": os.getpid(),
                 "host": socket.gethostname(), "by": os.environ.get("USER") or "",
                 "step": "", "detail": "starting"}
    ctx.beat(force=True)
    ctx.say("")
    ctx.say("=== Removing %s from %s, by %s on %s" % (recipe.name, root, ctx.state["by"],
                                                       ctx.state["host"]))
    files, size, seen = 0, 0, 0
    try:
        for path in paths:
            ctx.say("--- %s" % path)
            for top, dirs, names in os.walk(path, topdown=False):
                for name in names + dirs:
                    one = os.path.join(top, name)
                    seen += 1
                    if seen % 2000 == 0:
                        # every name, counted or not, so a long run of hard links still beats,
                        # and a Stop asked for meanwhile is heard
                        ctx.beat("removed %d files (%s)" % (files, _gb(size)))
                    try:
                        st = os.lstat(one)
                        if os.path.isdir(one) and not os.path.islink(one):
                            os.rmdir(one)
                        else:
                            os.remove(one)
                            # Only the last link of a file gives anything back. A conda package
                            # is hard-linked into the environment from the package cache, so
                            # counting every name told HOPE-MD's removal 85,266 files and 6.44 GB
                            # for a folder of 59,953 inodes and 3.8 GB.
                            if st.st_nlink <= 1:
                                files += 1
                                size += st.st_size
                    except FileNotFoundError:
                        continue
            if os.path.islink(path) or os.path.isfile(path):
                os.remove(path)
            elif os.path.isdir(path):
                os.rmdir(path)
        ctx.say("=== %s is removed: %d files, %s given back" % (recipe.name, files, _gb(size)))
        os.remove(state_path(root, key))
        return {"tool": key, "state": ABSENT, "removed": {"files": files, "bytes": size},
                "install": recipe.where(root)}
    except Stopped:
        ctx.state.update(state=STOPPED, detail="stopped part way when asked; Remove finishes it",
                         finished=time.time())
        ctx.say("=== stopped when asked")
    except OSError as why:
        ctx.state.update(state=FAILED, problem="could not remove %s: %s" % (
            getattr(why, "filename", "") or "a file", why.strerror or why),
            detail="the removal stopped part way", finished=time.time())
        ctx.say("=== FAILED: %s" % ctx.state["problem"])
    finally:
        if os.path.exists(state_path(root, key)):
            ctx.state["beat"] = time.time()
            write_state(root, key, ctx.state)
        ctx.close()
        try:
            os.remove(stop_path(root, key))
        except OSError:
            pass
    return ctx.state


def start(root, key, action="install", account="", python=None):
    """Start an install in a process of its own, left running on this login node.

    A session of its own, so it is not in the page's process group and nothing that stops the
    page stops it. Its output goes to the install's log as well as the log the steps write, so a
    Python too old to even start it still leaves a sentence where the page looks.
    """
    from .recipes import RECIPES
    recipe = RECIPES[key]
    now = status(root, recipe)
    if now.get("state") in BUSY:
        raise Failed("%s is already being %s" % (recipe.name, {
            CHECKING: "checked", REMOVING: "removed"}.get(now.get("state"), "installed")))
    if now.get("state") == PRESENT:
        raise Failed(now["detail"])
    if action == "remove" and now.get("state") == ABSENT:
        raise Failed("%s is not installed here" % recipe.name)
    log = log_path(root, key)
    os.makedirs(os.path.dirname(log), exist_ok=True)
    argv = [python or sys.executable, "-m", "hope_labs.setup", action, key, "--root", root]
    if account:
        argv += ["--account", account]
    checkout = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = dict(os.environ, PYTHONPATH=checkout, HOPELABS_ROOT=root)
    with open(log, "a", encoding="utf-8") as out:
        proc = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=env, cwd=checkout,
                                start_new_session=True)
    # Written at once, so the page sees it start rather than an absent tool for a moment.
    state = dict(read_state(root, key), tool=key, name=recipe.name, action=action,
                 state={"check": CHECKING, "remove": REMOVING}.get(action, INSTALLING),
                 pid=proc.pid,
                 host=socket.gethostname(), by=os.environ.get("USER") or "",
                 started=time.time(), beat=time.time(), detail="starting", problem="")
    write_state(root, key, state)
    return proc.pid


def stop(root, key):
    """Ask a running install to stop. It stops at its next heartbeat, between two lines of output,
    and a check that has a job in the queue cancels the job on its way out."""
    os.makedirs(setup_dir(root), exist_ok=True)
    with open(stop_path(root, key), "w") as handle:
        handle.write("%s %s\n" % (os.environ.get("USER") or "", time.ctime()))
    return True


# ---------------------------------------------------------------------------- the queue

def job_state(job_id):
    """What Slurm says about a job: PENDING, RUNNING, COMPLETED, FAILED, ... or "" if nothing."""
    code, text = profilemod.run(["squeue", "-h", "-j", str(job_id), "-o", "%T"], timeout=30)
    text = text.strip()
    if code == 0 and text and "invalid" not in text.lower():
        return text.splitlines()[0].strip()
    code, text = profilemod.run(["sacct", "-n", "-X", "-P", "-j", str(job_id), "-o", "State"],
                                timeout=30)
    if code == 0 and text.strip():
        return text.strip().splitlines()[0].split()[0].strip()
    return ""


FINISHED = ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL",
            "PREEMPTED", "BOOT_FAIL", "DEADLINE")


def submit(ctx, script, folder, what):
    """Queue a job script written into `folder`. Returns its id."""
    path = os.path.join(folder, "job.sbatch")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(script)
    code, text = profilemod.run(["sbatch", "--parsable", path], timeout=60)
    found = re.search(r"^\s*(\d+)", text or "")
    if code != 0 or not found:
        raise Failed("sbatch refused %s: %s" % (what, (text or "").strip()[-400:]))
    ctx.say("  queued %s as job %s" % (what, found.group(1)))
    return found.group(1)


def wait_for(ctx, job_id, what, poll=15, gone_after=6):
    """Wait for a job to finish, saying where it is. Returns Slurm's last word on it.

    A job neither squeue nor sacct knows (a cluster without accounting, once it has left the
    queue) is taken to have finished after a few polls; the caller then judges it by what it
    left, which is the only judgment that matters anyway.
    """
    began, unknown, last = time.time(), 0, ""
    while True:
        state = job_state(job_id)
        if state != last:
            ctx.say("  job %s: %s" % (job_id, state or "not listed"))
            last = state
        if state in FINISHED or state.startswith("CANCELLED"):
            return state
        if not state:
            unknown += 1
            if unknown >= gone_after:
                return ""
        mins = int((time.time() - began) // 60)
        ctx.beat("%s: job %s %s, %d min" % (what, job_id,
                                             {"PENDING": "waiting in the queue",
                                              "RUNNING": "running"}.get(state, state.lower()
                                                                        or "finishing"),
                                             mins), force=True, job=job_id)
        for _ in range(poll):
            time.sleep(1)
            ctx.beat()


def cancel_job(job_id):
    profilemod.run(["scancel", str(job_id)], timeout=30)


def free_room(path):
    """(free GB, free files) where `path` is, from the file system and any quota it reports.

    The smaller of the two answers wins. statvfs says what the disk has; a quota is what this
    person may still use of it, and on a shared cluster the quota is nearly always the tighter.
    """
    while path and not os.path.exists(path):
        path = os.path.dirname(path)
    gb, files = None, None
    try:
        st = os.statvfs(path)
        gb = st.f_bavail * st.f_frsize / 1e9
        files = st.f_favail if st.f_files else None
    except OSError:
        pass
    for q_gb, q_files in _quotas(path):
        if q_gb is not None:
            gb = q_gb if gb is None else min(gb, q_gb)
        if q_files is not None:
            files = q_files if files is None else min(files, q_files)
    return gb, files


def _quotas(path):
    """What the cluster's quota tools say is left for the folder holding `path`.

    Two tools, because there is no standard one: showquota, as TAMU's clusters have it, and lfs
    quota on any Lustre file system. Both are read loosely and anything not understood is left
    out: a quota nobody could read is not a reason to refuse an install.
    """
    out = []
    code, text = profilemod.run(["showquota"], timeout=60)
    if code == 0:
        best = None
        for where, gb, files in showquota_rows(text):
            if (path + "/").startswith(where.rstrip("/") + "/"):
                if best is None or len(where) > len(best[0]):
                    best = (where, gb, files)
        if best:
            out.append(best[1:])
    if shutil.which("lfs"):
        user = os.environ.get("USER") or ""
        code, text = profilemod.run(["lfs", "quota", "-q", "-u", user, path], timeout=60)
        if code == 0:
            for line in text.splitlines():
                cols = line.split()
                if len(cols) >= 7 and cols[0].startswith("/"):
                    used_kb, limit_kb = _int(cols[1]), _int(cols[3]) or _int(cols[2])
                    fused, flimit = _int(cols[5]), _int(cols[7] if len(cols) > 7 else cols[6])
                    out.append(((limit_kb - used_kb) * 1024 / 1e9 if limit_kb else None,
                                (flimit - fused) if flimit else None))
    return out


def showquota_rows(text):
    """[(folder, free GB, free files)] from what showquota prints, as TAMU's clusters have it.

    Every folder a person may write to and has a quota on is a row: their home, their scratch,
    and each group's space. Kept apart from running it, because the launcher reads the same
    answer over its connection to find where somebody can install.
    """
    rows = []
    for line in (text or "").splitlines():
        cols = line.split()
        if len(cols) >= 5 and cols[0].startswith("/"):
            used, limit = _bytes(cols[1]), _bytes(cols[2])
            fused, flimit = _int(cols[3]), _int(cols[4])
            rows.append((cols[0], (limit - used) / 1e9 if used is not None and limit else None,
                         (flimit - fused) if flimit else None))
    return rows


def _bytes(text):
    found = re.match(r"([\d.]+)\s*([KMGTP]?)", str(text or "").upper())
    if not found:
        return None
    return float(found.group(1)) * {"": 1, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12,
                                    "P": 1e15}[found.group(2)]


def _int(text):
    found = re.match(r"\s*(\d+)", str(text or ""))
    return int(found.group(1)) if found else 0
