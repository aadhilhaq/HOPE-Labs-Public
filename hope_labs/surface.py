"""Molecular surfaces for the viewers, computed by MSMS.

By Aadhil Haq

This module is shared, byte for byte, with other tools by the same authors,
so a surface drawn by any of them is computed the same way; keep the copies
identical.

MSMS, by Michel Sanner, triangulates the solvent excluded surface: the
surface a probe sphere of 1.5 Angstrom traces as it rolls over the atoms.
It is run from the ADCP 1.1 suite, which carries it as the Python library
mslib, through msms_mslib.py beside this module and the suite's own Python;
or as an msms program, where one is installed and named. Neither is copied
into this package (see the third party notices). Published work that shows
one of these surfaces cites Sanner, Olson and Spehner, Biopolymers
38:305-320, 1996.

MSMS's own inputs are used. Each atom is a sphere with the radius
pdb_to_xyzr, MSMS's own converter, would give it. The suite carries that
table as MolKit's radii_patterns.py, "array of radii taken from
pdb_to_xyzr", and it is read from the installed tree as data rather than
imported or copied. Hydrogens are left out and the heavy atoms take the
table's united atom radii, which is what pdb_to_xyzr does by default, so a
structure with hydrogens and the same structure without them give the same
surface. Where the table cannot be read, an element table of this module's
own stands in, and the answer says so.

MSMS triangulates one connected surface, the outer surface of whichever
piece holds the atom it starts from. A receptor in separate pieces, two
chains that do not touch or a domain whose linker is missing, would show a
surface on that one piece only. So the atoms are first grouped into pieces
that touch, MSMS is run on each piece, and the outer surfaces are drawn
together. MSMS's own -all_components finds every piece too, but it also
triangulates every cavity sealed inside the protein, which the see-through
surface would show as bubbles (45 of them in the antibody 1IGT); it took four
times as long, and on GroEL with GroES it crashed.

The answer is the triangulation itself: vertices, normals and triangles,
packed as little-endian binary and base64 encoded, so a surface of fifty
thousand vertices costs the page about a megabyte rather than several. Each
vertex also carries the residue of the atom it lies over, so a viewer can
color a patch of the surface by residue or by chain. VIEWER_JS is the other
half: the page code that turns an answer into a 3Dmol shape.

Where msms is absent, refuses, runs out of time or memory, or the structure
is larger than is sensible to triangulate on a shared login node, the answer
says why and the viewer draws its own surface instead.
"""
import ast
import base64
import glob
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time

#: The probe, in Angstrom: a water molecule, and MSMS's own default.
PROBE = 1.5

#: Above this many heavy atoms no surface is computed and the viewer draws
#: its own. MSMS on GroEL with GroES (1AON, 58,674 atoms) at the density
#: below took a few seconds and well under the memory limit on a compute
#: node; this leaves room above that for a slower login node.
MAX_ATOMS = 120000

#: Wall seconds and address space MSMS may use. It runs on the machine that
#: serves the page, which is usually a shared login node, where long or
#: large processes get the account flagged. GroEL with GroES peaked at about
#: 250 MB and 5 s, so these leave room without letting one surface hog it.
TIMEOUT = 30
MEMORY = 1 << 30

#: Each answer is cached by what it was computed from, so a structure is
#: triangulated once whoever asks for it next. 4: every separate piece has
#: its surface, where 3 and before had only one; one conformer per atom.
CACHE_VERSION = 4

#: One MSMS at a time, so two people opening large structures at once do
#: not both hold their memory on the same login node.
_RUN_LOCK = threading.Lock()


class SurfaceError(Exception):
    """Why no surface could be computed. The page shows it, quietly, and
    draws its own surface instead."""


def density_for(n_atoms):
    """Vertices per square Angstrom, by size: fine for a small receptor,
    coarser as it grows, so a small one is smooth and a large complex stays
    a size a browser draws without trouble. MSMS's own default is 1.0.

    Measured with the msms program 2.6.1 on a compute node: MDM2 (1YCR
    chain A, 705 heavy atoms) at 3.0 gives 11,488 vertices in 0.15 s; the
    dengue envelope dimer (7A3R, 5,912) at 1.0 gives 38,530 in 0.4 s; GroEL
    with GroES (1AON, 58,870) at 0.5 gives 234,229 in 4.5 s.
    """
    n = int(n_atoms or 0)
    if n <= 2000:
        return 3.0
    if n <= 5000:
        return 2.0
    if n <= 15000:
        return 1.0
    if n <= 40000:
        return 0.5
    return 0.3


