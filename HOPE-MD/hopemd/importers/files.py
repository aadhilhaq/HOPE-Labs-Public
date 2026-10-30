"""Plain files: a receptor PDB and a ligand PDB / SDF / MOL2, or one complex PDB.

Detection, not asking: chains, residues, waters, cofactors, disulfides (SG-SG under
2.3 Å), those across the split of receptor and partner among them, the solute's extent for the
box estimate, and the ligand class.
"""
from __future__ import annotations

import math
import shutil
from dataclasses import dataclass, field
from pathlib import Path

AMINO = {"ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE", "LEU", "LYS", "MET",
         "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL", "HID", "HIE", "HIP", "CYX", "ASH", "GLH",
         "LYN", "ACE", "NME", "NHE", "MSE", "SEC", "PYL"}
NUCLEIC = {"DA", "DT", "DG", "DC", "DU", "A", "U", "G", "C", "DA5", "DA3", "DT5", "DT3", "DG5", "DG3",
           "DC5", "DC3", "A5", "A3", "U5", "U3", "G5", "G3", "C5", "C3", "RA", "RU", "RG", "RC"}
WATER = {"HOH", "WAT", "TIP", "TIP3", "SOL", "DOD"}
IONS = {"NA", "CL", "K", "MG", "CA", "ZN", "MN", "FE", "CU", "NI", "CO", "CD", "SO4", "PO4", "BR", "IOD"}


@dataclass
class ChainInfo:
    id: str
    kind: str              # protein | nucleic | ligand | water | ion | other
    residues: int
    first: int
    last: int
    resnames: list[str] = field(default_factory=list)


@dataclass
class PDBInfo:
    path: str
    atoms: int
    heavy_atoms: int
    chains: list[ChainInfo]
    waters: int
    ions: int
    hetero: list[str]                  # non-water, non-ion HETATM residue names
    extent: list[float]                # bounding box of protein/nucleic/ligand atoms
    disulfides: list[list[str]]        # ["A:25","A:80"], an insertion code with its number ("H:60A")
    altlocs: int
    # "A:41" for every polymer residue in file order, its insertion code with its number ("H:60A"): one per residue as
    # tleap counts them. Keyed by the number and the name alone, thrombin's 60B and 60C, two prolines, were one residue,
    # and every range the masks are built from was one short after them
    residue_keys: list[str]
    bounds: list[list[float]] = field(default_factory=list)   # [[x, y, z] lowest, [x, y, z] highest] of the extent's atoms

    def polymer_residues(self) -> int:
        return sum(c.residues for c in self.chains if c.kind in ("protein", "nucleic", "ligand"))


def _classify(resname: str) -> str:
    r = resname.strip().upper()
    if r in WATER:
        return "water"
    if r in IONS:
        return "ion"
    if r in AMINO:
        return "protein"
    if r in NUCLEIC:
        return "nucleic"
    return "other"


def inspect_pdb(path: str | Path) -> PDBInfo:
    path = Path(path)
    atoms = heavy = waters = ions = altlocs = 0
    chains: dict[str, dict] = {}
    hetero: set[str] = set()
    coords = []
    sg = []           # (key, x, y, z)
    keys: list[str] = []
    seen_res: set[tuple[str, str, str]] = set()
    with path.open() as fh:
        for line in fh:
            rec = line[:6].strip()
            if rec not in ("ATOM", "HETATM"):
                continue
            atoms += 1
            name = line[12:16].strip()
            alt = line[16].strip()
            resn = line[17:20].strip()
            ch = line[21].strip() or "A"
            try:
                resi = int(line[22:26])
            except ValueError:
                continue
            rid = f"{resi}{line[26:27].strip()}"      # the number and the insertion code: 60, 60A ... are residues apart
            x, y, z = float(line[30:38]), float(line[38:46]), float(line[46:54])
            elem = (line[76:78].strip() or name[0]).upper()
            if alt and alt != "A":
                altlocs += 1
            kind = _classify(resn)
            if kind == "water":
                waters += (name.strip().upper() in ("O", "OW", "OH2")) or 0
                continue
            if kind == "ion":
                ions += 1
                continue
            if elem != "H":
                heavy += 1
            coords.append((x, y, z))
            if kind == "other":
                hetero.add(resn)
            c = chains.setdefault(ch, {"kinds": {}, "first": resi, "last": resi, "res": set(), "names": []})
            c["kinds"][kind] = c["kinds"].get(kind, 0) + 1
            c["first"] = min(c["first"], resi); c["last"] = max(c["last"], resi)
            rk = (ch, rid, resn)
            if rk not in seen_res:
                seen_res.add(rk); c["res"].add(rid); c["names"].append(resn)
                keys.append(f"{ch}:{rid}")
            if resn in ("CYS", "CYX") and name == "SG":
                sg.append((f"{ch}:{rid}", x, y, z))
    out_chains = []
    for cid, c in chains.items():
        kind = max(c["kinds"], key=c["kinds"].get)
        if kind == "other":
            kind = "ligand"
        out_chains.append(ChainInfo(cid, kind, len(c["res"]), c["first"], c["last"], c["names"]))
    ext = [0.0, 0.0, 0.0]
    bounds: list[list[float]] = []
    if coords:
        bounds = [[min(p[i] for p in coords) for i in range(3)], [max(p[i] for p in coords) for i in range(3)]]
        ext = [round(bounds[1][i] - bounds[0][i], 1) for i in range(3)]
    from ..structure import SS_BOND_A
    ss = []
    for i in range(len(sg)):
        for j in range(i + 1, len(sg)):
            d = math.dist(sg[i][1:], sg[j][1:])
            if d < SS_BOND_A:
                ss.append([sg[i][0], sg[j][0]])
    return PDBInfo(str(path), atoms, heavy, out_chains, waters, ions, sorted(hetero), ext, ss, altlocs, keys, bounds)


