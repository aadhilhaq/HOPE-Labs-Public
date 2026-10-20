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
TOP = "Up stops here: HOPE Labs reads your scratch, your home and the group's folders."


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
        out.sort(key=lambda r: (not r["dir"], r["name"].lower()))
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


#: The tools whose installers put the ADCP 1.1 suite, and the MSMS library in it, somewhere this
#: launcher can name: ADCP docking's install.sh, which also writes ADCPSUITE_HOME into its
#: activate.sh.
MSMS_INSTALLS = ("adcp",)

#: How long the cluster may take over one surface, start to finish: a Python starting on a login
#: node, the file read, MSMS itself (at most surface.TIMEOUT) and the answer printed.
SURFACE_SECONDS = 120


def surface_line(hub, path):
    """The line that computes one surface on the cluster, from the HOPE Labs checkout there.

    It is handed the installs the ADCP 1.1 suite may be beside, as this launcher knows them (a
    person's own copy wins over the lab's), and the checkout itself, whose folder is where Set up
    has ADCP docking's installer put the suite. hope_labs/msms.py says where it looks in each.
    """
    from . import tools as catalogue
    labs = (getattr(hub, "labs", "") or catalogue.LABS_INSTALL).rstrip("/")
    installs = []
    for key in MSMS_INSTALLS:
        tool = catalogue.BY_KEY.get(key)
        if tool is not None:
            installs.append(hub.install_of(tool))
    installs.append(labs)
    return ('cd %s || exit 1; %sPYTHONPATH=%s exec timeout %d "$PY" -m hope_labs.msms %s'
            % (_q(labs), catalogue.python_search("HOPELABS_PYTHON", "the surfaces"), _q(labs),
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
    try:
        status, out, err = tunnel.run(hub.t, surface_line(hub, where),
                                      timeout=SURFACE_SECONDS + 30)
    except Exception as why:                                    # noqa: BLE001
        return {"source": "fallback",
                "why": "the cluster could not be asked for it (%s)" % str(why)[:200]}
    return read_surface(status, out, err)


def _q(text):
    return shlex.quote(str(text))
