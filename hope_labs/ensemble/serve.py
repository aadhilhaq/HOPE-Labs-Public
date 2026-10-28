"""The ensemble page: one process, stdlib only, serving the form, the Runs screen and the API.

By Aadhil Haq

Run it on a login node, which is the one machine that can reach the alignment server::

    python -m hope_labs.ensemble.serve bioemu --port 0

then from your own computer::

    ssh -N -L 8140:localhost:8140 grace.hprc.tamu.edu

and open the URL it printed. It carries a token and nothing but the documentation answers without
it, so the page is yours even though the login node is shared. HOPE Labs starts it for you and
forwards the port itself; the two lines above are for a terminal.

The shape of this is the folding pages' (hope_labs/folding/serve.py), down to the words of the
handshake line the launcher reads: get that wrong and the page works perfectly while the launcher
never sees it start. What differs is what a run is - one sequence rather than a table of chains,
and a spread of structures rather than a ranking of them - and one route the other pages have no
use for: a comparison against a simulation of the same chain, which is the whole reason this page
sits beside HOPE-MD.

A page whose tool is not installed here still starts, prints the NO-INSTALL marker the launcher
reads, says across its top what is missing and where it looked, and refuses to queue.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import secrets
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import SERVES
from . import docs as pagedocs
from . import job as jobmod
from . import runreport as reportmod
from . import runs as runsmod
from . import submit as submitmod
from .. import images as imagesmod
from .. import picker as pickermod
from .. import reporting as rk
from .. import staged as stagedmod
from ..folding import browse as browsemod
from ..folding import sites as sitesmod

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
#: The shell and the palette are the design page's, so every page of ours reads as one family and
#: there is one stylesheet to change. A name not found here is looked for there, and then in the
#: folding page's folder, which is where the lab vendors 3Dmol: the viewer is drawn in the
#: browser, and a page reached through a tunnel has no internet to fetch it from a CDN. The
#: design and docking pages already look in the same places.
SHARED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pages", "web")
VENDOR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "folding",
                      "web")

#: What may be downloaded out of a run folder: the samples, the structures, what reading them
#: found, the alignment, the job script and the log. The viewer reads a structure through the
#: same route.
SERVED = (".pdb", ".cif", ".json", ".out", ".dat", ".fa", ".fasta", ".a3m", ".txt", ".log",
          ".sbatch", ".xtc", ".csv", ".png")

#: The most a request body may be. A sequence is kilobytes and an alignment is named rather than
#: sent, so this is a guard against a mistake rather than a limit anybody meets.
BODY_CAP = 4 * 1024 * 1024


class State:
    tool = ""
    token = ""
    roots = ()
    dry_run = False
    install = ""
    root = ""
    site = None
    installed = False
    why_not = ""
    docs = None
    #: One submission at a time. Phase one talks to a server that rations its answers, so two at
    #: once would only be two that wait.
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
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">%s</svg>'
            % pagedocs.mark(S.tool, "#0d5c6b", "#c2603a")).encode("utf-8")


def index_page():
    with open(os.path.join(WEB, "index.html"), encoding="utf-8") as handle:
        page = handle.read()
    # The bar along the bottom is the form pages' own (hope_labs/footer.py), styled by the shared
    # stylesheet: one still line and Credits, rather than the documentation's rolling one.
    return (page.replace("<!--__CREDITBAR__-->", S.docs.creditbar())
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

    The settings come from the tool's own table, so the form is that table: there is no second
    list here to fall out of step with the job that reads it.
    """
    site = S.site
    return {
        "tool": S.tool, "app": S.docs.app, "tool_version": S.docs.version,
        "user": me(), "host": socket.gethostname(), "cluster": cluster_address(),
        "roots": list(S.roots), "install": S.install, "image": S.install, "root": S.root,
        # What the page calls "My scratch" and "Home" when it says where a run goes, which are
        # the names the structure browser gives the same two folders.
        "home": os.path.realpath(os.path.expanduser("~")),
        "scratch": os.path.realpath(pickermod.scratch()) if pickermod.scratch() else "",
        "dry_run": S.dry_run, "installed": S.installed, "why_not": S.why_not,
        "site": {"key": site.key, "name": site.name, "gpus": site.gpu_types(),
                 "partition": site.gpu_partition, "max_hours": site.max_gpu_hours(),
                 "checked": S.tool in site.checked,
                 "notes": sorted(site.notes.values())},
        "fields": submitmod.fields(S.tool, site),
        # The three presets, with the minutes each costs on this site's card: choosing one is
        # choosing a cost, and the paragraph this replaced was doing that arithmetic in prose.
        "presets": [dict(one, minutes=jobmod.minutes_for(one["samples"])) for one in jobmod.PRESETS],
        "resources": jobmod.RESOURCES[S.tool],
        "limits": {"max_length": jobmod.MAX_LENGTH, "typical_max": jobmod.TYPICAL_MAX,
                   "min_samples": jobmod.MIN_SAMPLES,
                   "max_gpu_seconds": int(site.max_gpu_hours() * 3600)},
        # Said on the page itself, not only in the documentation: an ensemble in a viewer looks
        # exactly like a trajectory and will be read as one unless it says otherwise.
        "statement": NOT_A_TRAJECTORY,
    }


