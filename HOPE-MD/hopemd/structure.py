"""Structure files without a dependency: a small mmCIF reader that yields PDB records, and the
tables that say what a HETATM group is (water, ion, buffer, cofactor, glycan, ligand).

Everything downstream works on PDB text: PDBFixer, pdb2pqr, pdb4amber, tleap and 3Dmol all
read it, and a 4-character residue name or a 2-character chain id is not something this tool
will meet in a complex it is asked to simulate.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- residue tables
AMINO = {"ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE", "LEU", "LYS", "MET",
         "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL", "HID", "HIE", "HIP", "CYX", "CYM", "ASH", "GLH",
         "LYN", "ACE", "NME", "NHE"}
#: modified amino acids mapped to their parent, the parent's atoms kept where they are and the rest dropped: what
#: expression and crystallization put on a residue (selenomethionine for phasing; the cacodylate and mercaptoethanol
#: adducts and the oxidized states of a cysteine, whose reduced form is the parent), and modifications that leave the
#: residue's charge as it was. The force fields here have templates for the parents only; Prepare names each one.
MODIFIED_AMINO = {"MSE": "MET", "CAS": "CYS", "CME": "CYS", "CSO": "CYS", "CSD": "CYS", "CSX": "CYS", "CSS": "CYS",
                  "OCS": "CYS", "SMC": "CYS", "HYP": "PRO", "MLY": "LYS", "MLZ": "LYS", "M3L": "LYS", "HIC": "HIS",
                  "PCA": "GLU"}
#: atoms of a modified residue that are the parent's under another name (selenomethionine's SE is methionine's SD), or
#: that the parent cannot keep where they are (pyroglutamate's CD closes its ring on the N terminus; the parent's is
#: modeled again)
MODIFIED_ATOMS = {"MSE": {"SE": "SD"}, "PCA": {"CD": None}}
#: modified amino acids whose charge is not their parent's: a phosphate (-2 at pH 7.4) on a serine, threonine,
#: tyrosine or histidine, a sulfate on a tyrosine, a carboxylate on a lysine (a carbamate) or a glutamate (Gla), the
#: acetyl that takes a lysine's charge. The builds load no parameters for them, and as the parent the run would be
#: another molecule with another charge, usually not the one the structure was solved to show: they are never
#: mapped, and a run with their chain is refused, as one with a modified nucleotide is
CHARGED_MODIFIED = {"SEP": "SER", "TPO": "THR", "PTR": "TYR", "NEP": "HIS", "TYS": "TYR", "KCX": "LYS", "CGU": "GLU",
                    "ALY": "LYS"}
#: the heavy atoms of each amino acid, its side chain's beside N, CA, C, O and OXT: what a modified residue keeps when
#: it is mapped to one
AMINO_ATOMS = {r: {"N", "CA", "C", "O", "OXT", *side.split()} for r, side in {
    "ALA": "CB", "ARG": "CB CG CD NE CZ NH1 NH2", "ASN": "CB CG OD1 ND2", "ASP": "CB CG OD1 OD2", "CYS": "CB SG",
    "GLN": "CB CG CD OE1 NE2", "GLU": "CB CG CD OE1 OE2", "GLY": "", "HIS": "CB CG ND1 CD2 CE1 NE2",
    "ILE": "CB CG1 CG2 CD1", "LEU": "CB CG CD1 CD2", "LYS": "CB CG CD CE NZ", "MET": "CB CG SD CE",
    "PHE": "CB CG CD1 CD2 CE1 CE2 CZ", "PRO": "CB CG CD", "SER": "CB OG", "THR": "CB OG1 CG2",
    "TRP": "CB CG CD1 CD2 NE1 CE2 CE3 CZ2 CZ3 CH2", "TYR": "CB CG CD1 CD2 CE1 CE2 CZ OH", "VAL": "CB CG1 CG2"}.items()}
NUCLEIC = {"DA", "DT", "DG", "DC", "DU", "A", "U", "G", "C", "DA5", "DA3", "DT5", "DT3", "DG5", "DG3",
           "DC5", "DC3", "A5", "A3", "U5", "U3", "G5", "G3", "C5", "C3"}
#: the nucleotides the Amber nucleic-acid force fields have templates for (OL3 for RNA; OL21, bsc1 and OL15
#: for DNA). Any other nucleotide in a strand is a modified one, and nothing here parameterizes it.
STANDARD_NUCLEOTIDES = {"A", "C", "G", "U", "DA", "DC", "DG", "DT"}
WATER = {"HOH", "WAT", "DOD", "TIP", "TIP3", "SOL", "H2O"}
MONOVALENT_IONS = {"NA", "K", "CL", "BR", "IOD", "CS", "RB", "LI", "F"}
METALS = {"ZN", "MG", "CA", "MN", "FE", "FE2", "CU", "CU1", "NI", "CO", "CD", "HG", "MO", "W", "SR", "BA"}
# the crystallographic stuff: cryoprotectants, precipitants, buffers, detergents, reductants
BUFFERS = {"GOL", "EDO", "PEG", "PGE", "PG4", "P6G", "1PE", "2PE", "PE4", "PE5", "PE8", "PG0", "PGO",
           "SO4", "PO4", "ACT", "ACY", "DMS", "MPD", "BME", "TRS", "EPE", "MES", "BCT", "NO3", "CIT",
           "FMT", "IMD", "IPA", "EOH", "MOH", "NH4", "AZI", "SCN", "TLA", "TAR", "MLI", "SIN", "CAC",
           "BEN", "BEZ", "BU3", "PDO", "DIO", "DTT", "DTU", "TCE", "OLA", "OLC", "LDA", "BOG", "LMT",
           "SDS", "C8E", "HTG", "UNX", "UNL", "NHE", "MRD", "MBN", "PGR", "PE3", "12P", "15P"}
COFACTORS = {"HEM", "HEC", "HEA", "HEB", "HDD", "NAD", "NAI", "NAP", "NDP", "FAD", "FDA", "FMN", "FNR",
             "ATP", "ADP", "AMP", "GTP", "GDP", "GNP", "GCP", "ANP", "ACP", "AGS", "ADN", "SAM", "SAH",
             "PLP", "PMP", "TPP", "TDP", "COA", "ACO", "BTN", "CLA", "BCL", "CHL", "MTE", "SF4", "FES",
             "F3S", "PQQ", "THF", "H4B", "BH4", "MQ7", "UQ1", "PLM", "RET", "B12", "CNC", "COB", "HAS"}
GLYCANS = {"NAG", "NDG", "BMA", "MAN", "FUC", "GAL", "GLC", "BGC", "SIA", "NGA", "A2G", "XYS", "FUL",
           "GLA", "RAM", "MAG", "G6D", "BDP", "GCU", "IDR"}
#: the monatomic ions the builds have parameters for, by PDB residue name: (atom name the build
#: expects, formal charge, element). tleap's atomic_ions.lib (Amber 24) has a unit of each name
#: holding that atom, typed for the 12-6 ion sets leaprc.water.opc, .tip3p and .tip4pew all load;
#: OpenMM's amber14/tip3p.xml, which the bilayer build reads, uses the same names, and Desmond's
#: OPLS types each from its element and the formal charge in the PDB's charge column. Fe2+ is
#: FE2 with an atom FE in the PDB and FE2 in both libraries. CU1 (Cu+) is left out: TIP4P-Ew's
#: sets have no Cu+.
BUILD_IONS = {"LI": ("LI", 1, "LI"), "NA": ("NA", 1, "NA"), "K": ("K", 1, "K"), "RB": ("RB", 1, "RB"),
              "CS": ("CS", 1, "CS"), "F": ("F", -1, "F"), "CL": ("CL", -1, "CL"), "BR": ("BR", -1, "BR"),
              "IOD": ("I", -1, "I"), "MG": ("MG", 2, "MG"), "CA": ("CA", 2, "CA"), "SR": ("SR", 2, "SR"),
              "BA": ("BA", 2, "BA"), "ZN": ("ZN", 2, "ZN"), "MN": ("MN", 2, "MN"), "FE2": ("FE2", 2, "FE"),
              "CO": ("CO", 2, "CO"), "NI": ("NI", 2, "NI"), "CU": ("CU", 2, "CU"), "CD": ("CD", 2, "CD"),
              "HG": ("HG", 2, "HG"), "FE": ("FE", 3, "FE")}
#: a crystal water whose oxygen is this close to a heavy atom of the partner is near it: the choice that keeps the
#: waters near the partner (or the ligand) keeps these, the first shell and the waters bridging it to the receptor
NEAR_WATER_A = 5.0


def classify(resname: str) -> str:
    r = resname.strip().upper()
    if r in WATER:
        return "water"
    if r in AMINO or r in MODIFIED_AMINO or r in CHARGED_MODIFIED:
        return "protein"
    if r in NUCLEIC:
        return "nucleic"
    if r in MONOVALENT_IONS:
        return "ion"
    if r in METALS:
        return "metal"
    if r in BUFFERS:
        return "buffer"
    if r in COFACTORS:
        return "cofactor"
    if r in GLYCANS:
        return "glycan"
    return "ligand"


# --------------------------------------------------------------------------- mmCIF -> PDB
def is_mmcif(text: str) -> bool:
    """Whether a text is mmCIF: its first line that is neither blank nor a comment opens a data block. A model
    can open with comments before data_ (the AlphaFold Server writes its terms of use there)."""
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return s.startswith("data_")
    return False


def _cif_tokens(line: str) -> list[str]:
    # mmCIF values are whitespace separated; quotes with ' or " when they contain spaces
    try:
        return shlex.split(line, posix=True)
    except ValueError:
        return line.split()


def cif_loop(text: str, category: str) -> tuple[list[str], list[list[str]]]:
    """(columns, rows) of one looped category, e.g. "_atom_site". Empty if absent."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].strip() == "loop_":
            j = i + 1
            hdr = []
            while j < len(lines) and lines[j].startswith("_"):
                hdr.append(lines[j].strip()); j += 1
            if hdr and hdr[0].startswith(category + "."):
                cols = [h.split(".", 1)[1] for h in hdr]
                rows: list[list[str]] = []
                k = j
                buf: list[str] = []
                while k < len(lines) and not lines[k].startswith(("loop_", "#", "_")) and lines[k].strip():
                    if lines[k].startswith(";"):          # multi-line text field: take it whole
                        buf.append(lines[k][1:]); k += 1
                        while k < len(lines) and not lines[k].startswith(";"):
                            k += 1
                        k += 1; continue
                    buf.extend(_cif_tokens(lines[k]))
                    while len(buf) >= len(cols):
                        rows.append(buf[:len(cols)]); buf = buf[len(cols):]
                    k += 1
                return cols, rows
            i = j
        i += 1
    return [], []


def cif_values(text: str, item: str) -> list[str]:
    """Every value of one mmCIF item ("_exptl.method"), looped or set once."""
    category, key = item.rsplit(".", 1)
    cols, rows = cif_loop(text, category)
    if key in cols:
        return [r[cols.index(key)] for r in rows]
    m = re.search(rf"^{re.escape(item)}[ \t]+(\S.*)$", text, re.M)
    return _cif_tokens(m.group(1))[:1] if m else []


def cif_seqres(text: str, chain_letter) -> list[str]:
    """SEQRES records from _pdbx_poly_seq_scheme (or _entity_poly_seq + struct_asym), so that
    PDBFixer can find missing residues in a structure that came as mmCIF."""
    cols, rows = cif_loop(text, "_pdbx_poly_seq_scheme")
    seqs: dict[str, list[str]] = {}
    if cols:
        ix = {c: n for n, c in enumerate(cols)}
        for r in rows:
            ch = r[ix["pdb_strand_id"]] if "pdb_strand_id" in ix else r[ix.get("asym_id", 0)]
            seqs.setdefault(ch, []).append(r[ix["mon_id"]])
    if not seqs:
        return []
    out = []
    for ch, res in seqs.items():
        L = chain_letter(ch)
        for n in range(0, len(res), 13):
            chunk = res[n:n + 13]
            out.append(f"SEQRES {n // 13 + 1:3d} {L} {len(res):4d}  " + " ".join(f"{x:>3s}" for x in chunk))
    return out


