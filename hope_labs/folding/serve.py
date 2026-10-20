"""The folding page: one process, stdlib only, serving the form, the Runs screen and the API.

By Aadhil Haq

Run it on a login node, which is the one machine that can reach the alignment server::

    python -m hope_labs.folding.serve boltz2 --port 0

then from your own computer::

    ssh -N -L 8140:localhost:8140 grace.hprc.tamu.edu

and open the URL it printed. It carries a token and nothing but the documentation answers without
it, so the page is yours even though the login node is shared. HOPE Labs starts it for you and
forwards the port itself; the two lines above are for a terminal.

The same module serves both tools, as the design page serves its two, and the shape of the
handshake is the same line in the same words: get that wrong and the page works perfectly while
the launcher never sees it start.

A page whose tool is not installed here still starts
----------------------------------------------------
It prints the NO-INSTALL marker the launcher reads, and then serves anyway, saying across the top
which tool is missing and where it looked. That is deliberate, and it is the one place these
pages differ from the design pages, which stop. The reason is ACES: neither folding tool has been
found there, and a launcher that says only "not installed" sends somebody looking in the wrong
place. The page can still show what a run would be told, and it refuses to queue one.
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
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import SERVES
from . import browse as browsemod
from . import docs as pagedocs
from . import job as jobmod
from . import runs as runsmod
from . import sites as sitesmod
from . import submit as submitmod
from .. import docskit

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
#: The shell and the palette are the design page's, so the four pages read as one family and
#: there is one stylesheet to change. A name not found here is looked for there.
SHARED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pages", "web")

#: What may be downloaded out of a run folder: a structure, a score, an alignment, the input, the
#: plan, the job script and the log, which is everything a person has a reason to want. The
#: viewer reads a structure through the same route.
SERVED = (".pdb", ".cif", ".json", ".out", ".csv", ".fa", ".fasta", ".yaml", ".yml", ".txt",
          ".log", ".a3m", ".sbatch", ".bibtex", ".png")

#: The most a request body may be. The cap on an uploaded FASTA is submitmod's; this is the guard
#: in front of it, so a body far larger is dropped before it is read into memory.
#:
#: Twice the file cap rather than a little over it: a file is sent base64 encoded, which is a
#: third larger than the file, so a guard at the file's own cap would refuse a file at the cap
#: with the wrong sentence. Being refused for the size of the encoding rather than the size of
#: the file is the kind of message nobody can act on.
BODY_CAP = submitmod.UPLOAD_CAP * 2

#: How much of an over-large body to read and throw away before answering. A client is still
#: sending when the Content-Length is refused, and answering into a half-sent request gives it a
#: broken pipe instead of the sentence: the browser then reports a network error and the person
#: never learns that the file was simply too big. Read in blocks so nothing large is ever held.
DRAIN_CAP = 64 * 1024 * 1024


class State:
    tool = ""
    token = ""
    roots = ()
    dry_run = False
    install = ""
    site = None
    installed = False
    why_not = ""
    docs = None
    #: One submission at a time. The dry-run flag the driver honours is a module flag, which is
    #: the right shape for a driver and the wrong one for a threaded server; and phase one talks
    #: to a server that rations its answers, so two at once would only be two that wait.
    queueing = threading.Lock()


S = State()

#: The mark, as the bytes /favicon.ico answers with. Filled in by serve(), once there is a tool.
FAVICON = b""


def me():
    return os.environ.get("USER") or ""


def cluster_address():
    """What to tunnel to. A login node calls itself login1.cluster, which is no use from
    outside, so the address comes from the name Slurm knows the cluster by."""
    address = getattr(S.site, "login_hosts", ()) or ()
    if address and address[0]:
        return address[0]
    name = sitesmod.cluster_name()
    if name in ("grace", "aces"):
        return "%s.hprc.tamu.edu" % name
    return socket.getfqdn()


def favicon_svg():
    """The tool's mark as a standalone SVG, for /favicon.ico."""
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">%s</svg>'
            % pagedocs.mark(S.tool, "#0d5c6b", "#c2603a")).encode("utf-8")


def index_page():
    """The form, with the tool's name in it and the bar along the bottom filled in."""
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
    raise jobmod.Refused("%s is outside the run folders this page serves (%s)"
                         % (path, ", ".join(S.roots)))


# ------------------------------------------------------------------ the API