class Atom:
    __slots__ = ("serial", "name", "resname", "chain", "resnum", "icode",
                 "x", "y", "z", "element", "hetatm", "altloc")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


WATER = {"HOH", "WAT", "DOD", "H2O", "TIP", "TIP3", "SOL"}

#: AutoDock's atom types in a PDBQT file, as elements. The rest are an
#: element already (Cl, Zn, Br and so on).
_AD_ELEMENT = {"A": "C", "C": "C", "CG0": "C", "CG1": "C", "CG2": "C",
               "CG3": "C", "G0": "C", "G1": "C", "G2": "C", "G3": "C",
               "N": "N", "NA": "N", "NS": "N", "OA": "O", "OS": "O",
               "SA": "S", "S": "S", "HD": "H", "HS": "H", "H": "H"}

_FLOAT = re.compile(r"-?\d+\.\d+")


def format_of(name="", text=""):
    """"cif", "pdbqt" or "pdb", from a file's name, else from its text."""
    n = (name or "").lower()
    if n.endswith(".gz"):
        n = n[:-3]
    if n.endswith((".cif", ".mmcif")):
        return "cif"
    if n.endswith(".pdbqt"):
        return "pdbqt"
    if n.endswith((".pdb", ".ent")):
        return "pdb"
    head = (text or "")[:20000]
    if head.lstrip().startswith("data_") or "\n_atom_site." in head:
        return "cif"
    return "pdb"


def _number(text):
    """A residue number: decimal, or hexadecimal past 9,999 residues, which
    is how OpenMM writes one it cannot keep."""
    s = text.strip()
    try:
        return int(s)
    except ValueError:
        return int(s, 16)


def _coords(L):
    """x, y and z from their fixed columns, or from the numbers in them when
    a wide number pushed the columns along."""
    try:
        return float(L[30:38]), float(L[38:46]), float(L[46:54])
    except ValueError:
        pass
    got = _FLOAT.findall(L[30:66])
    if len(got) < 3:
        raise ValueError("no coordinates in %r" % L[30:54])
    return float(got[0]), float(got[1]), float(got[2])


def _read_pdb(text, pdbqt=False):
    """The atom records of the first model. 3Dmol stops at the first END and
    draws the first model, so this does too."""
    out, seen_model = [], False
    for L in text.splitlines():
        rec = L[:6]
        if rec == "MODEL ":
            if seen_model and out:
                break
            seen_model = True
            continue
        if rec == "ENDMDL" or rec.rstrip() == "END":
            if out:
                break
            continue
        if rec not in ("ATOM  ", "HETATM"):
            continue
        try:
            resnum = _number(L[22:26])
            x, y, z = _coords(L)
        except ValueError:
            continue
        if pdbqt:
            t = L[77:80].strip()
            element = _AD_ELEMENT.get(t, t.upper())
        else:
            element = L[76:78].strip()
        out.append(Atom(name=L[12:16].strip(), resname=L[17:20].strip(),
                        chain=L[21:22].strip(), resnum=resnum,
                        icode=L[26:27].strip(), altloc=L[16:17].strip(),
                        x=x, y=y, z=z, element=element,
                        hetatm=rec == "HETATM"))
    return out


def _read_cif(text):
    """The atom_site loop of the first model, with the author's chain and
    residue numbers, which are the ones 3Dmol shows."""
    cols, rows, in_loop, in_header = [], [], False, False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("loop_"):
            if cols and rows:
                break
            in_loop, in_header, cols = True, True, []
            continue
        if in_loop and s.startswith("_atom_site."):
            cols.append(s.split(".", 1)[1].split()[0])
            in_header = True
            continue
        if in_loop and cols and in_header and not s.startswith("_"):
            in_header = False
        if cols and not in_header:
            if not s or s.startswith("#") or s.startswith("loop_"):
                if rows:
                    break
                in_loop, cols = False, []
                continue
            rows.append(s.split())
    if not cols or not rows:
        return []
    ix = {c: i for i, c in enumerate(cols)}

    def g(r, *names, **kw):
        for n in names:
            if n in ix and ix[n] < len(r):
                v = r[ix[n]]
                return kw.get("default") if v in (".", "?") else v
        return kw.get("default")

    out, first = [], None
    for r in rows:
        if len(r) < len(cols):
            continue
        m = g(r, "pdbx_PDB_model_num", default="1")
        if first is None:
            first = m
        elif m != first:
            continue
        try:
            out.append(Atom(
                name=(g(r, "auth_atom_id", "label_atom_id", default="")
                      or "").strip('"'),
                resname=g(r, "auth_comp_id", "label_comp_id", default="") or "",
                chain=g(r, "auth_asym_id", "label_asym_id", default="") or "",
                resnum=int(float(g(r, "auth_seq_id", "label_seq_id",
                                   default="0") or 0)),
                icode=(g(r, "pdbx_PDB_ins_code", default="") or "").strip(),
                altloc=(g(r, "label_alt_id", default="") or "").strip(),
                x=float(g(r, "Cartn_x")), y=float(g(r, "Cartn_y")),
                z=float(g(r, "Cartn_z")),
                element=g(r, "type_symbol", default="") or "",
                hetatm=(g(r, "group_PDB", default="ATOM") == "HETATM")))
        except (TypeError, ValueError):
            continue
    return out


