"""Looking inside a run folder, and getting what is in it onto this computer.

By Aadhil Haq

The Results view could say where a run was and start the next tool on it, and that was all: a
person who wanted to read a table, look at a structure or send a figure to somebody had to open a
terminal and scp it. So this is the floor under the tools' own pages - whatever a tool does not
show, it can at least be listed and fetched.

Everything goes over the connection the launcher already has. A file is streamed from the cluster
to the browser on this computer, so the browser's own download machinery decides where it lands
and nothing is written by hand; a folder is zipped on the cluster first, because thousands of
small files over SFTP is slower than one large one by an order of magnitude.

Any absolute path may be asked for, and the cluster's own permissions are the gate: the
connection is signed in as the person, so a folder they cannot read in a terminal cannot be read
here either. A relative path, or one that climbs with .., is refused before the cluster is asked.

The structure viewer's surfaces are read from the same files, so they pass the same gate
(fetchable), and they are computed where the files are: MSMS runs on the cluster, through
hope_labs/msms.py in the HOPE Labs checkout there, and only its answer comes down the connection.
"""
import json
import os
import posixpath
import re
import shlex
import stat
import time

#: Chunks the size SFTP is happiest with. Smaller spends the round trip too often; larger holds
#: more of a large file in memory than there is reason to.
CHUNK = 1 << 18

#: A folder with more in it than this is listed to here and said to be longer, because a run with
#: forty thousand poses in it would otherwise be sent to the page in full.
MOST_ENTRIES = 500

#: Over this, a download is refused unless it is asked for again with over=1. A run folder of a
#: hundred gigabytes down a home connection is not a thing to start by accident.
WARN_BYTES = 512 * 1024 * 1024


class Refused(Exception):
    """Something the page asked for that will not be done, with the reason a person reads."""


#: Whether any absolute path on the cluster may be read, the cluster's own permissions being the
#: only gate, rather than only the folders roots() names. A folder the account cannot read in a
#: terminal stays unreadable either way: SFTP is signed in as the same account.
ANY_PATH = True

#: The one line the file browser shows at the top of what it may read, where Up stops.
TOP = "Up stops here: your scratch, your home and the group's folders."


def roots(hub):
    """The folders a path may be inside. The person's own, and the group's."""
    out = [hub.scratch]
    labs = getattr(hub, "labs", "")
    if labs:
        out.append(posixpath.dirname(labs.rstrip("/")))
    if hub.user:
        out += ["/scratch/user/%s" % hub.user, "/home/%s" % hub.user]
    return [posixpath.normpath(p) for p in out if p]


def inside(hub, path):
    """`path` made absolute and checked. Raises Refused with the reason if it is not ours."""
    asked = str(path or "").strip()
    said = posixpath.normpath(asked)
    if not said or not said.startswith("/"):
        raise Refused("that is not a path on the cluster")
    if ".." in said.split("/") or (ANY_PATH and ".." in asked.split("/")):
        raise Refused("that path climbs out of itself")
    if ANY_PATH:
        return said
    for root in roots(hub):
        if said == root or said.startswith(root + "/"):
            return said
    raise Refused("%s is outside the folders this launcher reads: %s"
                  % (said, ", ".join(roots(hub))))


def _sftp(hub):
    import paramiko
    return paramiko.SFTPClient.from_transport(hub.t)


def may_read(hub, path):
    """Whether `path` is one this launcher reads at all, before the cluster is asked."""
    try:
        inside(hub, path)
        return True
    except Refused:
        return False


