"""The tools: where each lives, how it is started, and how it says it is ready.

Everything that differs between the tools is in this table, so the hub itself knows nothing
about any particular one. Each is a web page served on a login node; the hub starts it over the
shared SSH connection, forwards a local port and shows it. ADCP docking, HADDOCK docking,
BindCraft2 and HOPE-MD serve a page of their own. The design and folding tools that never had one
are driven by pages in this repository, under hope_labs/pages and hope_labs/folding, which start
from the HOPE Labs checkout rather than from the install.

Every tool says it is ready in one line carrying the port it took and the token that gates it.
None of them takes a token on the command line, and that is deliberate: a login node is shared
and `ps` shows every argument to everybody on it.
"""
from __future__ import annotations

import re
import shlex

# HOPE-MD, BindCraft2, the docking console and the two design pages:
# "open  http://127.0.0.1:<port>/?t=<token>"
URL_LINE = re.compile(r"http://127\.0\.0\.1:(\d+)/\?t=([A-Za-z0-9_\-]+)")


#: The HOPE Labs folder before a cluster has one. Every install below sits under it, so the
#: folder HOPE Labs is set up in moves them all (rebase).
LAB_ROOT = "/path/to/hope-labs"

#: Where HOPE Labs itself is installed on the cluster. Two of the tools below have no page of
#: their own and never had one, so theirs is part of this repository (hope_labs/pages) rather
#: than of the tool it drives: their start lines run from here and are told where the tool is.
#: hub.FLOW_INSTALL is the same path for the same reason, the flow runner being ours too.
LABS_INSTALL = LAB_ROOT + "/HOPE-labs"

#: Pythons to try, in order, for the two pieces of this repository that run on the cluster: the
#: flow runner and the design pages. A login node's own python3 is 3.6 on this cluster, which
#: cannot parse either of them, so one is looked for rather than named outright. First is the
#: one Set up makes beside the checkout (hope_labs/setup/bootstrap.sh), named from the checkout,
#: which is where every line using this has already gone.
PYTHONS = ("../envs/hopelabs/bin/python3",
           "python3.12", "python3.11", "python3.10", "python3.9", "python3")


def rebase(install, root):
    """`install` as it would be under `root`, keeping the layout the lab uses.

    Only the lab root is replaced, so a path already pointing somewhere else is left alone and
    a tool named individually keeps whatever it was given.
    """
    root = (root or "").strip().rstrip("/")
    if not root or not install.startswith(LAB_ROOT + "/"):
        return install
    return root + install[len(LAB_ROOT):]


def installs_for(root="", overrides=None):
    """{tool key: where it is}, from one root and any tool named on its own."""
    out = {}
    for tool in TOOLS:
        where = rebase(tool.install, root)
        if where != tool.install:
            out[tool.key] = where
    for key, where in (overrides or {}).items():
        if key in BY_KEY and str(where or "").strip():
            out[key] = str(where).strip()
    return out


def remote_path(path):
    """A path for the far end's shell, with ~ still able to expand."""
    path = (path or "").strip()
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        return '"$HOME"/' + shlex.quote(path[2:])
    return shlex.quote(path)


#: Markers a rendered start line carries in place of the three things the launcher fills in.
#: Replaced by hand rather than by a format string: a start line is shell, and shell is full of
#: characters a format string would try to read.
WHERE = "__HOPELABS_INSTALL__"
RUNSMARK = "__HOPELABS_RUNS__"
PORT = 999999                        # a number, because the lines format the port as one
PORTMARK = "__HOPELABS_PORT__"

#: What the shape of a tools catalogue means. Raised when a tool gains something a launcher has
#: to understand to start it, not when a tool is added or its words change. A launcher reading a
#: catalogue newer than it understands keeps its own.
RULES = 1


def _url_ready(text, asked_port):
    """(port, token) once a tool that prints its URL line has printed it."""
    found = URL_LINE.search(text)
    return (int(found.group(1)), found.group(2)) if found else None


def _register(name, fn):
    fn._reader_name = name
    READERS[name] = fn
    return fn