def read_text(text, fmt=None, name=""):
    """[Atom] from the text of a PDB, PDBQT or mmCIF file: the first model."""
    fmt = fmt or format_of(name, text)
    if fmt == "cif":
        return _read_cif(text or "")
    return _read_pdb(text or "", pdbqt=(fmt == "pdbqt"))


def select(atoms, chains=None, invert=False, water=False):
    """The atoms a viewer's selection names: these chains, or every chain
    but these when `invert`, and no waters unless asked for. A water is a
    sphere of its own, and a crystal's hundreds of them would each be
    triangulated as a bubble beside the protein."""
    want = {(c or "").strip() for c in (chains or ())}
    out = []
    for a in atoms:
        if not water and (a.resname or "").strip().upper() in WATER:
            continue
        if want and (((a.chain or "").strip() in want) == bool(invert)):
            continue
        out.append(a)
    return out


def one_conformer(atoms):
    """The atoms with one alternate location per residue: those without one,
    and those of the first location the residue lists, normally A, which is
    the one 3Dmol draws, so the surface and the drawing agree."""
    first = {}
    for a in atoms:
        alt = (getattr(a, "altloc", "") or "").strip()
        if not alt:
            yield a
            continue
        k = (a.chain, a.resnum, a.icode or "")
        if first.setdefault(k, alt) == alt:
            yield a


# --- radii -----------------------------------------------------------------

#: Element radii for a heavy atom when the suite's table cannot be read, in
#: Angstrom, as united atoms: a carbon or nitrogen counts the hydrogens it
#: carries, as pdb_to_xyzr's own united radii do.
ELEMENT_RADII = {"C": 1.90, "N": 1.70, "O": 1.50, "S": 1.80, "P": 1.80,
                 "SE": 2.00, "ZN": 1.40, "MG": 1.60, "CA": 1.97, "FE": 1.30,
                 "MN": 1.24, "CU": 1.40, "CO": 1.25, "NI": 1.25, "CD": 1.49,
                 "NA": 1.80, "K": 2.20, "CL": 1.75, "BR": 1.85, "I": 1.98,
                 "F": 1.47}
DEFAULT_RADIUS = 1.80

_TABLES = {}


def adfr_home(msms_exe):
    """The suite MSMS comes from: the ADCP 1.1 suite's folder, from its
    Python, or the folder above an msms program's bin folder."""
    if not msms_exe:
        return None
    first = msms_exe[0] if isinstance(msms_exe, (list, tuple)) else msms_exe
    return os.path.dirname(os.path.dirname(os.path.realpath(first)))


def _cmd(exe):
    """MSMS as a command: the msms program's path, or a list such as a
    Python and a script that take the program's arguments and write its
    files (the ADCP 1.1 suite's mslib, for one)."""
    return list(exe) if isinstance(exe, (list, tuple)) else [exe]


def _env(exe):
    """The environment MSMS runs in: as it is for the program, and for the
    library the suite's own, with nothing of this package's Python in it."""
    if not isinstance(exe, (list, tuple)):
        return None
    env = dict(os.environ)
    for k in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(k, None)
    env["PYTHONNOUSERSITE"] = "1"
    # mslib loads numpy, whose OpenBLAS starts a thread for every CPU; on a
    # 48-core node under the memory cap MSMS runs with, they cannot all be
    # given memory and no surface is written.
    env["OPENBLAS_NUM_THREADS"] = "1"
    lib = os.path.join(adfr_home(exe), "lib")
    env["LD_LIBRARY_PATH"] = (env.get("LD_LIBRARY_PATH", "") + os.pathsep
                              + lib).strip(os.pathsep)
    return env


