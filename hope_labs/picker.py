"""The structure browser's server half: the places it offers, and what a run folder holds.

By Aadhil Haq

The dialog itself is /static/picker.js (hope_labs/pages/web/picker.js), shared by every page of
ours. It lists one folder at a time through the page's own /api/browse, which is
hope_labs/folding/browse.py, and asks this module for two things the listing cannot say:

* ``places``: where the dialog offers to start. The person's own scratch and home, this tool's
  run folder, the run folders of the lab's other tools that exist for this person, and the group
  folders they can read. Named the way a person names them ("My scratch", "HOPE-MD runs"), not
  by their paths, which are kept for the tooltip.
* ``run_structure``: for a folder that is a run of ours, the structure in it. A run folder holds
  its structures a level or two down (inputs/, build/, analysis/), so a person walking into one
  would otherwise have to know where to look; the dialog offers "Use this run's structure"
  instead.

Standard library only, like the rest of what the pages import: they run on a login node.
"""
from __future__ import annotations

import glob
import grp
import json
import os
import pwd

#: What a structure may be called, for the fallback search inside a run folder.
STRUCTURES = (".pdb", ".ent", ".cif", ".mmcif")

#: Where a run of each kind keeps the structure worth starting again from, best first. A record
#: of ours (submission.json) and a HOPE-MD run (spec.json) are told apart by the file that marks
#: them, which is also what makes a folder a run rather than a folder.
LAYOUTS = (
    ("submission.json", ("inputs/given.pdb", "inputs/given.cif", "out/topology.pdb",
                         "analysis/ensemble.pdb", "inputs/target.pdb", "inputs/receptor_in.pdb")),
    # A HOPE-MD run keeps the structure it was first given as input/original_*, as it was
    # deposited; everything after it is prepared for a force field, with caps and protonation
    # states in the residue names, which a sequence model reads as residues it has no letter for.
    ("spec.json", ("input/original_*", "input/complex.pdb", "build/prepared.pdb",
                   "analysis/r1_view.pdb", "input/receptor.pdb")),
)


def _user():
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except (OSError, KeyError):
        return os.environ.get("USER") or ""


def scratch():
    """$SCRATCH, or where it points on the TAMU clusters when a non-login shell did not set it."""
    user = _user()
    for where in (os.environ.get("SCRATCH"), "/scratch/user/%s" % user if user else ""):
        if where and os.path.isdir(where):
            return where
    return ""


def _expand(path):
    """A run folder as hope_labs/tools.py writes it: $SCRATCH and $USER left to the shell."""
    mine = scratch()
    text = str(path or "").replace("$SCRATCH", mine or "$SCRATCH").replace("$USER", _user())
    return os.path.expanduser(os.path.expandvars(text))


def _tools():
    """The lab's tools, as the launcher lists them. A tree made without the catalog (a cut-down
    edition) still has places, just none of the other tools' run folders."""
    try:
        from . import tools
    except ImportError:
        return ()
    return tools.TOOLS


def places(tool="", runs=()):
    """Where the dialog offers to start, in the order it offers them.

    Only folders that exist and can be opened: a place that answers "no permission" when it is
    pressed is a place that should not have been offered.
    """
    out, seen = [], set()

    def add(path, label, kind, section=""):
        if not path:
            return
        real = os.path.realpath(os.path.expanduser(path))
        if real in seen or not os.path.isdir(real) or not os.access(real, os.R_OK | os.X_OK):
            return
        seen.add(real)
        out.append({"label": label, "path": real, "kind": kind, "section": section})

    catalog = {one.key: one for one in _tools()}
    own = catalog.get(tool)
    add(scratch(), "My scratch", "scratch")
    add(os.path.expanduser("~"), "Home", "home")
    for root in runs or ():
        add(root, "%s runs" % (own.name if own else "This page's"), "runs")
    # The other tools' run folders under a heading of their own: somebody with every tool's runs
    # has a dozen of them, and they are where a structure made elsewhere is found.
    for one in catalog.values():
        if one.key == tool:
            continue
        for root in one.runs:
            path = _expand(root)
            shared = os.path.realpath(path).startswith(("/scratch/group/", "/group/"))
            add(path, "%s %sruns" % (one.name, "group " if shared and len(one.runs) > 1 else ""),
                "runs", "Other tools")
    user = _user()
    try:
        names = [grp.getgrgid(g).gr_name for g in os.getgroups()]
    except (OSError, KeyError):
        names = []
    for name in names:
        # A person's own primary group is not a shared folder: on Grace it is their NetID.
        if name == user:
            continue
        for base in ("/scratch/group", "/group", "/work/group"):
            add(os.path.join(base, name), "%s group" % name, "group", "Groups")
    # Two places of one name left after that say which is which by the folder they are in.
    labels = [one["label"] for one in out]
    for one in out:
        if labels.count(one["label"]) > 1:
            one["label"] = "%s (%s)" % (one["label"],
                                        os.path.basename(os.path.dirname(one["path"])))
    return {"places": out}


def _label_of(folder, marker):
    if marker == "spec.json":
        return "HOPE-MD"
    try:
        with open(os.path.join(folder, "submission.json"), encoding="utf-8") as handle:
            key = (json.load(handle) or {}).get("tool") or ""
    except (OSError, ValueError):
        key = ""
    for one in _tools():
        if one.key == key:
            return one.name
    return ""


def run_structure(folder):
    """{"name", "tool", "structure"} for a run folder of ours holding a structure, else None.

    A folder counts as a run when it carries the file a run of ours is marked by. Inside one, the
    places each kind of run keeps its structure are tried in order, and then any structure file
    at all within three levels, so a run laid out some other way still offers one.
    """
    folder = os.path.realpath(os.path.expanduser(str(folder or "")))
    if not os.path.isdir(folder):
        return None
    for marker, wanted in LAYOUTS:
        if not os.path.isfile(os.path.join(folder, marker)):
            continue
        found = ""
        for rel in wanted:
            hits = sorted(path for path in glob.glob(os.path.join(folder, rel))
                          if path.lower().endswith(STRUCTURES) and os.path.isfile(path))
            if hits:
                found = hits[0]
                break
        if not found:
            for depth in ("*", "*/*", "*/*/*"):
                hits = sorted(path for path in glob.glob(os.path.join(folder, depth))
                              if path.lower().endswith(STRUCTURES) and os.path.isfile(path))
                if hits:
                    found = hits[0]
                    break
        if found:
            return {"name": os.path.basename(folder), "tool": _label_of(folder, marker),
                    "structure": found, "file": os.path.relpath(found, folder)}
    return None
