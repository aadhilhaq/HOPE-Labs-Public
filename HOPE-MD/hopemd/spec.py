"""The run spec: every field is chosen, detected, or derived.

Chosen fields are set by the person. Detected fields are read from what was
imported (:mod:`hopemd.importers`) and shown, never typed. Derived fields are
computed here from the rest and shown in the derived panel; they are never
stored as inputs, so they cannot disagree with what they came from.

The lists below are the compatibility matrix: an engine filters the force
fields, a force field filters the water models. A control that could name a
thing the engine cannot read is a job that fails at the wall.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field, asdict
from typing import Any

from .site import SITES, Site

ENGINES = ("amber", "openmm", "gromacs", "namd", "desmond")
#: What each source is called in the page's list. Short enough to be read inside the box it is
#: shown in: a name of 26 characters came out of a select on the form as "Complex file (PDB / mm"
#: at every text size. What each one means is behind the field's ? (app.js KIND_HELP).
SOURCE_KINDS = {
    "files":        "Receptor + partner",
    "complex":      "Complex file",
    "rcsb":         "PDB code (RCSB)",
    "alphafold":    "AlphaFold",
    "poseviewer":   "Maestro poseviewer",
}
ENGINE_LABELS = {
    "amber": "Amber (pmemd.cuda)", "openmm": "OpenMM", "gromacs": "GROMACS",
    "namd": "NAMD 3", "desmond": "Desmond",
}
LIGAND_CLASSES = ("none", "peptide", "protein", "small_molecule", "nucleic_acid")
# "protein" is a protein partner: a second chain group of a complex, or a partner file of amino-acid
# chains as long as a protein (importers.files.PROTEIN_MIN_RESIDUES). Covered by the protein force field
# like a peptide, it is analyzed as an interface rather than a ligand.
#: the crystal-water choices: an import of files keeps none, those near the partner or all (system.keep_waters);
#: Prepare drops all, those far from the partner or none (system.prep.drop_waters)
KEEP_WATERS = ("none", "near_ligand", "all")
DROP_WATERS = ("all", "far", "none")

PROTEIN_FF = {
    "amber":   ("ff19SB", "ff14SB"),
    "openmm":  ("ff19SB", "ff14SB"),     # the tleap topology, as Amber's: tleap has no CHARMM force field
    "gromacs": ("ff19SB", "ff14SB"),      # the tleap topology, converted by ParmEd: the same force field as Amber
    "namd":    ("ff19SB", "ff14SB"),      # NAMD reads the prmtop itself
    "desmond": ("OPLS_2005", "OPLS4"),    # Desmond's own; OPLS4 only with the OPLS license
}
NUCLEIC_FF = {
    "amber":   ("OL21+OL3", "bsc1+OL3"),
    "openmm":  ("OL15+OL3", "bsc1+OL3"),
    "gromacs": ("OL21+OL3", "bsc1+OL3"),
    "namd":    ("OL21+OL3", "bsc1+OL3"),
    "desmond": (),                        # assign_forcefield types the whole system with the run's OPLS, DNA and RNA included
}
# water models allowed per protein force field; first one is the default pairing
WATER = {
    "ff19SB":         ("OPC", "TIP3P", "TIP4P-Ew"),
    "ff14SB":         ("TIP3P", "OPC", "TIP4P-Ew"),
    "amber14sb":      ("TIP3P", "OPC"),
    "amber99sb-ildn": ("TIP3P", "SPC/E", "TIP4P"),
    "a99SB-disp":     ("TIP4P-D",),
    "OPLS4":          ("SPC", "TIP3P", "TIP4P", "TIP4P-Ew"),
    "OPLS_2005":      ("SPC", "TIP3P"),          # Desmond's build refuses OPLS_2005 with a four-site water
}
WATER_SITES = {"TIP3P": 3, "SPC": 3, "SPC/E": 3, "OPC": 4, "TIP4P": 4, "TIP4P-Ew": 4, "TIP4P-D": 4}
#: engines that cannot run a 4-site water from the Amber topology. NAMD 3.0.1 reads the extra point as
#: a lone pair but then stops with "Failed to find water bond lengths": its TIP4P code expects the
#: extra site after the oxygen, and tleap writes it after the two hydrogens.
THREE_SITE_ONLY = ("namd",)
#: how a small molecule is parameterized. The Amber-topology engines run antechamber (GAFF2, AM1-BCC
#: charges) on the ligand's net charge; Desmond's System Builder types the ligand with the run's own
#: OPLS force field from the SDF's bond orders and formal charges, so it needs no choice and no
#: typed charge.
LIGAND_PARAMS = {
    "amber":   ("gaff2-am1bcc",),
    "openmm":  ("gaff2-am1bcc",),
    "gromacs": ("gaff2-am1bcc",),
    "namd":    ("gaff2-am1bcc",),
    "desmond": (),
}
BOX_SHAPES = ("compact", "cubic", "rectangular")   # compact = truncated octahedron / dodecahedron
#: engines that build one box shape whatever the spec says: Desmond's System Builder writes an orthorhombic
#: box with the padding as its buffer on every side (build_geometry: shape = orthorhombic, size_type = buffer)
FIXED_BOX = {"desmond": "rectangular"}
SALTS = ("NaCl", "KCl", "MgCl2")
CUTOFF_A = 10.0
FIXED_TIMESTEP = {"desmond": 2.0}   # RESPA 2/2/6; the control is shown fixed with the reason

PRESETS = {
    "quick":     dict(length_ns=10,  replicates=1, timestep_fs=4.0, save_ps=20),
    "standard":  dict(length_ns=100, replicates=3, timestep_fs=4.0, save_ps=20),
    "long":      dict(length_ns=500, replicates=3, timestep_fs=4.0, save_ps=20),
    # CPU check: for when the GPU queue is full and the question is only "does the pose
    # hold for the first few ns". One whole CPU node, at most 10 ns and 500 frames.
    "cpu_check": dict(length_ns=5,   replicates=1, timestep_fs=4.0, save_ps=10),
}
CPU_MAX_NS = 10.0
CPU_MAX_FRAMES = 500
CPU_ENGINES = ("amber", "gromacs")           # OpenMM on CPU is ~5 ns/day here; the NAMD module is a CUDA build; Desmond is GPU only

WATER_DENSITY_PER_A3 = 0.0334     # molecules per Å^3 at 300 K
MOLAR_WATER = 55.5
BYTES_PER_ATOM_FRAME = 12         # single-precision xyz in NetCDF/XTC-ish terms

# Membrane systems: a lipid bilayer in place of a water box, built by OpenMM's Modeller (on Desmond, by its System Builder).
ENVIRONMENTS = ("water", "membrane")
# what openmm.app.Modeller.addMembrane builds by name (OpenMM 8.6 in the group env)
LIPIDS = ("POPC", "POPE", "DLPC", "DLPE", "DMPC", "DOPC", "DPPC")
ORIENTATIONS = ("as_imported", "opm", "annotation")
# OpenMM's data ships amber14/protein.ff14SB.xml, lipid17.xml and tip3p.xml; ff19SB and OPC are not
# there, so a bilayer build is ff14SB + TIP3P on the four engines that run the prmtop it makes.
MEMBRANE_ENGINES = ("amber", "openmm", "gromacs", "namd", "desmond")   # four run the prmtop the membrane build writes; Desmond builds its own bilayer
DESMOND_LIPIDS = ("POPC", "POPE", "DPPC", "DMPC")   # the bilayer patches Desmond 2024-4 ships (mmshare/data/system_builder/<lipid>.mae.gz)
#: the waters a Desmond bilayer is built with: the patches come hydrated with SPC (POPC.mae.gz holds 8 909
#: SPC waters), so a three-site water, SPC or the TIP3P that OPLS_2005 also takes
DESMOND_MEMBRANE_WATER = ("SPC", "TIP3P")
MEMBRANE_PROTEIN_FF = "ff14SB"
MEMBRANE_WATER = "TIP3P"
MEMBRANE_LIGAND_CLASSES = ("none", "protein", "peptide", "nucleic_acid")   # no GAFF templates in the OpenMM build path
MEMBRANE_SALTS = ("NaCl", "KCl")  # what Modeller.addMembrane places (a monovalent pair; it cannot place Mg2+)
MEMBRANE_PADDING_A = (5.0, 40.0)  # water above and below the bilayer, Modeller's minimumPadding
BILAYER_THICKNESS_A = 40.0        # what the derived box and the Setup viewer's slab assume
AREA_PER_LIPID_A2 = 65.0          # about right for the seven lipids at 300 K
ATOMS_PER_LIPID = 134             # POPC with hydrogens; the others are within a few percent
# Modeller.addMembrane tiles a pre-equilibrated patch of 128 lipids over the solute's xy extent
# plus the padding (nx = ceil((extent + 2 padding) / patch), whole tiles, never clipped) and makes
# z at least the patch's own height: the box is a multiple of the patch cell, so a solute
# narrower than one tile still gets the whole tile. Cell (x, y, z in Å) and atoms per lipid
# from OpenMM 8.6's app/data/<lipid>.pdb (CRYST1).
LIPIDS_PER_PATCH = 128
LIPID_PATCH_A = {
    "POPC": (63.7, 66.9, 73.1, 134), "POPE": (61.5, 58.7, 84.0, 125), "DLPC": (62.5, 65.5, 58.4, 106),
    "DLPE": (59.3, 59.2, 77.1, 97), "DMPC": (62.9, 63.3, 63.6, 118), "DOPC": (62.1, 66.6, 76.4, 138),
    "DPPC": (55.9, 56.9, 81.0, 130),
}
# Desmond's System Builder sizes its boxes its own way (build_geometry, Desmond 2024-4; measured on its builds
# here; tleap's are hopemd.solvation's). A water box is the solute's extent plus the buffer on each side and 1 Å
# more, with about 17 Å^3 of it left to each solute atom. A bilayer (membrane_box) is the extent plus the membrane
# padding in x and y, and the lipid patch's own height plus the padding in z; the lipids over the solute's
# footprint (the ellipse in its x-y extent) are left out, and the solute with the gaps around it in the bilayer
# keeps about 29 Å^3 an atom from the water.
DESMOND_BOX_EXTRA_A = 1.0
DESMOND_SOLUTE_A3 = 17.0
DESMOND_BILAYER_SOLUTE_A3 = 29.0
#: the patches membrane_box tiles (mmshare-v6.8/data/system_builder/<lipid>.mae.gz): cell x, y, z (Å), lipids,
#: atoms per lipid, and the SPC waters that hydrate them
DESMOND_LIPID_PATCH = {
    "POPC": (81.8, 81.8, 77.1, 200, 134, 8909), "POPE": (75.9, 75.9, 92.2, 200, 125, 9765),
    "DPPC": (79.0, 79.0, 81.0, 200, 130, 9000), "DMPC": (79.7, 79.7, 76.7, 200, 118, 9000),
}


@dataclass
class Source:
    kind: str = "files"            # one of SOURCE_KINDS
    path: str = ""                 # receptor file | complex file | AF zip or cif | run folder | poseviewer
    ligand_path: str = ""          # files: ligand file (pdb/sdf/mol2)
    peptide: str = ""
    pose: int = 1
    pdb_id: str = ""               # rcsb: 4-character id
    assembly: str = "1"            # rcsb: biological assembly number, or "asu" for the asymmetric unit
    ligand: str = ""               # rcsb, complex, alphafold: the bound small molecule run as the partner, "BTN A 300"


@dataclass
class Prep:
    """The preparation stage. Every default is the conservative one; the report says what happened."""
    drop_waters: str = "all"       # all | far | none   (far = keep the waters within 5 A of the partner/ligand; none = keep every one)
    drop_buffers: bool = True      # glycerol, sulfate, PEG, acetate, DMSO ... the crystallographic stuff
    keep_cofactors: bool = True    # metals and biological cofactors stay (and need parameters)
    keep_other_hetero: bool = False
    drop_hetero: list[str] = field(default_factory=list)   # residue names Clean removes whatever their class ("NAD")
    altloc: str = "A"              # which alternate location to keep
    model: int = 1                 # NMR / multi-model files: which model
    #: a gap inside a chain up to this many residues is built (PDBFixer, seeded, then relaxed with its stems on
    #: springs and every join checked: prepare.py), 12 at most (prepare.MAX_LOOP); 0 builds none
    model_loops_max: int = 12
    #: no longer read: a chain's missing ends are never built, whatever this says (kept so that a spec written
    #: before still loads)
    rebuild_termini: bool = False
    #: a protein gap left open (too long to model, or loop modeling off) has its two ends capped: an NME on
    #: the residue before it and an ACE on the residue after it, numbered one past and one short of them, so
    #: neither end is a charged terminus the protein does not have. The chain's true termini stay charged.
    #: Off, each piece ends in charged termini (an OXT before the gap, NH3+ after it), as a nucleic-acid
    #: break always does. Not done again on a structure prepared per molecule (prepared_per_molecule).
    cap_breaks: bool = True
    trim_plddt: float = 50.0       # AlphaFold: trim terminal residues below this pLDDT (0 = keep all)
    protonation: str = "propka"    # propka (pdb2pqr) | pdbfixer | none
    #: the structure arrives with each of its molecules already prepared on its own - split,
    #: protonated, its termini, disulfides and gaps settled, and recombined - so Prepare does not
    #: model or protonate it again and undo that. What it still does is its own: the clean, the
    #: hetero groups it carries, the minimization and the split, which are decisions about the
    #: assembled complex rather than about one molecule. Set by a flow whose preparation card has
    #: done the per-molecule work (HOPE Labs, hope_flow); a structure imported here is never
    #: already prepared, so the default is false.
    prepared_per_molecule: bool = False
    minimise: bool = True          # restrained minimization after modeling (OpenMM, vacuum)
    minimise_steps: int = 500
    implicit_solvent: bool = False # GBn2 instead of vacuum: better, ten times slower
    restraint_kcal: float = 50.0   # on heavy atoms of residues that were in the input; modeled residues are free


@dataclass
class Membrane:
    """The bilayer, when system.environment is "membrane". Built by OpenMM's Modeller.addMembrane
    with lipid17 on the Amber-topology engines, by the System Builder's membrane_box on Desmond;
    the membrane normal is z, as in an OPM file."""
    lipid: str = "POPC"                # one of LIPIDS
    padding_A: float = 10.0            # water above and below the bilayer (also Modeller's xy padding)
    center_z_A: float = 0.0            # membrane center along z; the OPM convention is 0
    orientation: str = "as_imported"   # as_imported: the file's membrane normal is already z | opm: fetch OPM's coordinates at import | annotation: orient on the segments
    segments: str = ""                 # transmembrane segments typed by the person, "A:30-56, A:71-96": they override what UniProt or BLAST found


@dataclass
class System:
    source: Source = field(default_factory=Source)
    prep: Prep = field(default_factory=Prep)
    environment: str = "water"     # water | membrane
    membrane: Membrane = field(default_factory=Membrane)
    chains: list[str] = field(default_factory=list)      # empty = all polymer chains
    receptor_chains: list[str] = field(default_factory=list)   # complex: the chains fitted for RMSD
    partner_chains: list[str] = field(default_factory=list)    # complex: the other side of the interface
    ph: float = 7.4
    keep_waters: str = "none"      # none | near_ligand | all: the crystal waters an import of files keeps (Prepare's are prep.drop_waters)
    keep_cofactors: bool = True
    keep_metals: bool = False      # an import of files keeps the metal ions the builds have parameters for (Prepare's are prep.keep_cofactors)
    # detected
    ligand_class: str = "none"
    ligand_charge: int | None = None
    ligand_charge_confirmed: bool = False
    ligand_hydrogens: int | None = None  # in the ligand file as imported; a file without them is refused
    ligand_formal_charge: int | None = None  # the formal charges of a ligand file with bond orders (SDF, MOL2), summed: the net charge must equal it
    solute_atoms: int | None = None      # heavy+H after preparation, if known
    extent_A: list[float] | None = None  # bounding box of the solute, x y z
    solute_shape: dict = field(default_factory=dict)   # what tleap's water box depends on (hopemd.solvation.solute_shape)
    receptor_residues: list[int] | None = None   # first,last after renumbering (tleap style)
    ligand_residues: list[int] | None = None
    disulfides: list[list[str]] = field(default_factory=list)   # ["A:25","A:80"]
    #: the disulfides that join the receptor to the partner, which no one file of the build holds: {"receptor": "H:122",
    #: "partner": "L:1", "residues": [119, 268] (topology numbers), "distance_A": 1.97} (importers/files.py)
    split_disulfides: list[dict] = field(default_factory=list)
    residue_index: dict[str, int] = field(default_factory=dict) # "A:41" -> sequential number
    chains_found: list[dict] = field(default_factory=list)      # what the importer saw
    hetero_found: list[dict] = field(default_factory=list)      # HETATM groups and their class
    ligands_found: list[dict] = field(default_factory=list)     # bound small molecules that could be the partner (importers.bound_ligand)
    gaps_found: list[dict] = field(default_factory=list)        # missing residues per chain
    breaks: list[str] = field(default_factory=list)             # chain breaks ended with TER, "A:228-229" (residues missing between them)
    capped: list[str] = field(default_factory=list)             # those of them Prepare capped, NME before the gap and ACE after it
    stretched: list[str] = field(default_factory=list)          # links longer than a bond kept bonded, no residue missing: "A:64-65 (C–N 3.14 Å)"
    confidence: dict = field(default_factory=dict)              # AlphaFold: ptm, iptm, mean pLDDT
    meta: dict = field(default_factory=dict)                    # RCSB: title, method, resolution
    waters: int = 0                                             # in the file as imported
    altlocs: int = 0
    models: int = 1
    tm_annotation: dict = field(default_factory=dict)           # transmembrane segments: UniProt via SIFTS, or a hydrophobicity scan (hopemd/annotation.py)
    prepared: bool = False                                      # prep ran and prepared.pdb exists
    placement: dict = field(default_factory=dict)               # how the partner sits against the receptor at import: min_distance_A, contacts, clashes
    waters_kept: list[str] = field(default_factory=list)        # the crystal waters input/waters.pdb holds, "HOH A:301": the import's, or Prepare's once it has run
    ions_kept: list[dict] = field(default_factory=list)         # the ions an import of files kept in input/ions.pdb, as Prepare's report lists the ones it carries
    prep_report: dict = field(default_factory=dict)


@dataclass
class ForceField:
    engine: str = "amber"
    protein: str = "ff19SB"
    nucleic: str | None = None
    ligand: str | None = None
    water: str = "OPC"


@dataclass
class Box:
    shape: str = "compact"
    padding_A: float = 12.0
    salt: str = "NaCl"
    salt_mM: float = 150.0


@dataclass
class Protocol:
    preset: str = "standard"
    temperature_K: float = 300.0
    pressure_bar: float = 1.0
    length_ns: float = 100.0
    timestep_fs: float = 4.0        # 4 fs means HMR
    save_ps: float = 20.0
    replicates: int = 3
    minimise_steps: int = 5000
    heat_ps: float = 200.0
    equil_ps: float = 1000.0        # total, spread over the restraint ladder
    restrain: list[str] = field(default_factory=list)   # residue keys "A:41", "H:60A" kept restrained in production
    restraint_kcal: float = 5.0
    seed: int = -1                  # -1 = engine picks one per replicate


@dataclass
class Compute:
    site: str = "grace"
    device: str = "gpu"             # gpu | cpu  (cpu = one whole node, the short check only)
    partition: str = ""             # empty = the site's GPU partition (or CPU partition when device=cpu)
    gpu: str = ""                   # empty = the site's default; "auto" = the fastest type with a free card at submit
    gpu_chosen: str = ""            # what "auto" resolved to at submit, so the run knows what it ran on
    gpus_per_replicate: int = 1
    cpus: int = 4
    mem_gb: int = 16
    #: the Slurm account every job of the run is charged to; empty is the submitter's default.
    #: Set by a flow, which is where somebody charging a batch of runs to one allocation says so
    #: (HOPE Labs' simulation card, "Account to charge"); the page leaves it empty.
    account: str = ""
    walltime: str = ""              # empty = the estimate's (the page fills the field with it); a typed one is each production job's limit
    segment_hours: float = 0.0      # cap on one production segment, hours; 0 = the partition's limit
    max_concurrent: int = 0         # 0 = no throttle
    container: bool = False         # run the engine from the site's image rather than the module
    tidy: bool = True               # a last job packs the small files a finished run no longer needs (hopemd.tidy)


@dataclass
class Spec:
    name: str = ""
    system: System = field(default_factory=System)
    forcefield: ForceField = field(default_factory=ForceField)
    box: Box = field(default_factory=Box)
    protocol: Protocol = field(default_factory=Protocol)
    compute: Compute = field(default_factory=Compute)
    analysis: dict[str, Any] = field(default_factory=lambda: {
        "mmgbsa": True, "mmgbsa_stride_ps": 200, "mmgbsa_from_ns": 0.0, "mmgbsa_to_ns": 0.0,   # to = 0: the end
        "viewer_stride": 20, "tmd_abfe": False})
    version: int = 1
    rendered_by: list[dict] = field(default_factory=list)    # hopemd.provenance: the code of each render (submit, prepare, mmgbsa)

    # ---------------------------------------------------------------- io
    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, d: dict) -> "Spec":
        def build(cls_, sub):
            if sub is None:
                return cls_()
            kw = {}
            for f in cls_.__dataclass_fields__.values():
                if f.name in sub:
                    v = sub[f.name]
                    if f.name == "source" and cls_ is System:
                        v = build(Source, v)
                    if f.name == "prep" and cls_ is System:
                        v = build(Prep, v)
                    if f.name == "membrane" and cls_ is System:
                        v = build(Membrane, v)
                    kw[f.name] = v
            return cls_(**kw)
        s = cls()
        s.name = d.get("name", "")
        s.system = build(System, d.get("system"))
        s.forcefield = build(ForceField, d.get("forcefield"))
        s.box = build(Box, d.get("box"))
        s.protocol = build(Protocol, d.get("protocol"))
        # an emptied seed box arrives as null: a new seed each run, as -1 says; 42.0 is 42, since
        # the job scripts do integer arithmetic on it
        if s.protocol.seed is None:
            s.protocol.seed = -1
        elif isinstance(s.protocol.seed, float) and s.protocol.seed.is_integer():
            s.protocol.seed = int(s.protocol.seed)
        s.compute = build(Compute, d.get("compute"))
        s.analysis = {**s.analysis, **d.get("analysis", {})}
        s.version = d.get("version", 1)
        s.rendered_by = list(d.get("rendered_by") or [])
        return s

    @classmethod
    def from_json(cls, text: str) -> "Spec":
        return cls.from_dict(json.loads(text))

    # ---------------------------------------------------------------- helpers
    @property
    def site(self) -> Site:
        return SITES[self.compute.site]

    @property
    def hmr(self) -> bool:
        return self.protocol.timestep_fs > 2.5

    @property
    def engine(self) -> str:
        return self.forcefield.engine

    def apply_preset(self, key: str) -> None:
        p = PRESETS[key]
        self.protocol.preset = key
        for k, v in p.items():
            setattr(self.protocol, k, v)
        self.compute.device = "cpu" if key == "cpu_check" else "gpu"
        self.compute.partition = ""

    @property
    def gpu(self) -> str:
        """The GPU type this run uses, or will at submit (resolve_gpu)."""
        return resolve_gpu(self)[0]

    @property
    def gpu_partition(self) -> str:
        """The partition a GPU job goes to: the chosen one, else the one that carries the GPU type."""
        return self.compute.partition or self.site.partition_for(self.gpu)

    @property
    def on_cpu(self) -> bool:
        return self.compute.device == "cpu"

    def default_name(self) -> str:
        src = self.system.source
        base = src.peptide or src.pdb_id or src.path.replace("\\", "/").rsplit("/", 1)[-1] or "run"
        base = re.sub(r"\.(pdb|cif|zip|maegz|mae)$", "", base, flags=re.I)
        stem = re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_")[:24]
        if src.kind in ("poseviewer", "alphafold"):
            stem += f"_p{src.pose}"
        if src.kind in ("complex", "rcsb", "alphafold") and (src.ligand or "").strip():
            stem += "_" + re.sub(r"[^A-Za-z0-9]+", "", re.split(r"[\s:]+", src.ligand.strip())[0])[:5]
        return f"{stem}_{self.engine}"


# ---------------------------------------------------------------------- choices
def choices(spec: Spec) -> dict[str, list[str]]:
    """The filtered option lists for the form, given the current engine and force field."""
    e = spec.forcefield.engine if spec.forcefield.engine in ENGINES else "amber"
    prot = spec.forcefield.protein
    water = WATER.get(prot, ("TIP3P",))
    if e in THREE_SITE_ONLY:
        water = tuple(w for w in water if WATER_SITES.get(w, 3) == 3) or ("TIP3P",)
    protein = PROTEIN_FF[e]
    salts = SALTS
    if spec.system.environment == "membrane" and e == "desmond":
        # the System Builder's bilayer: Desmond's own force field and lipid patches, a three-site water and two salts
        water = tuple(w for w in water if w in DESMOND_MEMBRANE_WATER) or DESMOND_MEMBRANE_WATER[:1]
        salts = MEMBRANE_SALTS
    elif spec.system.environment == "membrane":
        # the bilayer build has one force field, one water and two salts (see MEMBRANE_PROTEIN_FF)
        protein, water, salts = (MEMBRANE_PROTEIN_FF,), (MEMBRANE_WATER,), MEMBRANE_SALTS
    out = {
        "engine": [x for x in ENGINES if x in spec.site.modules],   # an engine the site has no module for is not offered
        "protein": list(protein),
        "nucleic": list(NUCLEIC_FF[e]),
        "ligand": list(LIGAND_PARAMS[e]),
        "water": list(water),
        "environment": list(ENVIRONMENTS),
        "lipid": list(DESMOND_LIPIDS if e == "desmond" else LIPIDS),
        "orientation": list(ORIENTATIONS),
        "timestep_fs": ["2", "4"] if e not in FIXED_TIMESTEP else [f"{FIXED_TIMESTEP[e]:g}"],
        "box": [FIXED_BOX[e]] if e in FIXED_BOX else list(BOX_SHAPES),
        "salt": list(salts),
        "gpu": [GPU_AUTO] + spec.site.gpu_types(),
        "partition": [p.name for p in spec.site.partitions.values() if p.gpu == (spec.compute.device != "cpu")],
        "device": ["gpu", "cpu"],
        "preset": list(PRESETS),
        "source_kind": list(SOURCE_KINDS),
        "protonation": ["propka", "pdbfixer", "none"],
    }
    if spec.system.environment == "membrane":
        out["box"] = ["rectangular"]          # a bilayer is periodic in x and y: the one shape _validate_membrane takes
    return out


# ---------------------------------------------------------------------- validation
def validate(spec: Spec) -> list[dict]:
    """Field-level problems. Each is {"field": dotted.path, "message": text}.
    Submit is disabled while this list is not empty."""
    errs: list[dict] = []
    err = lambda f, m: errs.append({"field": f, "message": m})   # noqa: E731
    ff, box, pro, cmp, sys_ = spec.forcefield, spec.box, spec.protocol, spec.compute, spec.system

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", spec.name or ""):
        err("name", "Name the run: letters, digits, _ . - and no spaces, up to 64 characters.")
    if spec.compute.account and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", spec.compute.account):
        err("compute.account", "The account to charge is letters, digits, _ . - and no spaces.")
    if cmp.site not in SITES:
        err("compute.site", f"Unknown site {cmp.site!r}; this build knows {', '.join(SITES)}.")
        return errs
    site = spec.site

    if ff.engine not in ENGINES:
        err("forcefield.engine", f"{ff.engine!r} is not an engine here.")
        return errs
    if ff.engine not in site.modules:
        err("forcefield.engine", site.notes.get(ff.engine) or f"{site.name} has no module for {ENGINE_LABELS[ff.engine]}.")
        return errs
    if ff.protein not in PROTEIN_FF[ff.engine]:
        err("forcefield.protein", f"{ENGINE_LABELS[ff.engine]} does not load {ff.protein}; "
            f"it loads {', '.join(PROTEIN_FF[ff.engine])}.")
    if ff.water not in WATER.get(ff.protein, ()):
        err("forcefield.water", f"{ff.protein} is not fitted for {ff.water}; use "
            f"{', '.join(WATER.get(ff.protein, ()))} ({WATER.get(ff.protein, ('?',))[0]} is the pairing it was fitted to).")
    elif ff.engine in THREE_SITE_ONLY and WATER_SITES.get(ff.water, 3) == 4:
        err("forcefield.water", f"{ENGINE_LABELS[ff.engine]} cannot run {ff.water}, a 4-site water, from the Amber topology: "
            "it stops before the minimization. Use TIP3P here, or run "
            f"{ff.water} on Amber, OpenMM or GROMACS.")
    if sys_.ligand_class == "small_molecule":
        if sys_.ligand_hydrogens == 0:
            err("system.source", "The ligand file has no hydrogens. Give the ligand as SDF or MOL2 with its bond "
                "orders and formal charges, and its hydrogens are added as drawn; a PDB ligand needs them in the file.")
        if LIGAND_PARAMS[ff.engine]:
            if not ff.ligand:
                err("forcefield.ligand", "A small molecule needs parameters: choose "
                    f"{', '.join(LIGAND_PARAMS[ff.engine])}.")
            elif ff.ligand not in LIGAND_PARAMS[ff.engine]:
                err("forcefield.ligand", f"{ENGINE_LABELS[ff.engine]} cannot read {ff.ligand}; "
                    f"choose {', '.join(LIGAND_PARAMS[ff.engine])}.")
            if sys_.ligand_charge is None:
                err("system.ligand_charge", "The ligand's net charge was not detected; set it.")
            elif sys_.ligand_formal_charge is not None and sys_.ligand_charge != sys_.ligand_formal_charge:
                # sqm and AM1-BCC run without complaint on a charge two away from the one drawn (the electron count stays even)
                err("system.ligand_charge", f"The ligand file's formal charges sum to {sys_.ligand_formal_charge:+d}, and HOPE-MD "
                    f"runs the molecule the file draws: to run it at {sys_.ligand_charge:+d}, load a file protonated for that charge.")
            elif not sys_.ligand_charge_confirmed:
                err("system.ligand_charge", f"Confirm the net charge ({sys_.ligand_charge:+d}) before "
                    "charging: AM1-BCC with the wrong total runs to completion and is wrong in every frame.")
    if has_nucleic(sys_) and NUCLEIC_FF[ff.engine] and not ff.nucleic:
        err("forcefield.nucleic", "A nucleic acid is present; choose its force field: "
            f"{', '.join(NUCLEIC_FF[ff.engine])}.")
    if not (0.0 <= sys_.ph <= 14.0):
        err("system.ph", "pH must be between 0 and 14.")
    if sys_.source.kind in ("complex", "rcsb", "alphafold"):
        found = {c["id"] for c in sys_.chains_found if c.get("kind") in ("protein", "nucleic")}
        if found and sys_.partner_chains and set(sys_.partner_chains) & set(sys_.receptor_chains):
            err("system.partner_chains", "A chain cannot be on both sides of the interface.")
        if found and sys_.receptor_chains and not set(sys_.receptor_chains) <= found:
            err("system.receptor_chains", f"Chains in this file: {', '.join(sorted(found))}.")
        if found and sys_.partner_chains and not set(sys_.partner_chains) <= found:
            err("system.partner_chains", f"Chains in this file: {', '.join(sorted(found))}.")
        if sys_.source.ligand and sys_.partner_chains:
            err("system.partner_chains", f"{sys_.source.ligand} is the partner, so no chain is: every polymer chain Prepare "
                "keeps is the receptor. Empty the partner chains, or run no bound ligand.")
        for c in run_chains(sys_):
            if c.get("modified"):
                names = ", ".join(c["modified"][:8]) + (" …" if len(c["modified"]) > 8 else "")
                err("system.partner_chains" if c.get("id") in (sys_.partner_chains or []) else "system.receptor_chains",
                    f"Chain {c['id']} has modified nucleotides ({names}). The nucleic-acid force fields here (OL3 for RNA; "
                    "OL21, bsc1 and OL15 for DNA) have no parameters for them and Prepare cannot keep them, so HOPE-MD does "
                    "not simulate this chain. Leave it out of the receptor and partner chains, or start from a structure "
                    "without the modifications.")
            if c.get("charged_modified"):
                names = ", ".join(c["charged_modified"][:8]) + (" …" if len(c["charged_modified"]) > 8 else "")
                err("system.partner_chains" if c.get("id") in (sys_.partner_chains or []) else "system.receptor_chains",
                    f"Chain {c['id']} has modified amino acids whose charge is not their parent's ({names}): a phosphate, "
                    "sulfate, carboxylate or acetyl the force fields here have no parameters for. As the parent the run would "
                    "be another molecule with another charge, so HOPE-MD does not simulate this chain. Leave it out of the "
                    "receptor and partner chains, or start from a structure without the modifications.")
        # a hetero group Clean keeps goes into the build only as an ion the build has parameters for;
        # anything else is refused here by name rather than dropped on the way without a word
        uncovered = sorted({h["resname"] for h in clean_keeps(sys_)[1]})
        if uncovered:
            one = len(uncovered) == 1
            names = uncovered[0] if one else ", ".join(uncovered[:-1]) + " and " + uncovered[-1]
            err("system.prep.drop_hetero", f"{names} {'is' if one else 'are'} kept by Clean, but no force field here covers "
                f"{'it' if one else 'them'}: drop {'it' if one else 'them'} in Clean ('also drop' under Prepare), or run "
                f"{'it' if one else 'one'} as the partner.")
    # a range that ends before it starts holds no residue: a structure with no chain the build can use, whose masks
    # would read :1-0
    for f, what, rng in (("system.receptor_residues", "receptor", sys_.receptor_residues),
                         ("system.ligand_residues", "partner", sys_.ligand_residues)):
        if rng is not None and (len(rng) != 2 or rng[1] < rng[0]):
            err(f, f"The {what} holds no residue (its range is {'-'.join(str(x) for x in rng) or 'empty'}): the structure has no "
                "protein or nucleic-acid chain for the build. Import it again and check its chains.")
    if sys_.source.kind == "rcsb" and not re.fullmatch(r"[0-9][A-Za-z0-9]{3}", sys_.source.pdb_id or ""):
        err("system.source.pdb_id", "A PDB id is four characters, digit first: 4CMM, 8TQS.")
    pr = sys_.prep
    if pr.drop_waters not in DROP_WATERS:
        err("system.prep.drop_waters", f"Waters: {', '.join(DROP_WATERS)} (drop all, drop those far from the partner, drop none).")
    if sys_.keep_waters not in KEEP_WATERS:
        err("system.keep_waters", f"Crystal waters: {', '.join(KEEP_WATERS)}.")
    if not (0 <= pr.model_loops_max <= 12):
        err("system.prep.model_loops_max", "Gaps longer than 12 residues are not built here; a longer one is left open and "
            "capped. For the missing stretch itself, use an AlphaFold model.")
    if not (0.0 <= pr.trim_plddt <= 100.0):
        err("system.prep.trim_plddt", "pLDDT is 0 to 100.")
    if pr.protonation not in ("propka", "pdbfixer", "none"):
        err("system.prep.protonation", "Protonation: propka, pdbfixer, or none.")

    if box.shape not in BOX_SHAPES:
        err("box.shape", f"Box must be one of {', '.join(BOX_SHAPES)}.")
    elif ff.engine in FIXED_BOX and box.shape != FIXED_BOX[ff.engine]:
        err("box.shape", f"{ENGINE_LABELS[ff.engine]}'s System Builder builds an orthorhombic box, with the padding as its "
            f"buffer on every side, whatever the shape asked; choose {FIXED_BOX[ff.engine]}.")
    if box.padding_A < CUTOFF_A and sys_.environment != "membrane":    # a bilayer's water is membrane.padding_A
        err("box.padding_A", f"{box.padding_A:g} Å is under the {CUTOFF_A:g} Å cutoff, so the complex "
            "would see its own periodic image. Use 10 Å or more; 12 Å is the default.")
    if box.salt not in SALTS:
        err("box.salt", f"Salt must be one of {', '.join(SALTS)}.")
    if not (0.0 <= box.salt_mM <= 1000.0):
        err("box.salt_mM", "Salt concentration must be between 0 and 1000 mM.")

    if sys_.environment not in ENVIRONMENTS:
        err("system.environment", f"{sys_.environment!r} is not an environment here; it is water or membrane.")
    elif sys_.environment == "membrane":
        errs.extend(_validate_membrane(spec))

    if pro.length_ns <= 0:
        err("protocol.length_ns", "Length must be positive, in ns.")
    if ff.engine in FIXED_TIMESTEP and abs(pro.timestep_fs - FIXED_TIMESTEP[ff.engine]) > 1e-9:
        err("protocol.timestep_fs", f"{ENGINE_LABELS[ff.engine]} runs its RESPA scheme; the timestep "
            f"is fixed at {FIXED_TIMESTEP[ff.engine]:g} fs and HMR is not offered.")
    elif pro.timestep_fs not in (2.0, 4.0):
        err("protocol.timestep_fs", "Timestep is 2 fs, or 4 fs with hydrogen mass repartitioning.")
    if pro.save_ps <= 0 or (pro.save_ps * 1000) % pro.timestep_fs > 1e-6:
        err("protocol.save_ps", f"Save interval must be a whole number of steps at {pro.timestep_fs:g} fs.")
    # --- the MM-GBSA window: a slice of the production, frames every so many ps ---
    an = spec.analysis
    if mmgbsa_runs(spec):
        a0, a1, st = float(an.get("mmgbsa_from_ns", 0) or 0), float(an.get("mmgbsa_to_ns", 0) or 0), float(an.get("mmgbsa_stride_ps", 200) or 0)
        if a0 < 0 or a0 >= pro.length_ns:
            err("analysis.mmgbsa_from_ns", f"MM-GBSA starts inside the run: 0 to {pro.length_ns:g} ns.")
        if a1 and (a1 <= a0 or a1 > pro.length_ns):
            err("analysis.mmgbsa_to_ns", f"MM-GBSA ends after it starts and within the run ({pro.length_ns:g} ns), or 0 for the end.")
        if st < pro.save_ps:
            err("analysis.mmgbsa_stride_ps", f"MM-GBSA can use at most every saved frame: every {pro.save_ps:g} ps or more.")
        elif (a1 or pro.length_ns) - a0 > 0 and int(((a1 or pro.length_ns) - a0) * 1000 / st) < 1:
            err("analysis.mmgbsa_stride_ps", "The MM-GBSA window holds no frame at this stride.")
    if not (1 <= pro.replicates <= 5):
        err("protocol.replicates", "Replicates: 1 to 5.")
    if isinstance(pro.seed, bool) or not isinstance(pro.seed, int) and not (isinstance(pro.seed, float) and pro.seed.is_integer()):
        err("protocol.seed", "The seed is a whole number: -1 for a new one per replicate, or 0 to 2 000 000 000 to repeat a run.")
    elif not (-1 <= int(pro.seed) <= 2_000_000_000):
        # each replicate and segment adds an offset to it (1000 x replicate + 100 x segment), and the engines take 32-bit seeds
        err("protocol.seed", "The seed is -1 for a new one per replicate, or 0 to 2 000 000 000 to repeat a run.")
    if not (250.0 <= pro.temperature_K <= 400.0):
        err("protocol.temperature_K", "Temperature between 250 and 400 K.")

    if cmp.device not in ("gpu", "cpu"):
        err("compute.device", "Device is gpu or cpu.")
    if cmp.device == "cpu":
        if ff.engine not in CPU_ENGINES:
            err("compute.device", f"The CPU check runs {', '.join(ENGINE_LABELS[e] for e in CPU_ENGINES)}; "
                f"{ENGINE_LABELS[ff.engine]} on CPU is too slow to be worth a node.")
        if pro.length_ns > CPU_MAX_NS:
            err("protocol.length_ns", f"A CPU check is at most {CPU_MAX_NS:g} ns. It answers whether the "
                "pose holds at the start, not how it behaves; anything longer waits for a GPU.")
        if pro.save_ps > 0 and pro.length_ns * 1000.0 / pro.save_ps > CPU_MAX_FRAMES:
            err("protocol.save_ps", f"A CPU check keeps at most {CPU_MAX_FRAMES} frames: save every "
                f"{pro.length_ns * 1000.0 / CPU_MAX_FRAMES:g} ps or more for {pro.length_ns:g} ns.")
        part = cmp.partition or site.cpu_partition
        if part not in site.partitions or site.partitions[part].gpu:
            err("compute.partition", f"{site.name} CPU partitions: "
                f"{', '.join(p.name for p in site.partitions.values() if not p.gpu)}.")
        elif cmp.walltime:
            h = parse_walltime(cmp.walltime)
            if h is None:
                err("compute.walltime", "Walltime as HH:MM:SS or D-HH:MM:SS.")
            elif h > site.partitions[part].max_hours:
                err("compute.walltime", f"{part} on {site.name} allows {site.partitions[part].max_hours:g} h per job.")
        part = None
    else:
        part = cmp.partition or site.partition_for(spec.gpu)
    if part is None:
        pass
    elif part not in site.partitions or not site.partitions[part].gpu:
        err("compute.partition", f"{site.name} GPU partitions: "
            f"{', '.join(p.name for p in site.partitions.values() if p.gpu)}.")
    else:
        gpu = spec.gpu
        if cmp.gpu not in ("", GPU_AUTO) and cmp.gpu not in site.gpu_types():
            err("compute.gpu", f"{site.name} has {', '.join(site.gpu_types())}, not {cmp.gpu}; "
                f"auto takes the fastest one with a free card at submit.")
        elif cmp.partition and gpu not in site.partitions[part].gres_types and cmp.gpu != GPU_AUTO:
            err("compute.gpu", f"Partition {part} on {site.name} has "
                f"{', '.join(site.partitions[part].gres_types)}, not {gpu}.")
        if cmp.walltime:
            h = parse_walltime(cmp.walltime)
            if h is None:
                err("compute.walltime", "Walltime as HH:MM:SS or D-HH:MM:SS.")
            elif h > site.partitions[part].max_hours:
                err("compute.walltime", f"{part} on {site.name} allows {site.partitions[part].max_hours:g} h "
                    "per job; a longer run is chained as segments, so ask for the cap or less.")
    if cmp.segment_hours < 0 or (0 < cmp.segment_hours < 1):
        err("compute.segment_hours", f"{cmp.segment_hours:g} h is under the 1 h floor: a segment carries a 30 min "
            "margin on top of 1.5 × its estimate, so anything shorter leaves it no time to run. Use 0 for the partition's limit.")
    elif part is not None and cmp.segment_hours > site.partitions[part].max_hours:
        err("compute.segment_hours", f"{cmp.segment_hours:g} h is over {part}'s {site.partitions[part].max_hours:g} h limit on "
            f"{site.name}; 0 uses that limit.")
    if cmp.mem_gb < 1:
        err("compute.mem_gb", f"{site.name} refuses a job without --mem; give it at least 1 GB.")
    if cmp.gpus_per_replicate < 1:
        err("compute.gpus_per_replicate", "At least one GPU per replicate.")
    return errs


def _validate_membrane(spec: Spec) -> list[dict]:
    """The rules a bilayer adds. On the Amber-topology engines the build is OpenMM's Modeller with
    amber14's lipid17, which fixes the force field, the water and the box, and the engine then runs
    the prmtop it wrote; on Desmond the System Builder builds the bilayer from its own lipid patches."""
    errs: list[dict] = []
    err = lambda f, m: errs.append({"field": f, "message": m})   # noqa: E731
    ff, box, sys_, mem = spec.forcefield, spec.box, spec.system, spec.system.membrane
    if ff.engine not in MEMBRANE_ENGINES:
        err("forcefield.engine", f"{ENGINE_LABELS[ff.engine]} does not run a bilayer here; "
            f"{', '.join(ENGINE_LABELS[e] for e in MEMBRANE_ENGINES)} do. Choose one of those, or a water box.")
    elif ff.engine == "desmond":
        # Desmond's own System Builder tiles the bilayer (build_geometry's membrane_box): OPLS, its own lipids
        if mem.lipid not in DESMOND_LIPIDS:
            err("system.membrane.lipid", f"Desmond's System Builder ships {', '.join(DESMOND_LIPIDS)} bilayers; {mem.lipid} is not "
                "among them. Choose one of those, or Amber / OpenMM for it.")
        if ff.water in WATER.get(ff.protein, ()) and ff.water not in DESMOND_MEMBRANE_WATER:
            err("forcefield.water", f"{ff.water} cannot be used with a Desmond bilayer: the System Builder's lipid patches come "
                f"hydrated with SPC, a three-site water. Choose {' or '.join(DESMOND_MEMBRANE_WATER)}.")
    else:
        if ff.protein != MEMBRANE_PROTEIN_FF:
            err("forcefield.protein", f"{ff.protein} cannot be used with a bilayer: OpenMM's data ships lipid17 alongside "
                f"{MEMBRANE_PROTEIN_FF} only (amber14/protein.ff14SB.xml). Choose {MEMBRANE_PROTEIN_FF}.")
        if ff.water != MEMBRANE_WATER:
            err("forcefield.water", f"{ff.water} cannot be used with a bilayer: the membrane build has "
                f"{MEMBRANE_WATER} (amber14/tip3p.xml) and no OPC. Choose {MEMBRANE_WATER}.")
    if box.shape != "rectangular":
        err("box.shape", f"A {box.shape} box cannot hold a bilayer: a membrane is periodic in x and y, so the box "
            "is rectangular. Choose rectangular.")
    if box.salt not in MEMBRANE_SALTS:
        err("box.salt", f"{box.salt} cannot be placed in a bilayer build: "
            + ("a Desmond bilayer takes a monovalent pair here" if ff.engine == "desmond"
               else "OpenMM's addMembrane places a monovalent pair only")
            + f". Choose {' or '.join(MEMBRANE_SALTS)}.")
    if sys_.ligand_class not in MEMBRANE_LIGAND_CLASSES:
        err("system.ligand_class", f"A {sys_.ligand_class.replace('_', ' ')} is not built into a bilayer here"
            + ("" if ff.engine == "desmond" else ": the OpenMM build path has no GAFF templates")
            + ". A membrane run takes a protein, peptide or nucleic acid partner, or none.")
    if water_choice(sys_) != "none":
        complex_route = sys_.source.kind in ("complex", "rcsb", "alphafold")
        err("system.prep.drop_waters" if complex_route else "system.keep_waters",
            "Crystal waters are not built into a bilayer here: the bilayer build places its own water around the solute. "
            + ("Set Prepare's waters to drop all." if complex_route else "Set the crystal waters to none."))
    if mem.lipid not in LIPIDS:
        err("system.membrane.lipid", f"{mem.lipid!r} is not a lipid Modeller builds; choose {', '.join(LIPIDS)}.")
    lo, hi = MEMBRANE_PADDING_A
    if not (lo <= mem.padding_A <= hi):
        err("system.membrane.padding_A", f"{mem.padding_A:g} Å of water above and below the bilayer is outside "
            f"{lo:g} to {hi:g} Å: under {lo:g} the head groups see their own image, over {hi:g} is water for nothing. "
            "10 Å is the default.")
    if mem.segments.strip():
        try:
            from .annotation import parse_manual_segments
            parse_manual_segments(mem.segments)
        except ValueError as e:
            err("system.membrane.segments", f"{e}: type the transmembrane segments as chain:start-end, separated by commas.")
    if mem.orientation not in ORIENTATIONS:
        err("system.membrane.orientation", f"{mem.orientation!r} is not an orientation here; it is as_imported "
            "(the membrane normal is already z), opm (fetch the OPM orientation at import) or annotation "
            "(orient on the transmembrane segments UniProt annotates, or the hydrophobicity scan predicts).")
    # a bilayer's masks are residue ranges (the solute first, then lipids, water, ions), not
    # names; without the ranges the renderers would hold and keep two residues (the [1, 1]
    # fallback). A files import records them; an RCSB/OPM import gets them from Prepare.
    if needs_prepare(spec):
        pass                      # the chained Prepare job records the ranges and re-renders the masks before the build
    elif not sys_.receptor_residues:
        err("system.receptor_residues", "The solute's residue range is not known (value: none), and a bilayer run "
            "builds its restraint and strip masks from it. Import the structure again, or run Prepare.")
    elif sys_.ligand_class != "none" and not sys_.ligand_residues:
        err("system.ligand_residues", "The partner's residue range is not known (value: none), and a bilayer run "
            "builds its restraint and strip masks from it. Import the structure again, or run Prepare.")
    return errs


GPU_AUTO = "auto"


def resolve_gpu(spec: "Spec", availability: dict | None = None) -> tuple[str, str]:
    """The GPU type a run uses, and why. "" is the site's default; a named type is itself;
    "auto" is the fastest type with a free card right now (availability = {type: {"free": n,
    ...}} from queue.gpu_availability), the default when nothing is free; and once a run was
    submitted, what auto resolved to then (compute.gpu_chosen). An explicit partition limits
    auto to the types that partition carries, and with nothing free it waits for the fastest of
    those when the default is not among them. On Grace, a40 nodes can sit idle while a100 jobs
    wait: this is what auto is for."""
    cmp, site = spec.compute, spec.site
    if cmp.gpu_chosen:
        return cmp.gpu_chosen, f"{cmp.gpu_chosen}, chosen at submit"
    if cmp.gpu and cmp.gpu != GPU_AUTO:
        return cmp.gpu, ""
    if cmp.gpu != GPU_AUTO:
        return site.default_gpu, ""
    free = availability or {}
    order = site.gpu_types()
    if cmp.partition and cmp.partition in site.partitions:
        order = [g for g in order if g in site.partitions[cmp.partition].gres_types]
    seen = " · ".join(f"{g} {free[g].get('free', 0)}" for g in order if g in free)
    need = max(1, cmp.gpus_per_replicate)
    for g in order:
        if free.get(g, {}).get("free", 0) >= need:
            return g, f"auto: {g} ({free[g]['free']} free now; {seen})"
    # nothing free (or no sinfo: the page's preview): the run waits for the site's default type, or, when the
    # chosen partition does not carry it (gpu-a40 on Grace), for the fastest type the partition does
    wait = site.default_gpu if site.default_gpu in order or not order else order[0]
    return wait, ("auto: no GPU free right now" + (f" ({seen})" if seen else "") + f"; queued for {wait}")


def _safe_gres(site: Site, gpu: str, count: int, part: str) -> str:
    try:
        return site.gres(gpu, count, part)
    except ValueError:
        return f"gpu:{gpu}:{count} (not in {part})"


def parse_walltime(text: str) -> float | None:
    """'HH:MM:SS' or 'D-HH:MM:SS' -> hours."""
    m = re.fullmatch(r"(?:(\d+)-)?(\d{1,3}):(\d{2})(?::(\d{2}))?", text.strip())
    if not m:
        return None
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 24 + h + mi / 60 + s / 3600


def chain_ranges(system: "System") -> list[dict]:
    """The chains of the built complex as residue ranges in the topology's numbering (tleap
    numbers the receptor's chains first, in file order, then the partner's), so a viewer of the
    stripped trajectory, which carries no chain letters, can draw one chain differently from
    another. [{id, part: "rec" | "lig", range: [first, last], residues, approx}]. approx is
    set when the chains' residue counts do not add up to the range the build recorded (a chain
    the preparation changed after the import), and the ranges are then by count, best effort."""
    rec, lig = system.receptor_residues, system.ligand_residues
    chains = [c for c in system.chains_found if c.get("kind") in ("protein", "nucleic", "nucleic_acid") and c.get("residues")]
    partner = set(system.partner_chains or [])
    receptor = set(system.receptor_chains or []) or {c["id"] for c in chains if c["id"] not in partner}
    out: list[dict] = []
    for part, ids, rng in (("rec", receptor, rec), ("lig", partner, lig)):
        mine = [c for c in chains if c["id"] in ids]
        if not mine or not rng:
            continue
        n, total = rng[0], sum(int(c["residues"]) for c in mine)
        approx = total != rng[1] - rng[0] + 1
        for c in mine:
            k = int(c["residues"])
            out.append({"id": c["id"], "part": part, "range": [n, min(n + k - 1, rng[1]) if not approx else n + k - 1],
                        "residues": k, "approx": approx})
            n += k
    return out


def clean_keeps(system: "System") -> tuple[list[dict], list[dict]]:
    """The hetero groups of the import (hetero_found) that Clean keeps under the prep options, as (the
    ions Prepare carries into the build, the groups no force field here covers). The classes are the
    ones prepare() drops, read from the residue name as Clean reads it; the cations a nucleic acid binds
    (nucleic_bound, counted at import) stay when the buffer setting drops ions, as Clean keeps them. A
    bound ligand chosen as the partner (source.ligand) is out of the structure before Clean, and a
    modified nucleotide is refused with its chain. An ion is carried when the builds have parameters
    for it (BUILD_IONS)."""
    from .structure import BUILD_IONS, classify
    pr = system.prep
    dropped = {"water"}
    if pr.drop_buffers:
        dropped |= {"buffer", "ion"}
    if not pr.keep_cofactors:
        dropped |= {"cofactor", "metal"}
    if not pr.keep_other_hetero:
        dropped |= {"ligand", "glycan"}
    names = {str(n).strip().upper() for n in pr.drop_hetero or []}
    chosen = re.split(r"[\s:]+", (system.source.ligand or "").strip())[0].upper()
    kept = []
    for h in system.hetero_found or []:
        name = str(h.get("resname", "")).upper()
        if name in names or name == chosen or h.get("class") == "modified nucleotide":
            continue
        kind = classify(name)
        if kind not in dropped:
            kept.append(h)
        elif kind == "ion" and pr.drop_buffers and h.get("nucleic_bound"):
            kept.append({**h, "count": h["nucleic_bound"]})
    return [h for h in kept if h.get("resname") in BUILD_IONS], [h for h in kept if h.get("resname") not in BUILD_IONS]


def water_choice(system: "System") -> str:
    """The crystal waters the run keeps: "none", "near" (those within structure.NEAR_WATER_A of the partner) or
    "all". On the complex route it is Prepare's choice (prep.drop_waters), on the others the import's (keep_waters)."""
    if system.source.kind in ("complex", "rcsb", "alphafold"):
        return {"far": "near", "none": "all"}.get(system.prep.drop_waters, "none")
    return {"near_ligand": "near", "all": "all"}.get(system.keep_waters, "none")


def run_chains(system: "System") -> list[dict]:
    """The polymer chains the run simulates, as detected: on a complex source the receptor and partner
    chains chosen (every chain while none is named), on the files route the receptor file's chains."""
    chains = [c for c in system.chains_found if c.get("kind") in ("protein", "nucleic", "nucleic_acid")]
    chosen = set(system.receptor_chains or []) | set(system.partner_chains or [])
    if system.source.kind in ("complex", "rcsb", "alphafold") and chosen:
        chains = [c for c in chains if c.get("id") in chosen]
    return chains