def describe(exe):
    """The MSMS in use, in words, for the page's list of tools."""
    if not exe:
        return ""
    if isinstance(exe, (list, tuple)):
        return "mslib in %s (ADCP 1.1 suite)" % adfr_home(exe)
    return exe


def radii_file(home):
    """MolKit's copy of pdb_to_xyzr's table in the ADCP 1.1 suite's folder,
    or None."""
    if not home:
        return None
    found = glob.glob(os.path.join(home, "lib", "python3*", "site-packages",
                                   "MolKit", "radii_patterns.py"))
    return found[0] if found else None


def read_radii_table(path):
    """(patterns, radii) from radii_patterns.py, read as data.

    patterns is [(residue regex, atom regex, type number)] in the table's
    order, the first match winning, as in pdb_to_xyzr; radii is {type
    number: (covalent, explicit, united)}. Only the two literal assignments
    are evaluated, with ast.literal_eval, so nothing in the file is run.
    """
    if path in _TABLES:
        return _TABLES[path]
    import warnings
    with warnings.catch_warnings():
        # The file writes regular expressions in plain strings ("\\*"),
        # which a newer Python warns about while reading them the same way.
        warnings.simplefilter("ignore")
        tree = ast.parse(open(path).read(), path)
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ("AtomRadiiPatterns", "AAradii"):
            found[node.targets[0].id] = ast.literal_eval(node.value)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pats = [(re.compile(r), re.compile(a), int(n))
                for r, a, n in found["AtomRadiiPatterns"]]
    radii = {int(k): (float(v[0]), float(v[1]), float(v[2]))
             for k, v in found["AAradii"].items()}
    _TABLES[path] = (pats, radii)
    return pats, radii


def radius_of(resname, name, element, table=None):
    """An atom's united atom radius: from pdb_to_xyzr's table when there is
    one and it names the atom, from the element otherwise."""
    if table is not None:
        pats, radii = table
        res = (resname or "XXX").strip() or "XXX"
        nm = (name or "").strip()
        for rp, ap, num in pats:
            if ap.search(nm) and rp.search(res):
                r = radii.get(num)
                if r:
                    return r[2]
                break
    el = (element or "").strip().upper() or (name or "").strip()[:1].upper()
    return ELEMENT_RADII.get(el, DEFAULT_RADIUS)


def _is_h(a):
    e = (getattr(a, "element", "") or "").strip().upper()
    if e:
        return e in ("H", "D")
    n = (getattr(a, "name", "") or "").strip().lstrip("0123456789")
    return n[:1] in ("H", "D")


def sphere_rows(atoms, table=None):
    """([(x, y, z, r)], residue keys, index of each sphere's residue) for the
    heavy atoms, one conformer each. Hydrogens are left out, as pdb_to_xyzr
    leaves them out by default: the united atom radii already count them."""
    rows, keys, which, seen = [], [], [], {}
    for a in one_conformer(atoms):
        if _is_h(a):
            continue
        r = radius_of(a.resname, a.name, a.element, table)
        rows.append((float(a.x), float(a.y), float(a.z), r))
        k = "%s:%s%s" % ((a.chain or "").strip() or "_", a.resnum,
                         (a.icode or "").strip())
        if k not in seen:
            seen[k] = len(keys)
            keys.append(k)
        which.append(seen[k])
    return rows, keys, which


def xyzr_text(rows):
    """MSMS's input: one sphere per line."""
    return "".join("%.3f %.3f %.3f %.2f\n" % row for row in rows)


def spheres(atoms, table=None):
    """(xyzr text, residue keys, index of each sphere's residue) for the
    heavy atoms (sphere_rows, as MSMS reads them)."""
    rows, keys, which = sphere_rows(atoms, table)
    return xyzr_text(rows), keys, which


# --- pieces ------------------------------------------------------------------

#: The neighboring cells of a grid, each pair once: the cell itself is
#: handled apart.
_HALF = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
         for dz in (-1, 0, 1) if (dx, dy, dz) > (0, 0, 0)]


