"""One session on the login node for every tool this launcher starts.

A login node allows ten sessions at once on one SSH connection (sshd's MaxSessions: Grace's
refuse the eleventh with "Connect failed"), and each tool used to hold one of its own for as long
as it ran, besides the ones the page opens for a moment to read the runs, list a folder or fetch
a file. With enough tools open none of that could be done any more, and the page, saying only
Connect failed, looked signed out.

So one session runs a small supervisor on the login node, the script below, and every tool is its
child, each on a terminal of its own as sshd gave it one: the same shell run the same way, the same
environment, and output that comes line by line. The supervisor goes on reading what every tool
prints and passes it on. That ends the other way a tool used to stop: once it had said it was
ready nothing read it again, and after 2 MB unread SSH passes nothing more on, so a page printing
a line for each request stalled on its next one, hours into a session.

When the connection ends - Sign out, a dropped network, a laptop gone to sleep - the supervisor
sees its input close and ends every tool as sshd ended each when it had its own session:
everything in the tool's own session, and nothing that started a session of its own on purpose.
An install by Set up is one of those, and carries on, as its page says.

A cluster whose login node cannot run the script gets the old way, a session for each tool, with
its output read all the same.
"""
from __future__ import annotations

import codecs
import collections
import itertools
import json
import shlex
import threading
import time

from . import tunnel

#: Seconds the launcher waits for the supervisor to say it is up. A cold login node takes a while
#: to start any Python, and this is only ever waited for once a connection.
HELLO_SECONDS = 45

#: How often the launcher tells the supervisor it is still there, and how long the supervisor
#: hears nothing before it takes the launcher for gone and ends the tools. A connection that dies
#: without closing (a laptop asleep behind a router that forgets it) is not seen by sshd for hours.
PING_SECONDS = 30
QUIET_SECONDS = 1800

#: What it writes when no Python could run it, which the launcher reads as "use the old way".
NO_PYTHON = "HOPE-LABS-SUPERVISOR-NO-PYTHON"

