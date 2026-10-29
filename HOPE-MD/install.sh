#!/usr/bin/env bash
# HOPE-MD install: the checkout beside its environment, somewhere you can write to that is not
# $HOME. Safe to re-run. Grace and ACES (any Slurm cluster with the engines as modules).
#
#     bash install.sh                        # into /scratch/user/$USER          (each person's own)
#     bash <(curl -fsSL https://raw.githubusercontent.com/aadhilhaq/HOPE-MD/main/install.sh) [PREFIX]
#
# Layout it makes, and that activate.sh, the launcher and update.sh expect:
#
#     <PREFIX>/HOPE-MD/MD        the code: a git clone (`bash update.sh` pulls it), or a copy put there
#                                before this runs, as HOPE Labs' Set up does, which is used as it is
#     <PREFIX>/HOPE-MD/env       the environment the server and Prepare run from
#     <PREFIX>/HOPE-MD/conda     only when no base environment was found (see below)
#     <PREFIX>/HOPE-MD/.cache    pip's and conda's caches, so nothing lands in $HOME
#
# The environment is a venv on top of a base that has OpenMM, PDBFixer, pdb2pqr and AmberTools
# when one is readable (one named in HOPEMD_BASE_PYTHON, or one this script knows of); otherwise a
# conda env is made in the prefix. AmberTools alone is tens of
# thousands of files, so on a file-count quota that is the expensive step, and the base is used
# whenever it can be. The engines are never installed: they are the cluster's modules, named in
# hopemd/site.py and loaded by the job scripts; the audit at the end says which lines load here.
set -euo pipefail

say()  { printf '\n\033[1m[install]\033[0m %s\n' "$*"; }
note() { printf '  %s\n' "$*"; }
die()  { printf '\n\033[1m[install] STOPPED\033[0m %s\n' "$*" >&2; exit 1; }

REPO="${HOPEMD_REPO:-https://github.com/aadhilhaq/HOPE-MD.git}"
PREFIX="${1:-${SCRATCH:-/scratch/user/$USER}}"
PREFIX="$(cd "$(dirname "$PREFIX")" 2>/dev/null && pwd)/$(basename "$PREFIX")"
case "$PREFIX" in "$HOME"|"$HOME"/*) die "$PREFIX is under \$HOME. Home is 10 GB and 10 000 files here; install in your scratch or a group allocation.";; esac
ROOT="$PREFIX/HOPE-MD"; MD="$ROOT/MD"; ENV="$ROOT/env"
export PIP_CACHE_DIR="$ROOT/.cache/pip"
mkdir -p "$ROOT" "$PIP_CACHE_DIR" || die "cannot write to $ROOT"
# the cluster's name, as scripts/audit.sh names its file: ACES's login nodes are alogin<n>
case "$(hostname)" in alogin*|ac[0-9]*|aces*) HOSTC=aces;; grace*) HOSTC=grace;; *) HOSTC=$(hostname | sed 's/[0-9].*//');; esac

say "HOPE-MD into $ROOT  (host $(hostname), user $USER)"

# ---- 1. the checkout --------------------------------------------------------------------
if [ -d "$MD/.git" ]; then
  say "checkout exists: $MD; pulling"
  git -C "$MD" pull --ff-only || note "pull refused (local changes, or a read-only shared checkout); keeping what is there"
  note "$(git -C "$MD" log -1 --format='at %h (%cd): %s' --date=short)"
elif [ -f "$MD/hopemd/__init__.py" ]; then
  # a copy put here rather than cloned: HOPE Labs carries one and its Set up copies it in
  say "using the copy of HOPE-MD at $MD as it is"
  [ -f "$MD/BUNDLED" ] && note "$(head -1 "$MD/BUNDLED")"
else
  say "cloning $REPO -> $MD"
  git clone -q "$REPO" "$MD" || die "clone failed; clone it to $MD yourself (git clone $REPO $MD) and run this script again. On a node without the internet, clone on a login node."
  note "$(git -C "$MD" log -1 --format='at %h (%cd): %s' --date=short)"
fi

# ---- 2. a base with the Prepare stage's tools -----------------------------------------------
say "looking for a base Python with OpenMM, PDBFixer, pdb2pqr and AmberTools"
BASE=""
CANDS=("${HOPEMD_BASE_PYTHON:-}")
CANDS+=("$ROOT/conda/bin/python3")
for cand in "${CANDS[@]}"; do
  [ -n "$cand" ] && [ -x "$cand" ] || continue
  if "$cand" -c "import openmm, pdbfixer, pdb2pqr" >/dev/null 2>&1 && "$cand" -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)"; then
    BASE="$cand"; break
  fi
