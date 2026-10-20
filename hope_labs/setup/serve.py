"""The Set up page: what this cluster has, which tools are installed here, and Install.

By Aadhil Haq

One process, standard library only, started on a login node by the launcher the way every page
of ours is started (hope_labs/tools.py, SETUP), and printing the same line when it is ready:

    python -m hope_labs.setup.serve --host 127.0.0.1 --port 0

It serves three things and does very little itself. The cluster's profile (profile.py) it reads,
asks again and saves. The installs it starts as processes of their own (engine.start), which go
on when this page is closed, and it reads back their state and their logs. The tool list is
hope_labs/tools.py's, with each tool's folder moved under this HOPE Labs folder exactly as the
launcher moves it, so a tool this page installs is the tool the launcher opens.

Anyone who can open the page can install into the HOPE Labs folder, if the cluster lets them
write there: the page asks the file system rather than keeping a list of its own.
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

from . import labs_root, profile_path
from . import engine
from . import profile as profilemod
from .recipes import RECIPES
from .. import docskit
from .. import tools as catalogue

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
SHARED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pages", "web")

#: The mark: a box with a tick in it. Drawn in the page's two colours, like every other mark.
MARK = ('<rect x="3.5" y="5" width="17" height="14.5" rx="2.5" fill="none" stroke="{a}" '
        'stroke-width="2"/><path d="M8 12.4l2.8 2.8L16.4 9.6" fill="none" stroke="{b}" '
        'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>'
        '<path d="M8 5V3.5h8V5" fill="none" stroke="{a}" stroke-width="2"/>')


class State:
    root = ""
    token = ""
    probing = threading.Lock()
    checking = {}                         # the GPU check this page started, while it waits


S = State()


def me():
    return os.environ.get("USER") or ""


def mark(a="#0d5c6b", b="#c2603a"):
    return MARK.format(a=a, b=b)


def index_page():
    from .. import docs as labsdocs
    with open(os.path.join(WEB, "index.html"), encoding="utf-8") as handle:
        page = handle.read()
    return (page.replace("/*__CREDITBAR_CSS__*/", docskit.CREDITBAR_CSS.strip())
                .replace("<!--__CREDITBAR__-->", labsdocs.creditbar())
                .replace("__MARK__", mark()))


# ------------------------------------------------------------------ what the page shows

def tool_rows():
    """Every tool, where it would be under this folder, and how far its install has got."""
    rows = []
    for tool in catalogue.TOOLS:
        where = catalogue.rebase(tool.install, S.root)
        recipe = RECIPES.get(tool.key)
        row = {"key": tool.key, "name": tool.name, "tagline": tool.tagline,
               "category": tool.category, "install": where, "recipe": None}
        if recipe:
            row["recipe"] = recipe.as_json(S.root)
            row["status"] = engine.status(S.root, recipe)
        else:
            row["status"] = {"state": engine.PRESENT if os.path.exists(where) else engine.ABSENT}
        rows.append(row)
    return rows


def api_hello():
    gb, files = engine.free_room(S.root)
    return {"root": S.root, "user": me(), "host": socket.gethostname(),
            "writable": os.access(S.root, os.W_OK),
            "profile": profilemod.load(S.root), "profile_path": profile_path(S.root),
            "free": {"gb": round(gb, 1) if gb is not None else None, "files": files},
            "tools": tool_rows(), "sources": [name for name, _ in profilemod.SOURCES],
            "gpu_check": dict(S.checking)}


def api_state():
    return {"tools": tool_rows(), "profile": profilemod.load(S.root),
            "gpu_check": dict(S.checking)}


def api_probe(body):
    """Ask the cluster again and write down what it says, keeping what the person chose.

    One at a time: a probe runs a dozen commands and two at once would only be the same answer
    twice as slowly.
    """
    if not S.probing.acquire(blocking=False):
        raise engine.Failed("the cluster is already being asked; this answer will be the same one")
    try:
        doc = profilemod.probe(account=str(body.get("account") or "").strip())
    except profilemod.NotSlurm as why:
        raise engine.Failed(str(why))
    finally:
        S.probing.release()
    old = profilemod.load(S.root)
    if old:
        for key in ("name", "address"):
            doc[key] = old.get(key) or doc.get(key, "")
        names = {p["name"]: p for p in doc["partitions"] if p.get("usable")}
        for key, value in (old.get("use") or {}).items():
            if value and (not key.endswith("_partition") or value in names):
                doc["use"][key] = value
        if old.get("gpu_check"):
            doc = profilemod.amend(doc, gpu_check=old["gpu_check"])
    doc["network"]["proxy"] = profilemod.proxy(S.root)
    profilemod.save(doc, S.root)
    return {"profile": doc}


def api_profile(body):
    doc = profilemod.load(S.root)
    if not doc:
        raise engine.Failed("this cluster has not been read yet: press Read the cluster first")
    try:
        doc = profilemod.amend(doc, use=body.get("use") or None, name=body.get("name"),
                               address=body.get("address"), proxy=body.get("proxy"))
    except ValueError as why:
        raise engine.Failed(str(why))
    profilemod.save(doc, S.root)
    return {"profile": doc}


def api_gpu_check(body):
    """Ask a GPU node what it has, again: the driver may have changed since it was last asked.

    Queued here and waited for in a thread of this page's, because it is a minute's job; an
    install that needs the answer and finds none asks for itself.
    """
    if S.checking.get("state") in ("queued", "running"):
        return {"gpu_check": dict(S.checking)}
    doc = profilemod.load(S.root)
    if not doc or not (doc.get("use") or {}).get("gpu_partition"):
        raise engine.Failed("choose a GPU partition first, under This cluster")
    folder = os.path.join(S.root, "setup", "gpucheck")
    os.makedirs(folder, exist_ok=True)
    script = profilemod.gpu_check_script(doc["use"]["gpu_partition"], folder,
                                         str(body.get("account") or "").strip())
    path = os.path.join(folder, "job.sbatch")
    with open(path, "w") as handle:
        handle.write(script)
    code, text = profilemod.run(["sbatch", "--parsable", path])
    job = text.strip().split(";")[0].strip()
    if code != 0 or not job.isdigit():
        raise engine.Failed("sbatch refused the GPU check: %s" % text.strip()[-300:])
    S.checking.clear()
    S.checking.update(job=job, state="queued", since=time.time())

    def wait():
        while True:
            state = engine.job_state(job)
            if state in engine.FINISHED or not state:
                break
            S.checking["state"] = "running" if state == "RUNNING" else "queued"
            time.sleep(5)
        out = os.path.join(folder, "gpucheck_%s.out" % job)
        try:
            got = profilemod.parse_gpu_check(open(out, errors="replace").read())
        except OSError:
            got = None
        if not got or not got.get("driver"):
            S.checking.update(state="failed", said="the job left no answer in %s" % out)
            return
        got.update(when=time.strftime("%Y-%m-%d %H:%M"), job=job)
        current = profilemod.load(S.root) or doc
        profilemod.save(profilemod.amend(current, gpu_check=got), S.root)
        S.checking.update(state="done")

    threading.Thread(target=wait, daemon=True).start()
    return {"gpu_check": dict(S.checking)}


def api_install(body):
    """Start the ticked tools' installs, each a process of its own that outlives this page."""
    asked = [str(k) for k in body.get("tools") or []]
    if not asked:
        raise engine.Failed("tick at least one tool")
    unknown = [k for k in asked if k not in RECIPES]
    if unknown:
        raise engine.Failed("Set up cannot install %s yet" % ", ".join(unknown))
    if not profilemod.load(S.root):
        raise engine.Failed("read the cluster first, under This cluster: an install needs to "
                            "know which partition to check itself on")
    started = {}
    for key in asked:
        started[key] = engine.start(S.root, key, "install",
                                    str(body.get("account") or "").strip())
    return {"started": started, "tools": tool_rows()}