def listing(hub, path):
    """What is in a folder: directories first, then files, each with its size and when it changed.

    A run folder is read far more often than it is written, so nothing here is cached: a person
    looking at a run that is still going wants what is there now. No path is the person's own
    scratch, which is where the file browser opens when it is not opened on a run.

    "up" is the folder above, when this launcher would read it, and "" at the top of what it
    reads, where "top" says so in one line; at / there is no folder above and nothing to say.
    """
    where = inside(hub, path if str(path or "").strip() else hub.scratch)
    sftp = _sftp(hub)
    try:
        try:
            rows = sftp.listdir_attr(where)
        except IOError as why:
            raise Refused("%s could not be read: %s" % (where, why))
        out = []
        for row in rows:
            isdir = stat.S_ISDIR(row.st_mode or 0)
            out.append({"name": row.filename, "dir": isdir,
                        "size": 0 if isdir else int(row.st_size or 0),
                        "when": int(row.st_mtime or 0)})
        # Folders first, then files, and a hidden name after a plain one in each: a person's
        # scratch holds a dozen dot-folders of caches and settings, and sorted by name alone
        # they filled the first screen of the browser - and, at 500 entries, the whole of what
        # it was sent - with folders nobody opens on purpose.
        out.sort(key=lambda r: (not r["dir"], r["name"].startswith("."), r["name"].lower()))
        more = max(0, len(out) - MOST_ENTRIES)
        above = posixpath.dirname(where) if where != "/" else ""
        up = above if above and may_read(hub, above) else ""
        return {"path": where, "up": up, "top": "" if up or not above else TOP,
                "entries": out[:MOST_ENTRIES], "more": more}
    finally:
        try:
            sftp.close()
        except Exception:                                       # noqa: BLE001
            pass


def size_of(hub, path):
    """How big one file is, or -1 for a folder. Used to warn before a long download."""
    where = inside(hub, path)
    sftp = _sftp(hub)
    try:
        got = sftp.stat(where)
        return -1 if stat.S_ISDIR(got.st_mode or 0) else int(got.st_size or 0)
    except IOError as why:
        raise Refused("%s could not be read: %s" % (where, why))
    finally:
        try:
            sftp.close()
        except Exception:                                       # noqa: BLE001
            pass


def fetchable(hub, path, over=False):
    """`path` checked as a download is checked, returning its size: inside the folders this
    launcher reads, a file rather than a folder, and no larger than WARN_BYTES unless `over` says
    the size is meant. Raises Refused with the reason a person reads.

    One function for every route that reads a file, so the download and the viewer's surface
    cannot come to refuse different things.
    """
    size = size_of(hub, path)
    if size < 0:
        raise Refused("that is a folder; ask for it as a zip")
    if size > WARN_BYTES and not over:
        raise Refused("%s is %.1f GB. Ask again with over=1 if that is meant."
                      % (posixpath.basename(str(path)), size / 1e9))
    return size


def chunks(hub, path):
    """Yield a file from the cluster, a piece at a time. The caller writes them out."""
    where = inside(hub, path)
    sftp = _sftp(hub)
    try:
        with sftp.open(where, "rb") as handle:
            handle.prefetch()
            while True:
                piece = handle.read(CHUNK)
                if not piece:
                    return
                yield piece
    except IOError as why:
        raise Refused("%s could not be read: %s" % (where, why))
    finally:
        try:
            sftp.close()
        except Exception:                                       # noqa: BLE001
            pass


def zipped(hub, path, say=None):
    """Zip a folder on the cluster. Returns (path of the zip, its size, what to delete after).

    Made on the cluster rather than here because the alternative is one SFTP round trip per file,
    and a finished run holds thousands. It lands in the person's own scratch, never in a home
    directory, and the caller is expected to remove it once it has been sent.
    """
    from . import tunnel
    where = inside(hub, path)
    say = say or (lambda *a: None)
    keep = "%s/.hope_labs_zips" % (hub.scratch or "/tmp")
    name = "%s_%s.zip" % (posixpath.basename(where) or "run", time.strftime("%Y%m%d-%H%M%S"))
    out = "%s/%s" % (keep, name)
    line = ("mkdir -p %s && cd %s && zip -rq %s %s && stat -c %%s %s"
            % (_q(keep), _q(posixpath.dirname(where)), _q(out), _q(posixpath.basename(where)),
               _q(out)))
    say("zipping %s on the cluster" % where)
    status, text, err = tunnel.run(hub.t, line, timeout=900)
    digits = [l.strip() for l in (text or "").splitlines() if l.strip().isdigit()]
    if status != 0 or not digits:
        raise Refused("%s could not be zipped: %s"
                      % (where, ((err or text) or "").strip()[:300] or "zip said nothing"))
    return out, int(digits[-1]), out


