"""cpptraj .dat and MMPBSA.py outputs -> analysis/summary.json.

Runs at the end of the analysis job and again on demand from the server (it is
cheap). Series are downsampled to ~400 points for the page; the full files stay.
Usage: python3 -m hopemd.analysis.summarise <rundir>
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

from ..numbering import contact_label, label, solute_ids


def read_xy(path: Path) -> tuple[list[float], list[float]]:
    xs, ys = [], []
    try:
        for line in path.read_text().splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split()
            try:
                xs.append(float(parts[0])); ys.append(float(parts[1]))
            except (ValueError, IndexError):
                continue
    except OSError:
        pass
    return xs, ys


def read_cols(path: Path, n: int) -> list[list[float]]:
    """The first n numeric columns of a cpptraj .dat, one list per column; empty on a missing file."""
    cols: list[list[float]] = [[] for _ in range(n)]
    try:
        for line in path.read_text().splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split()
            try:
                vals = [float(p) for p in parts[:n]]
            except ValueError:
                continue
            if len(vals) < n:
                continue
            for c, v in zip(cols, vals):
                c.append(v)
    except OSError:
        pass
    return cols


def area_per_lipid(box_dat: Path, lipids: int) -> tuple[list[float], list[float]] | None:
    """Å² per lipid per frame from cpptraj's `vector vbox box` output (frame, x, y, z, ...):
    the xy area of the box over the lipids of one leaflet. The solute's footprint is in the
    box area too, so it reads high by that over half the lipids (72 Å² for a helix pair in
    POPC where the bare patch gives 66); the tiles say so. None without lipids or a series."""
    if not lipids or lipids <= 0:
        return None
    frames, xs, ys = read_cols(box_dat, 3)
    if not frames:
        return None
    return frames, [x * y / (lipids / 2.0) for x, y in zip(xs, ys)]


def downsample(xs: list[float], ys: list[float], n: int = 400) -> tuple[list[float], list[float]]:
    if len(xs) <= n:
        return xs, ys
    step = len(xs) / n
    ix = [int(i * step) for i in range(n)]
    return [xs[i] for i in ix], [ys[i] for i in ix]


def mmgbsa_result(path: Path) -> dict | None:
    """DELTA TOTAL mean and std err from an MMPBSA.py FINAL_RESULTS-style file."""
    try:
        text = path.read_text()
    except OSError:
        return None
    m = re.search(r"DELTA TOTAL\s+(-?[0-9.]+)\s+(-?[0-9.]+)\s+(-?[0-9.]+)", text)
    if not m:
        return None
    mean, std, sem = (float(x) for x in m.groups())
    frames = None
    fm = re.search(r"\|\s*Frames\s*:\s*(\d+)|Number of frames\s*[:=]\s*(\d+)", text)
    if fm:
        frames = int(next(g for g in fm.groups() if g))
    if frames is None:
        # MMPBSA.py's FINAL_RESULTS carries no frame count (Amber 24); the per-frame table
        # written with -eo does: one row per frame under the first "Frame #" header
        frames = frames_in_csv(path.with_name(path.stem + "_frames.csv"))
    return {"mean": mean, "std": std, "sem": sem, "frames": frames}


def frames_in_csv(path: Path) -> int | None:
    """Rows under the first 'Frame #' header of an MMPBSA.py -eo file, i.e. the frames scored."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    n = None
    for line in lines:
        if line.startswith("Frame #"):
            n = 0; continue
        if n is None:
            continue
        if line[:1].isdigit():
            n += 1
        elif n:
            break
    return n


