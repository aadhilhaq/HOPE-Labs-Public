#!/bin/bash
# HOPE-MD site audit. Run on a login node. Safe: reads only.
# the cluster's name, not the login node's: ACES's login nodes are alogin<n>, its compute nodes ac<nnn>
case "$(hostname)" in alogin*|ac[0-9]*|aces*) C=aces;; grace*) C=grace;; *) C=$(hostname | sed 's/[0-9].*//');; esac
OUT=hopemd-audit-$C.txt
exec > >(tee "$OUT") 2>&1
echo "== $(date)  host $(hostname)  user $USER"
echo "== scratch: $SCRATCH"; df -h "$SCRATCH" 2>/dev/null | tail -1; quota -s 2>/dev/null | tail -3

echo; echo "== modules (what module spider knows)"
for m in Amber AmberTools GROMACS NAMD VMD OpenMM Desmond Schrodinger POV-Ray FFmpeg Anaconda3 Python CUDA \
         Apptainer Singularity PyMOL nodejs WebProxy; do
  echo "-- $m"; module -t spider "$m" 2>&1 | grep -v '^$' | head -20
done

echo; echo "== the lines hopemd/site.py will put in the job scripts, tried one by one"
# The site's module lines are the single place site facts live; this tests exactly those, so a
# line that stops loading (a module retired, a toolchain changed) is caught here and fixed
# there. Needs the environment for hopemd's own Python (activate.sh), else the system python3.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$HERE/activate.sh" ] && . "$HERE/activate.sh"
PY="${HOPEMD_PYTHON:-python3}"
# the site by hopemd/site.py's own rule (site_for_host: a grace* host is Grace, an alogin* or ac* host ACES), so the
# audit tries the block the jobs will use; the same rule in the shell when hopemd does not import
H=$(hostname)
SITE=$("$PY" -c "import sys; sys.path.insert(0, sys.argv[1]); from hopemd.site import site_for_host; s = site_for_host(sys.argv[2]); print(s.key if s else '')" "$HERE" "$H" 2>/dev/null || true)
[ -n "$SITE" ] || SITE=$(case "$H" in alogin*|ac*) echo aces;; *) echo grace;; esac)
echo "-- site block: $SITE"
"$PY" - "$HERE" "$SITE" <<'PYEOF' 2>/dev/null > "$OUT.lines" || echo "-- hopemd not importable with $PY (Python 3.10+ needed); the module lines below are not tested"
import sys; sys.path.insert(0, sys.argv[1])
from hopemd.site import SITES
for eng, lines in SITES[sys.argv[2]].modules.items():
    print(eng + "\t" + " ; ".join(lines))
PYEOF
declare -A CHECK=( [amber]="tleap pdb4amber parmed pmemd.cuda pmemd.MPI cpptraj.OMP MMPBSA.py antechamber" [openmm]="python3" [gromacs]="gmx" [namd]="namd3" [vmd]="vmd povray" [ffmpeg]="ffmpeg" [desmond]="" )
while IFS=$'\t' read -r eng lines; do
  [ -z "$eng" ] && continue
  ( ml purge 2>/dev/null
    if eval "$lines" 2>/dev/null; then
      echo "-- $eng: OK   $lines"
      for b in ${CHECK[$eng]:-}; do printf '     %-12s %s\n' "$b" "$(command -v "$b" 2>/dev/null || echo MISSING)"; done
      case $eng in
        openmm)  python3 -c "import openmm, openmm.version as v; print('     openmm', v.version)" 2>&1 | tail -1 ;;
        # the programs are found by command -v whether or not their Python packages are: import them
        amber)   python3 -c "import importlib.metadata as m; m.version('pdb4amber'); import parmed, MMPBSA_mods; print('     python       pdb4amber, parmed and MMPBSA_mods import')" 2>&1 | tail -1 ;;
        gromacs) gmx --version 2>/dev/null | grep -iE 'gromacs version|gpu support|cuda' | sed 's/^/     /' ;;
        desmond) echo "     SCHRODINGER=$SCHRODINGER"; "$SCHRODINGER/licadmin" info 2>&1 | grep -iE 'license|server|desmond|error|expir' | head -6 | sed 's/^/     /' ;;
      esac
    else echo "-- $eng: CANNOT LOAD   $lines   (fix hopemd/site.py)"; fi )
done < "$OUT.lines"
rm -f "$OUT.lines"

echo; echo "== containers / python / tools"
which singularity apptainer 2>/dev/null; singularity --version 2>/dev/null; apptainer --version 2>/dev/null
( module load Anaconda3 2>/dev/null && { which conda; conda --version; } )
which python3 node 2>/dev/null; python3 --version; node --version 2>/dev/null

echo; echo "== queues and GPUs"
sinfo -o '%P %G %D %l %a' | grep -iE 'partition|gpu'
for p in gpu cpu long medium short; do scontrol show partition $p 2>/dev/null | grep -oE 'PartitionName=\S+|MaxTime=\S+|MaxNodes=\S+' | tr '\n' ' '; echo; done
gpuavail 2>/dev/null | head -25
sacctmgr show qos format=name,maxwall,maxjobspu,maxsubmit -p 2>/dev/null | head
myproject 2>/dev/null | head -12

echo; echo "== done: $OUT"
