"""Tools that run from a container image: where the image and its weights live, how they are
fetched, and the line that runs the tool inside the image.

An image is one file however much is installed in it, which is what lets a tool cost the file
quota one entry where its Python environment would cost tens of thousands. The cluster pulls it
and runs it; it cannot build one (images/README.md says where they are built). Nothing here needs
root, and nothing here writes into the home folder: the registry's download cache and the
temporary folder are both under the HOPE Labs folder.

Layout, under the HOPE Labs folder (the folder holding the HOPE-labs checkout):

    images/<file>.sif            the image, one file
    weights/<key>/               the tool's weights and caches, fetched on a login node
    setup/cache/singularity/     the registry's download cache, emptied after each pull

A compute node has no network on the clusters this is for, so everything a run needs from the
internet is fetched first, here, on a login node, and the job runs with the network shut
(HF_HUB_OFFLINE): a weight that was not fetched is a clear failure, not a hang.
"""
import os
import posixpath
import re
import shlex
import shutil
import subprocess


class Image:
    """One tool's image.

    `ref` is what is pulled (docker://...), or the path of an image the cluster already provides,
    which is used where it lies and never copied. `fetch` is the Python run inside the image, on a
    login node, that puts the weights in {weights}; `env` is what a job sets inside the image.
    """

    def __init__(self, key, name, ref, file, gpu=True, gb=0, fetch="", env=None, binds=(),
                 links=None):
        self.links = dict(links or {})
        self.key = key
        self.name = name
        self.ref = ref
        self.file = file
        self.gpu = gpu
        self.gb = gb
        self.fetch = fetch
        self.env = dict(env or {})
        self.binds = tuple(binds)

    def provided(self):
        """Whether the cluster provides the image itself, as a path, rather than one pulled."""
        return self.ref.startswith("/")


IMAGES = {
    "rfd4": Image(
        "rfd4", "RFD4-Proteina",
        "docker://ghcr.io/aadhilhaq/hope-labs-rfd4-proteina:0.0.3-cu12",
        "rfd4-proteina_0.0.3-cu12.sif", gpu=True, gb=12,
        # Two files out of that repository and no more: the model bundle rfd4-generate loads is
        # config.json and model.safetensors (1.7 GB together), and the rest of nvidia/RFD4-Proteina
        # is 104 GB of refolding oracles, benchmarks and runtime images that generation never
        # opens. The revision is the one this image pins in its own inference.yaml rather than a
        # number written down here twice: the model is loaded at that revision, so it is the
        # revision that has to be in the cache for an offline run to find it.
        fetch="import pathlib, re, rfproteina; from huggingface_hub import snapshot_download; "
              "cfg = (pathlib.Path(rfproteina.__file__).parent / 'configs' / 'inference.yaml')"
              ".read_text(); "
              "rev = re.search(r'^hf_ckpt_revision:\\s*(\\S+)', cfg, re.M).group(1); "
              "print(snapshot_download('nvidia/RFD4-Proteina', revision=rev, "
              "cache_dir='{weights}/hf/hub', "
              "allow_patterns=['config.json', 'model.safetensors']))",
        env={"HF_HOME": "{weights}/hf", "HF_HUB_OFFLINE": "1", "DATA_PATH": "{weights}/data"}),
    "bioemu": Image(
        "bioemu", "BioEmu",
        "docker://ghcr.io/aadhilhaq/hope-labs-bioemu:1.4.1",
        "bioemu_1.4.1.sif", gpu=True, gb=8,
        # BioEmu 1.1's checkpoint and AlphaFold2's parameters, which its embeddings are made with
        # (about 3.5 GB), each where BioEmu looks for it.
        fetch="from huggingface_hub import hf_hub_download as get; "
              "[print(get('microsoft/bioemu', 'checkpoints/bioemu-v1.1/' + n, "
              "cache_dir='{weights}/hf/hub')) for n in ('checkpoint.ckpt', 'config.yaml')]; "
              "from pathlib import Path; "
              "from bioemu.colabfold_inline.model_runner import download_alphafold_params as af; "
              "print(af(Path('{weights}/colabfold')))",
        # BioEmu reads AlphaFold2's parameters from ~/.cache/colabfold and nowhere else, so a job
        # links that to these, in the home folder it gives the image (job_lines).
        links={".cache/colabfold": "{weights}/colabfold"},
        env={"HF_HOME": "{weights}/hf", "HF_HUB_OFFLINE": "1"}),
}


def image_path(root, key):
    img = IMAGES[key]
    return img.ref if img.provided() else posixpath.join(root, "images", img.file)


def root_of(install):
    """The HOPE Labs folder that holds this image file.

    A tool in an image is installed as one file, so the install a page is handed is that file and
    not a folder. Everything else the run needs - the weights, the caches, the other images - is
    beside it under the same folder, which is the one Set up was pointed at. Taken from the path
    rather than asked for a second time: a page told where the image is has been told where the
    folder is, and two settings that have to agree are one setting too many.
    """
    return os.path.dirname(os.path.dirname(os.path.abspath(str(install or "."))))