#: Ions, metals, cofactors, other ligands and waters are taken out of a receptor file at import, where the
#: residue ranges are counted: the build's protonation dropped them anyway, after the ranges were
#: fixed, and every mask past them then pointed one residue too far for each one dropped. The waters
#: and ions the run keeps go to their own files first (input/waters.pdb, input/ions.pdb), which every
#: build adds after the solute.


def strip_to_polymer(path: str | Path, drop: tuple[str, ...] = ("ion", "other", "water")) -> dict[str, int]:
    """Rewrite a receptor or partner PDB without the residue classes in `drop`, and with each
    modified amino acid as its parent, as Prepare maps them (structure.map_modified: selenomethionine
    as methionine, a cacodylated cysteine as cysteine ...; the build's protonation knows none of them
    and would drop the residue). A receptor loses ions, waters and every other hetero group, which the build's protonation
    dropped anyway, after the residue ranges were counted; a partner loses ions and waters only, so a residue
    tleap has no template for still stops the build with its name rather than leave a silent gap.
    CONECT records go too: they would name atoms that are gone, and the build writes its own.
    Returns what was taken out, as residue name -> number of residues."""
    from ..structure import map_modified
    path = Path(path)
    removed: dict[str, int] = {}
    seen: set = set()
    out = []
    for line in map_modified(path.read_text(errors="replace"))[0].splitlines():
        if line.startswith("CONECT"):
            continue
        if line.startswith(("ATOM", "HETATM")):
            resn = line[17:20].strip().upper()
            if _classify(resn) in drop:
                key = (line[21:22], line[22:27], resn)
                if key not in seen:
                    seen.add(key)
                    removed[resn] = removed.get(resn, 0) + 1
                continue
        out.append(line)
    path.write_text("\n".join(out) + "\n")
    return removed


def mapped_in(path: str | Path, what: str) -> str:
    """The note on the modified amino acids of a file strip_to_polymer maps to their parent."""
    from ..structure import modified_residues, mapped_note
    mapped = [m for m in modified_residues(Path(path).read_text(errors="replace")) if m["mapped"]]
    return (f"modified residues of the {what} file mapped to their parent, the parent's atoms kept where they were: "
            f"{mapped_note(mapped)}") if mapped else ""


def removed_note(removed: dict[str, int], what: str) -> str:
    removed = {r: n for r, n in removed.items() if _classify(r) != "water"}     # the waters have a note of their own
    if not removed:
        return ""
    groups = ", ".join(f"{n} {r}" for r, n in sorted(removed.items()))
    return (f"taken out of the {what} file: {groups}. Ions other than those a nucleic acid binds, metals unless kept, cofactors "
            "and other ligands are not simulated from a receptor or partner file; a small molecule to simulate comes in as "
            "its own SDF or MOL2 file")


def left_out(removed: dict[str, int], kept: list[dict], what: str) -> dict[str, int]:
    """What strip_to_polymer took out of a file, less the ions kept from it (they went to input/ions.pdb)."""
    out = dict(removed)
    for g in kept:
        if g["file"] == what:
            name = g["resname"].upper()
            out[name] = out.get(name, 0) - 1
    return {r: n for r, n in out.items() if n > 0}


def keep_chains(path: str | Path, chains: list[str] | None) -> str:
    """Rewrite a receptor PDB with only the polymer chains named (all when none is), in place; its hetero
    groups and waters stay for their own rules, as Clean keeps them. A chain named that the file has not stops
    the import with the file's chains. Returns the note on the chains left out."""
    want = [str(c).strip() for c in chains or [] if str(c).strip()]
    if not want:
        return ""
    path = Path(path)
    lines = path.read_text(errors="replace").splitlines()
    polymer = lambda l: l.startswith(("ATOM", "HETATM")) and _classify(l[17:20]) in ("protein", "nucleic")   # noqa: E731
    found = list(dict.fromkeys((l[21].strip() or "A") for l in lines if polymer(l)))
    missing = [c for c in want if c not in found]
    if missing:
        raise ValueError(f"chain{'s' if len(missing) > 1 else ''} {', '.join(missing)} {'are' if len(missing) > 1 else 'is'} not in "
                         f"the receptor file, which has chain{'s' if len(found) != 1 else ''} {', '.join(found) or 'none'}: "
                         "name its chains in the chains field, or empty it to keep them all")
    left = [c for c in found if c not in want]
    if not left:
        return ""
    path.write_text("\n".join(l for l in lines if not (polymer(l) and (l[21].strip() or "A") in left)) + "\n")
    return (f"chain{'s' if len(left) > 1 else ''} {', '.join(left)} of the receptor file left out, as the chains field keeps "
            f"{', '.join(want)}")