def contacts_kept(native: Path, threshold: float = 0.5, ids: list[str] | None = None) -> dict | None:
    """From nativecontacts 'writecontacts': fraction of frames each contact is present. With the solute's ids
    (hopemd.numbering.solute_ids) a contact lost is named by them ("H:60A@OE1 – L:12@NH2"), cpptraj's own name, in
    topology numbers, kept beside it as "topology"."""
    try:
        lines = native.read_text().splitlines()
    except OSError:
        return None
    kept, lost, total = [], [], 0
    for line in lines:
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        # columns: #  Contact  Nframes  Frac.  Avg  Stdev  (cpptraj writecontacts)
        try:
            name, frac = parts[1], float(parts[3])
        except (IndexError, ValueError):
            continue
        total += 1
        entry = {"contact": contact_label(name, ids), "fraction": round(frac, 3)}
        if entry["contact"] != name:
            entry["topology"] = name
        (kept if frac >= threshold else lost).append(entry)
    if not total:
        return None
    return {"total": total, "kept": len(kept), "lost": lost[:12], "threshold": threshold}


def summarise(rundir: str | Path, save_ps: float | None = None, write: bool = True) -> dict:
    """The run's numbers from the analysis files. write=False reads and returns them without
    touching analysis/summary.json: the report reads a run folder and writes nothing in it, and a
    stored summary written before a later MM-GBSA job would otherwise be overwritten by whoever
    opened the report rather than by the job that made the numbers."""
    rundir = Path(rundir)
    spec = json.loads((rundir / "spec.json").read_text()) if (rundir / "spec.json").exists() else {}
    save_ps = save_ps or spec.get("protocol", {}).get("save_ps", 20.0)
    reps = spec.get("protocol", {}).get("replicates", 3)
    a = rundir / "analysis"
    # how many saved frames the viewer trajectory skips between its own: the page turns a frame
    # number into ns with it, and a run written at another stride was read at the wrong time
    stride = int(spec.get("analysis", {}).get("viewer_stride") or 20)
    out: dict = {"replicates": {}, "save_ps": save_ps, "view_stride": stride}
    # a bilayer run: build/system.json says how many lipids the membrane build placed, and the
    # analysis wrote the box lengths per frame; together they are the area per lipid
    try:
        system = json.loads((rundir / "build" / "system.json").read_text())
    except (OSError, ValueError):
        system = {}
    lipids = int(system.get("lipids") or 0)
    if lipids > 0:
        out["lipids"] = lipids
        if system.get("lipid"):
            out["lipid"] = system["lipid"]
    # the solute's residues by the structure's own ids, in topology order (None for a run built before the build kept
    # them, or on Desmond): what names a residue below
    ids = solute_ids(rundir, spec) if spec else None
    for i in range(1, reps + 1):
        r: dict = {}
        for key in ("rmsd_rec", "rmsd_lig", "rmsd_iface", "rg"):
            xs, ys = read_xy(a / f"r{i}_{key}.dat")
            if ys:
                ns = [x * save_ps / 1000.0 for x in xs]
                ns, ys2 = downsample(ns, ys)
                r[key] = {"ns": [round(v, 3) for v in ns], "values": [round(v, 3) for v in ys2],
                          "mean": round(statistics.fmean(ys), 3), "max": round(max(ys), 3)}
        bsa = buried_area(a / f"r{i}_sa_rec.dat", a / f"r{i}_sa_partner.dat", a / f"r{i}_sa_complex.dat")
        if bsa:
            xs, ys = bsa
            ns = [x * save_ps / 1000.0 for x in xs]
            ns, ys2 = downsample(ns, ys)
            r["bsa"] = {"ns": [round(v, 3) for v in ns], "values": [round(v, 1) for v in ys2],
                        "mean": round(statistics.fmean(ys), 1), "min": round(min(ys), 1), "first": round(ys[0], 1)}
        apl = area_per_lipid(a / f"r{i}_box.dat", lipids)
        if apl:
            xs, ys = apl
            ns = [x * save_ps / 1000.0 for x in xs]
            ns, ys2 = downsample(ns, ys)
            r["area_per_lipid"] = {"ns": [round(v, 3) for v in ns], "values": [round(v, 2) for v in ys2],
                                   "mean": round(statistics.fmean(ys), 2)}
        xs, ys = read_xy(a / f"r{i}_rmsf.dat")
        if ys:
            # atomicfluct byres numbers the residues as the topology does; each is named by its own id beside it
            res = [int(x) for x in xs]
            top = max(range(len(ys)), key=ys.__getitem__)
            r["rmsf"] = {"residue": res, "values": [round(v, 3) for v in ys],
                         "median": round(statistics.median(ys), 3),
                         "peak": {"residue": label(ids, res[top]), "value": round(ys[top], 3)}}
            if ids:
                r["rmsf"]["labels"] = [label(ids, n) for n in res]
        ck = contacts_kept(a / f"r{i}_native.dat", ids=ids)
        if ck:
            r["contacts"] = ck
        mg = mmgbsa_result(a / f"r{i}_mmgbsa.dat")
        if mg:
            r["mmgbsa"] = mg
        if (a / f"r{i}_view.pdb").exists():
            r["view"] = f"analysis/r{i}_view.pdb"
        if r:
            out["replicates"][str(i)] = r
    conf = spec.get("system", {}).get("confidence") or {}
    if conf.get("iptm") is not None:
        # an AlphaFold model: the prediction's own confidence beside the simulation's numbers,
        # so a bad prediction is not read as a bad simulation
        out["confidence"] = {k: conf[k] for k in ("iptm", "ptm", "ranking_score") if k in conf}
    if write:
        (a).mkdir(exist_ok=True)
        (a / "summary.json").write_text(json.dumps(out))
    return out


