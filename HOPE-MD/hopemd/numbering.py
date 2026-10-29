"""The residues of a built topology, named as the structure names them.

tleap numbers a topology's residues 1, 2, 3 ... in the order of the build's files: the receptor's, then the
partner's, then the ions the run carries. The build hands pdb4amber those files numbered the same way
(hopemd.structure number), because ParmEd, which pdb4amber reads with, bonds a residue to the next only when their
numbers follow, and it keeps each file's own ids beside it: build/receptor_numbering.json and
build/ligand_numbering.json. Whatever names a residue of the topology (the contacts a trajectory lost, the residue
whose fluctuation peaks) names it through solute_ids, as the structure does: H:60A, not 64.

Standard library only: the analysis job runs it with whatever python3 the engine module's node has.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .structure import parent_name

PDB_PARTNERS = ("peptide", "protein", "nucleic_acid")


def read_numbering(rundir: str | Path, part: str) -> dict | None:
    """build/<part>_numbering.json (part: receptor or ligand) as the build wrote it, or None."""
    try:
        d = json.loads((Path(rundir) / "build" / f"{part}_numbering.json").read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d.get("residues"), list) else None


def _pdb_residues(path: Path) -> list[tuple[str, str]]:
    """(id "A:401", name) of each residue of a PDB, in file order: a residue wherever the chain, the number, the
    insertion code or the name changes."""
    out, last = [], None
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return out
    for l in lines:
        if l.startswith(("ATOM", "HETATM")) and len(l) > 26:
            k = l[17:27]
            if k != last:
                out.append((f"{l[21].strip() or 'A'}:{l[22:27].strip()}", l[17:20].strip()))
                last = k
    return out


def prmtop_labels(path: str | Path, n: int) -> list[str] | None:
    """The first n residue labels of an Amber topology (its RESIDUE_LABEL section, read line by line and left as soon as
    it has them), or None when the file cannot be read or holds fewer."""
    out: list[str] = []
    inside = False
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                if line.startswith("%FLAG"):
                    if inside:
                        break
                    inside = line.split()[1:2] == ["RESIDUE_LABEL"]
                    continue
                if not inside or line.startswith("%FORMAT") or line.startswith("%COMMENT"):
                    continue
                s = line.rstrip("\n")
                out += [s[i:i + 4].strip() for i in range(0, len(s), 4) if s[i:i + 4].strip()]
                if len(out) >= n:
                    break
    except OSError:
        return None
    return out[:n] if len(out) >= n else None


def solute_ids(rundir: str | Path, spec: dict | None = None) -> list[str] | None:
    """The id ("H:60A") of each residue of the solute and the ions the build carries, in the topology's order: entry
    i is topology residue i + 1. The receptor's residues, then the partner's (a small molecule is its one residue, LIG),
    then the ions of input/ions.pdb, the partner's first, as the build adds them.

    None where the residues cannot be named so with certainty, and the page and the report then name them by their
    topology numbers, as they did before the build kept its numbering: a run built before then, an engine whose
    topology keeps the structure's own ids (Desmond), a numbering whose counts are not the run's residue ranges, or one
    whose names are not the topology's (pdb2pqr dropped a residue, say). A wrong name is worse than a number."""
    rundir = Path(rundir)
    if spec is None:
        try:
            spec = json.loads((rundir / "spec.json").read_text())
        except (OSError, ValueError):
            return None
    if (spec.get("forcefield") or {}).get("engine") == "desmond":
        return None
    sysm = spec.get("system") or {}
    rec = read_numbering(rundir, "receptor")
    if rec is None:
        return None
    ids = [f"{r['chain']}:{r['id']}" for r in rec["residues"]]
    names = [r["name"] for r in rec["residues"]]
    rr = sysm.get("receptor_residues")
    if rr and rr[1] - rr[0] + 1 != len(ids):
        return None
    cls = sysm.get("ligand_class") or "none"
    lr = sysm.get("ligand_residues")
    n_partner = 0
    if cls in PDB_PARTNERS:
        lig = read_numbering(rundir, "ligand")
        if lig is None:
            return None
        ids += [f"{r['chain']}:{r['id']}" for r in lig["residues"]]
        names += [r["name"] for r in lig["residues"]]
        n_partner = len(lig["residues"])
    elif cls == "small_molecule":
        ids.append("LIG")
        names.append("")
        n_partner = 1
    ions = _pdb_residues(rundir / "input" / "ions.pdb")
    if cls != "none" and lr:
        bound = lr[1] - lr[0] + 1 - n_partner         # the carried ions that bind the partner, inside its range
        if lr[0] != len(ids) - n_partner + 1 or not 0 <= bound <= len(ions):
            return None
    ids += [i for i, _ in ions]
    names += [""] * len(ions)
    # the topology's own names, when it is there: each must be the residue the numbering says, a state of it aside
    labels = prmtop_labels(rundir / "build" / "complex.prmtop", len(ids)) if (rundir / "build" / "complex.prmtop").exists() else []
    if labels is None:
        return None
    for want, got in zip(names, labels):
        if want and parent_name(want) != parent_name(got):
            return None
    return ids


def label(ids: list[str] | None, n: int) -> str:
    """Topology residue n (1, 2, 3 ...) by its own id, or ":n" when it has none."""
    return ids[n - 1] if ids and 0 < n <= len(ids) else f":{n}"


def contact_label(name: str, ids: list[str] | None) -> str:
    """A contact as cpptraj's nativecontacts names it (":164@OE1_:51@NH2", topology numbers) by the residues' own ids:
    "H:60A@OE1 – L:12@NH2". Unchanged without ids, or when it is not of that form."""
    m = re.fullmatch(r":(\d+)@(\S+?)_:(\d+)@(\S+)", name.strip())
    if not ids or not m:
        return name
    return f"{label(ids, int(m.group(1)))}@{m.group(2)} – {label(ids, int(m.group(3)))}@{m.group(4)}"