def api_check(body):
    key = str(body.get("tool") or "")
    if key not in RECIPES:
        raise engine.Failed("there is no install of %r to check" % key)
    pid = engine.start(S.root, key, "check", str(body.get("account") or "").strip())
    return {"started": {key: pid}, "tools": tool_rows()}


def api_stop(body):
    key = str(body.get("tool") or "")
    if key not in RECIPES:
        raise engine.Failed("there is no install of %r to stop" % key)
    engine.stop(S.root, key)
    return {"tools": tool_rows()}


def api_log(query):
    key = query.get("tool", [""])[0]
    if key not in RECIPES:
        raise engine.Failed("there is no install of %r" % key)
    text, size = engine.log_tail(S.root, key, int(query.get("since", ["0"])[0] or 0))
    return {"text": text, "size": size}


# ------------------------------------------------------------------ serving

class Handler(BaseHTTPRequestHandler):
    server_version = "hope-setup-page"

    def log_message(self, fmt, *args):
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

    def _static(self, name):
        for root in (WEB, SHARED):
            path = os.path.realpath(os.path.join(root, name.lstrip("/")))
            if path.startswith(os.path.realpath(root) + os.sep) and os.path.isfile(path):
                kind = mimetypes.guess_type(path)[0] or "application/octet-stream"
                with open(path, "rb") as handle:
                    return self._send(200, kind, handle.read(),
                                      {"X-Content-Type-Options": "nosniff"})
        return self._send(404, "text/plain", b"not found")

    def _docs(self, path):
        from .. import docs as labsdocs
        status, kind, body, location = labsdocs.respond(path)
        if location:
            self.send_response(status)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        return self._send(status, kind, body, {"X-Content-Type-Options": "nosniff"})

    def do_GET(self):                                             # noqa: N802
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path in ("/", "/index.html"):
            return self._send(200, "text/html; charset=utf-8", index_page().encode("utf-8"))
        if url.path.startswith("/static/"):
            return self._static(url.path[len("/static/"):])
        if url.path == "/favicon.ico":
            svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">%s</svg>' % mark()
            return self._send(200, "image/svg+xml", svg.encode("utf-8"))
        if url.path == "/docs" or url.path.startswith("/docs/"):
            return self._docs(url.path)
        if not self._allowed(query):
            return self._send(403, "text/plain",
                              b"missing or wrong token: open the URL the server printed")
        try:
            if url.path == "/api/hello":
                return self._json(api_hello())
            if url.path == "/api/state":
                return self._json(api_state())
            if url.path == "/api/log":
                return self._json(api_log(query))
        except engine.Failed as refusal:
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
            length = min(int(self.headers.get("Content-Length") or 0), 1 << 20)
            body = json.loads(self.rfile.read(length) or b"{}")
            routes = {"/api/probe": api_probe, "/api/profile": api_profile,
                      "/api/gpucheck": api_gpu_check, "/api/install": api_install,
                      "/api/check": api_check, "/api/stop": api_stop}
            if url.path in routes:
                return self._json(routes[url.path](body))
        except engine.Failed as refusal:
            return self._json({"error": str(refusal)}, 400)
        except ValueError:
            return self._json({"error": "could not read that request"}, 400)
        except Exception:                                          # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": "the page hit an error; the server's terminal has the "
                                        "traceback"}, 500)
        self._send(404, "text/plain", b"not found")


def serve(host="127.0.0.1", port=0, root="", token=""):
    S.root = os.path.abspath(os.path.expanduser(root)) if root else labs_root()
    S.token = token or secrets.token_urlsafe(12)
    profilemod.use_proxy(S.root)
    httpd = ThreadingHTTPServer((host, port), Handler)
    return httpd, httpd.server_address[1], S.token


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m hope_labs.setup.serve",
                                     description="Serve the Set up page for this HOPE Labs folder.")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--root", default="", help="the HOPE Labs folder (default: the one "
                                                   "holding this checkout)")
    parser.add_argument("--token", default=None)
    args = parser.parse_args(argv)
    try:
        httpd, port, token = serve(args.host, args.port, args.root, args.token or "")
    except OSError as why:
        print("setup: the page could not take that port: %s" % why, file=sys.stderr)
        return 2
    print("setup: HOPE Labs folder %s" % S.root)
    # The line the launcher reads to know the page is up: hope_labs/tools.py, URL_LINE.
    print("open  http://127.0.0.1:%d/?t=%s" % (port, token), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nsetup: stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