def forget(hub, path):
    """Remove a zip this module made. Quiet: a leftover in scratch is not worth an error."""
    from . import tunnel
    if not path or "/.hope_labs_zips/" not in path:
        return
    try:
        tunnel.run(hub.t, "rm -f %s" % _q(path), timeout=30)
    except Exception:                                           # noqa: BLE001
        pass


#: The tools whose installers put the ADCP 1.1 suite, and the MSMS library in it, somewhere
#: this launcher can name. None of them is installed here, so the suite is found by
#: $ADCPSUITE_HOME, by $MSMS_EXE or by an msms on the PATH instead (hope_labs/msms.py), and
#: where there is none the viewer draws its own surface.
MSMS_INSTALLS = ()

#: How long the cluster may take over one surface, start to finish: a Python starting on a login
#: node, the file read, MSMS itself (at most surface.TIMEOUT) and the answer printed.
SURFACE_SECONDS = 120


def surface_line(hub, path):
    """The line that computes one surface on the cluster, from the HOPE Labs checkout there.

    It is handed the installs the ADCP 1.1 suite may be beside, as this launcher knows them,
    and the checkout itself, whose folder is where the suite would sit. hope_labs/msms.py says
    where it looks in each.
    """
    from . import tools as catalog
    labs = (getattr(hub, "labs", "") or catalog.LABS_INSTALL).rstrip("/")
    installs = []
    for key in MSMS_INSTALLS:
        tool = catalog.BY_KEY.get(key)
        if tool is not None:
            installs.append(hub.install_of(tool))
    installs.append(labs)
    return ('cd %s || exit 1; %sPYTHONPATH=%s exec timeout %d "$PY" -m hope_labs.msms %s'
            % (_q(labs), catalog.python_search("HOPELABS_PYTHON", "the surfaces"), _q(labs),
               SURFACE_SECONDS, " ".join(_q(p) for p in [path] + installs)))


def read_surface(status, out, err):
    """The answer hope_labs/msms.py printed, or a fallback saying why there is none."""
    text = out or ""
    try:
        got = json.loads(text[text.index("{"):text.rindex("}") + 1])
        if isinstance(got, dict) and got.get("source"):
            return got
    except ValueError:
        pass
    lines = [l.strip() for l in ((err or "").strip() or text).splitlines() if l.strip()]
    said = lines[-1][:240] if lines else ""
    if "No module named" in said:
        why = "HOPE Labs on the cluster is older than its MSMS surfaces; updating it brings them"
    elif status == 124:
        why = "the cluster took longer than %d s over it" % SURFACE_SECONDS
    else:
        why = "the cluster could not compute it (%s)" % (said or "it said nothing")
    return {"source": "fallback", "why": why}


def surface(hub, path, over=False):
    """The molecular surface of one structure file, for the viewer: MSMS's, computed on the
    cluster, or {"source": "fallback", "why": ...} when there is none to be had, which the page
    answers with a surface of the viewer's own.

    The file is gated exactly as a download of it is (fetchable), since it is the same file being
    read: a path outside the folders this launcher reads, a folder, or a file larger than a
    download would be without over=1 raises Refused, and the page shows the reason.
    """
    from . import tunnel
    fetchable(hub, path, over)
    where = inside(hub, path)
    if getattr(hub, "labs_found", None) is False:
        # MSMS is run from the HOPE Labs checkout on the cluster, so with none there the line
        # could only fail, and its failure named the folder it was looked for in: in the public
        # edition, a placeholder no cluster has.
        return {"source": "fallback",
                "why": "HOPE Labs is not set up on this cluster yet, so there is nothing there "
                       "to compute it with"}
    try:
        status, out, err = tunnel.run(hub.t, surface_line(hub, where),
                                      timeout=SURFACE_SECONDS + 30)
    except Exception as why:                                    # noqa: BLE001
        return {"source": "fallback",
                "why": "the cluster could not be asked for it (%s)" % str(why)[:200]}
    return read_surface(status, out, err)


def _q(text):
    return shlex.quote(str(text))


# ------------------------------------------------------------------ the structure browser
# The dialog a page opens to choose a structure (hope_labs/pages/web/picker.js, carried in the
# launcher's app.js) asks its server for two things: where it may start, and one folder at a
# time. On a page running on the cluster those are hope_labs/picker.py and folding/browse.py,
# which read the file system they are on. The launcher is not on the cluster, so the same two
# answers are put together here, out of the connection it already holds.

