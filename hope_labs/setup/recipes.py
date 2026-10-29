"""What installing each tool means, step by step: the recipes the engine follows.

By Aadhil Haq

A recipe is a tool's install written down as steps, each of which can say whether it has already
been done. The engine (engine.py) runs them in order and the Set up page shows them, so a recipe
is also the page's description of the tool: what it needs from the cluster, how much room it
takes, where its downloads come from and under what license.

Boltz-2 is the first, chosen because it holds every part that makes installing on someone else's
cluster hard: a GPU partition and a card to ask for, a build of torch that must match the GPU
nodes' driver, 6 GB of weights that a compute node may have no network to fetch, and a job at the
end that proves the lot. What is here was learned the hard way, and its lessons are kept: a separate environment, the `cuda`
extra, the weights fetched on the login node and checked by size, mols.tar kept beside mols/.

Every environment is made with micromamba, a single file that needs no administrator, no conda
and no Python, so the only things asked of a cluster are a login node with a network and Slurm.
"""
from __future__ import annotations

import glob
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import tarfile
import threading
import time

from . import engine
from . import profile as profilemod
from . import setup_dir
from .engine import Failed

MICROMAMBA = ("https://github.com/mamba-org/micromamba-releases/releases/latest/download/"
              "micromamba-%s")
PLATFORMS = {"x86_64": "linux-64", "aarch64": "linux-aarch64", "ppc64le": "linux-ppc64le"}


class Step:
    """One step of an install: its name, what the page calls it, what it does, how it knows it
    has been done. `always` steps run every time (a check of room, a tidy-up)."""

    def __init__(self, name, title, run, done, always=False):
        self.name = name
        self.title = title
        self.run = run
        self._done = done
        self.always = always

    def done(self, ctx):
        return False if self.always else bool(self._done(ctx))


class Recipe:
    key = ""
    name = ""
    install = ""                          # where it goes, under the HOPE Labs folder
    summary = ""
    disk_gb = 0
    files = 0
    minutes = 0
    licence = ("", "")
    needs = {}
    sources = ()
    #: What the check is, in the page's words: the verb for what it proved ("Folded on g081 in
    #: 5 min") and the noun for doing it again ("Queue the check fold again"). Each tool's own,
    #: because a design run or a sampling run is not a fold, and the page said every check was.
    check_verb = ""
    check_noun = ""

    def where(self, root):
        return os.path.join(root, self.install)

    def repository(self):
        """The git repository this tool's code is cloned from, or "" when it is not cloned:
        what Set up asks the login node about before offering the tool (fetchable)."""
        return ""

    def not_here(self):
        """Why this tool cannot be installed on this cluster at all, or "" when it can."""
        return ""

    def owns(self, root):
        """Every folder this tool's install made under the HOPE Labs folder, which Remove takes
        away again. Not the package manager or its cache: the other tools share those."""
        return [self.where(root)]

    def steps(self):
        return []

    def as_json(self, root):
        return {"key": self.key, "name": self.name, "summary": self.summary,
                "install": self.where(root), "disk_gb": self.disk_gb, "files": self.files,
                "minutes": self.minutes,
                "licence": {"name": self.licence[0], "url": self.licence[1]},
                "needs": dict(self.needs), "sources": list(self.sources),
                "check_verb": self.check_verb, "check_noun": self.check_noun,
                "steps": [{"name": s.name, "title": s.title} for s in self.steps()]}


# ---------------------------------------------------------------------------- shared steps

def micromamba(root):
    return os.path.join(root, "tools", "micromamba")


def get_micromamba(ctx):
    """The package manager: one file, fetched once into the HOPE Labs folder's tools/."""
    machine = platform.machine()
    if machine not in PLATFORMS:
        raise Failed("this login node is %s, and micromamba is built for %s only"
                     % (machine, ", ".join(sorted(PLATFORMS))))
    path = micromamba(ctx.root)
    ctx.fetch(MICROMAMBA % PLATFORMS[machine], path, "micromamba", expect_at_least=1000000)
    os.chmod(path, 0o775)
    ctx.sh([path, "--version"], "micromamba")


def have_micromamba(ctx):
    path = micromamba(ctx.root)
    return os.path.isfile(path) and os.access(path, os.X_OK)


def site_packages(env):
    found = glob.glob(os.path.join(env, "lib", "python3*", "site-packages"))
    return found[0] if found else ""


def dist_version(env, name):
    """The version of an installed package, read off its dist-info folder rather than imported:
    importing anything from these environments means importing torch, which takes minutes."""
    folder = site_packages(env)
    pattern = re.compile(r"^%s-([^-]+)\.dist-info$" % re.escape(name).replace("_", "[-_]"),
                         re.I)
    for entry in sorted(os.listdir(folder)) if folder else []:
        found = pattern.match(entry)
        if found:
            return found.group(1)
    return ""


def torch_cuda(env):
    """(torch's version, the CUDA it was built for) from torch/version.py, without importing it."""
    path = os.path.join(site_packages(env), "torch", "version.py")
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return "", ""
    version = re.search(r"^__version__\s*=\s*'([^']+)'", text, re.M)
    cuda = re.search(r"^cuda\s*(?::[^=]*)?=\s*'([^']*)'", text, re.M)
    return (version.group(1) if version else ""), (cuda.group(1) if cuda else "")


# ---------------------------------------------------------------------------- Boltz-2