def cif_to_pdb(text: str, model: int = 1, use_auth: bool = True) -> str:
    """Convert the atom_site loop of an mmCIF to PDB records (SEQRES first, then ATOM/HETATM).

    Chain ids longer than one character are mapped to single letters (the mapping is stable
    within the file and recorded in a REMARK). B-factors are kept, which is what carries pLDDT
    for an AlphaFold model.
    """
    cols, rows = cif_loop(text, "_atom_site")
    if not cols:
        raise ValueError("no _atom_site loop in this mmCIF")
    ix = {c: n for n, c in enumerate(cols)}

    def col(row, *names, default=""):
        for nm in names:
            if nm in ix:
                v = row[ix[nm]]
                if v not in (".", "?"):
                    return v
        return default

    chain_map: dict[str, str] = {}
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

    def chain_letter(cid: str) -> str:
        if cid not in chain_map:
            if len(cid) == 1 and cid not in chain_map.values():
                chain_map[cid] = cid
            else:
                for L in letters:
                    if L not in chain_map.values():
                        chain_map[cid] = L; break
        return chain_map[cid]

    out = []
    serial = 0
    for r in rows:
        try:
            mnum = int(col(r, "pdbx_PDB_model_num", default="1"))
        except ValueError:
            mnum = 1
        if mnum != model:
            continue
        group = col(r, "group_PDB", default="ATOM")
        name = col(r, "auth_atom_id", "label_atom_id") if use_auth else col(r, "label_atom_id", "auth_atom_id")
        alt = col(r, "label_alt_id", default="")
        resn = col(r, "auth_comp_id", "label_comp_id") if use_auth else col(r, "label_comp_id", "auth_comp_id")
        cid = col(r, "auth_asym_id", "label_asym_id") if use_auth else col(r, "label_asym_id", "auth_asym_id")
        seq = col(r, "auth_seq_id", "label_seq_id") if use_auth else col(r, "label_seq_id", "auth_seq_id")
        ins = col(r, "pdbx_PDB_ins_code", default="")
        try:
            x, y, z = (float(col(r, "Cartn_x")), float(col(r, "Cartn_y")), float(col(r, "Cartn_z")))
            occ = float(col(r, "occupancy", default="1.0")); b = float(col(r, "B_iso_or_equiv", default="0.0"))
            resi = int(float(seq))
        except ValueError:
            continue
        elem = col(r, "type_symbol", default=name[:1]).upper()
        serial += 1
        nm = name if len(name) == 4 else " " + name.ljust(3)
        out.append(f"{group:<6s}{min(serial, 99999):5d} {nm:4s}{alt[:1] or ' '}{resn[:3]:>3s} {chain_letter(cid)}"
                   f"{resi:4d}{ins[:1] or ' '}   {x:8.3f}{y:8.3f}{z:8.3f}{occ:6.2f}{b:6.2f}          {elem:>2s}")
    remark = ["REMARK 900 HOPEMD chain map " + " ".join(f"{k}={v}" for k, v in chain_map.items() if k != v)] \
        if any(k != v for k, v in chain_map.items()) else []
    seqres = cif_seqres(text, chain_letter)
    return "\n".join(remark + seqres + out) + "\nEND\n"


def read_structure(path: str, model: int = 1) -> str:
    """PDB text from a .pdb or .cif file (model N for multi-model files)."""
    from pathlib import Path
    p = Path(path)
    text = p.read_text(errors="replace")
    if p.suffix.lower() in (".cif", ".mmcif") or is_mmcif(text):
        return cif_to_pdb(text, model=model)
    if "\nMODEL" in text or text.startswith("MODEL"):
        keep, inside, n = [], False, 0
        for line in text.splitlines():
            if line.startswith("MODEL"):
                n += 1; inside = (n == model); continue
            if line.startswith("ENDMDL"):
                inside = False; continue
            if inside or n == 0:
                keep.append(line)
        return "\n".join(keep) + "\nEND\n"
    return text


# --------------------------------------------------------------------------- inventory
@dataclass
class Group:
    resname: str
    kind: str
    count: int = 0
    chains: set = field(default_factory=set)


@dataclass
class Inventory:
    chains: list[dict]              # id, kind, residues, first, last, gaps [[after, before], ...]
    hetero: list[dict]              # resname, class, count, chains
    waters: int
    altlocs: int
    models: int
    plddt: dict | None              # per-chain mean, low-tail counts and why, if B is read as pLDDT

    def polymer_chains(self) -> list[str]:
        return [c["id"] for c in self.chains if c["kind"] in ("protein", "nucleic")]


# --------------------------------------------------------------------------- B-factor or pLDDT
#: the names AlphaFold gives its files: AlphaFold DB (AF-P69905-F1-model_v4), AlphaFold 2 (ranked_0.pdb,
#: relaxed_model_1_pred_0.pdb), ColabFold (<job>_unrelaxed_rank_001_alphafold2_ptm_model_1_seed_000.pdb)
#: and a numbered model (fold_<job>_model_0.cif, one of the names read_alphafold looks for)
ALPHAFOLD_NAME = re.compile(r"AF-[0-9A-Z]+-F\d+-model_v\d+|(?:^|_)ranked_\d+\.|(?:^|_)(?:un)?relaxed_(?:rank|model)_\d"
                            r"|alphafold2|_model_\d+\.cif$", re.I)


def experimental_method(text: str) -> str:
    """The method a PDB or mmCIF file says its coordinates come from (X-RAY DIFFRACTION, SOLUTION
    NMR, ELECTRON MICROSCOPY ...): a PDB file's EXPDTA record, an mmCIF's _exptl.method. "" for a
    file that names none, and for a theoretical or predicted model."""
    found = []
    for line in text.splitlines():
        if line.startswith(("ATOM", "HETATM")):
            break
        if line.startswith("EXPDTA"):
            found.append(line[10:79].strip())
    found += cif_values(text, "_exptl.method") if "_exptl" in text else []
    return ", ".join(m for m in found if m and not re.search(r"THEORETICAL|PREDICT", m, re.I))


def modelcif_plddt(text: str) -> bool:
    """A ModelCIF file (AlphaFold DB, AlphaFold 3) that declares a local
    pLDDT metric, the one its B_iso_or_equiv column carries."""
    if "_ma_qa_metric" not in text:
        return False
    names, modes = cif_values(text, "_ma_qa_metric.name"), cif_values(text, "_ma_qa_metric.mode")
    return any(n.lower() == "plddt" and m.lower() == "local" for n, m in zip(names, modes))


def _residues_of_one_b(pdb_text: str) -> tuple[int, int]:
    """(polymer residues whose atoms all carry one B-factor, polymer residues of two atoms or more)."""
    bs: dict[tuple, list[float]] = {}
    for line in pdb_text.splitlines():
        if line.startswith("ATOM") and classify(line[17:20]) in ("protein", "nucleic"):
            try:
                bs.setdefault((line[21], line[22:27], line[17:20]), []).append(float(line[60:66]))
            except ValueError:
                pass
    multi = [v for v in bs.values() if len(v) > 1]
    return sum(1 for v in multi if max(v) - min(v) < 0.005), len(multi)


def b_is_plddt(pdb_text: str, kind: str = "", original: str = "", name: str = "") -> tuple[bool, str]:
    """Whether the B-factor column holds AlphaFold's pLDDT, and why. The why is "" when the column does
    not look like pLDDT at all (every Cα under 100, a mean over 30, some over 90): an X-ray structure
    with high B-factors passes that test too, so passing it decides nothing. What decides, in order:
    the source (an RCSB entry is an experiment, an AlphaFold import a prediction), what the
    file says of itself (_exptl.method or EXPDTA; ModelCIF's pLDDT metric, AlphaFold DB's title), the
    names AlphaFold and ColabFold give their files, and for a file that says nothing, pLDDT's own
    signature: AlphaFold 2 and ColabFold write one value per residue to every atom of it, where
    refined B-factors differ from atom to atom. AlphaFold 3 writes a value per atom, and is known by
    its source or its ModelCIF metric instead. `original` is the file as it came (an mmCIF keeps its
    categories there; pdb_text is what it was converted to)."""
    ca = []
    for line in pdb_text.splitlines():
        if line.startswith(("ATOM", "HETATM")) and line[12:16].strip() == "CA" and classify(line[17:20]) in ("protein", "nucleic"):
            try:
                ca.append(float(line[60:66]))
            except ValueError:
                pass
    if not ca or max(ca) > 100.0 or sum(ca) / len(ca) <= 30 or not any(b > 90 for b in ca):
        return False, ""
    if kind == "rcsb":
        return False, "the structure is an RCSB entry"
    if kind == "alphafold":
        return True, "the structure is an AlphaFold model"
    head = original or pdb_text
    method = experimental_method(head)
    if method:
        return False, f"the file declares {method}"
    if modelcif_plddt(head):
        return True, "the file is ModelCIF and declares a local pLDDT metric"
    if any(line.startswith("TITLE") and "ALPHAFOLD" in line.upper() for line in head.splitlines()[:40]):
        return True, "the file is an AlphaFold DB model"
    if name and ALPHAFOLD_NAME.search(name):
        return True, f"the file is named as an AlphaFold model ({name})"
    one, multi = _residues_of_one_b(pdb_text)
    if multi < 3:
        return False, "the file names no source and has too few residues of several atoms to show pLDDT's signature"
    if one >= 0.95 * multi:
        return True, f"every residue's atoms share one B-factor ({one} of {multi} residues), as AlphaFold writes pLDDT"
    return False, f"the values differ between the atoms of {multi - one} of {multi} residues, as refined B-factors do"


def inventory(pdb_text: str, plddt: tuple[bool, str] | None = None) -> Inventory:
    """What the structure holds. `plddt` is the caller's reading of the B column (b_is_plddt with what
    it knows of the source); without one, the column is read by its own signature."""
    chains: dict[str, dict] = {}
    groups: dict[str, Group] = {}
    waters = altlocs = 0
    models = pdb_text.count("\nMODEL") or 1
    seen = set()
    ca_b: dict[str, list[float]] = {}
    for line in pdb_text.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        resn = line[17:20].strip(); ch = line[21].strip() or "A"
        try:
            resi = int(line[22:26])
        except ValueError:
            continue
        alt = line[16].strip()
        if alt and alt != "A":
            altlocs += 1
        kind = classify(resn)
        if kind == "water":
            if line[12:16].strip() in ("O", "OW", "OH2"):
                waters += 1
            continue
        if kind in ("protein", "nucleic"):
            c = chains.setdefault(ch, {"id": ch, "kind": kind, "res": [], "ids": set(), "first": resi, "last": resi, "kinds": {}})
            c["kinds"][kind] = c["kinds"].get(kind, 0) + 1
            c["ids"].add((resi, line[26:27].strip()))     # 60, 60A ... 60I are ten residues: the count is of these
            if (ch, resi) not in seen:
                seen.add((ch, resi)); c["res"].append(resi)
                c["first"] = min(c["first"], resi); c["last"] = max(c["last"], resi)
            if line[12:16].strip() == "CA":
                try:
                    ca_b.setdefault(ch, []).append(float(line[60:66]))
                except ValueError:
                    pass
            continue
        g = groups.setdefault(resn, Group(resn, kind))
        if (ch, resi, resn) not in seen:
            seen.add((ch, resi, resn)); g.count += 1; g.chains.add(ch)
    # a modified nucleotide sits in its strand as a HETATM group: it is listed with the chain it belongs to
    modified: dict[str, list[str]] = {}
    for m in modified_nucleotides(pdb_text):
        modified.setdefault(m["chain"], []).append(f"{m['resname']} {m['resi']}")
        if m["resname"] in groups:
            groups[m["resname"]].kind = "modified nucleotide"
    # so does a modified amino acid whose charge is not its parent's: it stays in its chain, never mapped
    charged: dict[str, list[str]] = {}
    for m in modified_residues(pdb_text):
        if not m["mapped"]:
            charged.setdefault(m["chain"], []).append(f"{m['resname']} {m['resi']}")
    out_chains = []
    for c in chains.values():
        res = sorted(set(c["res"]))
        gaps = [[a, b] for a, b in zip(res, res[1:]) if b - a > 1]
        out_chains.append({"id": c["id"], "kind": max(c["kinds"], key=c["kinds"].get), "residues": len(c["ids"]),
                           "first": res[0], "last": res[-1], "gaps": gaps,
                           "missing": sum(b - a - 1 for a, b in gaps),
                           **({"modified": modified[c["id"]]} if c["id"] in modified else {}),
                           **({"charged_modified": charged[c["id"]]} if c["id"] in charged else {})})
    # the copies of a cation a nucleic acid binds, which Clean keeps whatever the buffer setting (spec.clean_keeps
    # reads this to say beforehand what Prepare will keep)
    bound: dict[str, int] = {}
    if any("nucleic" in c["kinds"] for c in chains.values()):
        for _, _, name in nucleic_cations(pdb_text):
            bound[name] = bound.get(name, 0) + 1
    hetero = [{"resname": g.resname, "class": g.kind, "count": g.count, "chains": sorted(g.chains),
               **({"nucleic_bound": bound[g.resname]} if g.resname in bound else {})}
              for g in sorted(groups.values(), key=lambda g: (g.kind, g.resname))]
    is_plddt, basis = plddt if plddt is not None else b_is_plddt(pdb_text)
    allb = [b for v in ca_b.values() for b in v]
    found = None
    if is_plddt and allb:
        found = {"mean": round(sum(allb) / len(allb), 1),
                 "per_chain": {ch: round(sum(v) / len(v), 1) for ch, v in ca_b.items()},
                 "below_50": {ch: sum(1 for b in v if b < 50) for ch, v in ca_b.items()},
                 "basis": basis}
    return Inventory(out_chains, hetero, waters, altlocs, models, found)


