"""The Prepare stage: from a file somebody gave us to a structure an engine can build on.

Order, and what each step needs on the node:

Every step writes what it did into prep_report.json; the page shows that as the checks strip.
A step whose tool is missing is skipped and says so; the report is honest about it.

Run as a job on the site's prep partition (the server submits it and the page polls; inline
only in a dry run or without sbatch), or by hand:  python3 -m hopemd.prepare <rundir>
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .structure import (read_structure, inventory, filter_pdb, split_chains, classify, MODIFIED_AMINO,
                        ter_at_breaks, strip_5p_phosphates, normalise_nucleic_names, nucleic_cations, default_split,
                        BOUND_ION_A, b_is_plddt, hetero_groups, group_label, ions_pdb, ter_between_chains,
                        water_groups, near_waters, waters_pdb, heavy_xyz, NEAR_WATER_A, modified_residues, map_modified,
                        mapped_note, stretched_bonds, stretched_note, residue_ids, cap_gaps, capped_note, hold_caps,
                        return_caps, cap_records, caps_in)

CLASH_A = 2.2   # heavy-atom pairs closer than this, not bonded, count as clashes


#: The longest gap built as a loop (model_loops_max asks for less, never more). A longer one is a domain's worth of
#: guessing, and is left open, its two ends capped.
MAX_LOOP = 12
#: PDBFixer's seeds for building loops, tried in turn. A seed makes the loop it builds the same every time (left
#: unseeded, 7A3R's loops came out differently run to run); a further seed is tried only when no relaxation can make
#: the first seed's loops fit.
LOOP_SEEDS = (1, 2, 3)
#: What the caller names the setting that builds loops, for the message that says how to prepare without them. A
#: flow that runs this stage under its own name for the setting sets its own.
LOOPS_SETTING = "model loops up to (residues)"
#: Measured residues either side of a built loop that may move a little (on a spring) so the loop can close: the
#: loop's stems.
FLANK = 2
#: The relaxation attempts, in order: the stems' spring (kcal/mol/Å²) and the warm spell (ps). The first that leaves
#: every peptide bond in shape is kept.
RELAX_SCHEDULE = ((10.0, 2.0), (5.0, 5.0), (2.0, 10.0), (1.0, 10.0), (0.5, 20.0))
#: What every peptide bond in or beside a built loop must measure after relaxation, as ADapt's rebuilding checks its
#: loops: the C-N bond (Å), the two backbone angles across it (degrees; ideal 1.33 Å, 116 and 122), CA to CA (Å; 3.80
#: across a trans bond in crystal structures, a little more once minimized in ff14SB) and the twist, omega
#: (CA-C-N-CA), within `trans` degrees of 180. A cis bond, within `trans` of 0, passes only before a proline, with CA
#: to CA in `ca_ca_cis`. Every built residue with a CB is of the L hand: its improper N-C-CA-CB within `hand` degrees
#: of HAND, and a Thr or Ile has its CB of the natural hand as well. And no heavy atom of a built residue is nearer
#: than `contact` Å to an amino acid's heavy atom more than three bonds away (a hydrogen bond is 2.6 Å and up).
JUNCTION = {"bond": (1.30, 1.37), "ca_c_n": (105.0, 130.0), "c_n_ca": (110.0, 135.0),
            "ca_ca": (3.70, 3.95), "ca_ca_cis": (2.70, 3.05), "trans": 30.0, "hand": 25.0, "contact": 2.5}
#: The improper N-C-CA-CB of an L residue, in degrees (114 to 129 in measured structures), and its N-C-CA-HA (-119,
#: within 1.4 on the 361 residues of a prepared 7A3R chain A). Held on HA as well as CB, a CA stays tetrahedral: held
#: on CB alone, one in a loop often went nearly flat (improper 160 to 165 degrees, N-CA-C over 120) and stayed so
#: through every attempt.
HAND = 122.0
HAND_HA = -119.0
#: The chiral CB of Thr and Ile: (X - CB) x (Y - CB) . (CA - CB) is positive in the natural form and negative in the
#: allo form.
CHIRAL_CB = {"THR": ("OG1", "CG2"), "ILE": ("CG1", "CG2")}
#: The restraints that hold a built loop's chemistry while it relaxes and in the minimization. Both are
#: flat-bottomed, so a loop inside their wells feels neither: each peptide bond in or beside it trans (omega within
#: TRANS_SLACK degrees of 180), and each built residue and stem with a CB of the L hand (its impropers within
#: HAND_SLACK degrees of HAND and HAND_HA), at RESTRAINT_K kJ/mol/rad² outside the well.
TRANS_SLACK = 15.0
HAND_SLACK = 20.0
RESTRAINT_K = 400.0
#: The heavy atoms of an amino acid's backbone, with the C-terminal oxygen; every other heavy atom is its side chain.
BACKBONE = frozenset({"N", "CA", "C", "O", "OXT"})
#: How far from a moving atom (built, or a stem) the relaxation keeps the structure, in nm: the 1.2 nm cutoff and
#: 0.8 nm of room for the loop to move in. Beyond it an atom cannot reach a moving one, and leaving it out of the
#: simulation changes no force on one.
NEIGHBOURHOOD_NM = 2.0
#: The names an amino acid can carry between here and the prepared file: the standard twenty, and the names pdb2pqr
#: and the Amber force fields give to protonation states and disulfides.
AMINO_ACIDS = frozenset({
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE", "LEU", "LYS", "MET", "PHE", "PRO", "SER",
    "THR", "TRP", "TYR", "VAL", "HID", "HIE", "HIP", "HSD", "HSE", "HSP", "CYX", "CYM", "ASH", "GLH", "LYN", "ARN",
    "TYM"})


class PrepareRefused(ValueError):
    """A structure the stage cannot go on with, with the sentence for the page."""


class _Skip(Exception):
    """A step that is not done this time, raised where the step's own work begins.

    One step is skipped for one reason - the molecules were prepared before they arrived - and it
    is skipped from inside the try that would otherwise do it, so the step's "it did not happen"
    paths stay in one place.
    """


def _log(report: dict, msg: str) -> None:
    report.setdefault("log", []).append(msg)


def trim_low_plddt(pdb_text: str, threshold: float) -> tuple[str, dict]:
    """Remove terminal residues whose CA B-factor (pLDDT) is under the threshold, per chain,
    stopping at the first residue that passes. Internal low-confidence stretches are kept."""
    if threshold <= 0:
        return pdb_text, {}
    lines = pdb_text.splitlines()
    ca: dict[str, list[tuple[int, float]]] = {}
    for l in lines:
        if l.startswith("ATOM") and l[12:16].strip() == "CA":
            try:
                ca.setdefault(l[21], []).append((int(l[22:26]), float(l[60:66])))
            except ValueError:
                pass
    drop: dict[str, set[int]] = {}
    for ch, seq in ca.items():
        seq.sort()
        d = set()
        for resi, b in seq:
            if b < threshold: d.add(resi)
            else: break
        for resi, b in reversed(seq):
            if b < threshold: d.add(resi)
            else: break
        if d:
            drop[ch] = d
    if not drop:
        return pdb_text, {}
    out = [l for l in lines if not (l.startswith(("ATOM", "HETATM")) and l[21] in drop
                                    and _int(l[22:26]) in drop[l[21]])]
    return "\n".join(out) + "\n", {ch: len(v) for ch, v in drop.items()}


def _int(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return -10**6


def residue_key(residue) -> str:
    """An OpenMM residue as the text names it: its number and its insertion code, "1H" or "60A".

    The text keys every residue by columns 23-27, number and insertion code together, and OpenMM keeps
    the two apart (residue.id is the number alone). Keyed by the number, thrombin's light chain (PDB 1PPB)
    has nine residues called L:1, and the threonine at L:1H was handed the disulfide state of the
    cysteine at L:1 ("Illegal variant for THR residue: CYX"), so the minimization failed."""
    return f"{str(residue.id).strip()}{(getattr(residue, 'insertionCode', '') or '').strip()}"


def bonded_successors(pdb_text: str, max_cn_A: float = 1.7, max_op_A: float = 2.0) -> set[tuple[str, str]]:
    """Keys (chain, resid) of residues whose backbone is bonded to the next residue's in the file (a
    protein's C to N, a nucleic acid's O3′ to P). A SEQRES gap between two such residues is a numbering
    artifact, not a missing loop."""
    prev = None; out = set()
    c_of: dict[tuple[str, str], tuple] = {}
    order: list[tuple[str, str]] = []
    n_of: dict[tuple[str, str], tuple] = {}
    o3_of: dict[tuple[str, str], tuple] = {}
    p_of: dict[tuple[str, str], tuple] = {}
    for l in pdb_text.splitlines():
        if not l.startswith("ATOM"):
            continue
        key = (l[21], l[22:27].strip()); name = l[12:16].strip()
        if not order or order[-1] != key:
            order.append(key)
        try:
            xyz = (float(l[30:38]), float(l[38:46]), float(l[46:54]))
        except ValueError:
            continue
        if name == "C": c_of[key] = xyz
        if name == "N": n_of[key] = xyz
        if name == "O3'": o3_of[key] = xyz
        if name == "P": p_of[key] = xyz
    for a, b in zip(order, order[1:]):
        if a[0] == b[0] and a in c_of and b in n_of:
            if sum((x - y) ** 2 for x, y in zip(c_of[a], n_of[b])) < max_cn_A ** 2:
                out.add(a)
        if a[0] == b[0] and a in o3_of and b in p_of:
            if sum((x - y) ** 2 for x, y in zip(o3_of[a], p_of[b])) < max_op_A ** 2:
                out.add(a)
    return out


def _angle(a, b, c) -> float:
    import math
    v1 = [a[i] - b[i] for i in range(3)]
    v2 = [c[i] - b[i] for i in range(3)]
    n = math.sqrt(sum(x * x for x in v1)) * math.sqrt(sum(x * x for x in v2))
    return math.degrees(math.acos(max(-1.0, min(1.0, sum(x * y for x, y in zip(v1, v2)) / n))))


def junction_geometry(path, built) -> list[str]:
    """Every peptide bond in or beside a built loop, and every built residue and stem, checked against JUNCTION.
    Returns what is out of range, as text, one line per bond, residue or contact. `built` holds (chain, number,
    insertion code) of each built residue.

    Bond lengths alone are not enough: PDBFixer closed 7A3R's loops at 1.31-1.36 Å with the CA-C-N angle at 42
    degrees, the N folded back onto the CA. Lengths and angles together are not enough either: they passed 7A3R's
    loops with bonds cis (274-275 at -2 degrees) and twisted (194-195 at -95), and a cis bond that is not before a
    proline stays cis through MD. So each bond's twist and CA to CA are checked, each residue's hand, and how near the
    built atoms come to the rest."""
    import math
    import numpy as np
    from .structure import _torsion
    at, order, names, heavy = {}, [], {}, []
    for line in open(path, errors="replace"):
        if line.startswith(("ATOM", "HETATM")):
            k = (line[21], line[22:26].strip(), line[26].strip())
            if not order or order[-1] != k:
                order.append(k)
            xyz = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
            name = line[12:16].strip()
            at[k + (name,)] = xyz
            names[k] = line[17:20].strip()
            el = (line[76:78].strip() or name.lstrip("0123456789")[:1]).upper()
            if names[k] in AMINO_ACIDS and el not in ("H", "D"):
                heavy.append((k, name, xyz))
    made = set(built)
    bad, links = [], []
    for a, b in zip(order, order[1:]):
        if a[0] != b[0] or not (a in made or b in made):
            continue
        ca1, c1, n2, ca2 = (at.get(a + ("CA",)), at.get(a + ("C",)), at.get(b + ("N",)), at.get(b + ("CA",)))
        if None in (ca1, c1, n2, ca2):
            continue
        d = math.dist(c1, n2)
        if d > 2.5:
            continue                         # a chain break left open, not a junction
        links.append((a, b))
        t1, t2 = _angle(ca1, c1, n2), _angle(c1, n2, ca2)
        w, dca = _torsion(ca1, c1, n2, ca2), math.dist(ca1, ca2)
        cis = abs(w) <= JUNCTION["trans"] and names[b] == "PRO"
        lo, hi = JUNCTION["ca_ca_cis" if cis else "ca_ca"]
        if not (JUNCTION["bond"][0] <= d <= JUNCTION["bond"][1]
                and JUNCTION["ca_c_n"][0] <= t1 <= JUNCTION["ca_c_n"][1]
                and JUNCTION["c_n_ca"][0] <= t2 <= JUNCTION["c_n_ca"][1]
                and (cis or abs(w) >= 180.0 - JUNCTION["trans"])
                and lo <= dca <= hi):
            bad.append(f"{a[0]}{a[1]}{a[2]}-{b[1]}{b[2]}: C-N {d:.2f} Å, CA-C-N {t1:.0f}, C-N-CA {t2:.0f}, omega {w:.0f}, "
                       f"CA-CA {dca:.2f} Å")
    # the built residues and their stems (which move with them): the hand of every one, and how far a built one's
    # CA is from the ideal
    moved = set()
    for i, k in enumerate(order):
        if k in made:
            moved.update(r for r in order[max(0, i - FLANK):i + FLANK + 1] if r[0] == k[0])
    for k in order:
        if k not in moved:
            continue
        n, c, ca, cb = (at.get(k + (x,)) for x in ("N", "C", "CA", "CB"))
        if None not in (n, c, ca, cb):
            imp = _torsion(n, c, ca, cb)
            if imp <= 0:
                bad.append(f"{k[0]}{k[1]}{k[2]} {names[k]}: of the D hand (improper N-C-CA-CB {imp:.0f})")
            elif k in made and abs(imp - HAND) > JUNCTION["hand"]:
                bad.append(f"{k[0]}{k[1]}{k[2]} {names[k]}: CA distorted (improper N-C-CA-CB {imp:.0f}, "
                           f"{HAND - JUNCTION['hand']:.0f} to {HAND + JUNCTION['hand']:.0f} allowed)")
        sub = CHIRAL_CB.get(names[k])
        if sub and None not in (ca, at.get(k + ("CB",)), at.get(k + (sub[0],)), at.get(k + (sub[1],))):
            o = np.asarray(at[k + ("CB",)])
            x, y = (np.asarray(at[k + (q,)]) - o for q in sub)
            if float(np.dot(np.cross(x, y), np.asarray(ca) - o)) <= 0:
                bad.append(f"{k[0]}{k[1]}{k[2]} {names[k]}: its CB of the allo hand")
    near = _closest_contact(heavy, made, links)
    if near and near[0] < JUNCTION["contact"]:
        bad.append(f"{near[1]}: {near[0]:.2f} Å apart, nearer than {JUNCTION['contact']:g} Å")
    return bad


def _closest_contact(heavy, made, links):
    """(distance, 'A193 CG .. A160 O') of the closest approach of a built residue's heavy atom to an amino acid's heavy
    atom more than three bonds away, or None. heavy: [(residue, name, xyz)]; links: the peptide bonds between
    residues, as (residue, next residue). Bonds are read from the distances: within a residue under 1.9 Å, C to N
    along `links`, and SG to SG under 2.3 Å (a disulfide)."""
    import math
    import numpy as np
    new = [i for i, h in enumerate(heavy) if h[0] in made]
    if not new or len(heavy) < 2:
        return None
    xyz = np.array([h[2] for h in heavy], dtype=float)
    close = []
    for i in new:
        d = np.sqrt(((xyz - xyz[i]) ** 2).sum(1))
        d[i] = np.inf
        for j in np.nonzero(d < 4.0)[0]:
            close.append((float(d[j]), i, int(j)))
    if not close:
        return None
    # the bonds among the atoms that came close, and their neighbors
    where = sorted({j for _d, i, j in close} | {i for _d, i, j in close})
    pocket = np.array([xyz[j] for j in where])
    region = [j for j in range(len(heavy)) if float(np.min(((pocket - xyz[j]) ** 2).sum(1))) < 36.0]
    by_res, named, bonded = {}, {}, {j: set() for j in region}
    for j in region:
        by_res.setdefault(heavy[j][0], []).append(j)
        named[heavy[j][0], heavy[j][1]] = j

    def bond(p, q):
        bonded[p].add(q)
        bonded[q].add(p)
    for js in by_res.values():
        for p in js:
            for q in js:
                if p < q and math.dist(heavy[p][2], heavy[q][2]) < 1.9:
                    bond(p, q)
    for a, b in links:
        if (a, "C") in named and (b, "N") in named:
            bond(named[a, "C"], named[b, "N"])
    sg = [j for j in region if heavy[j][1] == "SG"]
    for p in sg:
        for q in sg:
            if p < q and math.dist(heavy[p][2], heavy[q][2]) < 2.3:
                bond(p, q)
    for dist, i, j in sorted(close):
        if i not in bonded or j not in bonded:
            continue
        seen, edge = {i}, {i}
        for _ in range(3):
            edge = {y for x in edge for y in bonded.get(x, ())} - seen
            seen |= edge
        if j in seen:
            continue
        a, b = heavy[i], heavy[j]
        return dist, f"{a[0][0]}{a[0][1]}{a[0][2]} {a[1]} .. {b[0][0]}{b[0][1]}{b[0][2]} {b[1]}"
    return None


def _l_hand(topology, xyz, made) -> int:
    """xyz (nm, an n x 3 array) with every built residue's side chain set on the L hand before the relaxation, as
    ADapt sets its loops before refining them; returns how many residues were set right. PDBFixer's soft dynamics
    leave some CA of the D hand (seven of the sixteen it builds in 7A3R chain A), and a hydrogen placed on such a CA
    takes the place CB belongs in. So a residue whose improper N-C-CA-CB is out of JUNCTION's range has its side chain
    turned rigidly about CA until CB lies where an L residue's would, from N, CA and C (the constants place CB with
    ideal angles; not for Pro, whose ring a turn would open at N), and a Thr or Ile whose CB has the allo hand has two
    of its substituents swapped (CD1 going with CG1)."""
    import math
    import numpy as np
    from .structure import _torsion

    def key(r):
        return (r.chain.id, str(r.id).strip(), (r.insertionCode or "").strip())
    righted = 0
    for r in topology.residues():
        if key(r) not in made:
            continue
        at = {a.name: a.index for a in r.atoms() if a.element is not None and a.element.symbol != "H"}
        if not all(x in at for x in ("N", "CA", "C", "CB")):
            continue
        n, ca, c, cb = (xyz[at[x]].copy() for x in ("N", "CA", "C", "CB"))
        imp = _torsion(n, c, ca, cb)
        turned = False
        if r.name != "PRO" and (imp <= 0 or abs(imp - HAND) > JUNCTION["hand"]):
            b, d = (ca - n) * 10.0, (c - ca) * 10.0       # in A: the constants hold there, not in nm
            want = -0.58273431 * np.cross(b, d) + 0.56802827 * b - 0.54067466 * d
            have = cb - ca
            axis = np.cross(have, want)
            sin, cos = float(np.linalg.norm(axis)), float(np.dot(have, want))
            if sin < 1e-9 * float(np.linalg.norm(have) * np.linalg.norm(want)):
                axis = np.cross(have, b)         # CB straight opposite: any axis across it
                sin = 0.0
            axis = axis / float(np.linalg.norm(axis))
            angle = math.atan2(sin, cos)
            if abs(angle) > 1e-6:
                kx = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
                turn = np.eye(3) + math.sin(angle) * kx + (1 - math.cos(angle)) * kx.dot(kx)
                side = [i for name, i in at.items() if name not in BACKBONE]
                xyz[side] = (xyz[side] - ca).dot(turn.T) + ca
                turned = True
        q = CHIRAL_CB.get(r.name)
        if q and all(x in at for x in q):
            o = xyz[at["CB"]]
            x, y = (xyz[at[s]] - o for s in q)
            if float(np.dot(np.cross(x, y), xyz[at["CA"]] - o)) <= 0:
                px, py = xyz[at[q[0]]].copy(), xyz[at[q[1]]].copy()
                xyz[at[q[0]]], xyz[at[q[1]]] = py, px
                if "CD1" in at:
                    xyz[at["CD1"]] = xyz[at["CD1"]] + py - px
                turned = True
        righted += turned
    return righted


def _ha_opposite(topology, xyz, made) -> int:
    """xyz (nm) with the HA of every built residue with a CB put opposite its CA's three other substituents, 0.109 nm
    from CA, where the L hand has it. Modeller lays a new hydrogen at random and minimizes it, and on a CA that
    PDBFixer left nearly flat it can settle on CB's side. With HA held as well (HAND_HA), 7A3R chain A's loops passed
    on the second attempt with HA placed here first, and on the third without. Returns how many were placed."""
    import numpy as np

    def key(r):
        return (r.chain.id, str(r.id).strip(), (r.insertionCode or "").strip())
    placed = 0
    for r in topology.residues():
        if key(r) not in made:
            continue
        at = {a.name: a.index for a in r.atoms()}
        if not all(x in at for x in ("N", "CA", "C", "CB", "HA")):
            continue
        ca = xyz[at["CA"]].copy()
        u = sum((xyz[at[x]] - ca) / np.linalg.norm(xyz[at[x]] - ca) for x in ("N", "C", "CB"))
        if np.linalg.norm(u) > 1e-6:
            xyz[at["HA"]] = ca - 0.109 * u / np.linalg.norm(u)
            placed += 1
    return placed


def _hold_chemistry(system, topology, made, stems=()) -> tuple[int, int]:
    """The restraints that keep a built loop's chemistry while it relaxes, added to `system`: every peptide bond in or
    beside it trans, and every built residue with a CB, and every stem, of the L hand, its CB and its HA each held to
    their side (TRANS_SLACK, HAND_SLACK, HAND, HAND_HA). PDBFixer's soft force field leaves loops with bonds anywhere
    from cis to trans, and Amber's force field keeps whatever it is given: its barrier holds a cis bond cis. Unheld, a
    stem on its spring was turned to the D hand (7A3R's A:275). Returns (bonds, residues) held."""
    import math
    import openmm

    def key(r):
        return (r.chain.id, str(r.id).strip(), (r.insertionCode or "").strip())

    trans = openmm.CustomTorsionForce("0.5*k_trans*max(0, d-w_trans)^2; d=min(dt, 6.283185307179586-dt); "
                                      "dt=abs(theta-3.141592653589793)")
    trans.addGlobalParameter("k_trans", RESTRAINT_K)
    trans.addGlobalParameter("w_trans", math.radians(TRANS_SLACK))
    hand = openmm.CustomTorsionForce("0.5*k_hand*max(0, abs(d)-w_hand)^2; d=atan2(sin(theta-t0), cos(theta-t0))")
    hand.addGlobalParameter("k_hand", RESTRAINT_K)
    hand.addGlobalParameter("w_hand", math.radians(HAND_SLACK))
    hand.addPerTorsionParameter("t0")
    bonded = {frozenset((a.index, b.index)) for a, b in topology.bonds()}
    res = list(topology.residues())

    def atom(r, name):
        return next((a.index for a in r.atoms() if a.name == name), None)
    for a, b in zip(res, res[1:]):
        if a.chain.index != b.chain.index or not (key(a) in made or key(b) in made):
            continue
        idx = (atom(a, "CA"), atom(a, "C"), atom(b, "N"), atom(b, "CA"))
        if None not in idx and frozenset((idx[1], idx[2])) in bonded:
            trans.addTorsion(*idx, [])
    held = 0
    for r in res:
        if key(r) in made or key(r) in stems:
            idx = (atom(r, "N"), atom(r, "C"), atom(r, "CA"), atom(r, "CB"))
            if None not in idx:
                hand.addTorsion(*idx, [math.radians(HAND)])
                held += 1
                ha = atom(r, "HA")
                if ha is not None:
                    hand.addTorsion(idx[0], idx[1], idx[2], ha, [math.radians(HAND_HA)])
    system.addForce(trans)
    system.addForce(hand)
    return trans.getNumTorsions(), held


class _one_cpu_thread:
    """OpenMM's CPU platform, its contexts made with one thread while this lasts (the default it had is put back
    after). Summed over several threads, in whatever order they finish, the same relaxation came out differently on
    two runs (7A3R's loops passed their junction checks on the fourth attempt one time and the third the next), and so
    did the hydrogens Modeller places, whatever DeterministicForces said; on one thread both are the same every time."""

    def __enter__(self):
        import openmm
        self.cpu = openmm.Platform.getPlatformByName("CPU")
        self.was = self.cpu.getPropertyDefaultValue("Threads")
        self.cpu.setPropertyDefaultValue("Threads", "1")
        return self.cpu

    def __exit__(self, *exc):
        self.cpu.setPropertyDefaultValue("Threads", self.was)
        return False


class _seeded_random:
    """Python's random module seeded for what runs inside, and its state put back after. OpenMM's Modeller.addHydrogens
    lays each new hydrogen at a random place and then minimizes it, so unseeded the same structure came out of the
    relaxation two ways on two runs (7A3R's loops passed on the fourth attempt one time and the third the next)."""

    def __init__(self, seed: int):
        self.seed = seed

    def __enter__(self):
        import random
        self.state = random.getstate()
        random.seed(self.seed)

    def __exit__(self, *exc):
        import random
        random.setstate(self.state)
        return False


def _neighbourhood(system, xyz_nm, moving, radius_nm):
    """(a copy of `system` holding only the atoms within radius_nm of a moving one, the indices it kept, in order),
    or None when the system has a force this does not copy. Bonded terms and nonbonded exceptions are kept where all
    their atoms are, the torsion restraints with them; the masses, the restraints and the nonbonded method are the
    system's own. With the radius past
    the cutoff, every force on a moving atom is what it was in the whole system (measured on 7A3R's loops: the same to
    1 part in 10^5), and an attempt on one loop of a large protein simulates a fraction of it."""
    import numpy as np
    import openmm
    xyz = np.asarray(xyz_nm, dtype=float)
    near = np.zeros(len(xyz), dtype=bool)
    mv = np.asarray(sorted(moving), dtype=int)
    for chunk in np.array_split(mv, max(1, len(mv) // 256)):
        if len(chunk):
            near |= (((xyz[:, None, :] - xyz[None, chunk, :]) ** 2).sum(-1) < radius_nm ** 2).any(axis=1)
    keep = [int(i) for i in np.nonzero(near)[0]]
    new = {old: i for i, old in enumerate(keep)}
    sub = openmm.System()
    for old in keep:
        sub.addParticle(system.getParticleMass(old))
    for f in system.getForces():
        if isinstance(f, openmm.HarmonicBondForce):
            g = openmm.HarmonicBondForce()
            for t in range(f.getNumBonds()):
                a, b, r0, k = f.getBondParameters(t)
                if a in new and b in new:
                    g.addBond(new[a], new[b], r0, k)
        elif isinstance(f, openmm.HarmonicAngleForce):
            g = openmm.HarmonicAngleForce()
            for t in range(f.getNumAngles()):
                a, b, c, th, k = f.getAngleParameters(t)
                if a in new and b in new and c in new:
                    g.addAngle(new[a], new[b], new[c], th, k)
        elif isinstance(f, openmm.PeriodicTorsionForce):
            g = openmm.PeriodicTorsionForce()
            for t in range(f.getNumTorsions()):
                a, b, c, d, per, ph, k = f.getTorsionParameters(t)
                if a in new and b in new and c in new and d in new:
                    g.addTorsion(new[a], new[b], new[c], new[d], per, ph, k)
        elif isinstance(f, openmm.CustomTorsionForce):
            g = openmm.CustomTorsionForce(f.getEnergyFunction())
            for t in range(f.getNumGlobalParameters()):
                g.addGlobalParameter(f.getGlobalParameterName(t), f.getGlobalParameterDefaultValue(t))
            for t in range(f.getNumPerTorsionParameters()):
                g.addPerTorsionParameter(f.getPerTorsionParameterName(t))
            for t in range(f.getNumTorsions()):
                a, b, c, d, params = f.getTorsionParameters(t)
                if a in new and b in new and c in new and d in new:
                    g.addTorsion(new[a], new[b], new[c], new[d], params)
        elif isinstance(f, openmm.NonbondedForce):
            g = openmm.NonbondedForce()
            g.setNonbondedMethod(f.getNonbondedMethod())
            g.setCutoffDistance(f.getCutoffDistance())
            g.setReactionFieldDielectric(f.getReactionFieldDielectric())
            g.setUseSwitchingFunction(f.getUseSwitchingFunction())
            g.setSwitchingDistance(f.getSwitchingDistance())
            g.setUseDispersionCorrection(f.getUseDispersionCorrection())
            for old in keep:
                g.addParticle(*f.getParticleParameters(old))
            for t in range(f.getNumExceptions()):
                a, b, q, sig, eps = f.getExceptionParameters(t)
                if a in new and b in new:
                    g.addException(new[a], new[b], q, sig, eps)
        elif isinstance(f, openmm.CustomExternalForce):
            g = openmm.CustomExternalForce(f.getEnergyFunction())
            for t in range(f.getNumGlobalParameters()):
                g.addGlobalParameter(f.getGlobalParameterName(t), f.getGlobalParameterDefaultValue(t))
            for t in range(f.getNumPerParticleParameters()):
                g.addPerParticleParameter(f.getPerParticleParameterName(t))
            for t in range(f.getNumParticles()):
                a, params = f.getParticleParameters(t)
                if a in new:
                    g.addParticle(new[a], params)
        elif isinstance(f, openmm.CMMotionRemover):
            g = openmm.CMMotionRemover(f.getFrequency())
        else:
            return None
        sub.addForce(g)
    return sub, keep


def _stop_loops(n: int, why: str) -> PrepareRefused:
    return PrepareRefused(f"the {n} residue(s) built in the structure's gaps {why}. Lower '{LOOPS_SETTING}' (0 builds "
                          f"none) to prepare the structure as measured: a gap left open has its two ends capped.")


def relax_built(path, out_path, built) -> dict:
    """The residues PDBFixer built, relaxed before anything protonates them. Returns what was done, for the report.

    Only built residues move freely; their stems (FLANK measured residues either side) may give a little, on a spring
    to where they were measured, as loop modeling does (a loop held rigidly at both ends often cannot close: 7A3R's
    stayed at C-N-CA 92 degrees); every other measured atom is held exactly where it was measured (mass zero, so the
    minimizer cannot move it). While it relaxes every peptide bond in or beside the loop is held trans, and every
    built residue and stem of the L hand (_hold_chemistry); a built residue PDBFixer left of the D hand is set right
    first (_l_hand). Hydrogens come from OpenMM's templates, so the force field covers every residue, and
    only the heavy atoms of built and stem residues are written back (a hydrogen already on a stem is dropped, as it
    no longer sits on its atom; protonation places it again). Done before protonation because a junction PDBFixer
    closed badly cannot take its amide hydrogen, and the minimization that would have repaired it then fails for the
    want of it. Each attempt of RELAX_SCHEDULE starts again from PDBFixer's loop: minimize, a warm spell to leave its
    closure, minimize again, and later attempts loosen the stems and stay warm longer. The first whose every junction
    passes junction_geometry (every bond trans, every residue L, nothing clashing) is kept; a loop that still does not
    fit raises PrepareRefused.

    The relaxation is of the polymer: the protein and, where they come, the nucleic acids it binds (held, as everything
    measured is), so a loop is not relaxed into a strand that is not there. Ions, cofactors and waters are not in it.
    A nucleic acid the force field cannot take is left out of the relaxation rather than stopping it, and so, failing
    that, is every chain that holds no built residue (a chain of one residue has no template)."""
    import math
    import openmm
    from openmm import app, unit
    from .structure import classify
    made = set(built)

    def key(r):
        return (r.chain.id, str(r.id).strip(), (r.insertionCode or "").strip())

    def setup(keep_nucleic: bool, own_chains: bool = False):
        pdb = app.PDBFile(str(path))
        ff = app.ForceField("amber14-all.xml")
        m = app.Modeller(pdb.topology, pdb.positions)
        mine = {c.index for c in m.topology.chains() if any(key(r) in made for r in c.residues())}
        other = [r for r in m.topology.residues() if (r.name not in AMINO_ACIDS
                 and not (keep_nucleic and classify(r.name) == "nucleic"))
                 or (own_chains and r.chain.index not in mine)]
        if other:
            m.delete(other)              # relaxation only; the file keeps them
        import numpy as np
        xyz = np.array(m.positions.value_in_unit(unit.nanometer))
        righted = _l_hand(m.topology, xyz, made)
        m.positions = [openmm.Vec3(*p) for p in xyz] * unit.nanometer
        with _seeded_random(LOOP_SEEDS[0]), _one_cpu_thread() as cpu:    # Modeller lays new hydrogens at random first
            m.addHydrogens(ff, pH=7.0, platform=cpu)
        xyz = np.array(m.positions.value_in_unit(unit.nanometer))
        _ha_opposite(m.topology, xyz, made)
        m.positions = [openmm.Vec3(*p) for p in xyz] * unit.nanometer
        return m, ff.createSystem(m.topology, nonbondedMethod=app.CutoffNonPeriodic,
                                  nonbondedCutoff=1.2 * unit.nanometer, constraints=None), righted

    try:
        try:
            m, system, righted = setup(True)
        except Exception:  # noqa: BLE001 - a nucleic acid with no template: the protein alone
            try:
                m, system, righted = setup(False)
            except Exception:  # noqa: BLE001 - another chain with no template (a one-residue chain): the loops' own
                m, system, righted = setup(False, own_chains=True)
        res = list(m.topology.residues())
        idx = {key(r): i for i, r in enumerate(res)}
        stems = set()
        for k in made:
            i = idx.get(k)
            if i is None:
                continue
            for j in range(i - FLANK, i + FLANK + 1):
                if 0 <= j < len(res) and res[j].chain.id == res[i].chain.id and key(res[j]) not in made \
                        and res[j].name in AMINO_ACIDS:
                    stems.add(key(res[j]))
        spring = openmm.CustomExternalForce("k*((x-x0)^2+(y-y0)^2+(z-z0)^2)")
        spring.addGlobalParameter("k", 10.0 * 418.4)        # 10 kcal/mol/Å² in kJ/mol/nm²
        for p in ("x0", "y0", "z0"):
            spring.addPerParticleParameter(p)
        for a in m.topology.atoms():
            k = key(a.residue)
            if k in made:
                continue
            if k in stems and a.element is not None and a.element.symbol != "H":
                spring.addParticle(a.index, m.positions[a.index].value_in_unit(unit.nanometer))
            elif k not in stems:
                system.setParticleMass(a.index, 0.0)
        system.addForce(spring)
        held = _hold_chemistry(system, m.topology, made, stems)
        start = {a.index: m.positions[a.index].value_in_unit(unit.angstrom)
                 for a in m.topology.atoms() if key(a.residue) in stems
                 and a.element is not None and a.element.symbol != "H"}
        # Only the atoms the moving ones can feel are simulated (_neighbourhood), each attempt on one thread, so the
        # relaxation is the same every time: on several threads the CPU platform sums its forces in a different order
        # each run, DeterministicForces or not, and the dynamics carried the difference into whether a loop fitted
        moving = [a.index for a in m.topology.atoms() if key(a.residue) in made or key(a.residue) in stems]
        whole = m.positions.value_in_unit(unit.nanometer)
        cut = _neighbourhood(system, whole, moving, NEIGHBOURHOOD_NM)
        sub, keep = cut if cut else (system, list(range(system.getNumParticles())))
        pos0 = [tuple(whole[i]) for i in keep]
        xml = openmm.XmlSerializer.serialize(sub)
        ctx = openmm.Context(sub, openmm.VerletIntegrator(0.001), openmm.Platform.getPlatformByName("CPU"), {"Threads": "1"})
        ctx.setPositions([openmm.Vec3(*p) for p in pos0] * unit.nanometer)
        e0 = ctx.getState(getEnergy=True).getPotentialEnergy()
        del ctx
    except Exception as e:  # noqa: BLE001 - any failure here leaves invented atoms unrelaxed
        raise _stop_loops(len(made), f"could not be relaxed ({str(e)[:200]})") from e

    def write(pos):
        new = {}
        for a in m.topology.atoms():
            if (key(a.residue) in made or key(a.residue) in stems) and a.element is not None and a.element.symbol != "H":
                new[key(a.residue) + (a.name,)] = pos[a.index]
        out, moved = [], 0
        for line in open(path, errors="replace"):
            if line.startswith(("ATOM", "HETATM")):
                k = (line[21], line[22:26].strip(), line[26].strip(), line[12:16].strip())
                if k in new:
                    x, y, z = new[k]
                    line = line[:30] + "%8.3f%8.3f%8.3f" % (x, y, z) + line[54:]
                    moved += 1
                elif k[:3] in stems and (line[76:78].strip().upper() in ("H", "D")
                                         or k[3].lstrip("0123456789").startswith(("H", "D"))):
                    continue
            out.append(line)
        with open(out_path, "w") as fh:
            fh.writelines(out)
        return moved

    # The attempts are independent (each starts again from PDBFixer's loop, with its own spring, warm spell and
    # seed), so they run side by side, one process and one thread each, and the first in RELAX_SCHEDULE's order whose
    # junctions all pass is kept: the answer a run of them one after another would give, in the time of the slowest
    # one it needs
    tried = []
    whole_A = [tuple(p) for p in m.positions.value_in_unit(unit.angstrom)]
    jobs = [(xml, pos0, n, k_spring, warm_ps) for n, (k_spring, warm_ps) in enumerate(RELAX_SCHEDULE, 1)]
    with _attempts(Path(out_path).parent / "relax_attempts", len(jobs)) as run:
        failed = []
        for n, k_spring, e1_kcal, got in run(jobs):
            if isinstance(got, str):
                # an attempt that blew up (one on 7A3R chain A ended "Particle coordinate is NaN") is one that did not
                # pass: the next goes on
                failed.append(f"attempt {n}: {got[:200]}")
                continue
            pos = list(whole_A)
            for j, i in enumerate(keep):
                pos[i] = tuple(got[j])
            stem_rmsd = math.sqrt(sum(math.dist(tuple(pos[i]), tuple(p)) ** 2 for i, p in start.items())
                                  / max(1, len(start))) if start else 0.0
            moved = write(pos)
            bad = junction_geometry(out_path, built)
            tried.append((len(bad), bad, k_spring))
            if not bad:
                break
        else:
            if not tried:
                raise _stop_loops(len(made), f"could not be relaxed ({failed[0]})")
            fewest = min(tried, key=lambda t: t[0])
            raise _stop_loops(len(made), f"do not join the measured chain: {fewest[0]} fault(s) in their peptide bonds "
                                         f"and residues remain after {len(RELAX_SCHEDULE)} relaxation attempts "
                                         f"({'; '.join(fewest[1][:4])})")
    kc = unit.kilocalories_per_mole
    return {"built": len(made), "stems": len(stems), "spring_kcal": k_spring, "attempt": n,
            "attempts": len(RELAX_SCHEDULE), "stem_rmsd_A": round(stem_rmsd, 3), "heavy_atoms_moved": moved,
            "e0_kcal": round(e0.value_in_unit(kc), 1), "e1_kcal": round(e1_kcal, 1),
            "righted": righted, "held_trans": held[0], "held_l": held[1],
            "junctions": "every peptide bond in or beside the loops trans, C-N %g-%g Å, CA-CA %g-%g Å and its backbone "
                         "angles in range, every built residue of the L hand, none nearer than %g Å to another atom"
                         % (JUNCTION["bond"] + JUNCTION["ca_ca"] + (JUNCTION["contact"],))}


def _relax_attempt(job):
    """One attempt of RELAX_SCHEDULE, in a process of its own: (attempt, spring, energy in kcal/mol, the positions in Å
    of the atoms of the system, or the error as text). Minimize, a warm spell to leave PDBFixer's closure, minimize
    again, on one thread. The integrator is seeded before its context is made, which is when OpenMM reads the seed."""
    xml, pos0, n, k_spring, warm_ps = job
    try:
        import openmm
        from openmm import unit
        system = openmm.XmlSerializer.deserialize(xml)
        integ = openmm.LangevinMiddleIntegrator(300 * unit.kelvin, 5.0 / unit.picosecond, 0.0005 * unit.picoseconds)
        integ.setRandomNumberSeed(n)
        ctx = openmm.Context(system, integ, openmm.Platform.getPlatformByName("CPU"), {"Threads": "1"})
        ctx.setPositions([openmm.Vec3(*p) for p in pos0] * unit.nanometer)
        ctx.setParameter("k", k_spring * 418.4)
        openmm.LocalEnergyMinimizer.minimize(ctx, 1.0, 5000)
        ctx.setVelocitiesToTemperature(300 * unit.kelvin, n)
        integ.step(int(round(warm_ps / 0.0005)))
        openmm.LocalEnergyMinimizer.minimize(ctx, 0.5, 10000)
        state = ctx.getState(getEnergy=True, getPositions=True)
        return (n, k_spring, state.getPotentialEnergy().value_in_unit(unit.kilocalories_per_mole),
                [tuple(p) for p in state.getPositions(asNumpy=True).value_in_unit(unit.angstrom)])
    except Exception as e:  # noqa: BLE001 - said by the caller, which stops the stage
        return (n, k_spring, 0.0, f"{type(e).__name__}: {e}")


def _relax_main(argv) -> None:
    """One attempt run as a process of its own (_attempts): argv is the folder holding system.xml and start.json, the
    attempt's number, its spring and its warm spell; the result goes to attempt_<n>.json in that folder."""
    folder, n, k_spring, warm_ps = Path(argv[0]), int(argv[1]), float(argv[2]), float(argv[3])
    n, k_spring, e1, got = _relax_attempt(((folder / "system.xml").read_text(),
                                           json.loads((folder / "start.json").read_text()), n, k_spring, warm_ps))
    (folder / f"attempt_{n}.json").write_text(json.dumps({"e1": e1, "pos": got}))


class _attempts:
    """Runs _relax_attempt over the jobs, as many at once as this job has CPUs, each in a process of its own on one
    thread, and yields the results in the jobs' order; leaving the block stops whatever is still running. The
    processes are started afresh (python -c), not forked: OpenMM's threads in this one would be copied half-held, and
    a script that called Prepare without a main guard would be run again by multiprocessing's spawn. With one CPU,
    or where a process cannot start, the attempts run here in turn, which gives the same results."""

    def __init__(self, folder: Path, n: int):
        import os
        self.folder, self.n, self.procs = Path(folder), n, {}
        try:
            self.cpus = len(os.sched_getaffinity(0))
        except (AttributeError, OSError):
            self.cpus = os.cpu_count() or 1

    def __enter__(self):
        return self.run

    def _start(self, job):
        import os
        xml, pos0, n, k_spring, warm_ps = job
        home = str(Path(__file__).resolve().parent.parent)
        env = {**os.environ, "OPENMM_CPU_THREADS": "1"}
        self.procs[n] = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); from hopemd.prepare import _relax_main; "
             "_relax_main(sys.argv[2:])", home, str(self.folder), str(n), repr(k_spring), repr(warm_ps)],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    def run(self, jobs):
        if self.cpus <= 1 or self.n <= 1:
            for job in jobs:
                yield _relax_attempt(job)
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "system.xml").write_text(jobs[0][0])
        (self.folder / "start.json").write_text(json.dumps(jobs[0][1]))
        width = min(self.cpus, len(jobs))
        for job in jobs[:width]:
            self._start(job)
        for k, job in enumerate(jobs):
            n, k_spring = job[2], job[3]
            if k + width - 1 < len(jobs) and jobs[k + width - 1][2] not in self.procs:
                self._start(jobs[k + width - 1])
            proc = self.procs[n]
            _, err = proc.communicate()
            got = self.folder / f"attempt_{n}.json"
            if proc.returncode != 0 or not got.is_file():
                yield (n, k_spring, 0.0, f"attempt {n} ended without a result: {(err or '').strip()[-300:]}")
                continue
            res = json.loads(got.read_text())
            yield (n, k_spring, res["e1"], res["pos"])

    def __exit__(self, *exc):
        for proc in self.procs.values():
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        shutil.rmtree(self.folder, ignore_errors=True)
        return False