class Tool:
    """One tool: what it is, where it is, and the line that starts it."""

    def __init__(self, key, name, tagline, category, install, start, ready, blurb="",
                 needs_port=False, runs=(), next_steps=(), takes="", gives="", page="/",
                 flow_only=False):
        self.key = key
        self.name = name
        self.tagline = tagline
        self.blurb = blurb
        self.category = category
        self.install = install
        self._start = start
        self.ready = ready
        self.needs_port = needs_port      # True when the tool cannot pick a free port itself
        self.runs = tuple(runs)           # where its run folders live, $USER left to the shell
        self.next_steps = tuple(next_steps)
        self.takes = takes                # what another tool can hand it
        self.gives = gives                # what it leaves for the next tool
        self.page = page                  # the page its tab opens on, as its own launcher opens it
        #: A tool with no page of its own: its tile offers the canvas rather than a Launch that
        #: could only fail, and the hub refuses to start it. Nothing sets it now that the two
        #: design tools have pages, and it is kept because the next tool the lab installs will
        #: be listed here before anything can start it, as those two were.
        self.flow_only = flow_only

    def command(self, install="", runs="", port=0):
        return self._start(remote_path(install or self.install), runs, port)

    def lines(self):
        """The two start lines as text, with the port left to be filled in.

        A tool added to the lab's install has to be startable by a launcher built before it
        existed, and a launcher cannot be sent a function. So the line is rendered twice - with a
        runs folder and without - against a marker standing in for the port, and the launcher puts
        its own port in. Two renderings rather than a template with conditions in it: the
        conditions already live in the start lines, and a second language for expressing them is
        a second thing to get wrong.
        """
        if self.flow_only:
            # A tool with no page has no line to render, and asking for one raises on purpose.
            return {}

        def rendered(runs):
            line = self._start(WHERE, runs, PORT)
            if str(PORT) not in line and self.needs_port:
                raise ValueError("%s's start line does not use the port it is given" % self.key)
            return line.replace(str(PORT), PORTMARK)

        return {"plain": rendered(""), "with_runs": rendered(RUNSMARK)}

    def as_json(self):
        return {"key": self.key, "name": self.name, "tagline": self.tagline, "blurb": self.blurb,
                "category": self.category, "install": self.install,
                "next_steps": [{"to": to, "label": label} for to, label in self.next_steps],
                "takes": self.takes, "gives": self.gives, "page": self.page,
                "flow_only": self.flow_only, "needs_port": self.needs_port,
                "runs": list(self.runs), "ready": _ready_name(self.ready),
                "start": self.lines()}


#: How a tool says it is up. These are the readers a launcher has; a tool whose install names one
#: this launcher does not have is listed but not started, which is the honest half of the job.
READERS = {}


def _ready_name(fn):
    return getattr(fn, "_reader_name", "") or getattr(fn, "__name__", "").lstrip("_")


def catalogue():
    """Every tool, as data, for a launcher that takes its tools from the lab's install.

    The Set up page travels beside the tools rather than among them: it is not a tool, and a
    launcher that has never heard of it reads "tools" and nothing else, so it lists nothing new.
    """
    return {"rules": RULES, "tools": [t.as_json() for t in TOOLS], "setup": SETUP.as_json()}


def find(key):
    """A tool by its key, or the Set up page, or None."""
    if key == SETUP.key:
        return SETUP
    return BY_KEY.get(key)


def adopt(doc):
    """Replace the tools with the ones in this catalogue. Returns what was adopted, or "".

    This is what lets a tool installed on the cluster appear in a launcher built before it. What
    travels is the description and the line that starts it; how a tool says it is ready stays
    here, by name, because that is a reader rather than a string. A tool naming a reader this
    launcher does not have is listed and not started, with its tile saying so, rather than being
    hidden or started wrongly.
    """
    global TOOLS, BY_KEY, SETUP
    try:
        if not isinstance(doc, dict) or not doc.get("tools"):
            return ""
        if int(doc.get("rules", 1)) > RULES:
            return ""
        made = [_tool_from(t) for t in doc["tools"]]
        if not made:
            return ""
        setup = _tool_from(doc["setup"]) if isinstance(doc.get("setup"), dict) else SETUP
    except Exception:                                           # noqa: BLE001
        return ""
    TOOLS = tuple(made)
    BY_KEY = {t.key: t for t in TOOLS}
    SETUP = setup
    return "%d tools" % len(TOOLS)


def _tool_from(d):
    lines = d.get("start") or {}
    reader = READERS.get(d.get("ready") or "", _never)

    def start(install, runs, port):
        text = lines.get("with_runs" if runs else "plain") or lines.get("plain") or ""
        if not text:
            raise RuntimeError("%s did not say how it is started" % d.get("name", d.get("key")))
        return (text.replace(WHERE, install)
                    .replace(RUNSMARK, remote_path(runs) if runs else "")
                    .replace(PORTMARK, str(int(port))))

    return Tool(d["key"], d.get("name") or d["key"], d.get("tagline", ""),
                d.get("category", "Design"), d.get("install", ""), start, reader,
                blurb=d.get("blurb", ""), needs_port=bool(d.get("needs_port")),
                runs=d.get("runs") or (), takes=d.get("takes", ""), gives=d.get("gives", ""),
                page=d.get("page", "/"),
                flow_only=bool(d.get("flow_only")) or reader is _never,
                next_steps=[(n["to"], n["label"]) for n in d.get("next_steps") or []])