#: elements the small-molecule force fields here (GAFF2 with AM1-BCC; OPLS for an organic ligand) have no
#: parameters for: a ligand that carries one is refused at import, by name, rather than typed wrongly
METALS = {"Li", "Be", "Na", "Mg", "Al", "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Ga",
          "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn", "Cs", "Ba", "La", "Ce",
          "Pr", "Nd", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir",
          "Pt", "Au", "Hg", "Tl", "Pb", "Bi", "Th", "U"}


def ligand_elements(path: str | Path) -> list[str]:
    """The element symbols of a ligand file's first record (SDF/MOL), its first molecule (MOL2) or
    its atoms (PDB), capitalized as symbols."""
    p = Path(path)
    suf = p.suffix.lower()
    lines = p.read_text(errors="replace").splitlines()
    out = []
    if suf in (".sdf", ".mol", ".sd"):
        try:
            n = int(lines[3][:3])
        except (IndexError, ValueError):
            return out
        for line in lines[4:4 + n]:
            parts = line.split()
            if len(parts) >= 4:
                out.append(parts[3].capitalize())
    elif suf == ".mol2":
        block = False
        for line in lines:
            if line.startswith("@<TRIPOS>ATOM"):
                block = True; continue
            if line.startswith("@<TRIPOS>"):
                if block:
                    break
                continue
            if block and len(line.split()) >= 6:
                out.append(line.split()[5].split(".")[0].capitalize())
    else:
        for line in lines:
            if line.startswith(("ATOM", "HETATM")):
                el = line[76:78].strip() or line[12:16].strip().lstrip("0123456789")[:1]
                out.append(el.capitalize())
    return out


def normalise_small_molecule(src: str | Path, dst_dir: str | Path) -> tuple[Path, dict]:
    """The ligand as one SDF record with all its hydrogens, which antechamber and Desmond's
    structconvert both read: the first molecule of an SDF, MOL, SD or MOL2 file, read by RDKit with
    its bond orders and formal charges, hydrogens the file lacks added as drawn. Returns the file
    written and {"charge", "added_h", "records", "rdkit"}. Without RDKit the file is copied as it
    is and its charge read from its first record; a PDB ligand is copied as it is."""
    src, dst_dir = Path(src), Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    suf = src.suffix.lower()
    info = {"charge": None, "added_h": 0, "records": 1, "rdkit": False}
    metals = sorted(set(ligand_elements(src)) & METALS)
    if metals:
        raise ValueError(f"the ligand {src.name} contains {', '.join(metals)}: the ligand force fields here (GAFF2 with "
                         "AM1-BCC, OPLS for an organic ligand) have no parameters for a metal complex, so HOPE-MD does not "
                         "simulate it yet.")
    if suf == ".pdb":
        dst = dst_dir / "ligand.pdb"
        if src.resolve() != dst.resolve():
            shutil.copyfile(src, dst)
        return dst, info
    try:
        from rdkit import Chem
    except ImportError:
        dst = dst_dir / ("ligand.mol2" if suf == ".mol2" else "ligand.sdf")
        if src.resolve() != dst.resolve():
            shutil.copyfile(src, dst)
        info["charge"] = sdf_net_charge(dst)
        return dst, info
    text = src.read_text(errors="replace")
    if suf == ".mol2":
        info["records"] = max(1, text.count("@<TRIPOS>MOLECULE"))
        mol = Chem.MolFromMol2File(str(src), removeHs=False)
    else:
        sup = Chem.SDMolSupplier(str(src), removeHs=False)
        info["records"] = len(sup)
        mol = sup[0] if len(sup) else None
    if mol is None:
        raise ValueError(f"the first molecule of {src.name} could not be read: check its bond orders and "
                         "charges, or give it as SDF")
    missing = sum(a.GetNumImplicitHs() for a in mol.GetAtoms())
    if missing:
        mol = Chem.AddHs(mol, addCoords=True)
    info.update(charge=Chem.GetFormalCharge(mol), added_h=missing, rdkit=True)
    dst = dst_dir / "ligand.sdf"
    w = Chem.SDWriter(str(dst))
    w.write(mol)
    w.close()
    return dst, info


