"""The water box tleap builds around a solute, worked out from the solute's coordinates without running tleap: the
box and the water count the estimate shows before Submit.

What tleap does (leap's commands.c and tools.c, as the Amber 24 module of Grace and ACES runs them):

- solvateOct (isotropic, its default) and solvateBox with iso first turn the solute onto its principal axes, those of
  the moment of inertia of the atom centers with every atom of unit mass. solvateBox without iso leaves it as it is.
- The solute is centered on the box that holds its atoms' van der Waals spheres: the R* of each atom type, 1.0 Å for a
  hydrogen whose R* is 0, 1.5 Å for an atom without a type.
- solvateOct checks the clearance across the hexagonal faces: on each axis half the extent of the atom centers plus
  the buffer, the face at half the length of that half-box's diagonal, and the atom center farthest along the
  diagonal (along the face's normal for that half-box). When that atom is closer to the face than the buffer, the
  buffer is scaled up by (its distance + buffer) / (the face's distance): by 1.2 to 1.35 for a globular protein, so a
  12 Å buffer becomes 14 to 16 Å.
- The water region is the vdW box with the buffer on every side; isotropic, the largest side all round. solvateOct
  cuts a truncated octahedron from that cube (|x|, |y|, |z| under half the side, |x| + |y| + |z| under three quarters
  of it) and its cell is √3 (side / 2 + 0.5) along each lattice vector, at 109.47°. solvateBox's cell is the box
  holding the solvated system's vdW spheres, about 3 Å more than the region on each axis.
- The water is copies of the model's pre-equilibrated box of 216 molecules laid edge to edge; a molecule goes when
  any of its atoms is outside the region or overlaps a solute atom (closer than the sum of the two radii).

AmberTools 26 changed the hexagonal-face check (the isotropic vdW box, the true face distance, the radii counted and
the whole half-box scaled), which makes the octahedron of a globular solute larger; this follows the Amber 24 rule
the builds here run.

Everything here is plain Python on coordinates already read: solute_shape() runs where a structure is imported or
prepared and keeps what the box depends on; water_box() is the arithmetic the estimate repeats as the padding, the
shape or the water model change.
"""
from __future__ import annotations

import math
from pathlib import Path

#: R* (Å) of the Amber types (ff19SB, OL15/OL3, GAFF2, the ions' 12-6 sets) by element: what tleap pads each atom by
RADIUS = {"C": 1.908, "N": 1.824, "O": 1.6612, "S": 2.0, "P": 2.1, "F": 1.75, "CL": 1.948, "BR": 2.02, "I": 2.15,
          "NA": 1.467, "K": 1.705, "MG": 1.33, "CA": 1.608, "ZN": 1.219, "MN": 1.36, "FE": 1.4, "CU": 1.3,
          "NI": 1.3, "CO": 1.3, "CD": 1.45, "LI": 1.2, "RB": 1.86, "CS": 2.1}
#: a hydrogen's by the element it is bonded to: C-H types are 1.1 to 1.49 Å, N-H and S-H 0.6 Å, and an O-H of R* 0
#: counts 1.0 Å in tleap
H_RADIUS = {"C": 1.42, "N": 0.6, "S": 0.6, "O": 1.0}
WATER_O_RADIUS = 1.777
UNTYPED_RADIUS = 1.5
#: what the hydrogens tleap adds to a file without them would extend, from eleven solutes with and without theirs:
#: the vdW box (per side), the atom-center extent (per side), and the farthest atom along a body diagonal
H_ALLOWANCE_A = (0.13, 0.55, 0.82)
#: hydrogens per residue at pH 7 as the Amber libraries build them (an inner residue); others count one per heavy atom
HYDROGENS = {"ALA": 5, "ARG": 13, "ASN": 6, "ASP": 4, "ASH": 5, "CYS": 5, "CYX": 4, "CYM": 4, "GLN": 8, "GLU": 6,
             "GLH": 7, "GLY": 3, "HIS": 7, "HIE": 7, "HID": 7, "HIP": 8, "ILE": 11, "LEU": 11, "LYS": 13, "LYN": 12,
             "MET": 9, "MSE": 9, "PHE": 9, "PRO": 7, "SER": 5, "THR": 7, "TRP": 10, "TYR": 9, "VAL": 9, "ACE": 3,
             "NME": 4, "DA": 11, "DG": 11, "DC": 11, "DT": 12, "DU": 10, "A": 11, "G": 11, "C": 11, "U": 10,
             "RA": 11, "RG": 11, "RC": 11, "RU": 10, "HOH": 2, "WAT": 2}
