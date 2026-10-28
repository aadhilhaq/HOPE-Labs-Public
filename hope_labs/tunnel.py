"""Getting from a laptop to a running server on the cluster.

One transport serves every tool: the hub signs in once and each tool it starts gets its own
channel on that connection, which is why a person approves Duo once rather than five times.

Four steps, and the reason this is a module rather than a script is that the
awkward one is in the middle:

  1. connect and authenticate, which on Grace means a password and then Duo
  2. start the monitor on the node we landed on
  3. forward a local port to it through the same connection
  4. hand back a URL

Authentication is the part worth being careful about. The site asks for a
password and then a Duo challenge, and the Duo challenge is a free-text prompt
whose wording is decided by Duo and changes: it may offer a numbered menu, it
may ask for a passcode, it may say an approval has already been sent. So
nothing here tries to recognize or answer it. Every prompt the server sends is
passed to the caller with its text intact, and whatever the caller returns goes
back unaltered. The caller shows the text to the person sitting there.

No password is ever written down. It exists as an argument to a callback and
is not stored, logged, cached or offered back on the next connection, because
the only way to be sure a credential file cannot leak is not to have one.
"""

from __future__ import annotations
import os
import select
import socket
import socketserver
import threading

try:
    import paramiko
except ImportError:                                    # pragma: no cover
    paramiko = None


class TunnelError(RuntimeError):
    """Something went wrong that the person at the keyboard can act on."""


# --- authentication --------------------------------------------------------

KNOWN_HOSTS = os.path.expanduser("~/.hopelabs/known_hosts")

# Seconds allowed for the whole sign-in, Duo approval included. See _patience.
AUTH_PATIENCE = 120


def _host_id(host, port):
    """The name OpenSSH files a key under: bare for 22, [host]:port otherwise."""
    return host if int(port) == 22 else "[%s]:%d" % (host, int(port))


def check_host_key(t, host, port=22, log=None):
    """Refuse a server whose key has CHANGED. Record one never seen before.

    Without this there is no check of any kind: paramiko's Transport verifies
    nothing unless asked, so the password and the Duo response would go to
    whoever answered on port 22, and anyone able to answer for a login node's
    name - or sit between - would collect both.

    Trust on first use rather than a demand for a pre-seeded known_hosts,
    because refusing an unknown host would lock somebody out of their own
    cluster on the day they installed this, and a launcher that cannot connect
    gets replaced by one that does not check at all. A first sighting is
    recorded and reported; a key that later DIFFERS is refused, which is the
    case that means something.

    The system ~/.ssh/known_hosts is read, so a host already trusted by ssh is
    already trusted here. It is never written to: new keys go to
    ~/.hopelabs/known_hosts, so nothing this does can disturb the user's own SSH
    configuration.
    """
    try:
        key = t.get_remote_server_key()
    except Exception as e:
        raise TunnelError(f"{host} did not present a host key ({e}); refusing "
                          f"to send a password to it")
    name = _host_id(host, port)
    hk = paramiko.hostkeys.HostKeys()
    for path in (os.path.expanduser("~/.ssh/known_hosts"), KNOWN_HOSTS):
        try:
            if os.path.isfile(path):
                hk.load(path)
        except Exception:
            pass                  # an unreadable file is not a reason to stop
    known = hk.lookup(name) or {}
    seen = known.get(key.get_name()) if known else None
    if seen is not None:
        if seen.asbytes() != key.asbytes():
            raise TunnelError(
                f"the host key for {name} has CHANGED.\n"
                f"  was  {seen.get_base64()[:24]}...\n"
                f"  now  {key.get_base64()[:24]}...\n"
                "Nothing has been sent. This is what it looks like when "
                "something is answering in the cluster's place - and also "
                "what it looks like after a login node is rebuilt. Check with "
                "the people who run it before removing the old line from "
                f"~/.ssh/known_hosts or {KNOWN_HOSTS}.")
        return "known"
    # First sighting: record it, and say so rather than saying nothing.
    try:
        os.makedirs(os.path.dirname(KNOWN_HOSTS), exist_ok=True)
        with open(KNOWN_HOSTS, "a") as fh:
            fh.write("%s %s %s\n" % (name, key.get_name(), key.get_base64()))
        os.chmod(KNOWN_HOSTS, 0o600)
    except OSError:
        pass                      # remembering is a convenience, not the check
    if log:
        log(f"first connection to {name}; its key was recorded "
            f"({key.get_name()} {key.get_base64()[:16]}...). A change to it "
            f"later will be refused.")
    return "new"