def pieces(rows, probe=PROBE):
    """The spheres grouped into pieces whose surfaces join: [[index]],
    largest first.

    Two atoms belong to one piece when the probe cannot pass between them,
    that is when they are closer than their two radii and the probe's
    diameter. A piece's outer surface is then one connected surface, which
    is what MSMS triangulates when it is given that piece.

    Done without comparing every atom with every other. Neighbors in the
    file are joined first, because bonded atoms follow each other, which
    leaves a few groups per chain. Then on a grid whose cells are as wide as
    the longest reach, only groups in the same or neighboring cells that are
    not already one piece are compared, atom by atom, until one pair is close
    enough. On a compute node the 10,434 atoms of the antibody 1IGT took
    0.03 s and the 58,870 of GroEL with GroES 0.2 s.
    """
    n = len(rows)
    if not n:
        return []
    parent = list(range(n))

    def find(i):
        root = i
        while parent[root] != root:
            root = parent[root]
        while parent[i] != root:
            parent[i], i = root, parent[i]
        return root

    two_p = 2.0 * probe
    for i in range(1, n):
        x0, y0, z0, r0 = rows[i - 1]
        x1, y1, z1, r1 = rows[i]
        lim = r0 + r1 + two_p
        if (x0 - x1) ** 2 + (y0 - y1) ** 2 + (z0 - z1) ** 2 < lim * lim:
            a, b = find(i - 1), find(i)
            if a != b:
                parent[a] = b

    def touch(la, lb):
        for i in la:
            xi, yi, zi, ri = rows[i]
            ri += two_p
            for j in lb:
                xj, yj, zj, rj = rows[j]
                lim = ri + rj
                dx = xi - xj
                if dx >= lim or -dx >= lim:
                    continue
                if dx * dx + (yi - yj) ** 2 + (zi - zj) ** 2 < lim * lim:
                    return True
        return False

    cell = 2.0 * max(r for _x, _y, _z, r in rows) + two_p
    grid = {}
    for i, (x, y, z, _r) in enumerate(rows):
        grid.setdefault((int(x // cell), int(y // cell), int(z // cell)),
                        []).append(i)
    groups = {}
    for key, members in grid.items():
        g = {}
        for i in members:
            g.setdefault(find(i), []).append(i)
        groups[key] = list(g.values())
    for (cx, cy, cz), ga in groups.items():
        for a in range(len(ga)):
            for b in range(a + 1, len(ga)):
                ra, rb = find(ga[a][0]), find(ga[b][0])
                if ra != rb and touch(ga[a], ga[b]):
                    parent[ra] = rb
        for dx, dy, dz in _HALF:
            gb = groups.get((cx + dx, cy + dy, cz + dz))
            if not gb:
                continue
            for la in ga:
                for lb in gb:
                    ra, rb = find(la[0]), find(lb[0])
                    if ra != rb and touch(la, lb):
                        parent[ra] = rb
    out = {}
    for i in range(n):
        out.setdefault(find(i), []).append(i)
    return sorted(out.values(), key=lambda m: (-len(m), m[0]))


# --- msms itself -------------------------------------------------------------

#: Where the ADCP 1.1 suite is looked for inside a place: the place itself,
#: or the folders its installers put it in.
SUITE_SUBDIRS = ("", "adcpsuite", os.path.join("tools", "docking", "adcpsuite"))


def find_suite(places=(), env="ADCPSUITE_HOME"):
    """The ADCP 1.1 suite's folder: $ADCPSUITE_HOME, then inside each of
    `places`, or None. It is the one whose own Python has mslib."""
    cands = [os.environ.get(env)]
    for place in places or ():
        if place:
            cands += [os.path.join(place, sub) for sub in SUITE_SUBDIRS]
    for home in cands:
        if (home and os.path.isfile(os.path.join(home, "bin", "python"))
                and glob.glob(os.path.join(home, "lib", "python3*", "site-packages",
                                           "mslib", "__init__.py"))):
            return os.path.realpath(home)
    return None


def find_msms(places=(), env="MSMS_EXE"):
    """How MSMS is run here: $MSMS_EXE, or an msms program in the bin
    folder of one of `places`; else the ADCP 1.1 suite's MSMS library,
    mslib, as the command [suite's Python, msms_mslib.py], which takes the
    program's arguments and writes its files; else an msms on the PATH.
    None when there is none of them."""
    given = os.environ.get(env)
    if given and os.path.isfile(given) and os.access(given, os.X_OK):
        return given
    for place in places or ():
        if not place:
            continue
        for sub in ("bin", ""):
            p = os.path.join(place, sub, "msms")
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p
    home = find_suite(places)
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "msms_mslib.py")
    if home and os.path.isfile(script):
        return [os.path.join(home, "bin", "python"), script]
    return shutil.which("msms")


def version(exe):
    """The first line msms prints about itself, or ''. It names its
    version and build."""
    if not exe:
        return ""
    try:
        p = subprocess.run(_cmd(exe), capture_output=True, text=True, timeout=20,
                           stdin=subprocess.DEVNULL, env=_env(exe))
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in (p.stdout + "\n" + p.stderr).splitlines():
        if "MSMS" in line and re.search(r"\d", line):
            return line.strip()
    return ""


def _limits():
    """Run in the child before msms starts: cap its address space."""
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY, MEMORY))
    except (ImportError, ValueError, OSError):
        pass


