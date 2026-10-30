"""The HOPE-MD server: one process, stdlib only, serves the page and the API.

Run on a login node (the internet is there; the queue is there)::

    python3 -m hopemd.server --port 8098 --runs $SCRATCH/hopemd_runs

then from your PC::

    ssh -N -L 8098:localhost:8098 grace.hprc.tamu.edu

and open the URL the server printed (it carries a token; nothing of the session answers without
it, and only the page's own files and the documentation at /docs/ do).
`--dry-run` renders everything and writes the run folder but never calls sbatch, which
is how the page is developed on a laptop.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from . import (__version__, COPYRIGHT, COPYRIGHT_SHORT, CREDITS, HOW_TO_CITE, ACKNOWLEDGE,
               VIEWER_REFERENCES)
from .provenance import CODE
from .spec import Spec, validate, derive, choices, PRESETS, ENGINE_LABELS, CPU_MAX_NS, CPU_MAX_FRAMES, needs_prepare, partner_class
from .site import SITES, site_for_host
from .engines import engine_for
from . import queue as q
from .importers import import_files, inspect_pdb, import_complex
from .importers.files import detect_inputs
from .analysis import summarise
from .prepare import prepare
from .spec import SOURCE_KINDS, Prep

WEB = Path(__file__).resolve().parent / "web"


class State:
    token: str = ""
    runs: Path = Path(".")
    dry_run: bool = False
    site_key: str = "grace"
    places: dict[str, str] = {}


S = State()


def named_places() -> dict[str, str]:
    """Browse opens on these, not on a raw path."""
    p: dict[str, str] = {}
    scratch = os.environ.get("SCRATCH")
    if scratch:
        p["My scratch"] = scratch
    shared = os.environ.get("HOPEMD_SHARED", "")          # a group space, where one is named
    if os.path.isdir(shared):
        p["Shared install"] = shared
    shared_runs = os.environ.get("HOPEMD_SHARED_RUNS", os.path.join(shared, "hopemd_runs") if shared else "")
    if os.path.isdir(shared_runs):
        p["Shared runs"] = shared_runs        # runs put in the group space for others to open
    p["HOPE-MD runs"] = str(S.runs)
    p["Home"] = os.path.expanduser("~")
    return {k: v for k, v in p.items() if os.path.isdir(v)} or {"Home": os.path.expanduser("~")}


#: What the structure browser offers a place as, so it is drawn with the right icon. The labels
#: are named_places()'s own: the settings keys and the paths are untouched here.
PLACE_KINDS = {"My scratch": "scratch", "Home": "home", "HOPE-MD runs": "runs",
               "Shared runs": "runs", "Shared install": "group"}
#: The order the browser lists the folders of this install in. The other tools' run folders come
#: after them, worked out below.
PLACE_ORDER = (("My scratch", ""), ("Home", ""), ("HOPE-MD runs", ""), ("Shared runs", ""),
               ("Shared install", ""))

TOOL_RUNS = (
    ("RFdiffusion", ("$SCRATCH/rfdiffusion_runs",)),
    ("RFdiffusion3", ("$SCRATCH/rfd3_runs",)),
    ("RFD4-Proteina", ("$SCRATCH/rfd4_runs",)),
    ("BoltzGen", ("$SCRATCH/boltzgen_runs",)),
    ("Boltz-2", ("$SCRATCH/boltz2_runs",)),
    ("ColabFold", ("$SCRATCH/colabfold_runs",)),
    ("BioEmu", ("$SCRATCH/bioemu_runs",)),
)
#: Where a group's shared space is on the clusters we run on.
GROUP_BASES = ("/scratch/group", "/group", "/work/group")


def _me() -> str:
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except (OSError, KeyError, ImportError):
        return os.environ.get("USER") or ""


def _expand(path: str) -> str:
    """A run folder as the tool catalogue writes it: $SCRATCH, $HOME and $USER filled in."""
    mine = os.environ.get("SCRATCH") or ("/scratch/user/%s" % _me() if _me() else "")
    text = (str(path or "").replace("$SCRATCH", mine or "$SCRATCH")
            .replace("$USER", _me()).replace("$HOME", os.path.expanduser("~")))
    return os.path.expanduser(os.path.expandvars(text))


def api_places() -> dict:
    """Where the structure browser offers to start (/static/picker.js asks for this).

    The same shapes hope_labs/picker.py places() answers for the HOPE Labs pages, and worked out
    the same way: this install's own folders, then the run folders of the lab's other tools under
    a heading of their own, then every unix group's shared space. A folder is offered only when
    it is there and can be opened - a place that answers "no permission" when it is pressed is a
    place that should not have been offered - no path is offered twice however many names reach
    it, and two places left with one name say which is which by the folder they are in."""
    out: list[dict] = []
    seen: set[str] = set()

    def add(path: str, label: str, kind: str, section: str = "") -> None:
        if not path:
            return
        real = os.path.realpath(os.path.expanduser(path))
        if real in seen or not os.path.isdir(real) or not os.access(real, os.R_OK | os.X_OK):
            return
        seen.add(real)
        out.append({"label": label, "path": real, "kind": kind, "section": section})

    named = named_places()
    for label, section in PLACE_ORDER:
        if label in named:
            add(named[label], label, PLACE_KINDS.get(label, "folder"), section)
    for label, path in named.items():                 # anything a site adds later still appears
        add(path, label, PLACE_KINDS.get(label, "folder"), "")
    for name, roots in TOOL_RUNS:
        for root in roots:
            path = _expand(root)
            shared = os.path.realpath(path).startswith(("/scratch/group/", "/group/"))
            add(path, "%s %sruns" % (name, "group " if shared and len(roots) > 1 else ""),
                "runs", "Other tools")
    me = _me()
    try:
        import grp
        names = [grp.getgrgid(g).gr_name for g in os.getgroups()]
    except (OSError, KeyError, ImportError):
        names = []
    for name in names:
        if name == me:          # a person's own primary group is not a shared folder
            continue
        for base in GROUP_BASES:
            add(os.path.join(base, name), "%s group" % name, "group", "Groups")
    labels = [one["label"] for one in out]
    for one in out:
        if labels.count(one["label"]) > 1:
            one["label"] = "%s (%s)" % (one["label"], os.path.basename(os.path.dirname(one["path"])))
    return {"places": out}


#: A structure file, for the run-folder search below and for what an upload may be.
STRUCTURE_SUFFIXES = (".pdb", ".ent", ".cif", ".mmcif")


def run_structure(folder: Path) -> dict | None:
    """{"name", "tool", "kind", "structure", "file"} for a folder that is a run of one of the
    lab's tools, else None.

    A run folder keeps its structure a level or two down (input/, poses/, docking/), so somebody
    walking into one would otherwise have to know where to look. The browser offers "Use this
    run's structure" instead, and `kind` is the source kind HOPE-MD reads it as. The markers are
    the ones the importers already look for, so nothing here can offer a folder they refuse."""
    if (folder / "spec.json").is_file():
        for pattern in ("input/original_*", "input/original.*", "input/complex.pdb",
                        "build/prepared.pdb", "analysis/r1_view.pdb", "input/receptor.pdb"):
            hits = sorted(f for f in folder.glob(pattern)
                          if f.is_file() and f.suffix.lower() in STRUCTURE_SUFFIXES)
            if hits:
                return {"name": folder.name, "tool": "HOPE-MD", "kind": "complex",
                        "structure": str(hits[0]), "file": hits[0].name}
        return None
    return None


#: The most a structure sent up from somebody's own computer may be, before base64. A PDB of a
#: large complex is a few megabytes; the cap on the whole request in do_POST is larger again.
UPLOAD_CAP = 12 << 20
#: What that file may be: what the page can read, and nothing else.
UPLOAD_SUFFIXES = STRUCTURE_SUFFIXES + (".mae", ".maegz", ".zip", ".sdf", ".mol2", ".mol", ".gz")


def api_upload(body: dict) -> dict:
    """A structure chosen on This computer in the browser, kept under the runs root.

    It is then read from there like any other path, so every way in meets the same import."""
    name = os.path.basename(str(body.get("name") or "")).strip()
    if not name or name.startswith("."):
        return {"error": "that file has no name this page can use"}
    suffix = os.path.splitext(name)[1].lower()
    if suffix not in UPLOAD_SUFFIXES:
        return {"error": f"HOPE-MD reads {', '.join(UPLOAD_SUFFIXES)}; {suffix or 'a file with no extension'} is not one of them"}
    try:
        data = base64.b64decode(str(body.get("data") or ""), validate=True)
    except (ValueError, TypeError):
        return {"error": "that file did not arrive whole; try it again"}
    if not data:
        return {"error": "that file is empty"}
    if len(data) > UPLOAD_CAP:
        return {"error": f"that file is larger than {UPLOAD_CAP >> 20} MB: put it on the cluster and choose it there"}
    where = S.runs / "_uploads"
    try:
        where.mkdir(parents=True, exist_ok=True)
        target = where / f"{time.strftime('%Y%m%d-%H%M%S')}_{re.sub(r'[^A-Za-z0-9._-]+', '_', name)}"
        target.write_bytes(data)
    except OSError as e:
        return {"error": f"it could not be kept in {where}: {e.strerror or e}"}
    return {"path": str(target), "name": name}


def default_spec() -> Spec:
    s = Spec()
    s.compute.site = S.site_key
    s.apply_preset("standard")
    return s


# ----------------------------------------------------------------------------- handlers
def api_site(params: dict) -> dict:
    key = params.get("site", [S.site_key])[0]
    site = SITES[key]
    return {
        "site": key, "name": site.name, "version": __version__, "commit": CODE["commit"], "dry_run": S.dry_run,
        "copyright": COPYRIGHT, "copyright_short": COPYRIGHT_SHORT,
        "credits": list(CREDITS), "how_to_cite": HOW_TO_CITE, "acknowledge": ACKNOWLEDGE,
        "viewer_references": [{"name": n, "when": w, "reference": r, "doi": d} for n, w, r, d in VIEWER_REFERENCES],
        "sbatch": q.sbatch_available(), "runs_root": str(S.runs), "places": named_places(),
        "partitions": {k: {"max_hours": p.max_hours, "gpu": p.gpu, "gres": list(p.gres_types)}
                       for k, p in site.partitions.items()},
        "gpu_partition": site.gpu_partition, "cpu_partition": site.cpu_partition,
        # The run plan names where each stage happens; Prepare has a partition of its own.
        "prep_partition": site.prep_partition,
        "gpu_types": site.gpu_types(), "gpu_free": q.gpu_availability(site),
        "default_gpu": site.default_gpu, "cpu_cores_per_node": site.cpu_cores_per_node,
        "engines": ENGINE_LABELS, "engines_ready": ["amber", "openmm", "gromacs", "namd", "desmond"],
        "mmgbsa_tools": {k: engine_for(k).MMGBSA_TOOL for k in ("amber", "openmm", "gromacs", "namd", "desmond")}, "presets": PRESETS, "cpu_max_ns": CPU_MAX_NS, "cpu_max_frames": CPU_MAX_FRAMES,
        "forcefield_notes": {k: engine_for(k).FORCEFIELD_NOTES for k in ("amber", "openmm", "gromacs", "namd", "desmond")},
        "source_kinds": SOURCE_KINDS,
        "modules": {k: list(v) for k, v in site.modules.items()},
        "notes": site.notes, "default_spec": default_spec().to_dict(),
    }


def api_validate(body: dict) -> dict:
    spec = Spec.from_dict(body.get("spec", {}))
    if not spec.name:
        spec.name = spec.default_name()
    if needs_prepare(spec):
        # until Prepare has split the complex, the partner is what the chosen chains are: DNA or RNA, protein, or none
        spec.system.ligand_class = partner_class(spec.system)
    errors = validate(spec)
    try:
        d = derive(spec, availability=q.gpu_availability(spec.site))
    except Exception as e:   # a derived value that cannot be computed is itself an error
        d = {}
        errors.append({"field": "compute.site", "message": f"cannot derive: {e}"})
    if d:
        try:                 # the space the run would go into, beside what it adds; nothing shown when unread
            from .quota import check
            qc = check(spec, d, S.runs)
            if qc["level"] != "unknown":
                d["quota"] = {"level": qc["level"], "text": qc["text"]}
            if qc["level"] == "full":
                errors.append({"field": "quota", "message": qc["text"]})
        except Exception:
            pass
    return {"spec": spec.to_dict(), "errors": errors, "derived": d, "choices": choices(spec)}


def api_preset(body: dict) -> dict:
    spec = Spec.from_dict(body.get("spec", {}))
    spec.apply_preset(body.get("preset", "standard"))
    return api_validate({"spec": spec.to_dict()})


class ImportRefused(Exception):
    """An import that cannot go on, with the sentence for the page."""


def _import_options(body: dict) -> dict:
    """What an import of files applies of the page's System fields: the receptor chains kept (chains),
    the crystal waters (keep_waters) and the metal ions (keep_metals)."""
    return {"chains": [str(c).strip() for c in body.get("chains") or [] if str(c).strip()],
            "waters": str(body.get("keep_waters") or "none"), "keep_metals": bool(body.get("keep_metals"))}


def _stage_import(kind: str, src: dict, body: dict) -> tuple[dict, Path]:
    """Bring one source into a fresh staging folder: the detected System fields (with any
    import note under "note") and the folder. Raises ImportRefused with the reason."""
    stage = S.runs / "_staging" / secrets.token_hex(4)
    stage.mkdir(parents=True, exist_ok=True)
    opts = _import_options(body)
    if kind == "files":
        rec = os.path.expanduser(src.get("path", ""))
        lig = os.path.expanduser(src.get("ligand_path", "")) or None
        if not os.path.isfile(rec):
            raise ImportRefused(f"receptor file not found: {rec}")
        if lig and not os.path.isfile(lig):
            raise ImportRefused(f"ligand file not found: {lig}")
        try:
            rec = _materialise(rec, stage, 1)
            lig = _materialise(lig, stage, 1) if lig else None
            sysm = import_files(rec, lig, stage, **opts)
        except ValueError as e:
            raise ImportRefused(str(e))
    elif kind == "poseviewer":
        # Maestro's poseviewer: the receptor first, then the poses; pose k is structure k + 1
        path = os.path.expanduser(src.get("path", ""))
        if not os.path.isfile(path):
            raise ImportRefused(f"poseviewer file not found: {path}")
        try:
            pose = max(1, int(src.get("pose", 1)))
            rec = _materialise(path, stage, 1, "receptor")
            lig = _materialise(path, stage, pose + 1, f"pose{pose}")
            sysm = import_files(rec, lig, stage, **opts)
        except ValueError as e:
            raise ImportRefused(str(e))
    elif kind in ("complex", "rcsb", "alphafold"):
        sysm, note = None, ""
        if kind == "complex":
            try:
                src = dict(src, path=_materialise(os.path.expanduser(src.get("path", "")), stage, 1))
            except ValueError as e:
                raise ImportRefused(str(e))
        try:
            if kind == "rcsb" and body.get("environment") == "membrane" and (body.get("membrane") or {}).get("orientation") == "opm":
                sysm, note = _import_opm(src, stage)      # (system, note), or (None, why the RCSB file is used instead)
            if sysm is None:
                sysm = import_complex(kind, src, stage)
            # the small molecules bound in it, any of which can be the partner; the one chosen is made it
            from .importers.bound_ligand import bound_ligands, take_bound_ligand
            sysm["ligands_found"] = bound_ligands(stage)
            if str(src.get("ligand") or "").strip():
                got = take_bound_ligand(stage, str(src["ligand"]), body.get("ph"))
                note = " · ".join(n for n in (note, got.pop("note")) if n)
                sysm["meta"] = {**(sysm.get("meta") or {}), **got.pop("meta")}
                sysm.update(got)
        except (FileNotFoundError, ValueError) as e:
            raise ImportRefused(str(e))
        except Exception as e:
            raise ImportRefused(f"{kind} import failed: {e}")
        if note:
            sysm["note"] = " · ".join(x for x in (note, sysm.get("note", "")) if x)
    else:
        raise ImportRefused(f"unknown source kind {kind!r}")
    # what the last import kept goes with it: a complex keeps its waters and ions in Prepare
    sysm.setdefault("waters_kept", [])
    sysm.setdefault("ions_kept", [])
    sysm["staging"] = stage.name
    return sysm, stage


def api_import(body: dict) -> dict:
    """Bring a source into a staging folder, return the detected fields and the structure."""
    src = body.get("source", {})
    kind = src.get("kind", "files")
    try:
        sysm, stage = _stage_import(kind, src, body)
    except ImportRefused as e:
        return {"error": str(e)}
    note = sysm.pop("note", "")
    # what UniProt (or a hydrophobicity scan) says about transmembrane segments, and the
    # orientation on them when the run asked for it
    typed = str((body.get("membrane") or {}).get("segments", "")).strip()
    if typed:
        try:
            from .annotation import annotate_manual
            sysm["tm_annotation"] = annotate_manual(typed)
        except ValueError as e:
            sysm["tm_annotation"] = {"method": "none", "membrane": False, "chains": {}, "source": "", "summary": f"typed segments not read: {e}"}
    else:
        sysm["tm_annotation"] = _annotate(kind, src, stage, sysm)
        if sysm["tm_annotation"].get("method") == "predicted" and sysm.get("chains_found"):
            _start_blast(stage)            # a Swiss-Prot search in the background; the page polls /api/tm_status
            sysm["tm_annotation"]["pending"] = True
    if body.get("environment") == "membrane" and (body.get("membrane") or {}).get("orientation") == "annotation":
        note = (note + " · " if note else "") + _orient_stage(stage, sysm)
    return {"system": sysm, "structure": _complex_pdb(stage), **({"note": note} if note else {})}


def _blast_email() -> str:
    return os.environ.get("HOPEMD_EMAIL") or f"{os.environ.get('USER', 'hopemd')}@tamu.edu"


def _start_blast(stage: Path) -> None:
    """BLAST every protein chain of the staged structure against Swiss-Prot (EBI's service,
    about a minute) in a thread of the server; the answer lands in <stage>/tm_annotation.json
    and the page picks it up. A login-node thread, not a job: it is a web request."""
    import threading
    from .annotation import annotate_by_blast
    text = _complex_pdb(stage)
    def work():
        try:
            a = annotate_by_blast(text, _blast_email())
        except Exception as e:
            a = {"method": "predicted", "membrane": False, "chains": {}, "source": "", "summary": "", "error": f"the BLAST search failed ({type(e).__name__}: {e})"}
        try:
            (stage / "tm_annotation.json").write_text(json.dumps(a))
        except OSError:
            pass
    threading.Thread(target=work, daemon=True).start()


def api_tm_status(params: dict) -> dict:
    """The BLAST annotation of a staging folder when it has arrived, else pending."""
    staging = params.get("staging", [""])[0]
    p = S.runs / "_staging" / staging / "tm_annotation.json"
    if not staging or "/" in staging or not p.exists():
        return {"pending": True}
    try:
        a = json.loads(p.read_text())
    except ValueError:
        return {"pending": True}
    if a.get("error"):
        return {"pending": False, "error": a["error"]}
    return {"pending": False, "tm_annotation": a}


def api_tm_orient(body: dict) -> dict:
    """Orient the staged structure on segments now known: the BLAST answer, or ones typed
    (body.segments). Returns the system fields that changed and the structure to redraw."""
    staging = str(body.get("staging", ""))
    stage = S.runs / "_staging" / staging
    if not staging or "/" in staging or not (stage / "input").exists():
        return {"error": "no such staging folder; import again"}
    typed = str(body.get("segments", "")).strip()
    sysm: dict = {"membrane": {}, "meta": dict(body.get("meta") or {})}
    if typed:
        try:
            from .annotation import annotate_manual
            sysm["tm_annotation"] = annotate_manual(typed)
        except ValueError as e:
            return {"error": f"typed segments not read: {e}"}
    else:
        st = api_tm_status({"staging": [staging]})
        if st.get("tm_annotation"):
            sysm["tm_annotation"] = st["tm_annotation"]
        elif body.get("tm_annotation"):
            sysm["tm_annotation"] = body["tm_annotation"]
        else:
            return {"error": "no transmembrane segments known yet"}
    note = _orient_stage(stage, sysm) if body.get("orient", True) else ""
    return {"system": sysm, "structure": _complex_pdb(stage), "note": note}


def _annotate(kind: str, src: dict, stage: Path, sysm: dict) -> dict:
    """Transmembrane segments for the staged structure: UniProt through PDBe's SIFTS for an
    RCSB entry (or a file whose HEADER carries the entry's code), UniProt by accession for an
    AlphaFold job named after one, else a hydrophobicity scan of the sequence. Never raises:
    a web service that is down is a note on the result, not a failed import."""
    from . import annotation as ann
    chains = [c["id"] for c in sysm.get("chains_found", []) if c.get("kind") == "protein"]
    a: dict = {}
    try:
        pid = str(src.get("pdb_id", "")).strip()
        if kind == "complex" and not pid:
            try:
                head = Path(os.path.expanduser(src.get("path", ""))).read_text(errors="replace")[:4000]
                m = re.search(r"^HEADER.{56}([0-9][A-Za-z0-9]{3})", head, re.M)
                pid = m.group(1) if m else ""
            except OSError:
                pid = ""
        if kind in ("rcsb", "complex") and pid:
            a = ann.annotate_pdb_entry(pid, chains)
        elif kind == "alphafold":
            acc = ann.accession_in_text(Path(os.path.expanduser(src.get("path", ""))).name)
            if acc:
                a = ann.annotate_accession(acc, chains)
        if not a.get("chains"):
            a = ann.annotate_by_sequence(_complex_pdb(stage))
    except Exception as e:  # no network, a changed API: the scan still says something
        try:
            a = ann.annotate_by_sequence(_complex_pdb(stage))
            a["note"] = f"UniProt lookup failed ({type(e).__name__}); the sequence was scanned instead"
        except Exception as e2:
            a = {"method": "none", "membrane": False, "chains": {}, "source": "", "summary": f"no annotation ({e2})"}
    return a


def _orient_stage(stage: Path, sysm: dict) -> str:
    """Rotate the staged structure so the transmembrane segments' axis is z and their center
    the origin (hopemd.annotation.orient_by_segments); the receptor and ligand files together
    when Prepare has split them. The extent follows the new frame; the membrane center is 0."""
    from .annotation import orient_by_segments
    ann = sysm.get("tm_annotation") or {}
    if not ann.get("chains"):
        return "no transmembrane segments to orient on: the structure is as imported"
    inp = stage / "input"
    names = [n for n in ("receptor.pdb", "ligand.pdb", "ions.pdb", "waters.pdb") if (inp / n).exists()] or (["complex.pdb"] if (inp / "complex.pdb").exists() else [])
    if not names:
        return "nothing staged to orient"
    texts = [(inp / n).read_text(errors="replace") for n in names]
    counts = [len(t.splitlines()) for t in texts]
    joined = "\n".join(t.rstrip("\n") for t in texts) + "\n"
    out, info = orient_by_segments(joined, ann["chains"])
    if not info.get("segments"):
        return info.get("note", "not oriented")
    lines = out.splitlines()
    pos = 0
    for n, cnt in zip(names, counts):
        (inp / n).write_text("\n".join(lines[pos:pos + cnt]) + "\n")
        pos += cnt
    xs, ys, zs = [], [], []
    for line in out.splitlines():
        if line.startswith(("ATOM", "HETATM")) and len(line) >= 54:
            try:
                xs.append(float(line[30:38])); ys.append(float(line[38:46])); zs.append(float(line[46:54]))
            except ValueError:
                pass
    if xs:
        sysm["extent_A"] = [round(max(xs) - min(xs), 1), round(max(ys) - min(ys), 1), round(max(zs) - min(zs), 1)]
    # and so does tleap's rectangular box
    if names == ["complex.pdb"]:
        from .importers.complex import complex_shape
        predicted = bool((sysm.get("confidence") or {}).get("plddt"))
        sysm["solute_shape"] = complex_shape(out, trim_plddt=Prep().trim_plddt if predicted else 0.0)["solute_shape"]
    else:
        from .importers.files import staged_solute
        from .solvation import solute_shape
        lig = inp / "ligand.pdb"
        sysm["solute_shape"] = solute_shape(staged_solute(stage, lig if lig.exists() else None))
    sysm["membrane"] = dict(sysm.get("membrane") or {}, center_z_A=0.0)
    meta = dict(sysm.get("meta") or {}); meta["orientation"] = "annotation"; meta["tm_source"] = ann.get("source", "")
    sysm["meta"] = meta
    return info["note"]


def _materialise(path: str, stage: Path, ct: int = 1, tag: str = "") -> str:
    """A file the importers read: a .pdb or .cif as it is; a .mae/.maegz turned into PDB text
    (structure block `ct`, 1-based: the receptor first in a poseviewer) and a gzipped .pdb/.cif
    unpacked, both written under the staging folder. Nothing else is touched."""
    from .importers.maestro import is_maestro, read_mae
    p = Path(path)
    if not p.is_file():
        return path
    inp = stage / "input"; inp.mkdir(parents=True, exist_ok=True)
    if is_maestro(p):
        cts = read_mae(p)
        if not cts:
            raise ValueError(f"{p.name}: no structure block in this Maestro file")
        if ct > len(cts):
            raise ValueError(f"{p.name} has {len(cts)} structure{'s' if len(cts) != 1 else ''}: no pose {ct - 1}")
        block = cts[ct - 1]
        # a ligand block goes on as SDF, with the bond orders and formal charges the force fields need
        ext, text = (".sdf", block["sdf"]) if block.get("sdf") else (".pdb", block["pdb"])
        out = inp / f"original_{tag or p.stem.replace('.mae', '')}_ct{ct}{ext}"
        out.write_text(text)
        return str(out)
    if p.suffix.lower() == ".gz":
        import gzip
        inner = p.name[:-3]
        out = inp / f"original_{inner}"
        out.write_bytes(gzip.open(p, "rb").read())
        return str(out)
    return path


def _import_opm(src: dict, stage: Path) -> tuple[dict | None, str]:
    """A membrane run that asked for the OPM orientation: the entry's coordinates from the OPM
    (opm.phar.umich.edu), whose membrane normal is z and whose membrane center is z = 0, in
    place of the RCSB's. The DUM atoms that mark the two membrane planes are dropped by
    hopemd.membrane.fetch_opm; their half thickness travels in system.meta. On any failure
    (no OPM entry, no network, the module missing) the caller falls back to the RCSB file
    and the note says so, so nothing is silently the wrong way up."""
    pid = str(src.get("pdb_id", "")).strip()
    try:
        from .membrane import fetch_opm
        text, half = fetch_opm(pid)
    except Exception as e:
        return None, (f"OPM has no oriented coordinates for {pid.upper()} ({e}); the RCSB file was imported as deposited: "
                      "check in the viewer that the membrane normal is z, or orient it first")
    inp = stage / "input"; inp.mkdir(parents=True, exist_ok=True)
    f = inp / f"original_{pid.upper()}_opm.pdb"
    f.write_text(text)
    sysm = import_complex("complex", {"path": str(f)}, stage)
    # the entry's coordinates, so its B-factors are the experiment's whatever their values (as Prepare reads them)
    sysm["confidence"] = {k: v for k, v in (sysm.get("confidence") or {}).items() if k != "plddt"}
    meta = dict(sysm.get("meta") or {})
    try:                                   # the entry's title and resolution, as the RCSB import records them
        from .importers.complex import _get, RCSB_ENTRY
        e = json.loads(_get(RCSB_ENTRY.format(pid.upper()), timeout=30))
        meta.update(title=e.get("struct", {}).get("title", ""),
                    resolution_A=(e.get("rcsb_entry_info", {}).get("resolution_combined") or [None])[0],
                    released=e.get("rcsb_accession_info", {}).get("initial_release_date", "")[:10])
    except Exception:
        pass
    meta["opm_half_thickness_A"] = half            # None when the OPM file carried no DUM planes
    meta["orientation"] = "opm"
    sysm["meta"] = meta
    sysm["membrane"] = {"center_z_A": 0.0}
    return sysm, (f"OPM-oriented coordinates for {pid.upper()}: the membrane normal is z, the center z = 0, "
                  + (f"the planes at ±{half:g} Å" if half is not None else "no membrane planes (DUM atoms) in the file: is this a membrane entry?"))


def api_prepare(body: dict) -> dict:
    """The Prepare stage on the staged import. On the cluster it is a job on the short CPU
    partition (compute runs on compute nodes, never on a login node) and the page polls
    /api/prepare_status; on a laptop, or in a dry run, it runs inline."""
    spec = Spec.from_dict(body.get("spec", {}))
    staging = body.get("staging")
    stage = _staging_dir(staging)
    if not staging or not stage.exists():
        return {"error": "import a structure first"}
    # a split Prepare cannot make (a chain on both sides, one the file has not, modified nucleotides) is refused here
    refused = [e["message"] for e in validate(spec) if e["field"] in ("system.receptor_chains", "system.partner_chains")]
    if refused:
        return {"error": " ".join(refused)}
    if S.dry_run or not q.sbatch_available():
        try:
            report = prepare(stage, spec.to_dict())
        except FileNotFoundError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"prepare failed: {e}"}
        return _prepare_result(spec, stage, report)
    jobfile = stage / "prepare.job"
    if jobfile.exists():
        live = q.job_state(jobfile.read_text().strip())
        if live in q.SLURM_STATES_LIVE and not (stage / "build" / "prep_report.json").exists():
            return {"queued": True, "job": jobfile.read_text().strip(), "partition": spec.site.prep_partition, "staging": stage.name}
    (stage / "spec.json").write_text(spec.to_json())      # what the job prepares with
    for stale in (stage / "build" / "prep_report.json", jobfile):
        if stale.exists():
            stale.unlink()
    (stage / "prepare.sbatch").write_text(q.prepare_job_script(spec, stage))
    try:
        r = subprocess.run(["sbatch", "--parsable", "prepare.sbatch"], cwd=stage, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"error": f"sbatch failed for the Prepare job: {e}"}
    if r.returncode != 0:
        return {"error": f"sbatch refused the Prepare job: {r.stderr.strip()}"}
    jid = r.stdout.strip().split(";")[0]
    jobfile.write_text(jid)
    return {"queued": True, "job": jid, "partition": spec.site.prep_partition, "staging": stage.name}


def api_prepare_status(params: dict) -> dict:
    """Where the Prepare job is: queued, running, failed (with the log), or done (with the result)."""
    staging = params.get("staging", [""])[0]
    stage = _staging_dir(staging)
    if not staging or not stage.exists():
        return {"error": "import a structure first"}
    jobfile = stage / "prepare.job"
    jid = jobfile.read_text().strip() if jobfile.exists() else ""
    report_p = stage / "build" / "prep_report.json"
    if report_p.exists():
        try:
            report = json.loads(report_p.read_text())
        except ValueError:
            report = {}
        if report.get("finished"):
            spec = Spec.from_json((stage / "spec.json").read_text()) if (stage / "spec.json").exists() else Spec()
            out = _prepare_result(spec, stage, report)
            out.update(state="done", job=jid)
            return out
    st = q.job_state(jid)
    if st in q.SLURM_STATES_LIVE:
        return {"state": "running" if st == "RUNNING" else "queued", "job": jid, "slurm": st}
    logs = sorted((stage / "logs").glob("prepare.*.out"), key=lambda p: p.stat().st_mtime) if (stage / "logs").exists() else []
    tail = logs[-1].read_text(errors="replace")[-1500:] if logs else ""
    return {"state": "failed", "job": jid, "slurm": st or "unknown",
            "error": f"the Prepare job {jid or '?'} ended ({st or 'not in the queue'}) without a report" + (": " + tail.strip().splitlines()[-1] if tail.strip() else ""),
            "log": tail}


def _prepare_result(spec: Spec, stage: Path, report: dict) -> dict:
    """The reply the page merges into its spec: the detected fields the stage changed."""
    # re-detect from the split, prepared files so the masks and the box estimate are right
    lig = stage / "input" / "ligand.pdb"
    sysm = detect_inputs(stage, lig if lig.exists() else None)
    # partner chains of DNA or RNA are a nucleic-acid partner (detect_inputs reads them so); any other chains a protein one
    sysm["ligand_class"] = ("nucleic_acid" if sysm["ligand_class"] == "nucleic_acid" else "protein") if lig.exists() else "none"
    if not lig.exists() and (stage / "input" / "ligand.sdf").exists():
        sysm["ligand_class"] = "small_molecule"          # a small molecule bound in the entry, chosen at import
    sysm["prepared"] = True
    sysm["breaks"] = report.get("breaks", [])
    sysm["capped"] = [c["break"] for c in report.get("caps") or []]
    sysm["stretched"] = report.get("stretched", [])
    sysm["waters_kept"] = (report.get("steps", {}).get("clean") or {}).get("waters_kept") or []
    # detect_inputs saw the receptor file only; the partner chains are found too, or the
    # partner-chain check refuses a chain that is plainly in the file
    if lig.exists():
        sysm["chains_found"] = sysm["chains_found"] + [c.__dict__ for c in inspect_pdb(lig).chains]
    # what the import detected and Prepare does not re-measure travels with the spec
    for k in ("hetero_found", "gaps_found", "confidence", "meta", "waters", "altlocs", "models", "tm_annotation"):
        v = getattr(spec.system, k)
        if v:
            sysm[k] = v
    sysm["prep_report"] = {"log": report.get("log", []), "clashes": report.get("clashes"), "split": report.get("split"),
                           "placement": report.get("placement"),
                           "steps": {k: (v if not isinstance(v, dict) else {kk: vv for kk, vv in v.items() if kk != "kept_hetero"})
                                     for k, v in report.get("steps", {}).items()}}
    sysm["receptor_chains"] = report.get("split", {}).get("receptor_chains", [])
    sysm["partner_chains"] = report.get("split", {}).get("partner_chains", [])
    # the carried ions that bind the partner follow it in every build (input/ions.pdb), inside its range
    n_ions = int(report.get("split", {}).get("partner_ions") or 0)
    if n_ions and sysm.get("ligand_residues"):
        sysm["ligand_residues"] = [sysm["ligand_residues"][0], sysm["ligand_residues"][1] + n_ions]
    return {"system": sysm, "structure": _complex_pdb(stage), "report": report}


LIGAND_MARK = "REMARK 999 HOPEMD LIGAND"


def _complex_pdb(rundir: Path) -> str:
    """Receptor then ligand, separated by a REMARK the page splits on to load two models. Before
    Prepare has split a complex, the whole imported complex is sent as one model. A small molecule
    in SDF (a ligand file, or a bound ligand taken out of the complex) is drawn as the second."""
    parts = []
    for name in ("receptor.pdb", "ligand.pdb"):
        p = rundir / "input" / name
        if p.exists():
            body = "\n".join(l for l in p.read_text(errors="replace").splitlines()
                             if l.startswith(("ATOM", "HETATM", "TER")))
            for extra in ("ions.pdb", "waters.pdb"):          # the ions and crystal waters the run keeps, drawn with the receptor
                f = rundir / "input" / extra
                if name == "receptor.pdb" and f.exists():
                    body += "\n" + "\n".join(l for l in f.read_text(errors="replace").splitlines() if l.startswith(("HETATM", "TER")))
            parts.append((LIGAND_MARK + "\n" if name == "ligand.pdb" else "") + body + "\nEND")
    if not parts and (rundir / "input" / "complex.pdb").exists():
        body = "\n".join(l for l in (rundir / "input" / "complex.pdb").read_text(errors="replace").splitlines()
                         if l.startswith(("ATOM", "HETATM", "TER")))
        parts.append(body + "\nEND")
    sdf = rundir / "input" / "ligand.sdf"
    if parts and sdf.exists() and not (rundir / "input" / "ligand.pdb").exists():
        from .importers.bound_ligand import sdf_as_pdb
        parts.append(LIGAND_MARK + "\n" + sdf_as_pdb(sdf) + "\nEND")
    return "\n".join(parts) + "\n"


def api_poses(params: dict) -> dict:
    """What a many-pose source holds, for the batch box: a poseviewer's poses, the title of each structure after the receptor."""
    kind = params.get("kind", [""])[0]
    path = os.path.expanduser(params.get("path", [""])[0])
    if not path or not os.path.exists(path):
        return {"error": "no such path"}
    if kind == "poseviewer":
        from .importers.maestro import read_mae, is_maestro
        if not is_maestro(path):
            return {"error": "not a Maestro file"}
        cts = read_mae(path)
        return {"kind": kind, "receptor": cts[0]["title"] if cts else "", "poses": [{"pose": i, "title": c["title"]} for i, c in enumerate(cts[1:], 1)]}
    return {"error": f"{kind or 'this source'} holds one structure; the batch is for a poseviewer"}


def api_submit_batch(body: dict) -> dict:
    """One run per selection, with the settings of the spec given: each selection is imported
    into its own staging folder, its detected fields replace the loaded pose's, the name is
    derived (peptide, pose, engine), and it is validated, rendered and submitted like a single
    run. selections: [{"peptide": ..., "pose": k}, ...]. Every result is reported, refusals
    included; the ones that pass are queued. The notes a single Submit shows (the OPLS4 license
    among them) come back once, under "notes"."""
    base = body.get("spec", {})
    kind = (base.get("system", {}).get("source") or {}).get("kind", "")
    if kind not in ("poseviewer",):
        return {"error": "the batch is for a poseviewer"}
    selections = body.get("selections") or []
    if not selections:
        return {"error": "nothing selected"}
    keep = ("prep", "ph", "environment", "membrane", "keep_waters", "keep_cofactors", "keep_metals", "chains")
    out, log, notes = [], [], []
    for sel in selections[:100]:
        spec_d = json.loads(json.dumps(base))
        src = dict(spec_d["system"]["source"])
        src["pose"] = int(sel.get("pose", 1))
        if sel.get("peptide"):
            src["peptide"] = str(sel["peptide"])
        label = f"pose {src['pose']}"
        try:
            sysm, stage = _stage_import(kind, src, spec_d.get("system", {}))
        except ImportRefused as e:
            out.append({"selection": label, "error": str(e)}); log.append(f"{label}: {e}"); continue
        sysm.pop("note", None); staging = sysm.pop("staging")
        # a small molecule's net charge is its own pose's: the charge the person confirmed on the
        # loaded pose holds for a pose that reads the same, and a pose with no charge in its file
        # (a PDB) takes the confirmed one; any other pose has to be loaded and confirmed itself
        if sysm.get("ligand_class") == "small_molecule":
            q, q0 = sysm.get("ligand_charge"), spec_d["system"].get("ligand_charge")
            confirmed = bool(spec_d["system"].get("ligand_charge_confirmed")) and q0 is not None
            if q is None and confirmed:
                sysm["ligand_charge"], sysm["ligand_charge_confirmed"] = q0, True
            elif q is not None and confirmed and q == q0:
                sysm["ligand_charge_confirmed"] = True
            elif q is not None and confirmed:
                msg = f"net charge {q:+d} in this pose's file, not the confirmed {q0:+d}: load the pose and confirm its charge"
                out.append({"selection": label, "error": msg}); log.append(f"{label}: refused: {msg}"); _clear_staging(staging); continue
        kept = {k: spec_d["system"].get(k) for k in keep if k in spec_d["system"]}
        spec_d["system"].update(sysm); spec_d["system"].update(kept); spec_d["system"]["source"] = src
        spec_d["name"] = ""
        r = api_render({"spec": spec_d, "staging": staging}, submit=True)
        if r.get("errors"):
            msg = "; ".join(e["message"] for e in r["errors"])
            out.append({"selection": label, "error": msg}); log.append(f"{label}: refused: {msg}"); _clear_staging(staging)
        elif r.get("error"):
            out.append({"selection": label, "error": r["error"]}); log.append(f"{label}: {r['error']}"); _clear_staging(staging)
        else:
            out.append({"selection": label, "name": r["name"], "rundir": r.get("rundir"), "jobs": (r.get("submitted") or {}).get("jobs", {})})
            log.append(f"{label}: submitted as {r['name']}" + (" (dry run)" if (r.get('submitted') or {}).get('dry_run') else ""))
            notes += [n for n in r.get("notes", []) if n not in notes]
    return {"runs": out, "log": log, "dry_run": S.dry_run, "notes": notes}


def kept_ions_note(spec: Spec) -> str:
    """The run's note on the ions it keeps: which go into the build and as what, which side MM-GBSA
    counts them with, and what a metal site lacks. From Prepare's report once it has run, or from what an
    import of files kept; before Prepare, from the import and the options."""
    from .spec import clean_keeps, needs_prepare
    from .prepare import SITE_METALS
    sysm = spec.system
    if sysm.prepared or (not needs_prepare(spec) and sysm.ions_kept):
        carried = (((sysm.prep_report or {}).get("steps") or {}).get("protonate", {}).get("carried") or []) if sysm.prepared \
            else sysm.ions_kept
        names = [c["group"] for c in carried]
        sites = [c["group"] for c in carried if c["group"].split()[0] in SITE_METALS and c.get("site")]
        partner = [c["group"] for c in carried if c.get("side") == "partner"]
        what = "Carried into the build by Prepare" if sysm.prepared else "Kept by the import"
        known = True
    elif needs_prepare(spec):
        found = clean_keeps(sysm)[0]
        names = [f"{h['resname']} × {h.get('count', 1)}" for h in found]
        sites = [h["resname"] for h in found if h["resname"] in SITE_METALS]
        partner, what, known = [], "Prepare carries into the build", False
    else:
        return ""
    if not names:
        return ""
    params = "OPLS ion parameters" if spec.engine == "desmond" else f"the 12-6 parameters of the {spec.forcefield.water} ion set"
    note = f"{what}, as free ions with {params}: {', '.join(names)}."
    # the analysis keeps them by their place (engines.base.kept_last), whatever their name: none goes with the salt
    others = [n for n in names if n not in partner]
    scored = sysm.ligand_class != "none" and spec.analysis.get("mmgbsa", True)
    if scored and partner:
        note += (f" MM-GBSA counts {', '.join(partner)} with the partner, which binds {'it' if len(partner) == 1 else 'them'}"
                 + (", and any other with the receptor." if others else "."))
    elif scored and others and sysm.ligand_class == "small_molecule":
        note += " MM-GBSA scores the small molecule alone and counts the ions with the receptor."
    elif scored and others:
        note += " MM-GBSA counts them with the receptor." if known else " MM-GBSA counts each with the side whose residues bind it."
    if sites:
        note += (" A metal site is not held by a free ion: no bond ties it to the site. What Prepare can do it does - the residues "
                 "that hold the ion are named for the geometry, a coordinating cysteine as a thiolate and each histidine on the "
                 "tautomer that leaves its coordinating nitrogen free - and on 7R4Z that naming was the difference between a copper "
                 "that closed onto its histidines at 1.9 A in the minimization and one the minimization pushed out to 6 A and that "
                 "never came back. A bonded model such as ZAFF is not built here, so check the site in the minimized structure "
                 "before the run and in the trajectory after it.")
    return note


def kept_waters_note(spec: Spec) -> str:
    """The run's note on the crystal waters it keeps: how many, which, and that the analysis takes them as solvent.
    From the import or Prepare's report once they are known; before Prepare, from its option."""
    from .engines.base import crystal_waters
    from .spec import water_choice, needs_prepare
    from .structure import NEAR_WATER_A
    choice = water_choice(spec.system)
    which = f"those within {NEAR_WATER_A:g} Å of the partner" if choice == "near" else "every one in the structure"
    tail = (f" go into the build after the solute as {spec.forcefield.water} waters: the solvation places no water over them, and "
            "the analysis and MM-GBSA take them as solvent.")
    if needs_prepare(spec):
        return f"Prepare keeps the crystal waters, {which}; they{tail}" if choice != "none" else ""
    waters = crystal_waters(spec)
    if not waters:
        return ""
    shown = ", ".join(waters[:6]) + (" …" if len(waters) > 6 else "")
    return f"{len(waters)} crystal water{'s' if len(waters) != 1 else ''} kept ({which}: {shown}){tail}"


def api_render(body: dict, submit: bool) -> dict:
    spec = Spec.from_dict(body.get("spec", {}))
    if not spec.name:
        spec.name = spec.default_name()
    if needs_prepare(spec):
        spec.system.ligand_class = partner_class(spec.system)      # as api_validate: the chosen split decides
    errors = validate(spec)
    if errors:
        return {"errors": errors}
    if submit:
        choose_gpu_at_submit(spec)
    d = derive(spec, availability=q.gpu_availability(spec.site))
    try:
        render = engine_for(spec.engine).render(spec, d)
    except NotImplementedError as e:
        return {"errors": [{"field": "forcefield.engine", "message": str(e)}]}
    tm = spec.system.tm_annotation or {}
    if tm.get("membrane") and spec.system.environment != "membrane":
        render.notes.append(f"This is an annotated membrane protein ({tm.get('summary', '')}) simulated in a water box: "
                            "a lipid bilayer (System · environment) is what its transmembrane segments expect.")
    elif spec.system.environment == "membrane" and tm.get("method") in ("annotation", "predicted") and not tm.get("membrane"):
        render.notes.append(f"No transmembrane segment was found for this protein ({tm.get('summary', '')}), yet it is set up in a bilayer.")
    if spec.system.stretched:
        # Prepare's minimization closes them; Desmond's build bonds residues by their geometry, and an import of files
        # reaches it with the bonds as long as the file has them
        open_ = spec.engine == "desmond" and not spec.system.prepared
        render.notes.append(f"Kept as bonds, with no residue missing: {', '.join(spec.system.stretched)}. The deposited model stretched them; "
                            + ("Desmond's build bonds residues by their geometry, so it leaves them open: an Amber-topology engine "
                               "builds them as bonds." if open_ else "the build bonds them and the minimization closes them.")
                            + " A residue lost from a renumbered file can leave its neighbors this close: check them in the viewer.")
    for kept in (kept_ions_note(spec), kept_waters_note(spec)):
        if kept:
            render.notes.append(kept)
    out = {"files": [{"path": f.path, "text": f.text} for f in render.files],
           "jobs": [{"key": j.key, "script": j.script, "depends": j.depends, "array": j.array,
                     "gpu": j.gpu, "description": j.description} for j in render.jobs],
           "notes": render.notes, "derived": d, "name": spec.name}
    if not submit:
        return out
    rundir = S.runs / spec.name
    if rundir.exists() and (rundir / "jobs" / "ids.json").exists():
        return {"errors": [{"field": "name", "message": f"{spec.name} exists and was submitted; pick another name."}]}
    from .quota import check as quota_check, commit as quota_commit
    room = quota_check(spec, d, S.runs, render.jobs)
    if room["level"] == "full":
        return {"errors": [{"field": "quota", "message": room["text"]}]}
    if room["level"] in ("close", "unknown"):
        render.notes.append(room["text"])
    staging = str(body.get("staging") or "")
    if staging and not _take_staging(staging, rundir):
        return {"errors": [{"field": "staging", "message": "The imported structure is no longer here (its staging folder was "
                                                         "cleared and no run holds it): load it again."}]}
    if needs_prepare(spec):
        # Prepare opens the graph: a short CPU job that cleans, models, protonates, minimizes and
        # splits the structure, then re-renders the run's files from it; the build follows.
        from .engines.base import Job
        rundir.mkdir(parents=True, exist_ok=True)
        render.add("prepare.sbatch", q.prepare_job_script(spec, rundir, chain=True), executable=True)
        for j in render.jobs:
            if not j.depends:
                j.depends = [("prepare", "afterok")]
        render.jobs.insert(0, Job("prepare", "prepare.sbatch", description="Prepare: clean, model, protonate, minimize, split; then re-render the masks"))
        render.notes.append(f"Prepare runs first, as a job on {spec.site.prep_partition}; the build waits for it. The residue masks in md/ and "
                            "analysis/ are re-rendered by that job from the prepared structure, so the files in the run folder are provisional until it ends.")
        out["jobs"] = [{"key": j.key, "script": j.script, "depends": j.depends, "array": j.array, "gpu": j.gpu, "description": j.description} for j in render.jobs]
        out["notes"] = render.notes
    q.write_render(rundir, spec, render)
    res = q.submit(rundir, render, dry_run=S.dry_run)
    out.update(rundir=str(rundir), submitted=res)
    if len(res["jobs"]) == len(render.jobs):         # the whole graph is queued: the run holds what it needs
        quota_commit(room)
        if staging:
            _clear_staging(staging)
    return out


def _take_staging(staging: str, rundir: Path) -> bool:
    """Copy an import into the run folder: input/ and build/ from its staging folder or, once an
    earlier Submit has cleared that folder, from the run it went into, whose jobs/staging.json lists
    the files (the same structure submitted again with another engine). The list goes into this run
    too, with the Setup screen's Prepare job, whose log joins logs/ so the run's timing covers it.
    False when the import is nowhere to be found."""
    if "/" in staging or staging.startswith("."):
        return False
    stage, prep = S.runs / "_staging" / staging, ""
    if stage.is_dir():
        src = stage
        files = [f"{sub}/{f.name}" for sub in ("input", "build") if (stage / sub).is_dir()
                 for f in sorted((stage / sub).iterdir()) if f.is_file()]
        jobfile = stage / "prepare.job"
        if jobfile.exists() and q.job_state(jobfile.read_text().strip()) not in q.SLURM_STATES_LIVE:
            prep = jobfile.read_text().strip()
    else:
        found = _staging_record(staging)
        if found is None:
            return False
        src, files = found[0], found[1].get("files", [])
    for rel in files:
        if (src / rel).is_file():
            (rundir / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src / rel, rundir / rel)
    record = {"staging": staging, "files": files}
    log = stage / "logs" / f"prepare.{prep}.out"
    if prep and log.exists():
        (rundir / "logs").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(log, rundir / "logs" / log.name)
        record["prepare"] = prep
    (rundir / "jobs").mkdir(parents=True, exist_ok=True)
    (rundir / "jobs" / "staging.json").write_text(json.dumps(record, indent=1))
    return True


def _staging_record(staging: str) -> tuple[Path, dict] | None:
    """The run an import went into, once its staging folder is gone: the newest run folder whose
    jobs/staging.json names it, with that record."""
    best = None
    for p in S.runs.glob("*/jobs/staging.json"):
        try:
            rec = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if rec.get("staging") == staging and (best is None or p.stat().st_mtime > best[0]):
            best = (p.stat().st_mtime, p.parent.parent, rec)
    return (best[1], best[2]) if best else None


def _clear_staging(staging: str) -> None:
    """Remove the staging folder of one import once a run holds its files (or a batch refused it).
    Only that folder, never _staging/ as a whole, and not while a Prepare job of it is queued or
    running: the page is still waiting on that job."""
    stage = S.runs / "_staging" / str(staging)
    if not staging or "/" in str(staging) or str(staging).startswith(".") or not stage.is_dir():
        return
    jobfile = stage / "prepare.job"
    if jobfile.exists() and q.job_state(jobfile.read_text().strip()) in q.SLURM_STATES_LIVE:
        return
    shutil.rmtree(stage, ignore_errors=True)


def choose_gpu_at_submit(spec: Spec) -> str:
    """compute.gpu = auto becomes a real type now, from what sinfo says is free, and is written
    into the spec (compute.gpu_chosen) so the run folder says what it ran on."""
    from .spec import resolve_gpu, GPU_AUTO
    if spec.on_cpu or spec.compute.gpu != GPU_AUTO:
        return spec.gpu
    spec.compute.gpu_chosen = resolve_gpu(spec, q.gpu_availability(spec.site))[0]
    return spec.compute.gpu_chosen


def api_stage_log(params: dict) -> dict:
    rundir = _rundir(params.get("name", [""])[0])
    if rundir is None:
        return {"error": "no such run"}
    stage = params.get("stage", [""])[0]
    if not stage or "/" in stage:
        return {"error": "no stage given"}
    try:
        task = max(1, int(params.get("task", ["1"])[0])); n = min(2000, max(10, int(params.get("n", ["120"])[0])))
    except ValueError:
        task, n = 1, 120
    job = params.get("job", [""])[0]
    return q.stage_log(rundir, stage, task, n, job if job.isdigit() else "")


def api_runs() -> dict:
    return {"root": str(S.runs), "runs": q.list_runs(S.runs)}


def api_run(params: dict) -> dict:
    name = params.get("name", [""])[0]
    rundir = _rundir(name)
    if rundir is None:
        return {"error": "no such run"}
    spec = Spec.from_json((rundir / "spec.json").read_text())
    from .export.video import videos
    from .analysis.dense import dense_windows
    from .analysis.waters import NEAREST, near_what, waters_views
    from .timing import timing, record_timings
    from .spec import chain_ranges
    from .engines.base import own_ranges
    chains = chain_ranges(spec.system)
    out = {"name": name, "rundir": str(rundir), "spec": spec.to_dict(), "progress": q.progress(rundir),
           "log_tail": q.log_tail(rundir), "logs": q.run_logs(rundir), "notes": [], "videos": videos(rundir), "dense": dense_windows(rundir),
           "waters": {"views": waters_views(rundir), "nearest": NEAREST, "near": near_what(spec)[1]},
           "chains": chains}
    # a topology that keeps the input's chain letters and residue numbers (Desmond's): the viewer marks the
    # receptor, the partner and each chain by those, not by the ranges' 1, 2, 3 ...
    sysm = spec.system
    own = own_ranges(rundir, spec, [sysm.receptor_residues, sysm.ligand_residues] + [c["range"] for c in chains])
    if own:
        out["parts"] = {"rec": own[0], "lig": own[1]}
        for c, runs in zip(chains, own[2:]):
            c["own"] = runs
    try:
        from .tidy import status as tidy_status
        out["tidy"] = tidy_status(rundir)
        out["timing"] = timing(rundir)
        record_timings(rundir, out["timing"])      # one row per finished job, beside the run, for the next estimate
    except Exception as e:  # accounting down, a read-only checkout: the page still opens
        out["notes"].append(f"timing unavailable: {e}")
    sp = rundir / "analysis" / "summary.json"
    if (rundir / "analysis").exists():
        try:
            out["summary"] = summarise(rundir)
        except Exception as e:  # never let a bad .dat take the page down
            out["notes"].append(f"summary failed: {e}")
            if sp.exists():
                out["summary"] = json.loads(sp.read_text())
    return out


def _rundir(name: str) -> Path | None:
    if not name or "/" in name or name.startswith("."):
        return None
    p = S.runs / name
    return p if (p / "spec.json").exists() else None


def api_waters(body: dict) -> dict:
    """The strided view again with the waters nearest the partner kept: a cpptraj job."""
    from .analysis.waters import submit_waters
    rundir = _rundir(body.get("name", ""))
    if rundir is None:
        return {"error": "no such run"}
    try:
        rep = max(1, int(body.get("rep", 1)))
    except (TypeError, ValueError):
        return {"error": "the replicate must be a number"}
    try:
        return submit_waters(rundir, rep, dry_run=S.dry_run)
    except (OSError, ValueError) as e:
        return {"error": f"the waters job failed: {e}"}


def api_dense(body: dict) -> dict:
    """A dense window around a moment: ±5 ns at up to 100 frames, cut by a cpptraj job."""
    from .analysis.dense import submit_dense
    rundir = _rundir(body.get("name", ""))
    if rundir is None:
        return {"error": "no such run"}
    try:
        rep = max(1, int(body.get("rep", 1))); ns = float(body.get("ns", 0)); half = float(body.get("half_ns", 5.0))
    except (TypeError, ValueError):
        return {"error": "replicate and ns must be numbers"}
    try:
        return submit_dense(rundir, rep, ns, half, dry_run=S.dry_run)
    except (OSError, ValueError) as e:
        return {"error": f"dense window failed: {e}"}


def api_mmgbsa(body: dict) -> dict:
    """MM-GBSA on a run that has its production: the window from the page goes into the run's
    spec, the MM-GBSA input (analysis/mmpbsa.in, or Prime's table converter on Desmond) and the
    job script are rendered again, the stage joins the graph when it was not there (a run
    submitted without it), and it is submitted with everything after it."""
    rundir = _rundir(body.get("name", ""))
    if rundir is None:
        return {"error": "no such run"}
    spec = Spec.from_json((rundir / "spec.json").read_text())
    engine = engine_for(spec.forcefield.engine)
    if not engine.MMGBSA:
        return {"error": f"{engine.label} has no MM-GBSA here: MMPBSA.py needs an Amber topology and a {engine.label} run has none"}
    if spec.system.ligand_class == "none":
        return {"error": "an apo run has no partner or ligand to score"}
    from .spec import mmgbsa_refused
    if mmgbsa_refused(spec.system):
        return {"error": mmgbsa_refused(spec.system)}
    from .export.video import trajectory_files
    if not trajectory_files(rundir, 1) and not S.dry_run:
        return {"error": "no production trajectory yet: MM-GBSA reads the finished parts"}
    an = dict(spec.analysis)
    an["mmgbsa"] = True
    for key, cast in (("from_ns", float), ("to_ns", float), ("stride_ps", float)):
        if body.get(key) is not None and str(body.get(key)) != "":
            try:
                an["mmgbsa_" + key] = cast(body[key])
            except (TypeError, ValueError):
                return {"error": f"{key}: not a number"}
    spec.analysis = an
    bad = [e for e in validate(spec) if e["field"].startswith("analysis.")]
    if bad:
        return {"error": "; ".join(e["message"] for e in bad)}
    r = engine.render(spec, derive(spec))
    from .provenance import stamp
    stamp(spec, "mmgbsa")
    (rundir / "spec.json").write_text(spec.to_json())
    written = []
    for f in r.files:
        if f.path in ("analysis/mmpbsa.in", "analysis/prime_mmgbsa_to_hopemd.py", "jobs/50_mmgbsa.sbatch") or (f.path == "analysis/ligand_mask.txt" and not (rundir / f.path).exists()):
            (rundir / f.path).parent.mkdir(parents=True, exist_ok=True)
            (rundir / f.path).write_text(f.text)
            if f.executable:
                (rundir / f.path).chmod(0o755)
            written.append(f.path)
    if "jobs/50_mmgbsa.sbatch" not in written:
        return {"error": "the renderer wrote no MM-GBSA job for this run"}
    gpath = rundir / "jobs" / "graph.json"
    graph = json.loads(gpath.read_text()) if gpath.exists() else []
    if not any(j.get("key") == "mmgbsa" for j in graph):
        job = next(j for j in r.jobs if j.key == "mmgbsa")
        entry = {"key": job.key, "script": job.script, "depends": [list(d) for d in job.depends], "array": job.array, "gpu": job.gpu, "description": job.description}
        at = next((i for i, j in enumerate(graph) if j.get("key") == "tidy"), len(graph))
        graph.insert(at, entry)
        for j in graph:
            if j.get("key") == "tidy" and not any(d[0] == "mmgbsa" for d in j.get("depends", [])):
                j["depends"] = list(j.get("depends", [])) + [["mmgbsa", "afterany"]]
        gpath.write_text(json.dumps(graph, indent=2))
    else:
        for j in graph:
            if j.get("key") == "mmgbsa":
                j["description"] = engine._mmgbsa_description(spec) if hasattr(engine, "_mmgbsa_description") else j.get("description", "")
            elif j.get("key") == "tidy":         # an older graph's tidy waited for MM-GBSA to succeed (Engine.tidy_depends)
                j["depends"] = [[d[0], "afterany" if d[0] == "mmgbsa" else d[1]] for d in j.get("depends", [])]
        gpath.write_text(json.dumps(graph, indent=2))
    try:
        out = q.resubmit(rundir, "mmgbsa", dry_run=S.dry_run)
    except (KeyError, OSError, ValueError) as e:
        return {"error": f"MM-GBSA could not be submitted: {e}"}
    out["written"] = written
    out["window"] = {"from_ns": an.get("mmgbsa_from_ns", 0), "to_ns": an.get("mmgbsa_to_ns", 0), "stride_ps": an.get("mmgbsa_stride_ps", 200)}
    return out


def api_tidy(body: dict) -> dict:
    """Pack a finished run's small files (a queue job on the short partition), or unpack them
    (undo: true); an older run without a tidy stage gets one."""
    from . import tidy as _tidy
    rundir = _rundir(body.get("name", ""))
    if rundir is None:
        return {"error": "no such run"}
    if not body.get("undo"):
        live = _tidy.active_jobs(rundir)
        if live:
            return {"error": f"jobs still queued or running ({', '.join(live)}); tidy runs by itself when the graph ends"}
    return _tidy.submit(rundir, undo_=bool(body.get("undo")), dry_run=S.dry_run)


def api_video(body: dict) -> dict:
    """Queue the video job for one replicate on a CPU node: cpptraj cuts the frames, VMD writes
    them as POV-Ray scenes, POV-Ray renders, FFmpeg assembles."""
    from .export.video import submit_video
    rundir = _rundir(body.get("name", ""))
    if rundir is None:
        return {"error": "no such run"}
    try:
        rep = max(1, int(body.get("rep", 1)))
    except (TypeError, ValueError):
        return {"error": "replicate must be a number"}
    from .export.video import trajectory_files
    if not trajectory_files(rundir, rep) and not S.dry_run:
        return {"error": f"no production trajectory in r{rep}/ yet: the videos are cut from the finished parts (prod_s*_p*.nc, .dcd or .xtc)"}
    try:
        return submit_video(rundir, rep, dry_run=S.dry_run)
    except (OSError, ValueError) as e:
        return {"error": f"video job failed: {e}"}


MEDIA_TYPES = {".mp4": "video/mp4", ".png": "image/png", ".svg": "image/svg+xml", ".html": "text/html", ".pdf": "application/pdf"}


def api_media(params: dict) -> tuple[str, bytes]:
    """A binary file from inside a run folder: the video, a saved image, the report."""
    rundir = _rundir(params.get("name", [""])[0])
    rel = params.get("path", [""])[0]
    if rundir is None or not rel or ".." in rel.split("/"):
        return "text/plain", b"no such file"
    p = (rundir / rel).resolve()
    if not str(p).startswith(str(rundir.resolve())) or not p.is_file() or p.suffix.lower() not in MEDIA_TYPES:
        return "text/plain", b"no such file"
    return MEDIA_TYPES[p.suffix.lower()], p.read_bytes()


# ----------------------------------------------------------------------------- the report
# What a run's report says is hopemd.export.reportdata's; how it looks is reportkit's, the module
# the lab's tools share. A report only reads the run folder: it writes nothing there but the views
# added from the viewer, which go in the run's own report/ folder.
def api_report(params: dict) -> dict:
    """The report of a run, as the report page reads it (GET /api/report)."""
    rundir = _rundir(params.get("name", [""])[0] or params.get("rundir", [""])[0])
    if rundir is None:
        return {"error": "no such run"}
    from .export import reportdata
    raw = {k[4:]: v[0] for k, v in params.items() if k.startswith("opt.") and v}
    return reportdata.document(rundir, raw)


def report_page(params: dict) -> str:
    """The report page of a run: the tool's own header, reportkit and one call for the document."""
    from .export import reportdata
    name = params.get("name", [""])[0] or params.get("rundir", [""])[0]
    if _rundir(name) is None:
        return ("<!doctype html><meta charset=utf-8><p>Open the report from the run's own page: it carries "
                "the run and the token.")
    return reportdata.page(S.token, name, params.get("print", [""])[0] == "1")


def api_report_add(body: dict) -> dict:
    """A view from the run's viewer, kept in the run's report folder."""
    rundir = _rundir((body or {}).get("name") or "")
    if rundir is None:
        return {"error": "no such run"}
    from .export import reportdata
    try:
        got = reportdata.add_item(rundir, body)
    except OSError as exc:
        return {"error": f"the view could not be kept: {exc}"}
    if got.get("error"):
        return got
    return {"views": reportdata.Report(rundir, (body or {}).get("opt") or {}).views()}


def api_report_edit(body: dict) -> dict:
    """A view's own words changed, the view moved, or taken out; the views as the report now
    shows them."""
    rundir = _rundir((body or {}).get("name") or "")
    if rundir is None:
        return {"error": "no such run"}
    from .export import reportdata
    try:
        got = reportdata.edit_item(rundir, body)
    except OSError as exc:
        return {"error": f"the change could not be kept: {exc}"}
    if got.get("error"):
        return got
    return {"views": reportdata.Report(rundir, (body or {}).get("opt") or {}).views()}


def api_report_image(params: dict) -> bytes | None:
    """A view's picture, as bytes, or None."""
    rundir = _rundir(params.get("name", [""])[0])
    if rundir is None:
        return None
    from .export import reportdata
    return reportdata.item_image(rundir, params.get("id", [""])[0])


def _run_file(params: dict) -> Path | str:
    """The file /api/file sends: one inside a run folder, named by its path there (name=, path=). The
    file, or the sentence that refuses it. /api/surface lets a file through by the same rules."""
    rundir = _rundir(params.get("name", [""])[0])
    rel = params.get("path", [""])[0]
    if rundir is None or not rel or ".." in rel.split("/"):
        return "no such file"
    p = (rundir / rel).resolve()
    if not str(p).startswith(str(rundir.resolve())) or not p.is_file():
        return "no such file"
    if p.stat().st_size > 64 * 1024 * 1024:
        return "file too large to send"
    return p


def api_file(params: dict) -> tuple[str, str]:
    """A text file from inside a run folder, for the viewer and the log panes."""
    p = _run_file(params)
    if isinstance(p, str):
        return "text/plain", p
    return "text/plain", p.read_text(errors="replace")


def _staging_dir(staging) -> Path:
    """An import's staging folder by its name. The name is one folder name, as Setup makes it; one
    with a slash or a leading dot could reach outside the runs folder, so it names no folder here."""
    s = str(staging or "")
    if not s or "/" in s or "\\" in s or s.startswith("."):
        return Path("/nonexistent")
    return S.runs / "_staging" / s


def _structure_dir(params: dict) -> Path | None:
    """The folder whose structure /api/structure sends: an import's staging folder (staging=) or a run
    (name=). None when there is no such folder. /api/surface finds its folder by the same rules."""
    staging = params.get("staging", [""])[0]
    name = params.get("name", [""])[0]
    if staging:
        d = _staging_dir(staging)
    else:
        d = _rundir(name) or Path("/nonexistent")
    return d if d.exists() else None


def api_structure(params: dict) -> tuple[str, str]:
    d = _structure_dir(params)
    if d is None:
        return "text/plain", ""
    return "text/plain", _complex_pdb(d)


# ----------------------------------------------------------------------------- surfaces
def msms_places() -> list[str]:
    """Where the ADCP 1.1 suite is looked for after $MSMS_EXE and $ADCPSUITE_HOME, which
    hopemd.surface.find_msms reads itself: it tries each place's bin/ for an msms program, then each
    place, its adcpsuite/ and its tools/docking/adcpsuite/ for the suite, and runs MSMS through the
    suite's MSMS library, mslib."""
    up = Path(__file__).resolve().parents
    places: list[str] = []
    for g in (up[3] if len(up) > 3 else None,
              Path(os.environ["HOPEMD_SHARED"]) if os.environ.get("HOPEMD_SHARED") else None):
        for p in ((str(g),) if g is not None else ()):
            if p not in places:
                places.append(p)
    return places


def _surface_cache(folder: Path) -> str | None:
    """Where the surfaces of a folder's structures are kept: its hidden .surfaces, for a run folder or
    an import's staging folder of this runs folder. None, for no cache, when the folder cannot be
    written or lies inside the home folder. The cache only saves time: never an error, and nothing
    lists it."""
    try:
        real, runs, home = folder.resolve(), S.runs.resolve(), Path.home().resolve()
        staging = (S.runs / "_staging").resolve()
    except (OSError, RuntimeError):
        return None
    if real.parent not in (runs, staging) or real == home or home in real.parents:
        return None
    cache = real / ".surfaces"
    if cache.is_dir() or os.access(real, os.W_OK | os.X_OK):
        return str(cache)
    return None


_SPAN = re.compile(r"(-?\d+)(?:-(-?\d+))?")


def _residue_runs(sel: str) -> list[tuple[str, int, int]]:
    """The residues the Runs viewer selects, as it names them for /api/surface: "1-85", or by chain
    "A:25-109,L:17-29". [(chain or "" for any, first, last)]; empty for every atom."""
    out = []
    for item in (s.strip() for s in (sel or "").split(",")):
        if not item:
            continue
        chain, _, span = item.rpartition(":")
        m = _SPAN.fullmatch(span.strip())
        if not m:
            raise ValueError(f"{item!r} is not a residue range")
        first = int(m.group(1))
        out.append((chain.strip(), first, int(m.group(2)) if m.group(2) else first))
    return out


def _frame_atoms(text: str, frame: int, runs: list[tuple[str, int, int]]) -> str | None:
    """The atom lines of one frame of a PDB file of frames, counted as 3Dmol's addModelsAsFrames
    counts them: each MODEL is a frame, and a file without MODEL records is one. Only the residues
    `runs` names are kept (all, when it is empty). None when the file has no such frame."""
    lines = text.splitlines()
    models = any(L.startswith("MODEL") for L in lines)
    k, out = (-1 if models else 0), []
    for L in lines:
        if L.startswith("MODEL"):
            k += 1
            if k > frame:
                break
            continue
        if L.startswith("ENDMDL") or (not models and L.rstrip() == "END"):
            if k == frame:
                break
            continue
        if k != frame or not L.startswith(("ATOM  ", "HETATM")):
            continue
        if runs:
            try:
                num = int(L[22:26])
            except ValueError:
                try:
                    num = int(L[22:26].strip(), 16)      # past 9,999 residues, as OpenMM writes them
                except ValueError:
                    continue
            chain = L[21:22].strip()
            if not any((not c or c == chain) and lo <= num <= hi for c, lo, hi in runs):
                continue
        out.append(L)
    return "\n".join(out) + "\n" if k >= frame else None


def api_surface(params: dict) -> dict:
    """A molecular surface for a viewer, computed by MSMS (hopemd.surface) from the text of the file
    that viewer drew, or the reason there is none, for the page to draw its own surface and say why.

    The Setup viewer draws what /api/structure sends, the receptor as model 0 and the ligand or
    partner as model 1: part=rec or part=lig of an import (staging=) or of a run (name=). The Runs
    viewer draws a file of a run through /api/file, one MODEL a frame: name= and path= name it,
    frame= is the frame shown (0, the first) and sel= the residues the surface covers (as
    _residue_runs reads them). Each is let through exactly as the endpoint that sends it would let
    it through. Surfaces are kept in the folder's .surfaces (_surface_cache)."""
    one = lambda k: params.get(k, [""])[0]                    # noqa: E731
    if one("path"):
        f = _run_file(params)
        if isinstance(f, str):
            return {"source": "fallback", "why": f}
        folder = _rundir(one("name"))
        try:
            frame = max(0, int(one("frame") or 0))
            runs = _residue_runs(one("sel"))
        except ValueError as e:
            return {"source": "fallback", "why": f"the request is not understood ({e})"}
        text = _frame_atoms(f.read_text(errors="replace"), frame, runs)
        if text is None:
            return {"source": "fallback", "why": f"{f.name} has no frame {frame + 1}"}
        name = f.name
    else:
        folder = _structure_dir(params)
        if folder is None:
            return {"source": "fallback", "why": "no such structure"}
        part = one("part") or "rec"
        if part not in ("rec", "lig"):
            return {"source": "fallback", "why": "part is rec or lig"}
        rec, _, lig = _complex_pdb(folder).partition(LIGAND_MARK)
        text, name = (rec, "receptor.pdb") if part == "rec" else (lig, "ligand.pdb")
    from . import surface
    cache = _surface_cache(folder)
    try:                     # the folder's time is the run's "last changed" in the Runs list: the cache leaves it alone
        made, st = cache is not None and not os.path.isdir(cache), folder.stat()
    except OSError:
        made, st = False, None
    got = surface.answer(text, surface.find_msms(msms_places()), cache, name=name)
    if made and st is not None and os.path.isdir(cache):
        try:
            os.utime(folder, ns=(st.st_atime_ns, st.st_mtime_ns))
        except OSError:
            pass
    return got


def api_browse(params: dict) -> dict:
    path = params.get("path", [""])[0]
    places = named_places()
    if not path:
        return {"places": places, "path": "", "dirs": [], "files": []}
    p = Path(os.path.expanduser(path))
    roots = [Path(v) for v in places.values()]
    if not any(str(p.resolve()).startswith(str(r.resolve())) for r in roots):
        return {"error": "outside the places this server may browse", "places": places}
    if not p.is_dir():
        return {"error": f"not a folder: {p}", "places": places}
    dirs, files = [], []
    try:
        for e in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            if e.name.startswith("."):
                continue
            if e.is_dir():
                dirs.append({"name": e.name, "path": str(e)})
            elif e.suffix.lower() in (".pdb", ".cif", ".mmcif", ".ent", ".sdf", ".mol2", ".mol", ".maegz", ".mae", ".gz", ".json", ".zip"):
                files.append({"name": e.name, "path": str(e), "size": e.stat().st_size})
    except PermissionError:
        return {"error": "permission denied", "places": places}
    parent = str(p.parent) if any(str(p.parent.resolve()).startswith(str(r.resolve())) for r in roots) else ""
    # `run` and `writable` are what the structure browser needs beyond the listing: whether this
    # folder is a run of ours, and whether New folder can be offered here.
    return {"places": places, "path": str(p), "parent": parent, "dirs": dirs, "files": files,
            "run": run_structure(p), "writable": os.access(p, os.W_OK)}


def api_mkdir(body: dict) -> dict:
    """A new folder under one of the places the server may browse (the Browse dialog's New
    folder): the parent must exist there; the name is one path segment."""
    # The structure browser calls the folder it is in "parent"; the key the page has always sent
    # is "path", and both are taken rather than one being renamed.
    parent = Path(os.path.expanduser(str(body.get("path") or body.get("parent") or "")))
    name = str(body.get("name", "")).strip()
    if not name or "/" in name or name in (".", "..") or name.startswith("."):
        return {"error": "a folder name is one plain segment, not a path"}
    roots = [Path(v) for v in named_places().values()]
    if not parent.is_dir() or not any(str(parent.resolve()).startswith(str(r.resolve())) for r in roots):
        return {"error": f"cannot make a folder in {parent}: not a folder this server may browse"}
    target = parent / name
    try:
        target.mkdir(exist_ok=False)
    except FileExistsError:
        return {"error": f"{target} exists already"}
    except OSError as e:
        return {"error": f"could not make {target}: {e.strerror or e}"}
    return {"path": str(target)}


def api_resubmit(body: dict) -> dict:
    """A stage and everything after it, again: a killed segment resumes from its checkpoint."""
    rundir = _rundir(body.get("name", ""))
    if rundir is None:
        return {"error": "no such run"}
    try:
        return q.resubmit(rundir, str(body.get("from", "")), dry_run=S.dry_run)
    except KeyError as e:
        return {"error": str(e).strip("'")}
    except (OSError, ValueError) as e:
        return {"error": f"resubmit failed: {e}"}


def api_runs_root(body: dict) -> dict:
    """Open another runs folder for this session (the Runs tab's Browse button): your own, or
    one shared with you. Read-only folders open too; submitting there is refused by the
    filesystem, not by us."""
    raw = str(body.get("path", "")).strip()
    if not raw:
        return {"error": "no folder given"}
    p = Path(os.path.expanduser(raw))
    if not p.is_dir():
        return {"error": f"not a folder: {p}"}
    if not os.access(p, os.R_OK | os.X_OK):
        return {"error": f"cannot read {p} (permissions)"}
    S.runs = p.resolve()
    return {"root": str(S.runs), "writable": os.access(S.runs, os.W_OK), "runs": q.list_runs(S.runs)}


def api_cancel(body: dict) -> dict:
    rundir = _rundir(body.get("name", ""))
    if rundir is None:
        return {"error": "no such run"}
    return {"log": q.cancel(rundir)}


# ----------------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = f"hopemd/{__version__}"

    def log_message(self, fmt, *args):   # quiet; the terminal is for the person
        pass

    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, "application/json", json.dumps(obj).encode())

    def _authed(self, params: dict) -> bool:
        return params.get("t", [""])[0] == S.token or self.headers.get("X-Token") == S.token

    def _docs(self, path: str) -> None:
        """The documentation: its pages and their stylesheet, as hopemd.docs says to send them."""
        from . import docs
        code, ctype, body, location = docs.respond(path)
        self.send_response(code)
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        params = parse_qs(u.query)
        if u.path == "/static/msms.js":            # the page's half of hopemd.surface: drawing what /api/surface answers
            from .surface import VIEWER_JS
            return self._send(200, "application/javascript", VIEWER_JS.encode())
        # reportkit, the lab's report module: the two files every tool carries, byte for byte the
        # same, served by name from the package so no path arrives from the browser
        if u.path in ("/static/reportkit.js", "/static/reportkit.css"):
            f = Path(__file__).resolve().parent / "static" / os.path.basename(u.path)
            if not f.is_file():
                return self._send(404, "text/plain", b"this checkout carries no reportkit")
            ctype = "application/javascript; charset=ascii" if f.suffix == ".js" else "text/css; charset=ascii"
            return self._send(200, ctype, f.read_bytes())
        if u.path.startswith("/static/"):          # the page's own files carry nothing private
            f = (WEB / u.path[len("/static/"):]).resolve()
            if not str(f).startswith(str(WEB.resolve())) or not f.is_file():
                return self._send(404, "text/plain", b"no")
            ctype = {"js": "application/javascript", "css": "text/css", "html": "text/html",
                     "svg": "image/svg+xml", "png": "image/png"}.get(f.suffix[1:], "text/plain")
            return self._send(200, ctype, f.read_bytes())
        # The documentation needs no token: it is the same for everyone, holds nothing of the session,
        # and a page of it kept as a bookmark has no token to carry
        if u.path == "/docs" or u.path.startswith("/docs/"):
            return self._docs(u.path)
        if u.path == "/report":
            # The report of a run: a page to read and to save as a PDF. The token travels with it,
            # as it does with the main page.
            if not self._authed(params):
                return self._send(403, "text/plain", b"open the report from the run's page; it carries the token")
            return self._send(200, "text/html", report_page(params).encode())
        if not self._authed(params):
            return self._send(403, "text/plain", b"missing or wrong token; use the URL the server printed")
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, "text/html", (WEB / "index.html").read_bytes())
            if u.path == "/api/site":
                return self._json(api_site(params))
            if u.path == "/api/runs":
                return self._json(api_runs())
            if u.path == "/api/run":
                return self._json(api_run(params))
            if u.path == "/api/browse":
                return self._json(api_browse(params))
            if u.path == "/api/places":
                return self._json(api_places())
            if u.path == "/api/poses":
                return self._json(api_poses(params))
            if u.path == "/api/stage_log":
                return self._json(api_stage_log(params))
            if u.path == "/api/tm_status":
                return self._json(api_tm_status(params))
            if u.path == "/api/prepare_status":
                return self._json(api_prepare_status(params))
            if u.path == "/api/file":
                ct, text = api_file(params); return self._send(200, ct, text.encode())
            if u.path == "/api/report":
                return self._json(api_report(params))
            if u.path == "/api/report/image":
                data = api_report_image(params)
                if data is None:
                    return self._json({"error": "no such view"}, 404)
                return self._send(200, "image/png", data)
            if u.path == "/api/media":
                ct, data = api_media(params); return self._send(200, ct, data)
            if u.path == "/api/structure":
                ct, text = api_structure(params); return self._send(200, ct, text.encode())
            if u.path == "/api/surface":
                return self._json(api_surface(params))
            return self._send(404, "text/plain", b"no such route")
        except Exception:
            return self._json({"error": traceback.format_exc().splitlines()[-1]}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        params = parse_qs(u.query)
        if not self._authed(params):
            return self._send(403, "text/plain", b"missing or wrong token")
        n = int(self.headers.get("Content-Length", "0"))
        if n > (24 << 20):     # a view's picture is the largest thing the page posts, and is far smaller
            return self._json({"error": "that is too large to send"}, 413)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json({"error": "body is not JSON"}, 400)
        try:
            if u.path == "/api/validate":
                return self._json(api_validate(body))
            if u.path == "/api/preset":
                return self._json(api_preset(body))
            if u.path == "/api/import":
                return self._json(api_import(body))
            if u.path == "/api/prepare":
                return self._json(api_prepare(body))
            if u.path == "/api/render":
                return self._json(api_render(body, submit=False))
            if u.path == "/api/submit_batch":
                return self._json(api_submit_batch(body))
            if u.path == "/api/submit":
                return self._json(api_render(body, submit=True))
            if u.path == "/api/tm_orient":
                return self._json(api_tm_orient(body))
            if u.path == "/api/mkdir":
                return self._json(api_mkdir(body))
            if u.path == "/api/upload":
                return self._json(api_upload(body))
            if u.path == "/api/runs_root":
                return self._json(api_runs_root(body))
            if u.path == "/api/cancel":
                return self._json(api_cancel(body))
            if u.path == "/api/resubmit":
                return self._json(api_resubmit(body))
            if u.path == "/api/video":
                return self._json(api_video(body))
            if u.path == "/api/dense":
                return self._json(api_dense(body))
            if u.path == "/api/waters":
                return self._json(api_waters(body))
            if u.path == "/api/tidy":
                return self._json(api_tidy(body))
            if u.path == "/api/report/add":
                return self._json(api_report_add(body))
            if u.path == "/api/report/edit":
                return self._json(api_report_edit(body))
            if u.path == "/api/mmgbsa":
                return self._json(api_mmgbsa(body))
            return self._send(404, "text/plain", b"no such route")
        except Exception:
            return self._json({"error": traceback.format_exc().splitlines()[-1], "trace": traceback.format_exc()}, 500)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HOPE-MD server")
    ap.add_argument("--port", type=int, default=8098)
    ap.add_argument("--host", default="127.0.0.1", help="bind address; keep it local and tunnel")
    ap.add_argument("--runs", default=None, help="run folder root (default $HOPEMD_RUNS or $SCRATCH/hopemd_runs)")
    ap.add_argument("--site", default=None, help="grace | aces (default: from the hostname)")
    ap.add_argument("--dry-run", action="store_true", help="render and write, never sbatch")
    ap.add_argument("--token", default=None, help="fixed token (default: random per start)")
    a = ap.parse_args(argv)
    S.runs = Path(a.runs).expanduser() if a.runs else q.runs_root()
    S.runs.mkdir(parents=True, exist_ok=True)
    # finished runs add their ns/day to benchmarks.tsv; a shared, read-only checkout keeps
    # its committed rows and the new ones go beside the runs (estimate.py reads both)
    from .estimate import repo_benchmarks_path
    # New benchmark and timing rows go beside the runs, never into the checkout: a shared
    # checkout that gathers rows cannot `git pull`. The repository's tables are read too;
    # a maintainer merges a runs root's rows into them.
    if not os.environ.get("HOPEMD_BENCHMARKS"):
        os.environ["HOPEMD_BENCHMARKS"] = str(S.runs / "benchmarks.tsv")
    S.dry_run = a.dry_run or not q.sbatch_available()
    S.token = a.token or secrets.token_urlsafe(12)
    site = SITES.get(a.site or "") or site_for_host(socket.gethostname()) or SITES["grace"]
    S.site_key = site.key
    httpd = ThreadingHTTPServer((a.host, a.port), Handler)
    port = httpd.server_address[1]              # --port 0 lets the OS pick a free one, which is what the launcher asks for
    print(f"HOPE-MD {__version__}{' (' + CODE['commit'] + ')' if CODE['commit'] else ''} on {site.name}  runs: {S.runs}  {'DRY RUN (no sbatch)' if S.dry_run else 'sbatch ready'}", flush=True)
    # the URL line is the handshake with the desktop launcher: it reads the port and the token off it
    print(f"open  http://127.0.0.1:{port}/?t={S.token}", flush=True)
    print(f"from your PC first:  ssh -N -L {port}:127.0.0.1:{port} {site.login_hosts[0]}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