def part_kinds(system: "System", part: str = "") -> set[str]:
    """What the run is made of, "protein" and/or "nucleic": the receptor ("rec"), the partner ("lig") or the
    whole solute (""). A partner file's chains are not detected; its class says what it is."""
    partner = set(system.partner_chains or [])
    kinds = set()
    for c in run_chains(system):
        if not part or (part == "lig") == (c.get("id") in partner):
            kinds.add("nucleic" if c.get("kind") in ("nucleic", "nucleic_acid") else "protein")
    if part != "rec":
        kinds |= {"nucleic_acid": {"nucleic"}, "protein": {"protein"}, "peptide": {"protein"}}.get(system.ligand_class, set())
    return kinds


def has_nucleic(system: "System") -> bool:
    """DNA or RNA in the run, as the receptor or the partner: the build then loads a nucleic-acid force field."""
    return "nucleic" in part_kinds(system)


def partner_class(system: "System") -> str:
    """The class of a complex's partner, from the chains chosen for it: nucleic_acid when any of them is DNA
    or RNA, protein for protein chains, none without a partner. The person may change the split the import
    proposed, and the class follows it. A small molecule bound in the entry and chosen at import
    (source.ligand) is the partner whatever the chains: every chain is then the receptor."""
    if (system.source.ligand or "").strip():
        return "small_molecule"
    kinds = {c.get("kind") for c in system.chains_found if c.get("id") in set(system.partner_chains or [])}
    if kinds & {"nucleic", "nucleic_acid"}:
        return "nucleic_acid"
    return "protein" if kinds else "none"