def _key_files():
    """The private keys worth trying, in the places they normally live."""
    d = os.path.join(os.path.expanduser("~"), ".ssh")
    names = ("id_ed25519", "id_rsa", "id_ecdsa", "id_dsa")
    return [os.path.join(d, n) for n in names if os.path.isfile(os.path.join(d, n))]


def find_cert_key(name="id_aces_tamu", given=""):
    """Where the ACES key actually is, rather than where it ought to be.

    On the cluster the portal writes it to ~/.ssh/aces/. Downloading it puts
    it wherever the browser puts things, and a person then moves it somewhere
    sensible - which is ~/.ssh for most, ~/.ssh/aces for anyone copying the
    cluster's layout, and Downloads for anyone who has not moved it yet.
    Demanding one exact path turns a file that is present into a file that is
    missing, so all of them are looked at.

    Only a key with its certificate beside it counts: without the -cert.pub
    the key authenticates as nothing, so an ACES login would fail anyway and
    the reason would be the wrong one.
    """
    home = os.path.expanduser("~")
    seen, out = set(), []
    for cand in ([given] if given else []) + [
            os.path.join(home, ".ssh", name),
            os.path.join(home, ".ssh", "aces", name),
            os.path.join(home, "Downloads", name),
            os.path.join(home, "Downloads", "aces", name),
            os.path.join(home, "Desktop", name)]:
        if not cand:
            continue
        cand = os.path.normpath(os.path.expanduser(cand))
        if cand in seen:
            continue
        seen.add(cand)
        out.append(cand)
    withcert = [c for c in out
                if os.path.isfile(c) and os.path.isfile(c + "-cert.pub")]
    if withcert:
        return withcert[0], out
    keyonly = [c for c in out if os.path.isfile(c)]
    return (keyonly[0] if keyonly else None), out


def load_key(path):
    """A private key, with its certificate attached if one sits beside it.

    ACES issues an OpenSSH user certificate rather than accepting a password:
    the portal signs a key with its own CA and the pair is downloaded as
    id_aces_tamu and id_aces_tamu-cert.pub. The certificate is what the server
    actually checks, so loading the private key alone authenticates as nothing.
    """
    # normpath so a Windows user is shown C:\Users\you\.ssh\aces\... rather
    # than the half-and-half C:\Users\you/.ssh/aces/... that expanduser leaves
    # behind. Python does not care; somebody checking whether the file is where
    # the message says it is does.
    found, looked = find_cert_key(given=path)
    if found:
        path = found
    else:
        path = os.path.normpath(os.path.expanduser(path))
    if not os.path.isfile(path):
        where = "\n".join("    " + p for p in looked)
        raise TunnelError(
            f"No SSH key was found. Looked in:\n{where}\n\n"
            f"ACES has no password login: a certificate is the way in, and it "
            f"lasts 49 hours. To get one:\n\n"
            f"  1. open https://portal-aces.hprc.tamu.edu and sign in\n"
            f"  2. Utilities > sshca\n"
            f"  3. Open file app, go into .ssh / aces\n"
            f"  4. download BOTH id_aces_tamu and id_aces_tamu-cert.pub\n"
            f"  5. put BOTH files in "
            f"{os.path.join(os.path.expanduser('~'), '.ssh')}"
            f"\n\n"
            f"Both files, together, in the same folder. Then press Connect "
            f"again.")
    last = None
    for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            k = cls.from_private_key_file(path)
        except Exception as e:               # wrong type, or encrypted
            last = e
            continue
        cert = path + "-cert.pub"
        if not os.path.isfile(cert):
            raise TunnelError(
                f"{path} is there but {os.path.basename(cert)} is not, and "
                f"the certificate is the half the server checks - the key on "
                f"its own authenticates as nobody. Download both files from "
                f"the ACES portal, into the same folder.")
        if os.path.isfile(cert):
            try:
                k.load_certificate(cert)
            except Exception as e:
                raise TunnelError(
                    f"{cert} is not a certificate for {os.path.basename(path)} "
                    f"- {e}")
        return k
    raise TunnelError(f"{path} could not be read as a private key - {last}")


