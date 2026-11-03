"""What differs between one cluster and the next, as a table rather than in code.

A person should have to know their account and their password, not where the lab keeps five
installs. Each tool carries its own install path (tools.py); this table is only about getting on
to the cluster.
"""
from __future__ import annotations

import os
import re

FEEDBACK_TO = os.environ.get("HOPELABS_FEEDBACK_TO") or ""

OTHER = "Other (SLURM)"

SITES = {
    "Grace (TAMU)": {
        "host": "grace.hprc.tamu.edu",
        # The address is the cluster's own and does not change, so it is shown and not taken from
        # the box: a remembered address went on being used after the cluster was changed.
        "fixed": True,
        "auth": "password",
        "hint": "Your NetID.",
        "label": "NetID",
        # Shown in the empty box. People type the address they sign in to TAMU email with, and
        # SSH wants the part before the @.
        "placeholder": "e.g. jdoe  (your NetID only, without @tamu.edu)",
        "note": "Password and a Duo approval, once, however many tools you open.",
    },
    "ACES (TAMU)": {
        # The login nodes are not reachable from outside: the way in is a gateway that accepts a
        # certificate and nothing else, downloaded from the ACES portal and good for 49 hours.
        "host": "login.aces.hprc.tamu.edu",
        "jump": "aces-jump.hprc.tamu.edu:8822",
        "fixed": True,
        "key": "~/.ssh/id_aces_tamu",
        "jump_label": "Gateway (ACES)",
        "key_label": "Key with certificate (ACES)",
        "auth": "certificate",
        "hint": "Your ACCESS account (u.ab123456), not your NetID.",
        "label": "Account",
        "placeholder": "e.g. u.ab123456  (your ACCESS account, not your NetID)",
        "note": "ACES takes a certificate, not a password: get id_aces_tamu and id_aces_tamu-cert.pub "
                "from portal-aces.hprc.tamu.edu (Utilities > sshca), put both in ~/.ssh, and connect "
                "within 49 hours. Not every tool is installed there.",
    },
    OTHER: {
        "host": "",
        "auth": "password",
        "hint": "Whatever you log in with.",
        "label": "Username",
        "placeholder": "your username on that cluster",
        # Both rows shown and both optional: some clusters are reached through a gateway, and
        # some take a key and nothing else. A key in ~/.ssh is tried without being named.
        "optional": True,
        "jump_label": "Gateway, if there is one",
        "key_label": "SSH key, if not in ~/.ssh",
        "note": "Any cluster with Slurm. The first time you sign in to one, HOPE Labs offers to "
                "set itself up there, in a folder you choose, and then to install the tools.",
    },
}


def sites():
    """The names, with the real clusters first and Other last."""
    return [n for n in SITES if n != OTHER] + [OTHER]


def defaults_for(site_name, user=""):
    site = SITES.get(site_name) or {}
    return {
        "host": site.get("host", ""),
        "jump": site.get("jump", ""),
        "key": site.get("key", ""),
        "note": site.get("note", ""),
        "hint": site.get("hint", ""),
        "label": site.get("label", "Username"),
        "placeholder": site.get("placeholder", ""),
        "auth": site.get("auth", "password"),
        "optional": bool(site.get("optional")),
        "jump_label": site.get("jump_label", "Gateway"),
        "key_label": site.get("key_label", "SSH key"),
        "fixed": bool(site.get("fixed")),
        "feedback": FEEDBACK_TO,
    }


#: The boxes remembered for each cluster.
BOXES = ("user", "host", "jump", "key")


def remembered(cfg):
    """{cluster: the boxes last used for it}, from a settings file.

    Each cluster keeps its own, because the account is not the same on two clusters (a NetID on
    Grace, an ACCESS account on ACES) and one shared set of boxes carried Grace's address and
    NetID over to ACES. A file written before this kept one set, for the cluster it names, so
    that set is taken as that cluster's.
    """
    cfg = cfg or {}
    kept = cfg.get("sites") if isinstance(cfg.get("sites"), dict) else {}
    out = {name: {k: str(v.get(k) or "") for k in BOXES}
           for name, v in kept.items() if isinstance(v, dict)}
    site = cfg.get("site")
    if site in SITES and site not in out and any(cfg.get(k) for k in BOXES):
        out[site] = {k: str(cfg.get(k) or "") for k in BOXES}
    return out