def api_hello():
    """Everything the page needs to draw itself.

    The types and the settings come from the tool's own tables, so the form is those tables:
    there is no second list here to fall out of step with the job that reads them.
    """
    site = S.site
    return {
        "tool": S.tool, "app": S.docs.app, "tool_version": S.docs.version,
        "user": me(), "host": socket.gethostname(), "cluster": cluster_address(),
        "roots": list(S.roots), "install": S.install, "dry_run": S.dry_run,
        "installed": S.installed, "why_not": S.why_not,
        "site": {"key": site.key, "name": site.name, "gpus": site.gpu_types(),
                 "partition": site.gpu_partition, "max_hours": site.max_gpu_hours(),
                 "checked": S.tool in site.checked,
                 "notes": sorted(site.notes.values())},
        "entities": submitmod.entities(S.tool),
        "fields": submitmod.fields(S.tool, site),
        "resources": jobmod.RESOURCES[S.tool],
        # max_gpu_seconds is what the walltime estimate is clamped to: a suggestion the partition
        # would refuse is worse than no suggestion, because the refusal comes at submit.
        "limits": {"max_copies": submitmod.MAX_COPIES, "max_tokens": submitmod.MAX_TOKENS,
                   "warn_tokens": submitmod.WARN_TOKENS, "chains": len(submitmod.CHAIN_IDS),
                   "upload": submitmod.UPLOAD_CAP,
                   "max_gpu_seconds": int(site.max_gpu_hours() * 3600)},
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
    dry = S.dry_run or bool(body.get("dry_run"))
    if not S.installed and not dry:
        # Refused rather than queued. A job that cannot find its tool fails five minutes into a
        # booking, and the reason is already known here.
        raise jobmod.Refused(S.why_not)
    said = []
    with S.queueing:
        answer = submitmod.queue(S.tool, body, S.roots, install=S.install, site=S.site,
                                 dry_run=dry,
                                 say=lambda *what: said.append(" ".join(str(w) for w in what)))
    if answer.get("run"):
        answer["said"] = said
    return answer


def api_browse(query):
    """One folder of the cluster's own filesystem, for the picker dialog.

    Not confined to the run folders the way the run routes are: see browse.py for why that is
    both necessary and safe. The one thing kept from the rest of the page is the token, which is
    checked before this is reached like every other route here.
    """
    dirs = query.get("dirs", [""])[0] in ("1", "true", "yes")
    return browsemod.listing(query.get("path", [""])[0], dirs_only=dirs)


def api_copy(body):
    """Copy a finished run, or its structures, to a folder on the cluster."""
    said = []
    answer = submitmod.copy_out(inside_roots(body.get("path", "")), body.get("dest", ""),
                                body.get("what", "structures"),
                                say=lambda *what: said.append(" ".join(str(w) for w in what)))
    return dict(answer, said=said)


# ------------------------------------------------------------------ serving

class Handler(BaseHTTPRequestHandler):
    server_version = "hope-folding-page"

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
            self._drain(length)
            raise jobmod.Refused(
                "that is %.0f MB, and %d MB is the most the page accepts in one request"
                % (length / 1e6, BODY_CAP // 1024 // 1024))
        return json.loads(self.rfile.read(length) or b"{}")

    def _drain(self, length):
        """Read an over-large body away so the answer reaches the client rather than its socket.

        The connection is closed afterwards either way: there is no point keeping one open for a
        client that has just been told its request was too large.
        """
        left = min(length, DRAIN_CAP)
        while left > 0:
            got = self.rfile.read(min(left, 65536))
            if not got:
                break
            left -= len(got)
        self.close_connection = True

    def _static(self, name):
        """A file of the page's own, or of the design page's, which is where the shell lives."""
        for root in (WEB, SHARED):
            path = os.path.realpath(os.path.join(root, name.lstrip("/")))
            if path.startswith(os.path.realpath(root) + os.sep) and os.path.isfile(path):
                kind = mimetypes.guess_type(path)[0] or "application/octet-stream"
                with open(path, "rb") as handle:
                    return self._send(200, kind, handle.read(),
                                      {"X-Content-Type-Options": "nosniff"})
        return self._send(404, "text/plain", b"not found")

    def _docs(self, path):
        status, kind, body, location = S.docs.respond(path)
        if location:
            self.send_response(status)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        return self._send(status, kind, body, {"X-Content-Type-Options": "nosniff"})

    def _download(self, query):
        """A file out of a run folder. The viewer reads a structure through this too, so the
        disposition is only set when the browser is being asked to save it."""
        run = inside_roots(query.get("path", [""])[0])
        name = query.get("file", [""])[0]
        path = os.path.realpath(os.path.join(run, name))
        if not path.startswith(run + os.sep) or not os.path.isfile(path):
            return self._send(404, "text/plain", b"no such file in that run")
        if not path.lower().endswith(SERVED):
            return self._send(403, "text/plain", b"that kind of file is not served")
        kind = mimetypes.guess_type(path)[0] or "text/plain"
        extra = {}
        if query.get("save", [""])[0]:
            extra["Content-Disposition"] = ('attachment; filename="%s"'
                                            % os.path.basename(path))
        with open(path, "rb") as handle:
            self._send(200, kind, handle.read(), extra)

    def do_GET(self):                                             # noqa: N802
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path in ("/", "/index.html"):
            return self._send(200, "text/html; charset=utf-8", index_page().encode("utf-8"))
        if url.path.startswith("/static/"):
            return self._static(url.path[len("/static/"):])
        # The tool's mark, for a browser that asks for the old favicon address. The form says
        # which icon it wants and never asks for this, but the documentation is laid out by the
        # shared kit, which carries no icon link, so every documentation page asks. Answered
        # rather than refused: a favicon behind the token gate comes back 403, which a browser
        # logs as an error on a page that is working perfectly.
        if url.path == "/favicon.ico":
            return self._send(200, "image/svg+xml", FAVICON)
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
            if url.path == "/api/roots":
                return self._json(browsemod.roots(S.roots))
            if url.path == "/api/browse":
                return self._json(api_browse(query))
        except jobmod.Refused as refusal:
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
            if url.path == "/api/check":
                return self._json(submitmod.check(S.tool, body, S.roots, S.site, S.install))
            if url.path == "/api/paste":
                return self._json(submitmod.read_sequences(S.tool, body.get("text", "")))
            if url.path == "/api/upload":
                return self._json(submitmod.read_upload(S.tool, body))
            # A FASTA that is already here, picked by browsing rather than sent up from the
            # browser. Nothing crosses the connection: the path is read by this process.
            if url.path == "/api/sequences":
                return self._json(submitmod.read_path(S.tool, body.get("path", "")))
            if url.path == "/api/mkdir":
                return self._json(browsemod.make_dir(body.get("parent", ""),
                                                     body.get("name", "")))
            if url.path == "/api/submit":
                return self._json(api_submit(body))
            if url.path == "/api/copy":
                return self._json(api_copy(body))
            if url.path == "/api/cancel":
                done, says = runsmod.cancel(inside_roots(body.get("path", "")))
                return self._json({"ok": done, "message": says})
            if url.path == "/api/resubmit":
                done, says = runsmod.resubmit(inside_roots(body.get("path", "")))
                return self._json({"ok": done, "message": says})
        except jobmod.Refused as refusal:
            return self._json({"error": str(refusal)}, 400)
        except Exception:                                          # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": "the page hit an error; the server's terminal has the "
                                        "traceback"}, 500)
        self._send(404, "text/plain", b"not found")


def serve(tool, host="127.0.0.1", port=0, runs=None, token="", dry_run=False, install="",
          site=None):
    """Put the page up and give back (the server, the port, the token).

    Separated from main() so a test can start the page in its own process, drive it and shut it
    down, which is the only way to be sure the routes and the page agree.
    """
    if tool not in SERVES:
        raise ValueError("this page serves %s, not %r" % (" and ".join(SERVES), tool))
    S.tool = tool
    # A cluster whose login nodes reach the internet only through a proxy says so in its
    # profile; the alignments are fetched from here, so this process goes through it too.
    from ..setup import profile as profilemod
    profilemod.use_proxy()
    S.install = install or jobmod.INSTALLS[tool]
    # The install is told to the driver as well, so a copy somewhere else is the one that runs.
    jobmod.INSTALLS[tool] = S.install
    S.site = site or sitesmod.here()
    S.installed, S.why_not = sitesmod.installed_here(S.site, tool, S.install)
    S.roots = tuple(os.path.abspath(os.path.expanduser(root))
                    for root in (runs or [runsmod.runs_root(tool)]))
    for root in S.roots:
        # Made rather than demanded: the default is the person's own scratch, and a page that
        # refused to start until they had made a folder there would be refusing for nothing.
        os.makedirs(root, exist_ok=True)
    S.token = token or secrets.token_urlsafe(12)
    S.dry_run = bool(dry_run)
    S.docs = pagedocs.Docs(tool, S.install)
    global FAVICON
    FAVICON = favicon_svg()
    httpd = ThreadingHTTPServer((host, port), Handler)
    return httpd, httpd.server_address[1], S.token


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m hope_labs.folding.serve",
        description="Serve the page for one of the lab's two folding tools.")
    parser.add_argument("tool", choices=list(SERVES), help="which tool this page is for")
    parser.add_argument("--port", type=int, default=0,
                        help="port on this login node; 0 picks a free one")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address: keep it local and tunnel to it")
    parser.add_argument("--runs", action="append", default=None,
                        help="a run folder to read and write (repeatable; the default is the "
                             "tool's own under your scratch)")
    parser.add_argument("--install", default="",
                        help="where this tool is installed, if not the lab's own copy")
    parser.add_argument("--site", default="",
                        help="the cluster's key, when its own name does not say (grace, aces)")
    parser.add_argument("--token", default=None,
                        help="a fixed token (the default is a new one each start)")
    parser.add_argument("--dry-run", action="store_true",
                        help="write the run folder, the input and the job script, fetch no "
                             "alignments and queue nothing")
    args = parser.parse_args(argv)

    site = sitesmod.SITES.get(args.site.strip().lower()) if args.site else None
    try:
        httpd, port, token = serve(args.tool, host=args.host, port=args.port, runs=args.runs,
                                   token=args.token or "", dry_run=args.dry_run,
                                   install=args.install, site=site)
    except OSError as why:
        print("%s: the page could not take that port: %s" % (args.tool, why), file=sys.stderr)
        return 2

    # The marker the launcher reads for a missing install, printed and then carried on from. The
    # page says the same thing across its top and refuses to queue; see the module docstring.
    if not S.installed:
        print("NO-INSTALL %s" % S.install, file=sys.stderr)
        print("%s: %s" % (args.tool, S.why_not), file=sys.stderr)
    print("%s: %s, runs in %s" % (args.tool, S.site.name, S.roots[0])
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