def modified_residues(pdb_text: str) -> list[dict]:
    """The modified amino acids of a structure in file order: {"chain", "resname", "resi", "parent", "mapped"}, mapped
    False for one whose charge is not its parent's (CHARGED_MODIFIED)."""
    out, seen = [], set()
    for line in pdb_text.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        resn = line[17:20].strip().upper()
        if resn not in MODIFIED_AMINO and resn not in CHARGED_MODIFIED:
            continue
        key = (line[21], line[22:27], resn)
        if key not in seen:
            seen.add(key)
            out.append({"chain": line[21].strip() or "A", "resname": resn, "resi": line[22:27].strip(),
                        "parent": MODIFIED_AMINO.get(resn) or CHARGED_MODIFIED[resn], "mapped": resn in MODIFIED_AMINO})
    return out


def map_modified(pdb_text: str) -> tuple[str, list[dict]]:
    """Each modified amino acid of MODIFIED_AMINO as its parent: renamed, written as ATOM, with the parent's heavy atoms
    where they were (under the parent's names, MODIFIED_ATOMS) and nothing else; the protonation adds the parent's
    hydrogens. SEQRES names the parent too, or PDBFixer could not align the chain to its sequence and would find
    none of the loops it is missing. Returns the text and the residues mapped, as modified_residues lists them."""
    out = []
    for line in pdb_text.splitlines():
        if line.startswith("SEQRES"):
            line = line[:19] + " ".join(f"{MODIFIED_AMINO.get(n, n):>3s}" for n in line[19:].split())
        resn = line[17:20].strip().upper() if line.startswith(("ATOM", "HETATM")) else ""
        if resn not in MODIFIED_AMINO:
            out.append(line)
            continue
        parent, name = MODIFIED_AMINO[resn], line[12:16].strip()
        name = MODIFIED_ATOMS.get(resn, {}).get(name, name)
        if name not in AMINO_ATOMS[parent]:
            continue                # the modification's own atoms and the hydrogens; a None from MODIFIED_ATOMS too
        line = line.ljust(80)       # every heavy atom of an amino acid is named after its element
        out.append(("ATOM  " + line[6:12] + f" {name:<3s}" + line[16] + f"{parent:>3s}" + line[20:76] + f"{name[0]:>2s}"
                    + line[78:]).rstrip())
    return "\n".join(out) + "\n", [m for m in modified_residues(pdb_text) if m["mapped"]]


def mapped_note(mapped: list[dict]) -> str:
    """The modified residues mapped, by name and parent: "CAS A:65, A:130 to CYS; MSE A:1 to MET"."""
    by: dict[tuple[str, str], list[str]] = {}
    for m in mapped:
        by.setdefault((m["resname"], m["parent"]), []).append(f"{m['chain']}:{m['resi']}")
    return "; ".join(f"{name} {', '.join(where)} to {parent}" for (name, parent), where in by.items())


def filter_pdb(pdb_text: str, keep_chains: set[str] | None, drop_classes: set[str], altloc: str = "A",
               keep_resnames: set[str] = frozenset(), drop_resnames: set[str] = frozenset(),
               keep_groups: set[tuple[str, str, str]] = frozenset()) -> tuple[str, dict]:
    """Apply the keep/drop policy. Returns (text, removed counts by class): residues, each counted once
    however many atoms it has, and atoms for the chains left out and the alternate locations. keep_groups
    names single hetero groups, (chain, residue number, name), that stay whatever their class says."""
    out, removed, counted = [], {}, set()
    for line in pdb_text.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            # header records PDBFixer reads stay; TER is regenerated from the atoms that remain
            if line.startswith(("SEQRES", "REMARK 465", "SSBOND", "MODRES", "HEADER", "TITLE", "CRYST1")):
                out.append(line)
            continue
        resn = line[17:20].strip(); ch = line[21].strip() or "A"
        alt = line[16].strip()
        kind = classify(resn)
        drop = False
        if alt and alt != altloc:
            drop = True; kind = "altloc"
        elif resn in keep_resnames or (ch, line[22:27].strip(), resn) in keep_groups:
            drop = False
        elif resn in drop_resnames or kind in drop_classes:
            drop = True
        elif kind in ("protein", "nucleic") and keep_chains and ch not in keep_chains:
            drop = True; kind = "other_chain"
        if drop:
            if kind in ("other_chain", "altloc"):
                removed[kind] = removed.get(kind, 0) + 1
            elif (kind, ch, line[22:27], resn) not in counted:
                counted.add((kind, ch, line[22:27], resn))
                removed[kind] = removed.get(kind, 0) + 1
            continue
        if alt:
            line = line[:16] + " " + line[17:]
        out.append(line)
    return ter_between_chains("\n".join(out) + ("\n" if out else "")), removed


def ter_between_chains(pdb_text: str) -> str:
    """A TER wherever the chain id changes between coordinate records, and one at the end,
    with hetero groups (which have no chain continuity) each followed by TER as well."""
    out = []
    prev_chain = None; prev_kind = None
    for line in pdb_text.splitlines():
        if line.startswith("TER") or line.startswith("END"):
            continue
        if line.startswith(("ATOM", "HETATM")):
            ch = line[21]; kind = classify(line[17:20])
            polymer = kind in ("protein", "nucleic")
            if prev_chain is not None and (ch != prev_chain or polymer != prev_kind):
                out.append("TER")
            prev_chain, prev_kind = ch, polymer
        out.append(line)
    if prev_chain is not None:
        out.append("TER")
    out.append("END")
    return "\n".join(out) + "\n"


def group_by_residue(pdb_text: str) -> str:
    """Atoms of one residue made contiguous, residues kept in the order they first appear.
    A docked pose that lists every backbone atom first and the side chains after has
    each residue appear twice in sequence and tleap reads a 7-mer as fourteen residues.
    Other records keep their place relative to the block."""
    lines = pdb_text.splitlines()
    coords = [(i, l) for i, l in enumerate(lines) if l.startswith(("ATOM", "HETATM"))]
    if not coords:
        return pdb_text
    order: list[tuple] = []
    by_res: dict[tuple, list[str]] = {}
    for _, l in coords:
        key = (l[21], l[22:27], l[17:20])
        if key not in by_res:
            by_res[key] = []; order.append(key)
        by_res[key].append(l)
    if all(len(v) == 1 for v in by_res.values()) or _residues_contiguous(coords):
        return pdb_text
    first, last = coords[0][0], coords[-1][0]
    head = lines[:first]
    tail = [l for l in lines[last + 1:]]
    body = [l for key in order for l in by_res[key]]
    # TER records between the first and last atom are dropped: the grouping may have moved
    # atoms across them, and ter_between_chains / pdb4amber put them back where they belong
    return "\n".join(head + body + tail) + "\n"


def _residues_contiguous(coords: list[tuple[int, str]]) -> bool:
    seen: set[tuple] = set(); prev = None
    for _, l in coords:
        key = (l[21], l[22:27], l[17:20])
        if key != prev:
            if key in seen:
                return False
            seen.add(key); prev = key
    return True


def split_chains(pdb_text: str, first: list[str], second: list[str]) -> tuple[str, str]:
    """Two PDB texts: the receptor chains, then the partner chains, TER between chains and at
    the geometric breaks the input carried (its TER records are kept)."""
    def take(chs):
        L, keep = [], False
        for l in pdb_text.splitlines():
            if l.startswith(("ATOM", "HETATM")):
                keep = (l[21].strip() or "A") in chs
                if keep:
                    L.append(l)
            elif l.startswith("TER") and keep and L and not L[-1].startswith("TER"):
                L.append("TER")
        if not L:
            return ""
        if not L[-1].startswith("TER"):
            L.append("TER")
        return "\n".join(L) + "\nEND\n"
    return take(set(first)), take(set(second))


def hetero_groups(pdb_text: str) -> list[dict]:
    """The residues that are neither polymer nor water (ions, metals, cofactors, buffers, ligands,
    glycans), in file order: {resname, chain, resid, class, lines, ion}. `ion` is the build's
    (atom name, charge, element) for a monatomic ion the builds have parameters for (BUILD_IONS),
    None for anything else."""
    groups: dict[tuple, dict] = {}
    for line in pdb_text.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        kind = classify(line[17:20])
        if kind in ("protein", "nucleic", "water"):
            continue
        key = (line[21], line[22:27], line[17:20].strip())
        g = groups.setdefault(key, {"resname": key[2], "chain": key[0].strip() or "A", "resid": key[1].strip(),
                                    "class": kind, "lines": []})
        g["lines"].append(line)
    for g in groups.values():
        spec = BUILD_IONS.get(g["resname"])
        el = (g["lines"][0][76:78].strip() or g["lines"][0][12:14].strip()).upper()
        g["ion"] = spec if spec and len(g["lines"]) == 1 and el in ("", spec[2]) else None
    return list(groups.values())


def group_label(g: dict) -> str:
    """How the report names a group: "ZN A:301"."""
    return f"{g['resname']} {g['chain']}:{g['resid']}"


def water_groups(pdb_text: str) -> list[dict]:
    """The waters of a structure in file order, each with an oxygen: {resname, chain, resid, lines, o}, o the
    oxygen's coordinates."""
    groups: dict[tuple, dict] = {}
    for line in pdb_text.splitlines():
        if not line.startswith(("ATOM", "HETATM")) or classify(line[17:20]) != "water":
            continue
        key = (line[21], line[22:27], line[17:20].strip())
        g = groups.setdefault(key, {"resname": key[2], "chain": key[0].strip() or "A", "resid": key[1].strip(), "lines": [], "o": None})
        g["lines"].append(line)
        if line[12:16].strip() in ("O", "OW", "OH2"):
            try:
                g["o"] = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
            except ValueError:
                pass
    return [g for g in groups.values() if g["o"]]


def heavy_xyz(pdb_text: str, chains: set[str] | None = None, kinds: tuple[str, ...] = ("protein", "nucleic")) -> list[tuple[float, float, float]]:
    """The coordinates of the heavy atoms of a structure's residues of these kinds (protein, nucleic: a nucleotide
    under any of its names; other: anything else but water and ions), of the chains named when they are."""
    out = []
    for line in pdb_text.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        k = classify(line[17:20])
        kind = k if k in ("protein", "water") else "nucleic" if k == "nucleic" or plain_nucleotide(line[17:20]) else \
            "ion" if k in ("ion", "metal") else "other"
        if kind not in kinds:
            continue
        if chains is not None and (line[21].strip() or "A") not in chains:
            continue
        el = line[76:78].strip().upper() or line[12:16].strip().lstrip("0123456789")[:1].upper()
        if el == "H":
            continue
        try:
            out.append((float(line[30:38]), float(line[38:46]), float(line[46:54])))
        except ValueError:
            pass
    return out


