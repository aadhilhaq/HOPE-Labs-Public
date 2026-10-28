"""The surfaces the launcher's structure viewer draws, computed by MSMS where the structure is.

By Aadhil Haq

    python3 -m hope_labs.msms <structure> [<install> ...]

The installs are the tools the suite may be beside, as the launcher knows them, and the HOPE Labs
checkout. MSMS is looked for as surface.find_msms looks: $MSMS_EXE first, then an msms program in
one of those places, then the suite, by $ADCPSUITE_HOME or as each install names it or sits beside
it, then an msms on the PATH. Where none is found, or MSMS cannot triangulate the structure, the
answer says why and the viewer draws its own surface.
"""
from __future__ import annotations

import json
import os
import re
import sys

from . import surface

TOLD = ("ADCPSUITE_HOME",)


def told(install):
    """The folders an install's activate.sh names for its tools, in the order it names them."""
    try:
        with open(os.path.join(install, "activate.sh"), encoding="utf-8",
                  errors="replace") as fh:
            text = fh.read()
    except OSError:
        return []
    out = []
    for name in TOLD:
        # A value still holding a $ was not expanded when the file was written, and is not a
        # folder anybody can look in.
        for value in re.findall(r'^export %s="([^"$]*)"' % name, text, re.M):
            out += [part for part in value.split(":") if part]
    return out


def places(installs=()):
    """Where the ADCP 1.1 suite may be, in the order to look: for each install the suite its
    activate.sh names, the install itself and the folder holding it. $ADCPSUITE_HOME, which
    comes before them all, surface.find_suite reads itself."""
    out = []

    def add(path):
        path = (path or "").strip().rstrip("/")
        if path and path not in out:
            out.append(path)

    for install in installs or ():
        install = (install or "").strip().rstrip("/")
        if not install:
            continue
        for path in told(install):
            add(path)
        add(install)
        add(os.path.dirname(install))
    return out


def cache_dir():
    """Where answers are kept, so a structure is triangulated once: HOPE Labs' own cache on the
    cluster, $SCRATCH/.cache/hopelabs. None when there is
    no scratch folder, rather than the home folder: its quota is small, and a cache of surfaces
    would spend it. The cache is never an error; surface.cached carries on without one."""
    scratch = (os.environ.get("SCRATCH") or "").strip()
    if not scratch or not os.path.isabs(scratch):
        return None
    home = os.path.realpath(os.path.expanduser("~"))
    real = os.path.realpath(scratch)
    if real == home or real.startswith(home.rstrip(os.sep) + os.sep):
        return None
    return os.path.join(scratch, ".cache", "hopelabs", "surfaces")


def answer(path, installs=()):
    """The surface of one structure file, as surface.answer gives it: every atom but the waters,
    from the file's own text. Never raises."""
    exe = surface.find_msms(places(installs))
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as why:
        return {"source": "fallback", "why": "%s could not be read (%s)"
                % (os.path.basename(path), why.strerror or why)}
    return surface.answer(text, exe, cache_dir(), name=os.path.basename(path))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: python3 -m hope_labs.msms <structure> [<install> ...]", file=sys.stderr)
        return 2
    print(json.dumps(answer(argv[0], argv[1:])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