def count_hydrogens(path: str | Path) -> int:
    """Hydrogen atoms in a ligand file (SDF/MOL first record, MOL2, PDB)."""
    p = Path(path)
    suf = p.suffix.lower()
    lines = p.read_text(errors="replace").splitlines()
    if suf in (".sdf", ".mol", ".sd"):
        try:
            n = int(lines[3][:3])
        except (IndexError, ValueError):
            return 0
        return sum(1 for line in lines[4:4 + n] if len(line.split()) >= 4 and line.split()[3].upper() == "H")
    if suf == ".mol2":
        block, h = False, 0
        for line in lines:
            if line.startswith("@<TRIPOS>ATOM"):
                block = True; continue
            if line.startswith("@<TRIPOS>"):
                if block:
                    break
                continue
            if block and len(line.split()) >= 6 and line.split()[5].upper().startswith("H"):
                h += 1
        return h
    return sum(1 for line in lines if line.startswith(("ATOM", "HETATM"))
               and (line[76:78].strip().upper() or line[12:16].strip().lstrip("0123456789")[:1].upper()) == "H")


#: a partner file of amino-acid chains with this many residues or more is a protein, with fewer a peptide: a
#: protein partner is analyzed as an interface (interface Cα RMSD, buried surface), as a chain group split from a
#: complex is; a peptide (a docked one is a few to about twenty residues) as a docked ligand
PROTEIN_MIN_RESIDUES = 50


def detect_ligand_class(ligand_path: str | Path | None) -> str:
    """peptide | protein | small_molecule | nucleic_acid | none, from the ligand file alone."""
    if not ligand_path:
        return "none"
    p = Path(ligand_path)
    if p.suffix.lower() in (".sdf", ".mol2", ".mol", ".sd"):
        return "small_molecule"
    info = inspect_pdb(p)
    kinds = {c.kind for c in info.chains}
    if "nucleic" in kinds:
        return "nucleic_acid"
    if "protein" in kinds and "ligand" not in kinds:
        residues = sum(c.residues for c in info.chains if c.kind == "protein")
        return "protein" if residues >= PROTEIN_MIN_RESIDUES else "peptide"
    return "small_molecule"


def sdf_net_charge(path: str | Path) -> int | None:
    """Net charge from an SDF's M  CHG lines (V2000) or a MOL2's charge column."""
    p = Path(path)
    try:
        text = p.read_text(errors="ignore")
    except OSError:
        return None
    if p.suffix.lower() == ".mol2":
        q = 0.0; inatoms = False; found = False
        for line in text.splitlines():
            if line.startswith("@<TRIPOS>ATOM"):
                inatoms = True; continue
            if line.startswith("@<TRIPOS>MOLECULE") and found:
                break                                       # the first molecule only
            if line.startswith("@<TRIPOS>"):
                inatoms = False
            if inatoms and line.split():
                parts = line.split()
                if len(parts) >= 9:
                    try:
                        q += float(parts[8]); found = True
                    except ValueError:
                        pass
        return int(round(q)) if found else None
    q = 0
    for line in text.split("$$$$")[0].splitlines():       # the first record only: a multi-pose SDF is not one molecule
        if line.startswith("M  CHG"):
            parts = line.split()
            for i in range(4, len(parts), 2):
                q += int(parts[i])
    return q


def refuse_modified_nucleotides(path: str | Path, what: str) -> None:
    """Stop the import, naming them, when a strand of the file has modified nucleotides: nothing here has
    parameters for them, and taking them out would leave the strand cut where they were."""
    from ..structure import modified_nucleotides
    found = modified_nucleotides(Path(path).read_text(errors="replace"))
    if found:
        names = ", ".join(f"{m['resname']} {m['chain']}:{m['resi']}" for m in found[:8]) + (" …" if len(found) > 8 else "")
        raise ValueError(f"the {what} file has modified nucleotides ({names}): the nucleic-acid force fields here (OL3 for RNA; "
                         "OL21, bsc1 and OL15 for DNA) have no parameters for them, so HOPE-MD does not simulate them. Give the "
                         "structure without the modifications.")


def refuse_charged_modified(path: str | Path, what: str) -> None:
    """Stop the import, naming them, when the file has modified amino acids whose charge is not their parent's
    (structure.CHARGED_MODIFIED): nothing here has parameters for them, and as the parent the run would be another
    molecule with another charge."""
    from ..structure import modified_residues
    found = [m for m in modified_residues(Path(path).read_text(errors="replace")) if not m["mapped"]]
    if found:
        names = ", ".join(f"{m['resname']} {m['chain']}:{m['resi']}" for m in found[:8]) + (" …" if len(found) > 8 else "")
        raise ValueError(f"the {what} file has modified amino acids whose charge is not their parent's ({names}): a phosphate, "
                         "sulfate, carboxylate or acetyl the force fields here have no parameters for, and as the parent the run "
                         "would be another molecule with another charge, so HOPE-MD does not simulate them. Give the structure "
                         "without the modifications.")