def run_msms(exe, xyzr, workdir, probe=PROBE, density=1.0, timeout=TIMEOUT,
             name="surface"):
    """(vertices, normals, closest sphere per vertex, faces) from msms, or
    SurfaceError. Indices are 0-based.

    Without -all_components msms writes one component, the outer surface of
    the spheres it is given, as <name>.vert and <name>.face. (With it, it
    writes component 0 there and each further one as <name>_1, <name>_2 and
    so on, cavities included; that is not used.)"""
    inp = os.path.join(workdir, name + ".xyzr")
    out = os.path.join(workdir, name)
    with open(inp, "w") as fh:
        fh.write(xyzr)
    cmd = _cmd(exe) + ["-if", inp, "-of", out, "-probe_radius", "%g" % probe,
                       "-density", "%g" % density, "-no_area"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, cwd=workdir, env=_env(exe),
                           preexec_fn=_limits if os.name == "posix" else None)
    except subprocess.TimeoutExpired:
        raise SurfaceError("MSMS took longer than %d s" % timeout)
    except OSError as exc:
        raise SurfaceError("msms could not be started (%s)" % exc)
    vert, face = out + ".vert", out + ".face"
    if not (os.path.isfile(vert) and os.path.isfile(face)):
        said = [l.strip() for l in (p.stdout + "\n" + p.stderr).splitlines()
                if l.strip()]
        why = said[-1] if said else "no output"
        if p.returncode and p.returncode < 0:
            why = "stopped by signal %d; %s" % (-p.returncode, why)
        raise SurfaceError("MSMS wrote no surface (%s)" % why[:160])
    v, n, atom = [], [], []
    for line in open(vert):
        if line.startswith("#"):
            continue
        f = line.split()
        if len(f) < 8:
            continue                       # the counts line
        v.extend((float(f[0]), float(f[1]), float(f[2])))
        n.extend((float(f[3]), float(f[4]), float(f[5])))
        atom.append(max(0, int(f[7]) - 1))
    faces = []
    for line in open(face):
        if line.startswith("#"):
            continue
        f = line.split()
        if len(f) < 5:
            continue
        # In MSMS's own order: 3Dmol lights a triangle from the side its
        # winding faces, and MSMS winds them facing out, the way its normals
        # point.
        faces.extend((int(f[0]) - 1, int(f[1]) - 1, int(f[2]) - 1))
    if not v or not faces:
        raise SurfaceError("MSMS wrote an empty surface")
    return v, n, atom, faces


def _b64(fmt, values):
    return base64.b64encode(struct.pack("<%d%s" % (len(values), fmt),
                                        *values)).decode("ascii")


