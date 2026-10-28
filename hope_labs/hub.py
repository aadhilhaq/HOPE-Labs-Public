"""The hub: a small web server on this computer, serving the page that fronts every tool.

It runs on the laptop, not on the cluster. It holds the one SSH connection, starts a tool on the
login node when the page asks, forwards a local port to it and tells the page the URL. The tools
themselves are untouched: each is the same page it always was, shown inside the hub's shell.

Nothing here is reachable from outside this computer: the server binds the loopback address and
every request carries a token the page was opened with.
"""
from __future__ import annotations

import json
import mimetypes
import os
import posixpath
import re
import secrets
import shlex
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import EDITION, PAGE_API, PAGE_NEEDS, __version__
from . import canvas
from . import files as filesmod
from . import settings
from . import tools as catalog
from . import tunnel

def _web():
    """Where the page's files are, whether this is a checkout or a bundled executable.

    PyInstaller puts added data under its own unpacked folder, so the path beside this module is
    tried first and the bundle's root second; a build that forgot to carry the page is caught by
    --selftest rather than by a blank window.
    """
    beside = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
    if os.path.isdir(beside):
        return beside
    root = getattr(sys, "_MEIPASS", "")
    return os.path.join(root, "hope_labs", "web") if root else beside


WEB = _web()
START_TIMEOUT = 120          # a cold login node takes a while to import a tool's environment

#: Whether a tool or a card that the install on the cluster offers is taken when this launcher was
#: built without it. On, so a tool installed on the cluster reaches a launcher built months before
#: it. Off, the launcher stays the set it was built as: what it does not know is left out, and so
#: are the page and the bar of an install that offers it, since they were written for that set.
ADOPT_NEW = False

#: The tools and cards this launcher was built with, before anything is taken from a cluster.
BUILT_TOOLS = frozenset(catalog.BY_KEY)
BUILT_CARDS = frozenset(c.key for c in canvas.cards.CARDS)


def known_only(doc, field, built):
    """(`doc` with the entries of its `field` that are not in `built` taken out, their keys).

    Everything is kept while ADOPT_NEW is on, and anything that is not a catalog is handed back
    as it came, for adopt() to refuse in its own way.
    """
    if ADOPT_NEW or not isinstance(doc, dict) or not isinstance(doc.get(field), list):
        return doc, []
    kept = [e for e in doc[field] if isinstance(e, dict) and e.get("key") in built]
    left = sorted(str(e.get("key")) for e in doc[field]
                  if not (isinstance(e, dict) and e.get("key") in built))
    doc = dict(doc)
    doc[field] = kept
    if isinstance(doc.get("linker_families"), dict):
        # a card's choices travel beside the cards, and only the ones this launcher has are kept
        doc["linker_families"] = {k: v for k, v in doc["linker_families"].items()
                                  if k in canvas.cards.LINKER_FAMILIES}
    return doc, left


class Running:
    """A tool that is up: its channel on the shared connection and the local port reaching it."""

    def __init__(self, tool, channel, forwarder, local_port, remote_port, token):
        self.tool = tool
        self.channel = channel
        self.forwarder = forwarder
        self.local_port = local_port
        self.remote_port = remote_port
        self.token = token
        self.started = time.time()
        self.log = ""

    @property
    def url(self):
        return "http://127.0.0.1:%d%s%s" % (self.local_port, self.tool.page,
                                            ("?t=" + self.token) if self.token else "")

    def alive(self):
        return not (self.channel.exit_status_ready() and not self.channel.recv_ready())

    def stop(self):
        for close in (getattr(self.channel, "close", None),
                      getattr(self.forwarder, "shutdown", None),
                      getattr(self.forwarder, "server_close", None)):
            try:
                if close:
                    close()
            except Exception:                                   # noqa: BLE001
                pass


