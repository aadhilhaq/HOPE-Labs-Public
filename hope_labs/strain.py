"""Peptide strain: what the peptide pays to hold the shape it was docked in.

By Aadhil Haq

    strain = E(the peptide alone, in its docked shape)
           - E(the same peptide alone, relaxed from that shape)

Both energies are the peptide's own, with the receptor taken away: the force
field, the generalized Born solvent of the run's own MM-GBSA (its igb and its
salt) and the same surface term. The docked shape is the frame the binding
energy was computed on, so the strain describes the geometry the energy in
the same row describes. This is the quantity Prime MM-GBSA reports as the
ligand strain (r_psp_Lig_Strain_Energy, which is its dG_Bind minus its
dG_Bind(NS)); a pose Prime scored is given Prime's own number (`prime`), and
every other pose this one.

Nothing here is a binding energy, nothing is subtracted from one and nothing
is ranked on it. Lower is easier for the peptide.

HOW THE PROTOCOL WAS SETTLED, on 120 scored poses of docked peptides from
seven runs, 6 to 13 residues:

  The bound energy computed here is MM-GBSA's own Ligand total for the same
  frame, within 0.06 kcal/mol on every pose (mean 0.03). `measure` checks the
  two against each other on every pose that has the MM-GBSA output, so a
  peptide cut out of the wrong atoms, or measured on another solvent model,
  is refused rather than reported.

  The relaxation is conjugate gradient throughout (ntmin=0), unrestrained,
  for a fixed STEPS. sander's default, steepest descent first, left one of
  the 120 peptides 99 kcal/mol above where it started, which a minimization
  cannot do; conjugate gradient never went uphill and ended lowest of the
  four minimizers tried. L-BFGS (XMIN) was four times faster but carried 28
  of the 120 peptides into a deeper minimum further away (2 to 12 kcal/mol
  lower, 1.5 against 1.3 A median all-atom shift), which is not the nearest
  one.

  The step count is fixed rather than a gradient target, because a free
  peptide never quite stops settling: stopped at an RMS gradient of 0.05
  kcal/mol/A its energy went on falling, by 2.2 kcal/mol at the median and
  12.9 at worst, before 5000 steps. A fixed count gives the same number every
  time the same pose is measured, which is what lets two poses be compared.
  It does not make the number the bottom of the well.

  A free peptide folds onto itself in implicit solvent, so the number tends
  to grow with length: medians of 17 to 26 kcal/mol from 6 to 12 residues,
  35 at 13. Compare peptides of similar length. A docked shape with distorted
  bond angles shows as a very large strain (160 kcal/mol on one 12-residue
  pose whose angle energy alone was 158), which is what the column is for.

One file is written into each pose folder, <prefix>_strain.json, holding the
number and what it was measured with; the work itself is done in a temporary
folder. The file carries a fingerprint of the frame and the peptide topology,
so a pose scored again is measured again, and a VERSION, so a change of
protocol here measures every pose again rather than mixing two protocols in
one column.

HOPE Labs measures the binder of every full-atom design with it
(hope_labs/designscores.py), so a designed binder's strain is the same
measurement as a docked peptide's.

    python -m hope_labs.strain PATH [PATH ...] [--workers N] [--force]

PATH is a pose folder, a peptide's folder of poses, an mmgbsa folder or a run
folder; every pose folder under it is measured, and the ones already measured
are read back.
"""
import csv
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

#: The protocol's version. Raising it measures every pose again.
VERSION = 1

#: Conjugate gradient steps in the relaxation. See the module docstring.
STEPS = 5000

#: The salt every MM-GBSA here is computed at, M, when the pose folder does
#: not say otherwise.
SALTCON = 0.150

#: MMPBSA.py's own surface tension, kcal/mol/A^2, so the surface term is the
#: one the binding energy carries. sander's default is 0.005.
SURFTEN = 0.0072

#: How far the bound energy may sit from MM-GBSA's own Ligand total, kcal/mol.
#: The 120 poses measured agreed to 0.06.
AGREE = 0.5

#: Prime's column for its own ligand strain.
PRIME_KEY = "r_psp_Lig_Strain_Energy"

#: What a pose folder's own result file is called, after the prefix.
TAIL = "_strain.json"

METHOD = ("sander, the peptide alone, relaxed unrestrained by %d steps of "
          "conjugate gradient" % STEPS)

_SINGLE_IN = """peptide alone, in the docked shape
 &cntrl
  imin=1, maxcyc=1, ncyc=1, ntpr=1, ntxo=1,
  igb={igb}, saltcon={salt:.3f}, gbsa=1, surften={surften},
  cut=999.0, ntb=0,
 /
"""

