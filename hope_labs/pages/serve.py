"""The design page: one process, stdlib only, serving the form, the Runs screen and the API.

By Aadhil Haq

Run it on a login node, where the queue and the RCSB are::

    python -m hope_labs.pages.serve rfdiffusion --port 0

then from your own computer::

    ssh -N -L 8130:localhost:8130 grace.hprc.tamu.edu

and open the URL it printed. It carries a token and nothing but the documentation answers without
it, so the page is yours even though the login node is shared. HOPE Labs starts it for you and
forwards the port itself; the two lines above are for a terminal.

The same module serves both tools. Which one is the first argument, and everything that differs
between them is read from that tool's card in hope_flow/cards.py. --dry-run writes the run folder
and the job script and queues nothing, which is how the page is worked on without spending twelve
hours of an A100 on it.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from hope_flow import drivers

from . import SERVES
from . import docs as pagedocs
from . import runs as runsmod
from . import submit as submitmod
from .. import docskit

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

#: What may be downloaded out of a run folder. A design, a table, the plan, the job script and
#: the log, which is everything a person has a reason to want; anything else in there is a cache
#: the job wrote for itself.
SERVED = (".pdb", ".cif", ".json", ".out", ".csv", ".fa", ".fasta", ".sbatch", ".yaml", ".txt",
          ".log")

#: The most a request body may be. The cap on an uploaded structure is submitmod's; this is the
#: guard in front of it, so a body far larger is dropped before it is read into memory.
BODY_CAP = submitmod.UPLOAD_CAP + 1024 * 1024


class State:
    tool = ""
    token = ""
    roots = ()                  # the run folders this page reads and writes, in order
    dry_run = False
    staging = ""
    install = ""
    docs = None
    #: One submission at a time. The dry-run flag the driver honours is a module flag, which is
    #: the right shape for the runner and the wrong one for a threaded server, so submissions are
    #: serialised rather than left to race over it.
    queueing = threading.Lock()


S = State()


def me():
    return os.environ.get("USER") or ""


def cluster_address():
    """What to tunnel to. A login node calls itself login1.cluster, which is no use from
    outside, so the address comes from the name Slurm knows the cluster by."""
    try:
        config = subprocess.run(["scontrol", "show", "config"], capture_output=True, text=True,
                                timeout=10).stdout
        name = re.search(r"ClusterName\s*=\s*(\S+)", config)
        if name:
            return "%s.hprc.tamu.edu" % name.group(1).lower()
    except (OSError, subprocess.SubprocessError):
        pass
    return socket.getfqdn()


def index_page():
    """The form, with the tool's name in it and the bar along the bottom filled in.

    The bar states the version, the copyright and whose work the run does, and it is the only
    part of the page written here rather than in web/index.html.
    """
    with open(os.path.join(WEB, "index.html"), encoding="utf-8") as handle:
        page = handle.read()
    return (page.replace("/*__CREDITBAR_CSS__*/", docskit.CREDITBAR_CSS.strip())
                .replace("<!--__CREDITBAR__-->", S.docs.creditbar())
                .replace("__APP__", S.docs.app)
                .replace("__TOOL__", S.tool)
                .replace("__MARK__", pagedocs.mark(S.tool))
                .replace("__FAVICON__", pagedocs.favicon(S.tool)))


def inside_roots(path):
    """A path the page may touch: inside one of the run folders it was started with."""
    full = os.path.realpath(os.path.abspath(os.path.expanduser(path or "")))
    for root in S.roots:
        root = os.path.realpath(os.path.abspath(root))
        if full == root or full.startswith(root + os.sep):
            return full
    raise submitmod.Refused("%s is outside the run folders this page serves (%s)"
                            % (path, ", ".join(S.roots)))


# ------------------------------------------------------------------ the API

def api_hello():
    """Everything the page needs to draw itself.

    The fields come from the tool's card, so the form is the card: there is no second list of
    settings here to fall out of step with the canvas, and a setting added to the card appears on
    the page without a line being written.
    """
    return {
        "tool": S.tool, "app": S.docs.app, "tool_version": S.docs.version,
        "user": me(), "host": socket.gethostname(), "cluster": cluster_address(),
        "roots": list(S.roots), "install": S.install, "dry_run": S.dry_run,
        "card": {"name": submitmod.card_of(S.tool).name,
                 "tagline": submitmod.card_of(S.tool).tagline,
                 "note": submitmod.card_of(S.tool).note},
        "fields": submitmod.fields(S.tool),
        "target_fields": submitmod.target_fields(),
        "resources": drivers.DESIGN_RESOURCES[S.tool],
    }


def api_runs(query):
    mine = query.get("scope", ["mine"])[0] == "mine"
    found = runsmod.find_runs(S.roots, user=me() if mine else None)
    states = runsmod.job_states(
        [(runsmod.read_json(os.path.join(run, "submission.json"), {}) or {}).get("job_id")
         for run in found])
    return {"runs": [runsmod.summarise(run, states) for run in found[:200]],
            "scope": "mine" if mine else "all", "user": me(), "roots": list(S.roots)}


def api_run(query):
    return {"run": runsmod.detail(inside_roots(query.get("path", [""])[0]))}


def api_submit(body):
    """Queue the run. The refusals are the flow's own, because it is the flow's own code."""
    dry = S.dry_run or bool(body.get("dry_run"))
    said = []
    with S.queueing:
        answer = submitmod.queue(S.tool, body, S.roots, dry_run=dry,
                                 say=lambda *what: said.append(" ".join(str(w) for w in what)))
    if answer.get("run"):
        answer["said"] = said
    return answer


# ------------------------------------------------------------------ serving

class Handler(BaseHTTPRequestHandler):
    server_version = "hope-design-page"

    def log_message(self, fmt, *args):            # one line per request, not three
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def _send(self, code, kind, payload, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _json(self, data, code=200):
        self._send(code, "application/json", json.dumps(data).encode("utf-8"))

    def _allowed(self, query):
        return query.get("t", [""])[0] == S.token or self.headers.get("X-Token") == S.token

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > BODY_CAP:
            raise submitmod.Refused("that is more than the page accepts in one go")
        return json.loads(self.rfile.read(length) or b"{}")

    def _static(self, name):
        path = os.path.realpath(os.path.join(WEB, name.lstrip("/")))
        if not path.startswith(os.path.realpath(WEB) + os.sep) or not os.path.isfile(path):
            return self._send(404, "text/plain", b"not found")
        kind = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as handle:
            self._send(200, kind, handle.read(), {"X-Content-Type-Options": "nosniff"})

    def _docs(self, path):
        """A page or the stylesheet of the documentation. What to send is decided by the shared
        documentation module, which every one of the lab's pages answers the same way; this
        writes it out."""
        status, kind, body, location = S.docs.respond(path)
        if location:
            self.send_response(status)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        return self._send(status, kind, body, {"X-Content-Type-Options": "nosniff"})

    def _download(self, query):
        """A file out of a run folder: a design, a table, the plan, the job script, the log."""
        run = inside_roots(query.get("path", [""])[0])
        name = query.get("file", [""])[0]
        path = os.path.realpath(os.path.join(run, name))
        if not path.startswith(run + os.sep) or not os.path.isfile(path):
            return self._send(404, "text/plain", b"no such file in that run")
        if not path.lower().endswith(SERVED):
            return self._send(403, "text/plain", b"that kind of file is not served")
        kind = mimetypes.guess_type(path)[0] or "text/plain"
        with open(path, "rb") as handle:
            self._send(200, kind, handle.read(),
                       {"Content-Disposition": 'attachment; filename="%s"'
                        % os.path.basename(path)})

    def do_GET(self):                                             # noqa: N802
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path in ("/", "/index.html"):
            return self._send(200, "text/html; charset=utf-8", index_page().encode("utf-8"))
        if url.path.startswith("/static/"):
            return self._static(url.path[len("/static/"):])
        # The documentation needs no token: it is the same for everyone, holds nothing of the
        # session, and a page of it kept as a bookmark has no token to carry.
        if url.path == "/docs" or url.path.startswith("/docs/"):
            return self._docs(url.path)
        if not self._allowed(query):
            return self._send(403, "text/plain",
                              b"missing or wrong token: open the URL the server printed")
        try:
            if url.path == "/api/hello":
                return self._json(api_hello())
            if url.path == "/api/runs":
                return self._json(api_runs(query))
            if url.path == "/api/run":
                return self._json(api_run(query))
            if url.path == "/api/download":
                return self._download(query)
        except submitmod.Refused as refusal:
            return self._json({"error": str(refusal)}, 400)
        except Exception:                                          # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": "the page hit an error; the server's terminal has the "
                                        "traceback"}, 500)
        self._send(404, "text/plain", b"not found")

    def do_POST(self):                                            # noqa: N802
        url = urlparse(self.path)
        if not self._allowed(parse_qs(url.query)):
            return self._send(403, "text/plain", b"missing or wrong token")
        try:
            body = self._body()
            if url.path == "/api/target":
                return self._json(submitmod.read_target(S.tool, body, S.staging))
            if url.path == "/api/check":
                return self._json(submitmod.check(S.tool, body, S.roots))
            if url.path == "/api/submit":
                return self._json(api_submit(body))
            if url.path == "/api/cancel":
                done, says = runsmod.cancel(inside_roots(body.get("path", "")))
                return self._json({"ok": done, "message": says})
            if url.path == "/api/resubmit":
                done, says = runsmod.resubmit(inside_roots(body.get("path", "")))
                return self._json({"ok": done, "message": says})
        except submitmod.Refused as refusal:
            return self._json({"error": str(refusal)}, 400)
        except Exception:                                          # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": "the page hit an error; the server's terminal has the "
                                        "traceback"}, 500)
        self._send(404, "text/plain", b"not found")


def serve(tool, host="127.0.0.1", port=0, runs=None, token="", dry_run=False, install=""):
    """Put the page up and give back (the server, the port, the token).

    Separated from main() so a test can start the page in its own process, drive it and shut it
    down, which is the only way to be sure the routes and the page agree.
    """
    if tool not in SERVES:
        raise ValueError("this page serves %s, not %r" % (" and ".join(SERVES), tool))
    S.tool = tool
    S.install = install or drivers.INSTALLS[tool]
    # The install is told to the driver as well, so a copy somewhere else is the one that runs.
    # A whole lab tree moved elsewhere is named with HOPEFLOW_LAB_ROOT, which drivers reads for
    # the pieces beside the install: RFdiffusion's environment, ProteinMPNN, BoltzGen's weights.
    drivers.INSTALLS[tool] = S.install
    S.roots = tuple(os.path.abspath(os.path.expanduser(root))
                    for root in (runs or [runsmod.runs_root(tool)]))
    for root in S.roots:
        # Made rather than demanded: the default is the person's own scratch, and a page that
        # refused to start until they had made a folder there would be refusing for nothing.
        os.makedirs(root, exist_ok=True)
    S.token = token or secrets.token_urlsafe(12)
    S.dry_run = bool(dry_run)
    S.staging = tempfile.mkdtemp(prefix="hope-design-page-")
    S.docs = pagedocs.Docs(tool, S.install)
    httpd = ThreadingHTTPServer((host, port), Handler)
    return httpd, httpd.server_address[1], S.token


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m hope_labs.pages.serve",
        description="Serve the page for one of the lab's two machine-learning design tools.")
    parser.add_argument("tool", choices=list(SERVES), help="which tool this page is for")
    parser.add_argument("--port", type=int, default=0,
                        help="port on this login node; 0 picks a free one")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address: keep it local and tunnel to it")
    parser.add_argument("--runs", action="append", default=None,
                        help="a run folder to read and write (repeatable; the default is the "
                             "tool's own, which is where a flow puts its runs too)")
    parser.add_argument("--install", default="",
                        help="where this tool is installed, if not the lab's own copy")
    parser.add_argument("--token", default=None,
                        help="a fixed token (the default is a new one each start)")
    parser.add_argument("--dry-run", action="store_true",
                        help="write the run folder and the job script, and queue nothing")
    args = parser.parse_args(argv)

    # Said here, before the page is up, so the launcher can tell a missing tool from a page that
    # started and then refused everything. NO-INSTALL is the marker hub.Hub.why reads, and the
    # sentence a person sees names the folder it looked in.
    where = args.install or drivers.INSTALLS[args.tool]
    if not os.path.isdir(where):
        print("NO-INSTALL %s" % where, file=sys.stderr)
        return 3

    try:
        httpd, port, token = serve(args.tool, host=args.host, port=args.port, runs=args.runs,
                                   token=args.token or "", dry_run=args.dry_run,
                                   install=args.install)
    except OSError as why:
        print("%s: the page could not take that port: %s" % (args.tool, why), file=sys.stderr)
        return 2
    print("%s: runs in %s" % (args.tool, S.roots[0])
          + (", dry run: nothing will be queued" if S.dry_run else ""))
    print("%s: from your computer:  ssh -N -L %d:localhost:%d %s"
          % (args.tool, port, port, cluster_address()))
    # The line HOPE Labs reads to know the page is up, and the only one it reads. Its shape is
    # hope_labs/tools.py, URL_LINE: change one and the launcher stops seeing the other start.
    print("open  http://127.0.0.1:%d/?t=%s" % (port, token), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n%s: stopped" % args.tool)
    finally:
        shutil.rmtree(S.staging, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