#: The one thing this page must say about itself, in the words the documentation uses.
NOT_A_TRAJECTORY = (
    "An ensemble is not a trajectory. These structures are draws from the distribution BioEmu "
    "learned, so the order they come in means nothing, no rate or pathway can be read off them, "
    "and what they carry is how often a shape occurs rather than when.")


#: What the browser lists, by the word the page asks with: a structure to read a chain off, or an
#: alignment to hand the job. A file in the wrong listing is a file that would be clicked and
#: then refused.
BROWSE_KINDS = {"structure": submitmod.STRUCTURE_SUFFIXES, "alignment": (".a3m",)}


def api_browse(query):
    """One folder of the cluster, for the structure browser.

    Not confined to the run folders, as hope_labs/folding/browse.py explains: the page is one
    person's own process and lists what they could list from a shell. A folder that is a run of
    ours says which structure it holds, so the browser can offer "Use this run's structure".
    """
    dirs = query.get("dirs", [""])[0] in ("1", "true", "yes")
    want = query.get("want", ["structure"])[0]
    got = browsemod.listing(query.get("path", [""])[0], dirs_only=dirs,
                            kinds=BROWSE_KINDS.get(want, submitmod.STRUCTURE_SUFFIXES))
    if want == "structure" and got.get("path") and not dirs:
        found = pickermod.run_structure(got["path"])
        if found:
            got["run"] = found
    return got


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


def api_report(query):
    options = {key[4:]: value[0] for key, value in query.items()
               if key.startswith("opt.") and value}
    return reportmod.document(S.tool,
                              inside_roots(query.get("rundir", query.get("path", [""]))[0]),
                              options, install=S.install)


def api_submit(body):
    dry = S.dry_run or bool(body.get("dry_run"))
    if not S.installed and not dry:
        # Refused rather than queued. A job that cannot find its image fails a minute into a
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


def api_copy(body):
    said = []
    answer = submitmod.copy_out(inside_roots(body.get("path", "")), body.get("dest", ""),
                                body.get("what", "ensemble"),
                                say=lambda *what: said.append(" ".join(str(w) for w in what)))
    return dict(answer, said=said)


# --- the comparison, which is this page's own -----------------------------------------------

