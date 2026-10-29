"""A check that this install works where it will run: one structure prepared on the queue.

HOPE Labs' Set up runs it after installing HOPE-MD, and anybody can run it after an install of
their own. It proves what the import on a login node cannot: that a compute node finds this
install's interpreter, and that OpenMM, PDBFixer and pdb2pqr work there, which is where Prepare,
the ligand's parameters and the build all run. The structure is Trp-cage (PDB 1L2Y), twenty
residues, so the job is a minute or two of the short queue.

    python -m hopemd.selfcheck stage <folder> [--pdb 1L2Y]   on a login node: fetch it, write the job
    sbatch prepare.sbatch                                     in <folder>
    python -m hopemd.selfcheck read <folder>                  when the job has ended: what it made

Both print one JSON object. The folder is the check's own; nothing else is written.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: Twenty residues, one chain, an NMR model: the smallest protein the whole of Prepare runs on.
PDB_ID = "1L2Y"


def stage(folder: str | Path, pdb_id: str = PDB_ID) -> dict:
    """Fetch the structure, stage it as the page stages an import, and write the Prepare job."""
    import socket
    from . import queue as q
    from .importers.complex import import_complex
    from .site import site_for_host
    from .spec import Spec
    folder = Path(folder).resolve()
    (folder / "logs").mkdir(parents=True, exist_ok=True)     # Slurm opens the log before the job starts
    found = import_complex("rcsb", {"pdb_id": pdb_id, "assembly": "1"}, folder)
    spec = Spec()
    spec.name = "selfcheck"
    # the cluster this runs on, as the server picks it, so the job asks for one of its own queues
    here = site_for_host(socket.gethostname())
    if here is not None:
        spec.compute.site = here.key
    spec.system.source.kind, spec.system.source.pdb_id = "rcsb", pdb_id
    for key, value in found.items():
        if hasattr(spec.system, key):
            setattr(spec.system, key, value)
    (folder / "spec.json").write_text(spec.to_json())
    (folder / "prepare.sbatch").write_text(q.prepare_job_script(spec, folder, cpus=2, mem_gb=4, minutes=20))
    return {"folder": str(folder), "job": "prepare.sbatch", "pdb_id": pdb_id,
            "chains": [c["id"] for c in found.get("chains_found", [])],
            "partition": spec.site.prep_partition, "site": spec.site.key}


def read(folder: str | Path) -> dict:
    """What the Prepare job left: done or not, what it did, and the prepared receptor's size."""
    folder = Path(folder).resolve()
    report = folder / "build" / "prep_report.json"
    if not report.is_file():
        return {"ok": False, "why": f"the Prepare job wrote no {report}: its log is in {folder / 'logs'}"}
    try:
        got = json.loads(report.read_text())
    except ValueError as why:
        return {"ok": False, "why": f"{report} could not be read: {why}"}
    receptor = folder / "input" / "receptor.pdb"
    atoms = sum(1 for line in receptor.read_text().splitlines() if line.startswith("ATOM")) if receptor.is_file() else 0
    ok = bool(got.get("finished")) and atoms > 0
    return {"ok": ok, "atoms": atoms, "steps": sorted((got.get("steps") or {}).keys()),
            "started": got.get("started"), "finished": got.get("finished"),
            **({} if ok else {"why": "Prepare did not finish with a receptor: see " + str(folder / "logs")})}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hopemd.selfcheck", description=__doc__.split("\n\n")[0])
    ap.add_argument("what", choices=("stage", "read"))
    ap.add_argument("folder")
    ap.add_argument("--pdb", default=PDB_ID, help="the structure to prepare (default %(default)s)")
    args = ap.parse_args(argv)
    try:
        out = stage(args.folder, args.pdb) if args.what == "stage" else read(args.folder)
    except Exception as why:                                   # noqa: BLE001 - said, as JSON, not raised
        out = {"ok": False, "why": f"{type(why).__name__}: {why}"}
    print(json.dumps(out))
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