def _built_residues(fixer, gaps: dict, before: dict, taken: dict) -> list[tuple[str, str, str]]:
    """(chain, number, insertion code) of every residue PDBFixer built, found by where it sits in the chain rather
    than by its id, and renamed where its id is one a residue already has.

    PDBFixer numbers the residues it puts in a gap back from the residue after it, by number alone: 7A3R's four before
    A:195 are 191-194, as deposited, but three before thrombin's H:149E would be H:146-148, the ids of three measured
    residues. A built residue whose id is taken (taken[chain], the structure's ids with its hetero groups and waters),
    or given twice, takes instead the insertion codes after the residue before the gap, in order (H:149B, H:149C ...),
    so no hotspot list ever finds a built residue under a measured one's id. `gaps` is PDBFixer's missingResidues as it
    was built, `before` each chain's residues before addMissingAtoms."""
    import re
    out = []
    for ci, chain in enumerate(fixer.topology.chains()):
        new = list(chain.residues())
        old = before.get(ci, [])
        ids = set(taken.get(chain.id, set()))
        j = 0
        prev = None
        for pos in range(len(old) + 1):
            for _ in gaps.get((ci, pos), []):
                if j >= len(new):
                    break
                r = new[j]; j += 1
                rid = f"{str(r.id).strip()}{(r.insertionCode or '').strip()}"
                if rid in ids and prev is not None:
                    m = re.match(r"-?\d+", prev)
                    num, code = (m.group(), prev[m.end():]) if m else (prev, "")
                    free = next((c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if (not code or c > code)
                                 and f"{num}{c}" not in ids), None)
                    if free is not None:
                        r.id, r.insertionCode = num, free
                        rid = f"{num}{free}"
                ids.add(rid)
                out.append((chain.id, str(r.id).strip(), (r.insertionCode or "").strip()))
                prev = rid
            if pos < len(old) and j < len(new):
                prev = f"{str(new[j].id).strip()}{(new[j].insertionCode or '').strip()}"
                j += 1
    return out