def mark_breaks(path: str | Path) -> list[str]:
    """End the chain with TER, in place, wherever residues are missing between consecutive residues of a PDB (a
    protein's C..N, a nucleic acid's O3'..P longer than a bond across a missing number, or too long to close:
    structure.ter_at_breaks). Returns the breaks."""
    from ..structure import ter_at_breaks
    path = Path(path)
    text, breaks = ter_at_breaks(path.read_text(errors="replace"))
    if breaks:
        path.write_text(text)
    return breaks


def breaks_note(breaks: list[str], what: str) -> str:
    if not breaks:
        return ""
    return (f"{len(breaks)} chain break(s) in the {what} ({', '.join(breaks)}): each is ended with TER, so the pieces get "
            "their own termini rather than a bond across the gap")


def import_files(receptor: str | Path, ligand: str | Path | None, rundir: str | Path, chains: list[str] | None = None,
                 waters: str = "none", keep_metals: bool = False) -> dict:
    """Copy inputs into <rundir>/input/ and return the detected System fields. chains names the receptor file's
    polymer chains the run keeps (none named: all); waters is the crystal-water choice (none, near_ligand, all);
    keep_metals keeps the metal ions the builds have parameters for, and the cations a nucleic acid binds stay
    whatever is asked, as Clean keeps them. The waters and ions kept go to input/waters.pdb and input/ions.pdb."""
    from ..structure import stretched_bonds, stretched_note
    rundir = Path(rundir)
    (rundir / "input").mkdir(parents=True, exist_ok=True)
    rec_dst = rundir / "input" / "receptor.pdb"
    if Path(receptor).resolve() != rec_dst.resolve():
        _copy_pdb(receptor, rec_dst)
    notes = [keep_chains(rec_dst, chains)]
    refuse_modified_nucleotides(rec_dst, "receptor")
    refuse_charged_modified(rec_dst, "receptor")
    lig_dst, cls = None, "none"
    if ligand:
        ligand = Path(ligand)
        cls = detect_ligand_class(ligand)
        if cls == "small_molecule":
            lig_dst, info = normalise_small_molecule(ligand, rundir / "input")
            if info["records"] > 1:
                notes.append(f"the ligand file holds {info['records']} molecules; the first is the ligand")
            if info["added_h"]:
                notes.append(f"added {info['added_h']} hydrogens to the ligand as drawn: its protonation state is the one "
                             "that runs, so check it against the pH")
        else:
            lig_dst = rundir / "input" / "ligand.pdb"
            if ligand.resolve() != lig_dst.resolve():
                _copy_pdb(ligand, lig_dst)
            refuse_modified_nucleotides(lig_dst, "partner")
            refuse_charged_modified(lig_dst, "partner")
    # the waters and ions the run keeps leave the two files for their own, before the files lose the rest
    pdb_partner = lig_dst is not None and lig_dst.suffix == ".pdb" and cls != "small_molecule"
    texts = {"receptor": rec_dst.read_text(errors="replace"), "partner": lig_dst.read_text(errors="replace") if pdb_partner else ""}
    water_note, waters_kept = keep_waters(rundir, texts, lig_dst, waters)
    notes.append(water_note)
    ions = keep_ions(rundir, texts, lig_dst if cls != "none" else None, cls == "small_molecule", keep_metals)
    notes.append(ions["note"])
    notes.append(mapped_in(rec_dst, "receptor"))
    notes.append(removed_note(left_out(strip_to_polymer(rec_dst), ions["kept"], "receptor"), "receptor"))
    breaks = mark_breaks(rec_dst)
    stretched = stretched_bonds(rec_dst.read_text(errors="replace"))
    notes += [breaks_note(breaks, "receptor"), stretched_note(stretched, "receptor") if stretched else ""]
    if pdb_partner:
        notes.append(mapped_in(lig_dst, "partner"))
        notes.append(removed_note(left_out(strip_to_polymer(lig_dst, drop=("ion", "water")), ions["kept"], "partner"), "partner"))
        partner_breaks = mark_breaks(lig_dst)
        partner_stretched = stretched_bonds(lig_dst.read_text(errors="replace"))
        notes += [breaks_note(partner_breaks, "partner"), stretched_note(partner_stretched, "partner") if partner_stretched else ""]
        breaks += partner_breaks
        stretched += partner_stretched
    sysm = detect_inputs(rundir, lig_dst)
    if not any(c["kind"] in ("protein", "nucleic") for c in sysm["chains_found"]):
        raise ValueError(f"no protein or nucleic-acid chain was read from the receptor file {Path(receptor).name}: there is nothing "
                         "in it to simulate. Check that it holds the receptor's coordinates.")
    sysm["breaks"] = breaks
    sysm["stretched"] = stretched
    sysm["waters_kept"] = waters_kept
    sysm["ions_kept"] = ions["entries"]
    # the ions that bind the partner come first in input/ions.pdb, and its residue range takes them, as after Prepare
    n_par = sum(1 for e in ions["entries"] if e["side"] == "partner")
    if n_par and sysm.get("ligand_residues"):
        sysm["ligand_residues"] = [sysm["ligand_residues"][0], sysm["ligand_residues"][1] + n_par]
    notes = [n for n in notes + [sysm.get("note", "")] if n]
    if notes:
        sysm["note"] = " · ".join(notes)
    return sysm


