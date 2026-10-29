"""Complex sources: a file, the RCSB, or an AlphaFold result.

All three end the same way: `input/complex.pdb` (PDB text, one model, original chain ids),
`input/original.<ext>` untouched, and the detected fields the page shows before Prepare runs.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import urllib.request
import zipfile
from pathlib import Path

from ..spec import Prep
from ..structure import (read_structure, cif_to_pdb, inventory, default_split, b_is_plddt, is_mmcif, modified_residues,
                         mapped_note)

RCSB_FILES = "https://files.rcsb.org/download/{}"
RCSB_ENTRY = "https://data.rcsb.org/rest/v1/core/entry/{}"


def _get(url: str, timeout: float = 60) -> bytes:
    # urllib honors http_proxy/https_proxy, which `module load WebProxy` sets on compute nodes
    req = urllib.request.Request(url, headers={"User-Agent": "hopemd/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_rcsb(pdb_id: str, assembly: str, dest_dir: Path) -> dict:
    """Download the biological assembly (default 1) or the asymmetric unit as mmCIF."""
    pid = pdb_id.strip().upper()
    if not re.fullmatch(r"[0-9][A-Z0-9]{3}", pid):
        raise ValueError(f"{pdb_id!r} is not a PDB id")
    dest_dir.mkdir(parents=True, exist_ok=True)
    if assembly and assembly != "asu":
        name = f"{pid}-assembly{assembly}.cif"
    else:
        name = f"{pid}.cif"
    data = _get(RCSB_FILES.format(name))
    orig = dest_dir / f"original_{name}"
    orig.write_bytes(data)
    meta = {}
    try:
        e = json.loads(_get(RCSB_ENTRY.format(pid), timeout=30))
        meta = {"title": e.get("struct", {}).get("title", ""),
                "method": ", ".join(e.get("exptl", [{}])[0].get("method", "") for _ in [0]),
                "resolution_A": (e.get("rcsb_entry_info", {}).get("resolution_combined") or [None])[0],
                "released": e.get("rcsb_accession_info", {}).get("initial_release_date", "")[:10]}
    except Exception:
        pass
    return {"file": str(orig), "meta": meta, "assembly": assembly or "1"}


#: The line the AlphaFold Server writes at the top of every model it gives: its output terms. A model
#: that carries it is read like any other, and said to be the Server's, because those terms restrict
#: how its models may be used downstream.
SERVER_TERMS = re.compile(r"alphafoldserver\.com/output-terms", re.I)
SERVER_NOTE = ("this model came from the AlphaFold Server, whose output terms (alphafoldserver.com/output-terms) "
               "restrict how its models may be used downstream: check that this use is allowed, or use a model "
               "from AlphaFold 3 run yourself or from the AlphaFold DB")


def from_alphafold_server(cif_text: str, names: list[str] = ()) -> bool:
    """Whether a model is the AlphaFold Server's: its terms line, or the job request the Server puts beside it."""
    return bool(SERVER_TERMS.search(cif_text[:4000])) or any(n.endswith("_job_request.json") for n in names)


def _model_of(names: list[str], model_index: int, where: str) -> str:
    """The name of model model_index (model_<index>) among a result's *_model_N.cif names."""
    want = next((n for n in names if n.endswith(f"_model_{model_index}.cif")), None)
    if want is None:
        have = sorted(int(re.search(r"_model_(\d+)\.cif$", n).group(1)) for n in names)
        raise FileNotFoundError(f"{where} has no model {model_index + 1} (model_{model_index}): it holds "
                                f"model_{have[0]} to model_{have[-1]}, models {have[0] + 1} to {have[-1] + 1} in the model field")
    return want