# --- the lines that start each tool ----------------------------------------
# Each sources the install's own activate.sh rather than naming a Python: that file is where the
# installed layout is recorded, so the hub cannot drift away from what the installer did.

def _activate(override):
    """Enter the install's environment through its activate.sh, from inside the install.

    A Python named in `override` stands in for the file. With neither, the tool still starts on
    whatever python3 the login shell has, as it always did, but the line says first that the file
    is missing: a tool that then fails is reported for that, not as a module it could not find.
    """
    return ('if [ -f ./activate.sh ]; then . ./activate.sh; '
            'elif [ -z "${%s:-}" ]; then echo "NO-ACTIVATE $PWD/activate.sh"; fi; ' % override)


def _hopemd(where, runs, port):
    args = "-m hopemd.server --host 127.0.0.1 --port %d" % port
    if runs:
        args += " --runs " + remote_path(runs)
    return ('cd %s || exit 1; %sPY="${HOPEMD_PYTHON:-python3}"; PYTHONPATH=%s exec "$PY" %s'
            % (where, _activate("HOPEMD_PYTHON"), where, args))


def _bindcraft(where, runs, port):
    args = "--host 127.0.0.1 --port %d" % port
    if runs:
        args += " --runs " + remote_path(runs)
    # bc2-serve names the install's own environment, so no module is loaded and no Python named.
    return 'cd %s || exit 1; exec ./sflab/bc2-serve %s' % (where, args)


def _adcp(where, runs, port):
    args = "-m adcp_dock.cli serve --host 127.0.0.1 --port %d" % port
    if runs:
        args += " --runs " + remote_path(runs)
    return ('cd %s || exit 1; %sPY="${ADCP_PYTHON:-python3}"; PYTHONPATH=%s exec "$PY" %s'
            % (where, _activate("ADCP_PYTHON"), where, args))


def _haddock(where, runs, port):
    """HADDOCK docking, through the command its install.sh wrote, bin/haddock-gui, which names
    the environment HADDOCK3 is installed in: nothing is sourced and no Python chosen here. An
    install made into another folder is found through its activate.sh instead. A checkout that
    was never installed says NO-ACTIVATE, which the launcher reads as "run its install.sh"."""
    args = "serve --host 127.0.0.1 --port %d" % port
    if runs:
        args += " --runs " + remote_path(runs)
    return ('cd %s || exit 1; if [ -x ./bin/haddock-gui ]; then exec ./bin/haddock-gui %s; fi; '
            'if [ -f ./activate.sh ]; then . ./activate.sh; '
            'command -v haddock-gui >/dev/null 2>&1 && exec haddock-gui %s; fi; '
            'echo "NO-ACTIVATE $PWD/activate.sh"; exit 3' % (where, args, args))


def python_search(override, what):
    """A shell fragment leaving a Python of 3.8 or newer in $PY, or stopping to say there is none.

    Shared by the flow runner's line and the design pages', so the two cannot drift apart: a
    cluster that keeps its Pythons somewhere unusual is told once. `what` ends the sentence a
    person reads when none is found, so the failure is about their tool rather than about an
    interpreter they have never heard of.
    """
    tries = " ".join(shlex.quote(p) for p in PYTHONS)
    return ('PY=""; for C in "${%s:-}" %s; do [ -n "$C" ] || continue; '
            'command -v "$C" >/dev/null 2>&1 || continue; '
            '"$C" -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" '
            '>/dev/null 2>&1 || continue; PY="$C"; break; done; '
            '[ -n "$PY" ] || { echo "no Python of 3.8 or newer was found for %s" >&2; exit 3; }; '
            % (override, tries, what))


def _design_page(tool):
    """The line that starts one of the two design pages.

    The page is in this repository, so the line runs from the HOPE Labs checkout and the tool's
    own install is handed to it as a setting: a copy of RFdiffusion in somebody's own scratch is
    still driven by the lab's page, and the page is still the one this launcher was released
    with. A checkout somewhere else is named with HOPELABS_INSTALL.

    NO-LABS rather than NO-INSTALL when the checkout is missing, because for these two they are
    different folders and saying the tool is not installed would send somebody to the wrong one.
    """
    def start(where, runs, port):
        args = "%s --host 127.0.0.1 --port %d --install %s" % (tool, port, where)
        if runs:
            args += " --runs " + remote_path(runs)
        return ('HL="${HOPELABS_INSTALL:-%s}"; cd "$HL" || { echo "NO-LABS $HL"; exit 3; }; %s'
                'PYTHONPATH="$HL" exec "$PY" -m hope_labs.pages.serve %s'
                % (LABS_INSTALL, python_search("HOPELABS_PYTHON", "the %s page" % tool), args))
    return start


