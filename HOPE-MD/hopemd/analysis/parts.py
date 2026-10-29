"""The production parts as files: where each frame sits, and the cut a resumed segment makes.

A segment that is stopped (walltime or a kill) continues from its last restart or checkpoint
into a new part, and the frames the stopped part wrote past that point are simulated again by
the part after it. The production job cuts them from the stopped part before the next part
starts, so cpptraj, MMPBSA.py, the videos and the dense windows read the parts in order and
meet every frame once: frame k is k save intervals into the production.

Amber's NetCDF keeps each frame's time; NAMD's DCD keeps the step of its first frame and the
steps between frames in its header. OpenMM's DCD reporter writes the steps between frames in
both places, so its runner counts the frames to keep itself. Either file is cut in place,
nothing copied: the frame count in the header is lowered and the file truncated after the
last frame kept.

    python3 -m hopemd.analysis.parts time r1/prod_s1_p1.rst7                 the time (ps) an Amber restart holds
    python3 -m hopemd.analysis.parts cut r1/prod_s1_p1.nc --ps 5620 --save-ps 20    the frames up to 5620 ps kept
    python3 -m hopemd.analysis.parts cut r1/prod_s1_p1.dcd --step 1405000    a NAMD part: the frames up to that step kept
    python3 -m hopemd.analysis.parts join <run folder>                       a run resumed before the cut existed:
                                                                             each part cut where the next one starts

Standard library only: the production jobs run it with the server's interpreter.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
from pathlib import Path

#: NetCDF classic's types: (struct code, bytes)
NC_TYPES = {1: ("b", 1), 2: ("c", 1), 3: ("h", 2), 4: ("i", 4), 5: ("f", 4), 6: ("d", 8)}
STREAMING = 0xFFFFFFFF          # a record count netCDF leaves while it streams: no count to trust
PART = re.compile(r"prod_s(\d+)_p(\d+)\.(nc|dcd)")


# ---------------------------------------------------------------------------------- NetCDF
def _nc_header(fh) -> dict:
    """What this module needs of a NetCDF classic (CDF-1) or 64-bit offset (CDF-2) header, the
    formats pmemd and cpptraj write: the record count and, per variable, its type, size, offset and
    whether it has a value per frame (its first dimension is the unlimited one). The record size is
    the sum of the per-frame variables' sizes, or the one such variable's own size unpadded."""
    magic = fh.read(4)
    if magic[:3] != b"CDF" or magic[3:] not in (b"\x01", b"\x02"):
        raise ValueError("not a NetCDF classic or 64-bit offset file")
    offset = ">q" if magic[3] == 2 else ">i"

    def take(n: int) -> bytes:
        b = fh.read(n)
        if len(b) < n:
            raise ValueError("the NetCDF header ends early (a file cut off as it was written)")
        return b

    def i4() -> int:
        return struct.unpack(">i", take(4))[0]

    def name() -> str:
        n = i4()
        s = take(n).decode(errors="replace")
        take(-n % 4)
        return s

    def size_of(t: int) -> int:
        if t not in NC_TYPES:
            raise ValueError(f"no NetCDF type {t}")
        return NC_TYPES[t][1]

    def skip_attributes() -> None:
        i4()                                       # NC_ATTRIBUTE, or ABSENT before a zero count
        for _ in range(i4()):
            name()
            t, count = i4(), i4()
            size = count * size_of(t)
            take(size + (-size % 4))

    numrecs = struct.unpack(">I", take(4))[0]
    i4()
    dims = [(name(), i4()) for _ in range(i4())]
    skip_attributes()
    i4()
    variables = {}
    for _ in range(i4()):
        vname = name()
        dimids = [i4() for _ in range(i4())]
        skip_attributes()
        vtype, vsize = i4(), i4()
        size_of(vtype)
        begin = struct.unpack(offset, take(struct.calcsize(offset)))[0]
        if any(not 0 <= d < len(dims) for d in dimids):
            raise ValueError(f"variable {vname} names a dimension the header does not have")
        variables[vname] = {"type": vtype, "vsize": vsize, "begin": begin, "dims": dimids,
                            "record": bool(dimids) and dims[dimids[0]][1] == 0}
    records = [v for v in variables.values() if v["record"]]
    if len(records) == 1:
        v = records[0]
        recsize = NC_TYPES[v["type"]][1]
        for d in v["dims"][1:]:
            recsize *= dims[d][1]
    else:
        recsize = sum(v["vsize"] for v in records)
    start = min((v["begin"] for v in records), default=0)
    return {"numrecs": numrecs, "vars": variables, "recsize": recsize, "start": start}


def _nc_frames(path: Path, h: dict) -> int:
    """Whole frames in the file: the header's count, less any frame the file does not hold in full."""
    if h["numrecs"] == STREAMING:
        raise ValueError(f"{path}: the frame count is netCDF's streaming marker")
    if not h["recsize"]:
        return 0
    return min(h["numrecs"], max(0, (os.path.getsize(path) - h["start"]) // h["recsize"]))


def nc_times(path: str | Path) -> list[float]:
    """The time (ps) of each frame of an Amber NetCDF trajectory, in order."""
    path = Path(path)
    with path.open("rb") as fh:
        h = _nc_header(fh)
        t = h["vars"].get("time")
        if t is None or not t["record"]:
            raise ValueError(f"{path}: no time per frame")
        code, size = NC_TYPES[t["type"]]
        out = []
        for i in range(_nc_frames(path, h)):
            fh.seek(t["begin"] + i * h["recsize"])
            out.append(struct.unpack(">" + code, fh.read(size))[0])
    return out


def restart_time(path: str | Path) -> float:
    """The time (ps) an Amber restart holds: the time variable of a NetCDF restart (pmemd writes
    one unless ntxo=1), else the second number on the second line of an ASCII one. A production
    restart's time runs on from the ladder's, so the steps between two restarts of one run are
    the difference of their times over the step."""
    path = Path(path)
    with path.open("rb") as fh:
        head = fh.read(4)
        fh.seek(0)
        if head[:3] == b"CDF":
            h = _nc_header(fh)
            t = h["vars"].get("time")
            if t is None or t["record"]:
                raise ValueError(f"{path}: no time in this restart")
            code, size = NC_TYPES[t["type"]]
            fh.seek(t["begin"])
            raw = fh.read(size)
            if len(raw) < size:
                raise ValueError(f"{path}: the restart ends before its time")
            return float(struct.unpack(">" + code, raw)[0])
        lines = fh.read(4096).decode(errors="replace").splitlines()
    try:
        return float(lines[1].split()[1])
    except (IndexError, ValueError):
        raise ValueError(f"{path}: neither a NetCDF restart nor an ASCII one with a time") from None


# ------------------------------------------------------------------------------------- DCD
def dcd_header(path: str | Path) -> dict:
    """A DCD's frame layout from its header (the CHARMM format OpenMM and NAMD write): the first
    frame's step as the header gives it, the steps between frames, the bytes before the first
    frame and per frame, and the whole frames the file holds (the header's count, less a frame
    cut off mid-write)."""
    path = Path(path)
    with path.open("rb") as fh:
        head = fh.read(92)
        if len(head) < 92 or head[4:8] != b"CORD":
            raise ValueError(f"{path}: not a DCD file")
        end = "<" if struct.unpack("<i", head[:4])[0] == 84 else ">"
        if struct.unpack(end + "i", head[:4])[0] != 84:
            raise ValueError(f"{path}: not a DCD file")
        icntrl = struct.unpack(end + "20i", head[8:88])
        nset, istart, nsavc = icntrl[0], icntrl[1], icntrl[2]
        if icntrl[8] or icntrl[11]:
            raise ValueError(f"{path}: a DCD with fixed atoms or a fourth dimension")
        title = struct.unpack(end + "i", fh.read(4))[0]
        fh.seek(title + 4, os.SEEK_CUR)
        natom = struct.unpack(end + "3i", fh.read(12))[1]
        size = fh.tell()
    cell = 56 if icntrl[10] and icntrl[19] else 0        # 48 bytes of unit cell between two record markers
    frame = cell + 3 * (4 * natom + 8)
    whole = (os.path.getsize(path) - size) // frame
    return {"end": end, "istart": istart, "nsavc": nsavc, "natom": natom, "header": size, "frame": frame,
            "frames": max(0, min(nset, whole))}


# -------------------------------------------------------------------------------------- the cut
def keep_frames(path: str | Path, n: int) -> tuple[int, int]:
    """Keep the first n frames of a NetCDF or DCD part and drop the rest, in place: the header's
    frame count first, then the file truncated after the last frame kept. (frames kept, dropped)."""
    path = Path(path)
    with path.open("rb") as fh:
        head = fh.read(4)
    if head[:3] == b"CDF":
        with path.open("rb") as fh:
            h = _nc_header(fh)
        have = _nc_frames(path, h)
        n = max(0, min(n, have))
        with path.open("r+b") as fh:
            fh.seek(4)
            fh.write(struct.pack(">I", n))
        os.truncate(path, h["start"] + n * h["recsize"])
        return n, have - n
    h = dcd_header(path)
    have = h["frames"]
    n = max(0, min(n, have))
    with path.open("r+b") as fh:
        fh.seek(8)
        fh.write(struct.pack(h["end"] + "i", n))
        fh.seek(20)                                   # the step of the last frame
        fh.write(struct.pack(h["end"] + "i", h["istart"] + (n - 1) * h["nsavc"] if n else 0))
    os.truncate(path, h["header"] + n * h["frame"])
    return n, have - n


def cut_at_time(path: str | Path, ps: float, save_ps: float) -> tuple[int, int]:
    """Keep the frames of an Amber NetCDF part up to the time `ps`: the frame at that time is the
    one its restart was written with, the next part's first frame is one save interval after it.
    Half an interval of leeway for a time stored in single precision."""
    times = nc_times(path)
    n = next((i for i, t in enumerate(times) if t > ps + save_ps / 2.0), len(times))
    return keep_frames(path, n)


def cut_at_step(path: str | Path, step: int) -> tuple[int, int]:
    """Keep the frames of a NAMD DCD part up to the step `step` (the steps are NAMD's own, as its
    header counts them: the first frame's step, then one save interval apart)."""
    h = dcd_header(path)
    if h["nsavc"] <= 0:
        raise ValueError(f"{path}: the header gives no steps between frames")
    n = 0 if step < h["istart"] else (step - h["istart"]) // h["nsavc"] + 1
    return keep_frames(path, n)


def openmm_log_steps(path: str | Path) -> list[int]:
    """The step of each row of an OpenMM runner's part log (prod_s<k>_p<n>.log, its StateDataReporter's
    tab-separated rows, written at the same steps as the part's frames); [] for any other file."""
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return []
    if not lines or not lines[0].startswith('#"Step"'):
        return []
    try:
        return [int(line.split("\t")[0]) for line in lines[1:] if line.strip()]
    except ValueError:
        return []


def join(rundir: str | Path, save_ps: float | None = None) -> list[str]:
    """For a run whose stopped parts were resumed without the cut: within each segment, each part
    is cut where the part after it starts. Amber's part starts one save interval before its first
    frame's time (pmemd counts frames from a part's own start); NAMD's and OpenMM's frames sit on
    the steps of the whole production, so the part before keeps the frames before the next part's
    first one, its step from NAMD's DCD header or from the OpenMM runner's part log. A pair with
    nothing to go by is left alone (VMD's DCDs for a Desmond run, an OpenMM part whose log has gone),
    and so is GROMACS's xtc, which its own jobs cut. One line per part cut."""
    rundir = Path(rundir)
    if save_ps is None:
        save_ps = float(json.loads((rundir / "spec.json").read_text())["protocol"]["save_ps"])
    done = []
    for rdir in sorted(p for p in rundir.iterdir() if p.is_dir() and re.fullmatch(r"r\d+", p.name)):
        segments: dict[int, list[tuple[int, Path]]] = {}
        for p in rdir.iterdir():
            m = PART.fullmatch(p.name)
            if m:
                segments.setdefault(int(m.group(1)), []).append((int(m.group(2)), p))
        for _, parts in sorted(segments.items()):
            parts.sort()
            for (_, a), (_, b) in zip(parts, parts[1:]):
                if a.suffix != b.suffix:
                    continue
                if a.suffix == ".nc":
                    ta, tb = nc_times(a), nc_times(b)
                    if not ta or not tb or tb[0] <= ta[0]:
                        continue
                    kept, dropped = cut_at_time(a, tb[0] - save_ps, save_ps)
                    where = f"{tb[0] - save_ps:g} ps"
                else:
                    ha, hb = dcd_header(a), dcd_header(b)
                    if hb["nsavc"] > 1 and hb["frames"] and hb["istart"] > ha["istart"]:
                        kept, dropped = cut_at_step(a, hb["istart"] - hb["nsavc"])
                        where = f"step {hb['istart'] - hb['nsavc']}"
                    else:
                        sa, sb = openmm_log_steps(a.with_suffix(".log")), openmm_log_steps(b.with_suffix(".log"))
                        if not sa or not sb or sb[0] <= sa[0]:
                            continue
                        kept, dropped = keep_frames(a, sum(1 for s in sa[:ha["frames"]] if s < sb[0]))
                        where = f"step {sb[0] - ha['nsavc']}"
                if dropped:
                    done.append(f"{a.relative_to(rundir)}: {dropped} frame(s) past {where}, where {b.name} starts, cut; {kept} kept")
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m hopemd.analysis.parts", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("time", help="the time (ps) an Amber restart holds")
    t.add_argument("restart")
    c = sub.add_parser("cut", help="keep a part's frames up to a time (NetCDF) or a step (DCD)")
    c.add_argument("part")
    c.add_argument("--ps", type=float, help="NetCDF: the time of the last frame kept")
    c.add_argument("--save-ps", type=float, help="NetCDF: ps between frames")
    c.add_argument("--step", type=int, help="DCD: the step of the last frame kept")
    j = sub.add_parser("join", help="a run resumed before the cut existed: each part cut where the next one starts")
    j.add_argument("rundir")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "time":
            print(f"{restart_time(a.restart):.6f}")
        elif a.cmd == "cut":
            if a.step is not None:
                kept, dropped = cut_at_step(a.part, a.step)
                where = f"step {a.step}"
            elif a.ps is not None and a.save_ps:
                kept, dropped = cut_at_time(a.part, a.ps, a.save_ps)
                where = f"{a.ps:g} ps"
            else:
                ap.error("cut takes --step, or --ps with --save-ps")
            print(f"[hopemd] {a.part}: {dropped} frame(s) past {where} cut, {kept} kept")
        else:
            lines = join(a.rundir)
            for line in lines:
                print(f"[hopemd] {line}")
            print(f"[hopemd] {len(lines)} part(s) cut" + ("; now run the analysis and MM-GBSA again (Start again from analysis, "
                                                           "then from mmgbsa, on the Runs screen)" if lines else ""))
    except (OSError, ValueError, KeyError, struct.error) as e:
        print(f"[hopemd] {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
