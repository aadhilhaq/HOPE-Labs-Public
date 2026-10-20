"""What installing each tool means, step by step: the recipes the engine follows.

By Aadhil Haq

A recipe is a tool's install written down as steps, each of which can say whether it has already
been done. The engine (engine.py) runs them in order and the Set up page shows them, so a recipe
is also the page's description of the tool: what it needs from the cluster, how much room it
takes, where its downloads come from and under what licence.

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
import tarfile
import time

from . import engine
from . import profile as profilemod
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

    def where(self, root):
        return os.path.join(root, self.install)

    def steps(self):
        return []

    def as_json(self, root):
        return {"key": self.key, "name": self.name, "summary": self.summary,
                "install": self.where(root), "disk_gb": self.disk_gb, "files": self.files,
                "minutes": self.minutes,
                "licence": {"name": self.licence[0], "url": self.licence[1]},
                "needs": dict(self.needs), "sources": list(self.sources),
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


# ---------------------------------------------------------------------------- ADCP docking

class ADCP(Recipe):
    """ADCP docking, installed by its own install.sh, so that the page and a terminal install
    the same way: the repository's script finds or builds the environment (micromamba where the
    cluster has no conda), installs the ADCP 1.1 suite with the Scripps Research Institute's own
    recipe, each Scripps package checked against a pinned checksum, and writes the activate.sh
    the launcher's start line sources, ADCPSUITE_HOME among it. This recipe only fetches the
    code, runs that script into the HOPE Labs folder, and proves the result with a docking on the
    queue."""

    key = "adcp"
    name = "ADCP docking"
    install = "ADCP_docking"
    summary = ("Docks peptides into a receptor with AutoDock CrankPep, and rescores the poses "
               "with MM-GBSA. Runs on CPU nodes.")
    disk_gb = 5
    files = 62000
    minutes = 30
    #: The suite is named because HOPE Labs itself calls it: the structure viewer's surfaces are
    #: computed by MSMS through its MSMS library, mslib, with radii from its MolKit, whose
    #: MGLTOOLS licence is for non-commercial use.
    licence = ("BSD 3-Clause; parts of the ADCP 1.1 suite it installs, MolKit among them, are for "
               "non-commercial use",
               "https://github.com/aadhilhaq/ADCP_docking/blob/main/THIRD_PARTY_NOTICES.md")
    needs = {"gpu": False}
    sources = ("GitHub", "conda-forge", "PyPI", "Scripps")

    #: Where the code comes from: the repository, or HOPELABS_ADCP_SOURCE, which takes a git
    #: URL or a folder, with "#branch" after it for a branch other than the default.
    SOURCE = "https://github.com/aadhilhaq/ADCP_docking.git"
    #: The check: p53's peptide into MDM2, the structure the package's own example ships, with a
    #: search far too small to mean anything and big enough to run every stage, MM-GBSA included.
    CHECK_PEPTIDE = "p53: ETFSDLWKLLP"
    CHECK_SITE = "A:54,A:62,A:67,A:96,A:99"

    def steps(self):
        return [
            Step("room", "Room for it", self.room, None, always=True),
            Step("micromamba", "The package manager", get_micromamba, have_micromamba),
            Step("code", "The ADCP docking code", self.code, self.code_done),
            Step("installer", "Its environment and the ADCP 1.1 suite, by its own install.sh",
                 self.installer, self.installed),
            Step("check", "A real docking on the CPU queue", self.check, self.check_done),
        ]

    def room(self, ctx):
        Boltz2.room(self, ctx)

    def checkout(self, root):
        return self.where(root)

    def code(self, ctx):
        source = (os.environ.get("HOPELABS_ADCP_SOURCE") or self.SOURCE).strip()
        url, _, branch = source.partition("#")
        part = self.checkout(ctx.root) + ".part"
        shutil.rmtree(part, ignore_errors=True)
        argv = ["git", "clone", "--depth", "1"] + (["--branch", branch] if branch else [])
        if os.path.isdir(url):
            argv = ["git", "clone"] + (["--branch", branch] if branch else [])
        if not shutil.which("git"):
            raise Failed("git is not on this login node, and the ADCP docking code is fetched with it")
        ctx.sh(argv + [url, part], "fetching the ADCP docking code", timeout=1800)
        os.replace(part, self.checkout(ctx.root))

    def code_done(self, ctx):
        here = self.checkout(ctx.root)
        return (os.path.isfile(os.path.join(here, "install.sh"))
                and os.path.isfile(os.path.join(here, "adcp_dock", "__init__.py")))

    def installer(self, ctx):
        here = self.checkout(ctx.root)
        ctx.sh(["bash", os.path.join(here, "install.sh"), ctx.root],
               "install.sh: the environment and the ADCP 1.1 suite", timeout=4 * 3600, cwd=here)
        env = os.path.join(ctx.root, "envs", "adcp")
        receipt = ctx.state.setdefault("receipt", {})
        receipt.update(adcp_dock=_version_of(os.path.join(here, "adcp_dock", "__init__.py")),
                       python=_version_line(os.path.join(env, "bin", "python3")),
                       ambertools=os.path.isfile(os.path.join(env, "bin", "tleap")),
                       adcpsuite=_activate_value(here, "ADCPSUITE_HOME"))

    def installed(self, ctx):
        """Installed when activate.sh names the ADCP 1.1 suite, with its adcp, and a Python."""
        here = self.checkout(ctx.root)
        suite = _activate_value(here, "ADCPSUITE_HOME")
        python = _activate_value(here, "ADCP_PYTHON")
        return bool(suite and python and os.path.isfile(os.path.join(suite, "bin", "adcp"))
                    and os.access(python, os.X_OK))

    def check(self, ctx):
        """Dock a peptide on the queue with the package's own command line, every stage of it,
        and look at the table it leaves."""
        import shlex
        here = self.checkout(ctx.root)
        run = ctx.path("setup", "checks", self.key, time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(os.path.dirname(run), exist_ok=True)
        use = ((profilemod.load(ctx.root) or {}).get("use") or {})
        receptor = os.path.join(here, "adcp_dock", "example_run", "receptor", "1YCR.pdb")
        new = ['"$ADCP_PYTHON"', "-m", "adcp_dock.cli", "new", shlex.quote(run),
               "-r", shlex.quote(receptor), "--chain", "A", "-s", self.CHECK_SITE,
               "-p", shlex.quote(self.CHECK_PEPTIDE), "--poses", "2", "--replicas", "4",
               "--steps", "200000", "--cpus", "4", "--time", "00:40:00",
               "--mmgbsa", "--mmgbsa-poses", "1"]
        if use.get("cpu_partition"):
            new += ["--partition", shlex.quote(use["cpu_partition"])]
        if ctx.account:
            new += ["--account", shlex.quote(ctx.account)]
        line = (". ./activate.sh && %s && \"$ADCP_PYTHON\" -m adcp_dock.cli run %s"
                % (" ".join(new), shlex.quote(run)))
        began = time.time()
        ctx.sh(["bash", "-c", line], "queueing the check docking", timeout=1800, cwd=here)
        try:
            jobs = json.load(open(os.path.join(run, "job.json"), encoding="utf-8"))["jobs"]
        except (OSError, ValueError, KeyError):
            raise Failed("the check docking was not queued: %s has no job ids" % run)
        ctx.state["check"] = {"run": run, "jobs": jobs, "ok": False,
                              "partition": use.get("cpu_partition") or "the default"}
        for stage in ("prepare", "dock", "report"):
            try:
                state = engine.wait_for(ctx, jobs[stage], "the check docking, %s" % stage)
            except engine.Stopped:
                for job in jobs.values():
                    engine.cancel_job(job)
                raise
            if state not in ("COMPLETED", ""):
                for later in ("dock", "report"):
                    if later != stage and jobs.get(later):
                        engine.cancel_job(jobs[later])
                raise Failed("the check docking's %s job (%s) ended %s. Its log: %s"
                             % (stage, jobs[stage], state, _newest_log(run, stage)))
        table = os.path.join(run, "results.tsv")
        row = _first_row(table)
        if not row or not row.get("adcp_best"):
            raise Failed("the check docking finished without a score in %s. Its log: %s"
                         % (table, _newest_log(run, "report")))
        ctx.state["check"].update(ok=True, took=round(time.time() - began), table=table,
                                  peptide=row.get("name", ""), adcp_best=row.get("adcp_best"),
                                  mmgbsa_best=row.get("mmgbsa_best", ""))
        ctx.say("  docked %s in %d min: ADCP %s, MM-GBSA %s kcal/mol" % (
            row.get("name"), (time.time() - began) // 60, row.get("adcp_best"),
            row.get("mmgbsa_best") or "not run"))

    def check_done(self, ctx):
        check = ctx.state.get("check") or {}
        return bool(check.get("ok") and os.path.isfile(check.get("table") or ""))


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


RECIPES = {recipe.key: recipe for recipe in (Boltz2(), ADCP())}