def _folding_page(tool):
    """The line that starts one of the two folding pages.

    The same arrangement as _design_page, and for the same reason: the page is in this repository
    and the tool's own install is handed to it as a setting, so a copy of Boltz-2 in somebody's
    own scratch is still driven by the lab's page. It must run on a LOGIN node, and not only
    because the page is served there: fetching the alignments is the one part of a folding run
    that needs the network, and a compute node on this cluster has none.
    """
    def start(where, runs, port):
        args = "%s --host 127.0.0.1 --port %d --install %s" % (tool, port, where)
        if runs:
            args += " --runs " + remote_path(runs)
        return ('HL="${HOPELABS_INSTALL:-%s}"; cd "$HL" || { echo "NO-LABS $HL"; exit 3; }; %s'
                'PYTHONPATH="$HL" exec "$PY" -m hope_labs.folding.serve %s'
                % (LABS_INSTALL, python_search("HOPELABS_PYTHON", "the %s page" % tool), args))
    return start


def _setup_page(where, runs, port):
    """The line that starts the Set up page: from the HOPE Labs checkout, like the pages of ours,
    and on a LOGIN node, because installing is downloading and a compute node may have no
    network. It is handed nothing: the folder it installs into is the one holding the checkout.
    """
    return ('HL="${HOPELABS_INSTALL:-%s}"; cd "$HL" || { echo "NO-LABS $HL"; exit 3; }; %s'
            'PYTHONPATH="$HL" exec "$PY" -m hope_labs.setup.serve --host 127.0.0.1 --port %d'
            % (LABS_INSTALL, python_search("HOPELABS_PYTHON", "the Set up page"), port))


def _never(text, asked_port):
    """A tool that never says it is ready, because this launcher cannot read what it says.

    Every tool of the lab's has a page now, so nothing here uses this; what does is a tool
    adopted from the cluster that names a reader this launcher was built without. Such a tool is
    listed, and its tile offers the canvas rather than a Launch that could only hang.
    """
    return None


for _name, _fn in (("url", _url_ready), 
                   ("never", _never)):
    _register(_name, _fn)


