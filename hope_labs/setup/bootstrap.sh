#!/bin/sh
# HOPE Labs on a cluster it has never been on: the code, a package manager and a Python, in one
# folder. Nothing else is installed here; the tools are chosen afterwards, on the Set up page.
#
#   sh bootstrap.sh <folder> [<where the code comes from>]
#
# The launcher runs this over its own connection when it signs in to a cluster with no HOPE Labs
# on it, so nobody has to. It can be run by hand as well, on a LOGIN node (it downloads):
#
#   curl -fsSL <url of this file> | sh -s -- /scratch/me/hope-labs
#
# <where the code comes from> is a git URL (the default), a .tar.gz (a path or a URL), or a folder
# holding a checkout. Run twice, it does nothing the second time.
#
# By Aadhil Haq
set -eu

ROOT="${1:-}"
SOURCE="${2:-https://github.com/aadhilhaq/HOPE-Labs-Public.git}"
say() { printf 'HOPE-LABS %s\n' "$*"; }
die() { printf 'HOPE-LABS-FAILED %s\n' "$*"; exit 1; }

[ -n "$ROOT" ] || die "name the folder to set HOPE Labs up in"
# Every job script the tools write names paths inside this folder, and Slurm reads its own
# #SBATCH lines without honoring quotes, so a folder that needs quoting cannot hold a run.
case "$ROOT" in
  *[!A-Za-z0-9._/~+=@,-]*) die "the folder may hold letters, numbers and . _ - / only: $ROOT" ;;
esac
case "$ROOT" in "~"/*) ROOT="$HOME/${ROOT#"~/"}" ;; esac
case "$ROOT" in /*) ;; *) die "the folder must be a full path, starting with /: $ROOT" ;; esac

umask 002
mkdir -p "$ROOT" || die "cannot make $ROOT"
cd "$ROOT" || die "cannot go into $ROOT"
# Files made in here keep the folder's group, so a group can share one install.
chmod g+s "$ROOT" 2>/dev/null || true
say "folder $ROOT"

fetch() {      # fetch <url> <file>
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --retry 3 -o "$2" "$1"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$2" "$1"
  else
    die "neither curl nor wget is here, and one is needed to download anything"
  fi
}

# --- the code ----------------------------------------------------------------------------------
if [ -f HOPE-labs/hope_labs/__init__.py ]; then
  say "code already here"
else
  rm -rf HOPE-labs.part
  case "$SOURCE" in
    *.tar.gz|*.tgz)
      mkdir HOPE-labs.part
      if [ -f "$SOURCE" ]; then
        tar -xzf "$SOURCE" -C HOPE-labs.part || die "could not unpack $SOURCE"
      else
        fetch "$SOURCE" HOPE-labs.tgz || die "could not download $SOURCE"
        tar -xzf HOPE-labs.tgz -C HOPE-labs.part || die "could not unpack $SOURCE"
        rm -f HOPE-labs.tgz
      fi
      # A release archive holds one top folder; anything else is taken as it is.
      inner=$(ls HOPE-labs.part)
      if [ "$(echo "$inner" | wc -l)" -eq 1 ] && [ -d "HOPE-labs.part/$inner" ]; then
        mv "HOPE-labs.part/$inner" HOPE-labs && rmdir HOPE-labs.part
      else
        mv HOPE-labs.part HOPE-labs
      fi ;;
    /*)
      [ -d "$SOURCE" ] || die "there is no folder $SOURCE to take the code from"
      mkdir HOPE-labs.part
      (cd "$SOURCE" && tar --exclude=.git -cf - .) | (cd HOPE-labs.part && tar -xf -) \
        || die "could not copy $SOURCE"
      mv HOPE-labs.part HOPE-labs ;;
    *)
      command -v git >/dev/null 2>&1 || die "git is not here, and the code comes from $SOURCE"
      git clone -q --depth 1 "$SOURCE" HOPE-labs.part || die "could not fetch $SOURCE"
      mv HOPE-labs.part HOPE-labs ;;
  esac
  [ -f HOPE-labs/hope_labs/__init__.py ] || die "$SOURCE does not hold HOPE Labs"
  say "code fetched"
fi

# --- the package manager -----------------------------------------------------------------------
mkdir -p tools setup/home
case "$(uname -m)" in
  x86_64) PLATFORM=linux-64 ;;
  aarch64) PLATFORM=linux-aarch64 ;;
  ppc64le) PLATFORM=linux-ppc64le ;;
  *) die "this login node is $(uname -m), and micromamba is not built for it" ;;
esac
if [ ! -x tools/micromamba ]; then
  fetch "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-$PLATFORM" \
        tools/micromamba.part || die "could not download micromamba"
  mv tools/micromamba.part tools/micromamba
  chmod 775 tools/micromamba
  say "micromamba fetched"
fi

# --- a Python for the pages --------------------------------------------------------------------
# The pages and the installer need Python 3.8 or newer and nothing outside its standard library.
# A login node's own python3 is often older (3.6 on Grace), so one is made here, once. Nothing it
# runs writes to the real home: HOME and every cache point into this folder.
export HOME="$ROOT/setup/home" MAMBA_ROOT_PREFIX="$ROOT/tools/mamba" \
       CONDA_PKGS_DIRS="$ROOT/tools/mamba/pkgs" XDG_CACHE_HOME="$ROOT/setup/cache"
if [ ! -x envs/hopelabs/bin/python3 ]; then
  say "making a Python for the pages (a minute or two)"
  tools/micromamba create -y -q -p "$ROOT/envs/hopelabs" -c conda-forge --override-channels \
    python=3.11 >/dev/null || die "micromamba could not make the Python environment"
  tools/micromamba clean -a -y -q >/dev/null 2>&1 || true
  say "Python ready"
fi
envs/hopelabs/bin/python3 -c "import sys; assert sys.version_info >= (3, 8)" \
  || die "the Python made here does not run"

# A cluster whose login nodes reach the internet only through a proxy: the one this was run with
# is written down, so the pages and the installs started later go the same way.
if [ -n "${https_proxy:-${HTTPS_PROXY:-}}" ]; then
  printf '%s\n' "${https_proxy:-${HTTPS_PROXY:-}}" > setup/proxy
  say "web proxy ${https_proxy:-${HTTPS_PROXY:-}}"
fi

say "READY $ROOT"
