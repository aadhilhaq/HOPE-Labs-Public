"""Membrane systems: the solute in a lipid bilayer with water above and below, built by
OpenMM's Modeller and saved as an Amber prmtop/inpcrd by ParmEd, so Amber, OpenMM, GROMACS and
NAMD run it (Desmond builds its own bilayer).

The build job calls this instead of tleap when spec.system.environment is "membrane":

    python3 -m hopemd.membrane build --receptor build/receptor_prep.pdb [--ligand build/ligand_prep.pdb]
        --lipid POPC --padding-A 10 --center-z-A 0 --salt-mM 150 --ions NaCl --ph 7 --out build [--nucleic OL21+OL3]

(--ions is NaCl or KCl, what addMembrane can place; --nucleic the run's DNA and RNA force fields when the
solute has a nucleic acid) and gets build/complex.prmtop,
build/complex.inpcrd (with the box), build/complex_solv.pdb and build/system.json {atoms,
waters, ion_pairs, ions, lipids, lipid, box, membrane, hydrogens_added, ...}. The HMR step
that follows reads complex.prmtop as it does after tleap.

The prepared PDBs carry no hydrogens (pdb4amber --nohyd on every path of build.sh), and
Modeller.addMembrane starts by building a System of the solute, which needs them: build()
adds them first with Modeller.addHydrogens at the run's pH, keeping the protonation states
pdb2pqr/propka chose by reading the residue names of the files (HID/HIE/HIP, ASH, GLH, LYN,
CYX; OpenMM's PDB reader folds them to HIS/ASP/GLU/LYS/CYS, so they are read off the text).

Water in the prmtop is what tleap writes for TIP3P: three bonds (O-H1, O-H2, H1-H2) and no
angle, so pmemd's fast-water setup and OpenMM's prmtop reader both keep it rigid (see
_tleap_water).

What Modeller.addMembrane does (OpenMM 8.6): it tiles a pre-equilibrated patch of the lipid
(128 lipids plus their water, from OpenMM's data folder) over the solute's xy extent plus the
padding, deletes the lipids that overlap the solute, relaxes the rest with a short
low-temperature MD while the solute grows in, tops up the water to the padding above and
below, and replaces waters by ions to the ionic strength and to neutralize. The membrane
normal is z; the solute must already be oriented that way (an OPM file is: fetch_opm).

Residue order in the prmtop: the receptor's residues as in receptor_prep.pdb, then the
partner's, then lipids, water, ions; spec.system.receptor_residues / ligand_residues stay
valid. Residue names follow Amber's conventions so the cpptraj masks in the engines work
unchanged: WAT, Na+, Cl-, and the lipid by its full name (POPC, not OpenMM's POP).

Only the standard library is imported at module level; openmm and parmed are imported inside
the functions, so the server (which imports fetch_opm) never needs them.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.request
from pathlib import Path

# what openmm.app.Modeller.addMembrane can build (OpenMM 8.6, the patches in openmm/app/data)
LIPIDS = ("POPC", "POPE", "DLPC", "DLPE", "DMPC", "DOPC", "DPPC")
BILAYER_THICKNESS_A = 40.0            # what derive() and the page assume for the slab
# residue names of amber14/lipid17.xml (whole-lipid residues, as OpenMM has them) and the split
# head/tail residues AmberTools' lipid17 uses, so a topology from either route is counted
LIPID17_RESIDUES = frozenset(
    [h + t for h in ("DAP", "DLP", "DMP", "DOP", "DPP", "DSP", "POP", "SDP") for t in "ACEGS"]
    + ["CHL1", "PA", "PC", "PE", "PS", "PGR", "PH-", "OL", "ST", "LAL", "MY", "AR", "DHA", "SA", "CHL", "PI"])
WATER_RESIDUES = frozenset(["WAT", "HOH", "TIP3", "SOL"])
CATIONS = {"Na+": ("NA", "Na+", "SOD"), "K+": ("K", "K+", "POT")}
ANIONS = {"Cl-": ("CL", "Cl-", "CLA")}
IONS = {"NaCl": ("Na+", "Cl-"), "KCl": ("K+", "Cl-")}     # what addMembrane can place (no Mg2+)
# OpenMM's names -> Amber's, applied to the topology before ParmEd writes it
RENAME_RESIDUES = {"HOH": "WAT", "NA": "Na+", "CL": "Cl-", "K": "K+"}
RENAME_ATOMS = {"Na": "Na+", "Cl": "Cl-", "K": "K+"}      # OpenMM names an ion atom by its element symbol
# residue names that carry a protonation state (pdb2pqr --ffout=AMBER, pdb4amber) and are also
# the variant names Modeller.addHydrogens takes; every other residue gets its default at the pH
VARIANTS = frozenset(["HID", "HIE", "HIP", "ASH", "GLH", "LYN", "CYX"])
#: the run's nucleic-acid force field (spec.forcefield.nucleic) as OpenMM 8.6 ships it: DNA, then RNA. OL15 is what
#: amber14-all loads, and what a build that names none gets
NUCLEIC_XML = {"OL21+OL3": ("amber19/DNA.OL21.xml", "amber14/RNA.OL3.xml"),
               "bsc1+OL3": ("amber14/DNA.bsc1.xml", "amber14/RNA.OL3.xml"),
               "OL15+OL3": ("amber14/DNA.OL15.xml", "amber14/RNA.OL3.xml")}
# tleap's frcmod.tip3p: the HW-HW bond that closes the water triangle (kcal/mol/Å², Å)
TIP3P_HH = (553.0, 1.5136)
OPM_URL = "https://opm-assets.storage.googleapis.com/pdb/{pdb_id}.pdb"
PLATFORMS = ("CUDA", "OpenCL", "CPU", "Reference")


def _log(msg: str) -> None:
    print(f"[hopemd] {msg}", flush=True)


# ----------------------------------------------------------------------------- OPM
def split_opm(pdb_text: str) -> tuple[str, float | None]:
    """An OPM file carries two planes of DUM atoms (HETATM, residue DUM) at z = +/- the
    hydrophobic half thickness. Drop them and return the half thickness read off their z, or
    None when the text has no DUM atoms (then it was not an OPM file, or a soluble entry)."""
    kept, zs = [], []
    for line in pdb_text.splitlines():
        if line.startswith(("ATOM", "HETATM")) and line[17:20].strip() == "DUM":
            try:
                zs.append(float(line[46:54]))
            except ValueError:
                pass
            continue
        kept.append(line)
    text = "\n".join(kept).rstrip("\n") + "\n"
    if not zs:
        return text, None
    return text, round((max(zs) - min(zs)) / 2.0, 2)


def fetch_opm(pdb_id: str, timeout: float = 60.0) -> tuple[str, float | None]:
    """The OPM-oriented coordinates of an RCSB entry (membrane normal z, center z = 0) and
    the half thickness of its membrane in Å. Raises urllib's errors when OPM has no such
    entry (a soluble protein, or an id OPM has not processed)."""
    url = OPM_URL.format(pdb_id=pdb_id.strip().lower())
    req = urllib.request.Request(url, headers={"User-Agent": "HOPE-MD"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read().decode("utf-8", "replace")
    return split_opm(text)


# ----------------------------------------------------------------------------- counts
def count_system(residues: list[tuple[str, int]], box_A: tuple[float, float, float] | None,
                 lipid: str) -> dict:
    """system.json from a list of (residue name, atoms in it) and the box lengths in Å. Kept
    free of OpenMM so it can be tested anywhere."""
    lipids = waters = 0
    cations = {k: 0 for k in CATIONS}
    anions = {k: 0 for k in ANIONS}
    atoms = 0
    for name, n in residues:
        atoms += n
        if name in LIPID17_RESIDUES:
            lipids += 1
        elif name in WATER_RESIDUES:
            waters += 1
        else:
            for k, names in CATIONS.items():
                if name in names: cations[k] += 1
            for k, names in ANIONS.items():
                if name in names: anions[k] += 1
    ion_pairs = min(sum(cations.values()), sum(anions.values()))
    return {"atoms": atoms, "waters": waters, "ion_pairs": ion_pairs,
            "ions": sum(cations.values()) + sum(anions.values()),
            "lipids": lipids, "lipid": lipid,
            "box": " ".join(f"{v:.3f}" for v in box_A) if box_A else "", "membrane": True}


# ----------------------------------------------------------------------------- build
def residue_names(pdb_text: str) -> list[str]:
    """The residue names of a PDB in residue order, one per residue (a residue is a run of
    ATOM/HETATM lines with the same chain, number and insertion code), as the file spells
    them: HIE stays HIE. OpenMM's PDBFile folds those to HIS, so this is read off the text."""
    names, last = [], None
    for l in pdb_text.splitlines():
        if not l.startswith(("ATOM", "HETATM")):
            continue
        key = (l[21], l[22:27])
        if key != last:
            names.append(l[17:20].strip())
            last = key
    return names