def buried_area(rec: Path, partner: Path, complex_: Path) -> tuple[list[float], list[float]] | None:
    """Buried surface per frame, rec + partner - complex (both sides, Å²), from three molsurf
    series; None unless all three are there and the same length."""
    series = [read_xy(p) for p in (rec, partner, complex_)]
    if not all(ys for _, ys in series) or len({len(ys) for _, ys in series}) != 1:
        return None
    xs = series[0][0]
    return xs, [a + b - c for a, b, c in zip(series[0][1], series[1][1], series[2][1])]


# ---- the two analysis files summary.json does not hold -------------------------------
# Both are written by the analysis job's cpptraj script (hopemd/engines/amber.py, _cpptraj) and
# were read by nothing until the report read them. They are parsed when the report is drawn, not
# stored, because how many rows are shown is the reader's choice on the report's bar.

def split_ranges(rundir: str | Path, spec: dict | None = None) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    """The receptor's and the partner's residues in the topology's numbering, as the render wrote
    them: analysis/ligand_mask.txt (":248-260") for the partner, spec.json for the receptor.
    (None, None) for a run with no partner, or one whose ranges were not recorded: nothing can
    then be said to cross the split, and the report says the pairs are of the whole solute."""
    rundir = Path(rundir)
    if spec is None:
        try:
            spec = json.loads((rundir / "spec.json").read_text())
        except (OSError, ValueError):
            return None, None
    sysm = spec.get("system") or {}
    if (sysm.get("ligand_class") or "none") == "none":
        return None, None
    rec = sysm.get("receptor_residues") or None
    lig = sysm.get("ligand_residues") or None
    try:
        text = (rundir / "analysis" / "ligand_mask.txt").read_text()
        m = re.search(r":(\d+)-(\d+)", text)
        if m:
            lig = [int(m.group(1)), int(m.group(2))]
    except OSError:
        pass
    if not rec or not lig or len(rec) != 2 or len(lig) != 2:
        return None, None
    return (int(rec[0]), int(rec[1])), (int(lig[0]), int(lig[1]))


def _side(n: int, rec: tuple[int, int] | None, lig: tuple[int, int] | None) -> str:
    """"rec", "lig" or "" for topology residue n."""
    if rec and rec[0] <= n <= rec[1]:
        return "rec"
    if lig and lig[0] <= n <= lig[1]:
        return "lig"
    return ""


_ATOM = re.compile(r"^(?P<resname>[A-Za-z0-9*'\"]+)_(?P<resi>\d+)@(?P<atom>\S+)$")


def _hbond_atom(token: str, ids: list[str] | None) -> dict | None:
    """cpptraj's "LEU_7@O" as {resname, resi, atom, id}: the residue by its own id where the build
    kept them (hopemd.numbering), by its topology number otherwise."""
    m = _ATOM.match(token.strip())
    if not m:
        return None
    resi = int(m.group("resi"))
    return {"resname": m.group("resname"), "resi": resi, "atom": m.group("atom"),
            "id": label(ids, resi) if ids else ""}


