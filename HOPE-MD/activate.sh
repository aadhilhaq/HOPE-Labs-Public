#!/bin/sh
# HOPE-MD: put the interpreter the server runs with on PATH. Sourced by the desktop launcher
# (`. ./activate.sh` in the checkout) and by people:  . <prefix>/HOPE-MD/MD/activate.sh
#
# The shared install lays out  HOPE-MD/MD  (this checkout) beside  HOPE-MD/env  (a venv with
# OpenMM, PDBFixer, pdb2pqr, AmberTools), so the environment is found from here without a
# path being written down. HOPEMD_PYTHON, when set, wins; the server exports it to the jobs it
# renders, so the build and Prepare jobs use the same interpreter.
_here="$(cd "$(dirname "${BASH_SOURCE:-$0}")" 2>/dev/null && pwd)"
if [ -n "${HOPEMD_PYTHON:-}" ] && [ -x "${HOPEMD_PYTHON}" ]; then
  PATH="$(dirname "${HOPEMD_PYTHON}"):$PATH"
elif [ -x "$_here/../env/bin/python3" ]; then
  PATH="$_here/../env/bin:$PATH"; export HOPEMD_PYTHON="$_here/../env/bin/python3"
elif [ -x "$_here/env/bin/python3" ]; then
  PATH="$_here/env/bin:$PATH"; export HOPEMD_PYTHON="$_here/env/bin/python3"
fi
export PATH
# nothing in $HOME: caches the server and its tools would put under ~/.cache go to scratch instead
export HOPEMD_CACHE="${HOPEMD_CACHE:-${SCRATCH:-/tmp/$USER}/.cache/hopemd}"
export XDG_CACHE_HOME="$HOPEMD_CACHE" MPLCONFIGDIR="$HOPEMD_CACHE/matplotlib" CUDA_CACHE_PATH="$HOPEMD_CACHE/nv"
unset _here
