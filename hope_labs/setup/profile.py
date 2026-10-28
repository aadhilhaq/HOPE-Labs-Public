"""What a cluster has, read off the cluster itself and written down once: cluster.json.

By Aadhil Haq

Every page that queues a job has to know three things about the cluster it is on: which
partition takes a GPU job, which card to ask for, and how long a job may run there. For Grace and
ACES those are tables in hope_labs/folding/sites.py, read off each cluster by hand. Anywhere else
they are read here, by asking the cluster, and kept in one file in the HOPE Labs folder that every
page reads and the person setting up can correct on the Set up page.

Three questions, asked in this order:

    sinfo                 the partitions, their time limits and the cards in each
    sbatch --test-only    whether this person may use a partition as itself, without queueing
                          anything. Grace answers a job sent to `special` or `vnc` with "would
                          start in partition gpu": both exist, and neither is somewhere to send
                          a batch job, so a partition is only usable when the answer names it
    a GPU check job       the driver and the cards as a GPU node reports them. The only way to
                          learn which build of torch the nodes can run, and the one question
                          here that waits in the queue

Nothing here decides for the person. The choices a page needs (a GPU partition, a card, a CPU
partition) are worked out as defaults and shown, and the file holds whatever was confirmed.

The output of sinfo, sbatch and the check job is parsed by small functions that take text, so the
tests can hand them what another cluster prints without being on it.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

from . import labs_root, profile_path

SCHEMA = 1

#: What is known about each kind of card, by the short name clusters tend to give it in their
#: gres: (memory in GB, compute capability). The memory is a first guess where a name can mean two
#: cards (an a100 is 40 or 80 GB, a v100 16 or 32); a name that says its size, as Slurm's own
#: autodetected names do (nvidia_a100-sxm4-80gb), wins over it, and the GPU check job's reading
#: wins over both.
CARDS = {
    "b200": (180, 10.0), "h200": (141, 9.0), "gh200": (96, 9.0), "h100": (80, 9.0),
    "h800": (80, 9.0), "a100": (40, 8.0), "a800": (80, 8.0), "l40s": (48, 8.9),
    "a40": (48, 8.6), "l40": (48, 8.9), "a6000": (48, 8.6), "rtx8000": (48, 7.5),
    "a30": (24, 8.0), "a10": (24, 8.6), "l4": (24, 8.9), "a5000": (24, 8.6),
    "rtx6000": (24, 7.5), "rtx": (24, 7.5), "v100": (16, 7.0), "a4000": (16, 8.6),
    "t4": (16, 7.5), "p100": (16, 6.0), "a2": (16, 8.6), "k80": (12, 3.7),
}

#: The order a job wants cards in: the fastest with room to spare first. Not by memory alone: on
#: Grace that would put the 15 a40 nodes ahead of the 100 a100 nodes, which are faster and much
#: easier to get.
PREFERENCE = ("b200", "h200", "gh200", "h100", "h800", "a100", "a800", "l40s", "a40", "l40",
              "a6000", "rtx8000", "a30", "a10", "l4", "a5000", "rtx6000", "rtx", "v100",
              "a4000", "t4", "p100", "a2", "k80")

#: What a partition's name says about it, when the access test cannot tell: these hold
#: interactive sessions or debugging, which a long batch job should not be sent to by default.
NOT_FOR_BATCH = re.compile(r"debug|interactive|vnc|test|dev|preempt|scavenge|viz", re.I)

#: A gres type with no card named in it: "gpu:4". Asked for as a bare gpu:1.
ANY_GPU = "any"


# ---------------------------------------------------------------------------- the cards

def family(gres_type):
    """The kind of card a gres type names, or "" when it is not one known here.

    Clusters spell the same card several ways: a100, nvidia_a100-sxm4-80gb, tesla_v100-sxm2-32gb,
    nvidia_h100_80gb_hbm3, quadro_rtx_6000, rtx_a6000. The name is split into its words and the
    first word that is a known card wins, with plain "rtx" looked at last because it is part of so
    many longer names.
    """
    words = [w for w in re.split(r"[^a-z0-9]+", str(gres_type or "").lower()) if w]
    for i, word in enumerate(words):
        if word in CARDS and word != "rtx":
            return word
        if word == "rtx" and i + 1 < len(words) and ("rtx" + words[i + 1]) in CARDS:
            return "rtx" + words[i + 1]
    for i, word in enumerate(words):
        if word.startswith("rtx") and word in CARDS:
            return word
    return "rtx" if "rtx" in words else ""


def facts(gres_type):
    """What is known about a card from its gres type: {family, memory_gb, capability}."""
    kind = family(gres_type)
    memory, capability = CARDS.get(kind, (0, 0.0))
    said = re.search(r"(\d{2,3})\s*gb", str(gres_type or "").lower())
    if said:
        memory = int(said.group(1))
    return {"family": kind, "memory_gb": memory, "capability": capability}


def preference(gres_type):
    """Where a card comes in the order a job wants them; unknown cards last."""
    kind = family(gres_type)
    return PREFERENCE.index(kind) if kind in PREFERENCE else len(PREFERENCE)


# ---------------------------------------------------------------------------- sinfo

def hours_of(limit):
    """A Slurm time limit in hours, or None for no limit.

    sinfo writes [days-]hours:minutes:seconds, and shortens it: 30:00 is thirty minutes, 2:00:00
    two hours, 1-00:00:00 a day, 2-12 two and a half days.
    """
    text = str(limit or "").strip().lower()
    if not text or text in ("infinite", "unlimited", "n/a", "none"):
        return None
    days = 0
    if "-" in text:
        head, text = text.split("-", 1)
        days = int(head)
        bits = [int(b) for b in text.split(":") if b != ""] + [0, 0, 0]
        h, m, s = bits[:3]
    else:
        bits = [int(b) for b in text.split(":") if b != ""]
        if len(bits) >= 3:
            h, m, s = bits[:3]
        elif len(bits) == 2:
            h, m, s = 0, bits[0], bits[1]
        else:
            h, m, s = 0, (bits[0] if bits else 0), 0
    return round(days * 24 + h + m / 60.0 + s / 3600.0, 3)


def parse_gres(text):
    """{card type: per node} from sinfo's %G, which lists every generic resource on a node.

    gpu:a100:2(S:0-1),gpu:rtx:2 is two kinds; gpu:4 is four cards nobody named; (null) is none.
    Anything that is not a gpu (shard, mps, a license) is left out.
    """
    out = {}
    text = str(text or "").strip()
    if not text or text == "(null)":
        return out
    for item in re.split(r",(?![^(]*\))", text):
        item = re.sub(r"\(.*?\)", "", item).strip()
        bits = item.split(":")
        if not bits or bits[0] != "gpu":
            continue
        if len(bits) == 2 and bits[1].isdigit():
            kind, count = ANY_GPU, int(bits[1])
        elif len(bits) >= 3 and bits[-1].isdigit():
            kind, count = ":".join(bits[1:-1]), int(bits[-1])
        elif len(bits) == 2:
            kind, count = bits[1], 1
        else:
            continue
        out[kind] = max(out.get(kind, 0), count)
    return out


#: The columns asked of sinfo, in the order parse_sinfo reads them.
SINFO_FORMAT = "%P|%l|%G|%c|%m|%a|%D"


def parse_sinfo(text):
    """{partition: what it holds}, from `sinfo -h -o SINFO_FORMAT`.

    sinfo gives one line per partition and kind of node, so a partition with three kinds of GPU
    node comes back as three lines and is put back together here. The default partition carries a
    star after its name.
    """
    parts = {}
    for line in str(text or "").splitlines():
        cols = line.strip().split("|")
        if len(cols) < 7 or not cols[0]:
            continue
        name, limit, gres, cpus, mem, avail, nodes = cols[:7]
        default = name.endswith("*")
        name = name.rstrip("*")
        part = parts.setdefault(name, {
            "name": name, "default": False, "max_hours": hours_of(limit), "gpus": {},
            "cpus": 0, "mem_gb": 0, "nodes": 0, "cpu_nodes": 0, "up": False})
        part["default"] = part["default"] or default
        for kind, count in parse_gres(gres).items():
            part["gpus"][kind] = max(part["gpus"].get(kind, 0), count)
        part["cpus"] = max(part["cpus"], _int(cpus))
        part["mem_gb"] = max(part["mem_gb"], _int(mem) // 1024)
        n = _int(nodes)
        part["nodes"] += n
        if not parse_gres(gres):
            part["cpu_nodes"] += n
        part["up"] = part["up"] or avail.strip().lower().startswith("up")
    return parts


def _int(text):
    found = re.match(r"\s*(\d+)", str(text or ""))
    return int(found.group(1)) if found else 0


# ---------------------------------------------------------------------------- may I use it

def test_only_argv(part, account=""):
    """The sbatch line that asks whether a job may run in this partition, queueing nothing."""
    argv = ["sbatch", "--test-only", "-p", part["name"], "-t", "00:10:00", "--mem=1G",
            "-n", "1", "-c", "1", "--wrap=true"]
    if part["gpus"]:
        kind = sorted(part["gpus"], key=preference)[0]
        argv.insert(4, "--gres=" + ("gpu:1" if kind == ANY_GPU else "gpu:%s:1" % kind))
    if account:
        argv.insert(2, "--account=" + account)
    return argv


def parse_test_only(text):
    """(would it run, the partition it would run in, what was said), from sbatch --test-only.

    sbatch exits 0 for a refusal too, so the words are what count: "Job N to start at ... in
    partition P" is a yes, anything carrying "error" or "failure" is a no.
    """
    text = str(text or "")
    found = re.search(r"to start at .*? in partition (\S+)", text)
    if found:
        return True, found.group(1), ""
    said = [l.strip() for l in text.splitlines() if l.strip()]
    return False, "", (said[-1] if said else "sbatch said nothing")


def parse_accounts(text):
    """The accounts this person may charge, from `sacctmgr -nP show assoc ... format=account`."""
    seen = []
    for line in str(text or "").splitlines():
        name = line.split("|")[0].strip()
        if name and name not in seen:
            seen.append(name)
    return seen


# ---------------------------------------------------------------------------- the defaults

def choose(partitions):
    """The defaults a page needs: {gpu_partition, default_gpu, cpu_partition}.

    Only partitions this person may use as themselves are considered. The card is the first in
    PREFERENCE that any of them holds; its partition is the one holding it that will run a job
    longest, leaving out the ones whose names say they are for debugging or sessions. The CPU
    partition is the cluster's default when that has no cards, and otherwise the CPU partition
    with the most nodes, which is where a short step is likeliest to start soon.
    """
    usable = [p for p in partitions.values() if p.get("usable", True) and p.get("up", True)]
    gpu_parts = [p for p in usable if p["gpus"]]
    # The card is chosen among the partitions meant for batch jobs, so the newest cards sitting
    # in a two-hour debug partition do not make that partition the default.
    batch = [p for p in gpu_parts if not NOT_FOR_BATCH.search(p["name"])] or gpu_parts
    cards = sorted({k for p in batch for k in p["gpus"]}, key=lambda k: (preference(k), k))
    use = {"gpu_partition": "", "default_gpu": "", "cpu_partition": ""}
    if cards:
        card = cards[0]
        holding = [p for p in batch if card in p["gpus"]]
        holding.sort(key=lambda p: (bool(NOT_FOR_BATCH.search(p["name"])),
                                    -(p["max_hours"] or 1e6), p["name"]))
        use["default_gpu"] = card
        use["gpu_partition"] = holding[0]["name"]
    cpu_parts = [p for p in usable if not p["gpus"] or p.get("cpu_nodes")]
    plain = [p for p in cpu_parts if not p["gpus"]] or cpu_parts
    default = [p for p in plain if p.get("default")]
    if default:
        use["cpu_partition"] = default[0]["name"]
    elif plain:
        plain.sort(key=lambda p: (bool(NOT_FOR_BATCH.search(p["name"])), -p["nodes"], p["name"]))
        use["cpu_partition"] = plain[0]["name"]
    return use


# ---------------------------------------------------------------------------- the GPU check

#: Which build of torch a GPU node can run is decided by two things the scheduler does not say:
#: the CUDA version its driver supports, and how old the oldest card is. CUDA 13 runs only on a
#: driver from 580 up and has dropped every card before Turing (a V100 is 7.0); the CUDA 12.6
#: build runs on any driver from 525 up and on cards back to Maxwell. The newest cards (10.0 and
#: up) need 12.8 or later.
BUILDS = ("cu130", "cu128", "cu126", "cu118")


def _version(text):
    found = re.match(r"\s*(\d+)\.(\d+)", str(text or ""))
    return (int(found.group(1)), int(found.group(2))) if found else None


def torch_build(cuda, capabilities=()):
    """(the build of torch these nodes can run, why), or (None, why not)."""
    have = _version(cuda)
    caps = [float(c) for c in capabilities if c]
    oldest = min(caps) if caps else None
    newest = max(caps) if caps else None
    if have is None:
        return "cu126", ("the driver was not read, and the CUDA 12.6 build runs on any driver "
                         "from 525 up")
    if have < (11, 8):
        return None, ("the GPU nodes' driver supports CUDA %d.%d, and nothing older than 11.8 can "
                      "run a torch new enough" % have)
    if have < (12, 0):
        return "cu118", "the driver supports CUDA %d.%d, so the 11.8 build" % have
    if newest is not None and newest >= 10.0:
        if have >= (13, 0) and (oldest is None or oldest >= 7.5):
            return "cu130", "the newest cards need CUDA 12.8 or later, and the driver has 13"
        if have >= (12, 8):
            return "cu128", "the newest cards need CUDA 12.8 or later"
        return None, ("the newest cards here need CUDA 12.8 or later and the driver supports "
                      "%d.%d" % have)
    if have >= (13, 0) and (oldest is None or oldest >= 7.5):
        return "cu130", "the driver supports CUDA %d.%d and every card is Turing or newer" % have
    if have >= (13, 0):
        return "cu126", ("the driver supports CUDA 13, but CUDA 13 has dropped cards as old as "
                         "these (%.1f), so the 12.6 build" % oldest)
    return "cu126", "the driver supports CUDA %d.%d, so the 12.6 build" % have


GPU_CHECK = """#!/bin/bash
#SBATCH --job-name=hopelabs_gpucheck
#SBATCH --partition={partition}
#SBATCH --gres={gres}
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=00:10:00
#SBATCH --output={out}/gpucheck_%j.out
{account}
# What a GPU node says about itself, for HOPE Labs' Set up page: its cards, its driver, the CUDA
# that driver supports, and whether it can reach the internet. Nothing is installed or kept.
echo "HOPE-GPU-HOST $(hostname)"
if ! nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv,noheader,nounits 2>/dev/null | sed 's/^/HOPE-GPU-CARD /'; then
  nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits 2>&1 | sed 's/^/HOPE-GPU-CARD /'