def count_clashes(pdb_text: str) -> int:
    """Heavy-atom pairs under CLASH_A that are not in the same or adjacent residue. Grid-based, cheap."""
    atoms = []
    ordinal, last_key = 0, None
    for l in pdb_text.splitlines():
        if not l.startswith(("ATOM", "HETATM")):
            continue
        el = (l[76:78].strip() or l[12:14].strip()).upper()
        if el in ("H", "S") or classify(l[17:20]) == "metal":   # disulfides and coordination are not clashes
            continue
        try:
            key = (l[21], l[22:27])
            if key != last_key:
                ordinal += 1; last_key = key
            atoms.append((float(l[30:38]), float(l[38:46]), float(l[46:54]), l[21], ordinal))
        except ValueError:
            pass
    cell = 3.0
    grid: dict[tuple, list[int]] = {}
    for i, a in enumerate(atoms):
        grid.setdefault((int(a[0] // cell), int(a[1] // cell), int(a[2] // cell)), []).append(i)
    n = 0
    for (gx, gy, gz), idx in grid.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    other = grid.get((gx + dx, gy + dy, gz + dz))
                    if not other:
                        continue
                    for i in idx:
                        ai = atoms[i]
                        for j in other:
                            if j <= i:
                                continue
                            aj = atoms[j]
                            if ai[3] == aj[3] and abs(ai[4] - aj[4]) <= 1:
                                continue
                            if (ai[0]-aj[0])**2 + (ai[1]-aj[1])**2 + (ai[2]-aj[2])**2 < CLASH_A**2:
                                n += 1
    return n


def prepare(rundir: str | Path, spec_dict: dict) -> dict:
    """Run the stage for a run folder that has input/complex.pdb (or .cif). Returns the report."""
    rundir = Path(rundir)
    sysm = spec_dict.get("system", {})
    pr = sysm.get("prep", {})
    ph = float(sysm.get("ph", 7.4))
    # the structure arrives with every molecule prepared on its own (a flow's preparation card did
    # it, molecule by molecule, at this pH): modeling and protonation are not done again, because
    # doing them again is what would undo them. Everything else here is about the assembled
    # complex and still runs - the clean, the hetero groups carried, the minimization, the split.
    already = bool(pr.get("prepared_per_molecule"))
    build = rundir / "build"; build.mkdir(exist_ok=True)
    report: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "steps": {}}
    src = next((p for p in (rundir / "input").glob("complex.*")), None)
    if src is None:
        raise FileNotFoundError("input/complex.pdb|cif not found; import first")

    # 1 ---- clean
    text = read_structure(str(src), model=int(pr.get("model", 1)))
    # the ids a cap may not take: every residue's of the structure as it arrived, its hetero groups and waters too
    cap_taken = residue_ids(text)
    reading = plddt_reading(rundir, sysm, text)
    inv = inventory(text, plddt=reading)
    drop_waters = pr.get("drop_waters", "all")
    drop_classes = {"water"} if drop_waters not in ("far", "none") else set()
    if pr.get("drop_buffers", True):
        drop_classes |= {"buffer", "ion"}
    if not pr.get("keep_cofactors", True):
        drop_classes |= {"cofactor", "metal"}
    if not pr.get("keep_other_hetero", False):
        drop_classes |= {"ligand", "glycan"}
    drop_names = {str(n).strip().upper() for n in pr.get("drop_hetero") or [] if str(n).strip()}
    keep_chains = set(sysm.get("receptor_chains", []) + sysm.get("partner_chains", [])) or None
    # the ions a nucleic acid binds hold its structure (K+ in a G-quadruplex channel, Mg2+ in an RNA fold), so they
    # stay whatever the buffer setting says; bulk ions farther out go with the buffers. Metals follow keep_cofactors,
    # and a name under 'also drop' goes whatever binds it.
    nucleic = {c["id"] for c in inv.chains if c["kind"] == "nucleic" and (keep_chains is None or c["id"] in keep_chains)}
    bound = [g for g in nucleic_cations(text, chains=nucleic) if classify(g[2]) not in drop_classes - {"ion"}
             and g[2].upper() not in drop_names] if nucleic and "ion" in drop_classes else []
    text, removed = filter_pdb(text, keep_chains, drop_classes, altloc=pr.get("altloc", "A"), drop_resnames=drop_names,
                               keep_groups=set(bound))
    report["steps"]["clean"] = {"removed": removed, "kept_hetero": [h for h in inv.hetero if h["class"] not in drop_classes
                                                                    and h["resname"] not in drop_names]}
    if bound:
        report["steps"]["clean"]["bound_ions"] = [f"{name} {ch}:{resi}" for ch, resi, name in bound]
        _log(report, f"kept {len(bound)} ion(s) bound to the nucleic acid ({', '.join(report['steps']['clean']['bound_ions'])}): "
                     f"within {BOUND_ION_A:g} Å of it they hold its structure; bulk ions farther out were removed")
    # the crystal waters kept (all of them, or those near the partner) leave the structure now and come back at the
    # split as input/waters.pdb: PDBFixer would carry them, but pdb2pqr drops them (--drop-water) and the minimizer
    # has no template for them
    waters = water_groups(text) if "water" not in drop_classes else []
    if waters:
        if drop_waters == "far":
            _, par = split_of(sysm, inv.chains, (rundir / "input" / "ligand.sdf").exists())
            partner = (_ligand_xyz(rundir / "input" / "ligand.sdf") if (rundir / "input" / "ligand.sdf").exists()
                       else heavy_xyz(text, chains=set(par)) if par else [])
            near = near_waters(waters, partner)
            if len(near) < len(waters):
                removed["water"] = removed.get("water", 0) + len(waters) - len(near)
            _log(report, f"kept {len(near)} of the {len(waters)} crystal waters, those within {NEAR_WATER_A:g} Å of the partner"
                         if partner else f"no partner: no crystal water is near one, and none of the {len(waters)} was kept")
            waters = near
        else:
            _log(report, f"kept all {len(waters)} crystal waters")
        text = ter_between_chains("\n".join(l for l in text.splitlines()
                                             if not (l.startswith(("ATOM", "HETATM")) and classify(l[17:20]) == "water")) + "\n")
    if inv.altlocs:
        _log(report, f"{inv.altlocs} alternate-location atoms dropped, keeping altloc {pr.get('altloc', 'A')}")
    # nothing left to prepare: an import that read no chain, or chains asked for that the structure has not
    if not inventory(text, plddt=(False, "")).polymer_chains():
        raise PrepareRefused("Clean left no protein or nucleic-acid chain in the imported structure"
                             + (f" (the chains asked for: {', '.join(sorted(keep_chains))})" if keep_chains else "")
                             + ": there is nothing to prepare. Import it again and check that it holds the chains to simulate.")
    # a modified amino acid becomes its parent, its atoms where they were; one whose charge is not its parent's stops
    # the stage by name, as the force fields have nothing for it and the parent would be another molecule
    charged = [m for m in modified_residues(text) if not m["mapped"]]
    if charged:
        chains = sorted({m["chain"] for m in charged})
        raise PrepareRefused(", ".join(f"{m['resname']} {m['chain']}:{m['resi']}" for m in charged[:8]) + (" …" if len(charged) > 8 else "")
                             + ": modified amino acids whose charge is not their parent's (a phosphate, sulfate, carboxylate or "
                             "acetyl), which the force fields here have no parameters for. As the parent, the run would be another "
                             "molecule with another charge, so Prepare does not go on: leave chain"
                             + (f" {chains[0]}" if len(chains) == 1 else f"s {', '.join(chains)}")
                             + " out of the receptor and partner chains, or start from a structure without the modifications.")
    text, mapped = map_modified(text)
    if mapped:
        report["steps"]["clean"]["mapped"] = [f"{m['resname']} {m['chain']}:{m['resi']} to {m['parent']}" for m in mapped]
        _log(report, f"{len(mapped)} modified residue(s) mapped to their parent, the parent's atoms kept where they were: "
                     + mapped_note(mapped))
    # a strand that arrives with a 5'-terminal phosphate loses it before PDBFixer and the hydrogens see it
    text, stripped = strip_5p_phosphates(text)
    (build / "complex_clean.pdb").write_text(text)

    # 3 ---- trim (before modeling, so low-confidence tails are never rebuilt); only a model whose B
    # column is pLDDT (b_is_plddt): an X-ray structure's low B-factors are its best-ordered residues
    trimmed = {}
    threshold = float(pr.get("trim_plddt", 50))
    if inv.plddt and threshold > 0:
        text, trimmed = trim_low_plddt(text, threshold)
        report["steps"]["trim"] = {"plddt": True, "basis": inv.plddt["basis"], "threshold": pr.get("trim_plddt", 50),
                                   "residues_per_chain": trimmed}
        _log(report, f"B-factors read as pLDDT, since {inv.plddt['basis']}: "
                     + ("trimmed low-pLDDT termini: " + ", ".join(f"{k}: {v}" for k, v in trimmed.items()) if trimmed
                        else f"no terminal residue under {threshold:g}"))
    elif not inv.plddt and reading[1]:
        # B-factors in pLDDT's range on a structure that is not a model: the report says why nothing was cut
        report["steps"]["trim"] = {"plddt": False, "basis": reading[1]}
        _log(report, f"B-factors read as measured, since {reading[1]}: no pLDDT trim")

    # Clean's hetero groups as Clean left them: PDBFixer keeps them and propka counts the ions among its
    # charged groups, but pdb2pqr writes none of them back and the minimizer has no template for any.
    # They leave the structure after protonation; the ions the builds have parameters for come back
    # at the split, at these coordinates, in input/ions.pdb
    hetero = hetero_groups(text)

    # 2 ---- model (PDBFixer)
    clashes_before = count_clashes(text)
    input_residues = {(l[21], l[22:27].strip()) for l in text.splitlines() if l.startswith("ATOM")}
    modeled = {"available": False}
    states = protonation_states(text)     # HID/HIE/... in the input survive the round trip below
    built_loops: list[tuple[str, str, str]] = []
    if already:
        modeled = {"available": False, "skipped": "prepared per molecule"}
        _log(report, "each molecule was modeled and protonated on its own before it arrived, so "
                     "the loops, the missing atoms and the termini are as that left them: "
                     "PDBFixer is not run again over the assembled complex")
    try:
        if already:
            raise _Skip
        from pdbfixer import PDBFixer
        from openmm.app import PDBFile
        import io
        import os
        os.environ.setdefault("OPENMM_CPU_THREADS", "8")     # PDBFixer's loop closure on the CPU, as _minimise has it
        # Only gaps inside a chain are built, none longer than MAX_LOOP whatever model_loops_max asks, and never a
        # chain's missing ends (rebuild_termini is no longer read: PDBFixer laid 7A3R's 38-residue tail out as a
        # straight strand 177 Å long, further than a chain that long can reach)
        asked = int(pr.get("model_loops_max", MAX_LOOP))
        maxloop = max(0, min(asked, MAX_LOOP))
        if asked > MAX_LOOP:
            _log(report, f"model_loops_max is {asked}: no gap longer than {MAX_LOOP} residues is built, so {MAX_LOOP} is used")
        if pr.get("rebuild_termini", False):
            _log(report, "rebuild_termini is set, and a chain's missing ends are still not built: a tail is held at one "
                         "end only, so where it goes is a guess that then moves through the whole run")
        bonded = bonded_successors(text)
        taken_ids = residue_ids(text)
        pdbfixer_protonates = pr.get("protonation", "propka") == "pdbfixer"
        relaxed, built = None, []
        # Loops from PDBFixer's first seed; only when no relaxation can make them fit are they built again from the
        # next, and the report says so
        for attempt, seed in enumerate(LOOP_SEEDS):
            fixer = PDBFixer(pdbfile=io.StringIO(text))
            fixer.findNonstandardResidues()
            nonstd = [f"{r.name}{residue_key(r)}" for r, new in fixer.nonstandardResidues]
            fixer.replaceNonstandardResidues()
            fixer.findMissingResidues()
            chains_list = list(fixer.topology.chains())
            kept, skipped, artifacts, why_left = {}, {}, {}, {}
            for key, residues in list(fixer.missingResidues.items()):
                chain_idx, pos = key
                chain = chains_list[chain_idx]
                chain_res = list(chain.residues())
                n_res = len(chain_res)
                terminal = pos == 0 or pos == n_res
                before = chain_res[pos - 1] if pos > 0 else None
                label = f"{chain.id}:{residue_key(before) if before else 'N'}-{residue_key(chain_res[pos]) if pos < n_res else 'C'}"
                if not terminal and before is not None and (chain.id, residue_key(before)) in bonded:
                    # the flanking residues are already bonded: a skipped number, not a hole
                    artifacts[label] = len(residues); del fixer.missingResidues[key]
                elif terminal:
                    end = f"{chain.id}:{'N' if pos == 0 else 'C'}-term"
                    skipped[end] = len(residues); del fixer.missingResidues[key]
                    why_left[end] = "a chain's disordered end, never built"
                elif len(residues) > maxloop:
                    skipped[label] = len(residues); del fixer.missingResidues[key]
                    why_left[label] = (f"longer than {maxloop}" if maxloop else "loop building is off (model_loops_max 0)")
                else:
                    kept[label] = len(residues)
            gaps = dict(fixer.missingResidues)
            before_build = {ci: list(c.residues()) for ci, c in enumerate(fixer.topology.chains())}
            fixer.findMissingAtoms()
            missing_atoms = sum(len(v) for v in fixer.missingAtoms.values())
            with _one_cpu_thread() as cpu:
                fixer.platform = cpu                 # the same loop on a GPU node as on a CPU one, every time
                try:
                    fixer.addMissingAtoms(seed=seed)     # the same loop every time
                except TypeError:                        # a PDBFixer too old to take one
                    fixer.addMissingAtoms()
            built = _built_residues(fixer, gaps, before_build, taken_ids)
            buf = io.StringIO(); PDBFile.writeFile(fixer.topology, fixer.positions, buf, keepIds=True)
            fixed = buf.getvalue()
            (build / "complex_rebuilt.pdb").write_text(fixed)
            if not built:
                if pdbfixer_protonates:
                    with _seeded_random(seed), _one_cpu_thread():
                        fixer.addMissingHydrogens(ph)
                    buf = io.StringIO(); PDBFile.writeFile(fixer.topology, fixer.positions, buf, keepIds=True)
                    fixed = buf.getvalue()
                break
            try:
                relaxed = relax_built(build / "complex_rebuilt.pdb", build / "complex_relaxed.pdb", built)
                fixed = (build / "complex_relaxed.pdb").read_text()
                relaxed.update(seed=seed)
                break
            except PrepareRefused as e:
                if attempt + 1 < len(LOOP_SEEDS):
                    _log(report, f"the loops PDBFixer built from seed {seed} could not be made to fit ({str(e)[:300]}); "
                                 f"building them again from seed {LOOP_SEEDS[attempt + 1]}")
                    continue
                report["steps"]["model"] = {"available": True, "loops_modelled": kept, "gaps_left": skipped,
                                            "seeds_tried": list(LOOP_SEEDS), "stopped": str(e)}
                report["stopped"] = str(e)
                _log(report, f"Prepare stopped: {e}")
                (build / "prep_report.json").write_text(json.dumps(report, indent=2))
                raise
        if built and pdbfixer_protonates:
            f3 = PDBFixer(pdbfile=io.StringIO(fixed))
            f3.platform = fixer.platform
            with _seeded_random(relaxed["seed"] if relaxed else LOOP_SEEDS[0]), _one_cpu_thread():
                f3.addMissingHydrogens(ph)
            buf = io.StringIO(); PDBFile.writeFile(f3.topology, f3.positions, buf, keepIds=True)
            fixed = buf.getvalue()
        text = cap_records(restore_protonation_states(fixed, states))
        modeled = {"available": True, "nonstandard_replaced": nonstd, "loops_modelled": kept,
                    "gaps_left": skipped, "gaps_left_why": why_left, "numbering_artefacts": artifacts,
                    "missing_atoms_added": missing_atoms,
                    "built": [f"{c}:{n}{i}" for c, n, i in built]}
        if relaxed:
            modeled["relaxed"] = relaxed
            built_loops = list(built)
        _log(report, f"PDBFixer: {len(kept)} loop(s) built" + (f" ({', '.join(f'{k} {v} res' for k, v in kept.items())})" if kept else "")
                     + (f" from seed {relaxed['seed']}" if relaxed else "")
                     + f", {len(skipped)} gap(s) left open" + (f" ({', '.join(f'{k} {v} res: {why_left[k]}' for k, v in skipped.items())})" if skipped else "")
                     + f", {missing_atoms} missing atoms added" + (f", replaced {', '.join(nonstd)}" if nonstd else "")
                     + (f"; {len(artifacts)} SEQRES gap(s) ignored because the flanks are bonded" if artifacts else ""))
        if relaxed:
            _log(report, f"built loops relaxed before protonation: {relaxed['built']} residue(s) free, {relaxed['stems']} stem "
                         f"residue(s) either side on a {relaxed['spring_kcal']:g} kcal/mol/Å² spring (attempt "
                         f"{relaxed['attempt']} of {relaxed['attempts']}), stems moved {relaxed['stem_rmsd_A']:.2f} Å RMSD, "
                         f"every other measured atom held; {relaxed['junctions']}")
    except _Skip:
        pass                      # said above: the molecules were modeled before they arrived
    except PrepareRefused:
        raise                     # a loop that will not fit stops the stage, by name
    except ImportError:
        _log(report, "pdbfixer/openmm not importable on this node: loops and missing atoms NOT modeled "
                     "(pip install --user pdbfixer, or module load OpenMM)")
    except Exception as e:  # keep going with what we have; the report says so
        _log(report, f"PDBFixer failed: {e}")
    report["steps"]["model"] = modeled
    (build / "complex_fixed.pdb").write_text(text)

    # 4 ---- protonate
    prot = pr.get("protonation", "propka")
    cym: list[str] = []
    if already:
        # Nothing is run over the assembled complex: propka would re-title every residue of every
        # molecule at once, which is exactly the decision the per-molecule preparation has already
        # made and recorded. What follows this still runs: the nucleotide names, the hetero groups
        # that leave the structure and the ions carried past them.
        report["steps"]["protonate"] = {"method": "already prepared per molecule", "ph": ph}
        _log(report, f"protonation left as it arrived: each molecule was protonated on its own at "
                     f"pH {ph:g}, and the hydrogens in the file are those")
    elif prot == "propka":
        exe = pdb2pqr_command()
        if exe:
            inp = build / "complex_fixed.pdb"; outp = build / "complex_pqr.pdb"
            # pdb2pqr has no definition for ACE or NME and writes nothing when it meets one: the caps a structure
            # capped before arrives with go round it, and back on the residues they are bonded to after it
            uncapped, held = hold_caps(text)
            if held:
                inp = build / "complex_uncapped.pdb"; inp.write_text(uncapped)
            r = subprocess.run(exe + ["--ff=AMBER", "--ffout=AMBER", f"--with-ph={ph:g}",
                                      "--titration-state-method=propka", "--keep-chain", "--drop-water",
                                      str(inp), str(outp)], capture_output=True, text=True, timeout=600)
            if r.returncode == 0 and outp.exists():
                text = "\n".join(l[:54] + "  1.00  0.00" + l[66:] if len(l) > 66 else l[:54] + "  1.00  0.00"
                                 for l in outp.read_text().splitlines() if l.startswith(("ATOM", "HETATM", "TER", "END"))) + "\n"
                if held:
                    text = return_caps(text, held, hydrogens=True)
                    _log(report, f"{len(held)} ACE/NME cap(s) the structure arrived with went round pdb2pqr, which has no "
                                 "definition for them, and back on the residues they are bonded to")
                # a thiolate (CYM) is what propka predicts for a few buried cysteines. One that holds a
                # metal stays a thiolate, because a Cys4 site built neutral is four unit charges short
                # against the ion; the rest go back to neutral CYS, which is what most protocols want.
                if " CYM " in text:
                    binds = metal_bound_cysteines(text, hetero)
                    thiol = sorted({f"CYS {l[21]}:{l[22:27].strip()}" for l in text.splitlines()
                                    if l[17:20] == "CYM" and f"{l[21]}:{l[22:27].strip()}" in binds})
                    free = sorted({(l[21], l[22:27].strip()) for l in text.splitlines()
                                   if l[17:20] == "CYM" and f"{l[21]}:{l[22:27].strip()}" not in binds})
                    cym = [f"CYS {c}:{r}" for c, r in free]
                    if free:
                        text = rename_residues(text, [{"chain": c, "resid": r, "now": "CYS"} for c, r in free])
                        _log(report, f"{len(cym)} cysteine(s) propka made thiolate (CYM) were set back to neutral CYS")
                    if thiol:
                        _log(report, f"{len(thiol)} cysteine(s) propka made thiolate stay CYM, because "
                                     f"{'it binds' if len(thiol) == 1 else 'they bind'} a metal: {', '.join(thiol)}")
                states = _titration_summary(text)
                report["steps"]["protonate"] = {"method": "propka", "ph": ph, "states": states}
                _log(report, f"propka at pH {ph:g}: " + (", ".join(f"{k} {v}" for k, v in states.items()) or "every residue in its standard state"))
            else:
                report["steps"]["protonate"] = {"method": "propka", "failed": r.stderr[-400:]}
                _log(report, "pdb2pqr failed; tleap/pdbfixer defaults will apply")
        else:
            report["steps"]["protonate"] = {"method": "propka", "failed": "pdb2pqr not found"}
            _log(report, "pdb2pqr not found: protonation left to the engine's defaults "
                         "(run the server from an environment that has pdb2pqr)")
    elif prot == "pdbfixer":
        report["steps"]["protonate"] = {"method": "pdbfixer", "ph": ph, "done": modeled.get("available", False)}
    else:
        report["steps"]["protonate"] = {"method": "none"}
    # The metal sites, read off the geometry. propka decides a tautomer from the hydrogen-bond network,
    # which does not know an ion is there, and PDBFixer's rules and tleap's defaults do not either. Only
    # the residues whose current state would put a proton on the atom that holds the ion are renamed, so
    # a structure that arrives with its sites already right is left alone.
    tauto, notes = metal_tautomers(text, hetero)
    if tauto:
        text = rename_residues(text, tauto)
    if tauto or notes:
        report["steps"]["protonate"]["metal_sites"] = {"renamed": tauto, "notes": notes}
        if report["steps"]["protonate"].get("states") is not None:
            report["steps"]["protonate"]["states"] = _titration_summary(text)
        for line in metal_tautomer_log(tauto, notes):
            _log(report, line)

    # pdb2pqr names RNA as Amber did before OL3 (RA, RU5 ...), which the minimizer has no template for
    text, renamed = normalise_nucleic_names(text)
    if renamed:
        _log(report, f"{renamed} nucleotide(s) given the names OL3 and OL21 use (pdb2pqr writes Amber's older ones)")
    # Clean's hetero groups leave the structure here (the residues that bind them named as the build names them)
    if hetero:
        text = without_groups(text, hetero)
        report["steps"]["protonate"].update(kept_groups(text, hetero))
        for line in kept_groups_log(report["steps"]["protonate"]):
            _log(report, line)
    # chain breaks get a TER now, so neither the minimizer nor tleap bonds across a gap (a protein's C..N,
    # a nucleic acid's O3'..P, across a missing residue number or too long to close); the ends of a protein gap
    # are capped below, and a second PDBFixer pass gives the C-termini of any break left open their OXT so every
    # segment has a valid template. A link the deposited model stretched, with no residue missing, stays a bond:
    # the minimizer closes it, and the report names it
    text, breaks = ter_at_breaks(text)
    stretched = stretched_bonds(text)
    # every strand starts at O5': the first nucleotide of each strand and of each piece a break leaves loses
    # its phosphate, which the force field has no 5'-terminal template for
    text, more = strip_5p_phosphates(text)
    stripped += more
    if stripped:
        report["phosphates_5p"] = stripped
        _log(report, f"5'-terminal phosphate dropped from {', '.join(stripped)}: the nucleic-acid force fields start a strand at O5'")
    # each protein gap left open has its two ends capped, ACE and NME, so that neither is a charged terminus the
    # protein does not have, perhaps beside the site: an NH3+ and a COO- at every hole in the chain. Not when the
    # molecules were prepared on their own before they arrived: their ends are as that preparation left them
    caps: list[dict] = []
    uncapped: list[dict] = []
    arrived = caps_in(text)            # the gaps a preparation before this one capped: they stay as they are
    if pr.get("cap_breaks", True) and not already:
        text, caps, uncapped = cap_gaps(text, taken=cap_taken, hydrogens=_hydrogenated(text))
    every_cap = caps_in(text)
    capped = {c["break"] for c in every_cap}
    open_breaks = [b for b in breaks if b not in capped]
    open_breaks += [u["break"] for u in uncapped if u["break"] not in open_breaks]
    found = breaks + [c["break"] for c in every_cap] + [u["break"] for u in uncapped]
    if found:
        report["breaks"] = list(dict.fromkeys(found))
    if every_cap:
        report["caps"] = every_cap
    if arrived:
        _log(report, f"{len(arrived)} chain break(s) arrived capped, ACE and NME, and are left so: "
                     + ", ".join(f"{c['break']} (NME {c['nme']}, ACE {c['ace']})" for c in arrived))
    if caps:
        _log(report, capped_note(caps))
    if uncapped:
        report["uncapped"] = uncapped
    if open_breaks:
        off = not pr.get("cap_breaks", True) and any(_kind_at(text, b) == "protein" for b in open_breaks)
        why = {u["break"]: u["why"] for u in uncapped}
        _log(report, f"{len(open_breaks)} chain break(s) left open, each piece with its own termini: "
                     + ", ".join(b + (f" ({why[b]})" if b in why else "") for b in open_breaks)
                     + ("; capping is off (cap_breaks), so the ends of a protein gap are charged termini" if off else ""))
        try:
            from pdbfixer import PDBFixer
            from openmm.app import PDBFile
            import io
            states = protonation_states(text)   # propka's names go in as HID/HIE and would come out HIS
            f2 = PDBFixer(pdbfile=io.StringIO(text))
            f2.missingResidues = {}
            f2.findMissingAtoms(); f2.addMissingAtoms()
            buf = io.StringIO(); PDBFile.writeFile(f2.topology, f2.positions, buf, keepIds=True)
            text = cap_records(restore_protonation_states(buf.getvalue(), states))
        except ImportError:
            pass
        except Exception as e:
            _log(report, f"terminal atoms at the breaks not added: {str(e)[:120]}")
    if stretched:
        report["stretched"] = stretched
        _log(report, stretched_note(stretched))

    # 5 ---- minimize (OpenMM, vacuum by default, heavy atoms of the input restrained)
    mini = {"done": False}
    if pr.get("minimise", True):
        try:
            mini = _minimise(text, build, int(pr.get("minimise_steps", 500)), float(pr.get("restraint_kcal", 50.0)),
                             bool(pr.get("implicit_solvent", False)), input_residues, built_loops)
            text = (build / "complex_min.pdb").read_text()
            _log(report, f"minimized {mini['steps']} steps ({'GBn2' if mini.get('implicit') else 'vacuum'}): input heavy atoms "
                         f"moved {mini['rmsd_A']:.2f} Å RMSD" + (f", {mini['free_heavy_atoms']} modeled atoms moved up to "
                         f"{mini['free_max_move_A']:.1f} Å" if mini.get('free_heavy_atoms') else ""))
        except ImportError:
            _log(report, "openmm not importable: minimization skipped (module load OpenMM)")
        except Exception as e:
            mini = {"done": False, "error": str(e)[:300]}
            _log(report, f"minimization failed: {str(e)[:200]}")
    report["steps"]["minimise"] = mini
    # the built loops as they leave: the relaxation's checks again, on the structure that is written, so nothing
    # after it (protonation, the caps, the minimization) has twisted a bond or turned a residue unseen
    if built_loops:
        (build / "complex_checked.pdb").write_text(text)
        faults = junction_geometry(build / "complex_checked.pdb", built_loops)
        (build / "complex_checked.pdb").unlink()
        if faults:
            e = _stop_loops(len(built_loops), f"do not join the measured chain once prepared: {len(faults)} fault(s) in "
                                              f"their peptide bonds and residues after protonation and the minimization "
                                              f"({'; '.join(faults[:4])})")
            report["stopped"] = str(e)
            _log(report, f"Prepare stopped: {e}")
            (build / "prep_report.json").write_text(json.dumps(report, indent=2))
            raise e
        _log(report, f"built loops checked as prepared: every peptide bond in or beside the {len(built_loops)} built "
                     f"residue(s) trans, every one of them of the L hand, none nearer than {JUNCTION['contact']:g} Å to "
                     f"another atom")
    clashes_after = count_clashes(text)
    report["clashes"] = {"before": clashes_before, "after": clashes_after}

    # 6 ---- split and write
    text = cap_records(text)          # the caps under the force fields' names, whatever they arrived with
    (build / "prepared.pdb").write_text(text)
    inv2 = inventory(text)
    # a bound small molecule taken out at import (input/ligand.sdf) is the partner: every chain is the receptor
    small = (rundir / "input" / "ligand.sdf").exists()
    rec, par = split_of(sysm, inv2.chains, small)
    # the ions carried past protonation go to their own file, which every build adds after the partner.
    # Those that bind the partner come first and the partner's residue range takes them, so MM-GBSA
    # counts them with it; the rest lie outside both ranges, where MM-GBSA counts them with the
    # receptor. The analysis keeps them all by their place, whatever their name, and strips what
    # follows them as solvent.
    rec_txt, par_txt = split_chains(text, rec, par)
    if not any(l.startswith("ATOM") for l in rec_txt.splitlines()):
        raise PrepareRefused(f"the prepared structure holds no residue of the receptor chain{'s' if len(rec) != 1 else ''} "
                             f"({', '.join(rec) or 'none'}): there is nothing to build. Import it again and check its chains.")
    entries = {c["group"]: c for c in report["steps"].get("protonate", {}).get("carried") or []}
    for g in hetero:
        if g["ion"]:
            entries[group_label(g)]["side"] = ion_side(g, entries[group_label(g)]["site"], rec, par if par_txt else [])
    first = [g for g in hetero if g["ion"] and entries[group_label(g)]["side"] == "partner"]
    carried = first + [g for g in hetero if g["ion"] and g not in first]
    if carried:
        (rundir / "input" / "ions.pdb").write_text(ions_pdb(carried))
        report["steps"]["protonate"]["carried"] = [entries[group_label(g)] for g in carried]
        if first:
            _log(report, f"{', '.join(group_label(g) for g in first)} bind{'s' if len(first) == 1 else ''} the partner: its residue "
                         f"range takes {'it' if len(first) == 1 else 'them'}, so MM-GBSA counts {'it' if len(first) == 1 else 'them'} "
                         "with the partner")
    elif (rundir / "input" / "ions.pdb").exists():
        (rundir / "input" / "ions.pdb").unlink()
    # the crystal waters Clean kept, where they were, less any an atom Prepare modeled or moved now overlaps;
    # every build adds them after the ions, as the run's water model
    if waters:
        solute = heavy_xyz(text) + (_ligand_xyz(rundir / "input" / "ligand.sdf") if small else [])
        clash = near_waters(waters, solute, within_A=CLASH_A)
        if clash:
            waters = [w for w in waters if w not in clash]
            _log(report, f"{len(clash)} kept crystal water(s) left out ({', '.join(group_label(w) for w in clash)}): an atom Prepare "
                         f"modeled or moved lies within {CLASH_A:g} Å of the oxygen")
    if waters:
        (rundir / "input" / "waters.pdb").write_text(waters_pdb(waters))
        _log(report, f"{len(waters)} crystal water(s) go to the build after the solute and its ions (input/waters.pdb), as the "
                     "run's water model")
    elif (rundir / "input" / "waters.pdb").exists():
        (rundir / "input" / "waters.pdb").unlink()
    report["steps"]["clean"]["waters_kept"] = [group_label(w) for w in waters]
    # hetero groups that survived the policy go with the receptor side, each its own TER block
    het = [l for l in text.splitlines() if l.startswith("HETATM") and classify(l[17:20]) != "water"
           and (l[21].strip() or "A") not in set(rec) | set(par)]
    if het:
        rec_txt = rec_txt.replace("\nEND\n", "\n" + "\n".join(het) + "\nTER\nEND\n")
    (rundir / "input" / "receptor.pdb").write_text(rec_txt)
    if par_txt:
        (rundir / "input" / "ligand.pdb").write_text(par_txt)
    elif (rundir / "input" / "ligand.pdb").exists():
        (rundir / "input" / "ligand.pdb").unlink()
    report["split"] = {"receptor_chains": rec, "partner_chains": par, **({"partner_ions": len(first)} if first else {})}
    # how the partner sits against the prepared receptor, as an import of files reports it
    partner = rundir / "input" / ("ligand.pdb" if par_txt else "ligand.sdf")
    if partner.exists():
        report["placement"] = placement_report(rundir / "input" / "receptor.pdb", partner, bound=not par_txt)
        _log(report, report["placement"]["note"])
    report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (build / "prep_report.json").write_text(json.dumps(report, indent=2))
    return report


def split_of(sysm: dict, chains: list[dict], small: bool) -> tuple[list[str], list[str]]:
    """The receptor and partner chains: those named, else the split the import starts with (default_split);
    beside a bound small molecule (small) every polymer chain is the receptor."""
    rec = list(sysm.get("receptor_chains") or [])
    par = list(sysm.get("partner_chains") or [])
    poly = [c["id"] for c in chains if c["kind"] in ("protein", "nucleic")]
    if small:
        rec = rec or poly
    elif not rec and not par:
        rec, par, _ = default_split(chains)
    if not rec:
        rec = [c for c in poly if c not in par]
    if not par and not small:
        par = [c for c in poly if c not in rec]
    return rec, par


def _ligand_xyz(sdf: Path) -> list:
    """The heavy atoms of the bound small molecule the import took out (input/ligand.sdf)."""
    from .importers.files import _heavy_xyz
    return _heavy_xyz(sdf)


def plddt_reading(rundir: Path, sysm: dict, text: str) -> tuple[bool, str]:
    """b_is_plddt with what the import knew: the source kind and file name from the spec, and the
    file as it came (input/original*, where an mmCIF keeps the categories its PDB text lost)."""
    src = sysm.get("source") or {}
    original = next((p.read_text(errors="replace") for p in sorted((rundir / "input").glob("original*"))
                     if p.suffix.lower() in (".cif", ".mmcif", ".pdb", ".ent")), "")
    return b_is_plddt(text, str(src.get("kind", "")), original, Path(str(src.get("path") or "")).name)


#: metals a free ion does not hold in their sites: a zinc, iron or copper site is bonded, and a point
#: charge with a Lennard-Jones sphere has no bonds (ZAFF or MCPB.py build a bonded model)
SITE_METALS = {"ZN", "FE", "FE2", "CU", "CO", "NI", "MN", "CD", "HG"}


def ion_side(group: dict, site: list[str], rec: list[str], par: list[str]) -> str:
    """The side an ion belongs to: the one most of the residues that bind it are in, else its own chain's."""
    chains = [r.split()[1].split(":")[0] for r in site]
    on_par, on_rec = sum(c in par for c in chains), sum(c in rec for c in chains)
    if on_par != on_rec:
        return "partner" if on_par > on_rec else "receptor"
    return "partner" if group["chain"] in par else "receptor"


def without_groups(pdb_text: str, groups: list[dict]) -> str:
    """The structure without these residues (and without CONECT records, which could name them)."""
    keys = {(g["lines"][0][21], g["lines"][0][22:27], g["resname"]) for g in groups}
    out = [l for l in pdb_text.splitlines() if not l.startswith("CONECT")
           and not (l.startswith(("ATOM", "HETATM")) and (l[21], l[22:27], l[17:20].strip()) in keys)]
    return ter_between_chains("\n".join(out) + "\n")


def metal_site(pdb_text: str, group: dict, cutoff_A: float = 3.0) -> list[str]:
    """The polymer residues with an N, O or S atom within cutoff_A of a one-atom group: "CYS A:70"."""
    line = group["lines"][0]
    try:
        x, y, z = float(line[30:38]), float(line[38:46]), float(line[46:54])
    except ValueError:
        return []
    site: list[str] = []
    for l in pdb_text.splitlines():
        if not l.startswith(("ATOM", "HETATM")) or classify(l[17:20]) not in ("protein", "nucleic"):
            continue
        el = l[76:78].strip().upper() if l[76:78].strip().isalpha() else l[12:16].strip().lstrip("0123456789")[:1].upper()
        if el not in ("N", "O", "S"):
            continue
        try:
            d2 = (float(l[30:38]) - x) ** 2 + (float(l[38:46]) - y) ** 2 + (float(l[46:54]) - z) ** 2
        except ValueError:
            continue
        label = f"{l[17:20].strip()} {l[21]}:{l[22:27].strip()}"
        if d2 < cutoff_A ** 2 and label not in site:
            site.append(label)
    return site


#: the tautomer that leaves a histidine's coordinating nitrogen free: an ion held through NE2 needs the proton
#: on ND1, which is HID, and one held through ND1 needs HIE. Backwards puts a proton on the atom whose lone pair
#: is supposed to hold the ion.
TAUTOMER_FOR_DONOR = {"NE2": "HID", "ND1": "HIE"}
#: the atoms that can be a metal's donor, by the residues whose state they fix
DONOR_ATOMS = ("ND1", "NE2", "SG", "OD1", "OD2", "OE1", "OE2")


def metal_tautomers(pdb_text: str, groups: list[dict], cutoff_A: float = 3.0) -> tuple[list[dict], list[str]]:
    """The renamings the metal sites in a structure need, read off the structure's own geometry.

    propka decides a histidine's tautomer from the hydrogen-bond network, which does not know an ion is
    there, and it leaves most metal-bound histidines as HIE: a proton on NE2, the nitrogen a copper or zinc
    site most often binds through. The site is then a proton and a cation competing for one lone pair, and the
    cation loses. Measured on 7R4Z: both arms were built from the same coordinates, Cu-N 2.00-2.41 A, and the
    energy minimization alone decided it - with the tautomers read off the geometry the three histidines closed
    to 1.88-1.91 A, and with propka's the copper was expelled to 2.94-6.11 A before any dynamics had been run.
    Over 20 ns of production, three replicates each, it then held in every frame or drifted to 11 A. So the
    failure is visible in the minimized structure: a metal site that has opened up by the end of Prepare will
    not recover during the run.
    A cysteine that binds a metal is a thiolate for the same reason - a Cys4 site built neutral is four unit
    charges short against the cation - so a coordinating CYS becomes CYM rather than being set back.

    Returns (edits, notes). Each edit is {chain, resid, was, now, atom, dist, metal}; the notes are the sites
    this cannot decide, which the report says out loud instead of guessing at.
    """
    metals = []
    for g in groups:
        if g["class"] != "metal":
            continue
        line = g["lines"][0]
        try:
            metals.append((group_label(g), float(line[30:38]), float(line[38:46]), float(line[46:54])))
        except ValueError:
            continue
    if not metals:
        return [], []
    want: dict[tuple[str, str], dict] = {}
    refused: set[tuple[str, str]] = set()
    notes: list[str] = []
    for l in pdb_text.splitlines():
        if not l.startswith(("ATOM", "HETATM")) or classify(l[17:20]) != "protein":
            continue
        atom, resn, key = l[12:16].strip(), l[17:20].strip(), (l[21], l[22:27])
        if atom not in DONOR_ATOMS or key in refused:
            continue
        try:
            x, y, z = float(l[30:38]), float(l[38:46]), float(l[46:54])
        except ValueError:
            continue
        for label, mx, my, mz in metals:
            d = ((x - mx) ** 2 + (y - my) ** 2 + (z - mz) ** 2) ** 0.5
            if d >= cutoff_A:
                continue
            if resn in ("HIS", "HID", "HIE", "HIP") and atom in TAUTOMER_FOR_DONOR:
                now = TAUTOMER_FOR_DONOR[atom]
            elif resn in ("CYS", "CYM") and atom == "SG":
                now = "CYM"
            elif resn == "ASH":
                now = "ASP"
            elif resn == "GLH":
                now = "GLU"
            elif resn == "CYX" and atom == "SG":
                notes.append(f"CYX {key[0]}:{key[1].strip()} is in a disulfide and its SG is {d:.2f} A from "
                             f"{label}: a bonded cysteine is not that ion's ligand, so nothing was changed here")
                continue
            else:
                continue
            seen = want.get(key)
            if seen and seen["now"] != now:
                # both ring nitrogens hold an ion: neither proton can stay, and no template has that residue
                notes.append(f"{resn} {key[0]}:{key[1].strip()} binds a metal through {seen['atom']} and through "
                             f"{atom}: no tautomer leaves both free, so it was left as {resn} - a bonded model "
                             f"(MCPB.py) is what that site needs")
                refused.add(key); want.pop(key, None)
                break
            if seen and seen["dist"] <= d:
                continue
            want[key] = {"chain": key[0], "resid": key[1].strip(), "was": resn, "now": now,
                         "atom": atom, "dist": round(d, 2), "metal": label}
    edits = [e for e in want.values() if e["was"] != e["now"]]
    return edits, notes


def rename_residues(pdb_text: str, edits: list[dict]) -> str:
    """The structure with each edit's residue under the name its metal site needs."""
    by_res = {(e["chain"], e["resid"]): e["now"] for e in edits}
    out = []
    for l in pdb_text.splitlines():
        if l.startswith(("ATOM", "HETATM")):
            name = by_res.get((l[21], l[22:27].strip()))
            if name:
                l = l[:17] + f"{name:<3}" + l[20:]
        out.append(l)
    return "\n".join(out) + "\n"


def metal_tautomer_log(edits: list[dict], notes: list[str]) -> list[str]:
    """The report's lines for what the metal sites made of the protonation propka chose."""
    lines = []
    if edits:
        lines.append("metal sites read off the geometry: "
                     + ", ".join(f"{e['was']} {e['chain']}:{e['resid']} to {e['now']} "
                                 f"({e['atom']} {e['dist']:g} A from {e['metal']})" for e in edits)
                     + ". The atom that holds the ion has to stay deprotonated, and the hydrogen-bond network "
                       "propka works from does not know the ion is there")
    return lines + list(notes)


def metal_bound_cysteines(pdb_text: str, groups: list[dict]) -> set[str]:
    """The "A:70" of every cysteine, neutral or thiolate, whose SG is within coordination distance of a
    metal. propka's thiolates go back to neutral CYS except these: on a metal, a thiolate is the state
    the site needs, and setting it back is what left the lab's QA-20 zinc site four charges short."""
    return {s.split(" ", 1)[1] for g in groups if g["class"] == "metal"
            for s in metal_site(pdb_text, g) if s.split()[0] in ("CYS", "CYM")}


def kept_groups(pdb_text: str, groups: list[dict]) -> dict:
    """What became of the hetero groups Clean kept: the monatomic ions the builds have parameters for are
    carried (with the residues that bind them), anything else is not."""
    return {"carried": [{"group": group_label(g), "charge": g["ion"][1], "site": metal_site(pdb_text, g)}
                        for g in groups if g["ion"]],
            "not_carried": [{"group": group_label(g), "class": g["class"]} for g in groups if not g["ion"]]}


def kept_groups_log(step: dict) -> list[str]:
    """The report's lines for kept_groups: what was carried, what a metal site lacks, what was not."""
    lines = []
    carried, others = step.get("carried") or [], step.get("not_carried") or []
    if carried:
        lines.append("carried past protonation at their own coordinates: " + ", ".join(c["group"] for c in carried)
                     + "; the build adds them last, as free ions (input/ions.pdb)")
    sites = [c for c in carried if c["group"].split()[0] in SITE_METALS and c["site"]]
    if sites:
        one = len(sites) == 1
        obj, its, stays = ("it", "its site", "it stays") if one else ("them", "their sites", "they stay")
        named = [r for c in sites for r in c["site"] if r.split()[0] in ("CYM", "HID", "HIE")]
        lines.append("; ".join(f"{c['group']} sits on {', '.join(c['site'])}" for c in sites)
                     + f": the build has {obj} as {'a free ion' if one else 'free ions'}, a charge and a "
                     f"Lennard-Jones sphere with no bonds to {its}"
                     + (f", and the residues that hold {obj} are named for the geometry: a cysteine that binds a metal "
                        f"as a thiolate (CYM), a histidine on the tautomer that leaves its coordinating nitrogen free"
                        if named else "")
                     + f". That naming is what decides whether {stays} - on 7R4Z it was the difference between a "
                     f"copper that closed onto its three histidines at 1.9 A during the minimization and one the "
                     f"minimization pushed out to 6 A, which never came back in 20 ns - but no bond holds {obj} there, "
                     f"so check the minimized structure and then {its} in the trajectory. A bonded model of a "
                     f"metal site (ZAFF, MCPB.py) is not built here")
    if others:
        one = len(others) == 1
        names = ", ".join(f"{o['group']} ({o['class']})" for o in others)
        lines.append(f"{names} {'is' if one else 'are'} not in the build: no force field here covers {'it' if one else 'them'}. "
                     f"Drop {'it' if one else 'them'} in Clean ('also drop' under Prepare), or run {'it' if one else 'one'} "
                     f"as the partner; Submit refuses the run while Clean keeps {'it' if one else 'them'}")
    return lines
def placement_report(receptor: Path, partner: Path, bound: bool) -> dict:
    """The placement check of an import of files on the prepared structure: the closest heavy-atom
    pair, the pairs within 4.5 Å and those under 1.5 Å, with the note that says it. A bound ligand
    (bound=True) sat out the minimization, so a clash with it is atoms Prepare modeled or moved
    into its place, and the note says that plainly."""
    from .importers.files import placement, placement_note
    try:
        p = placement(receptor, partner)
    except Exception as e:  # a file numpy cannot read: the check is advisory, as at import
        return {"note": f"placement check not made ({e})"}
    if not p:
        return {"note": "placement check not made: no heavy atoms to compare"}
    if p["clashes"] and bound:
        note = (f"{p['clashes']} heavy-atom pair(s) closer than 1.5 Å between the prepared receptor and the ligand: the ligand "
                "was not in Prepare's minimization, so atoms Prepare modeled or moved overlap it. Look at the pocket before "
                "you submit")
    else:
        note = placement_note(p, "ligand" if bound else "partner")
    return {**p, "note": note}


def pdb2pqr_command() -> list[str] | None:
    """How to run pdb2pqr from here: as a module of this interpreter when the package is
    importable (a venv on top of a conda env has the package but not the console script),
    else the console script on PATH, else the one beside this interpreter's base prefix."""
    try:
        import pdb2pqr  # noqa: F401
        return [sys.executable, "-m", "pdb2pqr"]
    except ImportError:
        pass
    for name in ("pdb2pqr30", "pdb2pqr"):
        exe = shutil.which(name) or shutil.which(name, path=str(Path(sys.base_prefix) / "bin"))
        if exe:
            return [exe]
    return None


def _titration_summary(text: str) -> dict[str, int]:
    names = {}
    seen = set()
    for l in text.splitlines():
        if l.startswith("ATOM"):
            key = (l[21], l[22:27]); resn = l[17:20].strip()
            if key in seen:
                continue
            seen.add(key)
            if resn in ("HID", "HIE", "HIP", "ASH", "GLH", "LYN", "CYX", "CYM"):
                names[resn] = names.get(resn, 0) + 1
    return names


def _minimise(text: str, build: Path, steps: int, k_kcal: float, implicit: bool = False,
              input_residues: set | None = None, built=()) -> dict:
    """Restrained minimization. In vacuum by default: this is about clashes and strained
    modeled atoms, not about solvation, and vacuum is ten times faster. The residues built in
    the gaps (`built`, (chain, number, insertion code)) are free, but keep the chemistry their
    relaxation gave them: their peptide bonds trans and their residues of the L hand, by the
    relaxation's restraints (_hold_chemistry), which a loop inside their wells does not feel."""
    import io, math, os
    os.environ.setdefault("OPENMM_CPU_THREADS", "8")     # login-node fair use
    import openmm as mm
    import openmm.app as app
    from openmm import unit as u
    # OpenMM's reader folds HID/HIE/HIP/ASH/GLH/CYX into HIS/ASP/GLU/CYS and its
    # writer puts the folded names out, so propka's states would be lost here (every
    # histidine would come out HIS). They are read off the text first, handed to
    # addHydrogens as variants, and written back into the minimized file.
    states = protonation_states(text)
    # LYN is the one name the reader does not fold (OpenMM 8.6's pdbNames.xml): read as it is, the residue
    # gets no peptide bonds and no template matches its neighbor, so it goes in as LYS and its variant
    # makes it neutral again
    pdb = app.PDBFile(io.StringIO(_strip_hydrogens(text).replace(" LYN ", " LYS ")))
    ff = app.ForceField("amber14-all.xml", *(["implicit/gbn2.xml"] if implicit else []))
    modeller = app.Modeller(pdb.topology, pdb.positions)
    # hydrogens are re-added here with amber14 names so the force field always matches
    variants = [states.get((r.chain.id, residue_key(r))) for r in modeller.topology.residues()]
    with _seeded_random(LOOP_SEEDS[0]), _one_cpu_thread() as cpu:   # the same hydrogens every time (Modeller lays them at random first)
        modeller.addHydrogens(ff, variants=variants, platform=cpu)
    system = ff.createSystem(modeller.topology, nonbondedMethod=app.CutoffNonPeriodic, nonbondedCutoff=1.0 * u.nanometer,
                             constraints=app.HBonds)
    k = k_kcal * u.kilocalories_per_mole / u.angstrom ** 2
    rest = mm.CustomExternalForce("0.5*k*((x-x0)^2+(y-y0)^2+(z-z0)^2)")
    rest.addGlobalParameter("k", k)
    for p in ("x0", "y0", "z0"):
        rest.addPerParticleParameter(p)
    heavy, free_idx = [], []
    for a in modeller.topology.atoms():
        if a.element.symbol != "H":
            key = (a.residue.chain.id, residue_key(a.residue))
            if input_residues is None or key in input_residues:
                rest.addParticle(a.index, modeller.positions[a.index]); heavy.append(a.index)
            else:
                free_idx.append(a.index)       # a modeled residue: let it find its place
    system.addForce(rest)
    if built:
        _hold_chemistry(system, modeller.topology, set(built))
    sim, plat = _simulation_on_any_platform(modeller.topology, system, mm, app, u)
    sim.context.setPositions(modeller.positions)
    e0 = sim.context.getState(getEnergy=True).getPotentialEnergy().value_in_unit(u.kilojoule_per_mole)
    sim.minimizeEnergy(maxIterations=steps)
    st = sim.context.getState(getEnergy=True, getPositions=True)
    e1 = st.getPotentialEnergy().value_in_unit(u.kilojoule_per_mole)
    pos = st.getPositions(asNumpy=True).value_in_unit(u.angstrom)
    p0 = modeller.positions.value_in_unit(u.angstrom)
    import numpy as np
    pos, p0 = np.asarray(pos), np.asarray(p0)
    d = pos[heavy] - p0[heavy]
    rmsd = float(math.sqrt((d ** 2).sum(axis=1).mean())) if heavy else 0.0
    free_max = float(np.sqrt(((pos[free_idx] - p0[free_idx]) ** 2).sum(axis=1)).max()) if free_idx else 0.0
    buf = io.StringIO()
    app.PDBFile.writeFile(modeller.topology, st.getPositions(), buf, keepIds=True)
    (build / "complex_min.pdb").write_text(cap_records(restore_protonation_states(buf.getvalue(), states)))
    return {"done": True, "steps": steps, "e0": e0, "e1": e1, "rmsd_A": rmsd, "implicit": implicit,
            "free_heavy_atoms": len(free_idx), "free_max_move_A": free_max, "platform": plat.getName() if plat else "?"}


PLATFORMS = ("CUDA", "OpenCL", "CPU", "Reference")


def _simulation_on_any_platform(topology, system, mm, app, u):
    """The first platform that actually starts. `getPlatformByName("CUDA")` succeeds on a
    login node that has the CUDA plugin and no GPU (grace4/5, login5); the failure only
    comes when the context is created, so each platform is tried for real, in order, with
    its own integrator (an integrator can be bound to one context only). The CPU platform runs on one thread: on
    several it sums its forces in a different order each run, and the same structure came out of the minimization up
    to 0.16 Å apart on two runs (7A3R); on one, Prepare gives the same file every time."""
    last = None
    for name in PLATFORMS:
        try:
            plat = mm.Platform.getPlatformByName(name)
            integ = mm.LangevinMiddleIntegrator(300 * u.kelvin, 1 / u.picosecond, 0.001 * u.picoseconds)
            return app.Simulation(topology, system, integ, plat, {"Threads": "1"} if name == "CPU" else None), plat
        except Exception as e:   # no such platform, or a platform with no device here
            last = e
    raise RuntimeError(f"no OpenMM platform could start ({last})")


VARIANT_NAMES = ("HID", "HIE", "HIP", "ASH", "GLH", "LYN", "CYX", "CYM")


def protonation_states(text: str) -> dict[tuple[str, str], str]:
    """{(chain, resid): name} for every residue whose name carries a protonation state. The resid is the
    number and the insertion code together ("1H"), as residue_key names an OpenMM residue."""
    out = {}
    for l in text.splitlines():
        if l.startswith(("ATOM", "HETATM")) and l[17:20] in VARIANT_NAMES:
            out[(l[21], l[22:27].strip())] = l[17:20]
    return out


def restore_protonation_states(text: str, states: dict[tuple[str, str], str]) -> str:
    """Put the state-carrying names back on the residues OpenMM wrote with folded names."""
    if not states:
        return text
    out = []
    for l in text.splitlines():
        if l.startswith(("ATOM", "HETATM")):
            name = states.get((l[21], l[22:27].strip()))
            if name:
                l = l[:17] + name + l[20:]
        out.append(l)
    return "\n".join(out) + "\n"


def _hydrogenated(text: str) -> bool:
    """Whether the amino acids of this text carry their hydrogens (pdb2pqr's, or PDBFixer's)."""
    for l in text.splitlines():
        if l.startswith("ATOM") and classify(l[17:20]) == "protein":
            el = (l[76:78].strip() or l[12:14].strip()).upper()
            if el == "H" or l[12:16].strip().startswith(("H", "1H", "2H", "3H")):
                return True
    return False


def _kind_at(text: str, brk: str) -> str:
    """What a break ("A:190-195") is in: protein or nucleic, as classify names the residue before it."""
    chain, resi = brk.split(":", 1)[0], brk.split(":", 1)[1].rsplit("-", 1)[0]
    for l in text.splitlines():
        if l.startswith(("ATOM", "HETATM")) and (l[21].strip() or "A") == chain and l[22:27].strip() == resi:
            return classify(l[17:20])
    return ""


def _strip_hydrogens(text: str) -> str:
    """pdb2pqr's hydrogen names are not amber14's; the minimizer re-adds them. Residue names
    (HID/HIE/HIP, CYX, ASH, GLH, LYN) carry the protonation state through."""
    out = []
    for l in text.splitlines():
        if l.startswith(("ATOM", "HETATM")):
            el = (l[76:78].strip() or l[12:14].strip()).upper()
            if el == "H" or l[12:16].strip().startswith(("H", "1H", "2H", "3H")):
                continue
        out.append(l)
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    rd = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    spec = json.loads((rd / "spec.json").read_text()) if (rd / "spec.json").exists() else {}
    try:
        rep = prepare(rd, spec)
    except PrepareRefused as e:     # the job fails, and its log ends with why
        print(f"[hopemd] Prepare stopped: {e}", file=sys.stderr)
        raise SystemExit(1)
    print("\n".join(rep.get("log", [])))
    print(f"[hopemd] prepared {rd.resolve()} in {rep.get('started')} .. {rep.get('finished')}")