def keep_waters(rundir: Path, texts: dict[str, str], partner: Path | None, choice: str) -> tuple[str, list[str]]:
    """The crystal waters of the receptor and partner files the run keeps, written to input/waters.pdb (a stale one
    goes): none, those within structure.NEAR_WATER_A of the partner's heavy atoms (near_ligand), or all. Returns
    the note and the waters kept, "HOH A:301"."""
    from ..structure import water_groups, near_waters, waters_pdb, group_label, NEAR_WATER_A
    out = rundir / "input" / "waters.pdb"
    found = water_groups(texts["receptor"]) + water_groups(texts["partner"])
    kept = found if choice == "all" else []
    if choice == "near_ligand":
        kept = near_waters(found, _partner_xyz(partner) if partner else [])
    if kept:
        out.write_text(waters_pdb(kept))
    elif out.exists():
        out.unlink()
    if not found:
        return "", []
    if choice == "near_ligand" and not partner:
        note = f"no crystal water kept: with no partner file none is near one ({len(found)} left out)"
    elif choice == "near_ligand":
        note = (f"kept {len(kept)} of the {len(found)} crystal waters, those within {NEAR_WATER_A:g} Å of the partner"
                + (": the build adds them after the solute as the run's water model" if kept else ""))
    elif choice == "all":
        note = f"kept all {len(found)} crystal waters: the build adds them after the solute as the run's water model"
    else:
        note = f"{len(found)} crystal waters left out (crystal waters: none)"
    return note, [group_label(w) for w in kept]


def keep_ions(rundir: Path, texts: dict[str, str], partner: Path | None, small: bool, keep_metals: bool) -> dict:
    """The monatomic ions of the receptor and partner files the run keeps, as Prepare carries them: the cations a
    nucleic acid binds (within structure.BOUND_ION_A of it), and the metals when asked; only the ions the builds
    have parameters for (structure.BUILD_IONS). Each goes with the side whose heavy atom is nearest (the receptor
    beside a small molecule, which has no residue range of its own to take one), those of the partner first, into
    input/ions.pdb (a stale one goes). Returns {"kept": the groups, with the file each came from, "entries": what
    the run records of them (as Prepare's report lists the ions it carries), "note"}."""
    from ..structure import hetero_groups, heavy_xyz, ions_pdb, group_label, classify, CATIONS, BOUND_ION_A
    from ..prepare import metal_site
    out = rundir / "input" / "ions.pdb"
    nucleic = heavy_xyz(texts["receptor"], kinds=("nucleic",)) + heavy_xyz(texts["partner"], kinds=("nucleic",))
    solute = {"receptor": heavy_xyz(texts["receptor"], kinds=("protein", "nucleic", "other")),
              "partner": (heavy_xyz(texts["partner"], kinds=("protein", "nucleic", "other")) if texts["partner"]
                          else _partner_xyz(partner) if partner else [])}
    kept, entries = [], []
    for side_of_file in ("receptor", "partner"):
        for g in hetero_groups(texts[side_of_file]):
            if not g["ion"]:
                continue
            line = g["lines"][0]
            xyz = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
            bound = g["resname"] in CATIONS and any(math.dist(xyz, n) <= BOUND_ION_A for n in nucleic)
            if not (bound or (keep_metals and classify(g["resname"]) == "metal")):
                continue
            near = {s: min((math.dist(xyz, a) for a in solute[s]), default=float("inf")) for s in solute}
            side = "partner" if not small and near["partner"] < near["receptor"] else "receptor"
            site = metal_site(texts["receptor"], g) + metal_site(texts["partner"], g)
            kept.append(dict(g, file=side_of_file, side=side, bound=bound, site=site))
    kept.sort(key=lambda g: g["side"] != "partner")            # the partner's first: its residue range takes them
    if kept:
        out.write_text(ions_pdb(kept))
    elif out.exists():
        out.unlink()
    entries = [{"group": group_label(g), "charge": g["ion"][1], "site": g["site"], "side": g["side"]} for g in kept]
    bound = [group_label(g) for g in kept if g["bound"]]
    metals = [group_label(g) for g in kept if not g["bound"]]
    note = ""
    if kept:
        note = ("kept " + " and ".join(x for x in (
                    f"{', '.join(bound)} (bound to the nucleic acid)" if bound else "",
                    f"{', '.join(metals)} (metal{'s' if len(metals) > 1 else ''}, as asked)" if metals else "") if x)
                + f" as free ions: the build adds {'it' if len(kept) == 1 else 'them'} after the solute")
    return {"kept": kept, "entries": entries, "note": note}


