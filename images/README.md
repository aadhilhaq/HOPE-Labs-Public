# Tool images

Container images of tools HOPE Labs runs, built from this repository by
`.github/workflows/images.yml` and published as public packages on GitHub's registry. A
cluster pulls one as a single file:

    singularity pull hope-labs-rfd4-proteina.sif docker://ghcr.io/aadhilhaq/hope-labs-rfd4-proteina:cu12

so a tool costs one file of the file quota rather than the tens of thousands its Python
environment would. The Set up page pulls them and removes them; nothing here needs root.

| Image | Tool | Version | Licence | Built from |
|---|---|---|---|---|
| `hope-labs-rfd4-proteina:0.0.3-cu12` | RFD4-Proteina | 0.0.3 | BSD-3-Clause | the tool's own Dockerfile, RosettaCommons/RFD4-Proteina at aaccdd2 |
| `hope-labs-bioemu:1.4.1` | BioEmu | 1.4.1 | MIT | `bioemu/Dockerfile`, from PyPI |

## What is not in them

No model weights. Each tool's weights are fetched by the Set up page, on a login node, into the
HOPE Labs folder, from where the tool's authors publish them, under their terms, and bound into
the container when a job runs. Compute nodes need no network.

## The software in them

Each image holds the tool and the open-source packages it installs, under their own licences;
the tool's licence and notices are inside the image where its installation puts them. These
images are built and published for convenience: the tools belong to their authors, and a
publication should cite them, not these images.