#: What the browser offers as a structure file.
STRUCTURES = (".pdb", ".ent", ".cif", ".mmcif", ".pdbqt")

#: What marks a folder as a run of ours, and so as one with a structure worth offering whole.
RUN_MARKERS = ("submission.json", "spec.json")

#: Where a file given from this computer lands on the cluster. Under the person's own scratch,
#: never in a home folder, and named so that it is obvious later what put it there.
UPLOADS = "hope_labs_given"

#: The most a file given from this computer may be. A structure is kilobytes to a few megabytes;
#: anything of this size is a trajectory somebody reached for by mistake.
MOST_UPLOAD = 64 * 1024 * 1024


def _stat_many(sftp, paths):
    """Which of `paths` are folders that can be read. One SFTP session, one stat each."""
    out = set()
    for path in paths:
        try:
            if stat.S_ISDIR(sftp.stat(path).st_mode or 0):
                out.add(path)
        except (IOError, OSError):
            pass
    return out


def _whose(path, scratch, user):
    """Whose folder this is, in the words the browser puts after a tool's name.

    Several tools keep runs in more than one place - your own scratch and the group's, or an
    older layout in your home - and the browser listed every one of them as "<tool> runs". Two
    rows with the same name and different contents is a row nobody can choose between.
    """
    if scratch and (path == scratch or path.startswith(scratch.rstrip("/") + "/")):
        return "yours"
    if user and path.startswith("/home/%s" % user):
        return "in your home"
    for mark in ("/scratch/group/", "/group/", "/work/group/"):
        if path.startswith(mark):
            rest = path[len(mark):].split("/")[0]
            if rest:
                return "%s's" % rest
    return path


def _run_roots(hub):
    """Each tool's run folder on this cluster, newest layout first, as (label, path).

    A tool with one folder is "<tool> runs". A tool with more than one says whose each is, so
    the group's HOPE-MD runs and your own are two rows a person can tell apart.
    """
    from . import tools as catalog
    root = posixpath.dirname(str(getattr(hub, "labs", "") or "")).strip().rstrip("/")
    scratch = str(getattr(hub, "scratch", "") or "")
    user = str(getattr(hub, "user", "") or "")
    out = []
    for tool in catalog.TOOLS:
        here = []
        for folder in tool.runs:
            where = catalog.rebase(folder, root) if root else folder
            where = where.replace("$SCRATCH", scratch).replace("$USER", user)
            if "$" in where or not where.startswith("/"):
                continue
            here.append(posixpath.normpath(where))
        for where in here:
            label = "%s runs" % tool.name
            if len(here) > 1:
                label = "%s runs, %s" % (tool.name, _whose(where, scratch, user))
            out.append((label, where))
    return out


def places(hub):
    """Where the structure browser offers to start, in the order it offers them.

    The person's own scratch and home first, because that is where their own work is; then the
    run folders of the tools this cluster has, under a heading of their own; then the group
    folders this launcher reads. Only folders that are there and can be opened: a place that
    answers "no permission" when it is pressed should not have been offered.
    """
    scratch = str(getattr(hub, "scratch", "") or "")
    user = str(getattr(hub, "user", "") or "")
    wanted = []
    seen = set()

    def add(path, label, kind, section=""):
        path = posixpath.normpath(str(path or "").rstrip("/") or "/")
        if not path.startswith("/") or path in seen or not may_read(hub, path):
            return
        seen.add(path)
        wanted.append({"label": label, "path": path, "kind": kind, "section": section})

    add(scratch, "My scratch", "scratch")
    if user:
        add("/home/%s" % user, "Home", "home")
    for label, where in _run_roots(hub):
        add(where, label, "runs", "Tool runs")
    for root in roots(hub):
        if root.startswith(("/scratch/group/", "/group/", "/work/group/")):
            add(root, "%s group" % posixpath.basename(root), "group", "Groups")
    sftp = _sftp(hub)
    try:
        there = _stat_many(sftp, [one["path"] for one in wanted])
    finally:
        try:
            sftp.close()
        except Exception:                                       # noqa: BLE001
            pass
    return {"places": [one for one in wanted if one["path"] in there]}


