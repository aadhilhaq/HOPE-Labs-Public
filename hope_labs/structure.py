"""Read a structure: what chains are in it, what each one's sequence is, and what touches what.

By Aadhil Haq

Every page here asks a person for something they already have in a file. BioEmu asks for a
sequence; the folding pages ask for one row per chain; the design pages ask which residues a
binder should reach. In each case the answer is sitting in a structure on their disk, and until
this module existed each page made them fetch it by hand: open the file in a viewer, find the
chain, copy the one-letter codes, count the residue numbers, type it all back in. That is where
the mistakes come from, and none of them show up until a job has finished.

So this reads it, once, for all of them. One reading means the pages agree about what a chain is:
the sequence BioEmu samples, the row Boltz-2 folds and the numbering the design ruler draws all
come from the same four functions, so they cannot drift apart.

PDB and mmCIF, with the standard library and nothing else. These pages run on a login node where
there is no numpy and no gemmi, and a page that needed either would be a page that could not
start. What that costs is strictness: this reads coordinates, not chemistry, and anything it is
unsure of it says rather than guesses.

    python -m hope_labs.structure <a .pdb or .cif>
"""
from __future__ import annotations

import math
import os
import sys

#: The twenty, as a structure names them, plus the two every crystal structure carries that are
#: one of the twenty to every model that reads them: selenomethionine, and the three protonation
#: states a preparation writes for histidine. A residue outside this list is not guessed at.
THREE = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q", "GLU": "E",
    "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F",
    "PRO": "P", "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
    # Selenomethionine is methionine to every model here, and left out it would be a hole in the
    # chain. The histidines are what pdb2pqr and CHARMM write for the three protonation states,
    # so a prepared structure reads as protein rather than as a chain full of unknown residues.
    "MSE": "M", "HID": "H", "HIE": "H", "HIP": "H", "HSD": "H", "HSE": "H", "HSP": "H",
    "CYX": "C", "CYM": "C", "ASH": "D", "GLH": "E", "LYN": "K", "TYM": "Y",
}

#: What a nucleic acid chain is called, and what each residue is spelled with, so a strand reads
#: back as its bases rather than as a row of unknowns. The deposited names, plus the RA/RC/RG/RU
#: some programs write and I for inosine.
NUCLEIC_LETTER = {"DA": "A", "DC": "C", "DG": "G", "DT": "T", "DU": "U",
                  "A": "A", "C": "C", "G": "G", "U": "U", "T": "T", "I": "I",
                  "RA": "A", "RC": "C", "RG": "G", "RU": "U"}
NUCLEIC = set(NUCLEIC_LETTER)

#: Everything a structure carries that is not part of a chain. Water first, because every
#: structure has it and it is the one that would otherwise be counted as residues.
SOLVENT = {"HOH", "WAT", "DOD", "TIP3", "SOL"}

#: A single atom of metal or salt, which a structure writes as a residue of its own and which is
#: not part of any chain.
#:
#: Calcium is the one that matters, and it is a trap worth stating plainly: a calcium ion is
#: written `HETATM ... CA    CA A 149`, so its ATOM NAME and its RESIDUE NAME are both CA, and a
#: reader that counts a residue by the presence of an atom called CA counts every ion as a
#: residue. Calmodulin has four. The design page's sequence ruler, which pairs the letters it
#: reads here with the numbers its own reader finds, then disagreed about the length of the chain
#: and drew every residue as a question mark, on exactly the EF-hand proteins somebody would aim
#: a binder at.
IONS = {"CA", "MG", "ZN", "NA", "K", "CL", "FE", "FE2", "MN", "CU", "CU1", "CO", "NI", "CD",
        "HG", "SR", "BA", "LI", "CS", "RB", "AL", "F", "BR", "IOD", "PB", "PT", "AU", "AG",
        "MO", "W", "V", "CR", "SE4", "YB", "GD", "SM", "EU", "TB", "LU", "OS", "IR", "RU"}

BACKBONE = ("CA", "C1'", "C1*")


class Unreadable(ValueError):
    """A structure that cannot be read, said as a sentence for the page to show."""


def one_letter(name):
    """The one-letter code for a residue name, or "" when nothing here spells it.

    The twenty first, then the bases: no amino acid is written with a name a nucleotide uses, so
    the order only settles which table is asked first and never which answer comes back.
    """
    name = (name or "").strip().upper()
    return THREE.get(name) or NUCLEIC_LETTER.get(name, "")