def hbonds(path: str | Path, rec: tuple[int, int] | None = None, lig: tuple[int, int] | None = None,
           ids: list[str] | None = None) -> dict | None:
    """The hydrogen bonds of an `hbond ... avgout` file (#Acceptor DonorH Donor Frames Frac AvgDist
    AvgAng), most held first.

    The analysis runs hbond over the whole solute, so most of what it finds is a chain holding
    itself; `rec` and `lig` (split_ranges) mark the pairs whose acceptor and donor lie on opposite
    sides of the receptor/partner split, which are the ones that hold the interface together.
    None when the file is not there; {"rows", "total", "crossing", "split"} otherwise, with every
    row kept and `crossing` true on those that cross."""
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return None
    rows, total = [], 0
    for line in lines:
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if len(parts) < 7:
            continue
        acc, don_h, don = (_hbond_atom(parts[i], ids) for i in (0, 1, 2))
        if not acc or not don_h or not don:
            continue
        try:
            frames, frac, dist, ang = int(parts[3]), float(parts[4]), float(parts[5]), float(parts[6])
        except ValueError:
            continue
        total += 1
        sides = {_side(acc["resi"], rec, lig), _side(don["resi"], rec, lig)}
        rows.append({"acceptor": acc, "donorh": don_h, "donor": don, "frames": frames,
                     "frac": round(frac, 4), "dist": round(dist, 3), "angle": round(ang, 2),
                     "crossing": sides == {"rec", "lig"}})
    if not total:
        return None
    rows.sort(key=lambda r: (-r["frac"], r["acceptor"]["resi"]))
    return {"rows": rows, "total": total, "crossing": sum(1 for r in rows if r["crossing"]),
            "split": bool(rec and lig)}


def hbond_counts(path: str | Path, save_ps: float = 20.0) -> dict | None:
    """The hydrogen bonds per frame of `hbond ... out` (#Frame HB[UU]), as a series in ns with its
    mean and range; None when the file is not there."""
    xs, ys = read_xy(Path(path))
    if not ys:
        return None
    ns = [x * save_ps / 1000.0 for x in xs]
    ns2, ys2 = downsample(ns, ys)
    return {"ns": [round(v, 3) for v in ns2], "values": [round(v, 1) for v in ys2],
            "mean": round(statistics.fmean(ys), 1), "min": round(min(ys), 1), "max": round(max(ys), 1),
            "first": round(ys[0], 1)}


def contact_residues(path: str | Path, rec: tuple[int, int] | None = None,
                     lig: tuple[int, int] | None = None, ids: list[str] | None = None) -> list[dict] | None:
    """The residue pairs of nativecontacts' `resout` (#Res1 #Res2 TotalFrac Contacts), the pair that
    carries most of the interface first. Total fraction sums each atom pair's occupancy, so a pair of
    several contacts exceeds 1. The receptor's residue is put first where the split is known
    (split_ranges), and each residue is named by its own id where the build kept them."""
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return None
    out = []
    for line in lines:
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            r1, r2, total, n = int(parts[0]), int(parts[1]), float(parts[2]), int(parts[3])
        except ValueError:
            continue
        if _side(r1, rec, lig) == "lig" and _side(r2, rec, lig) == "rec":
            r1, r2 = r2, r1                       # the receptor's residue first, as the table reads
        out.append({"rec": r1, "lig": r2, "rec_id": label(ids, r1) if ids else "",
                    "lig_id": label(ids, r2) if ids else "", "total": round(total, 3), "contacts": n,
                    "crossing": {_side(r1, rec, lig), _side(r2, rec, lig)} == {"rec", "lig"}})
    if not out:
        return None
    out.sort(key=lambda r: -r["total"])
    return out


if __name__ == "__main__":
    d = summarise(sys.argv[1] if len(sys.argv) > 1 else ".")
    print(json.dumps({k: (list(v.keys()) if isinstance(v, dict) else v) for k, v in d["replicates"].items()}))