TOOLS = (
    Tool("adcp", "ADCP docking", "Dock a peptide into a receptor",
         "Docking", "/path/to/hope-labs/ADCP_docking", _adcp, _url_ready,
         blurb="Prepares a receptor and a peptide, runs ADCP on the queue and ranks the poses it "
               "finds, with a viewer for each one.",
         runs=("$SCRATCH/adcp_runs", "$HOME/adcp_runs"),
         gives="docked poses",
         next_steps=(("hopemd", "Simulate these poses in HOPE-MD"),)),

    Tool("haddock", "HADDOCK docking", "Dock a protein, DNA or RNA by its interface",
         "Docking", "/path/to/hope-labs/HADDOCK_GUI", _haddock, _url_ready,
         blurb="Docks proteins, peptides, DNA or RNA onto a receptor with HADDOCK3, driven by "
               "the residues known to be at the interface, one partner per job, and shows every "
               "cluster and model with the partners compared.",
         runs=("$SCRATCH/haddock_runs",),
         takes="a receptor and its partners",
         gives="docked complexes",
         next_steps=(("hopemd", "Simulate a complex in HOPE-MD"),)),

    Tool("bindcraft", "BindCraft2", "Design a protein binder",
         "Design", "/path/to/hope-labs/BindCraft2", _bindcraft, _url_ready,
         blurb="Designs miniprotein binders against a target with AlphaFold 2 and ProteinMPNN, "
               "from a PDB id, a structure or a sequence.",
         runs=("/path/to/hope-labs/bindcraft_runs/$USER",),
         gives="designed binders",
         next_steps=(("hopemd", "Simulate a design in HOPE-MD"),)),

    # The two machine-learning design tools. Neither install has a page inside it, so each is
    # driven by one of ours (hope_labs/pages), which queues the run through the same code a flow
    # queues it through. Their runs folders are the flow's own, so a run started either way is
    # found either way.
    Tool("rfdiffusion", "RFdiffusion", "Design a binder backbone, then its sequence",
         "Design", "/path/to/hope-labs/ML_programs/RFdiffusion",
         _design_page("rfdiffusion"), _url_ready,
         blurb="Diffuses a binder backbone onto the target with RFdiffusion, then gives it a "
               "sequence with ProteinMPNN. The design arrives already placed on the target.",
         runs=("$SCRATCH/rfdiffusion_runs",),
         gives="designed binders",
         next_steps=(("hopemd", "Simulate a design in HOPE-MD"),)),

    # RFdiffusion3 stands beside RFdiffusion rather than replacing it. The two cannot share an
    # environment - RFdiffusion runs on Python 3.9 in SE3nv and this needs 3.12 - and the older
    # one is what two of the flow's adapters have always read, so retiring it is a separate
    # decision from installing this.
    Tool("rfd3", "RFdiffusion3", "Design a binder, every atom at once",
         "Design", "/path/to/hope-labs/envs/rfd3",
         _design_page("rfd3"), _url_ready,
         blurb="Diffuses every atom at once rather than a frame per residue, so a design can be "
               "asked for by the atoms that have to be in the right place. Arrives already "
               "placed on the target.",
         runs=("$SCRATCH/rfd3_runs",),
         gives="designed binders",
         next_steps=(("hopemd", "Simulate a design in HOPE-MD"),)),

    Tool("boltzgen", "BoltzGen", "Generate a binder against the target",
         "Design", "/path/to/hope-labs/envs/boltzgen",
         _design_page("boltzgen"), _url_ready,
         blurb="Generates binders against a target of any kind, protein, peptide, nucleic acid "
               "or small molecule, then folds and ranks them with its own pipeline.",
         runs=("$SCRATCH/boltzgen_runs",),
         gives="designed binders",
         next_steps=(("hopemd", "Simulate a design in HOPE-MD"),)),

    # The two folding tools. Neither install has a page inside it either, so each is driven by
    # one of ours (hope_labs/folding). They are a category of their own rather than more Design:
    # nothing is designed here, a sequence goes in and a structure comes out, and that is what
    # somebody is looking for when they open the launcher with a sequence in hand.
    Tool("boltz2", "Boltz-2", "Fold a complex of chains and ligands",
         "Folding", "/path/to/hope-labs/envs/boltz",
         _folding_page("boltz2"), _url_ready,
         blurb="Folds protein, DNA, RNA and small molecules together, from a list of sequences. "
               "The alignments are fetched on the login node and the structure is predicted in "
               "a GPU job that needs no network.",
         runs=("$SCRATCH/boltz2_runs",),
         takes="sequences",
         gives="folded complexes",
         next_steps=(("hopemd", "Simulate a complex in HOPE-MD"),)),

    Tool("colabfold", "ColabFold", "Fold protein chains and complexes",
         "Folding", "/path/to/hope-labs/envs/colabfold",
         _folding_page("colabfold"), _url_ready,
         blurb="Folds one protein chain, or a complex of several, with ColabFold and "
               "AlphaFold 2. The alignment is fetched on the login node and the prediction runs "
               "offline, with the weights already in the install.",
         runs=("$SCRATCH/colabfold_runs",),
         takes="protein sequences",
         gives="folded structures",
         next_steps=(("hopemd", "Simulate a structure in HOPE-MD"),)),

    Tool("hopemd", "HOPE-MD", "Simulate a complex",
         "Simulation", "/path/to/hope-labs/HOPE-MD/MD", _hopemd, _url_ready,
         blurb="Molecular dynamics of a docked complex on five engines, from import to analysed "
               "trajectory: plots, MM-GBSA binding energies, a report and videos.",
         runs=("$SCRATCH/hopemd_runs",),
         takes="poses, designs and complexes",
         gives="trajectories and binding energies",
         next_steps=()),
)

BY_KEY = {tool.key: tool for tool in TOOLS}

#: The page that installs the tools on a cluster and says what the cluster has. Started like a
#: tool and shown in a tab like one, and kept out of TOOLS because it is not one: it has no tile,
#: no card on the canvas, no runs, and nothing hands it anything.
SETUP = Tool("setup", "Set up", "Install tools on this cluster", "Setup", LABS_INSTALL,
             _setup_page, _url_ready,
             blurb="What this cluster has, as Slurm describes it, and the tools installed here: "
                   "tick the ones wanted and they are installed and checked with a real job.")


def categories():
    """The categories, in the order the tools are listed."""
    seen = []
    for tool in TOOLS:
        if tool.category not in seen:
            seen.append(tool.category)
    return seen