fi
nvidia-smi 2>/dev/null | grep -o 'CUDA Version: [0-9.]*' | head -1 | sed 's/^/HOPE-GPU-CUDA /'
if command -v curl >/dev/null 2>&1 && curl -s -o /dev/null --max-time 8 https://pypi.org/simple/pip/; then
  echo "HOPE-GPU-NET yes"
else
  echo "HOPE-GPU-NET no"
fi
echo "HOPE-GPU-DONE"
"""


def gpu_check_script(partition, out, account="", gres="gpu:1"):
    """The check job: one card of whatever kind is free first, for under a minute."""
    return GPU_CHECK.format(partition=partition, gres=gres, out=out,
                            account=("#SBATCH --account=%s" % account) if account else "")


def parse_gpu_check(text):
    """What the check job found, or None when it did not get as far as saying it was done."""
    text = str(text or "")
    if "HOPE-GPU-DONE" not in text:
        return None
    got = {"host": "", "driver": "", "cuda": "", "cards": [], "network": None}
    for line in text.splitlines():
        if line.startswith("HOPE-GPU-HOST "):
            got["host"] = line.split(" ", 1)[1].strip()
        elif line.startswith("HOPE-GPU-CARD "):
            cols = [c.strip() for c in line.split(" ", 1)[1].split(",")]
            if len(cols) >= 3 and _int(cols[1]):
                card = {"name": cols[0], "memory_gb": int(round(_int(cols[1]) / 1024.0)),
                        "capability": float(cols[3]) if len(cols) > 3 and _version(cols[3]) else 0.0}
                got["driver"] = got["driver"] or cols[2]
                got["cards"].append(card)
        elif line.startswith("HOPE-GPU-CUDA "):
            found = re.search(r"([\d.]+)\s*$", line)
            got["cuda"] = found.group(1) if found else ""
        elif line.startswith("HOPE-GPU-NET "):
            got["network"] = line.split()[-1] == "yes"
    for card in got["cards"]:
        if not card["capability"]:
            card["capability"] = facts(card["name"])["capability"]
    return got


# ---------------------------------------------------------------------------- the network

#: What an install fetches from, by name. Checked from the login node, where installs run.
SOURCES = (
    ("conda-forge", "https://conda.anaconda.org/conda-forge/"),
    ("PyPI", "https://pypi.org/simple/pip/"),
    ("PyTorch", "https://download.pytorch.org/whl/"),
    ("Hugging Face", "https://huggingface.co/"),
    ("GitHub", "https://github.com/"),
    ("Scripps", "https://ccsb.scripps.edu/"),
    ("MSA server", "https://api.colabfold.com/"),
    ("RCSB", "https://files.rcsb.org/"),
)


def reachable(sources=SOURCES, timeout=10):
    """{name: True/False}, each fetched at once rather than one after another."""
    got = {}

    def one(name, url):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hope-labs-setup"})
            with urllib.request.urlopen(req, timeout=timeout) as answer:
                answer.read(512)
            got[name] = True
        except urllib.error.HTTPError:
            got[name] = True              # an answer, even a refusal, is a route out
        except Exception:                                          # noqa: BLE001
            got[name] = False

    threads = [threading.Thread(target=one, args=pair, daemon=True) for pair in sources]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout + 5)
    return {name: got.get(name, False) for name, _ in sources}


# ---------------------------------------------------------------------------- asking

def run(argv, timeout=60):
    """(exit status, everything it printed). A missing command is status 127, as in a shell."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return done.returncode, (done.stdout or "") + (done.stderr or "")
    except FileNotFoundError:
        return 127, "%s: not found" % argv[0]
    except subprocess.TimeoutExpired:
        return 124, "%s did not answer within %d seconds" % (argv[0], timeout)