WATER_RESIDUES = {"HOH", "WAT", "TIP", "TIP3", "SOL", "DOD"}
#: tleap's pre-equilibrated boxes (solvents.lib), 216 waters each: the edges in Å
WATER_BOX_A = {"OPC": (18.864844, 18.478108, 19.006241), "TIP3P": (18.774349, 18.774349, 18.774349),
               "TIP4P-Ew": (18.86, 18.867, 18.86)}
WATERS_PER_BOX = 216
#: the waters a region keeps, fitted to 145 tleap builds (eleven solutes of 700 to 10 400 atoms, the three shapes,
#: paddings of 8 to 20 Å, OPC, TIP3P and TIP4P-Ew): the box's density over the region, less a layer this thick over
#: its faces (a water goes when an atom crosses one) and this share of the solute's excluded volume, the union of its
#: heavy atoms' spheres grown by the probe (_excluded_volume)
FACE_LOSS_A = 0.41
EXCLUDED_SCALE = 0.98
EXCLUSION_PROBE_A = 2.0
#: the excluded volume per heavy atom where only the count is known (28 to 32 Å^3 in the same solutes)
EXCLUDED_A3_PER_HEAVY = 29.0
#: solvateBox's cell over its water region, on each axis (3.07 Å on average, 2.0 to 3.5 Å, in the same builds)
BOX_CELL_EXTRA_A = 3.1
SQRT3 = math.sqrt(3.0)


def _element(line: str) -> str:
    e = line[76:78].strip().upper()
    if not e or not e.isalpha():
        name = line[12:16].strip().upper()
        # a single-atom ion is named as its element (NA, CL, MG, ZN, CA); an atom name starts with its element
        e = name if name == line[17:20].strip().upper() and name in RADIUS else name.lstrip("0123456789")[:1]
    return "H" if e in ("H", "D") else e


def pdb_atoms(text: str, records: tuple[str, ...] = ("ATOM", "HETATM"), waters: bool = True) -> list[tuple]:
    """(element, residue name, residue key, x, y, z) of the first model's atoms, the first of alternate locations."""
    out = []
    for line in text.splitlines():
        if line.startswith("ENDMDL") and out:
            break
        if not line.startswith(records) or len(line) < 54 or line[16] not in " A1":
            continue
        resn = line[17:20].strip().upper()
        if not waters and resn in WATER_RESIDUES:
            continue
        try:
            x, y, z = float(line[30:38]), float(line[38:46]), float(line[46:54])
        except ValueError:
            continue
        out.append((_element(line), resn, line[21:27], x, y, z))
    return out


def read_atoms(path: str | Path, waters: bool = True) -> list[tuple]:
    """The atoms of a PDB, an SDF or MOL (first record) or a MOL2 file; nothing when it cannot be read."""
    p = Path(path)
    try:
        text = p.read_text(errors="replace")
    except OSError:
        return []
    suf = p.suffix.lower()
    out = []
    if suf in (".sdf", ".mol", ".sd"):
        lines = text.splitlines()
        try:
            n = int(lines[3][:3])
        except (IndexError, ValueError):
            return []
        for line in lines[4:4 + n]:
            parts = line.split()
            try:
                out.append((parts[3].upper(), "LIG", "lig", float(parts[0]), float(parts[1]), float(parts[2])))
            except (IndexError, ValueError):
                pass
        return [("H" if a[0] in ("H", "D") else a[0],) + a[1:] for a in out]
    if suf == ".mol2":
        block = False
        for line in text.splitlines():
            if line.startswith("@<TRIPOS>"):
                if block:
                    break
                block = line.startswith("@<TRIPOS>ATOM")
                continue
            parts = line.split()
            if block and len(parts) >= 6:
                el = parts[5].split(".")[0].upper()
                try:
                    out.append(("H" if el in ("H", "D") else el, "LIG", "lig", float(parts[2]), float(parts[3]), float(parts[4])))
                except ValueError:
                    pass
        return out
    return pdb_atoms(text, waters=waters)