_RELAX_IN = """peptide alone, relaxed from the docked shape
 &cntrl
  imin=1, ntmin=0, maxcyc={steps}, ntpr=500, ntxo=1,
  igb={igb}, saltcon={salt:.3f}, gbsa=1, surften={surften},
  cut=999.0, ntb=0,
 /
"""

_FLOAT = r"-?\d+\.\d*(?:[eE][-+]?\d+)?"


# ----------------------------------------------------------- the pose folder

def prefix_of(posedir):
    """The name a pose folder's files share, or None.

    The part before _lig.parm7, taken where the complex and receptor
    topologies are beside it, so a helper file that happens to end the same
    way is never taken for the peptide.
    """
    try:
        names = os.listdir(posedir)
    except OSError:
        return None
    found = [n[:-len("_lig.parm7")] for n in names if n.endswith("_lig.parm7")]
    found = [p for p in found
             if p + "_com.parm7" in names and p + "_rec.parm7" in names]
    return found[0] if len(found) == 1 else None


def _path(posedir, prefix, tail):
    return os.path.join(posedir, prefix + tail)


def frame_of(posedir, prefix):
    """The complex frame the binding energy was computed on, a basename.

    The one <prefix>_scored_on.txt names when the pose has it, else the
    minimized complex, else the raw pose: the same order MM-GBSA takes them
    in, so the strain and the energy describe one geometry.
    """
    told = _path(posedir, prefix, "_scored_on.txt")
    if os.path.isfile(told):
        try:
            name = open(told).read().strip()
        except OSError:
            name = ""
        if name and os.path.isfile(os.path.join(posedir, os.path.basename(name))):
            return os.path.basename(name)
    for tail in ("_min.rst7", "_com.rst7"):
        if os.path.isfile(_path(posedir, prefix, tail)):
            return prefix + tail
    return None


def measurable(posedir, prefix=None):
    """(True, None), or (False, why) for a pose that cannot be measured."""
    prefix = prefix or prefix_of(posedir)
    if not prefix:
        return False, "the pose folder holds no peptide topology"
    for tail in ("_com.parm7", "_rec.parm7", "_lig.parm7"):
        if not os.path.isfile(_path(posedir, prefix, tail)):
            return False, "the pose kept no %s" % (prefix + tail)
    if not frame_of(posedir, prefix):
        return False, "the pose kept no complex frame to measure"
    return True, None