def _af3_models(names: list[str], read) -> list[tuple[str, str, str]]:
    """AlphaFold 3's own output, as run_alphafold.py writes it: <job>_model.cif, the top-ranked, at the top,
    and a folder per sample, seed-<s>_sample-<n>/, each with its model and its summary_confidences.json.
    Returns [(model, its summary or "", what to call it)], the top-ranked first and then the samples by
    ranking score, so model 1 on the page is the one AlphaFold 3 ranked first."""
    top = [n for n in names if re.search(r"(?:^|/)[^/]+_model\.cif$", n) and not re.search(r"seed-\d+_sample-\d+/", n)]
    samples = {}
    for n in names:
        m = re.search(r"(?:^|/)seed-(\d+)_sample-(\d+)/(?:[^/]*_)?model\.cif$", n)
        if m:
            folder = n[:n.rindex("/") + 1]
            summary = next((x for x in names if x.startswith(folder) and x.endswith("summary_confidences.json")), "")
            samples[(int(m.group(1)), int(m.group(2)))] = (n, summary)
    score = {}
    table = next((n for n in names if n.endswith("ranking_scores.csv")), "")
    if table:
        for line in read(table).splitlines()[1:]:
            bits = line.split(",")
            if len(bits) >= 3:
                try:
                    score[(int(bits[0]), int(bits[1]))] = float(bits[2])
                except ValueError:
                    pass
    for key, (_m, summary) in samples.items():
        if key not in score and summary:
            try:
                score[key] = float(json.loads(read(summary)).get("ranking_score"))
            except (ValueError, TypeError):
                pass
    ranked = sorted(samples, key=lambda k: (-score.get(k, float("-inf")), k))
    out = []
    if top:
        summary = next((x for x in names if re.search(r"(?:^|/)[^/]+_summary_confidences\.json$", x)
                        and not re.search(r"seed-\d+_sample-\d+/", x)), "")
        out.append((top[0], summary, "the top-ranked model"))
        ranked = ranked[1:] if ranked else ranked           # the top-ranked is the first sample again
    out += [(samples[k][0], samples[k][1], f"seed-{k[0]}_sample-{k[1]}") for k in ranked]
    return out


def read_alphafold(path: str | Path, model_index: int = 0) -> dict:
    """An AlphaFold result: the folder AlphaFold 3 writes, or a zip of it, or one model file (an AlphaFold
    DB, AlphaFold 2, ColabFold or AlphaFold 3 model). Returns the chosen model as PDB text, the confidence
    summary written beside it (a value left empty, such as the ipTM of a single chain, left out), what
    the model is called, the file read, and whether it is the AlphaFold Server's (from_alphafold_server).
    A Server download (fold_<job>_model_<n>.cif beside fold_<job>_summary_confidences_<n>.json) is read
    too; the importer says whose it is."""
    p = Path(path).expanduser()
    names, read, where = [], None, p.name
    if p.is_file() and p.suffix.lower() == ".zip":
        z = zipfile.ZipFile(p)
        names = [n for n in z.namelist() if not n.endswith("/")]
        read = lambda n: z.read(n).decode("utf-8", "replace")                          # noqa: E731
    elif p.is_dir():
        names = sorted(str(x.relative_to(p)) for x in p.rglob("*") if x.is_file())
        read = lambda n: (p / n).read_text(errors="replace")                           # noqa: E731
    summary, label = {}, ""
    if read is None:
        cif_text = p.read_text(errors="replace")
        m = re.search(r"_model_(\d+)\.cif$", p.name)
        if m:                       # one model file is the model its name says, whichever was asked for
            model_index = int(m.group(1))
            sj = p.with_name(p.name.replace(f"_model_{m.group(1)}.cif", f"_summary_confidences_{m.group(1)}.json"))
            if sj.exists():
                summary = json.loads(sj.read_text())
            label = f"model_{model_index}"
        else:
            sj = next((x for x in (p.with_name(p.name.replace("_model.cif", "_summary_confidences.json")),
                                   p.with_name("summary_confidences.json")) if x != p and x.exists()), None)
            if p.name.endswith("model.cif") and sj:
                summary = json.loads(sj.read_text())
        name, siblings = p.name, [x.name for x in p.parent.glob("*_job_request.json")] if p.parent.is_dir() else []
    else:
        numbered = sorted(n for n in names if re.search(r"_model_\d+\.cif$", n))
        if numbered:
            want = _model_of(numbered, model_index, where)
            sj = want.replace(f"_model_{model_index}.cif", f"_summary_confidences_{model_index}.json")
            label = f"model_{model_index}"
        else:
            models = _af3_models(names, read)
            if not models:
                raise FileNotFoundError(f"no AlphaFold model in {where}: it holds neither AlphaFold 3's "
                                        "<job>_model.cif and seed-<s>_sample-<n>/ folders nor *_model_<n>.cif files")
            if model_index >= len(models):
                raise FileNotFoundError(f"{where} has {len(models)} model(s), so there is no model {model_index + 1}")
            want, sj, label = models[model_index]
        cif_text = read(want)
        if sj and sj in names:
            summary = json.loads(read(sj))
        name, siblings = Path(want).name, names
    pdb_text = cif_to_pdb(cif_text) if is_mmcif(cif_text) else cif_text
    conf = {}
    for k in ("ptm", "iptm", "ranking_score", "fraction_disordered", "has_clash"):
        if summary.get(k) is not None:
            conf[k] = summary[k]
    if "chain_pair_iptm" in summary:
        conf["chain_pair_iptm"] = summary["chain_pair_iptm"]
    if "chain_ptm" in summary:
        conf["chain_ptm"] = summary["chain_ptm"]
    return {"pdb": pdb_text, "confidence": conf, "model_index": model_index, "model": label, "file": name,
            "server": from_alphafold_server(cif_text, siblings)}