#: The supervisor, as run on the login node by `python3 -c`. A login node's own python3 can be 3.6,
#: so it is written for that and uses nothing outside the standard library.
SCRIPT = r'''
import codecs, errno, fcntl, json, os, pty, select, shlex, signal, struct, sys, termios, time

QUIET = float(sys.argv[1]) if len(sys.argv) > 1 else 1800.0
GRACE = 5.0              # seconds a tool is given to end before it is killed
HELD = 8 << 20           # bytes of a tool's output held for a launcher slow to read, then dropped
tools = {}
out = bytearray()
dropped = [0]
heard = [time.time()]
stopping = []


def emit(ev, output=False):
    if output and len(out) > HELD:
        dropped[0] += len(ev.get("data", ""))
        return
    out.extend((json.dumps(ev) + "\n").encode("utf-8"))


def flush():
    while out:
        try:
            n = os.write(1, bytes(out[:65536]))
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                return
            stopping.append("its output closed")          # the launcher is gone
            del out[:]
            return
        del out[:n]


def nonblocking(fd):
    fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) | os.O_NONBLOCK)


def start(tid, line):
    shell = os.environ.get("SHELL") or "/bin/sh"
    if not (shell.startswith("/") and os.access(shell, os.X_OK)):
        shell = "/bin/sh"
    # The environment sshd's own shell had once ~/.bashrc was read, which is the one this has:
    # bash reads ~/.bashrc again whenever SSH_CLIENT is set and SHLVL is low, and a second read
    # doubles what it adds to (MODULEPATH, MANPATH). So the variable is put back for the line
    # itself rather than given to the shell, and SHLVL is what sshd's shell had.
    env = dict(os.environ)
    again = ""
    for name in ("SSH_CLIENT", "SSH2_CLIENT"):
        if name in env:
            again += "export %s=%s; " % (name, shlex.quote(env.pop(name)))
    env["SHLVL"] = "0"
    env.setdefault("TERM", "vt100")
    pid, fd = pty.fork()
    if pid == 0:
        try:
            # What Python ignores, a tool should not: sshd's own children had the defaults.
            signal.signal(signal.SIGPIPE, signal.SIG_DFL)
            signal.signal(signal.SIGXFSZ, signal.SIG_DFL)
            try:
                env["SSH_TTY"] = os.ttyname(0)
                fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
            except OSError:
                pass
            os.execve(shell, [shell, "-c", again + line], env)
        except BaseException as e:
            try:
                os.write(2, ("cannot run %s: %s\r\n" % (shell, e)).encode("utf-8", "replace"))
            finally:
                os._exit(127)
    nonblocking(fd)
    tools[tid] = {"pid": pid, "fd": fd, "open": True, "status": None, "ending": 0.0,
                  "closing": 0.0, "dec": codecs.getincrementaldecoder("utf-8")("replace")}
    emit({"ev": "started", "id": tid, "pid": pid})


def drain(tid, t):
    """Pass on what the tool has printed. False once its terminal has closed."""
    for _ in range(16):
        try:
            data = os.read(t["fd"], 65536)
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                return True
            return False                                     # EIO: no one holds the terminal now
        if not data:
            return False
        text = t["dec"].decode(data)
        if text:
            emit({"ev": "out", "id": tid, "data": text}, output=True)
    return True


def in_session(sid):
    """This account's processes in session `sid`: the tool and whatever stayed in its session."""
    me, found = os.getuid(), []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            if os.stat("/proc/" + name).st_uid != me:
                continue
            with open("/proc/%s/stat" % name, "rb") as handle:
                stat = handle.read()
        except OSError:
            continue
        rest = stat[stat.rfind(b")") + 2:].split()
        if len(rest) > 3 and int(rest[3]) == sid and rest[0] != b"Z":
            found.append(int(name))
    return found


def signal_session(sid, sig):
    for pid in in_session(sid):
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def end(tid):
    t = tools.get(tid)
    if t is None or t["ending"]:
        return
    t["ending"] = time.time()
    signal_session(t["pid"], signal.SIGHUP)
    signal_session(t["pid"], signal.SIGTERM)


def reap():
    while True:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
        except OSError:
            return
        if pid == 0:
            return
        for t in tools.values():
            if t["pid"] == pid:
                if os.WIFSIGNALED(status):
                    t["status"] = -os.WTERMSIG(status)
                else:
                    t["status"] = os.WEXITSTATUS(status)


def tick(now):
    for tid in list(tools):
        t = tools[tid]
        if t["status"] is None:
            if t["ending"] and now - t["ending"] > GRACE:
                signal_session(t["pid"], signal.SIGKILL)
            continue
        if not t["closing"]:
            # Its own process has ended: what it printed last goes on, and what it left in its
            # session is ended as a hung-up terminal would end it.
            if t["open"]:
                drain(tid, t)
            t["closing"] = now
            signal_session(t["pid"], signal.SIGHUP)
            signal_session(t["pid"], signal.SIGTERM)
        left = in_session(t["pid"])
        if left and now - t["closing"] > GRACE:
            signal_session(t["pid"], signal.SIGKILL)
            left = []
        if not left:
            try:
                os.close(t["fd"])
            except OSError:
                pass
            emit({"ev": "exit", "id": tid, "status": t["status"]})
            del tools[tid]


def handle(line):
    try:
        msg = json.loads(line.decode("utf-8", "replace"))
    except ValueError:
        return
    op, tid = msg.get("op"), str(msg.get("id", ""))
    if op == "start" and tid and tid not in tools:
        try:
            start(tid, str(msg.get("line", "")))
        except OSError as e:
            emit({"ev": "exit", "id": tid, "status": None, "why": "could not be started: %s" % e})
    elif op == "stop":
        end(tid)
    elif op == "ping":
        emit({"ev": "pong"})
    elif op == "bye":
        stopping.append("asked to")


def main():
    def asked_to_stop(sig, frame):
        stopping.append("signal %d" % sig)
    for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, asked_to_stop)
    nonblocking(0)
    nonblocking(1)
    emit({"ev": "hello", "pid": os.getpid(), "python": sys.version.split()[0],
          "host": os.uname()[1]})
    pending = b""
    while not stopping:
        reading = [0] + [t["fd"] for t in tools.values() if t["open"]]
        try:
            ready, writable, _ = select.select(reading, [1] if out else [], [], 0.5)
        except (OSError, select.error):
            ready, writable = [], []
        if writable:
            flush()
        if 0 in ready:
            try:
                data = os.read(0, 65536)
            except OSError as e:
                data = b"" if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR) else None
            if data == b"":
                stopping.append("its input closed")         # the connection has gone
                break
            if data:
                heard[0] = time.time()
                pending += data
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    handle(line)
        for tid, t in list(tools.items()):
            if t["open"] and t["fd"] in ready and not drain(tid, t):
                t["open"] = False
        reap()
        now = time.time()
        tick(now)
        if dropped[0] and len(out) < HELD // 2:
            emit({"ev": "dropped", "bytes": dropped[0]})
            dropped[0] = 0
        if now - heard[0] > QUIET:
            stopping.append("nothing heard for %d s" % QUIET)
        flush()
    for tid in list(tools):
        end(tid)
    deadline = time.time() + GRACE + 2
    while tools and time.time() < deadline:
        reap()
        tick(time.time())
        time.sleep(0.2)
    for t in tools.values():
        signal_session(t["pid"], signal.SIGKILL)
    flush()
    os._exit(0)


main()
'''