def variants_of(names: list[str]) -> list[str | None]:
    """What Modeller.addHydrogens is told per residue: the state the prepared file named, or
    None for its default at the pH."""
    return [n if n in VARIANTS else None for n in names]


def _tleap_water(st, parmed) -> int:
    """Make every three-site water what tleap writes for TIP3P: bonds O-H1, O-H2 and H1-H2,
    no angle. ParmEd writes what the OpenMM System holds (two bonds and a harmonic angle),
    and neither engine keeps that water rigid: pmemd's fast-water setup wants the triangle
    of bonds, and OpenMM's prmtop reader constrains the bonds it finds and leaves the angle
    harmonic (openmm 8.6 amber_file_parser). Returns the waters done."""
    hh = parmed.BondType(*TIP3P_HH, list=st.bond_types)
    st.bond_types.append(hh)
    done = 0
    for res in st.residues:
        if res.name not in WATER_RESIDUES or len(res.atoms) != 3:
            continue
        hs = [a for a in res.atoms if a.atomic_number == 1]
        if len(hs) != 2 or hs[0] in hs[1].bond_partners:
            continue
        st.bonds.append(parmed.Bond(hs[0], hs[1], type=hh))
        done += 1
    for i in reversed(range(len(st.angles))):
        res = st.angles[i].atom2.residue
        if res.name in WATER_RESIDUES and len(res.atoms) == 3:
            st.angles[i].delete()
            del st.angles[i]
    return done


