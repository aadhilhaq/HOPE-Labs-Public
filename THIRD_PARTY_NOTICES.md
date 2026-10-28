# Third-party notices

HOPE Labs itself is not open source, and no license to it is granted; see [LICENSE](LICENSE). This notice
lists the software that is not ours: what travels inside the launcher's builds, what a page loads
in the browser, the program the viewer runs on the cluster, and the tools the launcher starts
there. The launcher distributes neither that program nor those tools: each is installed on the cluster
and keeps its own license, which its own notices state.

## Bundled into the launcher's builds

These travel inside the Windows and macOS downloads, so their licenses travel with them.

| Library | Owner | License | How it is used |
|---|---|---|---|
| CPython 3.12 | Python Software Foundation | PSF License Agreement | The runtime PyInstaller bundles, pinned in the build workflow |
| paramiko | Jeff Forcier and contributors | LGPL-2.1-or-later | The single SSH connection to the cluster, and every forwarded port |
| cryptography | The cryptography developers | Apache-2.0 or BSD-3-Clause, at your choice | Paramiko's key handling and ciphers |
| bcrypt | The Python Cryptographic Authority | Apache-2.0 | Paramiko's key derivation |
| PyNaCl | The Python Cryptographic Authority | Apache-2.0 | Paramiko's Ed25519 keys |

`.github/workflows/desktop.yml` installs paramiko unpinned, so the version in a download is the
latest release at the time that build ran; `BUILD_VERSION` in the bundle names the commit, and the
launcher's `--selftest` prints the paramiko it found.

## Used to make the builds, and not shipped in them

| Tool | License | What it does |
|---|---|---|
| PyInstaller | GPL-2.0-or-later, with the bootloader exception that allows bundling software under other terms | Makes the Windows folder and the macOS app |
| Pillow | MIT-CMU | Draws the application icon in `docs/brand/make_icon.py` |

## Loaded by a page in the browser

The folding pages are served from a login node through a forwarded port, so a browser on them can
fetch from the page and nowhere else. Both viewers are therefore copies in the repository, under
`hope_labs/folding/web/vendor/`, with the license texts of the libraries inside each minified
bundle beside it. They are served by the page on the cluster; they are not in the launcher's
downloads.

| Library | Owner | Version | License |
|---|---|---|---|
| 3Dmol.js | University of Pittsburgh and contributors | 2.5.5 | BSD-3-Clause, with the GLmol, Three.js and jQuery notices: `LICENSE-3Dmol.txt` |
| Mol* | The Mol* contributors | 4.9.0 | MIT: `LICENSE-molstar.txt` |

The launcher's own structure viewer, under Results and on a flow's cards, reads the same copy of
3Dmol.js from the cluster over its connection.

## Run on the cluster for the viewer

The viewer's surfaces are computed by MSMS, through mslib, the MSMS library in the ADCP 1.1 suite.
The launcher asks for one over its connection, and the HOPE Labs checkout on the cluster computes
it there (`hope_labs/msms.py`, through `hope_labs/surface.py`, the module it shares with HOPE-MD,
and `hope_labs/msms_mslib.py`, which the
suite's own Python runs). Only the triangulated surface comes back to the page.

| Program | Owner | License | How it is used |
|---|---|---|---|
| MSMS, through mslib, the MSMS library in the ADCP 1.1 suite | Michel Sanner, Scripps Research Institute (MSMS); sannerlab (mslib) | mslib's own license file is the BSD 2-Clause License, "Copyright (c) 2022, sannerlab". An `msms` program named by `MSMS_EXE` keeps its own terms, for non-commercial scientific research only: "You are free to use the program (MSMS) ONLY FOR NON-COMMERCIAL SCIENTIFIC RESEARCH. This license is issued to you as an individual. For institution wide use or commercial use of the software you will have to follow the official registration procedure." They ask that published work using MSMS cite Sanner, Olson and Spehner, Biopolymers 38:305 to 320, 1996. | mslib is called from the ADCP 1.1 suite as installed, wherever the cluster has it, with the suite's own Python; the suite is never copied into this repository and never distributed with the launcher. MSMS is given the radii of MolKit's copy of `pdb_to_xyzr`'s table, read from that suite as data (MolKit is under the MGLTOOLS license, for non-commercial use). Where the suite is absent, or MSMS cannot triangulate a structure, the viewer draws its own surface and says why under it. |

## The tools the launcher starts

Nothing here is distributed by HOPE Labs. Each is an install on the cluster, and each tool's own
documentation and notices are the authority on its terms; the launcher reaches them from the Docs
button on every tool's page.

| Tool | Whose it is | Terms | Where its notices are |
|---|---|---|---|
| RFdiffusion, with ProteinMPNN | the Institute for Protein Design, University of Washington; ProteinMPNN by Justas Dauparas | A BSD license covering the code and the weights; ProteinMPNN MIT | The `LICENSE` in each install, and the tool's own How to cite page |
| RFdiffusion3, with ProteinMPNN | the Institute for Protein Design, University of Washington, distributed through Rosetta Commons Foundry; ProteinMPNN by Justas Dauparas | BSD 3-Clause, which says in terms that it covers the model weights as well as the code; ProteinMPNN MIT | `ML_programs/foundry/LICENSE.md`, and the tool's own How to cite page |
| BoltzGen | Hannes Stärk and colleagues | MIT, code and weights; the published cards are saved beside the shared weights cache | `envs/boltzgen_cache/README-weights.txt`, and the tool's own How to cite page |
| Boltz-2 | Jeremy Wohlwend, Gabriele Corso, Saro Passaro and colleagues | MIT, code and weights | The install's `dist-info`, and the tool's own How to cite page |
| ColabFold | Milot Mirdita, Konstantin Schütze, Yoshitaka Moriwaki, Lim Heo, Sergey Ovchinnikov and Martin Steinegger | MIT; the folding is AlphaFold 2 and AlphaFold-Multimer, whose code is Apache-2.0 and whose weights are CC BY 4.0 from DeepMind | The install, and the tool's own How to cite page |
| RFD4-Proteina | RosettaCommons and NVIDIA | BSD 3-Clause for the code; the released model bundle is fetched from the Hugging Face Hub under its own terms | The `LICENSE` inside the container image, and the tool's own How to cite page |
| BioEmu | Sarah Lewis, Tim Hempel, José Jiménez-Luna and colleagues at Microsoft Research | MIT; the embeddings it reads a sequence through are AlphaFold 2's, whose weights are CC BY 4.0 from DeepMind | The package's own folder inside the container image, and the tool's own How to cite page |
| HOPE-MD | the lab; the engines are Amber, OpenMM, GROMACS, NAMD and Desmond | Mixed, and two are restricted: NAMD is licensed by the University of Illinois and requires its acknowledgment, and Desmond is Schrödinger's under the lab's license | The tool's own documentation |

## Data and external services

Structures are fetched at run time from RCSB, PDBe and PDBj (PDB data: CC0 1.0) and from the
AlphaFold Protein Structure Database (CC BY 4.0). Folding from a sequence builds alignments on the
public ColabFold MMseqs2 server at `api.colabfold.com`, which receives the sequences; nothing else
leaves the cluster, and the launcher itself sends nothing anywhere but the SSH connection a person
signs in with.

This notice is a summary, and the license files are the authority. Where the answer matters, read
them rather than this table, and if your use is commercial, have someone qualified read them: none
of this is legal advice.