class Hub:
    """Everything the page talks to: the connection, the tools that are up, the runs."""

    def __init__(self, transport, user="", host="", say=None, installs=None, labs="",
                 address=""):
        self.t = transport
        self.user = user
        self.host = host
        #: What the person typed to reach the cluster, which is what this computer remembers the
        #: cluster by: `host` is the login node the front door happened to give us.
        self.address = address or host
        self.say = say or (lambda *a: None)
        #: {tool key: where it is}, for a tool named on its own in the settings; it wins over
        #: wherever the HOPE Labs folder would put that tool.
        self._named = {k: v for k, v in dict(installs or {}).items() if k in catalog.BY_KEY}
        #: {tool key: where it is on the cluster}: every tool under the HOPE Labs folder, as
        #: tools.rebase moves them, and the checkout itself under "flow". Worked out by use_labs.
        self.installs = {}
        #: Where HOPE Labs is on this cluster: the checkout the pages, the flows and Set up run
        #: from: the one this computer was told of, or set up, for this cluster.
        self.labs = ""
        #: Whether that checkout is there, once asked (None until then), and the first-time set-up
        #: while it runs.
        self.labs_found = None
        #: Which edition that checkout is, once asked: "lab", "public", or "" for one not found.
        #: A checkout of the other edition is not this launcher's, and holds a different set of
        #: tools; use_own_edition is what does something about it.
        self.labs_edition = ""
        self.boot = {}
        self._installed = (0.0, {})
        self.use_labs(labs or dict(installs or {}).get("flow") or FLOW_INSTALL, check=False)
        self.running = {}                 # key -> Running
        self.token = secrets.token_urlsafe(12)
        self.lock = threading.Lock()
        self.starting = {}                # key -> the lock held while that tool starts
        self._scratch = ""                # the person's scratch on the cluster, asked for once
        self._spaces = None               # every space they may keep things in, asked for once
        self._listing = False             # whether that is being asked, in the background
        self.flows = Flows(self)          # queueing flows, and reading back how they are getting on
        #: Where the page being served comes from: a folder taken off the cluster, or "" for the
        #: one this launcher was built with. Set once, when the connection is made.
        self.page_dir = ""
        self.page_note = ""
        self._runs_cache = (0.0, [])
        #: What the install offered that this launcher left out, by key, when ADOPT_NEW is off.
        self.left_out = {"tools": [], "cards": []}

    def use_cluster_cards(self):
        """Take the cards from the install, so a card added there appears here.

        The cards are data; the rules about what may be linked to what stay in this launcher. So a
        new card reaches somebody who has not downloaded this in months, and a change to the rules
        themselves does not pretend to - the catalog says which rules it was written for, and one
        written for newer rules is left alone.
        """
        from hope_flow import cards
        try:
            offered, self.left_out["cards"] = known_only(self.flows.catalogue(), "cards",
                                                         BUILT_CARDS)
            got = cards.adopt(offered)
        except Exception as why:                                # noqa: BLE001
            self.say("the cards on the cluster could not be read (%s); using this launcher's own"
                     % str(why)[:160])
            return ""
        if self.left_out["cards"]:
            self.say("the cluster offers %d card(s) this launcher was not built with; they are "
                     "left out" % len(self.left_out["cards"]))
        if got:
            self.say("cards from the cluster: %s" % got)
        else:
            self.say("the cards on the cluster are newer than this launcher understands; "
                     "using its own. Download the current release for the rest.")
        return got

    def use_cluster_tools(self):
        """Take the tool list from the install, so a tool added there appears here.

        The same split as the cards: what a tool is and the line that starts it travel; how it
        says it is ready stays in this launcher, by name. A tool whose install names a reader this
        launcher does not have is listed and not started, rather than hidden or started wrongly.
        """
        try:
            offered, self.left_out["tools"] = known_only(self.flows.tools(), "tools", BUILT_TOOLS)
            got = catalog.adopt(offered)
        except Exception as why:                                # noqa: BLE001
            self.say("the tools on the cluster could not be read (%s); using this launcher's own"
                     % str(why)[:160])
            return ""
        if self.left_out["tools"]:
            self.say("the cluster offers %d tool(s) this launcher was not built with; they are "
                     "left out" % len(self.left_out["tools"]))
        if got:
            self.say("tools from the cluster: %s" % got)
        return got

    def foreign(self):
        """Whether the install offered what this launcher left out: then its page and its bar,
        written for that set of tools, are not taken either."""
        return bool(self.left_out["tools"] or self.left_out["cards"])

    def use_cluster_words(self):
        """Take the bar's copyright and credits from the install.

        The bar is written in the launcher rather than in the page, so it did not travel when the
        page did and a launcher went on stating whatever was true the day it was built. These are
        words and nothing else: the worst a bad answer can do is read oddly, so anything
        unreadable simply leaves this launcher's own in place.
        """
        from . import docs
        if self.foreign():
            return ""
        try:
            got = docs.adopt(self.flows.site())
        except Exception as why:                                # noqa: BLE001
            self.say("the bar on the cluster could not be read (%s); using this launcher's own"
                     % str(why)[:160])
            return ""
        if got:
            self.say("the bar from the cluster: %s" % got)
        return got

    def use_cluster_page(self, install=""):
        """Serve the lab's current page instead of this launcher's own, where that is possible.

        Called once, after signing in. Whatever it decides, the launcher works: the page it was
        built with is always there to fall back to.
        """
        from . import PAGE_API
        if self.foreign():
            self.page_dir, self.page_note = "", ""
            return ""
        where = install or self.installs.get("flow") or FLOW_INSTALL
        self.page_dir, self.page_note = page_from_cluster(self.t, where, PAGE_API, self.say)
        if self.page_note:
            self.say(self.page_note)
        return self.page_dir

    def web(self):
        """The folder the page is served out of."""
        return self.page_dir or WEB

    @property
    def scratch(self):
        """The person's scratch folder on the cluster.

        Asked for once and kept: it is the same for the whole session, and the clusters the lab
        uses have home quotas small enough that writing anything there is a mistake.
        """
        if not self._scratch:
            status, out, _ = tunnel.run(self.t, 'echo "${SCRATCH:-}"', timeout=30)
            got = (out or "").strip().splitlines()
            self._scratch = got[-1].strip() if status == 0 and got and got[-1].strip() else "/tmp"
        return self._scratch

    # ---- starting and stopping ------------------------------------------
    def free_remote_port(self):
        """A port nothing is using on the login node, for the two tools that cannot choose one.

        Asked for over the connection we already hold. There is a moment between asking and
        binding in which somebody else could take it; the tools that pick their own port avoid
        that, which is why only two go through here.
        """
        status, out, _ = tunnel.run(self.t, 'python3 -c "import socket; s=socket.socket(); '
                                            "s.bind(('127.0.0.1', 0)); print(s.getsockname()[1]); s.close()\"",
                                    timeout=30)
        got = (out or "").strip().splitlines()
        if status == 0 and got and got[-1].isdigit():
            return int(got[-1])
        return 8800 + secrets.randbelow(600)

    def launch(self, key, runs=""):
        """Start a tool and forward a port to it. Returns what the page needs to show it."""
        tool = catalog.find(key)
        if tool is None:
            raise ValueError("there is no tool called %r" % key)
        # One start at a time per tool: a second tab asking while the first start is still under
        # way waits for it and gets the same copy, instead of a second one on the login node.
        with self.lock:
            gate = self.starting.setdefault(key, threading.Lock())
        with gate:
            return self._launch(tool, key, runs)

    def _launch(self, tool, key, runs):
        # A tool with no page cannot be started this way, whatever asked. The page offers these
        # the canvas instead, so this answers a stale tab or a request made by hand.
        if getattr(tool, "flow_only", False):
            raise ValueError("%s has no page of its own: it is run from a flow, on the Flows "
                             "screen" % tool.name)
        with self.lock:
            up = self.running.get(key)
            if up is not None and up.alive():
                return self.state_of(key)
            if up is not None:
                up.stop()
                self.running.pop(key, None)
        # With no HOPE Labs on this cluster there is nothing to start from: every tool's folder
        # is under the HOPE Labs folder, and every page of ours runs from its checkout. The one
        # exception is a tool named on its own in the settings that serves its own page.
        if self.labs_found is False and (tool.key not in self._named
                                         or "HOPELABS_INSTALL" in tool.command(port=0)):
            self.needs_labs("%s cannot be started" % tool.name)

        asked = self.free_remote_port() if tool.needs_port else 0
        line = tool.command(install=self.install_of(tool), runs=runs, port=asked)
        if self.labs != FLOW_INSTALL:
            # The pages of ours start from the HOPE Labs checkout, and their lines name the lab's
            # own unless told otherwise; this tells them. Harmless to the tools that ignore it.
            line = "export HOPELABS_INSTALL=%s; %s" % (shlex.quote(self.labs), line)
        self.say("%s: starting on the login node" % tool.name)
        channel = self.t.open_session()
        channel.get_pty()                 # so the tool dies with this connection
        channel.exec_command(line)
        channel.settimeout(1.0)
        buf, deadline = "", time.time() + START_TIMEOUT
        while time.time() < deadline:
            if channel.exit_status_ready() and not channel.recv_ready():
                raise RuntimeError(self.why(tool, buf))
            try:
                chunk = channel.recv(65536).decode("utf8", "replace")
            except Exception:                                   # noqa: BLE001
                chunk = ""
            if chunk:
                buf += chunk
            got = tool.ready(buf, asked)
            if got:
                remote_port, token = got
                forwarder = tunnel.forward(self.t, tunnel.free_port(), remote_port)
                local = forwarder.server_address[1]
                up = Running(tool, channel, forwarder, local, remote_port, token)
                up.log = buf
                with self.lock:
                    self.running[key] = up
                self.say("%s: ready on 127.0.0.1:%d" % (tool.name, local))
                return self.state_of(key)
            time.sleep(0.2)
        channel.close()
        raise RuntimeError(self.why(tool, buf) or
                           "%s did not report a URL within %ds. What it said:\n%s"
                           % (tool.name, START_TIMEOUT, tail(buf)))

    def install_of(self, tool):
        """Where this copy of a tool is: what the settings say, else the lab's own."""
        if tool.key == catalog.SETUP.key:
            # The Set up page is part of HOPE Labs, so its copy is the checkout in use. Read off
            # the catalog it was the folder the launcher starts from, which in the public edition
            # is a placeholder, and a start that failed said there was no Set up there.
            return self.labs
        return self.installs.get(tool.key) or tool.install

    def needs_labs(self, what):
        """Refuse, in a sentence, what cannot be done while this cluster has no HOPE Labs.

        Whatever runs from the HOPE Labs folder, or from a tool under it, can only fail there,
        and the shell's failure names the folder it went to. In the public edition, before Set up
        has made one, that folder is a placeholder no cluster has, and it reached the person as a
        folder that was missing. Nothing is refused before the folder has been looked for (None):
        that is not known yet.
        """
        if self.labs_found is False:
            raise RuntimeError("HOPE Labs is not set up on this cluster yet, so %s here. The "
                               "Tools page offers to set it up." % what)

    # ---- the HOPE Labs folder on this cluster ----------------------------
    def use_labs(self, labs, check=True):
        """Point everything at the HOPE Labs checkout `labs`: the pages, the flows, Set up, and
        every tool under the folder holding it, laid out as the lab's own folder is.

        The lab's own checkout gives the lab's own installs, exactly as before. Anywhere else, a
        tool is wherever Set up put it: under the same folder, in the same layout, which is what
        tools.rebase works out. A tool named on its own in the settings keeps its own path.
        """
        labs = (labs or FLOW_INSTALL).rstrip("/") or FLOW_INSTALL
        root = posixpath.dirname(labs)
        self.labs = labs
        self.installs = dict(catalog.installs_for(root), **self._named)
        self.installs["flow"] = labs
        self._installed = (0.0, {})
        self.labs_edition = ""
        if check:
            self.labs_found = self.check_labs()
        return self.labs

    def check_labs(self):
        """Whether the HOPE Labs checkout is where this launcher is looking for it, and which
        edition it is, which is left in self.labs_edition."""
        where = shlex.quote(self.labs + "/hope_labs/__init__.py")
        status, out, _ = tunnel.run(
            self.t, "if test -f %s; then echo HOPE-LABS-HERE; grep -hE '^(EDITION|APP) *=' %s; "
                    "git -C %s config --get remote.origin.url; fi 2>/dev/null; true"
                    % (where, where, shlex.quote(self.labs)), timeout=30)
        here = "HOPE-LABS-HERE" in (out or "")
        self.labs_edition = edition_of(out) if here else ""
        return here

    def use_own_edition(self):
        """Fall back to a HOPE Labs of this launcher's own when the folder remembered for this
        cluster has gone, or holds the other edition, or was never there.

        One settings file used to serve both editions, and the folder in it was whichever of them
        was set up last: a public launcher set up in somebody's own scratch wrote its path there,
        and the other edition's launcher read that same path and went looking for its own
        tools in a checkout that does not hold them, finding none of them installed. The editions keep a
        file each now, so this is for the computer that already has the other one's folder
        written down, for a folder that has since been moved or removed, and for a launcher whose
        new file has not yet been told about the HOPE Labs that Set up made last time.

        Returns where it moved to, or "" for leaving things as they are, which it does when
        there is nothing better to move to: a cluster that has only the other edition on it is
        still opened with that, rather than with an error about a folder the person cannot see.
        """
        if self.labs_found is None:
            self.labs_found = self.check_labs()
        if self.labs_found and self.labs_edition == EDITION:
            return ""
        was, found, edition = self.labs, self.labs_found, self.labs_edition
        for place in self.own_places():
            if place == was:
                continue
            self.use_labs(place)
            if not (self.labs_found and self.labs_edition == EDITION):
                continue
            if was == FLOW_INSTALL and not found:
                self.say("HOPE Labs found in %s" % self.labs)
            else:
                self.say("%s was remembered for this cluster, but %s; the HOPE Labs at %s is "
                         "used instead" % (was, "it holds the %s edition and this is the %s "
                         "launcher" % (edition, EDITION) if found else "it is not there",
                         self.labs))
            self.remember_labs()
            return self.labs
        self.use_labs(was, check=False)
        self.labs_found, self.labs_edition = found, edition
        return ""

    def own_places(self):
        """Where this launcher looks for a HOPE Labs of its own, in order: the folder it was built
        to use, the one Set up offers first in the person's own scratch, then any of this edition
        found in the other spaces they have - a group's, a project's.

        One at a time, so the spaces are only listed when the first two are not it: listing them
        is a walk through every one, and the first time on a cold login node that takes a while.
        """
        tried = [FLOW_INSTALL]
        yield FLOW_INSTALL
        try:
            mine = (posixpath.join(self.scratch, "hope-labs", "HOPE-labs")
                    if self.scratch != "/tmp" else "")
        except Exception:                                       # noqa: BLE001
            mine = ""
        if mine:
            tried.append(mine)
            yield mine
        self.say("Looking for HOPE Labs in your other spaces on this cluster...")
        for space in self.spaces():
            for found in space["labs"]:
                if found["edition"] == EDITION and found["path"] not in tried:
                    tried.append(found["path"])
                    yield found["path"]

    def looked_in(self):
        """Where the search for a HOPE Labs of this launcher's own looked, for saying so: the
        folder named for this cluster, if one was, then the person's spaces as they were listed.

        Never the folder the launcher starts from when nothing was named. In the lab's edition
        that is the lab's own, inside a member's spaces anyway; in the public edition it is a
        placeholder no cluster has, and the window named it as the place HOPE Labs had been
        looked for, which sent a newcomer looking for a folder that is nowhere.
        """
        named = [self.labs] if self.labs != FLOW_INSTALL else []
        return named + [space["path"] for space in self._spaces or ()]

    def spaces(self, fresh=False):
        """Every space this person may keep things in on the cluster, with the room each has
        left and any HOPE Labs already in it, asked for once a session.

        Somebody given this launcher should not need to know where their cluster keeps things:
        their scratch, each group they belong to, a project or work folder. They are found the
        way the cluster itself lists them - showquota where it has one, which gives the room as
        well - and then by the usual names: $SCRATCH, $WORK and $PROJECT, and a folder named for
        each of their groups under the places clusters keep them. Each is looked in for a HOPE
        Labs at the top or one folder down, which is where Set up puts one.
        """
        if self._spaces is not None and not fresh:
            return self._spaces
        try:
            status, out, _ = tunnel.run(self.t, SPACES, timeout=180)
        except Exception as why:                                # noqa: BLE001
            self.say("the spaces on this cluster could not be listed: %s" % str(why)[:160])
            return []
        self._spaces = read_spaces(out)
        return self._spaces

    def spaces_soon(self):
        """The spaces if they have been listed, else None, with the listing started in the
        background, so a page that asks is answered at once and asks again."""
        if self._spaces is not None:
            return self._spaces
        with self.lock:
            if not self._listing:
                self._listing = True

                def work():
                    try:
                        self.spaces()
                    finally:
                        self._listing = False

                threading.Thread(target=work, daemon=True).start()
        return None

    def labs_state(self):
        root = posixpath.dirname(self.labs)
        suggest, spaces = "", []
        if self.labs_found is False:
            # Only a cluster still to be set up needs a folder suggested and the spaces listed,
            # and each is a round trip to the cluster.
            try:
                if self.scratch != "/tmp":
                    suggest = posixpath.join(self.scratch, "hope-labs")
            except Exception:                                   # noqa: BLE001
                suggest = ""
            listed = self.spaces_soon()
            spaces = None if listed is None else [
                dict(sp, labs=[f for f in sp["labs"] if f["edition"] == EDITION])
                for sp in listed]
            if not suggest:
                suggest = next((posixpath.join(sp["path"], "hope-labs") for sp in spaces or ()
                                if sp["writable"] and not sp["home"]), "")
        return {"path": self.labs, "root": root, "found": self.labs_found,
                "lab": self.labs == FLOW_INSTALL, "suggest": suggest, "boot": dict(self.boot),
                # Which edition this launcher is, and which the checkout it is pointed at turned
                # out to be. Equal in every ordinary case; use_own_edition is why.
                "build": EDITION, "edition": self.labs_edition, "spaces": spaces}

    def labs_here(self, path):
        """Use a HOPE Labs that is already set up on this cluster, at `path` (the folder or its
        checkout), and remember it for this cluster on this computer."""
        path = (path or "").strip().rstrip("/")
        if not path.startswith("/"):
            raise ValueError("give the whole path, starting with /")
        candidates = [path] if path.endswith("/HOPE-labs") else [path + "/HOPE-labs", path]
        for labs in candidates:
            self.use_labs(labs)
            if not self.labs_found:
                continue
            if self.labs_edition != EDITION:
                # The two editions carry different sets of tools, and this launcher takes the
                # set from the checkout it is pointed at. Pointed at the other edition's, it
                # would offer that edition's tools and nothing it was built for.
                raise ValueError("%s is the %s edition of HOPE Labs and this is the %s "
                                 "launcher: the two hold different tools. Open it with the %s "
                                 "launcher, or give the path of a %s checkout."
                                 % (self.labs, self.labs_edition, EDITION, self.labs_edition,
                                    EDITION))
            self.remember_labs()
            self.reload_from_cluster()
            return self.labs_state()
        raise ValueError("there is no HOPE Labs at %s: no HOPE-labs/hope_labs in it" % path)

    def remember_labs(self):
        try:
            settings.labs_save(self.address, self.labs)
        except (OSError, ValueError) as why:
            self.say("the folder was not written down on this computer: %s" % why)

    def reload_from_cluster(self):
        """Take the cards, the tools, the page and the bar from the checkout now in use.

        The tools and cards first: what they leave out decides whether the page and the bar are
        taken at all.
        """
        self.use_cluster_cards()
        self.use_cluster_tools()
        self.use_cluster_page()
        self.use_cluster_words()

    def set_up(self, folder, source="", proxy=""):
        """Set HOPE Labs up on this cluster for the first time, in `folder`, in the background.

        The script is this launcher's own copy of hope_labs/setup/bootstrap.sh, sent down the
        connection, so a cluster that has never seen HOPE Labs needs nothing on it first. It
        fetches the code, a package manager and a Python; the tools are installed afterwards, on
        the Set up page it opens.
        """
        folder = (folder or "").strip().rstrip("/")
        if not folder.startswith("/"):
            raise ValueError("give the whole path of the folder, starting with /")
        if re.search(r"[^A-Za-z0-9._/+=@,-]", folder):
            raise ValueError("the folder may hold letters, numbers and . _ - / only: job scripts "
                             "name it, and Slurm cannot read a path that needs quoting")
        if self.boot.get("state") == "running":
            raise ValueError("HOPE Labs is already being set up in %s" % self.boot.get("folder"))
        script = bootstrap_script()
        source = source or os.environ.get("HOPELABS_SOURCE", "")
        proxy = (proxy or "").strip()
        if proxy and not re.match(r"^(https?|socks5h?)://[A-Za-z0-9._:@/%-]+$", proxy):
            raise ValueError("a proxy is written like http://proxy.example.edu:3128")
        self.boot = {"state": "running", "folder": folder, "log": [], "started": time.time()}

        def work():
            env = ("http_proxy=%s https_proxy=%s " % (shlex.quote(proxy), shlex.quote(proxy))
                   if proxy else "")
            line = "%ssh -s -- %s %s <<'HOPEBOOTEOF'\n%s\nHOPEBOOTEOF" % (
                env, shlex.quote(folder), shlex.quote(source) if source else "", script)
            try:
                status = stream(self.t, line, lambda text: self.boot["log"].append(text),
                                timeout=3600)
                said = "\n".join(self.boot["log"])
                if status != 0 or "HOPE-LABS READY" not in said:
                    why = [l for l in self.boot["log"] if l.startswith("HOPE-LABS-FAILED")]
                    self.boot.update(state="failed", problem=(why[-1][17:] if why else
                                     tail(said, 4) or "the set-up stopped without saying why"))
                    return
                self.use_labs(folder + "/HOPE-labs")
                if not self.labs_found:
                    self.boot.update(state="failed", problem="the set-up finished but there is "
                                     "no HOPE Labs in %s" % folder)
                    return
                self.remember_labs()
                self.reload_from_cluster()
                self.boot.update(state="done", finished=time.time())
                self.say("HOPE Labs set up in %s" % folder)
            except Exception as why:                            # noqa: BLE001
                self.boot.update(state="failed", problem=str(why))

        threading.Thread(target=work, daemon=True).start()
        return self.labs_state()

    def installed(self):
        """{tool key: whether its install is on the cluster}, asked in one go and kept a minute.

        So a tile can say a tool is not installed here before Launch is pressed, rather than
        after a start that could only fail.
        """
        age, known = self._installed
        if known and time.time() - age < 60:
            return known
        tools = list(catalog.TOOLS)
        # A tool Set up has a record of is installed when that record says ready: a folder
        # half filled by an install that stopped is not something Launch could open. Any other
        # tool is installed when its folder is there, as the lab's own always have been.
        setup = posixpath.join(posixpath.dirname(self.labs), "setup")
        line = "; ".join(
            "if [ -f {rec} ]; then grep -q '\"state\": \"ready\"' {rec} && echo {key}; "
            "elif [ -e {where} ]; then echo {key}; fi".format(
                rec=shlex.quote(posixpath.join(setup, t.key + ".json")),
                where=shlex.quote(self.install_of(t)), key=t.key)
            for t in tools) + "; true"
        try:
            status, out, _ = tunnel.run(self.t, line, timeout=30)
        except Exception:                                       # noqa: BLE001
            return known
        there = set((out or "").split())
        known = {t.key: t.key in there for t in tools}
        self._installed = (time.time(), known)
        return known

    def why(self, tool, buf):
        """What went wrong, in terms that can be acted on."""
        low = buf.lower()
        where = self.install_of(tool)
        # The markers come from the start lines in tools.py. A missing install is checked first:
        # with no folder there is no activate.sh or environment either, and that is not the news.
        if "no-install" in low or ("no such file or directory" in low and "cd" in low):
            return "there is no %s on the cluster: %s" % (tool.name, where)
        # The design tools are fronted by a page that belongs to HOPE Labs rather than to the
        # install, so a missing checkout is a different folder from a missing tool and sending
        # somebody to the tool's would waste their afternoon.
        if "no-labs" in low:
            return ("%s is run from a page that is part of HOPE Labs, and the HOPE Labs install "
                    "on the cluster is not where it is expected.\n%s" % (tool.name, tail(buf)))
        if "no-activate" in low:
            return ("%s is installed at %s but its activate.sh is missing, so its environment "
                    "cannot be entered. Running its install.sh again writes it."
                    % (tool.name, where))
        if "no-env" in low:
            return ("%s is installed at %s but there is no environment at envs/hope beside it. "
                    "HOPE Labs looks for the environment in the folder that holds the install, "
                    "where its install.sh puts it." % (tool.name, where))
        if "no module named" in low:
            return ("%s was not found in its install at %s.\n%s" % (tool.name, where, tail(buf)))
        if "future feature annotations" in low or "syntaxerror" in low:
            return ("the cluster ran %s with a Python too old to parse it; its environment did not "
                    "load.\n%s" % (tool.name, tail(buf)))
        if "address already in use" in low:
            return "the port %s was given is taken; try again and it will be given another" % tool.name
        if "permission denied" in low:
            return "permission denied starting %s:\n%s" % (tool.name, tail(buf))
        if buf.strip():
            return "%s stopped before it was ready:\n%s" % (tool.name, tail(buf))
        return ""

    def stop(self, key):
        with self.lock:
            up = self.running.pop(key, None)
        if up is not None:
            up.stop()
            self.say("%s: stopped" % up.tool.name)
        return True

    def stop_all(self):
        for key in list(self.running):
            self.stop(key)

    # ---- telling the author something went wrong ------------------------
    def report(self, kind, said, contact="", context=None, preview=False):
        """Gather a report on the cluster, and write it where the lab can read it.

        It runs there rather than here because everything worth putting in a report is there: the
        job's accounting, the log it left, the checkout that ran it. The launcher adds only what
        the cluster cannot know - its own version, and what the person was looking at.

        `preview` builds the text and writes nothing, which is what the page asks for first: a
        person reads their own report before it is filed, because it quotes their run.
        """
        # It is gathered by the HOPE Labs checkout on the cluster, so with none there is nothing
        # to gather it.
        self.needs_labs("a report cannot be made")
        asked = {"said": said or "", "contact": contact or "",
                 "context": dict(context or {}), "preview": bool(preview)}
        asked["context"].setdefault("launcher", "HOPE Labs %s" % __version__)
        asked["context"].setdefault("page", "api %d, needs %d" % (PAGE_API, PAGE_NEEDS))
        asked["context"].setdefault("cluster", "%s@%s" % (self.user, self.host))
        install = catalog.rebase(self.installs.get("flow") or FLOW_INSTALL,
                                   self.installs.get("_root", ""))
        line = ('cd %s || exit 1; %sPYTHONPATH=%s exec "$PY" -m hope_labs.report %s'
                % (shlex.quote(install),
                   catalog.python_search("HOPEFLOW_PYTHON", "the report"),
                   shlex.quote(install), shlex.quote(kind)))
        status, out, err = tunnel.run(
            self.t, line + " <<'HOPEREPORTEOF'\n" + json.dumps(asked) + "\nHOPEREPORTEOF",
            timeout=120)
        try:
            got = json.loads(out[out.index("{"):out.rindex("}") + 1])
        except (ValueError, IndexError):
            raise RuntimeError("the report could not be made: %s"
                               % ((err or out).strip()[:300] or "the cluster said nothing"))
        if status != 0 and not got.get("text"):
            raise RuntimeError(got.get("problem") or "the report could not be made")
        return got

    # ---- what the page shows --------------------------------------------
    def state_of(self, key):
        tool = catalog.find(key)
        up = self.running.get(key)
        if up is not None and not up.alive():
            self.running.pop(key, None)
            up = None
        info = tool.as_json()
        info["install"] = self.install_of(tool)      # the copy in use, not always the lab's
        info["running"] = up is not None
        info["url"] = up.url if up else ""
        info["since"] = int(time.time() - up.started) if up else 0
        return info

    def state(self):
        there = self.installed() if self.labs_found else {}
        tools = []
        for tool in catalog.TOOLS:
            info = self.state_of(tool.key)
            if tool.key in there:
                info["installed"] = there[tool.key]
            tools.append(info)
        # "api" is what this server offers (PAGE_API), so a page taken from the cluster can tell a
        # launcher that answers a route from one built before it, and not ask the older one.
        return {"user": self.user, "host": self.host, "address": self.address,
                "tools": tools, "categories": catalog.categories(),
                "labs": self.labs_state(), "setup": self.state_of("setup"), "api": PAGE_API}

    # ---- runs, across every tool ----------------------------------------
    def runs(self, refresh=False, limit_each=12):
        """The most recent run folders of every tool, read in one go over the connection.

        One shell command rather than one per tool: each round trip to a login node costs a
        moment, and this is read whenever the Results view is opened.
        """
        age, cached = self._runs_cache
        if cached and not refresh and time.time() - age < 20:
            return cached
        parts = []
        root = posixpath.dirname(self.labs).strip().rstrip("/")
        for tool in catalog.TOOLS:
            for folder in tool.runs:
                moved = catalog.rebase(folder, root)
                if moved != folder:
                    folder = shlex.quote(root) + moved[len(root):]
                # ls -dt: newest first, directories only, and silence when the folder is absent.
                parts.append('for d in $(ls -dt %s/*/ 2>/dev/null | head -%d); do '
                             'printf "%s\\t%%s\\t%%s\\n" "$d" "$(stat -c %%Y "$d" 2>/dev/null)"; done'
                             % (folder, limit_each, tool.key))
        status, out, _ = tunnel.run(self.t, "; ".join(parts), timeout=60)
        found = []
        for line in (out or "").splitlines():
            bits = line.rstrip("\n").split("\t")
            if len(bits) != 3 or not bits[0]:
                continue
            key, path, when = bits[0], bits[1].rstrip("/"), bits[2]
            if key not in catalog.BY_KEY:
                continue
            found.append({"tool": key, "tool_name": catalog.BY_KEY[key].name,
                          "name": os.path.basename(path), "path": path,
                          "when": int(when) if when.isdigit() else 0,
                          "next_steps": [{"to": to, "label": label}
                                         for to, label in catalog.BY_KEY[key].next_steps]})
        found.sort(key=lambda row: row["when"], reverse=True)
        self._runs_cache = (time.time(), found)
        return found


