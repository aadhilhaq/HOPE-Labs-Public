"""The settings file on this computer: the sign-in boxes, the installs, the saved flows, and
where the flows that have been queued landed.

One module knows where the file is and how it is written, because three parts of the launcher
read it: the window saves the sign-in boxes into it, the hub reads the installs out of it, and
the canvas keeps its flows there. It is deliberately free of tkinter, so the hub can use it on a
machine with no display.

The file is in the person's own home folder on their own computer. Nothing here is ever written
on the cluster: a flow is a few hundred bytes of what was drawn, and it belongs beside the
settings it was drawn with rather than in a quota on Grace.
"""
from __future__ import annotations

import json
import os
import time

#: Where this launcher remembers its cluster, its install and its tools. The name carries the
#: edition, because the two editions install to different places and one file between them is one
#: file too few: a launcher set up in somebody's own scratch wrote that path here, and the other
#: edition's launcher, reading the same file, then looked for its own tools in a checkout that
#: does not hold them.
CONFIG_NAME = ".hope_labs_public.json"
CONFIG = os.path.join(os.path.expanduser("~"), CONFIG_NAME)
#: 2: the editions were given a settings file each. A file written before that was shared
#: between them, so the folder it remembers for a cluster may be the other edition's; it is
#: dropped once, on the first read, and the cluster is looked for in this edition's own place
#: again. Nothing else in the file is touched: the sign-in boxes, the flows and the runs are the
#: same in both.
SCHEMA = 2


#: The file both editions shared before each had its own, beside this one. An edition whose own
#: file is not there yet takes the sign-in boxes from it, so nobody types their cluster and their
#: NetID in again, and only those: the folders in it may be the other edition's, and so may the
#: flows and the runs, drawn with cards this edition may not have.
SHARED_NAME = ".hope_labs.json"
SIGN_IN = ("site", "host", "user", "jump", "key")


def _read(path):
    """The settings in `path`, or None when there is no file there to read."""
    try:
        with open(path) as fh:
            kept = json.load(fh)
    except OSError:
        return None
    except ValueError:
        return {}
    return kept if isinstance(kept, dict) else {}


def load():
    kept = _read(CONFIG)
    if kept is None:
        shared = os.path.join(os.path.dirname(CONFIG), SHARED_NAME)
        boxes = _read(shared) if shared != CONFIG else None
        return {k: boxes[k] for k in SIGN_IN if k in boxes} if boxes else {}
    try:
        current = int(kept.get("schema") or 1) >= SCHEMA
    except (TypeError, ValueError):
        current = False
    return kept if current else upgraded(kept)


def upgraded(kept):
    """This file as this version reads it. Pure: the next save writes the result down."""
    kept = dict(kept, schema=SCHEMA)
    kept.pop("labs", None)
    installs = kept.get("installs")
    if isinstance(installs, dict) and "flow" in installs:
        installs = dict(installs)
        installs.pop("flow")
        kept["installs"] = installs
    return kept


def save(d):
    """Keep these settings, and whatever else is already in the file with them.

    The sign-in boxes are not the only thing kept here: "root" and "installs" are written by
    hand (docs/install-on-grace.md) and "flows" and "runs" by the canvas, and a save that wrote
    only its own keys would delete them the moment Sign in was pressed, which is to say before
    they were ever read.
    """
    kept = load()                      # before the open below truncates it
    kept.update(dict(d, schema=SCHEMA))
    try:
        with open(CONFIG, "w") as fh:
            json.dump(kept, fh, indent=1)
    except OSError:
        pass


# ---- where HOPE Labs is, cluster by cluster -------------------------------
# Kept under one key as {the address signed in to: the HOPE Labs checkout there}. Written by the
# launcher when HOPE Labs is set up on a cluster or found somewhere it did not look, so a person
# is asked once per cluster and never edits this file for it.

