"""python3 -m hopemd.analysis <rundir>: cpptraj and MMPBSA outputs -> analysis/summary.json.

The analysis job calls this rather than `-m hopemd.analysis.summarise`, which Python runs
twice (once as the package's import, once as __main__) and warns about.
"""
import json
import sys

from .summarise import summarise

d = summarise(sys.argv[1] if len(sys.argv) > 1 else ".")
print(json.dumps({k: (list(v.keys()) if isinstance(v, dict) else v) for k, v in d["replicates"].items()}))
