"""A small molecule bound in an entry or complex file, run as the partner.

The import lists the small molecules a structure holds that could be the partner (bound_ligands):
waters, monatomic ions, crystallization additives and polymer residues are not among them. The one
chosen (source.ligand, "BTN A 300") is typed against its definition in the PDB's Chemical
Component Dictionary, fetched at import and kept as input/ccd_<code>.cif: where the atom names agree
with the CCD's, its bond orders and formal charges are carried over by name, and elsewhere RDKit
matches the CCD's graph to the bonds the coordinates make. A short list of rules sets its acids and
bases for pH 7.4, its hydrogens are added with coordinates, and it is written as input/ligand.sdf,
the file the small-molecule path reads on every route. It is taken out of input/complex.pdb with
every other copy of its component (one ligand runs per system), so Prepare never sees them, and
every polymer chain Prepare keeps is the receptor. A ligand this cannot type rightly (bound
covalently, holding a metal, missing atoms, spread over several residues) is refused by name.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from ..structure import WATER, classify, cif_loop, inventory, _cif_tokens
from .files import METALS

CCD_FILES = "https://files.rcsb.org/ligands/download/{}.cif"

#: crystallization additives: cryoprotectants, precipitants, buffers and salts, never offered as the partner
ADDITIVES = {"GOL", "EDO", "PEG", "PGE", "PG4", "1PE", "P6G", "MPD", "MRD", "DMS", "ACT", "ACY", "FMT", "SO4", "PO4",
             "NO3", "CIT", "TRS", "EPE", "MES", "IMD", "BME", "DTT", "IPA", "EOH", "MOH", "CL", "NA"}

#: covalent radii (Å): two atoms closer than the sum of theirs plus BOND_SLACK are bonded, not in contact
COVALENT_RADIUS = {"H": 0.31, "B": 0.84, "C": 0.76, "N": 0.71, "O": 0.66, "F": 0.57, "Si": 1.11, "P": 1.07,
                   "S": 1.05, "Cl": 1.02, "Se": 1.20, "Br": 1.20, "I": 1.39}
BOND_SLACK = 0.45
CELL_A = 3.2           # the grid the bond search runs on: no bond looked for is longer

#: the acids and bases set for pH 7.4: (group, SMARTS whose first atom gains or loses the proton, charge
#: change). Anilines, amides, aromatic nitrogens, phenols and anything else not listed keep the CCD's form.
PH74 = (
    ("carboxylic acid", "[OX2H1][CX3]=O", -1),
    ("phosphate or phosphonate OH", "[OX2H1][PX4]=O", -1),
    ("sulfate or sulfonate OH", "[OX2H1][SX4](=O)=O", -1),
    ("aliphatic amine", "[NX3;+0;!$(N~[!C,!X4])]", +1),
    ("amidine", "[NX2;+0;!a;!$(N-[!#6]);!$(N-[CX3]=[O,S])]=[CX3;!a;!$(C~[!#6;!#7]);!$(C(~[#7])(~[#7])~[#7])]"
                "[NX3;+0;!a;!$(N-[CX3]=[O,S]);!$(N-[SX4])]", +1),
    ("guanidine", "[NX2;+0;!a;!$(N-[!#6]);!$(N-C#N);!$(N-[SX4])]=[CX3;!a]([NX3;+0;!a;!$(N-[SX4]);!$(N-C#N)])"
                  "[NX3;+0;!a;!$(N-[SX4]);!$(N-C#N)]", +1),
)
BOND_ORDER = {"SING": 1, "DOUB": 2, "TRIP": 3, "AROM": 12}


# ------------------------------------------------------------------------------- the structure
def _res(line: str) -> tuple[str, str, str]:
    """(residue name, chain, number with any insertion code) of a coordinate record; a blank chain
    is A, as the inventory counts it."""
    return line[17:20].strip(), line[21].strip() or "A", line[22:27].strip()


def _atoms(pdb_text: str) -> list[dict]:
    out = []
    for line in pdb_text.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        try:
            xyz = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
        except ValueError:
            continue
        el = (line[76:78].strip() or line[12:14].strip().lstrip("0123456789")).capitalize()
        out.append({"serial": line[6:11].strip(), "name": line[12:16].strip(), "alt": line[16].strip(),
                    "res": _res(line), "el": el, "xyz": xyz})
    return out


def _cell(xyz) -> tuple[int, int, int]:
    return int(math.floor(xyz[0] / CELL_A)), int(math.floor(xyz[1] / CELL_A)), int(math.floor(xyz[2] / CELL_A))


def _texts(rundir: Path) -> tuple[str, str]:
    """The staged structure, and the mmCIF it came from when it came from one (for its struct_conn)."""
    inp = Path(rundir) / "input"
    cif = next(iter(sorted(inp.glob("original*.cif"))), None)
    return (inp / "complex.pdb").read_text(errors="replace"), (cif.read_text(errors="replace") if cif else "")


def _links(pdb_text: str, cif_text: str = "") -> list[tuple]:
    """The bonds between residues a file declares: LINK and CONECT records of PDB text, the covalent
    rows of an mmCIF's struct_conn (chains renamed as cif_to_pdb renamed them).
    [(residue, atom, residue, atom, "a LINK record")]"""
    out, serial = [], {}
    for line in pdb_text.splitlines():
        if line.startswith(("ATOM", "HETATM")):
            serial[line[6:11].strip()] = (_res(line), line[12:16].strip())
        elif line.startswith("LINK"):
            r1 = (line[17:20].strip(), line[21:22].strip() or "A", line[22:27].strip())
            r2 = (line[47:50].strip(), line[51:52].strip() or "A", line[52:57].strip())
            out.append((r1, line[12:16].strip(), r2, line[42:46].strip(), "a LINK record"))
    for line in pdb_text.splitlines():
        if line.startswith("CONECT"):
            ids = [line[i:i + 5].strip() for i in range(6, min(len(line), 31), 5)]
            a = serial.get(ids[0]) if ids else None
            for s in ids[1:]:
                b = serial.get(s)
                if a and b and a[0] != b[0]:
                    out.append((a[0], a[1], b[0], b[1], "a CONECT record"))
    if cif_text:
        m = re.search(r"^REMARK 900 HOPEMD chain map (.*)$", pdb_text, re.M)
        renamed = dict(p.split("=", 1) for p in m.group(1).split()) if m else {}
        for r in _category(cif_text, "_struct_conn"):
            if not r.get("conn_type_id", "").startswith("covale"):
                continue
            ends = []
            for k in ("1", "2"):
                ins = r.get(f"pdbx_ptnr{k}_PDB_ins_code", "")
                ch = r.get(f"ptnr{k}_auth_asym_id", "")
                ends.append(((r.get(f"ptnr{k}_auth_comp_id", ""), renamed.get(ch, ch),
                              r.get(f"ptnr{k}_auth_seq_id", "") + (ins if ins not in ("?", ".") else "")),
                             r.get(f"ptnr{k}_label_atom_id", "")))
            out.append((ends[0][0], ends[0][1], ends[1][0], ends[1][1], "a struct_conn record"))
    return out


def _site(pdb_text: str, cif_text: str = "") -> dict:
    """The heavy atoms Clean would keep (altloc A), waters aside, by residue and on a grid, with
    the declared links."""
    atoms = [a for a in _atoms(pdb_text) if a["alt"] in ("", "A") and a["el"] not in ("H", "D") and a["res"][0] not in WATER]
    by_res: dict[tuple, list[int]] = {}
    grid: dict[tuple, list[int]] = {}
    for i, a in enumerate(atoms):
        by_res.setdefault(a["res"], []).append(i)
        grid.setdefault(_cell(a["xyz"]), []).append(i)
    return {"atoms": atoms, "by_res": by_res, "grid": grid, "links": _links(pdb_text, cif_text),
            "element": {(a["res"], a["name"]): a["el"] for a in atoms}}


def _bonded(res: tuple, site: dict) -> list[tuple[tuple, str]]:
    """The residues `res` is bonded to, each with what says so: a LINK, CONECT or struct_conn record,
    or two atoms closer than their covalent radii allow. A bond to a metal or a monatomic ion is a
    coordination, not a bond to type, and does not count."""
    atoms, by_res, element = site["atoms"], site["by_res"], site["element"]

    def counts(other, a_el, b_el):
        return (other in by_res and len(by_res[other]) > 1 and a_el not in METALS and b_el not in METALS
                and classify(other[0]) not in ("metal", "ion", "water"))
    found: dict[tuple, str] = {}
    for r1, n1, r2, n2, record in site["links"]:
        for mine, my_atom, other, its_atom in ((r1, n1, r2, n2), (r2, n2, r1, n1)):
            if mine == res and other != res and counts(other, element.get((mine, my_atom), ""), element.get((other, its_atom), "")):
                found.setdefault(other, f"{my_atom}–{its_atom}, {record}")
    for i in by_res.get(res, []):
        a = atoms[i]
        cx, cy, cz = _cell(a["xyz"])
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in site["grid"].get((cx + dx, cy + dy, cz + dz), ()):
                        b = atoms[j]
                        if b["res"] == res or b["res"] in found or not counts(b["res"], a["el"], b["el"]):
                            continue
                        d = math.dist(a["xyz"], b["xyz"])
                        if d < COVALENT_RADIUS.get(a["el"], 0.8) + COVALENT_RADIUS.get(b["el"], 0.8) + BOND_SLACK:
                            found[b["res"]] = f"{a['name']}–{b['name']} {d:.2f} Å apart"
    # a bond to the polymer first: it is the one that says the most
    return sorted(found.items(), key=lambda kv: classify(kv[0][0]) not in ("protein", "nucleic"))


def _refusal(res: tuple, site: dict) -> tuple[str, str]:
    """Why a bound residue cannot be run as the partner: (a few words for the list, the sentence an
    import that asks for it is refused with). ("", "") when it can, as far as the structure tells."""
    key = " ".join(res)
    metals = sorted({site["atoms"][i]["el"] for i in site["by_res"].get(res, [])} & METALS)
    if metals:
        m = ", ".join(metals)
        return (f"contains {m}", f"{key} contains {m}: the ligand force fields here (GAFF2 with AM1-BCC, OPLS for an "
                "organic ligand) have no parameters for a metal complex, so HOPE-MD does not run it as the partner.")
    for other, how in _bonded(res, site):
        nbr = " ".join(other)
        if classify(other[0]) in ("protein", "nucleic"):
            return (f"covalently bound to {nbr}", f"{key} is covalently bound to {nbr} ({how}): a covalent ligand is part of "
                    "the polymer, not a separate molecule, so HOPE-MD does not run it as the partner.")
        return (f"bonded to {nbr}", f"{key} is bonded to {nbr} ({how}): it is one residue of a ligand spread over several, "
                "a peptide-like or polysaccharide chain, which one CCD component cannot type, so HOPE-MD does not run it as "
                "the partner.")
    return "", ""


def _offered(res: tuple, n_heavy: int) -> bool:
    return (n_heavy > 1 and res[0] not in ADDITIVES and res[0] not in WATER
            and classify(res[0]) not in ("protein", "nucleic", "water"))


def bound_ligands(rundir: str | Path) -> list[dict]:
    """The small molecules bound in the staged structure (input/complex.pdb) that could be run as
    the partner, in file order: {key ("BTN A 300"), resname, chain, resi, heavy_atoms, refused}.
    refused says in a few words why one cannot be (a metal, a covalent bond, one residue of
    several) and is empty when it can. Waters, monatomic ions, crystallization additives and
    polymer residues are not listed."""
    site = _site(*_texts(Path(rundir)))
    out = []
    for res, idx in site["by_res"].items():
        if not _offered(res, len(idx)):
            continue
        out.append({"key": " ".join(res), "resname": res[0], "chain": res[1], "resi": res[2], "heavy_atoms": len(idx),
                    "refused": _refusal(res, site)[0]})
    return out


# ------------------------------------------------------------------------------------- the CCD
def _category(text: str, cat: str) -> list[dict]:
    """The rows of one mmCIF category as dicts, looped or not (a category of one row is written
    as key-value pairs)."""
    cols, rows = cif_loop(text, cat)
    if cols:
        return [dict(zip(cols, r)) for r in rows]
    row = {}
    for line in text.splitlines():
        if line.startswith(cat + "."):
            k, _, v = line.partition(" ")
            vals = _cif_tokens(v.strip())
            row[k.split(".", 1)[1]] = vals[0] if vals else ""
    return [row] if row else []


def fetch_ccd(code: str, dest_dir: str | Path) -> Path:
    """A component's CCD definition as mmCIF, fetched from the RCSB at import and kept as
    <dest_dir>/ccd_<code>.cif; a folder that has it already reads it."""
    code = code.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{1,5}", code):
        raise ValueError(f"{code!r} is not a component code of the CCD")
    dst = Path(dest_dir) / f"ccd_{code}.cif"
    if not dst.exists():
        from . import complex as cx
        try:
            text = cx._get(CCD_FILES.format(code), timeout=30).decode("utf-8", "replace")
        except Exception as e:  # no network, no such component: the import says which
            raise ValueError(f"the CCD definition of {code} could not be fetched from the RCSB ({e}); the ligand's bond "
                             "orders come from it")
        if not text.lstrip().startswith("data_"):
            raise ValueError(f"the RCSB sent no CCD definition for {code}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text)
    return dst


def read_ccd(path: str | Path) -> dict:
    """A CCD definition: {code, name, atoms: {name: {el, charge}}, bonds: [(name, name, order)]}."""
    text = Path(path).read_text(errors="replace")
    comp = (_category(text, "_chem_comp") or [{}])[0]
    atoms = {}
    for r in _category(text, "_chem_comp_atom"):
        q = r.get("charge", "0")
        atoms[r["atom_id"]] = {"el": r.get("type_symbol", "").capitalize(),
                               "charge": int(q) if re.fullmatch(r"[+-]?\d+", q) else 0}
    bonds = [(r["atom_id_1"], r["atom_id_2"], r.get("value_order", "SING").upper())
             for r in _category(text, "_chem_comp_bond") if r.get("atom_id_1") in atoms and r.get("atom_id_2") in atoms]
    return {"code": comp.get("id", Path(path).stem.replace("ccd_", "")), "name": comp.get("name", ""), "atoms": atoms, "bonds": bonds}


# --------------------------------------------------------------------------------- the molecule
def _mol(atoms: list[tuple[str, int]], bonds: list[tuple[int, int, str]], xyz: list | None = None):
    """An RDKit molecule from (element, formal charge) atoms and (i, j, CCD order) bonds, with
    coordinates when given. Not sanitized."""
    from rdkit import Chem
    from rdkit.Geometry import Point3D
    rw = Chem.RWMol()
    for el, q in atoms:
        a = Chem.Atom(el)
        a.SetFormalCharge(q)
        rw.AddAtom(a)
    kinds = {1: Chem.BondType.SINGLE, 2: Chem.BondType.DOUBLE, 3: Chem.BondType.TRIPLE, 12: Chem.BondType.AROMATIC}
    for i, j, order in bonds:
        rw.AddBond(i, j, kinds[BOND_ORDER.get(order, 1)])
        if order == "AROM":
            for k in (i, j):
                rw.GetAtomWithIdx(k).SetIsAromatic(True)
            rw.GetBondBetweenAtoms(i, j).SetIsAromatic(True)
    mol = rw.GetMol()
    if xyz:
        conf = Chem.Conformer(len(atoms))
        for k, p in enumerate(xyz):
            conf.SetAtomPosition(k, Point3D(*p))
        mol.AddConformer(conf, assignId=True)
    return mol


def _incomplete(key: str, code: str, have: int, want: int, missing: list[str] | None = None) -> ValueError:
    names = f" ({', '.join(missing[:8])}{', …' if len(missing) > 8 else ''} not in the structure)" if missing else ""
    return ValueError(f"{key} has {have} of the {want} heavy atoms the CCD defines for {code}{names}: an incomplete "
                      "ligand cannot be typed, so HOPE-MD does not run it as the partner. Model the missing atoms first.")


def typed_from_ccd(heavy: list[dict], ccd: dict, key: str):
    """The crystal's heavy atoms as one molecule with the CCD's bond orders and formal charges, on the
    crystal's coordinates: by atom name where every name agrees with the CCD (element included), else
    by matching the CCD's graph to the bonds the coordinates make (RDKit's
    AssignBondOrdersFromTemplate). Returns (mol, "names" | "template"); raises ValueError for a
    ligand with atoms missing or one the CCD does not describe."""
    from rdkit import Chem
    code = ccd["code"]
    want = {n: a for n, a in ccd["atoms"].items() if a["el"] not in ("H", "D")}
    if not want:
        raise ValueError(f"the CCD defines no atoms for {code}: there is nothing to type {key} from")
    names = [a["name"] for a in heavy]
    by_name = len(set(names)) == len(names) and all(n in want and want[n]["el"] == a["el"] for n, a in zip(names, heavy))
    missing = [n for n in want if n not in set(names)] if by_name else []
    if missing or len(heavy) < len(want):
        raise _incomplete(key, code, len(heavy), len(want), missing)
    if len(heavy) > len(want):
        raise ValueError(f"{key} has {len(heavy)} heavy atoms where the CCD defines {len(want)} for {code}: it is not the "
                         "component its name says, so HOPE-MD does not run it as the partner.")
    try:  # RDKit's own complaints (an element it does not know, a valence, a ring it cannot kekulize)
        if by_name:
            at = {n: i for i, n in enumerate(names)}
            mol = _mol([(a["el"], want[a["name"]]["charge"]) for a in heavy],
                       [(at[x], at[y], o) for x, y, o in ccd["bonds"] if x in at and y in at], [a["xyz"] for a in heavy])
            Chem.SanitizeMol(mol)
            return mol, "names"
        from rdkit.Chem import AllChem
        tat = {n: i for i, n in enumerate(want)}
        template = _mol([(a["el"], a["charge"]) for a in want.values()],
                        [(tat[x], tat[y], o) for x, y, o in ccd["bonds"] if x in tat and y in tat])
        # Kekulé bonds across: an aromatic NH handed over without its hydrogen would not kekulize again
        Chem.SanitizeMol(template, Chem.SanitizeFlags.SANITIZE_ALL ^ Chem.SanitizeFlags.SANITIZE_SETAROMATICITY)
        crystal = _mol([(a["el"], 0) for a in heavy], [(i, j, "SING") for i, j in _geometric_bonds(heavy)], [a["xyz"] for a in heavy])
        crystal.UpdatePropertyCache(strict=False)
        Chem.FastFindRings(crystal)
    except Exception as e:
        raise ValueError(f"{key} could not be typed from the CCD definition of {code} ({e})")
    try:
        return AllChem.AssignBondOrdersFromTemplate(template, crystal), "template"
    except Exception:
        raise ValueError(f"the bonds {key}'s coordinates make are not those the CCD defines for {code}, so it cannot be "
                         "typed as that component and HOPE-MD does not run it as the partner.")


def _geometric_bonds(heavy: list[dict]) -> list[tuple[int, int]]:
    out = []
    for i in range(len(heavy)):
        for j in range(i + 1, len(heavy)):
            a, b = heavy[i], heavy[j]
            if math.dist(a["xyz"], b["xyz"]) < COVALENT_RADIUS.get(a["el"], 0.8) + COVALENT_RADIUS.get(b["el"], 0.8) + BOND_SLACK:
                out.append((i, j))
    return out


def protonate_ph74(mol):
    """The molecule's acids and bases set for pH 7.4 by the rules of PH74, on the heavy-atom molecule
    (hydrogens implicit). A group already charged is left as it is; one proton goes on an amidine or
    guanidine however many nitrogens it shares (a biguanide takes one); of two amines two carbons
    apart (ethylenediamine, piperazine) only the first is protonated, the second's pKa being near 6.
    Returns the new molecule and what changed, ["carboxylic acid deprotonated", ...]."""
    from rdkit import Chem
    rw = Chem.RWMol(mol)
    dist = Chem.GetDistanceMatrix(rw)
    changed, amines, conjugated = [], [], set()
    for what, smarts, dq in PH74:
        for match in rw.GetSubstructMatches(Chem.MolFromSmarts(smarts)):
            i = match[0]
            if dq > 0 and set(match) & conjugated:
                continue
            if what == "aliphatic amine":
                if any(dist[i][j] == 3 for j in amines):
                    continue
                amines.append(i)
            if what in ("amidine", "guanidine"):
                conjugated |= set(match)
            atom = rw.GetAtomWithIdx(i)
            atom.SetFormalCharge(atom.GetFormalCharge() + dq)
            atom.SetNumExplicitHs(0)          # its hydrogens follow from the new charge
            atom.SetNoImplicit(False)
            changed.append(f"{what} {'protonated' if dq > 0 else 'deprotonated'}")
        rw.UpdatePropertyCache(strict=False)
    out = rw.GetMol()
    Chem.SanitizeMol(out)
    return out, changed


def _said(changed: list[str]) -> str:
    """["carboxylic acid deprotonated", ...] as a phrase: "carboxylic acid deprotonated, 2 aliphatic
    amines protonated"."""
    out = []
    for c in dict.fromkeys(changed):
        n = changed.count(c)
        what, done = c.rsplit(" ", 1)
        out.append(f"{what} {done}" if n == 1 else f"{n} {what}s {done}")
    return ", ".join(out)


