"""Re-render a run's files from its prepared structure.

When Submit chains Prepare into the graph, the files were first rendered before the
structure was cleaned, modeled and split, so the residue ranges the masks are built from
(receptor first, partner after, tleap's numbering) were not known. The Prepare job runs this
last: it reads build/prep_report.json and the split input files, records the detected fields
into spec.json (the person's choices are untouched), and writes every rendered file again
except the job scripts already queued and the Prepare script itself.

    python3 -m hopemd.rerender <rundir>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .spec import Spec, derive

KEEP = ("source", "prep", "ph", "chains_found", "hetero_found", "gaps_found", "confidence", "meta", "waters",
        "tm_annotation", "environment", "membrane", "keep_waters", "keep_cofactors", "chains")
SKIP = ("prepare.sbatch", "jobs/graph.json")


def rerender(rundir: str | Path) -> dict:
    rundir = Path(rundir)
    spec = Spec.from_json((rundir / "spec.json").read_text())
    report = json.loads((rundir / "build" / "prep_report.json").read_text())
    if not report.get("finished"):
        raise RuntimeError("build/prep_report.json says Prepare did not finish; nothing re-rendered")
    from .server import _prepare_result
    from .engines import engine_for
    sysm = _prepare_result(spec, rundir, report)["system"]
    d = spec.to_dict()
    for k, v in sysm.items():
        if k not in KEEP and k in d["system"]:
            d["system"][k] = v
    d["system"]["receptor_chains"] = sysm.get("receptor_chains") or d["system"].get("receptor_chains", [])
    d["system"]["partner_chains"] = sysm.get("partner_chains") or d["system"].get("partner_chains", [])
    d["system"]["prepared"] = True
    spec = Spec.from_dict(d)
    (rundir / "spec.json").write_text(spec.to_json())
    render = engine_for(spec.engine).render(spec, derive(spec))
    from .provenance import stamp
    stamp(spec, "prepare", render)
    (rundir / "spec.json").write_text(spec.to_json())
    written = []
    for f in render.files:
        if f.path in SKIP:
            continue
        p = rundir / f.path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f.text)
        if f.executable:
            p.chmod(0o755)
        written.append(f.path)
    return {"written": written, "receptor_residues": spec.system.receptor_residues, "ligand_residues": spec.system.ligand_residues,
            "ligand_class": spec.system.ligand_class}


if __name__ == "__main__":
    out = rerender(sys.argv[1] if len(sys.argv) > 1 else ".")
    print(f"[hopemd] re-rendered {len(out['written'])} files from the prepared structure: receptor {out['receptor_residues']}, "
          f"{out['ligand_class']} {out['ligand_residues']}")