#: the atoms that trace a chain in the analysis (the RMSD fit, RMSF, the interface) and that hold a residue in
#: production: Cα of an amino acid (with C and N for a hold), P and C4′ of a nucleotide. The 5′ nucleotide of a
#: strand has no P; every nucleotide has a C4′.
TRACE_ATOMS = ("CA", "P", "C4'")
HOLD_ATOMS = ("CA", "C", "N", "P", "C4'")


def trace_mask(system: "System") -> str:
    """The atom part of the cpptraj masks that trace the chains: @CA for proteins, @CA,P,C4' when a nucleic
    acid is in the run (the names select per residue: an amino acid has no P or C4′, a nucleotide no Cα)."""
    return "@" + ",".join(TRACE_ATOMS if has_nucleic(system) else TRACE_ATOMS[:1])


def needs_prepare(spec: "Spec") -> bool:
    """A structure that arrived whole (an entry, a complex file, an AlphaFold model) is cleaned,
    modeled, protonated and split by the Prepare stage before the engine builds from it. When
    that has not happened yet, Submit queues Prepare as the first job of the graph and the
    Prepare job re-renders the run's files from the prepared structure (hopemd.rerender)."""
    s = spec.system
    return s.source.kind in ("complex", "rcsb", "alphafold") and not s.prepared


