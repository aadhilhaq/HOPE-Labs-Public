"""What each cluster has for a folding job, and which of the two tools has been seen on it.

By Aadhil Haq

Laid out as HOPE-MD's hopemd/site.py is: a table of sites, a lookup from the hostname, and
nothing in the code that knows the name of a queue. Everything a job script needs that differs
between Grace and ACES is here, so a page served on either asks for a card the site actually
has rather than one it was written against.

Two things are deliberately NOT here. Where a tool is installed belongs to hope_labs/tools.py,
which already carries an install per tool and already rebases it under another lab root; a second
table of paths would be a second thing to get wrong. And how to sign in to a cluster belongs to
hope_labs/clusters.py. What is left is the part a job cares about: the partitions, the cards in
them and whether sbatch insists on being told the memory.

The honest part is `checked`. Grace's block was read off the cluster: sinfo for the partitions,
the install folders for the tools. ACES's partitions come from HOPE-MD's own audit of an ACES
login node, and neither folding tool has been looked for there at all, by anybody. So ACES names
no tool as present, the page says so where a person can see it, and nothing here pretends
otherwise. Correct it by looking, not by assuming.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess


#: What a site names when nobody here knows its cards. Site.gres turns it into a bare gpu:1.
ANY_GPU = "any"

#: The Card menu's first option, and its default: nobody chose. The same word a flow card uses
#: for the same question (hope_flow/cards.py), so a person who has set the card on a flow reads
#: this menu without learning a second vocabulary. Site.card turns it into a card.
AUTO = "auto"


class Partition:
    """One queue: how long it will run a job and which cards are in it."""

    def __init__(self, name, max_hours, gres_types=()):
        self.name = name
        self.max_hours = max_hours
        self.gres_types = tuple(gres_types)


class Site:
    """One cluster, as a folding job has to ask it for things."""

    def __init__(self, key, name, login_hosts, host_prefixes, partitions, gpu_partition,
                 cpu_partition, default_gpu, gpu_memory_gb, mem_required=True, checked=(),
                 notes=None, order=()):
        self.key = key
        self.name = name
        self.login_hosts = tuple(login_hosts)
        #: What a hostname starts with here, login node or compute node. ACES needs all three:
        #: its login nodes are alogin<n> and its compute nodes ac<nnn>, which share no prefix.
        self.host_prefixes = tuple(host_prefixes)
        self.partitions = dict(partitions)
        self.gpu_partition = gpu_partition
        self.cpu_partition = cpu_partition
        self.default_gpu = default_gpu
        self.gpu_memory_gb = dict(gpu_memory_gb)
        #: Grace's job_submit plugin refuses a job that names neither --mem nor --mem-per-cpu, so
        #: a script written without one is rejected at submission rather than queued.
        self.mem_required = bool(mem_required)
        #: The tools whose install was actually found and read on this site. A tool not in here is
        #: reported as unknown rather than as working.
        self.checked = frozenset(checked)
        self.notes = dict(notes or {})
        #: The cards in the order a job wants them, for a site read off a cluster: its card names
        #: are the cluster's own (nvidia_a100-sxm4-80gb), which the fixed order below knows nothing
        #: about. Empty for the two sites in the table.
        self.order = tuple(order)

    # --- the cards -----------------------------------------------------------------------------

    def gpu_types(self):
        """Every card on this site's GPU partitions, the one a folding job wants first.

        Order by memory, because that is what decides whether a complex fits: Boltz-2 on a long
        protein and ColabFold on a multimer both run out of card before they run out of time.
        """
        order = self.order or ("h100", "a100", "a40", "a30", "rtx", "t4")
        have = {g for p in self.partitions.values() for g in p.gres_types}
        return [g for g in order if g in have] + sorted(g for g in have if g not in order)

    def card(self, gpu=None):
        """The card a job will actually be asked for, from whatever the form holds.

        `auto` is the menu's default and means nobody chose, so this resolves it to the site's
        own default card, which gpu_types orders first and is therefore the largest here. Not
        handed to Slurm as a bare gpu:1: both tools run out of card long before they run out of
        clock, so a long chain that landed on Grace's t4 would lose the whole booking, and the
        scheduler has no way to know that. The same reasoning as hope_flow/drivers.py's.
        """
        want = str(gpu or "").strip().lower()
        return self.default_gpu if not want or want == AUTO else want

    def partition_for(self, gpu=None):
        """The partition that carries this card: the default one when it does.

        Grace's `gpu` holds the a100, rtx and t4 nodes and its a40s are a partition of their own,
        so a job that asks for an a40 in `gpu` is refused outright by sbatch.
        """
        gpu = self.card(gpu)
        if gpu in self.partitions[self.gpu_partition].gres_types:
            return self.gpu_partition
        for name, part in self.partitions.items():
            if gpu in part.gres_types:
                return name
        return self.gpu_partition

    def gres(self, gpu=None, count=1):
        """The --gres line, or a refusal naming the cards this site does have."""
        want = self.card(gpu)
        if want not in self.gpu_types():
            raise ValueError("%s has no GPU called %r; it has %s"
                             % (self.name, want, ", ".join(self.gpu_types()) or "none"))
        # "any" is the only thing an unrecognised cluster can ask for, and it has to be asked for
        # without a type: gpu:any:1 is a constraint no node satisfies and sbatch refuses it
        # outright. Naming a type is better wherever a type is known, which is why the two sites
        # in the table never use this.
        if want == ANY_GPU:
            return "gpu:%d" % count
        return "gpu:%s:%d" % (want, count)

    def max_gpu_hours(self, gpu=None):
        return self.partitions[self.partition_for(gpu)].max_hours

    def memory_of(self, gpu=None):
        return self.gpu_memory_gb.get(self.card(gpu), 0)


# --------------------------------------------------------------------------- Grace
# Partitions from sinfo on a Grace login node: short 2 h, medium 1 day, long 7 days, xlong
# 21 days, gpu 4 days with gpu:a100:2, gpu:rtx:2 and gpu:t4:4 nodes, gpu-a40 4 days with
# gpu:a40:3, bigmem 2 days. Both tools were found, read and had their versions taken off the
# install here, which is what `checked` records.
GRACE = Site(
    key="grace",
    name="Grace",
    login_hosts=("grace.hprc.tamu.edu",),
    host_prefixes=("grace",),
    partitions={
        "short": Partition("short", 2),
        "medium": Partition("medium", 24),
        "long": Partition("long", 7 * 24),
        "gpu": Partition("gpu", 4 * 24, ("a100", "rtx", "t4")),
        "gpu-a40": Partition("gpu-a40", 4 * 24, ("a40",)),
    },
    gpu_partition="gpu",
    cpu_partition="short",
    default_gpu="a100",
    gpu_memory_gb={"a100": 40, "a40": 48, "rtx": 24, "t4": 16},
    mem_required=True,
    checked=("boltz2", "colabfold"),
    notes={
        # Measured on this cluster, not assumed, and the reason both tools are run in two phases:
        # a login node reaches the alignment server and a compute node reaches nothing at all.
        "network": "A Grace compute node has no route to the internet: DNS resolves and every "
                   "connection fails. The alignments are fetched on the login node, before the "
                   "job is queued.",
        "t4": "A t4 has 16 GB. Anything much over 1,000 residues wants the a100.",
    },
)

# --------------------------------------------------------------------------- ACES
# Partitions as HOPE-MD's own audit of an ACES login node recorded them (hopemd/site.py): cpu
# 3 days, gpu 2 days with h100 and a30 nodes, gpu_debug 2 h under a QoS that allows one person
# two jobs at a time. Every node has 96 cores and 500 GB.
#
# `checked` is empty on purpose. Neither Boltz-2 nor ColabFold has been looked for on ACES, and
# this file is not the place to guess: a page served there reports the tool as not installed and
# names the folder it looked in, which is a sentence somebody can act on. When one of them is
# installed and read on ACES, add its key here and nothing else changes.
ACES = Site(
    key="aces",
    name="ACES",
    login_hosts=("login.aces.hprc.tamu.edu",),
    #: alogin<n> for the login nodes, ac<nnn> for the compute nodes, and "aces" for anything that
    #: calls itself by the cluster's name.
    host_prefixes=("alogin", "aces", "ac"),
    partitions={
        "cpu": Partition("cpu", 3 * 24),
        "gpu": Partition("gpu", 2 * 24, ("h100", "a30")),
        "gpu_debug": Partition("gpu_debug", 2, ("h100", "a30")),
    },
    gpu_partition="gpu",
    cpu_partition="cpu",
    default_gpu="h100",
    gpu_memory_gb={"h100": 80, "a30": 24},
    mem_required=True,
    checked=(),
    notes={
        "installs": "Neither folding tool has been found on ACES. The page says so and names "
                    "the folder it looked in; it does not queue a job that could not run.",
        "gpu_debug": "gpu_debug takes two jobs per person at a time, and two hours each.",
    },
)

SITES = {site.key: site for site in (GRACE, ACES)}

#: What a site that is neither of these gets. It asks for a GPU without naming a type, which is
#: the only thing that queues on a cluster whose cards nobody here has seen, and it says in the
#: plan that it is doing that.
ELSEWHERE = Site(
    key="elsewhere",
    name="this cluster",
    login_hosts=(),
    host_prefixes=(),
    partitions={"gpu": Partition("gpu", 24, (ANY_GPU,))},
    gpu_partition="gpu",
    cpu_partition="short",
    default_gpu=ANY_GPU,
    gpu_memory_gb={},
    mem_required=True,
    checked=(),
    notes={"site": "This cluster is not one of the two in the table, so the job asks for a GPU "
                   "without naming a type and lets the scheduler choose."},
)


def site_for_host(hostname):
    """The site a hostname belongs to, or None.

    Prefixes rather than full names, because this is asked on a login node and inside a job and
    the two are named nothing like each other: alogin3 and ac042 on ACES.

    It is worth knowing what this cannot do. A Grace login node calls itself login1.cluster and a
    Grace compute node calls itself c263: neither carries the cluster's name anywhere, so no
    prefix test recognises Grace from a machine that is Grace. That is what cluster_name() below
    is for, and why here() asks it first.
    """
    name = (hostname or "").strip().lower()
    for site in SITES.values():
        if site.host_prefixes and name.startswith(site.host_prefixes):
            return site
    return None


def cluster_name():
    """What Slurm calls this cluster, lower case, or "".

    The only thing on either cluster that states which cluster it is, from a login node and from
    inside a job alike. The page already reads it for the address to tunnel to
    (hope_labs/pages/serve.py), so this is the same question asked for the other reason.
    """
    try:
        config = subprocess.run(["scontrol", "show", "config"], capture_output=True, text=True,
                                timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    found = re.search(r"ClusterName\s*=\s*(\S+)", config)
    return found.group(1).strip().lower() if found else ""


def here():
    """The site this process is running on.

    HOPELABS_SITE names it outright, for a cluster whose hostnames say nothing useful and for a
    test that has to be able to be somewhere it is not. Then Slurm, which is the one thing that
    answers correctly on a login node and on a compute node; then the hostname, which catches a
    machine with no Slurm on it. Anything unrecognised is ELSEWHERE rather than None: a page has
    to be able to draw a plan wherever it was started.

    A cluster with a profile (cluster.json in the HOPE Labs folder, written by the Set up page) is
    read from it before the table is consulted, because the profile was read off the cluster by
    the people using it and the table was written here. Grace and ACES have no profile in the
    lab's folder, so for them nothing changes.
    """
    named = (os.environ.get("HOPELABS_SITE") or "").strip().lower()
    if named in SITES:
        return SITES[named]
    profiled = profile_site()
    if profiled is not None:
        return profiled
    asked = cluster_name()
    if asked in SITES:
        return SITES[asked]
    return site_for_host(socket.gethostname()) or ELSEWHERE


#: What an unlimited partition is taken to allow, so a walltime box still has a top.
UNLIMITED_HOURS = 30 * 24


def from_profile(doc, checked=()):
    """A Site from a cluster's profile (hope_labs/setup/profile.py), or None when it names no GPU
    partition this person may use.

    Only the partitions the probe found usable go in, so a page never offers one that sbatch
    would quietly reroute or refuse. The cards keep the cluster's own names, because those are
    what --gres must say, and are ordered the way the profile ranks them.
    """
    from ..setup import profile as profilemod
    use = doc.get("use") or {}
    parts = {}
    for part in doc.get("partitions") or []:
        if part.get("usable") is False or not part.get("name"):
            continue
        parts[part["name"]] = Partition(part["name"], part.get("max_hours") or UNLIMITED_HOURS,
                                        tuple(part.get("gpus") or ()))
    gpu_partition = use.get("gpu_partition") or ""
    if gpu_partition not in parts or not parts[gpu_partition].gres_types:
        return None
    cards = sorted({g for p in parts.values() for g in p.gres_types},
                   key=lambda g: (profilemod.preference(g), g))
    default = use.get("default_gpu") or ""
    if default not in parts[gpu_partition].gres_types:
        default = sorted(parts[gpu_partition].gres_types,
                         key=lambda g: (profilemod.preference(g), g))[0]
    memory = {kind: int((info or {}).get("memory_gb") or 0)
              for kind, info in (doc.get("cards") or {}).items()}
    notes = {}
    network = doc.get("network") or {}
    if network.get("compute") is False:
        notes["network"] = ("A compute node here has no route to the internet. The alignments are "
                            "fetched on the login node, before the job is queued.")
    check = doc.get("gpu_check") or {}
    if check.get("driver"):
        notes["gpu_check"] = ("A GPU node here reported driver %s, CUDA %s."
                              % (check["driver"], check.get("cuda") or "unknown"))
    name = doc.get("name") or (doc.get("cluster") or "").capitalize() or "this cluster"
    address = doc.get("address") or ""
    return Site(key=doc.get("cluster") or "profile", name=name,
                login_hosts=(address,) if address else (), host_prefixes=(),
                partitions=parts, gpu_partition=gpu_partition,
                cpu_partition=use.get("cpu_partition") or "", default_gpu=default,
                gpu_memory_gb=memory, mem_required=True, checked=tuple(checked), notes=notes,
                order=cards)


def profile_site(root=None):
    """The site the HOPE Labs folder's profile describes, or None when it has none.

    `checked` is the tools Set up has installed AND seen fold here, which is what the table's
    `checked` means for Grace: a tool read on this cluster rather than assumed.
    """
    from ..setup import engine, profile as profilemod
    doc = profilemod.load(root)
    if not doc:
        return None
    checked = [key for key in ("boltz2", "colabfold")
               if engine.read_state(root, key).get("state") == engine.READY]
    try:
        return from_profile(doc, checked)
    except (KeyError, TypeError, ValueError):
        return None


def installed_here(site, tool, install):
    """(is it there, what to say about it) for one tool at one site.

    Three answers, not two. A folder that is there is there. A folder that is not there on a site
    where the tool has been read before is a broken install, which is worth saying differently
    from a site where nobody has ever looked: on ACES the sentence has to say that the lab has
    not installed it rather than imply something has gone wrong.
    """
    if os.path.isdir(install):
        return True, ""
    if tool in site.checked:
        return False, ("%s is installed on %s but the folder is not there now: %s"
                       % (tool, site.name, install))
    return False, ("%s is not installed on %s. Nothing was found at %s, and nobody has "
                   "recorded it working here, so this page can show you what a run would be "
                   "told and cannot queue one." % (tool, site.name, install))