def _platform(mm):
    """The first OpenMM platform that really starts. getPlatformByName("CUDA") succeeds on a
    node with the CUDA plugin and no GPU; only a Context shows the truth, so each is tried
    with a one-particle system, in order of speed."""
    last = None
    for name in PLATFORMS:
        try:
            plat = mm.Platform.getPlatformByName(name)
            probe = mm.System(); probe.addParticle(1.0)
            ctx = mm.Context(probe, mm.VerletIntegrator(0.001), plat)
            del ctx
            return plat
        except Exception as e:      # no such platform, or no device for it here
            last = e
    raise RuntimeError(f"no OpenMM platform could start ({last})")


def _seeded(integrator, seed: int):
    integrator.setRandomNumberSeed(seed)
    return integrator


def _extent_A(positions, u) -> tuple[list[float], list[float]]:
    xyz = positions.value_in_unit(u.angstrom)
    lo = [min(p[i] for p in xyz) for i in range(3)]
    hi = [max(p[i] for p in xyz) for i in range(3)]
    return lo, hi


def bond_split_disulfides(topology, positions) -> list[str]:
    """The disulfides between the receptor and the partner, bonded in the combined topology. Each PDBFile bonds its own
    file's, and Modeller.add joins nothing, so a pair across the split stayed two free cysteines (named CYX by the build,
    hopemd.structure links), which no template matches unbonded. Two cysteine sulfurs with no S-S bond yet, no HG and
    no more than SS_BOND_A apart are bonded here, the closest first, as the build finds them. Returns them, "H:122-L:1"."""
    from openmm import unit as u
    from .structure import SS_BOND_A, THIOL_H
    bonded = {a for b in topology.bonds() for a in b if b[0].name == "SG" and b[1].name == "SG"}
    free = [a for a in topology.atoms() if a.name == "SG" and a.residue.name in ("CYS", "CYX") and a not in bonded
            and not any(x.name in THIOL_H for x in a.residue.atoms())]
    xyz = [positions[a.index].value_in_unit(u.angstrom) for a in free]
    near = sorted((sum((p - q) ** 2 for p, q in zip(xyz[i], xyz[j])) ** 0.5, i, j)
                  for i in range(len(free)) for j in range(i + 1, len(free)))
    done, out = set(), []
    for d, i, j in near:
        if d > SS_BOND_A:
            break
        if i in done or j in done:
            continue
        done.update((i, j))
        a, b = free[i], free[j]
        topology.addBond(a, b)
        out.append(f"{a.residue.chain.id}:{a.residue.id}-{b.residue.chain.id}:{b.residue.id}")
    return out