def launch_line(quiet=QUIET_SECONDS):
    """The line that starts the supervisor on the login node: the first python3 the login shell
    finds, then the system's own, and a sentence when neither is there."""
    return ('for P in python3 /usr/bin/python3; do command -v "$P" >/dev/null 2>&1 && '
            'exec "$P" -u -c %s %d; done; echo %s; exit 3'
            % (shlex.quote(SCRIPT), int(quiet), NO_PYTHON))


class Unavailable(RuntimeError):
    """The login node cannot run the supervisor, so tools start the old way, a session each."""


class Proc:
    """A tool's process on the login node: what it printed, and whether it is still running.

    The first part of what it prints is kept whole, since that is where it says it is ready, and
    after it the most recent part, which is where it says why it stopped.
    """

    HEAD = 64 * 1024
    TAIL = 256 * 1024

    def __init__(self, name=""):
        self.name = name
        self.pid = None
        self.status = None
        self.why = ""
        self._head = ""
        self._tail = collections.deque()
        self._tail_len = 0
        self._cut = False
        self._lock = threading.Lock()
        self._done = threading.Event()

    def add(self, text):
        with self._lock:
            if len(self._head) < self.HEAD:
                room = self.HEAD - len(self._head)
                self._head += text[:room]
                text = text[room:]
            if text:
                self._tail.append(text)
                self._tail_len += len(text)
                while self._tail_len > self.TAIL and len(self._tail) > 1:
                    self._tail_len -= len(self._tail.popleft())
                    self._cut = True

    def text(self):
        with self._lock:
            return self._head + ("\n...\n" if self._cut else "") + "".join(self._tail)

    def finish(self, status=None, why=""):
        if self._done.is_set():
            return
        self.status, self.why = status, why or self.why
        self._done.set()

    def alive(self):
        return not self._done.is_set()

    def wait(self, timeout=None):
        return self._done.wait(timeout)

    def stop(self):
        raise NotImplementedError


class _Child(Proc):
    """A tool started by the supervisor."""

    def __init__(self, owner, tid, name=""):
        super().__init__(name)
        self.owner, self.id = owner, tid

    def stop(self):
        if self.alive():
            self.owner.send({"op": "stop", "id": self.id})