class NotSlurm(RuntimeError):
    """This cluster has no Slurm to ask, and HOPE Labs queues everything through Slurm."""


def probe(ask=run, account="", say=None, network=True):
    """What this cluster has, as a profile not yet written down.

    `ask` runs a command and gives back (status, text); a test hands in one that answers with
    another cluster's words.
    """
    say = say or (lambda *a: None)
    status, text = ask(["sinfo", "-h", "-o", SINFO_FORMAT])
    if status == 127:
        raise NotSlurm("there is no sinfo here. HOPE Labs queues its work through Slurm, and "
                       "this cluster does not seem to have it.")
    if status != 0 or not text.strip():
        raise NotSlurm("sinfo did not list any partitions: %s" % text.strip()[:300])
    partitions = parse_sinfo(text)
    say("%d partitions" % len(partitions))
    _, config = ask(["scontrol", "show", "config"])
    cluster = (re.search(r"ClusterName\s*=\s*(\S+)", config) or [None, ""])[1].strip().lower()
    slurm = (re.search(r"SLURM_VERSION\s*=\s*(\S+)", config) or [None, ""])[1].strip()
    def one(part):
        if not part["up"]:
            part.update(usable=False, runs_as="", said="the partition is not up")
            return
        _, said = ask(test_only_argv(part, account), timeout=30)
        ok, where, why = parse_test_only(said)
        part["usable"] = ok and where == part["name"]
        part["runs_as"] = where
        part["said"] = ("" if part["usable"] else
                        ("a job sent here is run in %s instead" % where) if ok else why)

    # All at once: each test takes a few seconds on a busy controller, and one after another
    # they made the probe a half-minute wait.
    asking = [threading.Thread(target=one, args=(part,), daemon=True)
              for part in partitions.values()]
    for t in asking:
        t.start()
    for t in asking:
        t.join(60)
    user = os.environ.get("USER") or ""
    _, assoc = ask(["sacctmgr", "-nP", "show", "assoc", "where", "user=" + user,
                    "format=account"])
    _, mine = ask(["sacctmgr", "-nP", "show", "user", user, "format=defaultaccount"])
    cards = {}
    for part in partitions.values():
        for kind in part["gpus"]:
            if kind != ANY_GPU:
                cards[kind] = dict(facts(kind), seen=False)
    doc = {
        "schema": SCHEMA, "cluster": cluster, "name": cluster.capitalize() if cluster else "",
        "address": "", "slurm": slurm,
        "probed": time.strftime("%Y-%m-%d %H:%M"), "probed_by": user,
        "probed_on": socket.gethostname(),
        "partitions": [partitions[k] for k in sorted(partitions)],
        "cards": cards,
        "accounts": parse_accounts(assoc), "default_account": mine.strip().splitlines()[0]
        if mine.strip() else "",
        "use": choose(partitions),
        "network": {"login": reachable() if network else {}, "compute": None, "proxy": ""},
        "gpu_check": {},
        "notes": {},
    }
    return doc