def complex_shape(pdb_text: str, trim_plddt: float = 0.0) -> dict:
    """The extent, the atom count and tleap's shape (hopemd.solvation) of a complex before Prepare has split it: what
    Prepare keeps of it by default, its chains (ATOM records) without a predicted model's termini under the pLDDT
    threshold."""
    from ..prepare import trim_low_plddt
    from ..solvation import pdb_atoms, solute_shape
    if trim_plddt > 0:
        pdb_text = trim_low_plddt(pdb_text, trim_plddt)[0]
    atoms = pdb_atoms(pdb_text, records=("ATOM",))
    heavy = sum(1 for a in atoms if a[0] != "H")
    extent = [round(max(a[3 + k] for a in atoms) - min(a[3 + k] for a in atoms), 1) if atoms else 0.0 for k in range(3)]
    shape = solute_shape(atoms)
    return {"solute_atoms": shape.get("atoms") or heavy * 2, "extent_A": extent, "solute_shape": shape}


def import_complex(kind: str, source: dict, rundir: str | Path) -> dict:
    """Stage a complex from one of the three sources. Returns the detected System fields."""
    rundir = Path(rundir)
    inp = rundir / "input"; inp.mkdir(parents=True, exist_ok=True)
    meta: dict = {}
    original = ""                  # the file as it came, when it can say where its coordinates are from
    if kind == "complex":
        src = Path(os.path.expanduser(source.get("path", "")))
        if not src.is_file():
            raise FileNotFoundError(f"file not found: {src}")
        shutil.copyfile(src, inp / ("original" + src.suffix.lower()))
        pdb_text = read_structure(str(src), model=1)
        original = src.read_text(errors="replace")
    elif kind == "rcsb":
        got = fetch_rcsb(source.get("pdb_id", ""), source.get("assembly", "1"), inp)
        meta = got["meta"]
        pdb_text = read_structure(got["file"], model=1)
    elif kind == "alphafold":
        got = read_alphafold(source.get("path", ""), max(0, int(source.get("pose", 1)) - 1))
        src = Path(os.path.expanduser(source.get("path", "")))
        if src.is_file():
            shutil.copyfile(src, inp / ("original" + src.suffix.lower()))
        pdb_text = got["pdb"]
        meta = {"confidence": got["confidence"], **({"model": got["model"]} if got["model"] else {}), "model_file": got["file"],
                **({"server": True} if got["server"] else {})}
    else:
        raise ValueError(kind)
    (inp / "complex.pdb").write_text(pdb_text)
    # B-factors are pLDDT only in a predicted model: the source says so, or the file does, or its signature
    inv = inventory(pdb_text, plddt=b_is_plddt(pdb_text, kind, original, Path(str(source.get("path") or "")).name))
    if not inv.polymer_chains():
        what = {"rcsb": f"PDB entry {str(source.get('pdb_id', '')).upper()}", "alphafold": meta.get("model_file", "")}.get(kind) \
            or Path(str(source.get("path") or "")).name or "the file"
        raise ValueError(f"no protein or nucleic-acid chain was read from {what}: there is nothing in it to simulate. "
                         "Check that it is a structure (PDB or mmCIF) with its coordinates.")
    # protein with DNA or RNA: the protein chains against every nucleic-acid strand; a nucleic acid alone: every
    # strand and no partner; otherwise the largest chain against the rest. The page shows the split to change.
    receptor, partner, ligand_class = default_split(inv.chains)
    notes = []
    if kind == "alphafold" and meta.get("model"):
        notes.append(f"{meta['model_file']}: {meta['model']}" + (", the top-ranked" if meta["model"] == "model_0" else ""))
    if kind == "alphafold" and meta.get("server"):
        notes.append(SERVER_NOTE)
    if ligand_class == "nucleic_acid":
        notes.append(f"protein chain{'s' if len(receptor) > 1 else ''} {', '.join(receptor)} as the receptor, the nucleic-acid "
                     f"chain{'s' if len(partner) > 1 else ''} {', '.join(partner)} together as the partner")
    elif receptor and any(c["kind"] == "nucleic" for c in inv.chains):
        notes.append(f"a nucleic acid alone: chain{'s' if len(receptor) > 1 else ''} {', '.join(receptor)} simulated together, with no partner")
    for c in inv.chains:
        if c.get("modified"):
            notes.append(f"chain {c['id']} has modified nucleotides ({', '.join(c['modified'][:8])}{' …' if len(c['modified']) > 8 else ''}), "
                         "which the nucleic-acid force fields here have no parameters for: a run with this chain is refused")
        if c.get("charged_modified"):
            notes.append(f"chain {c['id']} has modified amino acids whose charge is not their parent's ({', '.join(c['charged_modified'][:8])}"
                         f"{' …' if len(c['charged_modified']) > 8 else ''}), which the force fields here have no parameters for: a run "
                         "with this chain is refused")
    mapped = [m for m in modified_residues(pdb_text) if m["mapped"]]
    if mapped:
        notes.append(f"modified residues Prepare maps to their parent, keeping the parent's atoms where they are: {mapped_note(mapped)}")
    return {
        "ligand_class": ligand_class,
        "receptor_chains": receptor, "partner_chains": partner,
        "chains_found": inv.chains, "hetero_found": inv.hetero,
        "gaps_found": [{"chain": c["id"], "gaps": c["gaps"], "missing": c["missing"]} for c in inv.chains if c["gaps"]],
        "confidence": {**meta.get("confidence", {}), **({"plddt": inv.plddt} if inv.plddt else {})},
        **complex_shape(pdb_text, trim_plddt=Prep().trim_plddt if inv.plddt else 0.0), "disulfides": [], "split_disulfides": [],
        "waters": inv.waters, "altlocs": inv.altlocs, "models": inv.models, "meta": {k: v for k, v in meta.items() if k != "confidence"},
        "prepared": False, "prep_report": {}, **({"note": " · ".join(notes)} if notes else {}),
    }