def _radius(atom: tuple) -> float:
    return WATER_O_RADIUS if atom[0] == "O" and atom[1] in WATER_RESIDUES else RADIUS.get(atom[0], UNTYPED_RADIUS)


def _jacobi(a: list[list[float]]) -> list[list[float]]:
    """The eigenvectors (rows) of a symmetric 3 x 3 matrix, by largest eigenvalue first."""
    a = [row[:] for row in a]
    v = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    for _ in range(60):
        if sum(a[i][j] ** 2 for i in range(3) for j in range(3) if i != j) < 1e-18 * (1.0 + sum(a[i][i] ** 2 for i in range(3))):
            break
        for p, q in ((0, 1), (0, 2), (1, 2)):
            if a[p][q] == 0.0:
                continue
            theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
            t = (1.0 if theta >= 0 else -1.0) / (abs(theta) + math.sqrt(theta * theta + 1.0))
            c = 1.0 / math.sqrt(t * t + 1.0)
            s = t * c
            for k in range(3):
                a[k][p], a[k][q] = c * a[k][p] - s * a[k][q], s * a[k][p] + c * a[k][q]
            for k in range(3):
                a[p][k], a[q][k] = c * a[p][k] - s * a[q][k], s * a[p][k] + c * a[q][k]
            for k in range(3):
                v[k][p], v[k][q] = c * v[k][p] - s * v[k][q], s * v[k][p] + c * v[k][q]
    order = sorted(range(3), key=lambda k: -a[k][k])
    return [[v[0][k], v[1][k], v[2][k]] for k in order]


def _vdw_box(points: list[tuple], atoms: list[tuple], radii: list[float]) -> tuple[list[float], list[float]]:
    """The box holding the atoms' vdW spheres at these coordinates: (lowest, highest) on each axis. A hydrogen at a face
    takes the radius of its kind, from the heavy atom of its residue it is bonded to."""
    heavy_of: dict = {}
    for i, a in enumerate(atoms):
        if a[0] != "H":
            heavy_of.setdefault(a[2], []).append(i)
    known: dict[int, float] = {}

    def radius(i: int) -> float:
        if atoms[i][0] != "H":
            return radii[i]
        if i not in known:
            x, y, z = atoms[i][3:6]
            near = [(math.dist((x, y, z), atoms[j][3:6]), atoms[j][0]) for j in heavy_of.get(atoms[i][2], ())]
            near = [n for n in near if n[0] < 1.3]
            known[i] = H_RADIUS.get(min(near)[1], 1.0) if near else 1.0
        return known[i]

    lo, hi = [], []
    hmax = max(H_RADIUS.values())
    for k in range(3):
        top = max((p[k] + radii[i] for i, p in enumerate(points) if atoms[i][0] != "H"), default=-math.inf)
        bot = min((p[k] - radii[i] for i, p in enumerate(points) if atoms[i][0] != "H"), default=math.inf)
        for i, p in enumerate(points):
            if atoms[i][0] == "H" and (p[k] + hmax > top or p[k] - hmax < bot):
                top, bot = max(top, p[k] + radius(i)), min(bot, p[k] - radius(i))
        lo.append(bot)
        hi.append(top)
    return lo, hi


def _farthest(points: list[tuple], centres: list[float]) -> list[list[float]]:
    """The atoms farthest along the directions the hexagonal-face check can take, from a buffer of 0 to a far one:
    their |x|, |y|, |z| about the box center, all the check needs of them for any padding."""
    order = sorted(points, key=lambda p: -(p[0] * p[0] + p[1] * p[1] + p[2] * p[2]))
    keep = []
    for b in (0, 1, 2, 3, 4, 5, 6, 8, 10, 12, 14, 16, 18, 20, 23, 26, 30, 35, 40, 50, 70, 100, 200, 1e6):
        half = [c / 2 + b for c in centres]
        u = [half[1] * half[2], half[0] * half[2], half[0] * half[1]]
        norm = math.sqrt(sum(x * x for x in u)) or 1.0
        u = [x / norm for x in u]
        best, arg = -math.inf, None
        for p in order:
            if best >= 0 and p[0] * p[0] + p[1] * p[1] + p[2] * p[2] <= best * best:
                break                                   # no atom nearer the center can be farther along u
            d = p[0] * u[0] + p[1] * u[1] + p[2] * u[2]
            if d > best:
                best, arg = d, p
        if arg is not None and arg not in keep:
            keep.append(arg)
    return [list(p) for p in keep]