class Boltz2(Recipe):
    key = "boltz2"
    name = "Boltz-2"
    install = "envs/boltz"
    summary = ("Folds protein, DNA, RNA and small molecules together, from a list of sequences. "
               "Needs a GPU partition.")
    disk_gb = 16
    files = 95000
    minutes = 30
    licence = ("MIT, code and weights", "https://github.com/jwohlwend/boltz/blob/main/LICENSE")
    needs = {"gpu": True, "gpu_memory_gb": 16}
    sources = ("conda-forge", "PyPI", "PyTorch", "Hugging Face", "GitHub")
    check_verb = "Folded"
    check_noun = "fold"

    VERSION = "2.2.1"
    PYTHON = "3.11"
    #: The torch each build is pinned to: the one the lab's own install runs (2.14) wherever the
    #: build has it, and the newest there is where it does not.
    TORCH = {"cu130": "torch==2.14.1", "cu126": "torch==2.14.1", "cu128": "torch==2.11.0",
             "cu118": "torch"}
    #: The weights, where Boltz-2 2.2.1 itself fetches them from, and the least each may be.
    #: mols.tar stays beside mols/ once it is unpacked: Boltz checks for the tar on every run and
    #: fetches it again if it is gone, which a compute node cannot do.
    WEIGHTS = (
        ("mols.tar", "https://huggingface.co/boltz-community/boltz-2/resolve/main/mols.tar",
         1.0e9),
        ("boltz2_conf.ckpt",
         "https://huggingface.co/boltz-community/boltz-2/resolve/main/boltz2_conf.ckpt", 1.5e9),
        ("boltz2_aff.ckpt",
         "https://huggingface.co/boltz-community/boltz-2/resolve/main/boltz2_aff.ckpt", 1.5e9),
    )
    STAMP = ".hope_weights_ok"
    #: What the check folds: a short protein and a magnesium ion. The ion is there so the check
    #: also proves the chemical components were unpacked, which a protein alone never reads.
    CHECK_SEQUENCE = "MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQ"

    def steps(self):
        return [
            Step("room", "Room for it", self.room, None, always=True),
            Step("micromamba", "The package manager", get_micromamba, have_micromamba),
            Step("gpucheck", "Asking a GPU node what it has", self.gpu_ask, self.gpu_asked),
            Step("python", "Python %s" % self.PYTHON, self.python, self.python_done),
            Step("weights", "Boltz-2's weights, 6 GB", self.weights, self.weights_done),
            Step("torch", "PyTorch for these GPU nodes", self.torch, self.torch_done),
            Step("boltz", "Boltz-2 %s" % self.VERSION, self.boltz, self.boltz_done),
            Step("tidy", "Clearing the download caches", self.tidy, None, always=True),
            Step("check", "A real fold on the GPU queue", self.check, self.check_done),
        ]

    # --- room
    def room(self, ctx):
        where = self.where(ctx.root)
        gb, files = engine.free_room(ctx.root)
        need_gb, need_files = self.disk_gb, self.files
        if os.path.isdir(where):
            need_gb, need_files = need_gb / 2.0, need_files / 2   # part of it is there already
        ctx.say("  free where it goes: %s, %s" % (
            "%.0f GB" % gb if gb is not None else "space unknown",
            "{:,} files".format(int(files)) if files is not None else "files unknown"))
        if gb is not None and gb < need_gb:
            raise Failed("%s needs about %d GB and there is %.0f GB free in %s. Free some room, "
                         "or set HOPE Labs up somewhere with more." % (
                             self.name, self.disk_gb, gb, ctx.root))
        if files is not None and files < need_files:
            raise Failed("%s needs about {:,} files and the quota where it would go allows "
                         "{:,} more. Clusters cap the number of files as well as the space; "
                         "free some, or set HOPE Labs up somewhere with more."
                         .format(self.files, int(files)) % self.name)

    # --- what the GPU nodes can run
    def gpu_ask(self, ctx):
        """Queue the GPU check now and read it later: it waits in the queue while the weights
        download, and only the torch step needs its answer."""
        prof = ctx.profile or {}
        if (prof.get("gpu_check") or {}).get("cuda"):
            return
        use = prof.get("use") or {}
        if not use.get("gpu_partition"):
            raise Failed("the cluster's profile names no GPU partition. Open This cluster on the "
                         "Set up page and choose one.")
        folder = ctx.path("setup", "gpucheck")
        os.makedirs(folder, exist_ok=True)
        script = profilemod.gpu_check_script(use["gpu_partition"], folder, ctx.account)
        ctx.state["gpucheck_job"] = engine.submit(ctx, script, folder, "the GPU check")
        ctx.beat(force=True)

    def gpu_asked(self, ctx):
        return bool(((ctx.profile or {}).get("gpu_check") or {}).get("cuda")
                    or ctx.state.get("gpucheck_job"))

    def gpu_answer(self, ctx):
        """The GPU check's answer, waiting for it if it is still in the queue."""
        prof = profilemod.load(ctx.root) or {}
        if (prof.get("gpu_check") or {}).get("cuda"):
            ctx.profile = prof
            return prof["gpu_check"]
        job = ctx.state.get("gpucheck_job")
        if not job:
            raise Failed("the GPU check was never queued; Install again queues it")
        state = engine.wait_for(ctx, job, "the GPU check", poll=10)
        out = os.path.join(ctx.path("setup", "gpucheck"), "gpucheck_%s.out" % job)
        try:
            text = open(out, encoding="utf-8", errors="replace").read()
        except OSError:
            text = ""
        got = profilemod.parse_gpu_check(text)
        if not got or not got.get("driver"):
            ctx.state.pop("gpucheck_job", None)
            raise Failed("the GPU check job (%s, %s) did not say what the node has. Its output is "
                         "%s" % (job, state or "gone from the queue", out))
        got["when"] = time.strftime("%Y-%m-%d %H:%M")
        got["job"] = job
        prof = profilemod.amend(prof, gpu_check=got)
        profilemod.save(prof, ctx.root)
        ctx.profile = prof
        ctx.say("  %s: %s, driver %s, CUDA %s; compute node network: %s" % (
            got["host"], ", ".join("%s %d GB" % (c["name"], c["memory_gb"])
                                   for c in got["cards"]),
            got["driver"], got["cuda"] or "?", {True: "yes", False: "no"}.get(got["network"],
                                                                             "unknown")))
        return got

    # --- the environment
    def python(self, ctx):
        ctx.sh([micromamba(ctx.root), "create", "-y", "-p", self.where(ctx.root),
                "-c", "conda-forge", "--override-channels",
                "python=%s" % self.PYTHON, "pip"], "Python %s" % self.PYTHON, timeout=3600)

    def python_done(self, ctx):
        return os.path.isfile(os.path.join(self.where(ctx.root), "bin", "python"))

    def pip(self, ctx, *args, what=""):
        python = os.path.join(self.where(ctx.root), "bin", "python")
        ctx.sh([python, "-m", "pip", "install", "--no-cache-dir", "--progress-bar", "off"]
               + list(args), what or "pip", timeout=4 * 3600)

    def build(self, ctx):
        got = self.gpu_answer(ctx)
        build, why = profilemod.torch_build(got.get("cuda"),
                                            profilemod.capabilities(ctx.profile or {}))
        if not build:
            raise Failed("no build of torch can run on these GPU nodes: %s" % why)
        ctx.say("  torch build: %s (%s)" % (build, why))
        return build

    def torch(self, ctx):
        build = self.build(ctx)
        ctx.state.setdefault("receipt", {})["build"] = build
        self.pip(ctx, self.TORCH[build], "--index-url",
                 "https://download.pytorch.org/whl/%s" % build, what="torch (%s)" % build)

    def torch_done(self, ctx):
        version, cuda = torch_cuda(self.where(ctx.root))
        build = (ctx.state.get("receipt") or {}).get("build", "")
        if not version or not build:
            return False
        return cuda.replace(".", "") == build[2:]

    def boltz(self, ctx):
        try:
            self.pip(ctx, "boltz[cuda]==%s" % self.VERSION, what="Boltz-2 with its CUDA kernels")
            kernels = True
        except Failed as why:
            ctx.say("  the CUDA kernels would not install (%s); Boltz-2 without them is slower "
                    "and works, and its jobs are told so" % why)
            self.pip(ctx, "boltz==%s" % self.VERSION, what="Boltz-2")
            kernels = False
        env = self.where(ctx.root)
        version, cuda = torch_cuda(env)
        receipt = ctx.state.setdefault("receipt", {})
        receipt.update(boltz=dist_version(env, "boltz"), torch=version, cuda=cuda,
                       kernels=kernels, python=self.PYTHON)

    def boltz_done(self, ctx):
        env = self.where(ctx.root)
        return (os.path.isfile(os.path.join(env, "bin", "boltz"))
                and dist_version(env, "boltz") == self.VERSION)

    # --- the weights
    def cache(self, root):
        return os.path.join(self.where(root), "cache")

    def weights(self, ctx):
        cache = self.cache(ctx.root)
        os.makedirs(cache, exist_ok=True)
        try:
            os.remove(os.path.join(cache, self.STAMP))
        except OSError:
            pass
        for name, url, least in self.WEIGHTS:
            path = os.path.join(cache, name)
            if os.path.isfile(path) and os.path.getsize(path) >= least:
                ctx.say("  %s: already here" % name)
                continue
            ctx.fetch(url, path, name, expect_at_least=least)
        mols = os.path.join(cache, "mols")
        if not os.path.isdir(mols):
            # Unpacked into a folder of its own and then renamed, so a stop half way leaves no
            # mols/ that Boltz-2 would trust with half its components missing.
            part = os.path.join(cache, "mols.unpacking")
            shutil.rmtree(part, ignore_errors=True)
            os.makedirs(part)
            ctx.beat("unpacking the chemical components (about 45,000 files)", force=True)
            with tarfile.open(os.path.join(cache, "mols.tar")) as tar:
                count = 0
                for member in tar:
                    if not _safe(member):
                        continue
                    tar.extract(member, part)
                    count += 1
                    if count % 2000 == 0:
                        ctx.beat("unpacking the chemical components: {:,} files".format(count))
            inner = os.path.join(part, "mols")
            os.replace(inner if os.path.isdir(inner) else part, mols)
            shutil.rmtree(part, ignore_errors=True)
            ctx.say("  unpacked {:,} chemical components".format(count))
        for name, _, least in self.WEIGHTS:
            if os.path.getsize(os.path.join(cache, name)) < least:
                raise Failed("%s is smaller than it should be; Install again fetches it" % name)
        with open(os.path.join(cache, self.STAMP), "w") as handle:
            handle.write(time.ctime() + "\n")
        # Where the weights are, as the lab's own installer records it, so anything written for
        # that layout finds them here without being told.
        with open(os.path.join(self.where(ctx.root), "cache_path"), "w") as handle:
            handle.write(cache + "\n")

    def weights_done(self, ctx):
        cache = self.cache(ctx.root)
        return (os.path.isfile(os.path.join(cache, self.STAMP))
                and os.path.isdir(os.path.join(cache, "mols"))
                and all(os.path.isfile(os.path.join(cache, n))
                        and os.path.getsize(os.path.join(cache, n)) >= least
                        for n, _, least in self.WEIGHTS))

    # --- tidying
    def tidy(self, ctx):
        """The package caches, which are tens of thousands of files nobody needs afterwards."""
        if have_micromamba(ctx):
            try:
                ctx.sh([micromamba(ctx.root), "clean", "-a", "-y"], "clearing the package cache")
            except Failed as why:
                ctx.say("  the package cache was not cleared (%s); nothing depends on it" % why)
        shutil.rmtree(ctx.path("setup", "cache", "pip"), ignore_errors=True)

    # --- the check
    def check(self, ctx):
        """Fold something on the GPU queue with the page's own job script, and look at what came
        back. The same code the Boltz-2 page queues with, so a pass here is a pass there."""
        from ..folding import job as jobmod
        from ..folding import sites as sitesmod
        prof = profilemod.load(ctx.root)
        site = sitesmod.from_profile(prof) if prof else None
        site = site or sitesmod.here()
        name = "check"
        run = ctx.path("setup", "checks", self.key, time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(os.path.join(run, "inputs"))
        rows = [{"kind": "protein", "sequence": self.CHECK_SEQUENCE, "copies": 1,
                 "chains": ["A"], "name": "", "modifications": []},
                {"kind": "ion", "sequence": "MG", "copies": 1, "chains": ["B"], "name": "",
                 "modifications": []}]
        with open(os.path.join(run, "inputs", name + ".yaml"), "w") as handle:
            handle.write(jobmod.yaml_text(name, rows))
        asked = {"alignments": "single", "walltime": "00:45:00", "gpu": "auto",
                 "account": ctx.account, "diffusion_samples": 1, "recycling_steps": 1,
                 "sampling_steps": 50, "output_format": "mmcif"}
        try:
            script = jobmod.script_text(self.key, self.where(ctx.root), run, name, asked,
                                        site=site)
        except (jobmod.Refused, ValueError) as why:
            raise Failed("the check job could not be written: %s" % why)
        began = time.time()
        job = engine.submit(ctx, script, run, "the check fold")
        ctx.state["check"] = {"job": job, "run": run, "ok": False, "card": site.card(),
                              "partition": site.partition_for(), "site": site.name}
        try:
            state = engine.wait_for(ctx, job, "the check fold")
        except engine.Stopped:
            engine.cancel_job(job)
            raise
        found = sorted(glob.glob(os.path.join(run, "out", "boltz_results_" + name, "predictions",
                                              name, "*.cif")))
        log = os.path.join(run, "slurm_%s.out" % job)
        node = _node_of(log)
        ctx.state["check"].update(state=state, took=round(time.time() - began), node=node,
                                  structure=found[0] if found else "")
        if not found:
            raise Failed("the check fold (job %s, %s) wrote no structure. Its log is %s; the "
                         "last lines say: %s" % (job, state or "gone from the queue", log,
                                                 _last_lines(log)))
        scores = _confidence(os.path.join(run, "out", "boltz_results_" + name, "predictions",
                                          name))
        ctx.state["check"].update(ok=True, **scores)
        ctx.say("  folded on %s in %d min: %s" % (
            node or "a GPU node", (time.time() - began) // 60,
            ", ".join("%s %.2f" % (k, v) for k, v in scores.items()) or "no scores"))

    def check_done(self, ctx):
        check = ctx.state.get("check") or {}
        return bool(check.get("ok") and os.path.isfile(check.get("structure") or ""))


# ---------------------------------------------------------------------------- HOPE-MD

class HopeMD(Recipe):
    """HOPE-MD, installed by its own install.sh, which a terminal install runs as well.

    The public edition carries HOPE-MD's code in a folder of its own (HOPE-MD/, beside hope_labs),
    so that installing it fetches nothing from any repository: this copies that folder into the
    HOPE Labs folder and runs the script on it, which finds a copy rather than a clone and uses
    it as it lies. A checkout without the folder clones HOPE-MD's repository instead. The script
    makes the environment Prepare and the server run from; the engines are never installed,
    being the cluster's own modules, which is why HOPE-MD is offered only where its site table
    names them. The check prepares a small protein on the queue, which proves what an import on
    the login node cannot: that a compute node runs this install's Python, with OpenMM, PDBFixer
    and pdb2pqr."""

    key = "hopemd"
    name = "HOPE-MD"
    install = "HOPE-MD/MD"
    summary = ("Molecular dynamics of a complex, from Prepare to MM-GBSA and a report, on the "
               "cluster's own engines. Prepare and the page run from the environment made here.")
    disk_gb = 4
    files = 90000
    minutes = 30
    licence = ("Academic use: free for non-commercial research and teaching",
               "https://github.com/aadhilhaq/HOPE-Labs-Public/blob/main/HOPE-MD/LICENSE")
    needs = {"gpu": False}
    sources = ("conda-forge", "PyPI", "RCSB")
    check_verb = "Prepared"
    check_noun = "preparation"

    #: Where the code comes from when this checkout carries no copy: the repository, or
    #: HOPELABS_HOPEMD_SOURCE, a git URL or a folder, with "#branch" after it for another branch.
    SOURCE = "https://github.com/aadhilhaq/HOPE-MD.git"
    #: The copy the public edition carries, made by tools/make_public.py.
    BUNDLE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                          "HOPE-MD")
    #: The clusters HOPE-MD's site table (hopemd/site.py) names the queues and engine modules of,
    #: by their keys in ours (hope_labs/folding/sites.py). Elsewhere its server would fall back to
    #: Grace's, and every job would ask for a queue and modules that are not there.
    SITES = ("grace", "aces")
    _where = None

    def steps(self):
        return [
            Step("room", "Room for it", self.room, None, always=True),
            Step("micromamba", "The package manager", get_micromamba, have_micromamba),
            Step("code", "The HOPE-MD code", self.code, self.code_done),
            Step("installer", "Its environment, by its own install.sh", self.installer,
                 self.installed),
            Step("check", "A real preparation on the CPU queue", self.check, self.check_done),
        ]

    def room(self, ctx):
        Boltz2.room(self, ctx)

    def bundled(self):
        """The copy this checkout carries, or "" when it carries none."""
        return self.BUNDLE if os.path.isfile(os.path.join(self.BUNDLE, "BUNDLED")) else ""

    def not_here(self):
        """Why HOPE-MD cannot be installed on this cluster, or "" when it can.

        The cluster is told as sites.here() tells it, but from the table alone: a profile Set up
        wrote would answer for any cluster, Grace included. Asked once a process, because Slurm is
        asked and the page asks on every refresh."""
        if self._where is None:
            from ..folding import sites as sitesmod
            named = (os.environ.get("HOPELABS_SITE") or "").strip().lower()
            key = named if named in sitesmod.SITES else sitesmod.cluster_name()
            if key not in sitesmod.SITES:
                found = sitesmod.site_for_host(socket.gethostname())
                key = found.key if found else (key or socket.gethostname().split(".")[0])
            self._where = key
        if self._where in self.SITES:
            return ""
        return "HOPE-MD names the queues and engine modules of Grace and ACES only, and this is %s" \
            % self._where

    def repository(self):
        """The repository the code is cloned from, as code() reads it, without its #branch: ""
        when it is not cloned, which a checkout carrying its own copy never is."""
        if self.bundled():
            return ""
        return _clone_url(os.environ.get("HOPELABS_HOPEMD_SOURCE") or self.SOURCE)

    def owns(self, root):
        # install.sh puts the environment, and the conda base when it makes one, beside the code
        # in the HOPE-MD folder, with its caches and its audit.
        return [os.path.join(root, "HOPE-MD")]

    def code(self, ctx):
        here = self.where(ctx.root)
        part, old = here + ".part", here + ".old"
        for folder in (part, old):
            shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(os.path.dirname(here), exist_ok=True)
        source = self.bundled()
        if source:
            ctx.say("  copying the HOPE-MD this HOPE Labs carries: %s" % self._stamp(source))
            shutil.copytree(source, part, symlinks=True)
        else:
            url, _, branch = (os.environ.get("HOPELABS_HOPEMD_SOURCE") or self.SOURCE).strip().partition("#")
            if not shutil.which("git"):
                raise Failed("git is not on this login node, and the HOPE-MD code is fetched with it")
            argv = ["git", "clone"] + ([] if os.path.isdir(url) else ["--depth", "1"])
            ctx.sh(argv + (["--branch", branch] if branch else []) + [url, part],
                   "fetching the HOPE-MD code", timeout=1800)
        # An older copy is moved aside, not copied over, so nothing it had and this one has not is
        # left behind; the environment beside it is kept.
        if os.path.isdir(here):
            os.replace(here, old)
        os.replace(part, here)
        shutil.rmtree(old, ignore_errors=True)
        ctx.state.setdefault("receipt", {})["hopemd"] = self._stamp(here)

    def code_done(self, ctx):
        """Done when the code is there, and is the copy this HOPE Labs carries: an update of HOPE
        Labs brings a new copy, which Install puts in."""
        here = self.where(ctx.root)
        if not os.path.isfile(os.path.join(here, "hopemd", "__init__.py")):
            return False
        source = self.bundled()
        return not source or self._stamp(here) == self._stamp(source)

    @staticmethod
    def _stamp(folder):
        """The first line of a copy's BUNDLED: which HOPE-MD commit it is, and its date."""
        try:
            return open(os.path.join(folder, "BUNDLED"), encoding="utf-8").readline().strip()
        except OSError:
            return ""

    def installer(self, ctx):
        here = self.where(ctx.root)
        ctx.sh(["bash", os.path.join(here, "install.sh"), ctx.root],
               "install.sh: HOPE-MD's environment", timeout=4 * 3600, cwd=here)
        env = os.path.join(ctx.root, "HOPE-MD", "env")
        ctx.state.setdefault("receipt", {}).update(
            python=_version_line(os.path.join(env, "bin", "python3")),
            base=_venv_home(env))

    def installed(self, ctx):
        """Installed when the environment install.sh makes is there, on its base."""
        env = os.path.join(ctx.root, "HOPE-MD", "env")
        return os.access(os.path.join(env, "bin", "python3"), os.X_OK) and bool(_venv_home(env))

    def selfcheck(self, ctx, what, run):
        """HOPE-MD's own check (hopemd/selfcheck.py), from the install's environment as its page
        is started: what it printed, one JSON object."""
        import shlex
        line = ". ./activate.sh && python3 -m hopemd.selfcheck %s %s" % (what, shlex.quote(run))
        ctx.say("$ " + line)
        try:
            done = subprocess.run(["bash", "-c", line], cwd=self.where(ctx.root), env=ctx.env(),
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                  errors="replace", timeout=600)
        except subprocess.TimeoutExpired:
            raise Failed("HOPE-MD's check (%s) took more than ten minutes" % what)
        for text in (done.stderr or "").strip().splitlines()[-6:]:
            ctx.say("  " + text[:500])
        try:
            got = json.loads((done.stdout or "").strip().splitlines()[-1])
        except (IndexError, ValueError):
            raise Failed("HOPE-MD's check (%s) printed nothing it could read: %s"
                         % (what, (done.stderr or done.stdout or "nothing").strip()[-400:]))
        ctx.say("  " + json.dumps(got)[:500])
        return got

    def check(self, ctx):
        """Prepare Trp-cage (PDB 1L2Y) on the CPU queue, as the page's Prepare does, and look at
        what the job left."""
        run = ctx.path("setup", "checks", self.key, time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(run, exist_ok=True)
        staged = self.selfcheck(ctx, "stage", run)
        if staged.get("ok") is False:
            raise Failed("HOPE-MD could not stage its check: %s" % staged.get("why"))
        # From the check's own folder: the job works in the folder it was submitted from.
        argv = ["sbatch", "--parsable"] + (["--account=%s" % ctx.account] if ctx.account else [])
        try:
            done = subprocess.run(argv + ["prepare.sbatch"], cwd=run, env=ctx.env(),
                                  capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as why:
            raise Failed("sbatch could not be asked to queue the check preparation: %s" % why)
        found = re.search(r"^\s*(\d+)", done.stdout or "")
        if done.returncode != 0 or not found:
            raise Failed("sbatch refused the check preparation: %s"
                         % ((done.stdout or "") + (done.stderr or "")).strip()[-400:])
        job = found.group(1)
        ctx.say("  queued the check preparation as job %s" % job)
        began = time.time()
        ctx.state["check"] = {"job": job, "run": run, "ok": False,
                              "partition": staged.get("partition") or "the default"}
        try:
            state = engine.wait_for(ctx, job, "the check preparation")
        except engine.Stopped:
            engine.cancel_job(job)
            raise
        got = self.selfcheck(ctx, "read", run)
        log = os.path.join(run, "logs", "prepare.%s.out" % job)
        ctx.state["check"].update(state=state, took=round(time.time() - began),
                                  atoms=got.get("atoms", 0))
        if not got.get("ok"):
            raise Failed("the check preparation (job %s, %s) did not finish: %s. Its log is %s; "
                         "the last lines say: %s" % (job, state or "gone from the queue",
                                                     got.get("why"), log, _last_lines(log)))
        ctx.state["check"]["ok"] = True
        ctx.say("  prepared %s in %d min: %d atoms in the receptor"
                % (staged.get("pdb_id", "the check structure"), (time.time() - began) // 60,
                   got.get("atoms", 0)))

    def check_done(self, ctx):
        return bool((ctx.state.get("check") or {}).get("ok"))


def _clone_url(source):
    """A source as repository() answers it: the URL without its #branch, or "" for a folder or a
    source not fetched over the web."""
    url = source.strip().partition("#")[0]
    if os.path.isdir(url) or not re.match(r"^(https?|git)://", url):
        return ""
    return url


def _venv_home(env):
    """The base a venv was made on, from its pyvenv.cfg: "" when it is not one."""
    try:
        text = open(os.path.join(env, "pyvenv.cfg"), encoding="utf-8").read()
    except OSError:
        return ""
    found = re.search(r"^home\s*=\s*(.+)$", text, re.M)
    return found.group(1).strip() if found else ""


def _activate_value(checkout, name):
    """A variable activate.sh sets, read from the file rather than by sourcing it."""
    try:
        text = open(os.path.join(checkout, "activate.sh"), encoding="utf-8").read()
    except OSError:
        return ""
    found = re.search(r'^export %s="([^"]*)"' % re.escape(name), text, re.M)
    return found.group(1) if found else ""


def _version_of(path):
    try:
        found = re.search(r'^__version__\s*=\s*"([^"]+)"', open(path, encoding="utf-8").read(),
                          re.M)
    except OSError:
        return ""
    return found.group(1) if found else ""


def _version_line(python):
    """The Python an environment holds, from the one lib/python3.* folder with packages in it
    (there can be a python3.1 beside python3.11, holding nothing)."""
    folder = os.path.dirname(os.path.dirname(python))
    for lib in sorted(glob.glob(os.path.join(folder, "lib", "python3.*")), key=len, reverse=True):
        if os.path.isdir(os.path.join(lib, "site-packages")):
            return os.path.basename(lib).replace("python", "")
    return ""


def _first_row(table):
    try:
        lines = [l.rstrip("\n") for l in open(table, encoding="utf-8") if l.strip()]
    except OSError:
        return {}
    if len(lines) < 2:
        return {}
    return dict(zip(lines[0].split("\t"), lines[1].split("\t")))


def _newest_log(run, stage):
    found = sorted(glob.glob(os.path.join(run, "logs", "*.out")), key=os.path.getmtime)
    if not found:
        return "there is none in %s/logs" % run
    return "%s, ending: %s" % (found[-1], _last_lines(found[-1]))


def _safe(member):
    """A tar member that stays inside the folder it is unpacked into."""
    name = member.name
    return (member.isfile() or member.isdir()) and not name.startswith("/") \
        and ".." not in name.split("/")


def _node_of(log):
    try:
        text = open(log, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""
    found = re.search(r"--- node ---\s*\n(\S+)\s*\n(GPU 0: ([^(\n]+))?", text)
    if not found:
        return ""
    return found.group(1) + ((", " + found.group(3).strip()) if found.group(3) else "")


def _last_lines(path, n=3):
    try:
        lines = [l.strip() for l in open(path, encoding="utf-8", errors="replace") if l.strip()]
    except OSError:
        return "there is no log"
    return " / ".join(lines[-n:])[-400:] or "nothing"


def _confidence(folder):
    for path in sorted(glob.glob(os.path.join(folder, "confidence_*_model_0.json"))):
        try:
            doc = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        return {k: round(float(doc[k]), 3) for k in ("confidence_score", "ptm", "complex_plddt")
                if isinstance(doc.get(k), (int, float))}
    return {}


# ---------------------------------------------------------------------------- tools in an image

class FromImage(Recipe):
    """A tool installed as a container image: one file, and its weights beside it.

    An environment of this size is sixty thousand files; the image is one, which is what lets a
    tool fit in a personal scratch allowance at all. The image is pulled and the weights fetched
    here, on the login node, because a compute node on these clusters reaches nothing (measured:
    the GPU check's own HOPE-GPU-NET line says so). A job then runs the tool inside the image
    with the weights bound in and the network shut.

    A subclass names its image key (hope_labs/images.py) and says how it proves itself.
    """

    #: Room for a tool in an image: the file count is the image, its weights and the folders
    #: holding them, which is why it is tens rather than tens of thousands.
    files = 400

    def image(self):
        from .. import images as imagesmod
        return imagesmod.IMAGES[self.key]

    def where(self, root):
        from .. import images as imagesmod
        return imagesmod.image_path(root, self.key)

    def owns(self, root):
        from .. import images as imagesmod
        return [imagesmod.image_path(root, self.key), imagesmod.weights_dir(root, self.key)]

    def steps(self):
        return [
            Step("room", "Room for it", self.room, None, always=True),
            Step("image", "The image, pulled as one file", self.pull, self.pulled),
            Step("weights", "Its weights, %d GB" % self.weights_gb, self.weights,
                 self.weights_done),
            Step("check", "A real run on the GPU queue", self.check, self.check_done),
        ]

    def room(self, ctx):
        Boltz2.room(self, ctx)

    # --- the image
    def pull(self, ctx):
        from .. import images as imagesmod
        if self.image().provided():
            ctx.say("  %s is provided by this cluster at %s" % (self.name, self.image().ref))
            return
        where = imagesmod.image_path(ctx.root, self.key)
        os.makedirs(os.path.dirname(where), exist_ok=True)
        # A pull is a download and then a squashfs compression, niced (images.pull_argv), and
        # neither half can be hurried. From a Grace login node the whole pull of RFD4-Proteina's
        # 4.43 GB image took 3,295 s and BioEmu's 6.13 GB image 3,974 s, about 1.4 MB a second
        # all told. The hour and a half this had was written for five megabytes a second and left
        # BioEmu's own pull under half an hour to spare, so it is given three hours rather than a
        # figure that would cut a working pull off at the knees.
        ctx.sh(imagesmod.pull_argv(ctx.root, self.key), "pulling %s" % self.name, timeout=10800)
        os.replace(where + ".part", where)
        ctx.state.setdefault("receipt", {})["image"] = self.image().ref
        ctx.say("  %s: %s" % (os.path.basename(where), _gb(os.path.getsize(where))))

    def pulled(self, ctx):
        from .. import images as imagesmod
        return self.image().provided() or os.path.isfile(imagesmod.image_path(ctx.root, self.key))

    # --- the weights
    def weights(self, ctx):
        from .. import images as imagesmod
        where = imagesmod.weights_dir(ctx.root, self.key)
        os.makedirs(os.path.join(where, "home"), exist_ok=True)
        ctx.sh(imagesmod.fetch_argv(ctx.root, self.key), "fetching %s's weights" % self.name,
               timeout=5400)
        if not self.weights_done(ctx):
            raise Failed("the weights were fetched but %s is not in %s" % (self.wants, where))
        self.share(ctx, where)

    def share(self, ctx, where):
        """Let the rest of the group read what was just fetched.

        One person installs a tool and everybody in the group runs it, which is the whole
        arrangement in the lab's shared folder, and it only works if every file is readable by
        the group. The Hugging Face client does not cooperate: most of what it writes is 0644 and
        some of its own bookkeeping is 0600, so a colleague's job failed on one 11 kB file in a
        4 GB download. Put right here rather than left to umask, which is not consulted for a
        mode the writer sets outright.
        """
        fixed = 0
        for root, dirs, names in os.walk(where):
            for name in dirs + names:
                path = os.path.join(root, name)
                try:
                    if os.path.islink(path):
                        continue
                    mode = os.stat(path).st_mode & 0o777
                    want = mode | (0o750 if os.path.isdir(path) else 0o640)
                    if want != mode:
                        os.chmod(path, want)
                        fixed += 1
                except OSError:
                    continue            # somebody else's file: theirs to say, not ours
        if fixed:
            ctx.say("  %d file(s) and folder(s) opened to the group" % fixed)

    def weights_done(self, ctx):
        from .. import images as imagesmod
        where = imagesmod.weights_dir(ctx.root, self.key)
        return bool(glob.glob(os.path.join(where, self.wants)))

    # --- the check
    def check(self, ctx):
        """Run the tool inside its image on a GPU node, on something small, and look at what it
        wrote. The same command a job uses, so a pass here is a pass there."""
        from .. import images as imagesmod
        prof = profilemod.load(ctx.root) or {}
        use = prof.get("use") or {}
        if not use.get("gpu_partition"):
            raise Failed("the cluster's profile names no GPU partition. Open This cluster on the "
                         "Set up page and choose one.")
        run = ctx.path("setup", "checks", self.key, time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(run, exist_ok=True)
        self.prepare_check(ctx, run)
        home = os.path.join(run, "home")
        lines = imagesmod.home_lines(ctx.root, self.key, home)
        lines.append(imagesmod.line(imagesmod.exec_argv(
            ctx.root, self.key, self.check_argv(run), binds=[run], home=home)))
        # The card the cluster's profile names, as the Boltz-2 check asks for, rather than any
        # card at all: "gpu:1" can land on a 16 GB T4, which is a test of the queue, not the tool.
        from ..folding import sites as sitesmod
        site = sitesmod.from_profile(prof) or sitesmod.here()
        gpu = site.card()
        script = IMAGE_CHECK.format(
            name="hopelabs_%s_check" % self.key, partition=site.partition_for(gpu),
            gres=site.gres(gpu), out=run, cpus=4, mem=self.check_gb,
            walltime=self.check_walltime,
            account=("#SBATCH --account=%s" % ctx.account) if ctx.account else "",
            body="\n".join(lines))
        began = time.time()
        job = engine.submit(ctx, script, run, "the check run")
        ctx.state["check"] = {"job": job, "run": run, "ok": False}
        try:
            state = engine.wait_for(ctx, job, "the check run")
        except engine.Stopped:
            engine.cancel_job(job)
            raise
        log = os.path.join(run, "check_%s.out" % job)
        found = sorted(glob.glob(os.path.join(run, self.writes)))
        ctx.state["check"].update(state=state, took=round(time.time() - began),
                                  node=_node_of(log), wrote=len(found))
        if not found:
            raise Failed("the check run (job %s, %s) wrote no %s. Its log is %s; the last lines "
                         "say: %s" % (job, state or "gone from the queue", self.writes, log,
                                      _last_lines(log)))
        ctx.state["check"]["ok"] = True
        ctx.say("  %s ran on %s in %d min and wrote %d file(s)"
                % (self.name, ctx.state["check"]["node"] or "a GPU node",
                   (time.time() - began) // 60, len(found)))

    def prepare_check(self, ctx, run):
        """Put whatever the check run is asked to read into `run`, before the job is written.

        It is given the same folder the job will use rather than making its own, because the two
        have to agree: a tool that wrote its input under one timestamp and was then run under the
        next would be handed a path to nothing."""

    def check_done(self, ctx):
        return bool((ctx.state.get("check") or {}).get("ok"))


#: A check job for a tool in an image: one card, the tool's own command inside the image.
IMAGE_CHECK = """#!/bin/bash
#SBATCH --job-name={name}
#SBATCH --partition={partition}
#SBATCH --gres={gres}
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task={cpus}
#SBATCH --mem={mem}G
#SBATCH --time={walltime}
#SBATCH --output={out}/check_%j.out
{account}
set -uo pipefail
# In the check's own folder, not wherever this was submitted from. A tool writes its working
# files beside the job rather than beside its output: RFD4-Proteina's Hydra wrote a
# hydra_outputs/ folder of logs and resolved configs into the HOPE Labs checkout, because that
# is where Set up was run from.
cd {out} || exit 1
echo '--- node ---'
hostname
nvidia-smi -L 2>&1 | head -2 || echo 'no nvidia-smi on this node'
echo '--- running it in its image ---'
{body}
status=$?
echo "the check run finished with status $status"
exit $status
"""


class RFD4(FromImage):
    key = "rfd4"
    name = "RFD4-Proteina"
    summary = ("Designs binders, enzymes and nucleic-acid binders from one model, by what has to "
               "be in the right place. Needs a GPU partition.")
    disk_gb = 20
    weights_gb = 2
    #: Measured on Grace: the image took 3,295 s to pull (4.43 GB), the weights 44 s (1.7 GB in
    #: 33 files), and the check 13 min in the queue and 2 min running, about 70 min in all.
    minutes = 70
    licence = ("BSD 3-Clause", "https://github.com/RosettaCommons/RFD4-Proteina/blob/main/LICENSE")
    needs = {"gpu": True, "gpu_memory_gb": 24}
    sources = ("GitHub's registry", "Hugging Face")
    check_verb = "Designed"
    check_noun = "design"
    #: What the weights step must leave: the model bundle, under Hugging Face's own layout.
    wants = "hf/hub/models--nvidia--RFD4-Proteina/snapshots/*/model.safetensors"
    #: What the check must write. The suffix is left open because the model writes its designs
    #: compressed or plain, depending on how it was asked.
    writes = "out/inference_samples/*/*_generated.cif*"
    check_gb = 32
    check_walltime = "01:00:00"

    def check_argv(self, run):
        """One short monomer: the smallest run that exercises the model end to end.

        `inference_experiment=quickstart` is the preset the tool's own first run uses. It composes
        no metrics, so nothing refolds the design and no oracle has to be installed to prove that
        the model runs, and it skips the torch.compile warm-up, which costs minutes and pays for
        itself only over many designs.

        Nothing else is overridden. The sampling steps and the recycling schedule are one setting
        in two halves: a schedule is written for a number of steps, and asking for fewer steps
        than the schedule expands to is refused outright (recycle schedule expands to 60 steps;
        expected 20). The preset's own pair is quick enough, and a check is not the place to go
        shaving minutes off the thing being checked."""
        return ["rfd4-generate", "inference_experiment=quickstart",
                "inputs=%s" % os.path.join(run, "check.yaml"),
                "out_dir=%s" % os.path.join(run, "out")]

    def prepare_check(self, ctx, run):
        """Ask for an unconditioned chain, by pointing at one the image carries.

        A condition spec cannot start from nothing: every contig has to hold a segment that is
        preserved, so "40-40" on its own is refused by the grammar. A monomer is asked for the way
        the tool's own cookbook asks for one, with an annotated CIF holding a single expandable
        chain, and the image ships three of them."""
        with open(os.path.join(run, "check.yaml"), "w") as handle:
            handle.write("monomer:\n  input: %s\n  input_as_annotated_cif: true\n"
                         % self.monomer_cif(ctx))

    def monomer_cif(self, ctx):
        """Where the short monomer benchmark is inside the image.

        Read out of the installed package rather than written down here, because it travels with
        the package and not with this recipe."""
        from .. import images as imagesmod
        argv = imagesmod.exec_argv(ctx.root, self.key, [
            "python", "-c", "import os, rfproteina; print(os.path.join(os.path.dirname("
                            "rfproteina.__file__), 'data', 'benchmarks', 'monomer', "
                            "'monomer-short.cif.zst'))"], gpu=False)
        done = imagesmod.run(argv, ctx.root, timeout=600)
        # The image prints its own notices about mirrors it has not been given on the way past, so
        # the path is the line that looks like one rather than the whole of what it said.
        found = [l.strip() for l in (done.stdout or "").splitlines()
                 if l.strip().endswith(".cif.zst")]
        if not found:
            raise Failed("could not find the monomer benchmark inside %s. It said: %s"
                         % (self.name, (done.stderr or done.stdout or "nothing").strip()[-400:]))
        return found[-1]


class BioEmu(FromImage):
    key = "bioemu"
    name = "BioEmu"
    summary = ("Samples the structures a protein actually visits, from its sequence, in minutes "
               "rather than the days a simulation of the same spread would take. Needs a GPU "
               "partition.")
    disk_gb = 14
    weights_gb = 4
    #: Measured on Grace: the image alone took 3,974 s to pull (6.13 GB), the weights 25 s
    #: (3.6 GB in 47 files) and the check 2 min, so about 70 min before any wait in the queue.
    #: It said 45, which the pull by itself outlasts.
    minutes = 75
    licence = ("MIT", "https://github.com/microsoft/bioemu/blob/main/LICENSE")
    needs = {"gpu": True, "gpu_memory_gb": 16}
    sources = ("GitHub's registry", "Hugging Face", "ColabFold")
    check_verb = "Sampled"
    check_noun = "sampling"
    #: What the weights step must leave: BioEmu's own checkpoint. AlphaFold2's parameters, which
    #: its embeddings are made with, are fetched beside it and checked by the run itself.
    wants = "hf/hub/models--microsoft--bioemu/snapshots/*/checkpoints/bioemu-v1.1/checkpoint.ckpt"
    writes = "out/samples.xtc"
    check_gb = 24
    check_walltime = "01:00:00"
    #: A short, well-studied peptide: chignolin, which BioEmu's own examples sample.
    CHECK_SEQUENCE = "YYDPETGTWY"

    def check_argv(self, run):
        """Four structures of chignolin, sampled from an alignment of one sequence.

        The sequence is handed over as a one-line .a3m because that is the form BioEmu takes
        without asking ColabFold's server for an alignment: a compute node has no way out to the
        network, and a check given the bare sequence sits there retrying api.colabfold.com until
        its walltime runs out. A real run fetches a real alignment first, on a login node, and
        hands the sampling the same kind of file."""
        return ["python", "-m", "bioemu.sample",
                "--sequence", os.path.join(run, "check.a3m"),
                "--num_samples", "4", "--batch_size_100", "4",
                "--output_dir", os.path.join(run, "out"),
                "--model_name", "bioemu-v1.1",
                "--cache_embeds_dir", os.path.join(run, "cache", "embeds"),
                "--filter_samples", "False"]

    def prepare_check(self, ctx, run):
        with open(os.path.join(run, "check.a3m"), "w") as handle:
            handle.write(">check\n%s\n" % self.CHECK_SEQUENCE)


RECIPES = {recipe.key: recipe for recipe in (Boltz2(), HopeMD(), RFD4(), BioEmu())}


# ---------------------------------------------------------------------------- can it be fetched

#: How long the login node may take to say whether a repository can be read. git answers in a
#: fraction of a second (measured from a Grace login node: 0.2 s to GitHub, for a public
#: repository and a private one alike); one that hangs is given up on after this, and the tool is
#: offered as if nothing had been asked.
ASK_SECONDS = 10

#: What each repository said, asked once a session: {url: [answered, (readable, why not)]}.
_ASKED = {}
_ASKING = threading.Lock()

#: What git says of a repository only someone signed in may read. GitHub answers a private
#: repository and one that does not exist alike, by asking for a username, which a question with
#: prompts switched off turns into the first of these.
_SIGN_IN = re.compile(r"terminal prompts disabled|could not read (username|password)|"
                      r"authentication failed|repository not found|returned error: 40[134]", re.I)


def readable(url, root="", timeout=ASK_SECONDS):
    """(whether the git repository at `url` can be read without credentials, why not).

    Asked of this login node with git ls-remote, the way the install's own clone would meet it
    (engine.Context.env): no terminal to prompt on and no stored credentials, with the install's
    own home and the cluster's web proxy, so a person's ~/.gitconfig cannot make a repository
    readable here that the install then cannot clone. True or False when git said which; None
    when it could not be asked, which claims nothing either way.
    """
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="", SSH_ASKPASS="",
               GCM_INTERACTIVE="never", GIT_SSH_COMMAND="ssh -o BatchMode=yes")
    if root:
        env["HOME"] = os.path.join(setup_dir(root), "home")
        proxy = profilemod.proxy(root)
        if proxy:
            for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
                env[name] = proxy
    try:
        # A session of its own, so a git that hangs is stopped with everything it started.
        proc = subprocess.Popen(["git", "-c", "credential.helper=", "ls-remote", url, "HEAD"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, env=env, text=True, errors="replace",
                                start_new_session=True)
    except OSError:
        return None, "git is not on this login node"
    try:
        _out, said = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.wait()
        return None, "no answer in %d s" % timeout
    if proc.returncode == 0:
        return True, ""
    if _SIGN_IN.search(said or ""):
        return False, "its code is not public yet"
    lines = [line.strip() for line in (said or "").splitlines() if line.strip()]
    return None, (lines[-1][:200] if lines else "git said nothing")


def fetchable(recipe, root="", wait=0):
    """Whether Set up can fetch a recipe's code here: {available, asking, why_not}. A tool that
    cannot run on this cluster at all (Recipe.not_here) is not available either, whatever its code."""
    why = recipe.not_here()
    if why:
        return {"available": False, "asking": False, "why_not": why}
    url = recipe.repository()
    if not url:
        return {"available": True, "asking": False, "why_not": ""}
    with _ASKING:
        entry = _ASKED.get(url)
        if entry is None:
            entry = _ASKED[url] = [threading.Event(), (None, "")]
            threading.Thread(target=_ask, args=(url, root, entry), daemon=True).start()
    if wait:
        entry[0].wait(wait)
    ok, why = entry[1]
    return {"available": ok is not False, "asking": not entry[0].is_set(),
            "why_not": why if ok is False else ""}


def _ask(url, root, entry):
    try:
        entry[1] = readable(url, root)
    finally:
        entry[0].set()


def ask_early(root=""):
    """Start every question fetchable() will be asked, so the answers are in by the time the page
    first asks for its rows."""
    for recipe in RECIPES.values():
        fetchable(recipe, root)
