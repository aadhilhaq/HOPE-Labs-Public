# Third-party notices

HOPE-MD itself is not licensed for use outside the HOPE Lab; see [LICENSE](LICENSE). This notice
lists the software it uses, which keeps its own licence. Almost none of it is in this repository:
the engines and the analysis tools are modules on Texas A&M HPRC, loaded by the job scripts
(`hopemd/site.py` names the exact module lines, and every run records the ones it loaded).

## The engines

A run uses one of these. Which one is the person's choice on the Setup screen, and the licence
that applies is the one on the row they picked.

| Engine | Owner | Licence | How it is used |
|---|---|---|---|
| Amber 24, with AmberTools | The Amber developers | AmberTools is GPL-3.0-or-later, LGPL-3.0-or-later, BSD and MIT by component; `pmemd` is under Amber's own paid licence, held by the university | Building the system, the restraint ladder, production, and MM-GBSA rescoring |
| OpenMM 8.3 | The OpenMM developers, Stanford | MIT, with parts under LGPL | Production, as an alternative to Amber |
| GROMACS 2024.4 | The GROMACS developers | LGPL-2.1-or-later | Production, as an alternative |
| NAMD 3.0.1 | The Theoretical and Computational Biophysics Group, Beckman Institute, University of Illinois at Urbana-Champaign | Its own licence: free for non-commercial use, and **its acknowledgement is required in anything that reports a run it did** | Production, as an alternative |
| Desmond 2024-4 | D. E. Shaw Research, distributed by Schrödinger | Academic licence, under the university's Schrödinger agreement; the OPLS4 force field is licensed separately and the page says so where it is chosen | Production, as an alternative |
| Schrödinger 2025-3, Prime MM-GBSA | Schrödinger | The university's licence, with a fixed number of tokens; a job waits for them | Rescoring, optional |

NAMD's own terms state the acknowledgement that must appear. The launcher's credit bar and the
How to cite page both carry it, and so does a run's own report.

## Preparation, analysis and rendering

| Tool | Licence | Used for |
|---|---|---|
| PDBFixer | MIT | Modelling missing atoms and residues |
| PDB2PQR, with PROPKA 3 | BSD-3-Clause; PROPKA is LGPL-2.1 | Protonation at a chosen pH |
| cpptraj, from AmberTools | GPL-3.0-or-later | Imaging the trajectory, and the frames the video is made from |
| VMD 1.9.4 | Its own licence: free for non-commercial use, registration required | Rendering each frame as a scene for the video |
| POV-Ray | AGPL-3.0 | Rendering those scenes |
| FFmpeg 5.1 | LGPL-2.1-or-later, or GPL depending on the build | Assembling the frames into the videos |
| paramiko | LGPL-2.1-or-later | The desktop launcher, and bundled into its builds |
| MSMS, through mslib, the MSMS library in the ADCP 1.1 suite | mslib's own licence file is the BSD 2-Clause License, "Copyright (c) 2022, sannerlab". An `msms` program named by `$MSMS_EXE` keeps its own licence, for non-commercial scientific research only: "You are free to use the program (MSMS) ONLY FOR NON-COMMERCIAL SCIENTIFIC RESEARCH. This license is issued to you as an individual. For institution wide use or commercial use of the software you will have to follow the official registration procedure." Published work that shows one of these surfaces cites Sanner, Olson and Spehner, Biopolymers 38:305-320, 1996 | The molecular surfaces the page's Setup and Runs viewers draw (`hopemd/surface.py`). The page's server runs `hopemd/msms_mslib.py` with the installed suite's own Python, which imports mslib from that suite: the one installed in the same group folder (`tools/docking/adcpsuite`), or wherever `$ADCPSUITE_HOME` points. The suite is never copied into this repository or distributed with it. It is given the radii of MolKit's copy of `pdb_to_xyzr`'s table, read as data from the installed suite (MolKit is under the MGLTOOLS licence, for non-commercial use). Where the suite is absent, the viewer draws its own surface, so nothing else depends on it |

## Loaded by the browser

Both are copies in this repository, under `hopemd/web/vendor/`, because the page is served on a
login node through a forwarded port and a browser on it can fetch from the page and nowhere else.
The licence texts of the libraries inside each minified bundle sit beside it.

| Library | Owner | Version | Licence |
|---|---|---|---|
| 3Dmol.js | University of Pittsburgh and contributors | 2.5.5 | BSD-3-Clause, with the GLmol, Three.js and jQuery notices: `LICENSE-3Dmol.txt` |
| Mol* | The Mol* contributors | 4.9.0 | MIT: `LICENSE-molstar.txt` |

## Data and external services

Structures are fetched at run time from RCSB, PDBe and PDBj (PDB data: CC0 1.0) and from the
AlphaFold Protein Structure Database (CC BY 4.0). Nothing else leaves the cluster: HOPE-MD runs
where the data is, and the launcher sends nothing anywhere but the connection a person signs in
with.

This notice is a summary, and the licence files are the authority. Where the answer matters, read
them rather than this table, and if your use is commercial, have someone qualified read them:
none of this is legal advice, and five of the rows above are not open source.