def compute(atoms, exe, probe=PROBE, density=None, timeout=TIMEOUT,
            workdir=None):
    """The surface of these atoms as a dict the page can draw: the outer
    surface of every separate piece, one mesh.

    Raises SurfaceError with a reason a person can read.
    """
    if not exe or not all(os.path.isfile(p) for p in _cmd(exe)):
        raise SurfaceError("MSMS is not installed here: neither the ADCP 1.1 "
                           "suite's MSMS library (mslib) nor an msms program "
                           "was found")
    table_path = radii_file(adfr_home(exe))
    table = None
    if table_path:
        try:
            table = read_radii_table(table_path)
        except Exception:                                      # noqa: BLE001
            table = None
    rows, keys, which = sphere_rows(atoms, table)
    n = len(rows)
    if not n:
        raise SurfaceError("there are no heavy atoms to put a surface on")
    if n > MAX_ATOMS:
        raise SurfaceError("%d atoms is more than this server triangulates "
                           "(%d)" % (n, MAX_ATOMS))
    dens = float(density or density_for(n))
    t0 = time.time()
    parts = pieces(rows, probe)
    grouped = time.time() - t0
    tmp = tempfile.mkdtemp(prefix="msms_", dir=workdir)
    v, nrm, atom, faces, missed = [], [], [], [], []
    try:
        with _RUN_LOCK:
            # One limit for all the pieces together, as there was for one.
            end = time.time() + timeout
            for k, part in enumerate(parts):
                left = end - time.time()
                if left <= 0:
                    raise SurfaceError("MSMS took longer than %d s" % timeout)
                try:
                    pv, pn, pa, pf = run_msms(
                        exe, xyzr_text([rows[i] for i in part]), tmp, probe,
                        dens, left, name="piece%d" % k)
                except SurfaceError as exc:
                    # The largest piece is the receptor; without it there is
                    # nothing worth drawing. A stray ion or a fragment that
                    # MSMS cannot close is left without one, and said.
                    if k == 0 or "longer than" in str(exc):
                        raise
                    missed.append(len(part))
                    continue
                base = len(v) // 3
                v.extend(pv)
                nrm.extend(pn)
                atom.extend(part[a] if a < len(part) else part[0] for a in pa)
                faces.extend(base + f for f in pf)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    secs = time.time() - t0
    res = [which[a] if a < len(which) else 0 for a in atom]
    out = {"source": "msms", "probe": probe, "density": dens,
           "atoms": n, "vertices": len(v) // 3, "triangles": len(faces) // 3,
           "pieces": len(parts) - len(missed),
           "piece_atoms": [len(p) for p in parts][:20],
           "seconds": round(secs, 2), "grouping_seconds": round(grouped, 2),
           "radii": ("pdb_to_xyzr united atom radii, read from %s"
                     % os.path.relpath(table_path, adfr_home(exe))
                     if table else "element radii (pdb_to_xyzr's table "
                     "was not found)"),
           "encoding": "base64 little-endian: verts and norms float32 x,y,z; "
                       "faces uint32 0-based; res uint32 index into residues",
           "verts": _b64("f", v), "norms": _b64("f", nrm),
           "faces": _b64("I", faces), "res": _b64("I", res),
           "residues": keys}
    if missed:
        out["unsurfaced"] = {"pieces": len(missed), "atoms": sum(missed)}
    return out


def cache_key(atoms, exe, probe=PROBE, density=None):
    """What a cached surface was computed from: the spheres, the probe, the
    density and the msms program."""
    h = hashlib.sha1()
    for a in atoms:
        if _is_h(a):
            continue
        h.update(("%s %s %s %s %s %s %.3f %.3f %.3f\n"
                  % (a.chain, a.resnum, a.icode or "",
                     getattr(a, "altloc", "") or "", a.resname, a.name,
                     a.x, a.y, a.z)).encode())
    try:
        for part in _cmd(exe):
            st = os.stat(part)
            h.update(("%s %d %d" % (part, st.st_size, int(st.st_mtime))).encode())
    except (OSError, TypeError):
        h.update(b"no msms")
    h.update(("%g %s %d" % (probe, density, CACHE_VERSION)).encode())
    return h.hexdigest()


def cached(atoms, exe, cache_dir, probe=PROBE, density=None, log=None):
    """compute(), read from cache_dir when it was computed before, and
    written there when it was not. The cache is an optimization: a folder
    that cannot be written is not an error."""
    key = cache_key(atoms, exe, probe, density)
    path = os.path.join(cache_dir, key + ".json") if cache_dir else None
    if path and os.path.isfile(path):
        try:
            got = json.load(open(path))
            got["cached"] = True
            return got
        except (OSError, ValueError):
            pass
    got = compute(atoms, exe, probe, density)
    if path:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            tmp = path + ".part"
            with open(tmp, "w") as fh:
                json.dump(got, fh)
            os.replace(tmp, path)
        except OSError:
            pass
    got["cached"] = False
    return got


def answer(text, exe, cache_dir=None, name="", fmt=None, chains=None,
           invert=False, water=False):
    """What a viewer asks for, from the text of the file it drew: the
    surface of the atoms its selection names (select), cached, or
    {"source": "fallback", "why": ...} when MSMS cannot give one. Never
    raises, so a server can hand it straight to the page."""
    try:
        atoms = select(read_text(text, fmt, name), chains, invert, water)
        got = cached(atoms, exe, cache_dir)
        got["msms"] = exe
        return got
    except SurfaceError as exc:
        return {"source": "fallback", "why": str(exc)}
    except Exception as exc:                                # noqa: BLE001
        return {"source": "fallback",
                "why": "the surface could not be computed (%s)" % exc}


