"""Maestro files (.mae, .maegz) read without Schrödinger: the format is text, one f_m_ct
block per structure with an m_atom table (coordinates, PDB names, chain, residue number,
atomic number) and an m_bond table. Each block becomes PDB text; a poseviewer file is the
receptor followed by the poses.

    structures = read_mae(path)          # [{"title": ..., "pdb": ..., "sdf": ... or None}, ...]

A block that holds no protein, nucleic acid or water is a ligand: it is also written as an SDF
record with the file's bond orders and formal charges, which a PDB cannot carry and the force
fields need (antechamber's bond perception and Desmond's OPLS typing both read them).
"""
from __future__ import annotations

import gzip
import re
from pathlib import Path

SYMBOLS = ("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr "
           "Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au "
           "Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og").split()
#: atomic number -> the symbol as a PDB element column has it (upper case)
ELEMENTS = {z: sym.upper() for z, sym in enumerate(SYMBOLS, 1)}
STANDARD = {"ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE", "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP",
            "TYR", "VAL", "HID", "HIE", "HIP", "CYX", "ASH", "GLH", "LYN", "DA", "DC", "DG", "DT", "A", "C", "G", "U"}


def _tokens(text: str):
    """Words, quoted strings (as one token, quotes kept off; a long value may run across lines, as
    Maestro writes PubChem names and SMILES), braces and ':::'; '#' comments run to the end of
    their line."""
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            i += 1
        elif ch == "#":
            j = text.find("\n", i)
            i = n if j == -1 else j + 1
        elif ch == '"':
            j = text.find('"', i + 1)
            while j != -1 and text[j - 1] == "\\":
                j = text.find('"', j + 1)
            if j == -1:
                j = n
            out.append(('"', text[i + 1:j].replace('\\"', '"')))
            i = j + 1
        elif ch in "{}":
            out.append((ch, ch)); i += 1
        else:
            j = i
            while j < n and text[j] not in " \t\r\n{}\"":
                j += 1
            out.append(("w", text[i:j])); i = j
    return out


def _parse_block(toks: list, pos: int) -> tuple[dict, int]:
    """After a block's opening brace: property names up to ':::', their values, then nested
    blocks until the closing brace. Tables (name[N]) hold N rows of the columns."""
    names = []
    while toks[pos][1] != ":::":
        names.append(toks[pos][1]); pos += 1
    pos += 1
    values = []
    while len(values) < len(names):
        values.append(toks[pos][1]); pos += 1
    block = {"props": dict(zip(names, values)), "children": []}
    while toks[pos][1] != "}":
        head = toks[pos][1]; pos += 1
        m = re.match(r"^(\w+)\[(\d+)\]$", head)
        assert toks[pos][1] == "{", f"expected a block after {head}"
        pos += 1
        if m:
            cols = []
            while toks[pos][1] != ":::":
                cols.append(toks[pos][1]); pos += 1
            pos += 1
            nrows, rows = int(m.group(2)), []
            width = len(cols) + 1                     # the row index comes first
            for _ in range(nrows):
                rows.append([t[1] for t in toks[pos:pos + width]]); pos += width
            while toks[pos][1] == ":::":
                pos += 1
            assert toks[pos][1] == "}", f"table {head} did not end where its row count said"
            pos += 1
            block["children"].append({"name": m.group(1), "cols": cols, "rows": rows})
        else:
            child, pos = _parse_block(toks, pos)
            child["name"] = head
            block["children"].append(child)
    return block, pos + 1


def parse_mae(text: str) -> list[dict]:
    toks = _tokens(text)
    out, pos = [], 0
    while pos < len(toks):
        head = toks[pos][1]
        if head == "{":                              # the header block has no name
            _, pos = _parse_block(toks, pos + 1); continue
        pos += 1
        if pos < len(toks) and toks[pos][1] == "{":
            block, pos = _parse_block(toks, pos + 1)
            block["name"] = head
            if head == "f_m_ct":
                out.append(block)
    return out


