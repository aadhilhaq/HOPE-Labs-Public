"""Setting HOPE Labs up on a cluster: what the cluster has, and the tools installed into one folder.

By Aadhil Haq

Everything the tools need on a cluster lives in one folder, the HOPE Labs folder, which is a
folder somebody chose, and this package fills it:

    <folder>/HOPE-labs        this repository: the pages, the flows, and this installer
    <folder>/cluster.json     what the cluster has, read off it once (profile.py)
    <folder>/tools/           the package manager, fetched once, and its cache
    <folder>/envs/<name>      one environment per tool, as on Grace (envs/boltz, ...)
    <folder>/setup/           what each install did, its log, and the checks that proved it

tools.py describes every install as a path under that
folder and already knows how to move them all under another one (tools.rebase), so a tool
installed here is found by the same code that finds the lab's own copy, with nothing added.

Three things a person does, and nothing else:

    1. sign in from the launcher, and name a folder           (the launcher runs bootstrap.sh)
    2. look over what the cluster was found to have           (profile.py, on the Set up page)
    3. tick the tools to install, and press Install           (engine.py and recipes.py)

Every install ends with a check: a real job, on the cluster's own queue, that does what the
tool's page will do. A tool is called installed when that job has finished and left what it
should, not when the downloads stopped.
"""
from __future__ import annotations

import os

#: The file a cluster's profile is kept in, in the HOPE Labs folder.
PROFILE = "cluster.json"

#: The folder each install's state, log and checks are kept in, in the HOPE Labs folder.
SETUP = "setup"


def labs_root():
    """The HOPE Labs folder: the one holding this checkout, or HOPELABS_ROOT when it is named.

    Worked out from where this file is rather than asked for, because every process that needs
    it was started from the checkout: the pages, the flow runner, the installer, and the jobs
    they write. A checkout at <folder>/HOPE-labs gives <folder>.
    """
    named = (os.environ.get("HOPELABS_ROOT") or "").strip()
    if named:
        return os.path.abspath(os.path.expanduser(named))
    here = os.path.abspath(__file__)
    for _ in range(4):                    # setup/__init__.py -> setup -> hope_labs -> checkout
        here = os.path.dirname(here)
    return here


def setup_dir(root=None):
    return os.path.join(root or labs_root(), SETUP)


def profile_path(root=None):
    return os.path.join(root or labs_root(), PROFILE)
