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
        "feedback": FEEDBACK_TO,
    }


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