def _label(ccd: dict) -> str:
    name = ccd.get("name", "").strip()
    return name.lower() if name.replace(" ", "").isalpha() else name


# ----------------------------------------------------------------------------------- the import
def parse_key(key: str) -> tuple[str, str, str]:
    """"BTN A 300" (or BTN:A:300) as (residue name, chain, number)."""
    parts = [p for p in re.split(r"[\s:]+", key.strip()) if p]
    if len(parts) != 3:
        raise ValueError(f"{key!r} does not name one residue: give the residue name, chain and number, as BTN A 300")
    return parts[0].upper(), parts[1], parts[2]


def take_bound_ligand(rundir: str | Path, key: str, ph: float | None = None) -> dict:
    """Make the bound ligand `key` of the staged structure the partner: typed against its CCD
    definition, set for pH 7.4, written with its hydrogens as input/ligand.sdf, and taken out of
    input/complex.pdb with every other copy of its component, so that Prepare never sees them (one
    ligand runs per system). Returns the System fields of a small-molecule partner, with every polymer
    chain as the receptor, how its net charge was reached under meta (to merge into the import's), and
    the import's note under "note", which says too when the run's pH `ph` is far from 7.4. Raises
    ValueError with the reason when it cannot be typed rightly."""
    try:
        from rdkit import Chem
    except ImportError:
        raise ValueError("a bound ligand is typed with RDKit, which this server's Python does not have: run it from the "
                         "lab's environment")
    rundir = Path(rundir)
    inp = rundir / "input"
    pdb_text, cif_text = _texts(rundir)
    res = parse_key(key)
    key = " ".join(res)
    lines = pdb_text.splitlines()
    mine = [ln for ln in lines if ln.startswith(("ATOM", "HETATM")) and _res(ln) == res]
    if not mine:
        found = [c["key"] for c in bound_ligands(rundir) if not c["refused"]]
        raise ValueError(f"no {key} in this structure: " + (f"the bound ligands it holds are {', '.join(found)}" if found
                                                            else "it holds no bound ligand that could be the partner"))
    site = _site(pdb_text, cif_text)
    if not _offered(res, len(site["by_res"].get(res, []))):
        raise ValueError(f"{key} is a polymer residue, a water, an ion or a crystallization additive, not a bound ligand "
                         "to run as the partner")
    why = _refusal(res, site)[1]
    if why:
        raise ValueError(why)
    heavy = [a for a in _atoms("\n".join(mine)) if a["alt"] in ("", "A") and a["el"] not in ("H", "D")]
    ccd = read_ccd(fetch_ccd(res[0], inp))
    mol, how = typed_from_ccd(heavy, ccd, key)
    mol, changed = protonate_ph74(mol)
    Chem.AssignStereochemistryFrom3D(mol)
    q = Chem.GetFormalCharge(mol)
    full = Chem.AddHs(mol, addCoords=True)
    full.SetProp("_Name", res[0])
    w = Chem.SDWriter(str(inp / "ligand.sdf"))
    w.write(full)
    w.close()
    copy = lambda ln: ln.startswith(("ATOM", "HETATM", "ANISOU")) and ln[17:20].strip() == res[0]     # noqa: E731
    others = list(dict.fromkeys(_res(ln) for ln in lines if copy(ln) and _res(ln) != res))
    kept = "\n".join(ln for ln in lines if not copy(ln)) + "\n"
    (inp / "complex.pdb").write_text(kept)
    said = f"{_said(changed) or 'no group changed'} at pH 7.4"
    notes = [f"{key} ({_label(ccd) or res[0]}) is the partner, typed against its CCD definition"
             + ("" if how == "names" else " by matching its bonds, as its atom names differ from the CCD's")
             + f": {said}, net charge {q:+d}. Prepare works on the rest of the structure without it"]
    if others:
        notes.append(f"the other copies of {res[0]} ({', '.join(f'{c} {n}' for _, c, n in others)}) were removed: one ligand "
                     "runs per system")
    alts = sorted({ln[16] for ln in mine} - {" "})
    if alts:
        notes.append(f"{key} has alternate conformations ({', '.join(alts)}): altloc A was taken, as Prepare takes it for the "
                     "rest of the structure")
    notes.append(ph_caveat(key, ph))
    left = inventory(kept)            # the structure Clean will see: the chosen component is in none of its groups
    return {"ligand_class": "small_molecule", "ligand_charge": q, "ligand_formal_charge": q, "ligand_charge_confirmed": False,
            "ligand_hydrogens": sum(1 for a in full.GetAtoms() if a.GetAtomicNum() == 1),
            "receptor_chains": left.polymer_chains(), "partner_chains": [], "hetero_found": left.hetero,
            "meta": {"ligand_protonation": said}, "note": " · ".join(n for n in notes if n)}