def api_comparisons(query):
    """The comparisons already written for one run, newest first."""
    run = inside_roots(query.get("path", [""])[0])
    found = []
    folder = os.path.join(run, "compare")
    for name in sorted(os.listdir(folder) if os.path.isdir(folder) else []):
        doc = runsmod.read_json(os.path.join(folder, name, "compare.json"), {}) or {}
        if doc:
            found.append({"against": name, "headline": doc.get("headline") or "",
                          "simulation": doc.get("simulation") or {},
                          "visited": doc.get("visited"), "of": doc.get("of"),
                          "share_visited": doc.get("share_visited"),
                          "spread": doc.get("spread") or {},
                          "correlation": (doc.get("residues") or {}).get("correlation"),
                          "file": os.path.join("compare", name, "compare.json")})
    return {"comparisons": found, "path": run}


def api_compare(body):
    """Queue a comparison of one finished ensemble against one HOPE-MD run.

    The simulation is read wherever it is rather than only inside this page's run folders: an MD
    run belongs to HOPE-MD and lives under its own roots, and refusing to look there would make
    the one thing this page is for impossible. Nothing is written into it - the comparison is
    written into the ensemble's own folder - and the path is read by this process, not sent
    anywhere.
    """
    run = inside_roots(body.get("path", ""))
    md = os.path.realpath(os.path.abspath(os.path.expanduser(str(body.get("md") or "").strip())))
    if not md or not os.path.isdir(md):
        raise jobmod.Refused("name the HOPE-MD run folder to compare against; there is no folder "
                             "at %s" % (md or "that path"))
    if not runsmod.analysis(run):
        raise jobmod.Refused("this run has not been read yet, so there is nothing to compare: "
                             "wait for the job to finish, or queue it again if it stopped early.")
    top, traj = _md_files(md)
    if not top:
        raise jobmod.Refused(
            "%s holds no trajectory and no viewer export, so there is nothing to compare "
            "against. A HOPE-MD run keeps analysis/r1_view.pdb even after it has been tidied; "
            "this folder keeps neither." % md)
    name = runsmod.read_json(os.path.join(run, "submission.json"), {}).get("name") or "run"
    script = jobmod.compare_script_text(S.root, run, md, name, S.site,
                                        account=body.get("account") or "")
    if S.dry_run or body.get("dry_run"):
        return {"script": script, "queued": "", "dry_run": True, "reads": traj}
    import subprocess
    path = os.path.join(run, "compare.sbatch")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(script)
    done = subprocess.run(["sbatch", "--parsable", path], capture_output=True, text=True, cwd=run)
    if done.returncode != 0:
        raise jobmod.Refused((done.stderr or done.stdout).strip() or "sbatch refused the job")
    return {"queued": done.stdout.strip().split(";")[0], "reads": traj, "against": md}


def _md_files(md):
    from . import compare as comparemod
    return comparemod.md_files(md)


# ------------------------------------------------------------------ serving