VIEWER_JS = r"""
/* Molecular surfaces computed by MSMS on the server (surface.py) and drawn
   here as a mesh: vertices, normals and triangles, with the residue each
   vertex lies over. Where MSMS cannot give one, the answer says why and the
   page draws 3Dmol's own surface instead. */
const MSMS_SURFS = {};
function msmsBytes(t){
  const b = atob(t), u = new Uint8Array(b.length);
  for(let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i);
  return u.buffer;
}
/* The answer for a key: null while it is being fetched, then the answer.
   `then` is called with it once it arrives. */
function msmsSurface(key, url, then){
  if(key in MSMS_SURFS) return MSMS_SURFS[key];
  MSMS_SURFS[key] = null;
  fetch(url, {credentials: 'same-origin'})
    .then(r => r.json())
    .catch(e => ({error: String(e)}))
    .then(d => {
      MSMS_SURFS[key] = d && d.source ? d
        : {source: 'fallback', why: (d && (d.error || d.why)) || 'no answer'};
      if(then) then(MSMS_SURFS[key]);
    });
  return null;
}
function msmsMesh(d){
  if(d._mesh) return d._mesh;
  const v = new Float32Array(msmsBytes(d.verts));
  const n = new Float32Array(msmsBytes(d.norms));
  const f = new Uint32Array(msmsBytes(d.faces));
  const r = new Uint32Array(msmsBytes(d.res));
  const va = [], na = [];
  for(let i = 0; i < v.length; i += 3){
    va.push({x: v[i], y: v[i+1], z: v[i+2]});
    na.push({x: n[i], y: n[i+1], z: n[i+2]});
  }
  d._mesh = {vertexArr: va, normalArr: na, faceArr: Array.from(f), res: r};
  return d._mesh;
}
/* One surface into a viewer, as custom shapes, which are returned so a page
   can take them out again (viewer.removeShape). `colourOf` is given a
   residue key ("A:96") and returns a color, or null for the style's own.
   A large surface goes in as several shapes of at most 60,000 vertices:
   3Dmol splits a larger shape into groups by itself, and per vertex colors
   then land on the wrong triangles. */
function msmsAdd(viewer, d, style, colourOf){
  const m = msmsMesh(d), CHUNK = 60000;
  const base = colourOf ? $3Dmol.CC.color(style.color || '#9fb3c8') : null;
  const memo = {};
  const colourAt = g => {
    const r = m.res[g];
    if(!(r in memo)){
      const c = colourOf(d.residues[r]);
      memo[r] = (c !== null && c !== undefined) ? $3Dmol.CC.color(c) : base;
    }
    return memo[r];
  };
  const add = (va, na, fa, ca) => {
    const spec = Object.assign({vertexArr: va, normalArr: na, faceArr: fa},
                               style);
    if(ca) spec.colorArr = ca;
    const shape = viewer.addCustom(spec);
    /* 3Dmol paints every vertex with the shape's color when it draws it,
       unless the shape has none; without this the residues' colors never
       reach the screen. */
    if(ca) delete shape.color;
    return shape;
  };
  if(m.vertexArr.length <= CHUNK)
    return [add(m.vertexArr, m.normalArr, m.faceArr,
                colourOf ? m.vertexArr.map((_, g) => colourAt(g)) : null)];
  const shapes = [], f = m.faceArr;
  let at = new Map(), va = [], na = [], fa = [], ca = [];
  const flush = () => {
    if(fa.length) shapes.push(add(va, na, fa, colourOf ? ca : null));
    at = new Map(); va = []; na = []; fa = []; ca = [];
  };
  for(let t = 0; t < f.length; t += 3){
    if(va.length > CHUNK - 3) flush();
    for(let k = 0; k < 3; k++){
      const g = f[t + k];
      let l = at.get(g);
      if(l === undefined){
        l = va.length;
        at.set(g, l);
        va.push(m.vertexArr[g]);
        na.push(m.normalArr[g]);
        if(colourOf) ca.push(colourAt(g));
      }
      fa.push(l);
    }
  }
  flush();
  return shapes;
}
"""
