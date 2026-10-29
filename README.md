<p align="center">
  <img src="docs/brand/banner.png" alt="HOPE Labs" width="100%">
</p>

One front for a set of molecular design and simulation tools on a cluster. Sign in once, pick a
tool, and it opens in a tab of its own: binder design, folding, molecular dynamics and ensemble
sampling, each the page it already was, reached through one connection and one sign in.

| Tool | What it does | Leaves behind |
|---|---|---|
| **RFdiffusion** | Diffuses a binder backbone onto the target, then gives it a sequence with ProteinMPNN | designed binders |
| **RFdiffusion3** | Diffuses every atom of a binder at once, aimed by the atoms that must be in place | designed binders |
| **BoltzGen** | Generates binders against a target of any kind, then folds and ranks them | designed binders |
| **RFD4-Proteina** | Designs binders, enzymes and nucleic-acid binders from what has to be in place: the target held rigid, the residues to reach marked | designed binders, refolded and ranked |
| **Boltz-2** | Folds a complex of protein, DNA, RNA and ligand chains | folded structures |
| **ColabFold** | Folds protein chains and complexes | folded structures |
| **HOPE-MD** | Molecular dynamics of a complex on five engines | trajectories, binding energies |
| **BioEmu** | Samples the structures a protein visits, from its sequence or a structure, in minutes | structural ensembles |

The tools are not changed in any way. Each serves its own page on a login node, or is given one
by HOPE Labs from its folder there; the launcher starts the one you pick over the connection it is
holding, forwards a port to it and opens it in a tab of its own. Nothing about the tools is
installed on your computer.

The first time you sign in to a cluster, HOPE Labs offers to set itself up there, in one folder you
choose, and to install the tools you tick: the built-in documentation's *Setting up on a cluster*
says how, and *Installing by hand* covers the tools Set up does not install yet. HOPE-MD comes in
this repository, in its `HOPE-MD` folder, and Set up installs it from there on Grace and ACES.

## Using it

**Windows.** Download `HOPE-Labs-public-windows.zip` from the [releases page](../../releases/latest),
unzip it **whole** (the folder travels together) and run `HOPE-Labs.exe` inside it. It is
unsigned, so the first run shows "Windows protected your PC": choose More info, then Run anyway.

**macOS (Apple silicon).** Download `HOPE-Labs-public-mac-apple-silicon.zip`, unzip it and open
`HOPE-Labs.app`. It is unsigned, so the first time macOS will not open it: in System Settings,
under Privacy & Security, choose Open Anyway beside the line about HOPE-Labs, and confirm (on
macOS 14 and earlier, right-click `HOPE-Labs.app` and choose Open instead).

Pick **Grace (TAMU)**, type your NetID, press **Sign in and open HOPE Labs**. Grace asks for your
password and then a Duo approval, once, however many tools you then open. The page opens by
itself.

On another cluster with Slurm, pick **Other (SLURM)** and type its address and your account.

## What the page does

**Tools** is the landing page: every tool a block, with what it does, what it takes from another
tool and what it leaves for the next one, over a search box and the categories. **Launch** starts
it on the login node and opens it in a tab of its own, on the tool's own page, as the tool's own
launcher opens it. The block then
says *running*, and picking it again goes back to the tab that is already open rather than
starting a second copy.

**Results** lists the run folders of every tool together, newest first, filtered by tool. Each
run carries the step that usually follows it: *Simulate a design in HOPE-MD* beside a design run,
*Simulate a complex in HOPE-MD* beside a Boltz-2 one. Pressing one opens the tool that
takes it; its tab shows the run's folder with a **Copy path** button, and copies it on the way to
the tool, ready to be pasted into the tool's import box.

**Docs**, in the top right corner, opens the built-in documentation in a tab of its own: every
part of the page, a tool's tab, Results, setting HOPE Labs up on a cluster, and what each message
means. The bar along the bottom of the page carries the version, the copyright and who does the
work; each tool's own page has its own Docs and its own bar.

**Flows** is the third view: a canvas where the lab's tools are cards, linked socket to socket,
with the target filled in once and the whole chain launched from one button. What may be linked to
what is `hope_flow/cards.py`, which the canvas and the cluster both read, so they cannot disagree.
`hope_flow` is the runner: one small job per card, each waiting on the one before it through a
Slurm dependency, turning what that card left into what the next one needs. A launched flow needs
nothing of this running — close the laptop and it carries on.

**The page updates itself.** The launcher is a shell: it signs in, holds one connection, and
serves a page. That page is taken from HOPE Labs on the cluster, so a change to it reaches
everybody the next time they sign in rather than when they next download the launcher — which
matters, because a tool shared once should not need sharing again for every change to it. Two
numbers in `hope_labs/__init__.py` decide it: `PAGE_API` is what a build's server offers, and
`PAGE_NEEDS` is what the page files require of one. A launcher too old for the install's page
keeps its own and says so in a line, rather than serving a page whose buttons call routes it does
not have. Untick *Take the page from the cluster* to pin the launcher to the page it was built
with.

The cards travel the same way, and for the same reason. `hope-flow catalogue` is what an install
offers — the cards, their sockets, their settings — and the launcher adopts it, so a card added on
the cluster appears in a launcher built months before it. The cards are **data**; the rules about
what may be linked to what are **code**, and the code stays in the launcher. A catalog says
which rules it was written for, and one written for rules the launcher lacks is left alone rather
than half-read.

So: a new card, a new setting, a new view, a reworded refusal — none of these needs a new
download. A change to the rules themselves, or to the server behind the page, does, and says so.

**Sign out** closes the pages and the connection. Anything already queued on the cluster carries
on without it: the launcher submits work, it does not hold it.

## Running it from source

```bash
pip install paramiko
python -m hope_labs
```

Python 3.10 or newer, with tkinter. `python -m hope_labs --selftest` checks the parts that need no
display, including that each tool's start line and its ready line still parse and that the docs
came along, and `--version` prints the build.

## Building the executables

A tag starting with `v` builds both and makes a release page:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

PyInstaller does not cross-compile, so the Windows build runs on a Windows runner and the Mac build
on a Mac. `packaging/build_windows.bat` builds it on a Windows machine without a runner.

## How a tool is added

Everything that differs between tools is in [`hope_labs/tools.py`](hope_labs/tools.py): one entry
per tool, giving where it is installed, the line that starts it, and how it says it is ready. Every
tool prints one line with its address and its token when it is ready; a new tool either prints the
same or brings a parser of its own. Nothing else in the launcher knows one tool from another.

None of them takes a token on its command line, deliberately: a login node is shared and `ps` shows
every argument to everybody on it, so each tool prints its token instead and the launcher reads it
off the channel it is already holding.

## Credits

HOPE-MD is the HOPE Lab's own. RFdiffusion and RFdiffusion3 are by the Institute for Protein Design at the University of Washington; RFD4-Proteina is by RosettaCommons and NVIDIA; BoltzGen is by Hannes Stärk and colleagues; Boltz-2 is by Jeremy Wohlwend, Gabriele Corso, Saro Passaro and colleagues; ColabFold is by Sergey Ovchinnikov, Milot Mirdita and Martin Steinegger; and BioEmu is by Sarah Lewis, Tim Hempel, José Jiménez-Luna and colleagues at Microsoft Research. The methods they run,
AlphaFold, ProteinMPNN, Amber, OpenMM, GROMACS, NAMD, Desmond and the rest, belong to their
authors, and every run records what it used. Please acknowledge Texas A&M High Performance Research
Computing.

By Aadhil Haq, HOPE Lab (PI Dr. Sandun Fernando), Texas A&M University.