def ph_caveat(key: str, ph) -> str:
    """What to say when the run's pH is more than a unit from the 7.4 the rules set a bound ligand
    for; "" when it is not (or is not known)."""
    try:
        ph = float(ph)
    except (TypeError, ValueError):
        return ""
    if abs(ph - 7.4) <= 1.0 + 1e-9:            # 6.4 and 8.4 are within a unit, floating point aside
        return ""
    return (f"{key}'s acids and bases were set for pH 7.4, not for this run's pH of {ph:g}: check them, and the net charge, "
            "before you confirm it")


def sdf_as_pdb(path: str | Path) -> str:
    """The first record of an SDF as HETATM lines (residue name from its title when that is a code,
    else LIG; chain L), for the viewer, which draws the partner as the second model."""
    lines = Path(path).read_text(errors="replace").splitlines()
    try:
        n = int(lines[3][:3])
    except (IndexError, ValueError):
        return ""
    title = lines[0].strip()
    resn = title if re.fullmatch(r"[A-Z0-9]{1,3}", title) else "LIG"
    out, seen = [], {}
    for k, line in enumerate(lines[4:4 + n], start=1):
        parts = line.split()
        if len(parts) < 4:
            continue
        el = parts[3].capitalize()
        seen[el] = seen.get(el, 0) + 1
        name = f"{el}{seen[el]}"[:4]
        nm = name if len(name) == 4 else " " + name.ljust(3)
        out.append(f"HETATM{k:5d} {nm:4s} {resn:>3s} L   1    {float(parts[0]):8.3f}{float(parts[1]):8.3f}{float(parts[2]):8.3f}"
                   f"  1.00  0.00          {el.upper():>2s}")
    return "\n".join(out)