class Handler(BaseHTTPRequestHandler):
    server_version = "hope-ensemble-page"

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
            raise jobmod.Refused("that is %.0f MB, and %d MB is the most the page accepts in one "
                                 "request" % (length / 1e6, BODY_CAP // 1024 // 1024))
        return json.loads(self.rfile.read(length) or b"{}")

    def _static(self, name):
        """A file of the page's own, the design page's (the shell), or the folding page's
        (the vendored viewer)."""
        for root in (WEB, SHARED, VENDOR):
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
            extra["Content-Disposition"] = 'attachment; filename="%s"' % os.path.basename(path)
        with open(path, "rb") as handle:
            self._send(200, kind, handle.read(), extra)

    def do_GET(self):                                             # noqa: N802
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path in ("/", "/index.html"):
            return self._send(200, "text/html; charset=utf-8", index_page().encode("utf-8"))
        # reportkit, the lab's report module: the one copy in this checkout, served by name so
        # that no path arrives from the browser at all.
        if url.path in ("/static/reportkit.js", "/static/reportkit.css"):
            body, kind = rk.static_file(os.path.basename(url.path))
            if body is None:
                return self._send(404, "text/plain", b"this checkout has no reportkit")
            return self._send(200, kind, body, {"X-Content-Type-Options": "nosniff"})
        if url.path.startswith("/static/"):
            return self._static(url.path[len("/static/"):])
        if url.path == "/report":
            if not self._allowed(query):
                return self._send(403, "text/plain",
                                  b"open the report from the run's page, which carries the token")
            try:
                run = inside_roots(query.get("rundir", query.get("path", [""]))[0])
            except jobmod.Refused as refusal:
                return self._send(400, "text/plain", str(refusal).encode("utf-8"))
            return self._send(200, "text/html; charset=utf-8", reportmod.page_for(
                S.token, run, S.tool,
                autoprint=query.get("print", [""])[0] == "1").encode("utf-8"))
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
            if url.path == "/api/report":
                return self._json(api_report(query))
            if url.path == "/api/comparisons":
                return self._json(api_comparisons(query))
            if url.path == "/api/download":
                return self._download(query)
            if url.path == "/api/roots":
                return self._json(browsemod.roots(S.roots))
            # The structure browser (/static/picker.js): where it offers to start, then one
            # folder at a time.
            if url.path == "/api/places":
                return self._json(pickermod.places(S.tool, S.roots))
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
            if url.path == "/api/structure":
                # A structure read here, on the login node: the file stays in the page's own
                # folder and the run copies it in when it is submitted, so what was shown is
                # what is sampled and what the samples are measured against.
                said = []
                got = submitmod.read_structure(
                    body, stagedmod.store(S.roots),
                    say=lambda *what: said.append(" ".join(str(w) for w in what)))
                return self._json(dict(got, said=said))
            if url.path == "/api/check":
                return self._json(submitmod.check(S.tool, body, S.roots, S.site, S.install))
            if url.path == "/api/told":
                checked = submitmod.check(S.tool, body, S.roots, S.site, S.install)
                return self._json(submitmod.told(S.tool, checked.get("sequence") or "",
                                                 checked.get("name") or "run",
                                                 checked.get("asked") or {}, S.root,
                                                 site=S.site))
            if url.path == "/api/submit":
                return self._json(api_submit(body))
            if url.path == "/api/compare":
                return self._json(api_compare(body))
            if url.path == "/api/copy":
                return self._json(api_copy(body))
            if url.path == "/api/mkdir":
                return self._json(browsemod.make_dir(body.get("parent", ""),
                                                     body.get("name", "")))
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
    # A cluster whose login nodes reach the internet only through a proxy says so in its profile;
    # the alignment is fetched from here, so this process goes through it too.
    from ..setup import profile as profilemod
    profilemod.use_proxy()
    S.install = install or jobmod.INSTALLS[tool]
    # The install is told to the job as well, so a copy somewhere else is the one that runs, and
    # the folder holding it is where the weights and the caches are read from.
    jobmod.INSTALLS[tool] = S.install
    S.root = imagesmod.root_of(S.install)
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
        prog="python -m hope_labs.ensemble.serve",
        description="Serve the ensemble page.")
    parser.add_argument("tool", nargs="?", default=SERVES[0], choices=list(SERVES),
                        help="which tool this page is for")
    parser.add_argument("--port", type=int, default=0,
                        help="port on this login node; 0 picks a free one")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address: keep it local and tunnel to it")
    parser.add_argument("--runs", action="append", default=None,
                        help="a run folder to read and write (repeatable; the default is the "
                             "tool's own under your scratch)")
    parser.add_argument("--install", default="",
                        help="the tool's image, if not the lab's own copy")
    parser.add_argument("--site", default="",
                        help="the cluster's key, when its own name does not say (grace, aces)")
    parser.add_argument("--token", default=None,
                        help="a fixed token (the default is a new one each start)")
    parser.add_argument("--dry-run", action="store_true",
                        help="write the run folder and the job script, fetch no alignment and "
                             "queue nothing")
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