def _excluded_volume(points: list[tuple], radii: list[float]) -> float:
    """The volume of the union of the heavy atoms' spheres grown by EXCLUSION_PROBE_A, on a 1 Å grid: where no water
    of tleap's can be."""
    if not points:
        return 0.0
    rad = [r + EXCLUSION_PROBE_A for r in radii]
    reach = max(rad) + 2.0
    lo = [min(p[k] for p in points) - reach for k in range(3)]
    n = [int(max(p[k] for p in points) - lo[k] + reach) + 2 for k in range(3)]
    sy, sx = n[2], n[1] * n[2]                          # a voxel (i, j, k) is the integer i sx + j sy + k
    stencils: dict[float, list[int]] = {}
    cells: set[int] = set()
    for p, r in zip(points, rad):
        offs = stencils.get(r)
        if offs is None:
            m = int(r) + 1
            offs = stencils[r] = [i * sx + j * sy + k for i in range(-m, m + 1) for j in range(-m, m + 1)
                                  for k in range(-m, m + 1) if i * i + j * j + k * k <= r * r]
        cells.update(map((int(p[0] - lo[0]) * sx + int(p[1] - lo[1]) * sy + int(p[2] - lo[2])).__add__, offs))
    return float(len(cells))


def solute_shape(atoms: list[tuple]) -> dict:
    """What the water box depends on, from the solute's atoms (pdb_atoms, read_atoms): the heavy atoms and all the atoms
    the build will have, the vdW box on the principal axes, the atom-center extent on them, the atoms farthest along the
    body diagonals (|x|, |y|, |z| about the vdW box's center), the vdW box in the file's own frame and the volume kept
    from the water. A file without hydrogens (under a quarter of its atoms) is measured on its heavy atoms, with
    allowances for the hydrogens tleap adds. Empty when there are no atoms."""
    if not atoms:
        return {}
    with_h = sum(1 for a in atoms if a[0] == "H") >= 0.25 * len(atoms)
    pts = atoms if with_h else [a for a in atoms if a[0] != "H"]
    if not pts:
        return {}
    # all the atoms the build will have: a residue with no hydrogens in the file gets those of its kind (none for an
    # ion, one per heavy atom for a kind not in the table)
    per_res: dict = {}
    for a in atoms:
        r = per_res.setdefault((a[2], a[1]), [0, 0, ""])
        r[a[0] == "H"] += 1
        r[2] = r[2] or (a[0] if a[0] != "H" else "")
    total = heavy_atoms = 0
    for (_, resn), (heavy, h, element) in per_res.items():
        heavy_atoms += heavy
        if with_h and h:
            total += heavy + h
        elif heavy == 1 and element not in ("C", "N", "O", "S", "P"):
            total += heavy
        else:
            total += heavy + HYDROGENS.get(resn, heavy)
    radii = [_radius(a) for a in pts]
    n = len(pts)
    mean = [sum(a[3 + k] for a in pts) / n for k in range(3)]
    rel = [(a[3] - mean[0], a[4] - mean[1], a[5] - mean[2]) for a in pts]
    cov = [[sum(p[i] * p[j] for p in rel) for j in range(3)] for i in range(3)]
    axes = _jacobi(cov)
    principal = [tuple(p[0] * ax[0] + p[1] * ax[1] + p[2] * ax[2] for ax in axes) for p in rel]
    lo, hi = _vdw_box(principal, pts, radii)
    mid = [(lo[k] + hi[k]) / 2 for k in range(3)]
    centres = [max(p[k] for p in principal) - min(p[k] for p in principal) for k in range(3)]
    folded = [tuple(abs(p[k] - mid[k]) for k in range(3)) for p in principal]
    flo, fhi = _vdw_box([a[3:6] for a in pts], pts, radii)
    # the hydrogens the build adds stand out of a file that has none (an ion or a water gets none)
    adds_h = not with_h and total - heavy_atoms >= 0.25 * total
    vdw_h, centre_h, diagonal_h = H_ALLOWANCE_A if adds_h else (0.0, 0.0, 0.0)
    centres = [c + 2 * centre_h for c in centres]
    far = [[round(x + diagonal_h / SQRT3, 2) for x in p] for p in _farthest(folded, centres)]
    spheres = [a for a in atoms if a[0] != "H"]
    excluded = _excluded_volume([a[3:6] for a in spheres], [_radius(a) for a in spheres])
    waters = sum(1 for (_, resn) in per_res if resn in WATER_RESIDUES)
    return {"heavy": heavy_atoms, "atoms": total, "hydrogens": with_h, "excluded": round(excluded, -1), "waters": waters,
            "axes": [round(hi[k] - lo[k] + 2 * vdw_h, 2) for k in range(3)],
            "centres": [round(c, 2) for c in centres],
            "diagonal": far,
            "frame": [round(fhi[k] - flo[k] + 2 * vdw_h, 2) for k in range(3)]}