def index_page(name="index.html", root=""):
    """One of the launcher's pages, with the bar along its bottom filled in from the docs' credit line."""
    from . import docs, docskit
    path = os.path.join(root or WEB, name)
    if root and not os.path.isfile(path):
        path = os.path.join(WEB, name)
    with open(path, encoding="utf-8") as fh:
        page = fh.read()
    return (page.replace("/*__CREDITBAR_CSS__*/", docskit.CREDITBAR_CSS.strip())
                .replace("<!--__CREDITBAR__-->", docs.creditbar()))


def bootstrap_script():
    """This launcher's copy of the first-time set-up script, from the checkout or the bundle."""
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setup", "bootstrap.sh")
    if not os.path.isfile(here):
        here = os.path.join(getattr(sys, "_MEIPASS", ""), "hope_labs", "setup", "bootstrap.sh")
    with open(here, encoding="utf-8") as handle:
        text = handle.read()
    if "HOPEBOOTEOF" in text:
        raise RuntimeError("the set-up script holds its own end marker")
    return text


def stream(t, command, said, timeout=3600):
    """Run a command, handing each line it prints to `said` as it comes. Returns its status."""
    ch = t.open_session(timeout=60)
    ch.settimeout(1.0)
    ch.exec_command(command)
    buf, deadline = "", time.time() + timeout
    while time.time() < deadline:
        try:
            chunk = ch.recv(65536)
        except socket.timeout:
            chunk = None
        if chunk:
            buf += chunk.decode("utf8", "replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                if line.strip():
                    said(line.rstrip())
        while ch.recv_stderr_ready():
            for line in ch.recv_stderr(65536).decode("utf8", "replace").splitlines():
                if line.strip():
                    said(line.rstrip())
        if chunk == b"" or (ch.exit_status_ready() and not ch.recv_ready()):
            break
    if buf.strip():
        said(buf.rstrip())
    status = ch.recv_exit_status() if ch.exit_status_ready() else 124
    ch.close()
    return status


def tail(buf, n=12):
    lines = [l for l in (buf or "").splitlines() if l.strip()]
    return "\n".join(lines[-n:])


def said(out, err):
    """The one line of a runner's answer worth putting in front of a person.

    A refusal from the runner is a sentence on stderr, but a bug in it is a traceback, and the
    first three hundred characters of a traceback are frames and file names rather than what went
    wrong. The last line is the sentence either way.
    """
    lines = [l.strip() for l in ((err or "").strip() or (out or "")).splitlines() if l.strip()]
    return lines[-1][:300] if lines else ""


def handler_for(hub):
    class Handler(BaseHTTPRequestHandler):
        server_version = "hope-labs"

        def log_message(self, fmt, *args):
            pass                          # the window is the log; a browser's chatter is not news

        def _send(self, code, kind, payload):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        def _json(self, data, code=200):
            self._send(code, "application/json", json.dumps(data).encode())

        def _stream(self, name, size, pieces):
            """Send a file from the cluster as it arrives, rather than holding it here first.

            Content-Length is given because it is known, so the browser can draw a real progress
            bar; a run folder's zip is the thing people will fetch most and it is the one worth
            watching. A connection that drops part way leaves a short file, which the browser
            reports as a failed download - better than a silent truncation.
            """
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"' % os.path.basename(name).replace('"', ""))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command == "HEAD":
                return
            for piece in pieces:
                self.wfile.write(piece)

        def _allowed(self, query):
            return query.get("t", [""])[0] == hub.token or self.headers.get("X-Token") == hub.token

        def _static(self, name):
            root = hub.web()
            path = os.path.realpath(os.path.join(root, name.lstrip("/")))
            if not path.startswith(os.path.realpath(root)) or not os.path.isfile(path):
                # A page taken off the cluster may be missing a file this launcher's own has,
                # and the built-in one is the answer rather than a blank panel.
                path = os.path.realpath(os.path.join(WEB, name.lstrip("/")))
                if not path.startswith(os.path.realpath(WEB)) or not os.path.isfile(path):
                    return self._send(404, "text/plain", b"not found")
            kind = mimetypes.guess_type(path)[0] or "application/octet-stream"
            with open(path, "rb") as handle:
                self._send(200, kind, handle.read())

        def _docs(self, path):
            from . import docs
            status, kind, body, location = docs.respond(path)
            if location:
                self.send_response(status)
                self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            return self._send(status, kind, body)

        def do_GET(self):                                       # noqa: N802
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path in ("/", "/index.html"):
                return self._send(200, "text/html; charset=utf-8", index_page(root=hub.web()).encode("utf-8"))
            # The documentation needs no token: it is the same for everyone, holds nothing of the
            # session, and a page of it kept as a bookmark has no token to carry.
            if url.path == "/docs" or url.path.startswith("/docs/"):
                return self._docs(url.path)
            # Each tool opens in a tab of its own. The tab comes here first, starts the tool and
            # shows the wait, then goes on to the tool's own page.
            if url.path == "/tool":
                return self._send(200, "text/html; charset=utf-8", index_page("tool.html", hub.web()).encode("utf-8"))
            # The page code that draws MSMS's surfaces: the browser's half of the module the lab's
            # tools share (surface.VIEWER_JS), served from the module itself rather than kept as a
            # copy beside the page that could drift from it.
            if url.path == "/static/msms.js":
                from . import surface
                return self._send(200, "text/javascript; charset=utf-8",
                                  surface.VIEWER_JS.encode("utf-8"))
            if url.path.startswith("/static/"):
                return self._static(url.path[len("/static/"):])
            if not self._allowed(query):
                return self._send(403, "text/plain", b"open the page from the launcher window")
            try:
                if url.path == "/api/state":
                    return self._json(hub.state())
                if url.path == "/api/runs":
                    return self._json({"runs": hub.runs(refresh=query.get("refresh", [""])[0] == "1")})
                # Looking inside a run, and taking something out of it. Streamed through this
                # server so the browser's own download machinery decides where it lands: the
                # launcher writes nothing to this computer itself.
                if url.path == "/api/files/get":
                    asked = query.get("path", [""])[0]
                    size = filesmod.fetchable(hub, asked, query.get("over", [""])[0])
                    return self._stream(asked, size, filesmod.chunks(hub, asked))
                # The viewer's surface of a structure, computed by MSMS on the cluster where the
                # file is. It reads the same file a download would, so it is let through by the
                # same rules (files.fetchable), and a refusal reads the same.
                if url.path == "/api/files/surface":
                    return self._json(filesmod.surface(hub, query.get("path", [""])[0],
                                                       query.get("over", [""])[0]))
                # The structure browser: where it may start, and one folder at a time. The same
                # two answers a page running on the cluster gets from its own server, so the one
                # dialog is the same dialog wherever it is opened.
                if url.path == "/api/places":
                    return self._json(filesmod.places(hub))
                if url.path == "/api/browse":
                    return self._json(filesmod.browse(hub, query.get("path", [""])[0],
                                                      query.get("want", ["structure"])[0]))
                if url.path == "/api/files/zip":
                    asked = query.get("path", [""])[0]
                    where, size, tidy = filesmod.zipped(hub, asked, hub.say)
                    if size > filesmod.WARN_BYTES and not query.get("over", [""])[0]:
                        filesmod.forget(hub, tidy)
                        raise filesmod.Refused(
                            "that run zips to %.1f GB. Ask again with over=1 if that is meant."
                            % (size / 1e9))
                    try:
                        return self._stream(where, size, filesmod.chunks(hub, where))
                    finally:
                        filesmod.forget(hub, tidy)
                # The canvas: the cards it draws from, the flow it starts from, and the flows
                # kept on this computer. Behind the token like everything else under /api.
                if url.path == "/api/flow/cards":
                    return self._json(canvas.catalogue())
                if url.path == "/api/flow/example":
                    return self._json(canvas.example())
                if url.path == "/api/flow/saved":
                    return self._json({"flows": settings.flows()})
                # The flows that have been queued, so that a person who comes back the next day
                # finds the one they started rather than a folder they have to remember.
                if url.path == "/api/flow/queued":
                    return self._json({"runs": settings.queued()})
                # Where HOPE Labs is on this cluster, and the first-time set-up while it runs.
                if url.path == "/api/labs":
                    return self._json(hub.labs_state())
            except filesmod.Refused as refusal:
                # A refusal is an answer, not a fault: the path was outside what this reads, or
                # the download is larger than somebody meant to start. It reads as a sentence.
                return self._json({"error": str(refusal)}, 400)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # The page navigated away while this was still being answered. There is nobody
                # left to send anything to, and nothing went wrong: printing a stack trace into
                # the launcher's window tells a person their launcher has broken when it has not.
                return
            except Exception as exc:                            # noqa: BLE001
                traceback.print_exc()
                return self._json({"error": str(exc)}, 500)
            self._send(404, "text/plain", b"not found")

        def do_POST(self):                                      # noqa: N802
            url = urlparse(self.path)
            if not self._allowed(parse_qs(url.query)):
                return self._send(403, "text/plain", b"missing or wrong token")
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._json({"error": "could not read that request"}, 400)
            try:
                if url.path == "/api/launch":
                    return self._json(hub.launch(body.get("tool", ""), body.get("runs", "")))
                if url.path == "/api/stop":
                    hub.stop(body.get("tool", ""))
                    return self._json(hub.state_of(body.get("tool", "")))
                # A HOPE Labs this cluster already has, somewhere this launcher did not look; or
                # setting one up, which is the first thing a cluster new to HOPE Labs needs.
                if url.path == "/api/labs/use":
                    return self._json(hub.labs_here(body.get("path", "")))
                if url.path == "/api/labs/setup":
                    return self._json(hub.set_up(body.get("folder", ""), body.get("source", ""),
                                                 body.get("proxy", "")))
                # The canvas posts the flow as it stands and is told what is wrong with it, so the
                # page and the cluster are reading one answer rather than each their own.
                if url.path == "/api/flow/check":
                    return self._json(canvas.check(body.get("flow") or {},
                                                   done=body.get("done") or ()))
                if url.path == "/api/flow/save":
                    return self._json({"flows": settings.flow_save(body.get("flow") or {})})
                if url.path == "/api/flow/delete":
                    return self._json({"flows": settings.flow_delete(body.get("name", ""))})
                # The plan is what a person reads before committing a cluster allocation: what
                # each card will queue, where it lands, and the numbers that decide what it costs.
                if url.path == "/api/flow/plan":
                    return self._json(canvas.plan(hub.flows, body.get("flow") or {},
                                                  body.get("runs", "")))
                if url.path == "/api/flow/launch":
                    got = canvas.launch(hub.flows, body.get("flow") or {}, body.get("runs", ""))
                    # The folder is the only way back to a flow once this window is closed, so it
                    # is written down with the drawing it came from before the answer goes out. A
                    # settings file that cannot be written is not worth losing the launch over:
                    # the flow is queued either way, and the folder is in the answer.
                    try:
                        settings.queued_save(body.get("flow") or {}, got.get("folder", ""))
                    except (OSError, ValueError) as why:
                        hub.say("the flow was queued but not written down: %s" % why)
                    return self._json(got)
                if url.path == "/api/flow/status":
                    return self._json(hub.flows.status(body.get("folder", "")))
                if url.path == "/api/files/ls":
                    return self._json(filesmod.listing(hub, body.get("path", "")))
                # A structure given from this computer, written into the person's own scratch so
                # that everything after it works on a path on the cluster like any other.
                if url.path == "/api/files/put":
                    import base64
                    try:
                        raw = base64.b64decode(str(body.get("data", "")), validate=True)
                    except (ValueError, TypeError):
                        raise filesmod.Refused("that file did not arrive whole; try it again")
                    return self._json(filesmod.put(hub, body.get("name", ""), raw))
                # Carrying on is queueing, so it commits as much as Launch did. The page offers it
                # only once nothing of the flow is left in the queue, and the runner queues the
                # cards that have not finished rather than the whole flow again.
                # Correcting a flow that stopped. The rules about what may change are the
                # runner's, not this launcher's: it is the one that knows what has already run.
                if url.path == "/api/flow/edit":
                    return self._json(hub.flows.edit(body.get("folder", ""),
                                                     body.get("flow") or {}))
                # The flow's own page, gathered on the cluster where the run folders are.
                if url.path == "/api/flow/report":
                    return self._json(hub.flows.report(body.get("folder", "")))
                if url.path == "/api/flow/resume":
                    return self._json(hub.flows.resume(body.get("folder", "")))
                if url.path == "/api/flow/stop":
                    return self._json(hub.flows.stop(body.get("folder", "")))
                # Preview first, then write: the page asks twice on purpose, so nobody files a
                # report of their own run without having read what it quotes.
                if url.path == "/api/report":
                    return self._json(hub.report(body.get("kind", "feedback"),
                                                 body.get("said", ""), body.get("contact", ""),
                                                 body.get("context") or {},
                                                 bool(body.get("preview"))))
            except filesmod.Refused as refusal:
                return self._json({"error": str(refusal)}, 400)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # The page navigated away while this was still being answered. There is nobody
                # left to send anything to, and nothing went wrong: printing a stack trace into
                # the launcher's window tells a person their launcher has broken when it has not.
                return
            except Exception as exc:                            # noqa: BLE001
                return self._json({"error": str(exc)}, 400)
            self._send(404, "text/plain", b"not found")

    return Handler


class _Server(ThreadingHTTPServer):
    # Off, so a busy port raises instead of being shared. On Windows SO_REUSEADDR lets a second
    # process bind a port that is already listening, which would split one person's traffic
    # between two launchers; HTTPServer turns it on by default.
    allow_reuse_address = False

    def handle_error(self, request, client_address):
        """A browser that went away mid-answer is not an error anybody can act on.

        Pressing a link while a listing is still coming down closes the socket, and the write
        that was in flight raises. The launcher's window is what a person looks at when something
        is wrong, and a Python traceback in it is the worst thing it can say: it reads as the
        launcher having broken. Everything else is still printed, as it was.
        """
        why = sys.exc_info()[1]
        if isinstance(why, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        ThreadingHTTPServer.handle_error(self, request, client_address)


def serve(hub, port=0, host="127.0.0.1", tries=8):
    """Start the hub's own server. Returns (httpd, url).

    The same port every time, for the same person. Chrome remembers "always allow pop-ups" per
    address, and the address includes the port, so a page that moved port every session would be
    blocked again every session however often it was allowed. `port` is tried first, then the
    next few above it, then any free one, so a busy port never stops the launcher starting.
    """
    httpd = None
    for want in ([port + n for n in range(tries)] if port else []):
        try:
            httpd = _Server((host, want), handler_for(hub))
            break
        except OSError:
            continue
    if httpd is None:
        httpd = _Server((host, 0), handler_for(hub))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    bound = httpd.server_address[1]
    return httpd, "http://127.0.0.1:%d/?t=%s" % (bound, hub.token)


# --- flows -----------------------------------------------------------------------------------
# A flow is queued on the cluster and runs there. The launcher's part is to put the drawing where
# the cluster can read it and to ask the runner to start it; after that, nothing of the launcher
# is involved, and closing it leaves the flow running.

FLOW_INSTALL = "/path/to/hope-labs/HOPE-labs"


#: The person's spaces on the cluster, for Hub.spaces: what showquota lists, each prefixed so
#: it can be told from the rest, their home as the account names it (not $HOME, which a job or a
#: profile may have moved), then every folder they may keep things in, with whether they can
#: write there and what df says is free (kB), then any HOPE Labs checkout at the top of one or a
#: folder down, with the lines that say which edition it is.
SPACES = r"""q=$(showquota 2>/dev/null)
printf '%s\n' "$q" | sed 's/^/QUOTA /'
h=$(getent passwd "$(id -un)" 2>/dev/null | cut -d: -f6); echo "HOME|${h:-$HOME}"
{ printf '%s\n' "${SCRATCH:-}" "${WORK:-}" "${PROJECT:-}" "${WORKDIR:-}"
  printf '%s\n' "$q" | awk '$1 ~ /^\// {print $1}'
  for g in $(id -Gn 2>/dev/null); do
    for b in /scratch/group /scratch/groups /scratch/project /project /projects /work /group /groups; do
      [ -d "$b/$g" ] && echo "$b/$g"
    done
  done
} | awk 'NF && !seen[$0]++' | while IFS= read -r d; do
  [ -d "$d" ] || continue
  w=no; [ -w "$d" ] && w=yes
  echo "SPACE|$d|$w|$(df -Pk "$d" 2>/dev/null | awk 'NR==2{print $4}')"
  for f in "$d"/HOPE-labs/hope_labs/__init__.py "$d"/*/HOPE-labs/hope_labs/__init__.py; do
    [ -f "$f" ] || continue
    c=${f%/hope_labs/__init__.py}
    echo "LABS|$c|$(grep -hE '^(EDITION|APP) *=' "$f" | tr '\n' ' ')$(git -C "$c" config --get remote.origin.url 2>/dev/null)"
  done
done
true"""


def read_spaces(out):
    """[{path, writable, home, free_gb, free_files, labs: [{path, edition}]}] from what SPACES
    printed: the person's own scratch first, their home last, and never offered for an install,
    because a home folder's few thousand files are gone in the first minute of one."""
    from .setup.engine import showquota_rows
    lines = (out or "").splitlines()
    quota = showquota_rows("\n".join(l[6:] for l in lines if l.startswith("QUOTA ")))
    home = next((l[5:].rstrip("/") for l in lines if l.startswith("HOME|")), "")

    def inside(path, folder):
        return (path + "/").startswith(folder.rstrip("/") + "/")

    spaces, found = [], []
    for line in lines:
        bits = line.split("|")
        if bits[0] == "SPACE" and len(bits) >= 3:
            path = bits[1].rstrip("/") or "/"
            kb = bits[3].strip() if len(bits) > 3 else ""
            gb = int(kb) * 1024 / 1e9 if kb.isdigit() else None
            files = None
            rows = [q for q in quota if inside(path, q[0])]
            if rows:
                _, q_gb, files = max(rows, key=lambda q: len(q[0]))
                if q_gb is not None:
                    gb = q_gb if gb is None else min(gb, q_gb)
            spaces.append({"path": path, "writable": bits[2] == "yes",
                           "home": bool(home) and inside(path, home),
                           "free_gb": round(gb, 1) if gb is not None else None,
                           "free_files": files, "labs": []})
        elif bits[0] == "LABS" and len(bits) >= 3:
            found.append({"path": bits[1], "edition": edition_of("|".join(bits[2:]))})
    for one in found:
        holders = [sp for sp in spaces if inside(one["path"], sp["path"])]
        if holders:
            holder = max(holders, key=lambda sp: len(sp["path"]))
            if one["path"] not in [f["path"] for f in holder["labs"]]:
                holder["labs"].append(one)
    spaces.sort(key=lambda sp: (sp["home"], not sp["writable"]))
    return spaces


def edition_of(said):
    """Which edition a checkout is, from the EDITION and APP lines of its hope_labs/__init__.py
    and the repository it was cloned from.

    A checkout from before the editions were marked carries no EDITION line. It is told apart by
    where it came from, and failing that by its name: a public one set up before the public
    edition was renamed still says plain "HOPE Labs", but it was cloned from HOPE-Labs-Public.
    """
    said = said or ""
    for line in said.splitlines():
        if line.startswith("EDITION"):
            found = re.findall(r"""['\"]([^'\"]*)['\"]""", line)
            if found and found[0]:
                return found[0]
    return "public" if ("HOPE-Labs-Public" in said or "public edition" in said) else "lab"


def flow_command(install, args, root=""):
    """The line that runs the flow runner on the login node, in a given checkout.

    The runner needs Python 3.8 or newer. Rather than name one and fail on a cluster that keeps it
    somewhere else, the line tries each candidate and takes the first that is new enough, so the
    failure a person sees is about their flow rather than about an interpreter. The search is
    tools.python_search, shared with the design pages, which run from this same checkout and
    would otherwise need their own copy of it.
    """
    where = catalog.rebase(install or FLOW_INSTALL, root)
    return ('cd %s || exit 1; %sPYTHONPATH=%s exec "$PY" -m hope_flow.cli %s'
            % (shlex.quote(where), catalog.python_search("HOPEFLOW_PYTHON", "the flow runner"),
               shlex.quote(where), args))


class Flows:
    """Queueing flows on the cluster, and reading back how they are getting on."""

    def __init__(self, hub):
        self.hub = hub

    def _run(self, args, timeout=180):
        # The runner is the HOPE Labs checkout's own, so a cluster without one has none.
        self.hub.needs_labs("no flow can run")
        install = self.hub.installs.get("flow") or FLOW_INSTALL
        line = flow_command(install, args, self.hub.installs.get("_root", ""))
        return tunnel.run(self.hub.t, line, timeout=timeout)

    def _put(self, doc, name):
        """Write the drawing where the cluster can read it, and give back its path.

        It goes beside the flow's own folders rather than into a home directory: the clusters the
        lab uses have small home quotas, and a flow is scratch work.
        """
        # Refused before it is written, since the runner that would read it is not there.
        self.hub.needs_labs("no flow can run")
        where = "%s/.hope_flows" % (self.hub.scratch or "/tmp")
        path = "%s/%s.json" % (where, re.sub(r"[^A-Za-z0-9_.-]", "_", name or "flow"))
        text = json.dumps(doc, indent=2)
        status, out, err = tunnel.run(
            self.hub.t, "mkdir -p %s && cat > %s <<'HOPEFLOWEOF'\n%s\nHOPEFLOWEOF"
            % (shlex.quote(where), shlex.quote(path), text), timeout=60)
        if status != 0:
            raise RuntimeError("the flow could not be written to the cluster: %s"
                               % (err or out).strip()[:300])
        return path

    def catalogue(self):
        """The cards the install offers, so one added there reaches a launcher built before it."""
        status, out, err = self._run("catalogue --compact", timeout=120)
        if status != 0:
            raise RuntimeError(said(out, err) or "the runner would not list its cards")
        return json.loads(out[out.index("{"):out.rindex("}") + 1])

    def tools(self):
        """The tools the install offers, so one added there is listed here."""
        status, out, err = self._run("tools --compact", timeout=120)
        if status != 0:
            raise RuntimeError(said(out, err) or "the runner would not list the tools")
        return json.loads(out[out.index("{"):out.rindex("}") + 1])

    def site(self):
        """What the install's bar says, so a launcher states the current copyright and credits."""
        status, out, err = self._run("site --compact", timeout=120)
        if status != 0:
            raise RuntimeError(said(out, err) or "the runner would not say what its bar reads")
        return json.loads(out[out.index("{"):out.rindex("}") + 1])

    def plan(self, doc, runs=""):
        """What this flow would do, worked out on the cluster, where the tools actually are."""
        path = self._put(doc, doc.get("name"))
        args = "plan %s --json" % shlex.quote(path)
        if runs:
            args += " --runs " + shlex.quote(runs)
        status, out, err = self._run(args)
        try:
            return json.loads(out[out.index("{"):out.rindex("}") + 1])
        except (ValueError, IndexError):
            raise RuntimeError("the runner did not answer with a plan: %s"
                               % ((err or out).strip()[:400] or "it said nothing"))

    def launch(self, doc, runs=""):
        """Queue the flow. Returns where it is and what is waiting on what."""
        path = self._put(doc, doc.get("name"))
        args = "submit " + shlex.quote(path)
        if runs:
            args += " --runs " + shlex.quote(runs)
        status, out, err = self._run(args, timeout=600)
        if status != 0:
            raise RuntimeError((err or out).strip()[:600] or "the runner refused the flow")
        lines = [l.strip() for l in (out or "").splitlines() if l.strip()]
        folder = next((l for l in lines if l.startswith("/")), "")
        if not folder:
            raise RuntimeError("the runner queued something but did not say where: %s"
                               % " ".join(lines)[:300])
        self.hub.say("flow %s queued in %s" % (doc.get("name") or "", folder))
        return {"folder": folder, "log": lines}

    def status(self, folder):
        """How far a flow has got, read from the record its own jobs keep."""
        status, out, err = self._run("status %s --json" % shlex.quote(folder))
        try:
            return json.loads(out[out.index("{"):out.rindex("}") + 1])
        except (ValueError, IndexError):
            raise RuntimeError("that flow's record could not be read: %s"
                               % ((err or out).strip()[:300] or "it said nothing"))

    def edit(self, folder, doc):
        """Replace the drawing of a flow that has stopped. The runner says what may change."""
        path = self._put(doc, (doc or {}).get("name") or "edited")
        status, out, err = self._run("edit %s %s --json"
                                     % (shlex.quote(str(folder)), shlex.quote(path)))
        try:
            return json.loads(out[out.index("{"):out.rindex("}") + 1])
        except (ValueError, IndexError):
            raise RuntimeError(said(out, err)
                               or "the runner would not take the corrected flow")

    def report(self, folder):
        """Write the flow's one page on the cluster. Returns where it landed."""
        status, out, err = self._run("report %s" % shlex.quote(str(folder)), timeout=300)
        where = (out or "").strip().splitlines()
        if status != 0 or not where:
            raise RuntimeError(said(out, err) or "the runner would not write the flow's page")
        return {"path": where[-1].strip()}

    def resume(self, folder):
        """Carry on a flow that stopped. What finished is left alone; the rest is queued again.

        It waits as long as launch rather than as long as stop, because it does the same work:
        a flow of five cards is a dozen sbatch calls down the connection, and a resume cut short
        halfway would leave a person with half a flow in the queue and no way to tell which half.
        """
        status, out, err = self._run("resume %s" % shlex.quote(folder), timeout=600)
        if status != 0:
            raise RuntimeError(said(out, err) or "the flow could not be carried on")
        return {"resumed": (out or "").strip()}

    def stop(self, folder):
        status, out, err = self._run("stop %s" % shlex.quote(folder))
        if status != 0:
            raise RuntimeError(said(out, err) or "the flow could not be stopped")
        return {"stopped": (out or "").strip()}


# --- the page the launcher serves --------------------------------------------------------------
# The launcher is a shell: it signs in, holds one connection, and serves a page. The page was
# always the one built into it, which meant every change to the page - a new view, a reworded
# refusal, a field on a card - needed everybody to download a launcher again. Sharing a tool
# should not mean sharing it repeatedly.
#
# So the page is taken from the lab's install on the cluster when that install's page says it can
# work against this launcher's server, and from the build when it cannot. The two numbers that
# decide it are in __init__.py. A launcher that is too old to serve the newer page keeps its own
# and says so in one line, rather than serving a page whose buttons call routes it does not have.

PAGE_CACHE = os.path.join(os.path.expanduser("~"), ".hope_labs_page")
PAGE_FILES = ("index.html", "app.js", "flow.js", "style.css", "tool.html", "tool.js")


def page_from_cluster(transport, install, provides, say=None):
    """Fetch the install's page into a folder on this computer. Returns (folder, what to say).

    Fails safe in every direction: anything unreadable, anything missing, any version this
    launcher is too old for, and the answer is the page this launcher was built with.
    """
    say = say or (lambda *a: None)
    try:
        import paramiko
        sftp = paramiko.SFTPClient.from_transport(transport)
    except Exception as why:                                    # noqa: BLE001
        return "", "the page on the cluster could not be reached (%s); using this launcher's own" % why
    try:
        needs, version = _page_version(sftp, install)
        if needs is None:
            return "", ""                                       # no install to read; nothing to say
        if needs > provides:
            return "", ("HOPE Labs %s on the cluster needs a newer launcher than this one. "
                        "This launcher's own page is being used; download %s to get the rest."
                        % (version or "there", version or "the current release"))
        where = os.path.join(PAGE_CACHE, re.sub(r"[^A-Za-z0-9_.-]", "_", install))
        os.makedirs(where, exist_ok=True)
        got = 0
        for name in PAGE_FILES:
            try:
                sftp.get("%s/hope_labs/web/%s" % (install, name), os.path.join(where, name))
                got += 1
            except IOError:
                continue                                        # a page file this install lacks
        if got < 3:
            return "", ""                                       # not a page; keep the built-in one
        _page_docs(sftp, install, where)
        say("page %s from the cluster" % (version or ""))
        return where, ""
    except Exception as why:                                    # noqa: BLE001
        return "", "the page on the cluster could not be read (%s); using this launcher's own" % why
    finally:
        try:
            sftp.close()
        except Exception:                                       # noqa: BLE001
            pass


def _page_version(sftp, install):
    """(the server API that install's page needs, its version), or (None, "") when there is none."""
    try:
        with sftp.open("%s/hope_labs/__init__.py" % install) as fh:
            text = fh.read().decode("utf-8", "replace")
    except IOError:
        return None, ""
    needs = re.search(r"^PAGE_NEEDS\s*=\s*(\d+)", text, re.M)
    version = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    # An install from before these existed served a page this launcher's server can read, since
    # that is the server it had; treat it as the first contract rather than refusing it.
    return (int(needs.group(1)) if needs else 1), (version.group(1) if version else "")


def _page_docs(sftp, install, where):
    """The documentation beside the page, so it is as current as the page is."""
    for part in ("docs/pages", "docs/img"):
        here = os.path.join(where, *part.split("/"))
        os.makedirs(here, exist_ok=True)
        try:
            names = sftp.listdir("%s/hope_labs/web/%s" % (install, part))
        except IOError:
            continue
        for name in names[:200]:
            try:
                sftp.get("%s/hope_labs/web/%s/%s" % (install, part, name),
                         os.path.join(here, name))
            except IOError:
                continue