def near_waters(waters: list[dict], xyz: list, within_A: float = NEAR_WATER_A) -> list[dict]:
    """The waters whose oxygen lies within within_A of any of the points (a partner's heavy atoms)."""
    if not xyz:
        return []
    cell = within_A
    grid: dict[tuple, list] = {}
    for p in xyz:
        grid.setdefault((int(p[0] // cell), int(p[1] // cell), int(p[2] // cell)), []).append(p)
    out = []
    for w in waters:
        o = w["o"]
        c = (int(o[0] // cell), int(o[1] // cell), int(o[2] // cell))
        if any(_dist(o, p) <= within_A for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)
               for p in grid.get((c[0] + dx, c[1] + dy, c[2] + dz), ())):
            out.append(w)
    return out


def waters_pdb(groups: list[dict]) -> str:
    """Crystal waters as the builds read them: HOH with its oxygen and two hydrogens, each followed by TER. A water
    that came with its two hydrogens keeps them; one without gets them at the water's own geometry (0.9572 Å,
    104.52°), all pointing one way: tleap names the residue after the run's water model and adds a four-site
    water's extra point, Desmond's System Builder takes a water only with its hydrogens, and the minimization
    turns them."""
    out = []
    n = 0
    for g in groups:
        o = g["o"]
        hs = [l for l in g["lines"] if (l[76:78].strip().upper() or l[12:16].strip().lstrip("0123456789")[:1].upper()) == "H"]
        try:
            h = [(float(l[30:38]), float(l[38:46]), float(l[46:54])) for l in hs] if len(hs) == 2 else []
        except ValueError:
            h = []
        if not h:
            h = [(o[0] + 0.5859, o[1] + 0.7570, o[2]), (o[0] + 0.5859, o[1] - 0.7570, o[2])]
        chain, resid = g["lines"][0][21], g["lines"][0][22:27]
        for name, p, el in ((" O  ", o, "O"), (" H1 ", h[0], "H"), (" H2 ", h[1], "H")):
            n += 1
            out.append(f"HETATM{n:5d} {name} HOH {chain}{resid}   {p[0]:8.3f}{p[1]:8.3f}{p[2]:8.3f}  1.00  0.00           {el}")
        out.append("TER")
    return "\n".join(out) + "\nEND\n"


def ions_pdb(groups: list[dict]) -> str:
    """Monatomic ions as the builds read them: one HETATM each, with the atom name the build expects,
    the formal charge in the charge column (Maestro's reader takes the ion's charge from it) and the
    coordinates it came with, each followed by TER."""
    out = []
    for n, g in enumerate(groups, start=1):
        atom, q, el = g["ion"]
        line = g["lines"][0]
        name = f"{atom:<4s}" if len(el) == 2 else f" {atom:<3s}"
        out += [f"HETATM{n:5d} {name} {g['resname']:>3s} {line[21]}{line[22:27]}   {line[30:54]}  1.00  0.00"
                f"          {el:>2s}{abs(q)}{'+' if q > 0 else '-'}", "TER"]
    return "\n".join(out) + "\nEND\n"


def default_split(chains: list[dict]) -> tuple[list[str], list[str], str]:
    """The receptor chains, the partner chains and the partner's class a complex starts with. A protein
    with DNA or RNA bound: the protein chains against every nucleic-acid chain, so both strands of a duplex
    stay together. A nucleic acid alone (a duplex, a quadruplex, an RNA): every strand, and no partner.
    Otherwise the largest chain against the rest."""
    protein = [c["id"] for c in chains if c["kind"] == "protein"]
    nucleic = [c["id"] for c in chains if c["kind"] == "nucleic"]
    if protein and nucleic:
        return protein, nucleic, "nucleic_acid"
    if nucleic:
        return nucleic, [], "none"
    by_size = sorted(chains, key=lambda c: -c["residues"])
    receptor = [by_size[0]["id"]] if by_size else []
    partner = [c["id"] for c in chains if c["kind"] in ("protein", "nucleic") and c["id"] not in receptor]
    return receptor, partner, "protein" if partner else "none"


# --------------------------------------------------------------------------- strands and breaks
#: pdb2pqr --ffout=AMBER names nucleotides as Amber did before RNA.OL3 (RA, RC, RG, RU) and marks the ends of a
#: strand with a 5 or 3 (RU5, DG3). OpenMM has no template under the RNA names, and tleap maps them only where
#: it finds a strand ending, so both get the names OL3 and OL21 use, and pick the end variant themselves.
NUCLEOTIDE_NAME = re.compile(r"R?([ACGU])[35N]?|D([ACGT])[35N]?")
#: the atom names of the same era, renamed as RNA.OL3's and DNA.OL21's leaprc map them (addPdbAtomMap), and the
#: sugar atoms of files older still (C4* for C4')
NUCLEIC_ATOM_NAMES = {"O1P": "OP1", "O2P": "OP2", "O3P": "OP3", "H5'1": "H5'", "H5'2": "H5''", "H2'1": "H2'",
                      "H2'2": "H2''", "HO'2": "HO2'", "H5T": "HO5'", "H3T": "HO3'", "C5M": "C7",
                      **{f"{a}*": f"{a}'" for a in ("C1", "C2", "C3", "C4", "C5", "O2", "O3", "O4", "O5")}}
#: the phosphate of a 5′-terminal nucleotide: the force fields' 5′ residues start at O5′ and have no template for it
PHOSPHATE_5P = {"P", "OP1", "OP2", "OP3", "O1P", "O2P", "O3P"}
#: a bond is never longer than this, in Å: a peptide C–N is 1.33 Å and a nucleic acid's O3′–P 1.6 Å; a pair
#: further apart is a gap in the chain, or a bond the deposited model stretched
BREAK_CN_A = 2.0
BREAK_OP_A = 2.5
#: consecutive residues of a chain further apart than this (C–N, O3′–P), in Å, are a gap whatever their numbers say: a
#: renumbered file hides the residues it lost (GLY 179 and LYS 180 of a docking receptor sit 15.8 Å apart), and
#: closing a link this long would pull the residues either side out of place. Closer, with no number missing between
#: them, they are a bond the deposited model stretched (1BXW's 64-65 at 3.1 Å): it stays a bond, the minimization
#: closes it, and the report names it. A single residue lost from a renumbered file can leave its flanks 2.6 to 4.8 Å
#: apart (C–N, over the populated regions of phi and psi), so that list is where to look for one
GAP_A = 4.0
CATIONS = (MONOVALENT_IONS - {"CL", "BR", "IOD", "F"}) | METALS
#: an ion this close to a nucleic-acid heavy atom is bound to it, not bulk salt: K+ sits 2.4-3.0 Å from the O6 of
#: a G-quadruplex channel, Mg2+ 1.9-2.5 Å from the phosphate oxygens it coordinates
BOUND_ION_A = 3.5


def plain_nucleotide(name: str) -> str | None:
    """A, C, G, U, DA, DC, DG or DT for a standard nucleotide under any of its names (RA, RU5, DG3, A5 ...);
    None for anything else, a modified nucleotide included."""
    m = NUCLEOTIDE_NAME.fullmatch(name.strip().upper())
    if not m:
        return None
    return m.group(1) if m.group(1) else "D" + m.group(2)


def _residues(lines: list[str]) -> list[dict]:
    """The residues of PDB lines in file order: chain, number, name, atoms {name: xyz}, the indices of their
    lines, and whether a TER (or the start of the file) comes before them."""
    out: list[dict] = []
    cur, ter = None, True
    for i, l in enumerate(lines):
        if l.startswith(("TER", "END")):
            ter, cur = True, None
            continue
        if not l.startswith(("ATOM", "HETATM")):
            continue
        key = (l[21], l[22:27], l[17:20])
        if cur is None or cur["key"] != key:
            cur = {"key": key, "chain": l[21].strip() or "A", "resi": l[22:27].strip(), "name": l[17:20].strip(),
                   "atoms": {}, "lines": [], "after_ter": ter}
            out.append(cur)
            ter = False
        cur["lines"].append(i)
        try:     # an older file's C4* is C4'
            cur["atoms"][l[12:16].strip().replace("*", "'")] = (float(l[30:38]), float(l[38:46]), float(l[46:54]))
        except ValueError:
            pass
    return out


def _kind(res: dict) -> str:
    k = classify(res["name"])
    if k == "protein":
        return "protein"
    if k == "nucleic" or plain_nucleotide(res["name"]):
        return "nucleic"
    return "other"


def _dist(a, b) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def modified_nucleotides(pdb_text: str) -> list[dict]:
    """The nucleotides of a strand the force fields here have no template for: a residue with a sugar (C1′
    and C4′) bonded through O3′–P to the nucleotide before or after it, under any name but A, C, G, U, DA, DC,
    DG, DT (5-methylcytosine, pseudouridine, inosine, deoxyuridine ...). A free nucleotide such as an ATP
    cofactor is bonded to none and is not listed. [{"chain", "resname", "resi"}] in file order."""
    res = [r for r in _residues(pdb_text.splitlines()) if "C1'" in r["atoms"] and "C4'" in r["atoms"]]
    linked: set[int] = set()
    for i, (a, b) in enumerate(zip(res, res[1:])):
        if a["chain"] == b["chain"] and "O3'" in a["atoms"] and "P" in b["atoms"] \
                and _dist(a["atoms"]["O3'"], b["atoms"]["P"]) <= BREAK_OP_A:
            linked |= {i, i + 1}
    return [{"chain": r["chain"], "resname": r["name"], "resi": r["resi"]} for i, r in enumerate(res)
            if i in linked and plain_nucleotide(r["name"]) not in STANDARD_NUCLEOTIDES]


def nucleic_cations(pdb_text: str, within_A: float = BOUND_ION_A, chains: set[str] | None = None) -> list[tuple[str, str, str]]:
    """The cations bound to a nucleic acid (an ion or a metal with a nucleic-acid heavy atom within
    within_A; only the strands of `chains` count when it is given), as (chain, residue number, name) in
    file order."""
    res = _residues(pdb_text.splitlines())
    nucleic = [xyz for r in res if _kind(r) == "nucleic" and (chains is None or r["chain"] in chains)
               for name, xyz in r["atoms"].items() if not name.startswith(("H", "1H", "2H", "3H"))]
    out = []
    for r in res:
        if r["name"].upper() in CATIONS and any(_dist(xyz, n) <= within_A for xyz in r["atoms"].values() for n in nucleic):
            out.append((r["chain"], r["resi"], r["name"]))
    return out


def normalise_nucleic_names(pdb_text: str) -> tuple[str, int]:
    """pdb2pqr's names for nucleotides back to the ones OL3 and OL21 use: RA, RU5, DG3 ... become A, U, DG, the
    old phosphate and hydrogen names become OP1, OP2, H5′′, HO2′ ... Returns the text and the residues
    renamed."""
    lines = pdb_text.splitlines()
    renamed = 0
    for r in _residues(lines):
        plain = plain_nucleotide(r["name"])
        if not plain:
            continue
        renamed += plain != r["name"]
        for i in r["lines"]:
            l = lines[i].ljust(27)
            atom = l[12:16].strip()
            if atom in NUCLEIC_ATOM_NAMES:
                new = NUCLEIC_ATOM_NAMES[atom]
                l = l[:12] + (new if len(new) == 4 else " " + new.ljust(3)) + l[16:]
            lines[i] = (l[:17] + f"{plain:>3s}" + l[20:]).rstrip()
    return "\n".join(lines) + "\n", renamed


def _skips(a: str, b: str) -> bool:
    """Whether residue id b leaves an id out after a: a number (52 to 54), or an insertion code of the same number
    (60A to 60C, 60 to 60B). 52 to 52A or 53, 60I to 61, and insertion codes that run backwards (IMGT's 112B, 112A,
    112) leave none out."""
    na, nb = re.match(r"-?\d+", a), re.match(r"-?\d+", b)
    if not (na and nb):
        return False
    if int(nb.group()) - int(na.group()) > 1:
        return True
    ca, cb = a[na.end():].strip()[:1].upper(), b[nb.end():].strip()[:1].upper()
    return int(nb.group()) == int(na.group()) and cb.isalpha() and (not ca or ca.isalpha()) \
        and ord(cb) - (ord(ca) if ca else ord("A") - 1) > 1


def _long_link(a: dict, b: dict, kind: str, max_cn_A: float, max_op_A: float, gap_A: float) -> tuple[str, float, bool] | None:
    """How two consecutive residues of one chain and kind are linked when the link is longer than a bond: (C–N or
    O3′–P, its length, whether it is a gap). A gap is a link over gap_A, or one across a missing residue id (52 to 54,
    60A to 60C; 52 to 52A or 53 misses none). None for a bond, or when a linking atom is missing."""
    link = {"protein": ("C", "N", max_cn_A, "C–N"), "nucleic": ("O3'", "P", max_op_A, "O3′–P")}.get(kind)
    if not link or link[0] not in a["atoms"] or link[1] not in b["atoms"]:
        return None
    d = _dist(a["atoms"][link[0]], b["atoms"][link[1]])
    if d <= link[2]:
        return None
    return link[3], d, d > gap_A or _skips(a["resi"], b["resi"])


def ter_at_breaks(pdb_text: str, max_cn_A: float = BREAK_CN_A, max_op_A: float = BREAK_OP_A,
                  gap_A: float = GAP_A) -> tuple[str, list[str]]:
    """Insert TER where residues are missing between consecutive residues of a chain: their link (a protein's C..N
    over max_cn_A, a nucleic acid's O3′..P over max_op_A) spans a missing residue id (a number, or an insertion code
    of the same number), or is over gap_A whatever the ids, so that tleap treats a gap as two chains with their own
    termini instead of bonding across it; and wherever the chain id changes, or a strand meets a residue of another
    kind, without one. A link longer than a bond but not a gap stays a bond (stretched_bonds names it); a pair whose
    linking atom is missing is left joined, a TER already there is kept. Returns the text and the breaks, as
    "chain:before-after"."""
    lines = pdb_text.splitlines()
    ter_before: set[int] = set()
    breaks = []
    res = _residues(lines)
    for a, b in zip(res, res[1:]):
        if b["after_ter"]:
            continue
        ka, kb = _kind(a), _kind(b)
        if a["chain"] != b["chain"] or ka != kb:
            if ka != "other" or kb != "other":
                ter_before.add(b["lines"][0])
            continue
        link = _long_link(a, b, ka, max_cn_A, max_op_A, gap_A)
        if link and link[2]:
            ter_before.add(b["lines"][0])
            breaks.append(f"{a['chain']}:{a['resi']}-{b['resi']}")
    out = []
    for i, l in enumerate(lines):
        if i in ter_before:
            out.append("TER")
        out.append(l)
    return "\n".join(out) + "\n", breaks


def stretched_bonds(pdb_text: str, max_cn_A: float = BREAK_CN_A, max_op_A: float = BREAK_OP_A,
                    gap_A: float = GAP_A) -> list[str]:
    """The links ter_at_breaks leaves bonded though they are longer than a bond: consecutive residues of one chain
    with no TER and no residue id missing between them, their C–N over max_cn_A (O3′–P over max_op_A) and at most
    gap_A. Each as "A:64-65 (C–N 3.14 Å)"."""
    out = []
    res = _residues(pdb_text.splitlines())
    for a, b in zip(res, res[1:]):
        if b["after_ter"] or a["chain"] != b["chain"] or _kind(a) != _kind(b):
            continue
        link = _long_link(a, b, _kind(a), max_cn_A, max_op_A, gap_A)
        if link and not link[2]:
            out.append(f"{a['chain']}:{a['resi']}-{b['resi']} ({link[0]} {link[1]:.2f} Å)")
    return out


#: the residues a gap left open is capped with: an N-methylamide (NME) on the last residue before the gap, an acetyl
#: (ACE) on the first residue after it. Each end is then an ordinary neutral amide instead of a charged terminus
CAPS = ("ACE", "NME")
#: the caps' atoms under the names ff14SB and ff19SB give them (aminont12.lib, aminoct12.lib), which are OpenMM's too:
#: an ACE is C, O, CH3 and H1-H3; an NME is N, H, C (its methyl) and H1-H3. The older names (ff99SB's, and what the
#: lab's vendored preparation writes) call an NME's methyl CH3 and the methyl hydrogens of
#: either cap HH31-HH33, and tleap with ff19SB stops at them: "Atom .R<NME 191>.A<CH3 7> does not have a type".
#: Some docking programs' models call the methyl of either cap CA, its hydrogens HA1-HA3 and an NME's amide hydrogen HN
CAP_NAMES = {"ACE": {"HH31": "H1", "HH32": "H2", "HH33": "H3", "CA": "CH3", "HA1": "H1", "HA2": "H2", "HA3": "H3"},
             "NME": {"CH3": "C", "HH31": "H1", "HH32": "H2", "HH33": "H3", "CA": "C", "HA1": "H1", "HA2": "H2", "HA3": "H3",
                     "HN": "H"}}
#: what a terminus has that a capped end has not: the carboxylate's second oxygen on the residue before the gap, the
#: NH3+ hydrogens on the residue after it
_C_TERMINAL = {"OXT", "HXT", "OT2"}
_N_TERMINAL = {"H1", "H2", "H3", "HT1", "HT2", "HT3", "HN1", "HN2", "HN3"}
#: an ACE is turned until each of its heavy atoms is this far, in Å, from every heavy atom but the N and CA of the
#: residue it caps, as the lab's vendored preparation turns its own (cap_gaps)
ACE_ROOM_A = 2.8


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(u, v):
    return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])


def _unit(v):
    n = sum(x * x for x in v) ** 0.5
    return tuple(x / n for x in v) if n > 1e-6 else tuple(v)


def _torsion(a, b, c, d) -> float:
    """The a-b-c-d dihedral, in degrees: 0 with a and d on the same side of b-c (cis), 180 opposite (trans), in the
    sense _place builds it. The preparation the caps are ported from measures 180
    less this, so it puts an NME's N at the mirror image of where it belongs: as close as 0.65 Å to the carbonyl
    oxygen it should be trans to on 7A3R, and the minimization then leaves the amide cis."""
    import math
    b1, b2, b3 = _sub(b, a), _sub(c, b), _sub(d, c)
    n1, n2 = _cross(b1, b2), _cross(b2, b3)
    y = sum(x * x for x in b2) ** 0.5 * sum(p * q for p, q in zip(b1, n2))
    return math.degrees(math.atan2(y, sum(p * q for p, q in zip(n1, n2))))


def _place(a, b, c, bond: float, angle: float, torsion: float):
    """The atom d at `bond` Å from c, with the b-c-d angle and the a-b-c-d dihedral given in degrees: built this way,
    by angles and torsions, the atoms of a cap cannot fall on one line."""
    import math
    angle, torsion = math.radians(angle), math.radians(torsion)
    bc = _unit(_sub(c, b))
    n = _unit(_cross(_sub(b, a), bc))
    m = _cross(n, bc)
    d = (-bond * math.cos(angle), bond * math.sin(angle) * math.cos(torsion), bond * math.sin(angle) * math.sin(torsion))
    return tuple(c[i] + d[0] * bc[i] + d[1] * m[i] + d[2] * n[i] for i in range(3))


def _cap_line(name: str, resname: str, chain: str, resi: str, xyz) -> str:
    field = name if len(name) == 4 else " " + name.ljust(3)
    n = _number(resi)
    code = resi[len(str(n)):] or " "
    return (f"ATOM  {0:5d} {field} {resname:>3s} {chain}{n:4d}{code[:1]}   {xyz[0]:8.3f}{xyz[1]:8.3f}{xyz[2]:8.3f}"
            f"  1.00  0.00          {name[0]:>2s}")


def _number(resi: str) -> int | None:
    m = re.match(r"-?\d+", resi.strip())
    return int(m.group()) if m else None


def residue_ids(pdb_text: str) -> dict[str, set[str]]:
    """{chain letter: the residue ids its records use, number and insertion code together ("191", "60A")}: every
    residue of that letter, the hetero groups and the waters among them. What a cap's id must not be (cap_gaps)."""
    out: dict[str, set[str]] = {}
    for l in pdb_text.splitlines():
        if l.startswith(("ATOM", "HETATM")) and len(l) > 26:
            out.setdefault(l[21].strip() or "A", set()).add(l[22:27].strip())
    return out


def open_gaps(pdb_text: str, max_cn_A: float = BREAK_CN_A, gap_A: float = GAP_A) -> list[tuple[dict, dict]]:
    """The protein gaps of the text, as (the residue before, the residue after) in file order: consecutive amino acids
    of one chain whose C–N is a gap as ter_at_breaks finds one (over gap_A, or across a missing residue id). A TER
    already between them does not hide a gap, so a structure whose breaks an earlier preparation ended is read as it
    was; a pair whose numbering starts again after a TER is two molecules, not a gap. A cap is never one end of a gap:
    it has no backbone on its open side, so a structure capped before has no gap left to cap."""
    out = []
    res = _residues(pdb_text.splitlines())
    for a, b in zip(res, res[1:]):
        if a["chain"] != b["chain"] or _kind(a) != "protein" or _kind(b) != "protein" or a["name"] in CAPS or b["name"] in CAPS:
            continue
        link = _long_link(a, b, "protein", max_cn_A, BREAK_OP_A, gap_A)
        if not link or not link[2]:
            continue
        na, nb = _number(a["resi"]), _number(b["resi"])
        if b["after_ter"] and (na is None or nb is None or nb < na):
            continue
        out.append((a, b))
    return out


def cap_gaps(pdb_text: str, taken: dict[str, set[str]] | None = None,
             hydrogens: bool = False) -> tuple[str, list[dict], list[dict]]:
    """An NME on the last residue before each protein gap (open_gaps) and an ACE on the first residue after it, with
    a TER between the two caps, so neither end is a charged terminus the protein does not have. The terminal atoms
    the ends carry go (an OXT before the gap, NH3+ hydrogens after it). The caps are built on the residues' own
    backbone at standard bond lengths and angles, both peptide bonds trans; with hydrogens=True (a structure that
    carries its hydrogens) the caps get theirs, and the residue after the gap its amide hydrogen. The atoms are named
    as the force fields name them (CAP_NAMES).

    Numbering. The NME is numbered one past the residue before the gap and the ACE one short of the residue after
    it, as the lab's vendored preparation numbers them: A:190-195 is capped by NME A:191 and ACE A:194. These
    are the numbers of the residues missing at each end, and the caps are those residues' own backbone atoms (the
    NME's N and methyl stand where the next residue's N and CA would, the ACE's carbonyl and methyl where the
    previous residue's C, O and CA would). No residue is renumbered here, and no cap takes an id a residue of the
    chain has (taken[chain], from the structure as it arrived, with its hetero groups and waters, and the text), so a
    hotspot list names the residues it named before. A cap whose number is free but whose id is not (one residue
    missing, where both caps are numbered alike; a water numbered there) takes an insertion code, the first free one
    from A. A gap with no number free beside it (the residues either side numbered consecutively, as in a renumbered
    file, or a gap inside a run of insertion codes, thrombin's H:149A-149E) has its caps under insertion codes of the
    number before it (cap_ids): those of the missing residues where the run has them (NME H:149B, ACE H:149D), else
    the first free ones (NME A:3A, ACE A:3B between A:3 and A:4). That numbering once had to keep a cap bonded,
    since ParmEd, which pdb4amber reads with, bonds a residue to the next only when their numbers differ by one; the
    build now numbers every residue in file order for pdb4amber (number_for_build), so a cap's bond does not rest on
    its id, and an insertion code is as good as a number.

    The NMEs are placed first, as each stands where its residue's own carbonyl puts it and has no freedom. Each ACE
    is then turned about its residue's N-CA bond, from extended, to the first turn that leaves its heavy atoms
    ACE_ROOM_A or more from every heavy atom but that residue's N and CA (the residue's own side chain, the NMEs and
    the caps placed before it included), or to the turn that leaves the most room; as the lab's vendored preparation
    turns its own.

    Returns the text, the caps ([{"break": "A:190-195", "nme": "A:191", "ace": "A:194"}, ...]) and the gaps left open
    with why ([{"break": "A:179-180", "why": "..."}]). A gap whose ends lack the backbone atoms a cap is built on
    (N, CA, C and O before it; N, CA and C after it) is left open, as is one whose number before it has no insertion
    code free."""
    lines = pdb_text.splitlines()
    gaps = open_gaps(pdb_text)
    if not gaps:
        return pdb_text, [], []
    ids = residue_ids(pdb_text)
    for ch, got in (taken or {}).items():
        ids.setdefault(ch, set()).update(got)
    polymer: dict[str, set[int]] = {}
    # every heavy atom, as (the residue's first line, the atom's name, its coordinates); the terminal oxygen a
    # residue before a gap carries goes below (the NME's N stands where it is), so it is no neighbor of an ACE
    ends = {a["lines"][0] for a, _ in gaps}
    heavy: list[tuple[int, str, tuple]] = []
    for r in _residues(lines):
        if _kind(r) in ("protein", "nucleic") and _number(r["resi"]) is not None:
            polymer.setdefault(r["chain"], set()).add(_number(r["resi"]))
        heavy += [(r["lines"][0], name, xyz) for name, xyz in r["atoms"].items()
                  if not name.lstrip("0123456789").startswith(("H", "D"))
                  and not (r["lines"][0] in ends and name in _C_TERMINAL)]

    def free_id(chain: str, n: int) -> str:
        for code in [""] + [chr(c) for c in range(ord("A"), ord("Z") + 1)]:
            if f"{n}{code}" not in ids.setdefault(chain, set()):
                ids[chain].add(f"{n}{code}")
                return f"{n}{code}"
        return ""

    def cap_ids(chain: str, a_id: str, b_id: str) -> tuple[str, str] | None:
        """The ids of the NME and the ACE of the gap a_id-b_id: the numbers of the missing residues where they are
        free, else insertion codes of a_id's number (see above). None when no two are free."""
        na, nb = _number(a_id), _number(b_id)
        if na is None:
            return None
        n1, n2 = (na + 1, nb - 1) if nb is not None else (1, 0)
        if n1 <= n2 and n1 not in polymer.get(chain, set()) and n2 not in polymer.get(chain, set()):
            n_nme = free_id(chain, n1)
            n_ace = free_id(chain, n2)
            return (n_nme, n_ace) if n_nme and n_ace else None
        taken_ = ids.setdefault(chain, set())
        code_a = a_id[len(str(na)):].strip()[:1].upper()
        code_b = b_id[len(str(nb)):].strip()[:1].upper() if nb == na else ""
        letters = [chr(c) for c in range(ord("A"), ord("Z") + 1)]
        between = [f"{na}{c}" for c in letters if (not code_a or c > code_a) and (not code_b or c < code_b)
                   and f"{na}{c}" not in taken_]
        if len(between) >= 2:
            pair = (between[0], between[-1] if code_b else between[1])
        else:
            rest = [f"{na}{c}" for c in letters if f"{na}{c}" not in taken_ and f"{na}{c}" not in between]
            pick = between + rest
            if len(pick) < 2:
                return None
            pair = (pick[0], pick[1])
        taken_.update(pair)
        return pair

    after: dict[int, list[str]] = {}       # line index -> lines to write after it
    before: dict[int, list[str]] = {}      # line index -> lines to write before it
    drop: set[int] = set()
    caps, left = [], []
    # the gaps to cap, with their ids and their NMEs: every NME is placed before any ACE is turned
    todo = []
    for a, b in gaps:
        A, B = a["atoms"], b["atoms"]
        chain, label = a["chain"], f"{a['chain']}:{a['resi']}-{b['resi']}"
        if not ({"N", "CA", "C", "O"} <= set(A) and {"N", "CA", "C"} <= set(B)):
            left.append({"break": label, "why": "the residues either side lack the backbone atoms a cap is built on"})
            continue
        got = cap_ids(chain, a["resi"], b["resi"])
        if not got:
            left.append({"break": label, "why": f"no id is free for its caps: every insertion code of {a['resi']}'s "
                                                 "number is taken"})
            continue
        # NME on the piece that ends at a: its N trans to a's carbonyl oxygen, its methyl cis to it
        psi = _torsion(A["N"], A["CA"], A["C"], A["O"])
        nme_n = _place(A["N"], A["CA"], A["C"], 1.329, 116.2, psi + 180.0)
        nme_c = _place(A["O"], A["C"], nme_n, 1.449, 121.5, 0.0)
        todo.append((a, b, label, got, nme_n, nme_c))
    placed: list[tuple] = [x for t in todo for x in t[4:6]]
    for a, b, label, (n_nme, n_ace), nme_n, nme_c in todo:
        A, B = a["atoms"], b["atoms"]
        chain = a["chain"]
        nme = [("N", nme_n)]
        if hydrogens:
            nme.append(("H", _place(A["O"], A["C"], nme_n, 1.010, 119.0, 180.0)))
        nme.append(("C", nme_c))
        if hydrogens:
            nme += [(f"H{i}", _place(A["C"], nme_n, nme_c, 1.090, 109.5, t)) for i, t in enumerate((60.0, 180.0, 300.0), 1)]
        # ACE on the piece that starts at b: its carbonyl carbon on b's N, the oxygen cis to b's CA, the methyl trans.
        # Where the carbonyl goes about b's N-CA bond is not known (the residue it stands for is missing): extended,
        # unless that brings it within ACE_ROOM_A of a heavy atom other than b's N and CA (b's own side chain among
        # them: extended put 7A3R's ACE A:274 O 2.16 Å from Thr 275 OG1), and then the first turn that clears them all,
        # or failing that the one that leaves the most room
        near = [xyz for k, name, xyz in heavy if not (k == b["lines"][0] and name in ("N", "CA"))
                and _dist(xyz, B["N"]) < 9.0] + placed
        best = None
        for phi in (180.0, -150.0, -120.0, -90.0, -60.0, -30.0, 0.0, 30.0, 60.0, 90.0, 120.0, 150.0):
            c_ = _place(B["C"], B["CA"], B["N"], 1.335, 121.7, phi)
            trial = (c_, _place(B["CA"], B["N"], c_, 1.229, 122.7, 0.0), _place(B["CA"], B["N"], c_, 1.508, 116.6, 180.0))
            room = min((_dist(x, y) for x in trial for y in near), default=99.0)
            if best is None or room > best[0]:
                best = (room, trial)
            if room >= ACE_ROOM_A:
                break
        ace_c, ace_o, ace_m = best[1]
        placed += [ace_c, ace_o, ace_m]
        ace = [("C", ace_c), ("O", ace_o), ("CH3", ace_m)]
        if hydrogens:
            ace += [(f"H{i}", _place(B["N"], ace_c, ace_m, 1.090, 109.5, t)) for i, t in enumerate((60.0, 180.0, 300.0), 1)]
        drop.update(i for i in a["lines"] if lines[i][12:16].strip() in _C_TERMINAL)
        drop.update(i for i in b["lines"] if lines[i][12:16].strip() in _N_TERMINAL)
        last = max(i for i in a["lines"] if i not in drop)
        while last + 1 < len(lines) and lines[last + 1].startswith(("ANISOU", "SIGATM", "SIGUIJ")):
            last += 1                       # an atom's own records stay with it
        after.setdefault(last, []).extend(_cap_line(nm, "NME", chain, n_nme, xyz) for nm, xyz in nme)
        new_b = [] if b["after_ter"] else ["TER"]
        new_b += [_cap_line(nm, "ACE", chain, n_ace, xyz) for nm, xyz in ace]
        before.setdefault(b["lines"][0], []).extend(new_b)
        if hydrogens and b["name"] != "PRO" and "H" not in B:
            # the amide hydrogen, in the plane of the cap's C, N and CA, opposite the bisector of the two bonds
            u = _unit(tuple(x + y for x, y in zip(_unit(_sub(ace_c, B["N"])), _unit(_sub(B["CA"], B["N"])))))
            h = tuple(B["N"][k] - 1.01 * u[k] for k in range(3))
            at_n = next(i for i in b["lines"] if lines[i][12:16].strip() == "N")
            l = lines[at_n].ljust(30)
            after.setdefault(at_n, []).append(l[:6] + "    0  H  " + l[16:30] + f"{h[0]:8.3f}{h[1]:8.3f}{h[2]:8.3f}"
                                              + "  1.00  0.00           H")
        caps.append({"break": label, "nme": f"{chain}:{n_nme}", "ace": f"{chain}:{n_ace}"})
    if not caps:
        return pdb_text, [], left
    out = []
    for i, l in enumerate(lines):
        out.extend(before.get(i, []))
        if i not in drop:
            out.append(l)
        out.extend(after.get(i, []))
    return _serials("\n".join(out) + "\n"), caps, left


def caps_in(pdb_text: str) -> list[dict]:
    """The capped gaps of the text, as cap_gaps reports them: each NME followed by an ACE of the same chain, with the
    residues they are bonded to either side, [{"break": "A:190-195", "nme": "A:191", "ace": "A:194"}, ...]."""
    res = _residues(pdb_text.splitlines())
    out = []
    for k in range(1, len(res) - 2):
        a, nme, ace, b = res[k - 1], res[k], res[k + 1], res[k + 2]
        if nme["name"] == "NME" and ace["name"] == "ACE" and not nme["after_ter"] and not b["after_ter"] \
                and a["chain"] == nme["chain"] == ace["chain"] == b["chain"]:
            out.append({"break": f"{a['chain']}:{a['resi']}-{b['resi']}", "nme": f"{nme['chain']}:{nme['resi']}",
                        "ace": f"{ace['chain']}:{ace['resi']}"})
    return out


def hold_caps(pdb_text: str) -> tuple[str, list[dict]]:
    """The text without its ACE and NME caps, and the caps held to go back (return_caps). pdb2pqr has no definition
    for either and writes nothing when it meets one, so a structure capped before goes through it as it was before
    the caps were put on: the TER between the two caps of a gap goes with them, and the chain reads as one with a gap
    in it. Each cap is held with the residue it is bonded to (the one before an NME, the one after an ACE)."""
    lines = pdb_text.splitlines()
    res = _residues(lines)
    held, drop = [], set()
    for k, r in enumerate(res):
        if r["name"] not in CAPS:
            continue
        nb = res[k - 1] if r["name"] == "NME" else (res[k + 1] if k + 1 < len(res) else None)
        if r["name"] == "NME" and (k == 0 or r["after_ter"]):
            nb = None
        if r["name"] == "ACE" and nb is not None and nb["after_ter"]:
            nb = None
        if nb is None or nb["chain"] != r["chain"] or nb["name"] in CAPS:
            continue                            # bonded to nothing it can go back to: left where it is
        held.append({"name": r["name"], "lines": [lines[i] for i in r["lines"]], "anchor": (nb["chain"], nb["resi"]),
                     "ter": r["name"] == "ACE" and r["after_ter"]})
        drop.update(r["lines"])
        if r["name"] == "ACE" and r["after_ter"]:
            j = r["lines"][0] - 1               # the TER before it, which the NME before it leaves behind
            while j >= 0 and not lines[j].startswith("TER") and not lines[j].startswith(("ATOM", "HETATM")):
                j -= 1
            if j >= 0 and lines[j].startswith("TER") and k > 0 and res[k - 1]["name"] == "NME":
                drop.add(j)
    if not held:
        return pdb_text, []
    return "\n".join(l for i, l in enumerate(lines) if i not in drop) + "\n", held


def return_caps(pdb_text: str, held: list[dict], hydrogens: bool = False) -> str:
    """The caps hold_caps took out, back on the residues they are bonded to, with a TER before each ACE that had one.
    What a terminus has and a capped end has not goes from those residues (pdb2pqr may have made a chain end of
    them): the OXT before an NME, the NH3+ hydrogens after an ACE; with hydrogens=True the residue after an ACE has
    its amide hydrogen. A cap whose residue is no longer there is not put back."""
    if not held:
        return pdb_text
    lines = pdb_text.splitlines()
    res = {(r["chain"], r["resi"]): r for r in _residues(lines)}
    after: dict[int, list[str]] = {}
    before: dict[int, list[str]] = {}
    drop: set[int] = set()
    for cap in held:
        r = res.get(cap["anchor"])
        if r is None:
            continue
        if cap["name"] == "NME":
            drop.update(i for i in r["lines"] if lines[i][12:16].strip() in _C_TERMINAL)
            last = max(i for i in r["lines"] if i not in drop)
            after.setdefault(last, []).extend(cap["lines"])
            continue
        drop.update(i for i in r["lines"] if lines[i][12:16].strip() in _N_TERMINAL)
        first = r["lines"][0]
        prev = next((lines[j] for j in range(first - 1, -1, -1) if lines[j].startswith(("ATOM", "HETATM", "TER"))), "")
        before.setdefault(first, []).extend((["TER"] if cap["ter"] and prev.startswith(("ATOM", "HETATM")) else []) + cap["lines"])
        c = next((l for l in cap["lines"] if l[12:16].strip() == "C"), None)
        if hydrogens and c and r["name"] != "PRO" and "H" not in r["atoms"] and {"N", "CA"} <= set(r["atoms"]):
            n_, ca = r["atoms"]["N"], r["atoms"]["CA"]
            cx = (float(c[30:38]), float(c[38:46]), float(c[46:54]))
            u = _unit(tuple(x + y for x, y in zip(_unit(_sub(cx, n_)), _unit(_sub(ca, n_)))))
            h = tuple(n_[k] - 1.01 * u[k] for k in range(3))
            at_n = next(i for i in r["lines"] if lines[i][12:16].strip() == "N")
            l = lines[at_n].ljust(30)
            after.setdefault(at_n, []).append(l[:6] + "    0  H  " + l[16:30] + f"{h[0]:8.3f}{h[1]:8.3f}{h[2]:8.3f}"
                                              + "  1.00  0.00           H")
    out = []
    for i, l in enumerate(lines):
        out.extend(before.get(i, []))
        if i not in drop:
            out.append(l)
        out.extend(after.get(i, []))
    return _serials("\n".join(out) + "\n")


def _serials(pdb_text: str) -> str:
    """The atoms numbered in order again, unless a record refers to them by number; then the atoms numbered 0 (those
    just put in) after the highest number."""
    out = pdb_text.splitlines()
    conect = any(l.startswith("CONECT") for l in out)
    serial = 0 if not conect else max((int(l[6:11]) for l in out if l.startswith(("ATOM", "HETATM")) and l[6:11].strip().isdigit()),
                                      default=0)
    for i, l in enumerate(out):
        if l.startswith(("ATOM", "HETATM")) and (not conect or l[6:11] == "    0"):
            serial += 1
            out[i] = l[:6] + f"{min(serial, 99999):5d}" + l[11:]
    return "\n".join(out) + "\n"


def cap_records(pdb_text: str) -> str:
    """The caps as Prepare writes them: their atoms under the force fields' names, whatever older ones they came with
    (CAP_NAMES), and ATOM records, as the residues of the chain they are. OpenMM writes them as HETATM, and a tool
    that takes a HETATM for a ligand and drops it would leave the gap open again."""
    out = []
    for l in pdb_text.splitlines():
        if l.startswith(("ATOM", "HETATM")) and l[17:20].strip() in CAPS:
            new = CAP_NAMES[l[17:20].strip()].get(l[12:16].strip())
            if new:
                l = l[:12] + (new if len(new) == 4 else " " + new.ljust(3)) + l[16:]
            l = "ATOM  " + l[6:]
        out.append(l)
    return "\n".join(out) + "\n"


def capped_note(caps: list[dict]) -> str:
    """The sentence the report and the logs give the gaps cap_gaps capped."""
    one = len(caps) == 1
    return (f"{len(caps)} chain break{'' if one else 's'} capped, an NME on the residue before the gap and an ACE on the "
            f"residue after it, so {'its' if one else 'their'} ends are neutral amides rather than charged termini (the "
            f"chain's true termini stay charged): " + ", ".join(f"{c['break']} (NME {c['nme']}, ACE {c['ace']})" for c in caps))


def stretched_note(stretched: list[str], where: str = "") -> str:
    """The sentence the report and the logs give the bonds stretched_bonds names (those of the receptor, where="receptor")."""
    one = len(stretched) == 1
    return (f"{len(stretched)} bond{'' if one else 's'}{' in the ' + where if where else ''} longer than a bond with no residue "
            f"missing, left bonded for the minimization to close: {', '.join(stretched)}")


def strip_5p_phosphates(pdb_text: str) -> tuple[str, list[str]]:
    """Drop the phosphate of the first nucleotide of every strand, and of every piece a break leaves (the
    first residue after a TER): the Amber nucleic-acid force fields start a 5′ residue at O5′ and have no
    template for a phosphate there. Returns the text and the residues it took one from, "chain:number"."""
    lines = pdb_text.splitlines()
    drop: set[int] = set()
    stripped = []
    prev = None
    for r in _residues(lines):
        first = r["after_ter"] or prev is None or prev["chain"] != r["chain"] or _kind(prev) != _kind(r)
        prev = r
        if not first or _kind(r) != "nucleic":
            continue
        gone = [i for i in r["lines"] if lines[i][12:16].strip() in PHOSPHATE_5P]
        if gone:
            drop.update(gone)
            stripped.append(f"{r['chain']}:{r['resi']}")
    return "\n".join(l for i, l in enumerate(lines) if i not in drop) + "\n", stripped


#: the protonation states and terminal names a residue may take in the build in place of its own name: what pdb2pqr
#: and pdb4amber call it (HID, CYX ...) is still the residue the build numbered, and so is a nucleotide under any of
#: its names (plain_nucleotide)
STATE_PARENT = {"HID": "HIS", "HIE": "HIS", "HIP": "HIS", "CYX": "CYS", "CYM": "CYS", "ASH": "ASP", "GLH": "GLU",
                "LYN": "LYS"}
#: the residues a PDB's number field (columns 23-26) holds; the build numbers past it start again from 0, as ParmEd
#: writes them, and ParmEd's reader counts on through the wrap
PDB_RESIDUE_FIELD = 10000


def parent_name(name: str) -> str:
    """A residue's name with its protonation state or terminal form taken off: HID -> HIS, CYX -> CYS, DA5 -> DA."""
    name = name.strip().upper()
    return STATE_PARENT.get(name) or plain_nucleotide(name) or name


def number_for_build(pdb_text: str) -> tuple[str, dict]:
    """The text as pdb4amber must read it: every residue numbered in file order, 1, 2, 3 ..., without insertion codes,
    and a TER at every break. pdb4amber reads with ParmEd, which bonds a residue to the next only when their numbers
    differ by one, so a chain with insertion codes (thrombin's 60A-60I, an antibody's 82A-82C) or with a number its
    numbering scheme skips (chymotrypsin's) was cut at each one, and tleap built charged termini there: 52 TERs on
    thrombin's two chains where the file has 2. Numbered in sequence, a residue is joined to the next wherever the
    backbone joins.

    The real breaks are found first, on the file's own ids (ter_at_breaks: a link over GAP_A, or one across a residue id
    the file does not have), and ended with TER, which ParmEd and tleap both honor, so a gap stays a gap whatever its
    numbers become. A cap Prepare put on a gap (ACE, NME) follows or precedes its residue in the file and is numbered
    next to it, so it stays bonded to it; the TER between the two caps of a gap stays where it is.

    The numbers run on from one chain into the next (ParmEd joins nothing across a change of chain, or a TER): that is
    how pdb4amber writes its residues out and how tleap numbers the molecule, so a residue's number is the same in
    every file of the build after this one, and is its place in the molecule's part of the topology.

    Returns the text and the map: {"residues": [{"n": 1, "chain": "L", "id": "1H", "name": "THR"}, ...] in file order
    (n the build number, chain, id and name as the file has them), "breaks": the breaks as the file names them
    ("H:60I-64"), "after_break": the build numbers of the residues a break comes before, "coded": residues with an
    insertion code, "skipped": residues joined to the one before though their numbers skip (chymotrypsin's 217 to
    219)}."""
    text, breaks = ter_at_breaks(pdb_text)
    lines = text.splitlines()
    res = _residues(lines)
    entries, after_break = [], []
    coded = skipped = 0
    prev = None
    for n, r in enumerate(res, 1):
        num = n % PDB_RESIDUE_FIELD
        for i in r["lines"]:
            l = lines[i].ljust(27)
            lines[i] = l[:22] + f"{num:4d} " + l[27:]
        entries.append({"n": n, "chain": r["chain"], "id": r["resi"], "name": r["name"]})
        coded += _number(r["resi"]) is not None and r["resi"] != str(_number(r["resi"]))
        if prev is not None and prev["chain"] == r["chain"]:
            if r["after_ter"] and _kind(prev) == _kind(r) != "other":
                after_break.append(n)
            na, nb = _number(prev["resi"]), _number(r["resi"])
            skipped += not r["after_ter"] and na is not None and nb is not None and nb - na > 1
        prev = r
    field = None
    for i, l in enumerate(lines):           # an atom's own records (ANISOU ...) carry its residue's new number too
        if l.startswith(("ATOM", "HETATM")):
            field = l[22:27]
        elif field is not None and l.startswith(("ANISOU", "SIGATM", "SIGUIJ")):
            lines[i] = l.ljust(27)[:22] + field + l.ljust(27)[27:]
        else:
            field = None
    return "\n".join(lines) + "\n", {"residues": entries, "breaks": breaks, "after_break": after_break,
                                    "coded": coded, "skipped": skipped}


def restore_order(pdb_text: str) -> tuple[str, list[str]]:
    """The residues of a file the build numbered (number_for_build) put back in the order of their numbers, each with
    the TER that followed it. pdb2pqr writes the chains in an order of its own (thrombin's H before L, where the file
    and the build's numbering have L first), and the topology would then not be in the order the residue ranges, the
    restraints and the numbering's map count. Left as it is when two residues share a number (more residues than the
    number field holds). Returns the text and the chains in the order they were found, when it moved them."""
    lines = pdb_text.splitlines()
    res = _residues(lines)
    nums = [_number(r["resi"]) for r in res]
    if not res or None in nums or len(set(nums)) != len(nums) or nums == sorted(nums):
        return pdb_text, []
    order = []
    for r in res:
        if not order or order[-1] != r["chain"]:
            order.append(r["chain"])
    first, last = res[0]["lines"][0], res[-1]["lines"][-1]
    while last + 1 < len(lines) and lines[last + 1].startswith(("TER", "ANISOU", "SIGATM", "SIGUIJ")):
        last += 1                           # the last residue's own records and the TER after it go with it
    blocks = []
    for k, r in enumerate(res):
        end = res[k + 1]["lines"][0] if k + 1 < len(res) else last + 1
        # the residue's lines and what follows them up to the next residue (its ANISOU records, a TER after it)
        blocks.append((nums[k], [lines[i] for i in range(r["lines"][0], end)]))
    tail = lines[last + 1:]
    body = [l for _, b in sorted(blocks, key=lambda x: x[0]) for l in b]
    return "\n".join(lines[:first] + body + tail) + "\n", order


def _lined_up(res: list[dict], numbering: dict) -> bool:
    """Whether the residues of a file are those of the build's numbering, one for one in order: as many, each under
    its name or a state of it (pdb2pqr's HID for a HIS). A residue pdb2pqr dropped or added breaks the line-up."""
    known = numbering.get("residues") or []
    return len(known) == len(res) and all(parent_name(r["name"]) == parent_name(k["name"]) for r, k in zip(res, known))


def _relabel(label: str, ids: dict[tuple[str, str], str]) -> str:
    """A label the build's numbers name ("H:57-58", "H:57-58 (C–N 3.10 Å)", "L:9") under the residues' own ids."""
    m = re.match(r"^(.+?):(\d+)(?:-(\d+))?(.*)$", label)
    if not m:
        return label
    ch, a, b, rest = m.groups()
    out = f"{ch}:{ids.get((ch, a), a)}"
    if b is not None:
        out += f"-{ids.get((ch, b), b)}"
    return out + rest


def ready_chains(pdb_text: str, numbering: dict | None = None) -> tuple[str, dict]:
    """What the build does to each prepared PDB before the topology is made from it, on every route:
    nucleotide names as OL3 and OL21 have them, a TER at every break, no phosphate on a 5′ end. pdb2pqr
    writes TER only at the end of a chain, so the breaks are marked again after it: with the build's numbering
    (number_for_build) every break it found on the file's own ids gets its TER back, a gap of a residue whose ends
    lie close included, and the rest by their geometry. Returns the text and what changed, with the stretched bonds
    left bonded, each named by the residues' own ids when the numbering lines up with the file ("lined_up")."""
    text, renamed = normalise_nucleic_names(pdb_text)
    lined = False
    ids: dict[tuple[str, str], str] = {}
    restored = 0
    if numbering:
        lines = text.splitlines()
        res = _residues(lines)
        lined = _lined_up(res, numbering)
        if lined:
            ids = {(r["chain"], r["resi"]): k["id"] for r, k in zip(res, numbering["residues"])}
            want = set(numbering.get("after_break") or [])
            ter_before = {r["lines"][0] for r, k in zip(res, numbering["residues"]) if k["n"] in want and not r["after_ter"]}
            restored = len(ter_before)
            if ter_before:
                out = []
                for i, l in enumerate(lines):
                    if i in ter_before:
                        out.append("TER")
                    out.append(l)
                text = "\n".join(out) + "\n"
    text, breaks = ter_at_breaks(text)
    text, stripped = strip_5p_phosphates(text)
    stretched = stretched_bonds(text)
    if ids:
        breaks, stretched, stripped = ([_relabel(x, ids) for x in xs] for xs in (breaks, stretched, stripped))
    return text, {"renamed": renamed, "breaks": breaks, "phosphates": stripped, "stretched": stretched,
                  "lined_up": lined, "restored": restored}


#: two cysteine sulfurs this close, in Å, are a disulfide (an S–S bond is 2.05 Å): the import's measure
#: (importers/files.py inspect_pdb), and the build's for the pairs that no one file holds
SS_BOND_A = 2.3
#: the hydrogen a free cysteine carries on its sulfur, which a bonded one has not
THIOL_H = ("HG", "HG1")


def disulfide_pairs(first: list[dict], second: list[dict] | None = None,
                    within_A: float = SS_BOND_A) -> list[tuple[int, int, float]]:
    """The disulfides among residues (_residues): cysteines under any of their names (CYS, CYX, CYM) whose SG atoms lie
    within `within_A`, as (index in first, index in the other list, distance), the closest first and each sulfur in one
    pair at most. With `second`, the pairs with one cysteine in each list and only those: the disulfides that cross the
    split of a complex into a receptor and a partner, which pdb4amber, reading one file at a time, never sees."""
    def sulphurs(res):
        return [(k, r["atoms"]["SG"]) for k, r in enumerate(res) if parent_name(r["name"]) == "CYS" and "SG" in r["atoms"]]
    a = sulphurs(first)
    b = sulphurs(second) if second is not None else a
    near = sorted((_dist(x, y), i, j) for i, x in a for j, y in b
                  if (second is not None or i < j) and _dist(x, y) <= within_A)
    out, used_a, used_b = [], set(), set()
    for d, i, j in near:
        if second is None:                       # one list: a sulfur is taken on either side of a pair
            if {i, j} & used_a:
                continue
            used_a.update((i, j))
        else:
            if i in used_a or j in used_b:
                continue
            used_a.add(i); used_b.add(j)
        out.append((i, j, round(d, 2)))
    return out


def _bonded_cysteine(lines: list[str], res: dict, drop: set[int]) -> None:
    """A cysteine of a disulfide as the force fields name it: CYX, without the hydrogen on its sulfur (whose line
    indices go into `drop`)."""
    for i in res["lines"]:
        if lines[i][12:16].strip() in THIOL_H:
            drop.add(i)
        else:
            lines[i] = lines[i][:17] + "CYX" + lines[i][20:]


def _own_ids(res: list[dict], numbering: dict | None) -> list[str]:
    """Each residue of a file the build numbered by the structure's own id ("H:60A"), when the numbering lines up with
    the file one for one; else by the chain and number the file has."""
    if numbering and _lined_up(res, numbering):
        return [f"{k['chain']}:{k['id']}" for k in numbering["residues"]]
    return [f"{r['chain']}:{r['resi']}" for r in res]


def link_split_disulfides(receptor_text: str, partner_text: str, receptor_numbering: dict | None = None,
                          partner_numbering: dict | None = None,
                          planned: list[tuple[int, int]] | tuple = ()) -> tuple[str, str, dict]:
    """The receptor's and the partner's files, as the build hands them to tleap, with each disulfide between them made
    ready to bond: both cysteines CYX and without HG. pdb4amber reads each file alone, so it names such a cysteine CYS
    (or keeps the CYX that Prepare's pdb2pqr gave it, unbonded) and writes no CONECT record across the two, and tleap
    built two free cysteines whose sulfurs sit 2 Å apart: thrombin split H | L had +629 kcal/mol of van der Waals in
    one frame. tleap bonds each pair by the residues' places in the combined unit, the receptor's 1 to N, then the
    partner's from N + 1: `planned`, the pairs the render wrote into the tleap input (from the import's count,
    System.split_disulfides), must be the pairs found here, or the topology would bond the wrong atoms or miss one.

    Returns the two texts and {"disulfides": [{"receptor": "H:122", "partner": "L:1", "residues": [119, 268],
    "distance_A": 1.97}], "planned_only": [[p, q], ...], "found_only": [...]}, residues by their topology numbers and
    named by the structure's own ids where the numbering lines up."""
    rl, pl = receptor_text.splitlines(), partner_text.splitlines()
    rres, pres = _residues(rl), _residues(pl)
    rids, pids = _own_ids(rres, receptor_numbering), _own_ids(pres, partner_numbering)
    n = len(rres)
    found = disulfide_pairs(rres, pres)
    got = {(i + 1, n + j + 1) for i, j, _ in found}
    want = {(int(p), int(q)) for p, q in planned}
    rdrop: set[int] = set()
    pdrop: set[int] = set()
    out = []
    for i, j, d in found:
        _bonded_cysteine(rl, rres[i], rdrop)
        _bonded_cysteine(pl, pres[j], pdrop)
        out.append({"receptor": rids[i], "partner": pids[j], "residues": [i + 1, n + j + 1], "distance_A": d})

    def text(lines, drop):
        return "\n".join(l for k, l in enumerate(lines) if k not in drop) + "\n"
    return text(rl, rdrop), text(pl, pdrop), {"disulfides": out, "planned_only": sorted(map(list, want - got)),
                                              "found_only": sorted(map(list, got - want))}


def ssbond_records(pdb_text: str, within_A: float = SS_BOND_A) -> tuple[str, list[str]]:
    """The text with an SSBOND record for each disulfide it holds, each of its cysteines CYX and without HG. Maestro's
    PDB reader (structconvert, which the Desmond build goes through) bonds two sulfurs only when a record says so, and
    the build keeps no CONECT line of either file: without SSBOND records a Desmond build had no disulfide at all (none
    in the complex.cms of two earlier builds, and structconvert made none of thrombin's four; checked on Grace on 18
    Oct 2026), those across the split of a complex as unbonded as the rest. Returns the text and the pairs
    ("H:122-Z:1")."""
    lines = pdb_text.splitlines()
    res = _residues(lines)
    pairs = disulfide_pairs(res, within_A=within_A)
    drop: set[int] = set()
    records, names = [], []
    for k, (i, j, d) in enumerate(pairs, 1):
        a, b = res[i], res[j]
        _bonded_cysteine(lines, a, drop)
        _bonded_cysteine(lines, b, drop)
        la, lb = lines[a["lines"][0]].ljust(27), lines[b["lines"][0]].ljust(27)
        # the PDB format's columns: the names at 12-14 and 26-28, the chains at 16 and 30, the numbers at 18-21 and 32-35
        # with their insertion codes at 22 and 36, the symmetry operators at 60-65 and 67-72 and the length at 74-78
        records.append(f"SSBOND {k:3d} CYX {la[21]} {la[22:26]}{la[26]}   CYX {lb[21]} {lb[22:26]}{lb[26]}"
                       f"{'':23s}{'1555':>6s} {'1555':>6s} {d:5.2f}")
        names.append(f"{a['chain']}:{a['resi']}-{b['chain']}:{b['resi']}")
    body = [l for k, l in enumerate(lines) if k not in drop and not l.startswith("SSBOND")]
    return "\n".join(records + body) + "\n", names


def main(argv: list[str] | None = None) -> int:
    """python3 -m hopemd.structure number <in.pdb> <out.pdb> <map.json>: the residues numbered for pdb4amber
    (number_for_build), the map of their own ids written beside them.
    python3 -m hopemd.structure order <pdb>: pdb2pqr's output put back in the build's order, in place (restore_order).
    python3 -m hopemd.structure chains <pdb> [--numbering <map.json>] ...: each file made ready for the build in place
    (ready_chains), with the numbering the file was given when one follows it; what changed printed for the build's log.
    python3 -m hopemd.structure links <receptor.pdb> <receptor_map.json> <partner.pdb> <partner_map.json> <links.json>
    [--bond <p> <q>] ...: the disulfides across the split made ready for tleap's bond (link_split_disulfides), in place,
    and recorded in links.json; exits 3 when they are not the pairs the render bonds (--bond, topology numbers).
    python3 -m hopemd.structure ssbond <pdb>: an SSBOND record for each disulfide of the file, in place (ssbond_records)."""
    import json
    import sys
    from pathlib import Path
    args = sys.argv[1:] if argv is None else argv
    if len(args) == 4 and args[0] == "number":
        src, dst, mp = (Path(a) for a in args[1:])
        text, numbering = number_for_build(src.read_text(errors="replace"))
        dst.write_text(text)
        mp.write_text(json.dumps({"file": str(src), **numbering}, indent=0) + "\n")
        n = len(numbering["residues"])
        why = [f"{numbering['coded']} with an insertion code" if numbering["coded"] else "",
               f"{numbering['skipped']} after a skipped number" if numbering["skipped"] else ""]
        why = [w for w in why if w]
        print(f"[hopemd] {src}: {n} residues numbered 1-{n} in file order for pdb4amber, which bonds a residue to the next "
              f"only when their numbers follow"
              + (f" ({', '.join(why)}); " if why else "; ")
              + f"their own ids are kept in {mp}")
        if numbering["breaks"]:
            print(f"[hopemd] {src}: {len(numbering['breaks'])} chain break(s) ended with TER, each piece with its own "
                  f"termini: {', '.join(numbering['breaks'])}")
        return 0
    if len(args) == 2 and args[0] == "order":
        p = Path(args[1])
        text, order = restore_order(p.read_text(errors="replace"))
        if order:
            p.write_text(text)
            print(f"[hopemd] {p}: residues put back in the build's order (pdb2pqr wrote the chains as {', '.join(order)})")
        return 0
    if len(args) >= 6 and args[0] == "links":
        return _links_main(args[1:])
    if len(args) == 2 and args[0] == "ssbond":
        p = Path(args[1])
        text, pairs = ssbond_records(p.read_text(errors="replace"))
        p.write_text(text)
        if pairs:
            print(f"[hopemd] {p}: {len(pairs)} disulfide(s) written as SSBOND records, which Maestro's PDB reader bonds "
                  f"(it bonds no two sulfurs by their distance alone), the cysteines CYX: {', '.join(pairs)}")
        return 0
    if len(args) < 2 or args[0] != "chains":
        print("usage: python3 -m hopemd.structure number <in.pdb> <out.pdb> <map.json>\n"
              "       python3 -m hopemd.structure order <pdb>\n"
              "       python3 -m hopemd.structure chains <pdb> [--numbering <map.json>] ...\n"
              "       python3 -m hopemd.structure links <receptor.pdb> <receptor_map.json> <partner.pdb> <partner_map.json> "
              "<links.json> [--bond <p> <q>] ...\n"
              "       python3 -m hopemd.structure ssbond <pdb>", file=sys.stderr)
        return 2
    files: list[tuple[Path, Path | None]] = []
    rest = list(args[1:])
    while rest:
        name = rest.pop(0)
        if name == "--numbering" and files and rest:
            files[-1] = (files[-1][0], Path(rest.pop(0)))
            continue
        files.append((Path(name), None))
    for p, mp in files:
        numbering = None
        if mp is not None:
            try:
                numbering = json.loads(mp.read_text())
            except (OSError, ValueError) as e:
                print(f"[hopemd] {p}: the build's numbering ({mp}) could not be read ({e}); the breaks are found by their geometry")
        text, done = ready_chains(p.read_text(errors="replace"), numbering)
        p.write_text(text)
        if numbering is not None and not done["lined_up"]:
            print(f"[hopemd] {p}: its residues are not those {mp} numbered, one for one (a residue dropped or added since); "
                  "the breaks are found by their geometry and named by the build's numbers")
        if done["restored"]:
            print(f"[hopemd] {p}: {done['restored']} TER(s) put back at the chain breaks found on the structure's own ids "
                  "(pdb2pqr writes TER only at the end of a chain)")
        if done["renamed"]:
            print(f"[hopemd] {p}: {done['renamed']} nucleotide(s) given the names OL3 and OL21 use (pdb2pqr writes Amber's older ones)")
        if done["breaks"]:
            print(f"[hopemd] {p}: {len(done['breaks'])} chain break(s) ended with TER, each piece with its own termini: {', '.join(done['breaks'])}")
        if done["stretched"]:
            print(f"[hopemd] {p}: {stretched_note(done['stretched'])}")
        if done["phosphates"]:
            print(f"[hopemd] {p}: 5'-terminal phosphate dropped from {', '.join(done['phosphates'])}: the force field has no template for it")
    return 0


def _links_main(args: list[str]) -> int:
    """The links command of main: the two prepared files rewritten in place, links.json written, 3 on a mismatch."""
    import json
    from pathlib import Path
    rec, rmap, par, pmap, out = (Path(a) for a in args[:5])
    rest, planned = list(args[5:]), []
    while rest:
        if rest[0] != "--bond" or len(rest) < 3:
            print(f"[hopemd] links: cannot read {' '.join(rest)} (--bond <p> <q>, the topology numbers of the two cysteines)")
            return 2
        planned.append((int(rest[1]), int(rest[2])))
        rest = rest[3:]

    def numbering(p):
        try:
            return json.loads(p.read_text())
        except (OSError, ValueError):
            return None
    rtext, ptext, done = link_split_disulfides(rec.read_text(errors="replace"), par.read_text(errors="replace"),
                                               numbering(rmap), numbering(pmap), planned)
    named = {tuple(d["residues"]): f"{d['receptor']} (topology {d['residues'][0]}) and {d['partner']} (topology "
                                   f"{d['residues'][1]}), SG-SG {d['distance_A']:g} A" for d in done["disulfides"]}
    if done["found_only"]:
        print("[hopemd] a disulfide joins the receptor and the partner that the run was set up without: "
              + "; ".join(named[tuple(x)] for x in done["found_only"])
              + ". tleap would leave it unbonded and MM-GBSA would score a molecule cut in two: import the structure again "
              "(the import measures these), or keep both chains on one side of the split")
    if done["planned_only"]:
        print("[hopemd] the run bonds topology residues " + "; ".join(f"{p} and {q}" for p, q in done["planned_only"])
              + ", but in the build's files no disulfide joins them"
              + (" (the disulfides across the split are " + "; ".join(named.values()) + ")" if named else "")
              + ": the files no longer have the residues the import counted. Import the structure again")
    if done["found_only"] or done["planned_only"]:
        return 3
    rec.write_text(rtext)
    par.write_text(ptext)
    out.write_text(json.dumps({"disulfides": done["disulfides"]}, indent=1) + "\n")
    if done["disulfides"]:
        print(f"[hopemd] {len(done['disulfides'])} disulfide(s) across the split, receptor to partner, which pdb4amber "
              "cannot bond (it reads each file alone): both cysteines CYX, for the topology to bond: " + "; ".join(named.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
