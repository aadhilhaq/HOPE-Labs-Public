# BioEmu 1.4.1 (Microsoft Research, MIT), for HOPE Labs: biomolecular emulation, an ensemble of a
# protein's structures sampled from its sequence. Code and environment only; the BioEmu checkpoints
# and AlphaFold2's parameters are fetched on a login node into the HOPE Labs folder and bound in.
#
# Run with the host driver, offline, with the alignment and the weights given to it:
#   singularity exec --nv -B <weights>:/weights -B <run>:<run> hope-labs-bioemu.sif \
#     python -m bioemu.sample --sequence <run>/x.a3m --num_samples 100 --output_dir <run>/out ...
FROM python:3.12.12-slim-bookworm

ARG BIOEMU_VERSION=1.4.1

ENV PYTHONNOUSERSITE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_OFFLINE=1 \
    MPLBACKEND=Agg

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# The pip in the base image bundles a certifi that still trusts the CA withdrawn in October 2026,
# and pip trusts its own bundle beside the system store. The fixed pip is installed from a
# hash-pinned URL, so this one download is checked by checksum rather than by that store, and
# certifi is held above the fixed release for everything after it. RFD4-Proteina's own image does
# the same, for the same reason.
RUN python -m pip install --no-cache-dir "pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70" && \
    printf 'certifi>=2026.10.19\n' > /tmp/constraints.txt && \
    python -m pip install --no-cache-dir -c /tmp/constraints.txt "bioemu[cuda]==${BIOEMU_VERSION}" && \
    rm -f /tmp/constraints.txt && \
    python -m pip list 2>/dev/null | grep -iE '^(bioemu|torch|jax|certifi) '

# Proof that it imports, and that its command answers, before anything is pushed.
RUN python -c "import bioemu.sample, jax, torch; print('bioemu imports; jax', jax.__version__, 'torch', torch.__version__)" && \
    python -m bioemu.sample --help > /dev/null

WORKDIR /work