def _run_in(hub, where, names):
    """{"name", "file", "structure"} for a run folder of ours holding a structure, else None.

    Only asked of a folder that carries the file a run is marked by, and answered in one command
    rather than a stat for each place a run might keep its structure: a round trip to a login
    node costs a moment, and the browser asks this of every folder somebody opens.
    """
    from . import tunnel
    if not any(mark in names for mark in RUN_MARKERS):
        return None
    pattern = r"\|".join(ext.lstrip(".") for ext in STRUCTURES)
    line = ("find %s -maxdepth 3 -type f -regextype posix-extended -iregex '.*\\.(%s)$' "
            "2>/dev/null | head -40" % (_q(where), pattern))
    try:
        status, out, _ = tunnel.run(hub.t, line, timeout=30)
    except Exception:                                           # noqa: BLE001
        return None
    found = [one.strip() for one in str(out or "").splitlines() if one.strip()]
    if status != 0 or not found:
        return None
    # The structure a run was given, before anything was built from it, reads as a sequence; a
    # prepared one carries caps and protonation states in its residue names.
    def rank(path):
        rest = path[len(where):].lstrip("/").lower()
        for n, mark in enumerate(("input/original", "inputs/given", "input/complex",
                                  "inputs/target", "inputs/receptor")):
            if rest.startswith(mark):
                return (n, rest)
        return (len(STRUCTURES) + 1, rest)
    best = sorted(found, key=rank)[0]
    return {"name": posixpath.basename(where), "structure": best,
            "file": best[len(where):].lstrip("/")}


def browse(hub, path, want="structure"):
    """One folder, as the structure browser reads it: where it is, the folder above, its folders,
    and the files of the kind asked for. The same shape hope_labs/folding/browse.py answers with,
    so the dialog is the same dialog on the launcher and on a page running on the cluster.
    """
    got = listing(hub, path)
    where = got["path"]
    names = {one["name"] for one in got["entries"]}
    join = (where.rstrip("/") or "") + "/"
    dirs, files = [], []
    for one in got["entries"]:
        if one["dir"]:
            dirs.append({"name": one["name"], "path": join + one["name"]})
        elif want != "structure" or one["name"].lower().endswith(STRUCTURES):
            files.append({"name": one["name"], "path": join + one["name"], "size": one["size"]})
    out = {"path": where, "parent": got["up"], "dirs": dirs, "files": files,
           "truncated": bool(got["more"]), "most": MOST_ENTRIES}
    if got["top"]:
        out["top"] = got["top"]
    run = _run_in(hub, where, names)
    if run:
        out["run"] = run
    return out


def put(hub, name, data):
    """A file from this computer, written into the person's own scratch. Returns its path.

    The browser's "This computer" is the only way in that does not start on the cluster, and this
    is the whole of it: the bytes the page read are written where the person's own work goes,
    under a folder named for what put them there, and the path is handed back for the page to go
    on with exactly as if it had been chosen on the cluster.
    """
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", posixpath.basename(str(name or "")).strip())
    if not safe or safe.startswith("."):
        raise Refused("that file has no name this can write")
    if not safe.lower().endswith(STRUCTURES):
        raise Refused("%s is not a structure file (%s)" % (safe, ", ".join(STRUCTURES)))
    if len(data) > MOST_UPLOAD:
        raise Refused("%s is %.0f MB; this takes structures, not trajectories"
                      % (safe, len(data) / 1e6))
    if not data:
        raise Refused("%s is empty" % safe)
    scratch = str(getattr(hub, "scratch", "") or "")
    if not scratch:
        raise Refused("this launcher does not know where your scratch is on this cluster")
    folder = inside(hub, posixpath.join(scratch, UPLOADS))
    where = posixpath.join(folder, safe)
    sftp = _sftp(hub)
    try:
        try:
            sftp.stat(folder)
        except IOError:
            sftp.mkdir(folder)
        with sftp.open(where, "wb") as handle:
            handle.write(data)
    except IOError as why:
        raise Refused("%s could not be written to %s: %s" % (safe, folder, why))
    finally:
        try:
            sftp.close()
        except Exception:                                       # noqa: BLE001
            pass
    return {"path": where, "name": safe, "size": len(data)}