def weights_dir(root, key):
    return posixpath.join(root, "weights", key)


def singularity():
    """The container runtime on this machine: apptainer or singularity, whichever is here."""
    for name in ("apptainer", "singularity"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def runtime_env(root):
    """Where the runtime keeps its downloads and its scratch: never the home folder."""
    cache = posixpath.join(root, "setup", "cache", "singularity")
    tmp = posixpath.join(root, "setup", "cache", "singularity-tmp")
    return {"SINGULARITY_CACHEDIR": cache, "APPTAINER_CACHEDIR": cache,
            "SINGULARITY_TMPDIR": tmp, "APPTAINER_TMPDIR": tmp}


def pull_argv(root, key):
    """The command that pulls the image, to a part file renamed once it is whole.

    Run at the lowest priority there is. A pull is two halves: forty minutes of download, which
    costs a login node nothing, and then twenty-odd minutes of squashfs compression, which is
    real work on the one machine everybody shares. It cannot be moved to a compute node, because
    a compute node here reaches no registry, and it cannot be skipped: the mode that keeps the
    layers as they came needs a privilege this cluster does not grant (measured: `--oci` stops at
    "could not use fakeroot"). So it is niced instead, and gives way to anything anyone else is
    doing.
    """
    img = IMAGES[key]
    if img.provided():
        raise ValueError("%s is provided by the cluster at %s and is not pulled" % (img.name, img.ref))
    argv = [singularity() or "singularity", "pull", "--force", image_path(root, key) + ".part",
            img.ref]
    return (["nice", "-n", "19"] + argv) if shutil.which("nice") else argv


def _fill(text, root, key):
    return text.replace("{weights}", weights_dir(root, key))


def env_args(root, key, extra=None):
    """--env NAME=value for each variable the tool runs with inside the image."""
    env = dict((k, _fill(v, root, key)) for k, v in IMAGES[key].env.items())
    env.update(extra or {})
    out = []
    for name in sorted(env):
        if not re.match(r"^[A-Z_][A-Z0-9_]*$", name):
            raise ValueError("%r is not a variable name" % name)
        out += ["--env", "%s=%s" % (name, env[name])]
    return out


def exec_argv(root, key, argv, binds=(), extra_env=None, gpu=None, home=None):
    """The command that runs `argv` inside the tool's image.

    The weights folder and every folder in `binds` are bound in at the same path, so a path means
    the same thing inside and out. The host's environment is not carried in (--cleanenv): a
    PYTHONPATH or a conda variable from the person's shell would otherwise reach a Python that has
    nothing to do with it. The GPU is passed through where the tool uses one.

    The home folder inside is `home`, never the person's own: the runtime would otherwise mount
    the real one, and every cache the tools keep under ~ (matplotlib's, Triton's, the CUDA
    driver's, AlphaFold2's parameters) would land in a folder with a few thousand files to spare.
    A job gives one in its run folder; a fetch on the login node uses the weights folder's.
    """
    img = IMAGES[key]
    use_gpu = img.gpu if gpu is None else gpu
    home = home or posixpath.join(weights_dir(root, key), "home")
    out = [singularity() or "singularity", "exec", "--cleanenv", "--home", home]
    if use_gpu:
        out.append("--nv")
    seen = []
    for folder in (weights_dir(root, key),) + tuple(binds):
        folder = os.path.abspath(folder)
        if folder not in seen:
            seen.append(folder)
            out += ["--bind", folder]
    out += env_args(root, key, extra_env)
    out.append(image_path(root, key))
    return out + list(argv)


def home_lines(root, key, home):
    """The shell lines that make a job's home folder inside the image: the folder, and the links
    a tool needs in it to find what was fetched."""
    out = ["mkdir -p %s" % shlex.quote(home)]
    for rel, target in sorted(IMAGES[key].links.items()):
        path = posixpath.join(home, rel)
        out.append("mkdir -p %s && ln -sfn %s %s" % (
            shlex.quote(os.path.dirname(path)), shlex.quote(_fill(target, root, key)),
            shlex.quote(path)))
    return out


def fetch_argv(root, key):
    """The command that fetches the weights, run inside the image on a login node."""
    img = IMAGES[key]
    return exec_argv(root, key, ["python", "-c", _fill(img.fetch, root, key)], gpu=False,
                     extra_env={"HF_HUB_OFFLINE": "0"})


def line(argv):
    return " ".join(shlex.quote(str(a)) for a in argv)


def run(argv, root, timeout=3600):
    """Run one of these commands here, with the runtime's caches out of the home folder."""
    env = dict(os.environ, **runtime_env(root))
    for path in (env["SINGULARITY_CACHEDIR"], env["SINGULARITY_TMPDIR"]):
        os.makedirs(path, exist_ok=True)
    return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout)