def labs_for(address):
    """Where HOPE Labs is on the cluster reached at `address`, or "" when this computer has not
    been told."""
    kept = load().get("labs")
    if not isinstance(kept, dict):
        return ""
    return str(kept.get((address or "").strip().lower()) or "")


def labs_save(address, path):
    address = (address or "").strip().lower()
    if not address:
        raise ValueError("no cluster to remember it for")
    kept = load().get("labs")
    kept = dict(kept) if isinstance(kept, dict) else {}
    kept[address] = path
    save({"labs": kept})


# ---- the flows a person saved ---------------------------------------------
# Kept under one key as {name: the flow as drawn}, which is the shape flow.Flow reads and writes.
# A flow carries its own name as well, so one taken out of the file is a whole flow document and
# can be handed to the runner as it stands.

def flows():
    """Every saved flow, newest name last, as a list of flow documents."""
    kept = load().get("flows")
    if not isinstance(kept, dict):
        return []
    out = []
    for name in sorted(kept, key=lambda n: n.lower()):
        one = kept[name]
        if isinstance(one, dict):
            out.append(dict(one, name=one.get("name") or name))
    return out


def flow_named(name):
    return next((f for f in flows() if f.get("name") == name), None)


def flow_save(doc):
    """Keep this flow under its own name, replacing one saved under that name before.

    The name is the flow's, not a separate label: a flow is reopened, edited and saved again, and
    two names for one thing would be two things the moment one of them was changed.
    """
    name = str((doc or {}).get("name") or "").strip()
    if not name:
        raise ValueError("the flow needs a name before it can be kept")
    kept = load().get("flows")
    kept = dict(kept) if isinstance(kept, dict) else {}
    kept[name] = doc
    save({"flows": kept})
    return flows()


def flow_delete(name):
    kept = load().get("flows")
    kept = dict(kept) if isinstance(kept, dict) else {}
    kept.pop(str(name or ""), None)
    save({"flows": kept})
    return flows()


# ---- the flows that have been queued --------------------------------------
# A queued flow runs on the cluster whether the launcher is open or not, and the folder the runner
# put it in is the only way back to it. So it is written down here, beside the flows a person
# saved, together with the drawing it was queued from: the run view stands the cards where they
# were drawn and colors them from the record on the cluster, and somebody who signs in tomorrow
# finds the flow they started today rather than a folder they have to remember.
#
# Kept under the folder rather than under the flow's name, because a flow is launched more than
# once. The same name queued twice is two runs in two folders, and keying by name would lose the
# first of them the moment the second started.

#: How many runs to keep. Past this the file is growing for nobody: a flow older than the fortieth
#: is read out of Results or off the cluster, not watched.
KEEP_RUNS = 40


def queued():
    """Every flow queued from this computer, newest first, each with the drawing it came from."""
    kept = load().get("runs")
    if not isinstance(kept, dict):
        return []
    out = [dict(one, folder=folder) for folder, one in kept.items() if isinstance(one, dict)]
    # The folder settles a tie, because two flows queued in the same second otherwise come out in
    # whatever order the file was written in. A flow queued twice under one name has the time of
    # day appended to the second folder, so the folder puts them the right way round as well.
    out.sort(key=lambda r: (r.get("started") or 0, r.get("folder") or ""), reverse=True)
    return out


def queued_save(doc, folder):
    """Remember that this drawing was queued, and where the runner put it."""
    folder = str(folder or "").strip()
    if not folder:
        raise ValueError("a queued flow is remembered by its folder, and none was given")
    kept = load().get("runs")
    kept = dict(kept) if isinstance(kept, dict) else {}
    kept[folder] = {"name": str((doc or {}).get("name") or ""), "flow": doc,
                    "started": int(time.time())}
    # Newest kept, oldest dropped: a person watches the flow they have just started, not the one
    # before the fortieth.
    newest = sorted(kept.items(), key=lambda kv: ((kv[1] or {}).get("started") or 0, kv[0]),
                    reverse=True)[:KEEP_RUNS]
    save({"runs": dict(newest)})
    return queued()
