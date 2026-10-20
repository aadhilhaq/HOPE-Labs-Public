"""Walking the cluster's own filesystem, for the dialog that picks a FASTA or a run folder.

By Aadhil Haq

The page is reached over a forwarded port, so the browser is on somebody's laptop and the cluster
cannot see its disk. That cuts both ways: a file input offers the laptop's files and nothing else,
and a sequence file that is already on scratch - written by a previous run, or by a colleague -
could until now only be typed in as a path. Which meant typing a path from memory, and getting it
wrong, with nothing to show what was actually there.

So the page gets both: a file from the laptop arrives as its contents, and a file on the cluster
is chosen by browsing, here, where the files are. This is the same mechanism as the docking
console's (ADCP_docking/adcp_dock), routes and dialog alike, because somebody who has used that
console has already learned this dialog and should not have to learn a second one.

Why this is not confined to the run folders
-------------------------------------------
Everything else the page serves out of a path is confined to the folders it was started with, by
serve.inside_roots, because those routes read and write runs. This one is not, and deliberately:
a FASTA worth folding is as likely to be in the group allocation as in a run folder, and a run
folder somebody wants to write into does not exist yet. What makes that safe is that the page is
one person's own process, under their own account: it lists exactly what they would see from a
shell on the same login node, and the token in the URL is what keeps anybody else's shell out.
"""
from __future__ import annotations

import grp
import os
import pwd

#: What a FASTA may be called, which is what the uploader already accepts and therefore what
#: submit.read_upload will take. Listing a file this page would then refuse is worse than not
#: listing it: it reads as a file that cannot be folded rather than a file in the wrong format.
SEQUENCE_FILES = (".fasta", ".fa", ".faa", ".txt", ".seq")

#: The most of a folder to list at once. A scratch directory of ten thousand entries is a dialog
#: that takes a second to draw and cannot be read anyway; the filter box is what finds a name in
#: a folder that large, and it filters what was sent. Said in the answer so the page can say so.
MOST = 2000


def roots(runs=()):
    """Where the dialog offers to start, in the order it offers them.

    The group allocation first, then the person's own scratch, then home, then the folders this
    page reads runs from. The group allocation first because this is a shared installation: the
    sequences and the runs people care about are usually in it, and a dialog that opened in a
    home directory would start somewhere nothing is kept.
    """
    out, seen = [], set()

    def add(path, label):
        if not path:
            return
        real = os.path.realpath(os.path.expanduser(path))
        if real in seen or not os.path.isdir(real):
            return
        # Offered only if it can actually be opened. A place in the list that answers "no
        # permission" when it is clicked is a place that should not have been in the list.
        if not os.access(real, os.R_OK | os.X_OK):
            return
        seen.add(real)
        out.append({"path": real, "label": label})

    try:
        user = pwd.getpwuid(os.getuid()).pw_name
    except (OSError, KeyError):
        user = os.environ.get("USER") or ""
    try:
        groups = [grp.getgrgid(g).gr_name for g in os.getgroups()]
    except (OSError, KeyError):
        groups = []
    for name in groups:
        # A person's own primary group is not a shared allocation, and on Grace it is their
        # NetID, so it would be listed as one and lead nowhere.
        if name == user:
            continue
        for base in ("/scratch/group", "/group", "/work/group"):
            add(os.path.join(base, name), "%s (group)" % name)
    add(os.environ.get("SCRATCH"), "your scratch")
    add(os.path.join("/scratch/user", user) if user else "", "your scratch")
    add(os.path.expanduser("~"), "home")
    for root in runs or ():
        add(root, "this page's runs")
    return {"roots": out}


def listing(path, dirs_only=False, kinds=SEQUENCE_FILES):
    """One folder, as the dialog draws it: the files worth picking first, then the folders.

    Sequence files before folders, because somebody in this dialog is looking for a FASTA rather
    than for a filesystem, and a scratch folder holding two hundred run folders would otherwise
    push the one file they came for off the bottom of the list.

    A folder that cannot be listed comes back as a sentence in the answer rather than as a
    refusal, so the dialog can say it where the listing would have been and stay open on the
    crumbs that got there. Closing the dialog on a folder somebody is not allowed to read would
    throw away the navigation that is the whole point of it.
    """
    path = os.path.abspath(os.path.expanduser(str(path or "")))
    if os.path.isfile(path):
        path = os.path.dirname(path)
    if not os.path.isdir(path):
        return {"error": "not a folder: %s" % path}
    dirs, files = [], []
    try:
        names = sorted(os.listdir(path))
    except (PermissionError, OSError) as why:
        return {"error": "cannot read %s: %s" % (path, getattr(why, "strerror", why))}
    for name in names[:MOST]:
        if name.startswith("."):
            continue
        full = os.path.join(path, name)
        if os.path.isdir(full):
            dirs.append({"name": name, "path": full})
        elif not dirs_only and name.lower().endswith(tuple(kinds)):
            # A broken symbolic link must not empty the listing: getsize raises for a link with
            # nothing on the end of it, so the size is taken per entry and a failure drops that
            # one entry rather than the folder.
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            files.append({"name": name, "path": full, "size": size})
    return {"path": path, "parent": os.path.dirname(path), "dirs": dirs, "files": files,
            "writable": os.access(path, os.W_OK | os.X_OK),
            "truncated": len(names) > MOST, "most": MOST}


def make_dir(parent, name):
    """Create one folder inside `parent`, so a run can be given a folder that is not there yet.

    One level, and the name is taken apart rather than trusted: "a/b" typed into a New folder box
    means the first of those and would otherwise quietly produce the second, and "../elsewhere"
    means a folder nowhere near the one on the screen.
    """
    parent = os.path.abspath(os.path.expanduser(str(parent or "").strip()))
    name = str(name or "").strip()
    if not os.path.isdir(parent):
        return {"error": "no such folder: %s" % parent}
    if not name:
        return {"error": "give the folder a name"}
    if name in (".", "..") or "/" in name or "\\" in name:
        return {"error": "a name, not a path: no slashes, and not .. - browse to where you want "
                         "it first"}
    if name.startswith("."):
        return {"error": "a name starting with a dot is hidden from this dialog, so you would "
                         "not see the folder again"}
    full = os.path.join(parent, name)
    if os.path.isdir(full):
        return {"path": full, "existed": True}
    if os.path.exists(full):
        return {"error": "%s is already a file here" % name}
    if not os.access(parent, os.W_OK | os.X_OK):
        return {"error": "%s is not writable by you" % parent}
    try:
        os.mkdir(full)
    except OSError as why:
        return {"error": "could not create it: %s" % why}
    return {"path": full, "existed": False}