def ct_to_pdb(ct: dict) -> str:
    atoms = next((c for c in ct["children"] if c.get("name") == "m_atom"), None)
    if not atoms:
        return ""
    col = {c: i + 1 for i, c in enumerate(atoms["cols"])}   # +1: the row index is column 0
    def get(row, name, default=""):
        i = col.get(name)
        v = row[i] if i is not None and i < len(row) else default
        return default if v in ("<>", "") else v
    lines = []
    for k, row in enumerate(atoms["rows"], 1):
        try:
            x, y, z = float(get(row, "r_m_x_coord", "0")), float(get(row, "r_m_y_coord", "0")), float(get(row, "r_m_z_coord", "0"))
        except ValueError:
            continue
        el = ELEMENTS.get(int(get(row, "i_m_atomic_number", "6") or 6), "C")
        resname = (get(row, "s_m_pdb_residue_name", "") or "UNK").strip().upper()[:3] or "UNK"
        name = (get(row, "s_m_pdb_atom_name", "") or f"{el}{k}"[:4])
        name = name if len(name) == 4 else name.strip().rjust(2 if len(el) == 1 else 2).ljust(4) if len(name.strip()) < 4 else name.strip()[:4]
        if len(name) < 4:
            name = (" " + name.strip()).ljust(4) if len(el) == 1 else name.strip().ljust(4)
        chain = (get(row, "s_m_chain_name", "") or " ")[0]
        try:
            resnum = int(get(row, "i_m_residue_number", str(k)))
        except ValueError:
            resnum = k
        icode = (get(row, "s_m_insertion_code", "") or " ")[0]
        rec = "ATOM  " if resname in STANDARD else "HETATM"
        lines.append(f"{rec}{k:5d} {name:4s} {resname:>3s} {chain}{resnum:4d}{icode}   {x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00          {el:>2s}")
    return "\n".join(lines) + "\nEND\n"


def _table(ct: dict, name: str) -> dict | None:
    return next((c for c in ct["children"] if c.get("name") == name), None)


def _getter(table: dict):
    col = {c: i + 1 for i, c in enumerate(table["cols"])}   # +1: the row index is column 0
    def get(row, name, default=""):
        i = col.get(name)
        v = row[i] if i is not None and i < len(row) else default
        return default if v in ("<>", "") else v
    return get


def is_ligand_ct(ct: dict) -> bool:
    """A structure block with no standard residue and no water: a docked ligand, not a receptor."""
    atoms = _table(ct, "m_atom")
    if not atoms or not atoms["rows"]:
        return False
    get = _getter(atoms)
    names = {(get(row, "s_m_pdb_residue_name", "") or "UNK").strip().upper() for row in atoms["rows"]}
    return not (names & STANDARD) and not (names & {"HOH", "WAT", "SPC", "T3P", "TIP3"})


def ct_to_sdf(ct: dict) -> str | None:
    """A ligand block as one V2000 SDF record: coordinates, elements, the m_bond table's orders and
    the i_m_formal_charge column. None when the block has no bond table (an SDF without bonds would
    say nothing a PDB does not)."""
    atoms, bonds = _table(ct, "m_atom"), _table(ct, "m_bond")
    if not atoms or not bonds or not bonds["rows"]:
        return None
    get, bget = _getter(atoms), _getter(bonds)
    alines, charges = [], []
    for k, row in enumerate(atoms["rows"], 1):
        try:
            x, y, z = (float(get(row, c, "0")) for c in ("r_m_x_coord", "r_m_y_coord", "r_m_z_coord"))
        except ValueError:
            return None
        el = ELEMENTS.get(int(get(row, "i_m_atomic_number", "6") or 6), "C")
        el = el[0] + el[1:].lower()
        alines.append(f"{x:10.4f}{y:10.4f}{z:10.4f} {el:<3s} 0  0  0  0  0  0  0  0  0  0  0  0")
        try:
            q = int(get(row, "i_m_formal_charge", "0") or 0)
        except ValueError:
            q = 0
        if q:
            charges.append((k, q))
    blines = []
    for row in bonds["rows"]:
        try:
            a, b, order = int(bget(row, "i_m_from")), int(bget(row, "i_m_to")), int(bget(row, "i_m_order", "1") or 1)
        except ValueError:
            return None
        blines.append(f"{a:3d}{b:3d}{min(max(order, 1), 3):3d}  0  0  0  0")
    title = (ct["props"].get("s_m_title", "") or "ligand").replace("\n", " ")[:80]
    out = [title, "  hopemd", "", f"{len(alines):3d}{len(blines):3d}  0  0  0  0  0  0  0  0999 V2000", *alines, *blines]
    for i in range(0, len(charges), 8):
        chunk = charges[i:i + 8]
        out.append("M  CHG" + f"{len(chunk):3d}" + "".join(f"{a:4d}{q:4d}" for a, q in chunk))
    out += ["M  END", "$$$$"]
    return "\n".join(out) + "\n"


def read_mae(path: str | Path) -> list[dict]:
    """[{"title": ..., "pdb": PDB text, "sdf": SDF text or None}, ...], one per structure block, in
    file order; "sdf" is set for a ligand block that carries bonds."""
    p = Path(path)
    raw = gzip.open(p, "rb").read() if p.suffix.lower() in (".maegz", ".gz") else p.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    return [{"title": ct["props"].get("s_m_title", ""), "pdb": ct_to_pdb(ct),
             "sdf": ct_to_sdf(ct) if is_ligand_ct(ct) else None} for ct in parse_mae(text)]


def is_maestro(path: str | Path) -> bool:
    return Path(path).suffix.lower() in (".mae", ".maegz") or str(path).lower().endswith(".mae.gz")