def load_any_key(path, ask=None):
    """A private key named on its own, as most clusters take one: with a certificate if one sits
    beside it, and without one if not. A key with a passphrase asks for it, through the same
    dialog the server's own questions come through."""
    path = os.path.normpath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise TunnelError(f"There is no SSH key at {path}. Leave the box empty to use the keys "
                          f"in {os.path.join(os.path.expanduser('~'), '.ssh')}, or name the file.")
    passphrase = None
    last = None
    for attempt in range(2):
        for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
            try:
                k = cls.from_private_key_file(path, password=passphrase)
            except paramiko.PasswordRequiredException as e:
                last = e
                break
            except Exception as e:                                # noqa: BLE001
                last = e
                continue
            if os.path.isfile(path + "-cert.pub"):
                try:
                    k.load_certificate(path + "-cert.pub")
                except Exception:                                 # noqa: BLE001
                    pass
            return k
        if isinstance(last, paramiko.PasswordRequiredException) and ask and attempt == 0:
            got = ask("", "", [(f"Passphrase for {os.path.basename(path)}: ", False)])
            passphrase = (got or [""])[0]
            continue
        break
    raise TunnelError(f"{path} could not be read as a private key - {last}")


def cert_expiry(path):
    """When the certificate beside this key stops working, as text, or None.

    Worth saying out loud: an ACES certificate lasts 49 hours, and the failure
    when it lapses is an authentication error that looks exactly like a wrong
    password.
    """
    cert = os.path.normpath(os.path.expanduser(path)) + "-cert.pub"
    if not os.path.isfile(cert):
        return None
    try:
        import subprocess
        r = subprocess.run(["ssh-keygen", "-L", "-f", cert],
                           capture_output=True, text=True, timeout=10)
        for line in r.stdout.splitlines():
            if "Valid:" in line:
                return line.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _handler(ask, log=None):
    """The callback paramiko hands every keyboard-interactive request to.

    One answer per prompt - and nothing at all when there are none.

    RFC 4256 section 3.3: a request carrying zero prompts is a MESSAGE to show,
    and the client "MUST NOT prompt the user" for it. Grace sends exactly that
    after a Duo push is approved, so asking would put up, on every successful
    login, a dialog with no question in it and a Continue button, arriving
    after the person has already approved on their phone.

    That is worse than an extra click. The handler runs on the transport's
    packet thread while auth_interactive() counts down auth_timeout on the
    other, so the seconds spent looking at an empty box come out of the SAME
    budget as the Duo approval - and the login fails outright for anyone who
    does not dismiss it immediately.

    The message still gets through; it goes to the log, where the rest of the
    server's words go.

    Nothing here reads the prompt text. Duo decides that wording and changes
    it, so a handler that recognized "password" in a hidden prompt would one
    day hand the account password to Duo. Every question goes to the person, in
    the server's own words.
    """
    def handler(title, instructions, prompts):
        if not prompts:
            if instructions and instructions.strip() and log:
                log(instructions.strip())
            return []
        return ask(title, instructions, [(p, echo) for p, echo in prompts])
    return handler


def _patience(t, timeout):
    """How long the client waits at each stage of the handshake.

    paramiko's own defaults are banner_timeout 15 and auth_timeout 30. The
    second one is the problem: it covers the WHOLE keyboard-interactive
    exchange, so those 30 seconds would have to cover reading Duo's menu,
    typing a choice, and then unlocking a phone and tapping Approve, and anyone
    who paused to find their phone would be told authentication had failed.

    120 seconds is not arbitrary - it is OpenSSH's own LoginGraceTime, so the
    client gives up at the same moment the server does instead of well before
    it.
    """
    t.banner_timeout = timeout
    t.handshake_timeout = timeout
    t.auth_timeout = AUTH_PATIENCE