def kind_of(names):
    """What a chain is made of: "protein", "DNA", "RNA" or "other"."""
    names = [n.strip().upper() for n in names]
    if not names:
        return "other"
    protein = sum(1 for n in names if n in THREE)
    if protein >= max(1, len(names) // 2):
        return "protein"
    nucleic = [n for n in names if n in NUCLEIC]
    if len(nucleic) >= max(1, len(names) // 2):
        return "DNA" if any(n.startswith("D") for n in nucleic) else "RNA"
    return "other"


def _pdb_rows(text):
    """(chain, residue number, insertion code, residue name) for one atom per residue, of a PDB.

    The first model only: a structure of twenty NMR models is twenty copies of one chain, and the
    sequence is the same in each. Alternate locations other than the first are skipped for the
    same reason.
    """
    for line in text.splitlines():
        if line.startswith("ENDMDL"):
            break
        if not line.startswith(("ATOM", "HETATM")):
            continue
        if line[12:16].strip().upper() not in BACKBONE:
            continue
        alt = line[16:17].strip()
        if alt and alt not in ("A", "1"):
            continue
        name = line[17:20].strip().upper()
        if name in SOLVENT or (line.startswith("HETATM") and name in IONS):
            continue
        chain = line[21:22].strip() or "_"
        try:
            number = int(line[22:26])
        except ValueError:
            continue
        yield chain, number, line[26:27].strip(), name


def _cif_rows(text):
    """The same, of an mmCIF: the atom_site loop, by the names of its own columns.

    The author's numbering is used where the file carries it, because that is the numbering a
    person reads off a paper and types into a form; label_seq_id, which counts from one, would
    renumber every chain silently.
    """
    lines = text.splitlines()
    # The columns in the order the file declares them, which is the order of the fields in every
    # row of the loop that follows. Every column is counted, including the ones this module does
    # not read, because what is wanted is each field's position.
    order, first_row = [], None
    for n, line in enumerate(lines):
        bare = line.strip()
        if bare.lower().startswith("_atom_site."):
            order.append(bare.split(".", 1)[1].strip().lower())
        elif order and bare and not bare.startswith("#"):
            first_row = n
            break
    if not order or first_row is None:
        return
    index = {name: n for n, name in enumerate(order)}
    model = index.get("pdbx_pdb_model_num")
    for n in range(first_row, len(lines)):
        line = lines[n]
        bare = line.strip()
        if not bare or bare.startswith("#") or bare.lower().startswith(("loop_", "_", "data_")):
            break
        parts = bare.split()
        if len(parts) < len(order):
            continue
        if model is not None and parts[model] not in ("1", "."):
            continue        # the first model only, as for a PDB
        if parts[index["label_atom_id"]].strip('"').upper() not in BACKBONE:
            continue
        alt = parts[index["label_alt_id"]] if "label_alt_id" in index else "."
        if alt not in (".", "?", "A", "1"):
            continue
        name = parts[index["label_comp_id"]].strip('"').upper()
        if name in SOLVENT or (parts[index.get("group_pdb", 0)].upper() == "HETATM"
                               and name in IONS):
            continue
        chain = (parts[index["auth_asym_id"]] if "auth_asym_id" in index
                 else parts[index.get("label_asym_id", 0)])
        chain = chain.strip('"') or "_"
        number = None
        for key in ("auth_seq_id", "label_seq_id"):
            if key in index and parts[index[key]] not in (".", "?"):
                try:
                    number = int(parts[index[key]])
                    break
                except ValueError:
                    continue
        if number is None:
            continue
        code = ""
        if "pdbx_pdb_ins_code" in index:
            got = parts[index["pdbx_pdb_ins_code"]]
            code = "" if got in (".", "?") else got
        yield chain, number, code, name


def read(path):
    """Every chain in this structure, in file order, each with its sequence.

    Returns [{id, kind, n, first, last, sequence, gaps, insertions}]. `sequence` is the residues
    as they are in the file, in their own order, with nothing filled in where the chain has a
    hole: a gap is counted and said rather than papered over, because a sequence with a hole
    silently closed is a different protein.
    """
    path = os.path.abspath(os.path.expanduser(path))
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read(80 * 1024 * 1024)
    except OSError as why:
        raise Unreadable("that file could not be read: %s" % why)
    if not text.strip():
        raise Unreadable("that file is empty")
    lower = path.lower()
    rows = list(_cif_rows(text) if lower.endswith((".cif", ".mmcif")) else _pdb_rows(text))
    if not rows and not lower.endswith((".cif", ".mmcif")):
        # A file named .pdb that is really an mmCIF is common enough to be worth reading rather
        # than refusing: a fold writes .cif and somebody renames it.
        rows = list(_cif_rows(text))
    if not rows:
        raise Unreadable(
            "no chain was found in that file. A structure is read by its atoms here, so one with "
            "no ATOM records, or none carrying an alpha carbon, is not one this page can read.")
    order, held = [], {}
    for chain, number, code, name in rows:
        if chain not in held:
            held[chain] = []
            order.append(chain)
        held[chain].append((number, code, name))
    out = []
    for chain in order:
        residues = held[chain]
        names = [name for _n, _c, name in residues]
        kind = kind_of(names)
        letters = "".join(one_letter(name) or "X" for name in names)
        numbers = [n for n, _c, _name in residues]
        gaps = sum(1 for a, b in zip(numbers, numbers[1:]) if b - a > 1)
        out.append({
            "id": chain, "kind": kind, "n": len(residues),
            "first": numbers[0], "last": numbers[-1],
            "sequence": letters,
            # The residue numbers beside the letters, in step, so nothing downstream has to pair
            # them by counting. residues() hands the pairs over directly.
            "numbers": numbers,
            "unknown": letters.count("X"),
            "gaps": gaps,
            "insertions": sum(1 for _n, code, _name in residues if code),
        })
    return out


def residues(path, want=()):
    """{chain: [(residue number, letter)]}, which is what a ruler is drawn from.

    Given rather than left to be rebuilt: a page that has a chain's letters and, separately, its
    residue numbers, has to pair them, and the obvious pairing is by position. That is wrong the
    moment the two readings disagree about how many residues there are, and it fails silently,
    drawing the whole chain as question marks or, worse, labeling residue 30 with residue 24's
    letter. Pairing happens once, here, where both halves are read from the same pass over the
    file and cannot come apart.
    """
    out = {}
    for one in read(path):
        if want and one["id"] not in want:
            continue
        out[one["id"]] = list(zip(one["numbers"], one["sequence"]))
    return out


def chain_named(chains, want):
    """The chain with this id, or the first protein chain when none is named."""
    if want:
        for one in chains:
            if one["id"] == str(want):
                return one
        return None
    for one in chains:
        if one["kind"] == "protein":
            return one
    return chains[0] if chains else None


def sampleable(one):
    """What is wrong with sampling this chain, or "" when nothing is.

    Said here rather than at submit so the page can gray a chain out as soon as it is read, with
    the reason beside it.
    """
    if one is None:
        return "that chain is not in the file."
    if one["kind"] != "protein":
        return ("chain %s is %s. BioEmu samples one protein chain: it has no alphabet for a "
                "nucleic acid." % (one["id"], one["kind"]))
    if one["unknown"]:
        return ("chain %s holds %d residue(s) that are not one of the twenty amino acids. They "
                "would be sampled as nothing in particular, so the chain is refused rather than "
                "guessed at." % (one["id"], one["unknown"]))
    if one["n"] < 5:
        return "chain %s is %d residues, which is too short to sample." % (one["id"], one["n"])
    return ""


#: What a mutation may be written as: the residue that is there, its number in the structure's own
#: numbering, and what it becomes. "W23A", and "W23a" and "Trp23Ala" are not accepted, because a
#: form that took three spellings would have three ways to be wrong about which residue it meant.
def mutate(sequence, first, asked):
    """`sequence` with the mutations in `asked` applied, by the structure's own numbering.

    Returns (the sequence, what was done, what was wrong). Numbering is the chain's own, so a
    paper saying W23A is typed as W23A rather than counted from the start of the construct; the
    letter that is already there is checked, which is the whole reason to write it.
    """
    letters = list(sequence or "")
    done, bad = [], []
    for raw in [w.strip() for w in str(asked or "").replace(",", " ").split() if w.strip()]:
        word = raw.upper()
        if len(word) < 3 or not word[0].isalpha() or not word[-1].isalpha():
            bad.append("%s is not a mutation: write it as W23A, the residue there, its number in "
                       "this structure, and what it becomes." % raw)
            continue
        try:
            number = int(word[1:-1])
        except ValueError:
            bad.append("%s is not a mutation: write it as W23A." % raw)
            continue
        at = number - int(first)
        if at < 0 or at >= len(letters):
            bad.append("%s names residue %d, and this chain runs %d to %d."
                       % (raw, number, first, first + len(letters) - 1))
            continue
        if letters[at] != word[0]:
            bad.append("%s says residue %d is %s, and it is %s."
                       % (raw, number, word[0], letters[at]))
            continue
        if word[-1] not in THREE.values():
            bad.append("%s becomes %s, which is not one of the twenty amino acids." % (raw, word[-1]))
            continue
        letters[at] = word[-1]
        done.append(word)
    return "".join(letters), done, bad


#: How a chain's kind is named on a form that folds things. The folding pages take protein, DNA
#: and RNA as separate kinds, and what this reads off a structure is exactly those three.
AS_ENTITY = {"protein": "protein", "DNA": "dna", "RNA": "rna"}


def entities(path, want=(), named=""):
    """The chains of a structure as rows a folding form can take, and what cannot be a row.

    Returns (rows, left out). A row is {kind, name, sequence, copies, chain, residues}, which is
    the shape hope_labs/folding/submit.py builds a fold from, so dropping a structure on a
    folding page is the same thing as typing its chains in by hand, done correctly.

    Identical chains are folded as copies rather than as separate rows: a homodimer in a file is
    two chains of one sequence, and a fold asked for it as two rows would be asked to predict a
    complex of two things that are the same thing. That is also how the folding pages' own form
    expresses it, and how Boltz-2 reads it.
    """
    chains = read(path)
    rows, dropped, seen = [], [], {}
    for one in chains:
        if want and one["id"] not in want:
            continue
        kind = AS_ENTITY.get(one["kind"])
        if not kind:
            dropped.append("chain %s is %s, which is not something these pages fold"
                           % (one["id"], one["kind"]))
            continue
        if one["unknown"]:
            dropped.append("chain %s holds %d residue(s) that are not one of the twenty amino "
                           "acids" % (one["id"], one["unknown"]))
            continue
        key = (kind, one["sequence"])
        if key in seen:
            rows[seen[key]]["copies"] += 1
            rows[seen[key]]["chain"] += "," + one["id"]
            continue
        seen[key] = len(rows)
        rows.append({"kind": kind, "name": "%s_%s" % (named or stem(path), one["id"]),
                     "sequence": one["sequence"], "copies": 1, "chain": one["id"],
                     "residues": one["n"], "first": one["first"]})
    return rows, dropped


def stem(path):
    """The file's name without its suffix, as a row or a run can be named after it."""
    return os.path.splitext(os.path.basename(str(path or "structure")))[0]


#: How close two heavy atoms have to be to count as touching. The figure the lab's docking
#: reports use for a contact, so an interface taken off a bound structure here and one read off a
#: docked pose there are the same residues by the same rule.
CONTACT = 4.5


def heavy_atoms(path, chains=()):
    """[(chain, residue number, x, y, z)] for every heavy atom of the chains asked for.

    Hydrogens are left out because a contact is defined on heavy atoms, and coordinates that are
    not finite are left out because a model writes the atoms it never placed as NaN: a real
    design carried 1,219 of those in 2,393 atoms, and one of them poisons every distance it is
    put into.
    """
    out = []
    lower = str(path).lower()
    rows = list(_cif_atoms(path) if lower.endswith((".cif", ".mmcif")) else _pdb_atoms(path))
    if not rows and not lower.endswith((".cif", ".mmcif")):
        # The same fallback read() has, and for the same reason: a fold writes .cif and somebody
        # renames it .pdb. Without it here, a chain that read() could see had no atoms at all,
        # and the pages that name a chain by its atoms named nothing.
        rows = list(_cif_atoms(path))
    for chain, number, name, element, x, y, z in rows:
        if chains and chain not in chains:
            continue
        if element == "H" or name.startswith("H"):
            continue
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            continue
        out.append((chain, number, x, y, z))
    return out


def _pdb_atoms(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("ENDMDL"):
                    break
                if not line.startswith(("ATOM", "HETATM")):
                    continue
                name = line[12:16].strip().upper()
                if line[17:20].strip().upper() in SOLVENT:
                    continue
                alt = line[16:17].strip()
                if alt and alt not in ("A", "1"):
                    continue
                try:
                    yield (line[21:22].strip() or "_", int(line[22:26]), name,
                           line[76:78].strip().upper(), float(line[30:38]), float(line[38:46]),
                           float(line[46:54]))
                except ValueError:
                    continue
    except OSError:
        return


def _cif_atoms(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return
    order, first = [], None
    for n, line in enumerate(lines):
        bare = line.strip()
        if bare.lower().startswith("_atom_site."):
            order.append(bare.split(".", 1)[1].strip().lower())
        elif order and bare and not bare.startswith("#"):
            first = n
            break
    if first is None:
        return
    at = {name: n for n, name in enumerate(order)}
    chain_col = at.get("auth_asym_id", at.get("label_asym_id"))
    for line in lines[first:]:
        bare = line.strip()
        if not bare or bare.startswith(("#", "_", "loop_", "data_")):
            break
        parts = bare.split()
        if len(parts) < len(order):
            continue
        if parts[at.get("label_comp_id", 0)].strip('"').upper() in SOLVENT:
            continue
        number = None
        for key in ("auth_seq_id", "label_seq_id"):
            if key in at and parts[at[key]] not in (".", "?"):
                try:
                    number = int(parts[at[key]])
                    break
                except ValueError:
                    continue
        if number is None:
            continue
        try:
            xyz = tuple(float(parts[at[k]]) for k in ("cartn_x", "cartn_y", "cartn_z"))
        except (KeyError, ValueError):
            continue
        yield ((parts[chain_col].strip('"') if chain_col is not None else "_"), number,
               parts[at.get("label_atom_id", 0)].strip('"').upper(),
               parts[at.get("type_symbol", 0)].strip('"').upper() if "type_symbol" in at else "",
               xyz[0], xyz[1], xyz[2])


def interface(path, keep, against=(), cutoff=CONTACT):
    """Which residues of `keep` are within `cutoff` of the chains in `against`.

    The question a person with a bound structure is really asking when a page wants a site: not
    "which residues do I believe matter" but "which ones does the thing that binds here touch".
    Returns {chain: [residue numbers]}, in order.

    The atoms are bucketed into cells of the cutoff first, so this is linear in the atom count
    rather than quadratic: a 20,000-atom structure against a 2,000-atom partner is forty million
    pairs done the obvious way, which is a page that appears to have hung.
    """
    chains = read(path)
    keep = [c for c in (keep or []) if c]
    if not keep:
        keep = [one["id"] for one in chains if one["kind"] == "protein"][:1]
    against = [c for c in (against or []) if c] or [one["id"] for one in chains
                                                   if one["id"] not in keep]
    if not against:
        return {}, "this structure holds nothing but the chain(s) kept, so there is no bound "\
                   "partner to read a site off."
    mine = heavy_atoms(path, keep)
    theirs = heavy_atoms(path, against)
    if not mine or not theirs:
        return {}, "no heavy atoms were found for one of those chains."
    cells = {}
    for _chain, _number, x, y, z in theirs:
        cells.setdefault((int(x // cutoff), int(y // cutoff), int(z // cutoff)), []).append((x, y, z))
    found, limit = {}, cutoff * cutoff
    for chain, number, x, y, z in mine:
        if number in found.get(chain, ()):
            continue
        cx, cy, cz = int(x // cutoff), int(y // cutoff), int(z // cutoff)
        near = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for (ox, oy, oz) in cells.get((cx + dx, cy + dy, cz + dz), ()):
                        if (x - ox) ** 2 + (y - oy) ** 2 + (z - oz) ** 2 <= limit:
                            near = True
                            break
                    if near:
                        break
                if near:
                    break
            if near:
                break
        if near:
            found.setdefault(chain, []).append(number)
    for chain in found:
        found[chain] = sorted(set(found[chain]))
    total = sum(len(v) for v in found.values())
    return found, ("%d residue(s) of chain %s within %.1f A of chain %s"
                   % (total, ", ".join(keep), cutoff, ", ".join(against)))


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print(__doc__)
        return 2
    try:
        chains = read(args[0])
    except Unreadable as why:
        print(why)
        return 1
    for one in chains:
        note = sampleable(one)
        print("chain %-2s %-7s %4d residues  %s to %s%s%s"
              % (one["id"], one["kind"], one["n"], one["first"], one["last"],
                 "  %d gap(s)" % one["gaps"] if one["gaps"] else "",
                 "  " + note if note else ""))
        print("   " + one["sequence"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