done
if [ -z "$BASE" ]; then
  say "none readable: making one in $ROOT/conda (conda-forge: python 3.12, openmm, pdbfixer, pdb2pqr, ambertools)"
  note "this is tens of thousands of files; check your file quota first (showquota / lfs quota)"
  export CONDA_PKGS_DIRS="$ROOT/.cache/conda-pkgs" CONDA_ENVS_DIRS="$ROOT/.cache/conda-envs" CONDARC="$ROOT/.cache/condarc"
  mkdir -p "$CONDA_PKGS_DIRS" "$CONDA_ENVS_DIRS"; : > "$CONDARC"
  type module >/dev/null 2>&1 || source /etc/profile
  CONDA=""
  # micromamba in the folder above, where HOPE Labs' Set up keeps it, before a site's conda: it
  # is one file, and it solves this environment in minutes where conda takes much longer
  [ -x "$PREFIX/tools/micromamba" ] && CONDA="$PREFIX/tools/micromamba"
  [ -n "$CONDA" ] || for m in Anaconda3 Miniconda3 Mamba; do module load "$m" >/dev/null 2>&1 && CONDA="$(command -v conda || command -v mamba || true)" && [ -n "$CONDA" ] && break; done
  [ -n "$CONDA" ] || CONDA="$(command -v micromamba || command -v conda || true)"
  [ -n "$CONDA" ] || die "no conda on this machine (module load Anaconda3 failed); make a base by hand and set HOPEMD_BASE_PYTHON"
  # conda also writes into $HOME whatever its package cache is set to: its index and plugin caches,
  # its list of environments (~/.conda/environments.txt), a usage token and, when it looks for a
  # GPU, the CUDA driver's cache. So it runs with a HOME and an XDG cache beside the others.
  mkdir -p "$ROOT/.cache/home"
  HOME="$ROOT/.cache/home" XDG_CACHE_HOME="$ROOT/.cache" \
    "$CONDA" create -y -p "$ROOT/conda" -c conda-forge python=3.12 openmm pdbfixer pdb2pqr ambertools >/dev/null || die "conda create failed; the usual cause is the file quota"
  BASE="$ROOT/conda/bin/python3"
  [ -x "$BASE" ] || die "conda said done but $BASE is not there (conda does not check what it wrote); rm -rf $ROOT/conda and try again"
fi
note "base: $BASE ($("$BASE" --version 2>&1))"

# ---- 3. the environment: a venv on the base ---------------------------------------------------
if [ -x "$ENV/bin/python3" ]; then
  say "environment exists: $ENV"
else
  say "making $ENV on that base"
  "$BASE" -m venv --system-site-packages "$ENV" || die "venv failed"
fi
"$ENV/bin/python3" -c "import openmm, pdbfixer, pdb2pqr; print('  env ok: openmm', openmm.__version__, '| pdb2pqr', pdb2pqr.__version__)" || die "the environment cannot import the Prepare stage's tools"
# reportlab writes the PDF portfolio: the lab's base has it, a conda env made here does not
"$ENV/bin/python3" -c "import reportlab" >/dev/null 2>&1 || { note "adding reportlab, for the PDF portfolio"; "$ENV/bin/python3" -m pip install -q reportlab || note "reportlab did not install: everything but the PDF portfolio works"; }
for b in tleap pdb4amber cpptraj; do command -v "$b" >/dev/null 2>&1 || [ -x "$(dirname "$BASE")/$b" ] || note "note: $b is not in the base; the build job uses the Amber module for it anyway"; done

# ---- 4. does it run? --------------------------------------------------------------------------
say "HOPE-MD itself, from the environment"
( cd "$MD" && . ./activate.sh && python3 -c "import hopemd.server, hopemd.engines, hopemd.prepare; print('  hopemd imports')" ) || die "HOPE-MD does not import in this environment; read the output above"

# ---- 5. which of site.py's module lines load here -------------------------------------------------
say "the audit: which module lines in hopemd/site.py load on this cluster (a minute or two)"
mkdir -p "$ROOT/audit"
( cd "$ROOT/audit" && HOPEMD_PYTHON="$ENV/bin/python3" bash "$MD/scripts/audit.sh" >/dev/null 2>&1 || true )
AUD="$ROOT/audit/hopemd-audit-$HOSTC.txt"
if [ -f "$AUD" ]; then
  sed -n '/== the lines hopemd/,/== containers/p' "$AUD" | grep -E "^-- " | sed 's/^/  /'
  note "full audit: $AUD"
  grep -q "CANNOT LOAD" "$AUD" && note "a line that cannot load is fixed in hopemd/site.py (the site's block), never in a template" || true
fi

# ---- 6. what to do next ----------------------------------------------------------------------------
RUNS="${SCRATCH:-/scratch/user/$USER}/hopemd_runs"
if [ -f "$MD/BUNDLED" ]; then
  say "done. HOPE Labs opens it from $MD; its runs go to $RUNS"
  note "by hand on a login node:"
  note "    cd $MD && . ./activate.sh && python3 -m hopemd.server --port 8098 --runs $RUNS"
  note "update later: update HOPE Labs, then Install HOPE-MD again on its Set up page, which copies the new one in"
else
  say "done. In the desktop launcher:  tool directory  $MD      runs directory  $RUNS"
  note "by hand on a login node:"
  note "    cd $MD && . ./activate.sh && python3 -m hopemd.server --port 8098 --runs $RUNS"
  note "update later:  bash $MD/update.sh"
fi