def _partner_xyz(path: Path) -> list:
    """The partner's heavy atoms: a small molecule's, or a partner PDB's residues but its waters and ions."""
    if Path(path).suffix.lower() != ".pdb":
        return _heavy_xyz(Path(path))
    from ..structure import heavy_xyz
    return heavy_xyz(Path(path).read_text(errors="replace"), kinds=("protein", "nucleic", "other"))


def _copy_pdb(src: str | Path, dst: Path) -> None:
    """Copy a PDB with each residue's atoms made contiguous (see structure.group_by_residue)."""
    from ..structure import group_by_residue
    dst.write_text(group_by_residue(Path(src).read_text(errors="replace")))


def _heavy_xyz(path: Path) -> list:
    """Heavy-atom coordinates of a PDB, SDF/MOL or MOL2 file (what the placement check compares)."""
    text = Path(path).read_text(errors="replace")
    suf = Path(path).suffix.lower()
    out: list = []
    if suf in (".sdf", ".mol"):
        lines = text.splitlines()
        try:
            n = int(lines[3][:3]) if len(lines) > 3 else 0
        except ValueError:
            n = 0
        for line in lines[4:4 + n]:
            parts = line.split()
            if len(parts) >= 4 and parts[3].upper() != "H":
                try:
                    out.append((float(parts[0]), float(parts[1]), float(parts[2])))
                except ValueError:
                    pass
    elif suf == ".mol2":
        block = False
        for line in text.splitlines():
            if line.startswith("@<TRIPOS>ATOM"):
                block = True; continue
            if line.startswith("@<TRIPOS>"):
                block = False; continue
            if block:
                parts = line.split()
                if len(parts) >= 6 and not parts[5].upper().startswith("H"):
                    try:
                        out.append((float(parts[2]), float(parts[3]), float(parts[4])))
                    except ValueError:
                        pass
    else:
        for line in text.splitlines():
            if line.startswith(("ATOM", "HETATM")):
                el = line[76:78].strip().upper() or line[12:16].strip().lstrip("0123456789")[:1].upper()
                if el == "H":
                    continue
                try:
                    out.append((float(line[30:38]), float(line[38:46]), float(line[46:54])))
                except ValueError:
                    pass
    return out


def placement(receptor: str | Path, ligand: str | Path, contact_A: float = 4.5, clash_A: float = 1.5) -> dict:
    """How the partner sits against the receptor: the closest heavy-atom distance, the heavy-atom
    pairs within 4.5 A (contacts) and within 1.5 A (clashes). HOPE-MD does not dock: a partner
    that arrives away from the receptor stays there, so the import says so before a run is built."""
    import numpy as np
    r = np.array(_heavy_xyz(Path(receptor)), dtype=float)
    l = np.array(_heavy_xyz(Path(ligand)), dtype=float)
    if not len(r) or not len(l):
        return {}
    best, contacts, clashes = float("inf"), 0, 0
    for i in range(0, len(l), 256):                       # a few hundred partner atoms at a time: memory stays small
        d = np.sqrt(((l[i:i + 256, None, :] - r[None, :, :]) ** 2).sum(-1))
        best = min(best, float(d.min()))
        contacts += int((d < contact_A).sum())
        clashes += int((d < clash_A).sum())
    return {"min_distance_A": round(best, 2), "contacts": contacts, "clashes": clashes}


def placement_note(p: dict, what: str = "partner") -> str:
    """One line for the page: in contact, overlapping, or apart (and that HOPE-MD does not dock)."""
    if not p:
        return ""
    if p["min_distance_A"] > 6.0:
        return (f"the {what} is {p['min_distance_A']:g} Å from the receptor at its closest: HOPE-MD does not dock, it simulates "
                f"the pose it is given. Place the {what} first with a docking program, "
                "or import a docked pose.")
    if p["clashes"]:
        return f"{p['clashes']} heavy-atom pair(s) closer than 1.5 Å between receptor and {what}: they overlap, check the pose"
    return f"{what} in contact with the receptor: {p['contacts']} heavy-atom pairs within 4.5 Å, closest {p['min_distance_A']:g} Å"