def _flag(path, flag):
    """One %FLAG block of an Amber topology, as its fields, or None."""
    try:
        with open(path, errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    m = re.search(r"%FLAG " + flag + r"\s*\n%FORMAT\([^)]*\)\s*\n(.*?)(?=%FLAG|\Z)",
                  text, re.S)
    return m.group(1) if m else None


def natom(parm7):
    """Atoms in a topology, from POINTERS. None if unreadable."""
    block = _flag(parm7, "POINTERS")
    try:
        return int(block.split()[0]) if block else None
    except (ValueError, IndexError):
        return None


def heavy(parm7):
    """The heavy atoms' indices, in topology order. [] if unreadable."""
    z = _flag(parm7, "ATOMIC_NUMBER")
    if z:
        try:
            return [i for i, v in enumerate(z.split()) if int(v) != 1]
        except ValueError:
            pass
    mass = _flag(parm7, "MASS")
    if mass:
        try:
            return [i for i, v in enumerate(mass.split()) if float(v) > 2.0]
        except ValueError:
            return []
    return []


def residues(parm7):
    """Residues in a topology. None if unreadable."""
    block = _flag(parm7, "RESIDUE_LABEL")
    return len(block.split()) if block else None


def gb_settings(posedir, prefix):
    """(igb, saltcon, ligand mask or None) the pose's MM-GBSA used.

    From its own MMPBSA.py input when the pose kept one. Without it, igb from
    the radii the topology was built with, the way both tools choose it:
    mbondi3 is GBn2 (igb 8), anything else GB-OBC(II) (igb 5).
    """
    igb, salt, mask = None, SALTCON, None
    inp = _path(posedir, prefix, "_mmpbsa.in")
    if os.path.isfile(inp):
        text = open(inp, errors="replace").read()
        m = re.search(r"\bigb\s*=\s*(\d+)", text)
        igb = int(m.group(1)) if m else None
        m = re.search(r"\bsaltcon\s*=\s*(" + _FLOAT + ")", text)
        salt = float(m.group(1)) if m else salt
        m = re.search(r"\bligand_mask\s*=\s*[\"']([^\"']+)[\"']", text)
        mask = m.group(1).strip() if m else None
    if igb is None:
        radii = _flag(_path(posedir, prefix, "_lig.parm7"), "RADIUS_SET") or ""
        igb = 8 if "mbondi3" in radii else 5
    return igb, salt, mask


def mmgbsa_ligand(posedir, prefix):
    """MM-GBSA's own energy of the peptide in this frame, from MMPBSA.py's
    output: the TOTAL of its Ligand block. None without one."""
    dat = _path(posedir, prefix, "_mmgbsa.dat")
    try:
        text = open(dat, errors="replace").read()
    except OSError:
        return None
    if "Ligand:" not in text:
        return None
    block = text.split("Ligand:", 1)[1].split("Differences", 1)[0]
    m = re.search(r"^\s*TOTAL\s+(" + _FLOAT + ")", block, re.M)
    return float(m.group(1)) if m else None


def prime(posedir, prefix=None):
    """Prime's own ligand strain for this pose, kcal/mol, or None.

    Read from the table Prime MM-GBSA left in the pose folder; nothing is
    computed. Prime's number is on its own force field and solvent model, so
    it is reported for a pose Prime scored and never mixed with this module's
    in one column.
    """
    prefix = prefix or prefix_of(posedir)
    if not prefix:
        return None
    path = _path(posedir, prefix, "-out.csv")
    try:
        with open(path, newline="") as fh:
            for row in csv.DictReader(fh):
                v = row.get(PRIME_KEY)
                if v not in (None, ""):
                    try:
                        return float(v)
                    except ValueError:
                        return None
    except OSError:
        return None
    return None


# ------------------------------------------------------------- the result file

def fingerprint(posedir, prefix, frame):
    """What the number was measured on: the frame and the peptide topology."""
    h = hashlib.sha1()
    for name in (frame, prefix + "_lig.parm7"):
        try:
            with open(os.path.join(posedir, name), "rb") as fh:
                h.update(fh.read())
        except OSError:
            return None
        h.update(b"\0")
    return h.hexdigest()


def read(posedir, prefix=None):
    """The pose's result file when it still describes the pose, else None.

    Nothing is computed. A file from another VERSION of the protocol, or one
    whose frame or topology has changed since, is not read back.
    """
    prefix = prefix or prefix_of(posedir)
    if not prefix:
        return None
    path = _path(posedir, prefix, TAIL)
    try:
        with open(path) as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(got, dict) or got.get("version") != VERSION:
        return None
    frame = got.get("frame") or ""
    if not frame or got.get("fingerprint") != fingerprint(posedir, prefix, frame):
        return None
    return got


def _write(posedir, prefix, got):
    path = _path(posedir, prefix, TAIL)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(got, fh, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- measuring

def amber_home(home=None):
    """AmberTools' folder: given, $AMBERHOME, or the one sander is found in."""
    for cand in (home, os.environ.get("AMBERHOME")):
        if cand and os.path.isfile(os.path.join(cand, "bin", "sander")):
            return cand
    found = shutil.which("sander")
    if found:
        return os.path.dirname(os.path.dirname(os.path.realpath(found)))
    return None


def _rst7(path):
    """[(x, y, z), ...] from an ASCII Amber restart, or None."""
    try:
        with open(path, errors="replace") as fh:
            lines = fh.read().splitlines()
        n = int(lines[1].split()[0])
    except (OSError, ValueError, IndexError):
        return None
    vals = []
    for line in lines[2:]:
        for i in range(0, len(line.rstrip()), 12):
            chunk = line[i:i + 12].strip()
            if chunk:
                try:
                    vals.append(float(chunk))
                except ValueError:
                    return None
        if len(vals) >= 3 * n:
            break
    if len(vals) < 3 * n:
        return None
    return [tuple(vals[i:i + 3]) for i in range(0, 3 * n, 3)]


#: The terms sander prints under a minimization's energy, which add up to it.
_TERMS = {"BOND", "ANGLE", "DIHED", "VDWAALS", "EEL", "EGB", "1-4 VDW", "1-4 NB",
          "1-4 EEL", "EHBOND", "RESTRAINT", "ESURF"}


def _final(out):
    """(steps, energy, rms) from the FINAL RESULTS of a sander run, or None.

    The energy is the sum of the terms printed under it, which carry four
    decimals, where the total is printed to five significant figures: past
    1000 kcal/mol that is a tenth, more than a strain can afford. The sum is
    checked against the printed total and the total is used if they part.
    """
    try:
        text = open(out, errors="replace").read()
    except OSError:
        return None
    if "FINAL RESULTS" not in text:
        return None
    block = text.split("FINAL RESULTS", 1)[1]
    m = re.search(r"NSTEP\s+ENERGY\s+RMS\s+GMAX.*?\n\s*(\d+)\s+(" + _FLOAT
                  + r")\s+(" + _FLOAT + ")", block, re.S)
    if not m:
        return None
    steps, energy, rms = int(m.group(1)), float(m.group(2)), float(m.group(3))
    terms = block[m.end():].split("-----", 1)[0]
    parts = [(" ".join(k.split()), float(v)) for k, v in
             re.findall(r"([0-9A-Z][0-9A-Z -]*?)\s*=\s*(" + _FLOAT + ")", terms)]
    if parts and all(k in _TERMS for k, _v in parts):
        total = sum(v for _k, v in parts)
        digits = re.search(r"[eE]([-+]?\d+)", m.group(2))
        step = 10.0 ** (int(digits.group(1)) - 4) if digits else 1e-4
        if abs(total - energy) <= step:
            energy = total
    return steps, energy, rms


def _sander(home, env, work, name, mdin, parm, crd):
    with open(os.path.join(work, name + ".in"), "w") as fh:
        fh.write(mdin)
    subprocess.run([os.path.join(home, "bin", "sander"), "-O", "-i", name + ".in",
                    "-p", parm, "-c", crd, "-o", name + ".out",
                    "-r", name + ".rst7", "-inf", name + ".mdinfo"],
                   cwd=work, env=env, stdout=subprocess.PIPE,
                   stderr=subprocess.STDOUT, universal_newlines=True)
    return _final(os.path.join(work, name + ".out"))


def _refused(prefix, frame, fp, why, **extra):
    got = {"version": VERSION, "strain": None, "why": why, "frame": frame,
           "fingerprint": fp, "method": METHOD}
    got.update(extra)
    return got


def measure(posedir, prefix=None, home=None, env=None, log=None, force=False):
    """The pose's strain: read back when it has been measured, else measured.

    Returns a dict whose "strain" is kcal/mol, or None with "why" saying why
    not; it is never zero for a failure, which would read as a pose that cost
    nothing. A result is written into the pose folder whenever sander ran to
    the end, a refusal included, so the same pose is not tried again; one
    that could not start (no AmberTools, no files) writes nothing, so it is
    tried again once the cause is fixed.
    """
    say = log or (lambda *a: None)
    posedir = os.path.abspath(posedir)
    prefix = prefix or prefix_of(posedir)
    ok, why = measurable(posedir, prefix)
    if not ok:
        return {"strain": None, "why": why}
    if not force:
        got = read(posedir, prefix)
        if got is not None:
            return got
    home = amber_home(home)
    if not home:
        return {"strain": None, "why": "AmberTools' sander was not found"}
    cpptraj = os.path.join(home, "bin", "cpptraj")
    if not os.path.isfile(cpptraj):
        return {"strain": None, "why": "AmberTools' cpptraj was not found"}
    frame = frame_of(posedir, prefix)
    fp = fingerprint(posedir, prefix, frame)
    com, rec, lig = (_path(posedir, prefix, t)
                     for t in ("_com.parm7", "_rec.parm7", "_lig.parm7"))
    n_com, n_rec, n_lig = natom(com), natom(rec), natom(lig)
    if not (n_com and n_rec and n_lig):
        return {"strain": None, "why": "a topology in the pose folder could not be read"}
    if n_rec + n_lig != n_com:
        got = _refused(prefix, frame, fp,
                       "the receptor and peptide topologies hold %d + %d atoms against "
                       "%d in the complex, so the peptide cannot be cut out of the frame"
                       % (n_rec, n_lig, n_com))
        _write(posedir, prefix, got)
        return got
    igb, salt, mask = gb_settings(posedir, prefix)
    # The peptide's atoms by MM-GBSA's own ligand mask when the pose kept it,
    # else as the tail of the complex, which is where both tools put it.
    keep = mask or "@%d-%d" % (n_rec + 1, n_com)
    env = dict(env or os.environ, AMBERHOME=home, OMP_NUM_THREADS="1")
    t0 = time.time()
    work = tempfile.mkdtemp(prefix="strain_")
    try:
        with open(os.path.join(work, "cut.in"), "w") as fh:
            fh.write("parm %s\ntrajin %s\nstrip !(%s)\ntrajout bound.rst7 restart\n"
                     "run\nquit\n" % (com, os.path.join(posedir, frame), keep))
        subprocess.run([cpptraj, "-i", "cut.in"], cwd=work, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True)
        bound_xyz = _rst7(os.path.join(work, "bound.rst7"))
        if bound_xyz is None or len(bound_xyz) != n_lig:
            return {"strain": None,
                    "why": "the peptide could not be cut out of %s" % frame}
        settings = dict(igb=igb, salt=salt, surften=SURFTEN, steps=STEPS)
        single = _sander(home, env, work, "bound", _SINGLE_IN.format(**settings),
                         lig, "bound.rst7")
        if single is None:
            return {"strain": None, "why": "sander did not finish the bound energy"}
        e_bound = single[1]
        own = mmgbsa_ligand(posedir, prefix)
        detail = dict(igb=igb, saltcon=salt, bound=round(e_bound, 3),
                      mmgbsa_ligand=own, residues=residues(lig))
        if own is not None and abs(e_bound - own) > AGREE:
            got = _refused(prefix, frame, fp,
                           "the peptide's energy here is %.2f kcal/mol and MM-GBSA's own "
                           "is %.2f, so the two do not describe one geometry on one "
                           "model" % (e_bound, own), **detail)
            _write(posedir, prefix, got)
            return got
        relax = _sander(home, env, work, "relaxed", _RELAX_IN.format(**settings),
                        lig, "bound.rst7")
        if relax is None:
            return {"strain": None, "why": "sander did not finish the relaxation"}
        steps, e_relaxed, rms = relax
        strain = e_bound - e_relaxed
        moved = None
        relaxed_xyz = _rst7(os.path.join(work, "relaxed.rst7"))
        idx = [i for i in heavy(lig) if i < n_lig]
        if relaxed_xyz is not None and len(relaxed_xyz) == n_lig and idx:
            moved = round(math.sqrt(sum(
                sum((a - b) ** 2 for a, b in zip(bound_xyz[i], relaxed_xyz[i]))
                for i in idx) / len(idx)), 3)
        detail.update(relaxed=round(e_relaxed, 3), steps=steps, rms=rms, moved=moved,
                      seconds=round(time.time() - t0, 1))
        if strain < 0:
            # A minimization lowers the energy it is given, so a relaxed
            # peptide above its starting point is the minimizer's fault, and
            # the number would be one the method cannot produce.
            got = _refused(prefix, frame, fp,
                           "the relaxation ended %.2f kcal/mol above where it started, "
                           "which a minimization cannot do" % -strain, **detail)
            _write(posedir, prefix, got)
            return got
        got = {"version": VERSION, "strain": round(strain, 2), "why": None,
               "frame": frame, "fingerprint": fp, "method": METHOD}
        got.update(detail)
        _write(posedir, prefix, got)
        say("    %s: strain %.2f kcal/mol (%d steps, moved %s A)"
            % (prefix, strain, steps, "%.2f" % moved if moved is not None else "?"))
        return got
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------ the command line

def pose_folders(top):
    """Every pose folder at or under `top`, in a stable order."""
    top = os.path.abspath(top)
    if prefix_of(top):
        return [top]
    out = []
    for root, dirs, _files in os.walk(top):
        dirs.sort()
        for d in list(dirs):
            if re.match(r"pose\d+$", d) and prefix_of(os.path.join(root, d)):
                out.append(os.path.join(root, d))
        # Not into a pose folder's own subfolders, nor an archive's.
        dirs[:] = [d for d in dirs if not re.match(r"pose\d+$", d) and d != "archive"]
    return out


def main(argv=None):
    import argparse
    from concurrent.futures import ThreadPoolExecutor
    ap = argparse.ArgumentParser(prog="strain", description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--workers", type=int,
                    default=int(os.environ.get("SLURM_CPUS_PER_TASK") or 1))
    ap.add_argument("--force", action="store_true",
                    help="measure again poses that already have a result")
    ap.add_argument("--amber", default=None, help="AmberTools' folder")
    a = ap.parse_args(argv)
    folders = [f for p in a.paths for f in pose_folders(p)]
    if not folders:
        print("no pose folders found")
        return 1
    print("peptide strain for %d pose(s), %d at a time" % (len(folders), max(1, a.workers)),
          flush=True)

    def one(folder):
        return folder, measure(folder, home=a.amber, force=a.force)

    done = failed = 0
    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as pool:
        for folder, got in pool.map(one, folders):
            if got.get("strain") is not None:
                done += 1
                print("%s  %.2f kcal/mol" % (folder, got["strain"]), flush=True)
            else:
                failed += 1
                print("%s  not measured: %s" % (folder, got.get("why")), flush=True)
    print("%d measured, %d not" % (done, failed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