def connect(host, user, ask, port=22, timeout=40, try_keys=True,
            jump=None, key=None, log=None):
    """An authenticated SSH transport, or a TunnelError explaining why not.

    ``ask(title, instructions, prompts)`` is called for every challenge the
    server sends. ``prompts`` is a list of ``(text, echo)`` pairs, echo being
    False for a password. It must return one answer per prompt.

    Keys are tried first when there are any, because a site that accepts a key
    usually skips Duo, and not being asked at all beats being asked well.

    ``jump`` is (host, port) to go through first, and ``key`` a private key to
    authenticate with at both hops. ACES needs both: its login nodes are not
    reachable from outside, and the way in is a certificate presented to
    aces-jump.hprc.tamu.edu on port 8822. It offers publickey and nothing else,
    so there is no password to fall back on.
    """
    if paramiko is None:
        # "pip install paramiko" is the whole answer on Windows and on a
        # cluster, and no answer at all on a Mac, where the python that comes
        # with the machine refuses to install anything into itself:
        #
        #     error: externally-managed-environment
        #
        # which reads as a broken pip rather than as a deliberate refusal. It
        # is the single most likely thing to stop somebody on a Mac before
        # they have started, so it is answered here rather than left to be
        # searched for.
        raise TunnelError(
            "paramiko is not installed. This is the laptop-side package:\n"
            "    pip install paramiko\n\n"
            "If that says 'externally-managed-environment', the Mac's own\n"
            "python is refusing to be installed into. Make a place of your\n"
            "own and use that:\n"
            "    python3 -m venv ~/hope-labs\n"
            "    ~/hope-labs/bin/pip install paramiko\n"
            "    ~/hope-labs/bin/python -m hope_labs\n\n"
            "If it fails while building 'cryptography', the wheel for your\n"
            "python is missing rather than the package: upgrade pip first\n"
            "with 'pip install --upgrade pip', which is usually enough.")

    # A gateway is ACES's, which takes a certificate and nothing else; a key named for any other
    # cluster is taken as it is, certificate or not.
    pkey = (load_key(key) if jump else load_any_key(key, ask)) if key else None

    if jump:
        # Through the gateway first. Its channel becomes the socket the second
        # connection is made over, which is what "ssh -J" does.
        jhost, jport = jump
        try:
            jsock = socket.create_connection((jhost, jport), timeout)
        except OSError as e:
            raise TunnelError(f"cannot reach the gateway {jhost}:{jport} - {e}")
        jt = paramiko.Transport(jsock)
        _patience(jt, timeout)
        # EVERY EXIT FROM HERE CLOSES WHAT IT OPENED. There are eight ways out
        # of this branch - the gateway not answering, a host key that will not
        # verify, no certificate, the certificate refused, the channel
        # refused, the login node not answering, its host key, and its
        # certificate. A launcher retrying a failed connect, which is routine
        # (a missed Duo push, a lapsed certificate, a changed host key), would
        # otherwise accumulate one socket and one live Transport thread per
        # attempt. close() is idempotent, so closing something twice does no
        # harm.
        t = None
        try:
            try:
                jt.start_client(timeout=timeout)
            except paramiko.SSHException as e:
                raise TunnelError(
                    f"{jhost} did not answer as an SSH server - {e}")
            check_host_key(jt, jhost, jport, log)
            if pkey is None:
                raise TunnelError(
                    f"{jhost} accepts a certificate and nothing else, and no "
                    f"key was given. Generate one in the ACES portal under "
                    f"Utilities > sshca, download id_aces_tamu and "
                    f"id_aces_tamu-cert.pub, and point the launcher at them.")
            try:
                jt.auth_publickey(user, pkey)
            except paramiko.AuthenticationException as e:
                when = cert_expiry(key) or ""
                raise TunnelError(
                    f"{user}@{jhost} did not accept the certificate - {e}. "
                    + (f"It says {when}. " if when else "")
                    + "An ACES certificate lasts 49 hours; generate a fresh "
                      "one in the portal under Utilities > sshca if this one "
                      "has lapsed.")
            try:
                sock = jt.open_channel("direct-tcpip", (host, port),
                                       ("127.0.0.1", 0))
            except Exception as e:
                raise TunnelError(
                    f"the gateway would not reach {host}:{port} - {e}")
            t = paramiko.Transport(sock)
            _patience(t, timeout)
            try:
                t.start_client(timeout=timeout)
            except paramiko.SSHException as e:
                raise TunnelError(
                    f"{host} did not answer through the gateway - {e}")
            check_host_key(t, host, port, log)
            try:
                t.auth_publickey(user, load_key(key))
            except paramiko.AuthenticationException as e:
                raise TunnelError(
                    f"{user}@{host} did not accept the certificate - {e}")
            t._jump = jt          # keep the gateway open beneath it
            return t
        except BaseException:
            # The inner transport first: it is layered on a channel of the
            # outer one, so closing the gateway first would leave it holding
            # a dead channel.
            for closeable in (t, jt, jsock):
                if closeable is None:
                    continue
                try:
                    closeable.close()
                except Exception:                              # noqa: BLE001
                    pass
            raise

    try:
        sock = socket.create_connection((host, port), timeout)
    except OSError as e:
        raise TunnelError(f"cannot reach {host}:{port} - {e}")

    t = paramiko.Transport(sock)
    _patience(t, timeout)
    try:
        t.start_client(timeout=timeout)
    except paramiko.SSHException as e:
        raise TunnelError(f"{host} did not answer as an SSH server - {e}")
    check_host_key(t, host, port, log)

    # auth_none is expected to fail; it is how the server is asked what it
    # will accept, so the right method is tried rather than guessed.
    try:
        t.auth_none(user)
        return t                                   # a server with no auth at all
    except paramiko.BadAuthenticationType as e:
        allowed = list(e.allowed_types)
    except paramiko.AuthenticationException:
        allowed = ["password", "keyboard-interactive"]

    # A key can be all a server wants, or the first of two: a site that asks for a key AND a
    # code answers a good key with "partial success" and the methods still to come, and the
    # transport is not signed in yet. So what comes back is read rather than assumed.
    if pkey is not None and "publickey" in allowed:
        try:
            rest = t.auth_publickey(user, pkey)
        except paramiko.AuthenticationException as e:
            t.close()
            raise TunnelError(f"{user}@{host} did not accept the key {key} - {e}")
        if t.is_authenticated():
            return t
        allowed = list(rest) or allowed
    if try_keys and "publickey" in allowed and not t.is_authenticated():
        for path in _key_files():
            try:
                rest = t.auth_publickey(user, paramiko.PKey.from_path(path))
            except Exception:
                continue                    # an unusable key is not an error yet
            if t.is_authenticated():
                return t
            if rest:
                allowed = list(rest)
                break

    handler = _handler(ask, log)

    last = None
    for _ in range(3):          # a site may challenge more than once in a row
        if t.is_authenticated():
            return t
        try:
            if "keyboard-interactive" in allowed:
                t.auth_interactive(user, handler)
            elif "password" in allowed:
                answers = ask("", "", [(f"{user}@{host}'s password: ", False)])
                t.auth_password(user, answers[0])
            else:
                raise TunnelError(
                    f"{host} accepts only {', '.join(allowed) or 'nothing'}, "
                    "which this cannot do.")
            if t.is_authenticated():
                return t
        except paramiko.BadAuthenticationType as e:
            allowed = list(e.allowed_types)
            last = e
        except paramiko.AuthenticationException as e:
            last = e
            break

    t.close()
    raise TunnelError(f"{user}@{host} was not accepted - {last or 'no reason given'}")