def mmgbsa_window(spec: "Spec") -> tuple[float, float, float]:
    """(from_ns, to_ns, stride_ps) of the MM-GBSA slice, the end filled in: what the mmpbsa.in
    (Prime's job on Desmond), the estimate and the report all read."""
    an, pro = spec.analysis, spec.protocol
    a0 = max(0.0, float(an.get("mmgbsa_from_ns", 0) or 0))
    a1 = float(an.get("mmgbsa_to_ns", 0) or 0) or pro.length_ns
    st = max(float(an.get("mmgbsa_stride_ps", 200) or 200), pro.save_ps)
    return a0, min(a1, pro.length_ns), st


def split_links(sysm: "System") -> list[str]:
    """The disulfides that join the receptor to the partner, by the structure's ids: ["H:122–L:1"]."""
    return [f"{d.get('receptor')}–{d.get('partner')}" for d in (getattr(sysm, "split_disulfides", None) or [])
            if isinstance(d, dict)]


def mmgbsa_refused(sysm: "System") -> str:
    """Why MM-GBSA is not run on this system, or "" when nothing stands in its way.

    A disulfide that crosses the split joins the receptor to the partner: the run is one molecule. MM-GBSA's single
    trajectory takes each frame of the complex apart into the receptor and the partner and scores each alone, and here
    that cuts the bond: each half keeps a cysteine whose sulfur is bonded to nothing, and the complex's own terms
    across the link (the bond, the angles and torsions that span it, the pairs it excludes from the nonbonded sums or
    scales as 1-4) go into E(complex) - E(receptor) - E(partner) as if they were binding. The difference is not a
    binding energy, and no correction makes it one, so HOPE-MD does not run it and says why: the render queues no
    MM-GBSA job, a job queued before Prepare found the link stops at its first line (analysis/mmgbsa_refused.txt),
    the Runs screen's MM-GBSA... is refused, and the report says the two sides are covalently linked."""
    links = split_links(sysm)
    if not links:
        return ""
    what = (f"the disulfide {links[0]}" if len(links) == 1 else f"{len(links)} disulfides ({', '.join(links)})")
    return (f"MM-GBSA is not run: the receptor and the partner are covalently linked by {what}, so the run is one molecule. "
            "MM-GBSA scores the complex against the receptor and the partner taken apart, and taken apart here each keeps a "
            "cysteine whose bond was cut: the terms of the bond itself would go into the difference, which would not be a "
            "binding energy. A split that no bond crosses can be scored.")