def build(receptor: str | Path, ligand: str | Path | None, lipid: str, padding_A: float,
          center_z_A: float, salt_mM: float, out: str | Path, ions: str = "NaCl", ph: float = 7.0,
          nucleic: str = "OL15+OL3", seed: int | None = None) -> dict:
    """Embed the receptor (and partner) in a bilayer, solvate and neutralize, and write
    complex.prmtop, complex.inpcrd, complex_solv.pdb and system.json into `out`. Returns
    the system.json dict. The inputs are the prepared PDBs without hydrogens; they are added
    here at `ph`, with the states the files name (HID/HIE/HIP, ASH, GLH, LYN, CYX) kept.
    `nucleic` is the run's nucleic-acid force field (NUCLEIC_XML), for DNA or RNA in the solute.
    `seed` (the run's protocol.seed, when it has one) makes the build repeat: Modeller places some
    hydrogens and every ion with Python's random module, and relaxes the patch with a Langevin
    integrator that otherwise draws a seed of its own, on one thread of the CPU platform, whose threads
    otherwise sum the forces in no fixed order."""
    lipid = lipid.upper()
    if lipid not in LIPIDS:
        raise ValueError(f"membrane.lipid: {lipid!r} is not one OpenMM's Modeller can build; "
                         f"use one of {' '.join(LIPIDS)}")
    if ions not in IONS:
        raise ValueError(f"ions: {ions!r} is not one addMembrane can place; use one of {' '.join(IONS)}")
    if nucleic not in NUCLEIC_XML:
        raise ValueError(f"nucleic: {nucleic!r} is not a nucleic-acid force field OpenMM ships here; use one of {' '.join(NUCLEIC_XML)}")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    import openmm as mm
    import openmm.app as app
    from openmm import unit as u
    import parmed

    _log(f"membrane build: {lipid} bilayer, {padding_A:g} Å of water above and below, center z {center_z_A:g} Å, "
         f"{salt_mM:g} mM {ions}" + (f"; Modeller's random draws from seed {seed}, on one thread" if seed is not None else ""))
    rec = app.PDBFile(str(receptor))
    modeller = app.Modeller(rec.topology, rec.positions)
    n_rec = rec.topology.getNumResidues()
    names = residue_names(Path(receptor).read_text())
    _log(f"receptor: {n_rec} residues, {rec.topology.getNumAtoms()} atoms from {receptor}")
    if ligand:
        lig = app.PDBFile(str(ligand))
        modeller.add(lig.topology, lig.positions)          # after the receptor: numbering stays
        names += residue_names(Path(ligand).read_text())
        _log(f"partner: {lig.topology.getNumResidues()} residues, {lig.topology.getNumAtoms()} atoms from {ligand}")
        across = bond_split_disulfides(modeller.topology, modeller.positions)
        if across:
            _log(f"{len(across)} disulfide(s) across the split bonded, receptor to partner: {', '.join(across)}")
    n_solute = modeller.topology.getNumResidues()
    lo, hi = _extent_A(modeller.positions, u)
    _log(f"solute extent: x {hi[0]-lo[0]:.1f}  y {hi[1]-lo[1]:.1f}  z {hi[2]-lo[2]:.1f} Å; "
         f"z from {lo[2]:.1f} to {hi[2]:.1f} (the bilayer spans {center_z_A - BILAYER_THICKNESS_A/2:g} to "
         f"{center_z_A + BILAYER_THICKNESS_A/2:g}; the membrane normal must be z)")

    # the run's DNA and RNA force fields (OL15 and OL3 unless it named others): a nucleic-acid partner is an
    # allowed ligand class for a bilayer, and without them createSystem has no template for it
    ff = app.ForceField("amber14/protein.ff14SB.xml", *NUCLEIC_XML[nucleic], "amber14/lipid17.xml", "amber14/tip3p.xml")
    plat = _platform(mm)
    # pdb4amber --nohyd left the solute without hydrogens, and addMembrane's first step is a
    # System of the solute, which has no template for a residue missing them. The states
    # pdb2pqr chose are in the residue names of the files; OpenMM's reader folded them, so
    # they go in as variants. Hydrogens already present (a file with them) are kept.
    variants = variants_of(names)
    if len(variants) != n_solute:
        _log(f"residue count of the files ({len(variants)}) is not the topology's ({n_solute}); "
             "protonation states default at the pH")
        variants = None
    # a seeded build repeats: Modeller's random draws come from the seed, and its minimizations and relaxation run on
    # one thread, so the hydrogens, the patch and the waters it leaves room for come out alike (on four threads the CPU
    # platform's results differ from run to run, DeterministicForces or not; one thread adds a minute or two)
    restore = {}
    if seed is not None:
        random.seed(seed)
        prop, value = ("Threads", "1") if "Threads" in plat.getPropertyNames() else ("DeterministicForces", "true")
        if prop in plat.getPropertyNames():
            restore[prop] = plat.getPropertyDefaultValue(prop)
            plat.setPropertyDefaultValue(prop, value)
    n_before = modeller.topology.getNumAtoms()
    modeller.addHydrogens(ff, pH=ph, variants=variants, platform=plat)
    added = modeller.topology.getNumAtoms() - n_before
    named = sum(1 for v in (variants or []) if v)
    if variants is not None:                   # the prmtop then reads as tleap's: HIE, CYX, ASH ... by name
        for res, name in zip(modeller.topology.residues(), names):
            res.name = name
    _log(f"hydrogens: {added} added at pH {ph:g}; {named} residue(s) kept the state the prepared file named "
         f"({', '.join(sorted(set(v for v in (variants or []) if v))) or 'none'})")
    _log(f"OpenMM {mm.__version__} on the {plat.getName()} platform; tiling the {lipid} patch and relaxing it")
    pos_ion, neg_ion = IONS[ions]
    langevin = app.modeller.LangevinIntegrator
    if seed is not None:          # addMembrane's relaxation integrator, seeded from the run's seed
        app.modeller.LangevinIntegrator = lambda *a: _seeded(langevin(*a), seed)
    try:
        modeller.addMembrane(ff, lipidType=lipid, membraneCenterZ=center_z_A * u.angstrom,
                             minimumPadding=padding_A * u.angstrom, positiveIon=pos_ion, negativeIon=neg_ion,
                             ionicStrength=salt_mM * u.millimolar, neutralize=True, platform=plat)
    finally:
        app.modeller.LangevinIntegrator = langevin
        for prop, value in restore.items():
            plat.setPropertyDefaultValue(prop, value)
    _log(f"bilayer, water and ions placed in {time.time() - t0:.0f} s")

    # Amber's names, so the engines' masks (:WAT,Na+,Cl-) and the video's selections apply.
    # OpenMM's patch names every lipid POP/DLP/... (three letters); the full name is clearer
    # in a mask and is what lipid17.xml calls the template.
    for i, res in enumerate(modeller.topology.residues()):
        if i < n_solute:
            continue
        if res.name in RENAME_RESIDUES:
            res.name = RENAME_RESIDUES[res.name]
            for a in res.atoms():
                if a.name in RENAME_ATOMS:
                    a.name = RENAME_ATOMS[a.name]
        elif res.name == lipid[:3] or res.name == lipid:
            res.name = lipid

    # No constraints here: ParmEd 4.3.1 cannot write a prmtop from a System whose H bonds are
    # constrained (their bond terms are missing: "'NoneType' object has no attribute 'used'").
    # tleap writes every bond too; SHAKE (ntc=2) and OpenMM's HBonds are applied by the
    # engines when they load the prmtop, as for a water-box build.
    system = ff.createSystem(modeller.topology, nonbondedMethod=app.PME, constraints=None, rigidWater=False)
    st = parmed.openmm.load_topology(modeller.topology, system, xyz=modeller.positions)
    rigid = _tleap_water(st, parmed)
    box = [float(v) for v in st.box[:3]] if st.box is not None else None
    for name in ("complex.prmtop", "complex.inpcrd", "complex_solv.pdb"):
        st.save(str(out / name), overwrite=True)
    residues = [(r.name, len(r.atoms)) for r in st.residues]
    info = count_system(residues, tuple(box) if box else None, lipid)
    info.update({"padding_A": padding_A, "center_z_A": center_z_A, "salt_mM": salt_mM, "ph": ph,
                 "hydrogens_added": added, "waters_rigid": rigid,
                 "solute_residues": n_solute, "receptor_residues": n_rec, "platform": plat.getName(),
                 "build_s": round(time.time() - t0, 1)})
    (out / "system.json").write_text(json.dumps(info, indent=1) + "\n")
    _log(f"built: {info['atoms']} atoms, {info['lipids']} {lipid}, {info['waters']} waters ({rigid} as tleap's "
         f"TIP3P triangle), {info['ion_pairs']} ion pairs, box {info['box']} Å, {info['build_s']} s")
    return info