def shape_from_extent(extent: list[float], solute_atoms: int) -> dict:
    """A stand-in for a spec measured before solute_shape existed: the extent taken as the principal one, the vdW box
    the extent plus a carbon's radius on each side, the diagonal that of an ellipsoid of that extent."""
    ext = [float(e) for e in extent]
    centres = [e + 2 * H_ALLOWANCE_A[1] for e in ext]
    pad = RADIUS["C"] + H_ALLOWANCE_A[0]
    far = []
    for i in range(7):                                  # points of the ellipsoid's surface in the positive octant
        for j in range(7):
            th, ph = (i + 0.5) * math.pi / 14, (j + 0.5) * math.pi / 14
            n = (math.sin(th) * math.cos(ph), math.sin(th) * math.sin(ph), math.cos(th))
            far.append([round(c / 2 * x, 2) for c, x in zip(centres, n)])
    heavy = max(1, int(solute_atoms) // 2)
    return {"heavy": heavy, "atoms": int(solute_atoms), "hydrogens": False, "excluded": EXCLUDED_A3_PER_HEAVY * heavy,
            "axes": [e + 2 * pad for e in ext], "centres": centres, "diagonal": far, "frame": [e + 2 * pad for e in ext]}


def water_density(water: str) -> float:
    """Molecules per Å^3 of tleap's box of this water model (TIP3P's for a model with no tleap box here)."""
    a, b, c = WATER_BOX_A.get(water, WATER_BOX_A["TIP3P"])
    return WATERS_PER_BOX / (a * b * c)


def water_box(shape: dict, box_shape: str, padding: float, water: str) -> dict:
    """The box tleap builds around a solute of this shape (solute_shape) and the waters in it: {"desc", "cell" (Å; the
    lattice vector's length for the compact box), "region_A3" (where the water goes), "buffer" (Å, the padding after
    the hexagonal-face check), "waters"}."""
    b = float(padding)
    if box_shape == "compact":
        half = [c / 2 + b for c in shape["centres"]]
        u = [half[1] * half[2], half[0] * half[2], half[0] * half[1]]
        norm = math.sqrt(sum(x * x for x in u)) or 1.0
        u = [x / norm for x in u]
        far = max((p[0] * u[0] + p[1] * u[1] + p[2] * u[2] for p in shape.get("diagonal") or []), default=0.0)
        face = 0.5 * math.sqrt(sum(h * h for h in half))
        if face > 0 and far + b > face:
            b *= (far + b) / face
        d = max(shape["axes"]) / 2 + b                  # the square faces' distance from the center
        region, surface = 4 * d ** 3, (3 + 6 * SQRT3) * d ** 2
        cell = SQRT3 * (d + 0.5)
        out = {"desc": f"{cell:.0f} Å compact box", "cell": [round(cell, 2)] * 3}
    else:
        sides = [max(shape["axes"]) + 2 * b] * 3 if box_shape == "cubic" else [f + 2 * b for f in shape["frame"]]
        region = sides[0] * sides[1] * sides[2]
        surface = 2 * (sides[0] * sides[1] + sides[1] * sides[2] + sides[0] * sides[2])
        cell = [s + BOX_CELL_EXTRA_A for s in sides]
        desc = f"{cell[0]:.0f} Å cube" if box_shape == "cubic" else " × ".join(f"{c:.0f}" for c in cell) + " Å"
        out = {"desc": desc, "cell": [round(c, 2) for c in cell]}
    free = region - FACE_LOSS_A * surface - EXCLUDED_SCALE * shape["excluded"]
    out.update(region_A3=round(region), buffer=round(b, 2), waters=max(0, int(round(free * water_density(water)))))
    return out