def mmgbsa_runs(spec: "Spec") -> bool:
    """Whether the run scores MM-GBSA: asked for, a partner or ligand to score, and nothing that refuses it."""
    return bool(spec.analysis.get("mmgbsa", True)) and spec.system.ligand_class != "none" and not mmgbsa_refused(spec.system)


def mmgbsa_frames(spec: "Spec") -> int:
    a0, a1, st = mmgbsa_window(spec)
    return max(0, int(((a1 - a0) * 1000.0) / st))


def format_walltime(hours: float) -> str:
    """Rounded up to the minute: a walltime with seconds in it reads as a mistake."""
    total = int(math.ceil(hours * 60 - 1e-9)) * 60
    h, rem = divmod(total, 3600)
    mi, s = divmod(rem, 60)
    return f"{h:02d}:{mi:02d}:{s:02d}"


# ---------------------------------------------------------------------- derived
def derive(spec: Spec, ns_per_day: float | None = None, ns_per_day_note: str = "", availability: dict | None = None) -> dict:
    """Everything the page shows in the derived panel. Pure arithmetic; no files touched.
    availability (free GPUs by type, from queue.gpu_availability) is what "auto" chooses from."""
    sys_, box, pro, cmp = spec.system, spec.box, spec.protocol, spec.compute
    site = spec.site
    d: dict[str, Any] = {}

    # --- box and atoms (estimate until the build has run) ---
    ext = sys_.extent_A or [40.0, 40.0, 40.0]
    solute = sys_.solute_atoms or 2000
    solute_vol = solute * 22.0     # ~22 Å^3 per solute atom, rough
    lipids = 0
    desmond = spec.engine == "desmond"
    if sys_.environment == "membrane" and desmond:
        # the System Builder's membrane_box: no whole tiles, the patch cut to the solute plus the padding
        mem = sys_.membrane
        px, py, pz, n_patch, atoms_per_lipid, patch_waters = DESMOND_LIPID_PATCH.get(mem.lipid, DESMOND_LIPID_PATCH["POPC"])
        x, y = ext[0] + 2 * mem.padding_A, ext[1] + 2 * mem.padding_A
        z = max(pz, ext[2]) + 2 * mem.padding_A
        lipids = max(0, round(2 * (x * y - math.pi / 4 * ext[0] * ext[1]) / (px * py / (n_patch / 2))))
        lipid_vol = (px * py * pz - patch_waters / WATER_DENSITY_PER_A3) / n_patch
        box_desc = f"{x:.0f} × {y:.0f} × {z:.0f} Å, {mem.lipid} bilayer"
        vol = x * y * z
        waters = max(0, int((vol - lipids * lipid_vol - solute * DESMOND_BILAYER_SOLUTE_A3) * WATER_DENSITY_PER_A3))
        solute_atoms_in_box = solute + lipids * atoms_per_lipid
    elif sys_.environment == "membrane":
        # Modeller pads the solute by padding_A in x and y and tiles whole lipid patches over
        # that (LIPID_PATCH_A), so the box is nx by ny patch cells; z is the patch's height or
        # the solute plus padding_A above and below, whichever is more. An estimate: the build
        # deletes the lipids under the solute, and build/system.json has the real counts.
        mem = sys_.membrane
        px, py, pz, atoms_per_lipid = LIPID_PATCH_A.get(mem.lipid, LIPID_PATCH_A["POPC"])
        nx = max(1, math.ceil((ext[0] + 2 * mem.padding_A) / px))
        ny = max(1, math.ceil((ext[1] + 2 * mem.padding_A) / py))
        x, y = nx * px, ny * py
        z = max(pz, ext[2] + 2 * mem.padding_A)
        lipids = LIPIDS_PER_PATCH * nx * ny
        box_desc = f"{x:.0f} × {y:.0f} × {z:.0f} Å, {mem.lipid} bilayer"
        # water fills what is not bilayer, minus the part of the solute that stands out of it
        outside = max(0.0, 1.0 - BILAYER_THICKNESS_A / ext[2]) if ext[2] > 0 else 0.0
        vol = x * y * (z - BILAYER_THICKNESS_A)
        waters = max(0, int((vol - solute_vol * outside) * WATER_DENSITY_PER_A3))
        solute_atoms_in_box = solute + lipids * atoms_per_lipid
    elif desmond:
        # the System Builder's orthorhombic box, whatever the shape asked (FIXED_BOX)
        side = [e + 2 * box.padding_A + DESMOND_BOX_EXTRA_A for e in ext]
        vol = side[0] * side[1] * side[2]
        box_desc = f"{side[0]:.0f} × {side[1]:.0f} × {side[2]:.0f} Å"
        waters = max(0, int((vol - solute * DESMOND_SOLUTE_A3) * WATER_DENSITY_PER_A3))
        solute_atoms_in_box = solute
    else:
        # tleap's own box and water count, from the solute's shape as the import or Prepare measured it
        from .solvation import water_box, shape_from_extent
        shape = sys_.solute_shape or shape_from_extent(ext, solute)
        wb = water_box(shape, box.shape, box.padding_A, spec.forcefield.water)
        box_desc, waters = wb["desc"], wb["waters"]
        # a crystal water kept takes the model's extra point, as the box's waters do
        solute_atoms_in_box = solute + (WATER_SITES.get(spec.forcefield.water, 3) - 3) * shape.get("waters", 0)
    ion_pairs = int(round(box.salt_mM / 1000.0 * waters / MOLAR_WATER))
    sites = WATER_SITES.get(spec.forcefield.water, 3)
    if sys_.environment != "membrane" and not desmond:
        # tleap puts each ion in a water's place; MgCl2 is one Mg2+ and two Cl- a pair
        salt_ions = ion_pairs * (3 if box.salt == "MgCl2" else 2)
        atoms = solute_atoms_in_box + sites * (waters - salt_ions) + salt_ions
    else:
        atoms = solute_atoms_in_box + waters * sites + 2 * ion_pairs
    d.update(box=box_desc, waters=waters, ion_pairs=ion_pairs, atoms=atoms, lipids=lipids,
             atoms_estimated=sys_.solute_atoms is None)

    # --- steps, frames, disk ---
    steps = int(round(pro.length_ns * 1e6 / pro.timestep_fs))
    frames = int(round(pro.length_ns * 1000.0 / pro.save_ps))
    disk_gb = frames * atoms * BYTES_PER_ATOM_FRAME / 1e9
    stripped_gb = frames * solute * BYTES_PER_ATOM_FRAME / 1e9
    d.update(steps=steps, frames=frames, disk_gb_per_replicate=round(disk_gb, 2),
             stripped_gb_per_replicate=round(stripped_gb, 3),
             disk_gb_total=round(disk_gb * pro.replicates, 1), hmr=spec.hmr)

    # --- time ---
    on_cpu = cmp.device == "cpu"
    gpu, gpu_note = ("", "") if on_cpu else resolve_gpu(spec, availability)
    device_key = f"cpu{site.cpu_cores_per_node}" if on_cpu else gpu
    if ns_per_day is None:
        from .estimate import estimate_ns_per_day, speed_engine
        ns_per_day, ns_per_day_note = estimate_ns_per_day(site.key, device_key,
                                                          speed_engine(spec.engine, spec.forcefield.water, on_cpu),
                                                          atoms, pro.timestep_fs)
    hours = pro.length_ns / ns_per_day * 24.0
    part = cmp.partition or (site.cpu_partition if on_cpu else site.partition_for(gpu))
    if part not in site.partitions or site.partitions[part].gpu == on_cpu:
        part = site.cpu_partition if on_cpu else site.gpu_partition
    cap = site.partitions[part].max_hours
    if cmp.segment_hours > 0:                 # a chosen cap under the partition's: how chaining is exercised on a short run
        cap = min(cap, cmp.segment_hours)
    ask = 1.5 * hours + 0.5
    # the estimate alone, whatever the field holds: what the page fills the walltime field with
    estimate_h = ask if ask <= cap else cap
    # a walltime in the field is each production job's time limit: a longer one gives a slow node room, a
    # shorter one cuts the run into more segments
    typed_h = parse_walltime(cmp.walltime) if cmp.walltime else None
    if typed_h and typed_h > 0:
        cap = min(cap, typed_h)
    if ask <= cap:
        segments, seg_ns, walltime_h = 1, pro.length_ns, (typed_h if typed_h and typed_h > 0 else ask)
    else:
        seg_ns = max(pro.save_ps / 1000.0, math.floor((cap - 0.5) / 1.5 * ns_per_day / 24.0))
        segments = int(math.ceil(pro.length_ns / seg_ns))
        seg_ns = pro.length_ns / segments
        walltime_h = cap
    d.update(ns_per_day=round(ns_per_day, 1), ns_per_day_note=ns_per_day_note,
             hours_per_replicate=round(hours, 2), segments=segments,
             segment_ns=seg_ns, walltime=format_walltime(walltime_h), walltime_estimate=format_walltime(estimate_h),
             wall_cap_hours=cap, partition=part, device=cmp.device, device_key=device_key,
             gpu_hours=(0.0 if on_cpu else round(hours * pro.replicates * cmp.gpus_per_replicate, 1)),
             core_hours=(round(hours * pro.replicates * site.cpu_cores_per_node, 1) if on_cpu else
                         round(hours * pro.replicates * cmp.cpus, 1)),
             gres=("" if on_cpu else _safe_gres(site, gpu, cmp.gpus_per_replicate, part)),
             gpu=gpu, gpu_note=gpu_note, gpu_free=availability or {},
             cores=(site.cpu_cores_per_node if on_cpu else cmp.cpus))

    # --- the other stages, from finished jobs here (timings.tsv); the production from ns/day ---
    from .estimate import estimate_stage_seconds
    ladder = int(pro.minimise_steps + (pro.heat_ps + pro.equil_ps) * 1000 / 2.0)
    mm_frames = mmgbsa_frames(spec)
    stage_s, notes = {}, []
    # Prepare and Tidy are estimated like the rest, from finished jobs here: the run plan gave
    # Prepare a place and no time while every other step had both, which read as a step that
    # takes no time.
    for stage, st, fr in (("prepare", None, None), ("build", None, None), ("equil", ladder, None),
                          ("analysis", None, frames), ("mmgbsa", None, mm_frames)):
        if stage == "mmgbsa" and not mmgbsa_runs(spec):
            continue
        if stage == "prepare" and not needs_prepare(spec):
            continue
        sec, note = estimate_stage_seconds(site.key, spec.engine, device_key, stage, atoms, st, fr)
        if sec is not None:
            stage_s[stage] = round(sec)
        else:
            notes.append(note)
    stage_s["prod_segment"] = round(seg_ns / ns_per_day * 24 * 3600)
    stage_s["prod"] = round(hours * 3600)
    total = stage_s.get("build", 0) + stage_s.get("equil", 0) + stage_s["prod"] + stage_s.get("analysis", 0) + stage_s.get("mmgbsa", 0)
    d.update(stage_seconds=stage_s, total_seconds=total, stages_known=sorted(k for k in stage_s if k not in ("prod", "prod_segment")),
             stage_note=("; ".join(notes) if notes else "every stage from finished jobs here"))
    return d