def form_for(site_name, kept=None):
    """What the sign-in boxes hold for this cluster: {user, host, jump, key, fixed}.

    A cluster with a fixed address (Grace, ACES) always gets its own address and gateway,
    whatever was remembered; its account and key are what was used there last. Other gets back
    everything that was typed for it.
    """
    d = defaults_for(site_name)
    kept = kept or {}
    if d["fixed"]:
        host, jump = d["host"], d["jump"]
    else:
        host, jump = kept.get("host") or d["host"], kept.get("jump") or d["jump"]
    return {"user": kept.get("user") or "", "host": host, "jump": jump,
            "key": kept.get("key") or d["key"], "fixed": d["fixed"]}


# ---- the ACES certificate ----------------------------------------------------------------
# ACES signs in with an OpenSSH certificate from its portal that lasts 49 hours, and a lapsed one
# fails exactly like a wrong key. So the window says how long the one on this computer has left
# before anyone presses Sign in. Read here rather than by running ssh-keygen -L, which is not on
# every computer this launcher runs on.

def _field(blob, at):
    import struct
    n = struct.unpack(">I", blob[at:at + 4])[0]
    if at + 4 + n > len(blob):
        raise ValueError("the certificate ends early")
    return blob[at + 4:at + 4 + n], at + 4 + n


#: The public-key fields each certificate type carries before its serial number.
_KEY_FIELDS = (("sk-ecdsa-sha2-nistp256@openssh.com", 3), ("sk-ssh-ed25519@openssh.com", 2),
               ("ssh-ed25519", 1), ("ecdsa-sha2-", 2), ("ssh-rsa", 2), ("ssh-dss", 4))

#: valid_before of a certificate that never lapses.
FOREVER = 2 ** 64 - 1


def cert_window(key_path):
    """(valid from, valid until) of the certificate beside this key, as Unix times, or None
    when there is none or it cannot be read."""
    import base64
    import binascii
    import struct
    path = os.path.expanduser(key_path or "")
    try:
        with open(path + "-cert.pub") as fh:
            blob = base64.b64decode(fh.read().split()[1])
        kind, at = _field(blob, 0)
        base = kind.decode("ascii", "replace").replace("-cert-v01@openssh.com", "")
        fields = next((n for k, n in _KEY_FIELDS if base == k or base.startswith(k)), None)
        if fields is None:
            return None
        _nonce, at = _field(blob, at)
        for _ in range(fields):
            _part, at = _field(blob, at)
        at += 8 + 4                                   # serial, certificate type
        _key_id, at = _field(blob, at)
        _principals, at = _field(blob, at)
        return struct.unpack(">QQ", blob[at:at + 16])
    except (OSError, IndexError, ValueError, binascii.Error, struct.error):
        return None


def cert_says(key_path, now=None):
    """One line for the window about the certificate beside this key, or "" with none."""
    import time
    got = cert_window(key_path)
    if not got:
        return ""
    after, before = got
    if before == FOREVER:
        return "The certificate does not lapse."
    now = time.time() if now is None else now
    if now < after:
        return ("The certificate is not valid until %s: check this computer's clock."
                % time.strftime("%a %d %b %H:%M", time.localtime(after)))
    when = time.strftime("%a %d %b %H:%M", time.localtime(before))
    left = before - now
    if left <= 0:
        return ("The certificate lapsed on %s. Make a new one at portal-aces.hprc.tamu.edu "
                "(Utilities > sshca) and download both files into .ssh again." % when)
    span = ("%d hours" % (left // 3600)) if left >= 2 * 3600 else ("%d minutes" % (left // 60))
    return "The certificate is valid until %s, %s from now." % (when, span)


def clean_user(text):
    """The account as SSH wants it: no spaces, and no e-mail domain.

    The commonest mistake is typing the TAMU e-mail address, which SSH reads as a user called
    "jdoe@tamu.edu" and refuses in a way that looks like a wrong password. The part before the @
    is the NetID, so that is what is used.
    """
    text = (text or "").strip()
    return text.split("@", 1)[0].strip() if "@" in text else text


ACCOUNT_IN_PATH = re.compile(r"^/scratch/user/([^/]+)(/|$)")


def half_typed(path, user):
    """A saved path whose account segment is a strict prefix of the account now in the box was
    saved mid-keystroke, and is dropped rather than kept."""
    m = ACCOUNT_IN_PATH.match((path or "").strip())
    if not m or not user:
        return False
    seg = m.group(1)
    return seg != user and user.startswith(seg)


def parse_jump(text):
    """'host:port' -> (host, port); blank -> None."""
    text = (text or "").strip()
    if not text:
        return None
    host, _, port = text.partition(":")
    return (host, int(port) if port.strip().isdigit() else 22)


PORT_BASE = 12000
PORT_SPAN = 400


def suggested_port(user):
    seed = (user or "hope") + "|hope-labs"
    return PORT_BASE + (sum(ord(c) * (i + 1) for i, c in enumerate(seed)) % PORT_SPAN)