# --- running the monitor over there ----------------------------------------

def run(t, command, timeout=60):
    """Run a command and wait for it, as ``(exit status, stdout, stderr)``."""
    ch = t.open_session(timeout=timeout)
    ch.settimeout(timeout)
    ch.exec_command(command)
    out, err = b"", b""
    while True:
        if ch.recv_ready():
            out += ch.recv(65536)
        if ch.recv_stderr_ready():
            err += ch.recv_stderr(65536)
        if ch.exit_status_ready() and not ch.recv_ready() and not ch.recv_stderr_ready():
            break
    status = ch.recv_exit_status()
    ch.close()
    return status, out.decode(errors="replace"), err.decode(errors="replace")


def hostname(t):
    """The node actually landed on.

    The cluster's front door round-robins across login nodes, so which one this
    is cannot be known before connecting - and it is the node the monitor will
    be started on, so it is worth reporting.
    """
    try:
        got = run(t, "hostname -s")[1].strip()
        if got:
            return got
    except Exception:                                          # noqa: BLE001
        pass
    # Whenever that one exec fails, a failure message would otherwise say
    # "the monitor stopped on ?", which tells nobody anything. The address
    # connected to is not the node - the front door round-robins - but it
    # is true, and it is better than a question mark in a failure message.
    try:
        peer = t.getpeername()
        if peer and peer[0]:
            return "%s (could not read the node name)" % (peer[0],)
    except Exception:                                          # noqa: BLE001
        pass
    return "the cluster"


# --- forwarding a local port through the connection ------------------------

class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            ch = self.server.transport.open_channel(
                "direct-tcpip", (self.server.remote_host, self.server.remote_port),
                self.request.getpeername())
        except Exception:
            return                      # the far end is gone; the browser retries
        if ch is None:
            return
        try:
            while True:
                r, _, _ = select.select([self.request, ch], [], [], 1.0)
                if self.request in r:
                    d = self.request.recv(16384)
                    if not d:
                        break
                    ch.sendall(d)
                if ch in r:
                    d = ch.recv(16384)
                    if not d:
                        break
                    self.request.sendall(d)
        except OSError:
            pass                        # a closed browser tab is not an error
        finally:
            try:
                ch.close()
            except Exception:
                pass


class _Forwarder(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def forward(t, local_port, remote_port, remote_host="127.0.0.1",
            bind="127.0.0.1"):
    """Forward ``local_port`` to ``remote_host:remote_port`` over ``t``.

    Bound to the loopback address, so the tunnel is reachable from this laptop
    and not from the rest of whatever network it is on. Returns the server;
    call ``shutdown()`` to stop it.
    """
    srv = _Forwarder((bind, local_port), _Handler)
    srv.transport, srv.remote_host, srv.remote_port = t, remote_host, remote_port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def free_port():
    """A local port nothing is using, chosen by the operating system."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p
