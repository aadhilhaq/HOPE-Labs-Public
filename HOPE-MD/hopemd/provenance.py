"""Which HOPE-MD rendered a run: the package version and, from a git checkout, the commit.

The commit is read once, when the process starts, because that is the code the process runs: a
checkout pulled under a running server already names a newer commit than the one serving. Git
refuses to read a repository another account owns, and the lab's shared install is one, unless
it is named as safe; the name is passed on the command line, the one place an ordinary user can
set it.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from . import __version__

ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str) -> str:
    try:
        r = subprocess.run(["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT), *args],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def read_code() -> dict:
    """{"hopemd": the version, "commit": the short hash, or "" outside a git checkout,
    "modified": tracked files differ from that commit}."""
    commit = _git("rev-parse", "--short=7", "HEAD")
    modified = bool(commit) and bool(_git("status", "--porcelain", "--untracked-files=no"))
    return {"hopemd": __version__, "commit": commit, "modified": modified}


#: the code this process runs, read when it started
CODE = read_code()


def describe(entry: dict) -> str:
    """'HOPE-MD 0.1.0, commit 1f5f904', with what is known and nothing more."""
    text = f"HOPE-MD {entry.get('hopemd') or '?'}"
    if entry.get("commit"):
        return text + f", commit {entry['commit']}" + (" with uncommitted changes" if entry.get("modified") else "")
    return text + ", commit not recorded"


def stamp(spec, step: str, render=None) -> dict:
    """Record this process's code in spec.rendered_by (one entry per render that wrote the run:
    submit, prepare, mmgbsa) and, when the render writes README.txt, list every entry there."""
    entry = {"step": step, **CODE, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    spec.rendered_by = [*(spec.rendered_by or []), entry]
    if render is not None:
        lines = [f"rendered by:  {describe(e)} ({e.get('step', '?')}, {e.get('at', '?')})" for e in spec.rendered_by]
        for f in render.files:
            if f.path == "README.txt":
                f.text = f.text.rstrip("\n") + "\n\n" + "\n".join(lines) + "\n"
    return entry