# ----------------------------------------------------------------------------- command line
def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python3 -m hopemd.membrane", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="embed build/receptor_prep.pdb (+ partner) in a bilayer and write the Amber files")
    b.add_argument("--receptor", required=True)
    b.add_argument("--ligand", default=None, help="the partner's prepared PDB (protein/peptide/nucleic acid), if any")
    b.add_argument("--lipid", default="POPC", choices=LIPIDS)
    b.add_argument("--padding-A", type=float, default=10.0, help="water above and below the bilayer, Å")
    b.add_argument("--center-z-A", type=float, default=0.0, help="membrane center along z, Å (OPM: 0)")
    b.add_argument("--salt-mM", type=float, default=150.0)
    b.add_argument("--ions", default="NaCl", choices=sorted(IONS))
    b.add_argument("--ph", type=float, default=7.0, help="pH for the hydrogens added to the prepared solute")
    b.add_argument("--nucleic", default="OL15+OL3", choices=sorted(NUCLEIC_XML), help="the nucleic-acid force field, for DNA or RNA in the solute")
    b.add_argument("--out", default="build")
    b.add_argument("--seed", type=int, default=None, help="the run's seed: Modeller's hydrogens, ions and relaxation draw from it, so the build repeats")
    o = sub.add_parser("opm", help="fetch an entry's OPM-oriented coordinates, DUM atoms dropped")
    o.add_argument("pdb_id")
    o.add_argument("--out", default=None, help="file to write (default <id>_opm.pdb)")
    return p


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    if a.cmd == "build":
        try:
            build(a.receptor, a.ligand, a.lipid, a.padding_A, a.center_z_A, a.salt_mM, a.out, ions=a.ions, ph=a.ph, nucleic=a.nucleic,
                  seed=a.seed)
        except ValueError as e:
            _log(str(e))
            return 2
        except ImportError as e:
            _log(f"{e}: hopemd.membrane needs OpenMM and ParmEd in {sys.executable}; "
                 "point HOPEMD_PYTHON at the group env")
            return 2
        return 0
    text, half = fetch_opm(a.pdb_id)
    dest = Path(a.out or f"{a.pdb_id.lower()}_opm.pdb")
    dest.write_text(text)
    _log(f"{a.pdb_id.upper()} from OPM: {dest}" + (f", hydrophobic half thickness {half:g} Å" if half else ", no DUM atoms (not a membrane entry?)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