class Supervisor:
    """The launcher's end of the supervisor: one session, and every tool under it."""

    def __init__(self, transport, say=None):
        self.t = transport
        self.say = say or (lambda *a: None)
        self.ch = None
        self.info = {}
        self.dead = False
        self.why = ""
        self.noise = collections.deque(maxlen=20)
        self.children = {}
        self.last_pong = 0.0
        self._ids = itertools.count(1)
        self._send_lock = threading.Lock()
        self._hello = threading.Event()
        self._ended = threading.Event()

    # ---- starting and ending ---------------------------------------------
    def open(self, hello=HELLO_SECONDS, quiet=QUIET_SECONDS):
        """Start it. Raises Unavailable when the login node cannot run it, and whatever opening a
        session raises when the connection cannot give one."""
        ch = tunnel.open_session(self.t, timeout=30)
        ch.set_combine_stderr(True)
        ch.exec_command(launch_line(quiet))
        self.ch = ch
        threading.Thread(target=self._read, name="hope-labs-supervisor", daemon=True).start()
        if not self._hello.wait(hello) or self.dead:
            said = " / ".join(l for l in self.noise if l.strip())[-300:]
            self.close()
            if NO_PYTHON in said:
                raise Unavailable("the login node has no python3 to run it")
            raise Unavailable(self.why or ("it did not answer within %d s%s"
                                           % (hello, (": " + said) if said else "")))
        threading.Thread(target=self._ping, name="hope-labs-supervisor-ping", daemon=True).start()
        return self

    def close(self):
        """End it, and with it every tool it holds."""
        if self.ch is not None and not self.dead:
            self.send({"op": "bye"})
        self._ended.set()
        try:
            if self.ch is not None:
                # A moment for the bye to reach it: closing at once would end it all the same,
                # by its input closing, but the bye says it was meant.
                time.sleep(0.2)
                self.ch.close()
        except Exception:                                   # noqa: BLE001
            pass
        self._died("signed out")

    def start(self, line, name=""):
        """Start one tool. Returns its Proc."""
        if self.dead:
            raise RuntimeError("the session the tools run under has ended")
        tid = "t%d" % next(self._ids)
        child = _Child(self, tid, name)
        self.children[tid] = child
        if not self.send({"op": "start", "id": tid, "line": line}):
            child.finish(None, "the session the tools run under has ended")
        return child

    def send(self, msg):
        try:
            with self._send_lock:
                self.ch.sendall((json.dumps(msg) + "\n").encode("utf-8"))
            return True
        except Exception as why:                            # noqa: BLE001
            self.why = self.why or ("could not reach it: %s" % why)
            return False

    # ---- what it says ----------------------------------------------------
    def _read(self):
        buf = b""
        try:
            while True:
                data = self.ch.recv(65536)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._event(line)
        except Exception as why:                            # noqa: BLE001
            self.why = self.why or str(why)
        self._died(self.why or "its session ended")

    def _event(self, line):
        text = line.decode("utf-8", "replace").strip()
        try:
            ev = json.loads(text) if text.startswith("{") else None
        except ValueError:
            ev = None
        if not isinstance(ev, dict) or "ev" not in ev:
            # What the login shell printed on its way in, or the supervisor's own traceback.
            if text:
                self.noise.append(text)
            return
        kind, child = ev.get("ev"), self.children.get(str(ev.get("id", "")))
        if kind == "hello":
            self.info = ev
            self._hello.set()
        elif kind == "pong":
            self.last_pong = time.time()
        elif child is None:
            return
        elif kind == "started":
            child.pid = ev.get("pid")
        elif kind == "out":
            child.add(ev.get("data", ""))
        elif kind == "exit":
            child.finish(ev.get("status"), ev.get("why", ""))
            self.children.pop(child.id, None)

    def _died(self, why):
        if self.dead:
            return
        self.dead = True
        self.why = self.why or why
        self._hello.set()
        self._ended.set()
        for child in list(self.children.values()):
            child.finish(None, "the session it ran under ended (%s)" % self.why)
        self.children.clear()

    def _ping(self):
        while not self._ended.wait(PING_SECONDS):
            if self.dead or not self.send({"op": "ping"}):
                return


class ChannelProc(Proc):
    """A tool in a session of its own, the old way, for a login node that cannot run the
    supervisor. Its output is read for as long as it runs, so it cannot stall on a full window."""

    def __init__(self, transport, line, name=""):
        super().__init__(name)
        ch = tunnel.open_session(transport, timeout=60)
        ch.get_pty()                  # so the tool dies with this session, as it always did
        ch.exec_command(line)
        self.ch = ch
        threading.Thread(target=self._read, name="hope-labs-tool", daemon=True).start()

    def _read(self):
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                data = self.ch.recv(65536)
                if not data:
                    break
                self.add(dec.decode(data))
        except Exception:                                   # noqa: BLE001
            pass
        status = None
        try:
            if self.ch.exit_status_ready():
                status = self.ch.recv_exit_status()
        except Exception:                                   # noqa: BLE001
            pass
        self.finish(status)

    def stop(self):
        try:
            self.ch.close()
        except Exception:                                   # noqa: BLE001
            pass