def split_disulfides(receptor: Path, partner: Path, rinfo: PDBInfo, linfo: PDBInfo) -> list[dict]:
    """The disulfides whose two cysteines lie on either side of the split, receptor and partner (System.split_disulfides):
    [{"receptor": "H:122", "partner": "L:1", "residues": [119, 268], "distance_A": 1.97}], the residues by their places
    in the topology, the receptor's 1 to N in file order and the partner's from N + 1, as the masks count them. The
    build bonds each one (tleap's bond on those numbers; OpenMM's Modeller on a bilayer; an SSBOND record on Desmond),
    since pdb4amber, reading each file alone, bonds neither, and MM-GBSA is not run on a run that has one."""
    from ..structure import _residues, disulfide_pairs
    rres = _residues(receptor.read_text(errors="replace").splitlines())
    pres = _residues(partner.read_text(errors="replace").splitlines())
    place_r = {k: i + 1 for i, k in reversed(list(enumerate(rinfo.residue_keys)))}
    place_p = {k: i + 1 for i, k in reversed(list(enumerate(linfo.residue_keys)))}
    out = []
    for i, j, d in disulfide_pairs(rres, pres):
        a, b = f"{rres[i]['chain']}:{rres[i]['resi']}", f"{pres[j]['chain']}:{pres[j]['resi']}"
        if a in place_r and b in place_p:
            out.append({"receptor": a, "partner": b, "residues": [place_r[a], len(rinfo.residue_keys) + place_p[b]],
                        "distance_A": d})
    return out


def detect_inputs(rundir: str | Path, ligand_file: Path | None = None) -> dict:
    """The detected System fields from <rundir>/input/receptor.pdb (+ ligand.*), in place."""
    rundir = Path(rundir)
    rec_dst = rundir / "input" / "receptor.pdb"
    if ligand_file is None:
        ligand_file = next((p for p in (rundir / "input").glob("ligand.*")), None)
    rinfo = inspect_pdb(rec_dst)
    sysm: dict = {
        "ligand_class": "none", "solute_atoms": None, "extent_A": rinfo.extent,
        "disulfides": rinfo.disulfides, "chains_found": [c.__dict__ for c in rinfo.chains],
        "residue_index": {k: i + 1 for i, k in enumerate(rinfo.residue_keys)},
        "receptor_residues": [1, len(rinfo.residue_keys)], "ligand_residues": None,
        "ligand_charge": None, "ligand_formal_charge": None, "split_disulfides": [],
    }
    # a rough all-atom count for the box estimate: heavy atoms x 2 for a protein
    solute = rinfo.heavy_atoms * 2
    if ligand_file:
        dst = Path(ligand_file)
        cls = detect_ligand_class(dst)
        sysm["ligand_class"] = cls
        if cls == "small_molecule":
            sysm["ligand_charge"] = sdf_net_charge(dst) if dst.suffix != ".pdb" else None
            # a file with bond orders runs as it is drawn, so its formal charge is the net charge; a PDB ligand has none
            sysm["ligand_formal_charge"] = sysm["ligand_charge"]
            sysm["ligand_hydrogens"] = count_hydrogens(dst)
            sysm["ligand_residues"] = [len(rinfo.residue_keys) + 1, len(rinfo.residue_keys) + 1]
            solute += 60
        else:
            linfo = inspect_pdb(dst)
            n = len(linfo.residue_keys)
            sysm["ligand_residues"] = [len(rinfo.residue_keys) + 1, len(rinfo.residue_keys) + n]
            sysm["split_disulfides"] = split_disulfides(rec_dst, dst, rinfo, linfo)
            solute += linfo.heavy_atoms * 2
            # the box holds the two together: the extent of both, not the larger of each (1BHL's chains are 38 Å
            # tall each and 51 Å together)
            if rinfo.bounds and linfo.bounds:
                sysm["extent_A"] = [round(max(rinfo.bounds[1][i], linfo.bounds[1][i]) - min(rinfo.bounds[0][i], linfo.bounds[0][i]), 1)
                                    for i in range(3)]
            else:
                sysm["extent_A"] = [max(a, b) for a, b in zip(rinfo.extent, linfo.extent)]
        try:
            sysm["placement"] = placement(rec_dst, dst)
        except Exception:                                   # a file numpy cannot read: the check is advisory
            sysm["placement"] = {}
        what = {"peptide": "peptide", "protein": "partner", "nucleic_acid": "partner", "small_molecule": "ligand"}.get(cls, "partner")
        note = placement_note(sysm["placement"], what)
        if note:
            sysm["note"] = note
    # the solute as the build solvates it, for tleap's box (hopemd.solvation); the count of all its atoms, hydrogens
    # the build adds included, in place of the rough one when the files can be read
    from ..solvation import solute_shape
    shape = solute_shape(staged_solute(rundir, Path(ligand_file) if ligand_file else None))
    sysm["solute_shape"] = shape
    sysm["solute_atoms"] = shape.get("atoms") or solute
    return sysm


def staged_solute(rundir: str | Path, ligand_file: Path | None = None) -> list[tuple]:
    """The atoms of the solute the build solvates: the receptor and the partner (their waters left out, as pdb4amber
    --dry leaves them), then the ions and the crystal waters the run keeps (input/ions.pdb, input/waters.pdb)."""
    from ..solvation import read_atoms
    inp = Path(rundir) / "input"
    atoms = read_atoms(inp / "receptor.pdb", waters=False)
    if ligand_file:
        atoms += read_atoms(ligand_file, waters=False)
    for extra in ("ions.pdb", "waters.pdb"):
        if (inp / extra).exists():
            atoms += read_atoms(inp / extra)
    return atoms