# ---------------------------------------------------------------------------- the file

def load(root=None):
    """The cluster's profile, or None when it has not been written."""
    try:
        with open(profile_path(root), encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or int(doc.get("schema") or 0) != SCHEMA:
        return None
    return doc


def save(doc, root=None):
    """Write the profile, readable by the group, in one step so no page reads half of it."""
    path = profile_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = "%s.%d.tmp" % (path, os.getpid())
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(doc, handle, indent=2)
        handle.write("\n")
    os.chmod(temp, 0o664)
    os.replace(temp, path)
    return path


def amend(doc, use=None, name=None, address=None, gpu_check=None, proxy=None):
    """A profile with the person's corrections in, refused when they name what is not there."""
    doc = json.loads(json.dumps(doc))
    names = {p["name"]: p for p in doc.get("partitions", [])}
    if use:
        for key in ("gpu_partition", "cpu_partition", "default_gpu"):
            value = str(use.get(key) or "").strip()
            if key not in use:
                continue
            if key.endswith("_partition") and value and value not in names:
                raise ValueError("there is no partition called %r here" % value)
            if key == "default_gpu" and value:
                holding = names.get(str(use.get("gpu_partition") or doc["use"]["gpu_partition"]))
                if holding is None or value not in holding.get("gpus", {}):
                    raise ValueError("%s has no card called %r" % (
                        (holding or {}).get("name", "that partition"), value))
            doc["use"][key] = value
    if name is not None:
        doc["name"] = str(name).strip()[:60]
    if address is not None:
        doc["address"] = str(address).strip()[:200]
    if proxy is not None:
        doc.setdefault("network", {})["proxy"] = str(proxy).strip()[:200]
    if gpu_check:
        doc["gpu_check"] = gpu_check
        doc.setdefault("network", {})["compute"] = gpu_check.get("network")
        for card in gpu_check.get("cards", []):
            kind = family(card["name"])
            for key, known in doc.get("cards", {}).items():
                if family(key) == kind and kind:
                    known.update(memory_gb=card["memory_gb"] or known["memory_gb"],
                                 capability=card["capability"] or known["capability"],
                                 seen=True)
    return doc


def proxy(root=None):
    """The web proxy this cluster's login nodes need, or "": the profile's, else the one the
    first-time set-up was given (setup/proxy), which is all there is before the first probe."""
    doc = load(root) or {}
    said = ((doc.get("network") or {}).get("proxy") or "").strip()
    if said:
        return said
    try:
        with open(os.path.join(root or labs_root(), "setup", "proxy"), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def use_proxy(root=None):
    """Send this process's downloads through the cluster's proxy, if it has one and nothing
    has named one already. Called by the pages as they start: the launcher starts them with
    a bare environment, and a page that fetches alignments or installs must go the same way
    the first-time set-up went."""
    said = proxy(root)
    if said and not (os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")):
        for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            os.environ[name] = said
    return said


def capabilities(doc):
    """Every card's compute capability, seen or guessed: what decides the torch build."""
    caps = [c.get("capability") for c in (doc.get("cards") or {}).values()]
    caps += [c.get("capability") for c in (doc.get("gpu_check") or {}).get("cards", [])]
    return [c for c in caps if c]
