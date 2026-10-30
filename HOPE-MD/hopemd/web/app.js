/* HOPE-MD page. Plain JavaScript, no build step. Served by hopemd.server, which is the
   only thing it talks to. The steps are cards in the main column; the rail holds the resources,
   where the run goes, the plan and Submit. Every size comes from a token in style.css. */
'use strict';
const T = new URLSearchParams(location.search).get('t') || '';
const $ = id => document.getElementById(id);
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

async function api(path, body) {
  const url = path + (path.includes('?') ? '&' : '?') + 't=' + encodeURIComponent(T);
  const opt = body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {};
  const r = await fetch(url, opt);
  const ct = r.headers.get('Content-Type') || '';
  if (!ct.includes('json')) return {text: await r.text(), status: r.status};
  return r.json();
}
const get = (o, path) => path.split('.').reduce((a, k) => (a == null ? undefined : a[k]), o);
const set = (o, path, v) => { const ks = path.split('.'); let a = o; for (const k of ks.slice(0, -1)) a = a[k] = a[k] || {}; a[ks[ks.length - 1]] = v; };

/* ------------------------------------------------------------------ state */
let SITE = null, SPEC = null, CHOICES = {}, ERRORS = [], DERIVED = {}, STAGING = null, STRUCT = null;
let WALL_AUTO = true;   // the walltime follows the estimate until the user types one; Estimate puts it back on the estimate
let NAME_AUTO = true;               // the name follows the source until the person types one
let HAS_LIG = false;                // a second model (ligand / partner) is loaded in the viewer
let V = null, VR = null;            // 3Dmol viewers
const SHOW = {rec: true, lig: true}; let COLOR = 'chain', DRAW = 'cartoon', SHOWTM = true;
/* What the structure card shows about what was chosen: its name and where it came from. The
   source itself stays in SPEC.system.source, which is what is submitted. */
let PICKED = null;
/* ------------------------------------------------------------------ schema */
/* What each source is called in the list. Short enough to be read inside the box: a name of
   26 characters came out as "Complex file (PDB / mm" at every text size, and a choice nobody
   can read is not a choice. What each one means is behind the field's ?. */
let KINDS = {files: 'Receptor + partner', complex: 'Complex file', rcsb: 'PDB code (RCSB)', alphafold: 'AlphaFold',
            poseviewer: 'Maestro poseviewer'};
const COMPLEX = k => ['complex', 'rcsb', 'alphafold'].includes(k);
const LABEL = {
  none: 'no partner', peptide: 'peptide', protein: 'protein partner', small_molecule: 'small molecule', nucleic_acid: 'nucleic acid',
  all: 'Drop all', far: 'Keep near the partner', propka: 'PROPKA', pdbfixer: 'PDBFixer', none: 'None',
  'gaff2-am1bcc': 'GAFF2 + AM1-BCC', 'openff-sage': 'OpenFF Sage', cgenff: 'CGenFF', opls4: 'OPLS4',
  compact: 'Truncated octahedron', cubic: 'Cubic', rectangular: 'Rectangular',
  gpu: 'GPU', cpu: 'CPU node', quick: 'Quick check', standard: 'Standard', long: 'Long', cpu_check: 'CPU check',
  water: 'Water box', membrane: 'Lipid bilayer', as_imported: 'As imported', opm: 'Fetch the OPM orientation',
};
const LIPIDS = ['POPC', 'POPE', 'DLPC', 'DLPE', 'DMPC', 'DOPC', 'DPPC'];   // what OpenMM 8.6's Modeller.addMembrane builds
const MEMBRANE = s => s.system.environment === 'membrane';
/* what the run's chains are made of (spec.part_kinds): the receptor's ('rec'), the partner's ('lig') or the whole solute ('') */
function partKinds(s, part) {
  const sys = s.system, partner = new Set(sys.partner_chains || []), chosen = new Set((sys.receptor_chains || []).concat(sys.partner_chains || []));
  let chains = (sys.chains_found || []).filter(c => ['protein', 'nucleic', 'nucleic_acid'].includes(c.kind));
  if (COMPLEX(sys.source.kind) && chosen.size) chains = chains.filter(c => chosen.has(c.id));
  const kinds = new Set();
  for (const c of chains) if (!part || (part === 'lig') === partner.has(c.id)) kinds.add(c.kind === 'protein' ? 'protein' : 'nucleic');
  if (part !== 'rec') { if (sys.ligand_class === 'nucleic_acid') kinds.add('nucleic'); if (['protein', 'peptide'].includes(sys.ligand_class)) kinds.add('protein'); }
  return kinds;
}
const hasNucleic = s => partKinds(s, '').has('nucleic');
/* the atoms the analysis traces the chains by: Cα, and P and C4′ of a nucleotide */
const traceWords = k => k.has('nucleic') ? (k.has('protein') ? 'Cα, P and C4′' : 'P and C4′') : 'Cα';
const BILAYER_A = 40;                                                  // the slab the viewer draws: about a POPC bilayer
const lab = v => LABEL[v] || v;
/* The 3D viewers' labels and the interface diagram are drawn, not written: they are pixels on a
   canvas and units inside an SVG, so the type tokens never reach them and a page at 150% drew
   its contact labels at 9px. These read the smallest token back and scale the drawings by it,
   so a drawing grows with the control and never goes under the page's smallest size. */
let SIZEPROBE = null;
function tokenPx(name) {
  if (!SIZEPROBE) {
    SIZEPROBE = document.createElement('span');
    SIZEPROBE.style.cssText = 'position:absolute;visibility:hidden;pointer-events:none';
    document.body.appendChild(SIZEPROBE);
  }
  SIZEPROBE.style.fontSize = 'var(' + name + ')';
  return parseFloat(getComputedStyle(SIZEPROBE).fontSize) || 13;
}
/* 1 at 100% and below (where the tokens hold their floor), 1.5 at 150%. */
const drawScale = () => Math.max(1, tokenPx('--fs-xs') / 13);
const drawnPx = base => Math.round(base * drawScale());

/* what is what: a ? beside every field and group, the text on hover */
const HELP = {
  'system.source.kind': 'Where the structure comes from. It decides how the receptor and the ligand or partner are found and split, what is detected (chains, confidence, scores) and how the run is named.',
  'system.source.path': 'The file or folder for this source. Browse opens the places this server may read; a path can be typed or pasted.',
  'system.source.pdb_id': 'The four-character RCSB code (1BHL). The entry, its title and resolution are fetched from the RCSB.',
  'system.source.assembly': 'Which biological assembly to take: 1, 2 … as the RCSB defines them, or asu for the asymmetric unit as deposited.',
  'system.source.ligand_path': 'The ligand or partner file: a small molecule (sdf, mol2, pdb), a peptide or a protein chain (pdb). Empty simulates the receptor alone.',
  'system.source.peptide': 'Which peptide of the docking run to load now, in the order the docking ranked them. Every peptide can be simulated at once from the box below.',
  'system.source.pose': 'Which ranked pose (1 = best) of the peptide, or which AlphaFold model (1 = top ranked, the file the server names model_0).',
  'system.receptor_chains': 'The chains that are the receptor: fitted for the RMSD and the interface measures. Letters as in the file. With DNA or RNA bound to a protein the protein chains are proposed, with every strand as the partner; a nucleic acid alone is simulated whole, with no partner.',
  'system.partner_chains': 'The chains on the other side of the interface: the thing being simulated against the receptor.',
  'system.source.ligand': 'A small molecule bound in the structure, run as the partner in place of partner chains: its bond orders come from the PDB\'s Chemical Component Dictionary, its acids and bases are set for pH 7.4, and every chain Prepare keeps is the receptor. Waters, ions and crystallization additives are not offered, nor is a ligand bound covalently, holding a metal or spread over several residues.',
  'system.chains': 'Which chains of the receptor file to keep; empty keeps all polymer chains. A change imports the files again.',
  'system.ph': 'The pH the protonation states are assigned for (PROPKA in Prepare, or pdb2pqr in the build).',
  'system.keep_waters': 'Crystal waters to keep from the receptor and partner files: none, those within 5 Å of the partner or ligand, or all. The build adds them after the solute as the run\'s water model. A change imports the files again.',
  'system.keep_metals': 'Keep the metal ions of the files (Zn²⁺, Mg²⁺, Ca²⁺ …) the builds have parameters for, as free ions where the files have them. The ions a DNA or RNA binds stay either way. A change imports the files again.',
  'system.ligand_class': 'Detected from the ligand file: a small molecule needs GAFF2 parameters and a net charge; a peptide, protein or nucleic acid is covered by the force field.',
  'system.ligand_charge': 'The net charge of the small molecule. A file with bond orders (SDF, MOL2, a ligand taken from a structure) runs as it is drawn, so its formal charges fix it: to run another charge, load a file protonated for it. A PDB ligand has none, and you set it. AM1-BCC charges are fitted to it, so it must be right.',
  'system.ligand_charge_confirmed': 'Tick once the net charge is checked: a wrong charge is the commonest way a ligand simulation goes wrong.',
  'system.environment': 'A water box around the solute, or a lipid bilayer through it with water above and below (membrane proteins).',
  'system.membrane.lipid': 'The lipid of the bilayer: OpenMM\'s Modeller builds it with lipid17 on the Amber-topology engines, the System Builder on Desmond (POPC, POPE, DPPC or DMPC). POPC is the usual choice for a generic membrane.',
  'system.membrane.padding_A': 'How much water lies above and below the bilayer, in Å; also the padding in x and y.',
  'system.membrane.center_z_A': 'Where the bilayer center sits along z. 0 for an OPM entry or a structure oriented on its transmembrane segments.',
  'system.membrane.orientation': 'How the structure is put into the membrane frame: as imported (its normal is already z), from OPM (their curated placement for an RCSB entry), or on the transmembrane segments found by annotation.',
  'system.membrane.segments': 'Transmembrane segments you know, as chain:start-end; they override what UniProt or BLAST found, for the coloring and the orientation.',
  'system.prep.drop_buffers': 'Remove the crystallographic additives that came with the file: glycerol, sulfate, PEG, ethylene glycol, buffer ions and the like.',
  'system.prep.keep_cofactors': 'Keep metals and cofactors (heme, NAD, …). A metal ion goes into the build as a free ion; a cofactor no force field here covers stops Submit, by name, until it is dropped or run as the partner.',
  'system.prep.keep_other_hetero': 'Keep other bound ligands and glycans instead of removing them.',
  'system.prep.drop_hetero': 'Hetero groups to remove whatever their class, by residue name (NAD, HEM): a cofactor no force field here covers can go while the metals stay.',
  'system.prep.drop_waters': 'Remove all crystal waters, only those farther than 5 Å from the partner or ligand, or none. The waters kept go into the build after the solute as the run\'s water model.',
  'system.prep.altloc': 'Which alternate conformation to keep where the file has two (A by default).',
  'system.prep.model_loops_max': 'A gap inside a chain up to this many residues is built (PDBFixer, from a fixed seed), relaxed with its neighbors on springs, its peptide bonds held trans and its residues L, and checked; a loop that will not fit stops Prepare. 12 at most; 0 builds none. A longer gap is left open and capped (ACE, NME) unless capping is off. Missing chain ends are never built.',
  'system.prep.cap_breaks': 'Cap each gap left open: an NME on the residue before it and an ACE on the residue after it, so its ends are neutral amides, not a pair of charges (NH3+ and COO-) the protein does not have. The chain\'s true termini stay charged. Off, every piece of a broken chain ends in charged termini.',
  'system.prep.trim_plddt': 'Cut disordered tails of an AlphaFold model below this pLDDT; 0 keeps everything. Shown only when the B-factors are pLDDT: an X-ray structure\'s are not.',
  'system.prep.protonation': 'How side-chain protonation states are assigned at the pH above: PROPKA (pKa-based), PDBFixer defaults, or none.',
  'system.prep.minimise': 'A short restrained minimization in vacuum after modeling, to relieve clashes the modeling made.',
  'system.prep.minimise_steps': 'Steps of that minimization.',
  'forcefield.engine': 'The MD program. Amber (pmemd.cuda), OpenMM, GROMACS and NAMD run the same Amber topology (same force field and analysis; MM-GBSA by MMPBSA.py); Desmond builds its own system with OPLS, and its MM-GBSA is Prime\'s (thermal_mmgbsa.py, under the Schrödinger module and its license).',
  'forcefield.protein': 'The protein force field. ff19SB with OPC water is the current Amber recommendation; a bilayer on the Amber-topology engines needs ff14SB with TIP3P. On Desmond, OPLS_2005 is what the academic license covers; OPLS4 needs the OPLS license.',
  'forcefield.water': 'The water model, matched to the protein force field; a Desmond bilayer takes SPC or TIP3P, as its lipid patches come hydrated with SPC.',
  'forcefield.nucleic': 'The force field for DNA and RNA, as the partner or as the receptor itself: OL21 or bsc1 (OL15 on OpenMM) for DNA, each with OL3 for RNA. On Desmond the run\'s OPLS types the nucleic acid.',
  'forcefield.ligand': 'Parameters for a small molecule: GAFF2 with AM1-BCC charges, made in the build job.',
  'box.shape': 'The shape of the water box: compact (truncated octahedron, fewest waters), cubic, or rectangular (a bilayer needs it). Desmond builds rectangular only.',
  'box.padding_A': 'The least distance from any solute atom to the box edge, in Å. 10 to 12 is usual.',
  'box.salt': 'The salt added after neutralizing: NaCl, KCl or MgCl2.',
  'box.salt_mM': 'The salt concentration, in millimolar; 150 is physiological.',
  'protocol.length_ns': 'Production length per replicate, in nanoseconds.',
  'protocol.replicates': 'Independent repeats with different velocities; three tell you what is noise.',
  'protocol.seed': 'The random seed of the build (where the ions go) and of each replicate\'s starting velocities and thermostat. -1 draws new ones every run; a number of your own builds the same system and starts every replicate the same way, each offset from it so they still differ. Amber then repeats the run bit for bit on the same type of GPU; on the other engines a repeat starts the same but may drift apart, as their GPU arithmetic is not guaranteed to repeat in the last digits.',
  'protocol.timestep_fs': '4 fs runs with hydrogen mass repartitioning (done in the build); 2 fs is the conservative choice.',
  'protocol.save_ps': 'How often a frame is written, in picoseconds; 20 ps for a 100 ns run is 5 000 frames.',
  'protocol.temperature_K': 'The production temperature (Langevin thermostat on Amber, OpenMM and NAMD; V-rescale on GROMACS; MTK on Desmond).',
  'protocol.pressure_bar': 'The production pressure (Monte Carlo barostat on Amber and OpenMM, C-rescale on GROMACS, Langevin piston on NAMD, MTK on Desmond; semi-isotropic for a bilayer, NPgT on Desmond).',
  'analysis.mmgbsa': 'Binding free energy of the partner or ligand over frames of the production, one job per replicate after the trajectory is done: MMPBSA.py (GB-Neck2) on the Amber-topology engines, Prime MM-GBSA (thermal_mmgbsa.py, the Schrödinger module and its license) on Desmond.',
  'analysis.mmgbsa_from_ns': 'The first nanosecond of the production the MM-GBSA frames come from; 0 is the start. Skipping the first few nanoseconds leaves the relaxation out.',
  'analysis.mmgbsa_to_ns': 'The last nanosecond of the production the MM-GBSA frames come from; 0 means the end of the run.',
  'analysis.mmgbsa_stride_ps': 'One frame every this many picoseconds inside the window, no closer than the save interval; 200 ps over 10 ns is 50 frames, enough for the mean and its error.',
  'compute.device': 'One GPU per replicate, or one whole CPU node for a short check (10 ns at most).',
  'compute.partition': 'The Slurm partition; empty lets the GPU type decide.',
  'compute.gpu': 'Which GPU type to ask for; auto takes the fastest type with a free card at submit and switches partition with it.',
  'compute.gpus_per_replicate': 'GPUs per replicate; one is right for these system sizes.',
  'compute.max_concurrent': 'How many replicates may run at once; 0 means all of them.',
  'compute.walltime': 'The time limit of one production segment, filled in by itself from the measured speeds as the settings above change; type one to override it, empty it to follow the estimate again. A run longer than the partition allows is chained as segments.',
  'compute.tidy': 'When the run is done, a last small job packs the files nothing reads any more (stage logs, intermediate restarts, build scratch) into one archive under archive/, so a run holds tens of files instead of hundreds: the scratch quota counts files. Trajectories, restart points, analysis and report stay; Undo on the Runs screen unpacks.',
  'compute.segment_hours': 'A cap on one production segment, in hours, under the partition limit; 0 uses the partition limit.',
  'name': 'The run folder name; empty derives one from the source, the pose and the engine.',
};
const TYPE_HELP = {
  load: 'Import the source into a staging folder and detect what it holds: chains, ligand class, waters, confidence, transmembrane segments.',
  prepare: 'Runs on the short CPU partition: clean, model, protonate, minimize, split receptor and partner. For an entry, complex file or AlphaFold model Submit queues it first anyway; press it to look at the prepared structure before submitting.',
  presets: 'Fill the protocol with a known-good set of values; every field stays editable.',
  restrain: 'Residues held in place during production (click them in the viewer).',
  batch: 'Every pose or peptide ticked here becomes its own run with the settings on this page: same engine, protocol, pH, environment.',
  tmapply: 'Apply the typed segments to the check, the coloring and the orientation.',
};
const GROUP_HELP = {
  system: 'What is simulated: where it comes from, which chains, the ligand, the pH, water box or bilayer.',
  prep: 'How the imported structure is made simulation-ready, as a job on the cluster.',
  forcefield: 'The MD program and the force fields the topology is built with.',
  box: 'The solvent around the solute and the ions in it.',
  protocol: 'How long, how many repeats, how often frames are saved, at what temperature and pressure.',
  analysis: 'What is computed from the trajectory besides the standard read-back: the MM-GBSA binding energy and the frames it uses.',
  compute: 'Where and how the jobs run: GPU or CPU node, partition, time limits.',
};
const KIND_HELP = {
  files: 'Your receptor file plus, if any, your ligand or partner file. The ligand class is read from the ligand file.',
  complex: 'One file holding receptor and partner chains (pdb, cif, mae). You name which chains are which; Prepare splits them.',
  rcsb: 'Fetched from the RCSB by code, with its title and resolution; Prepare cleans it. An OPM orientation or a UniProt transmembrane annotation can come with it.',
  alphafold: 'An AlphaFold result: the folder AlphaFold 3 writes, a zip of it, or one model file (AlphaFold DB, AlphaFold 2, ColabFold): the model of your choice, with ipTM, pTM and pLDDT read; disordered tails can be trimmed.',
  poseviewer: 'A Maestro poseviewer file: the receptor first, then the poses, read here without Schrödinger.',
};
/* Anything the page can explain, found by one key: a field's path, a card's key, a kind of
   source, or one of the page's own pieces. */
const EXTRA_HELP = {
  plan: 'What the queue will be asked for, stage by stage, with the time each one took on this cluster in finished runs of the same size (timings.tsv) and the production time from the ns/day estimate. A stage with no finished job of its kind here yet shows a dash rather than a guess. Show the job prints the stages, the partition and the walltime that Submit will ask for.',
  choose: 'Opens the structure browser: this computer, which uploads the file; your scratch and home; this tool\'s run folder; and the run folders of the lab\'s other tools. A file dropped on the card does the same thing.',
  where: 'The run folder is made under this server\'s runs root, which is printed under the name. The name is filled in from the structure until you type one.',
  checks: 'What was read off the structure as it was imported: the chains and their kinds, the ligand, the waters and ions kept, the disulfides, and the size of the solute. None of it is pressable; it is what the run will be built from. Anything that would stop the run or change what it computes is written underneath as a warning, not as one of these.',
  lid: 'The partner or ligand runs along the middle and each receptor residue sits over the one it is closest to. A line is colored by what the contact is; hover it for the distance. The kinds ticked above the viewer filter this diagram too.',
};
function HELPTEXT(key) {
  return EXTRA_HELP[key] || HELP[key] || GROUP_HELP[key] || TYPE_HELP[key] || KIND_HELP[key] || '';
}
/* The ? beside a card's title and beside a setting: a button that opens the reason in a dialog.
   The sentences that used to sit in gray boxes on the form are all still here, behind these. */
const helpBtn = (key, what) => key && HELPTEXT(key) ? '<button class="q" type="button" data-helpkey="' + esc(key) + '" aria-label="About ' + esc(what || 'this setting') + '">?</button>' : '';
/* A label: at most four words, never cut, and the ? beside it. */
function labelHtml(text, help) { return '<span class="fname">' + esc(String(text || '')) + '</span>' + helpBtn(help, text); }

/* The form, step by step. Each group is a card in the main column, except the last two, which
   are the rail. A label is at most four words; what a setting is for is behind its ?, never in a
   paragraph beside it. `more: true` puts a setting under the card's "More …" line: it is still a
   setting, just not one of the first things anybody needs. */
const GROUPS = [
  {key: 'system', title: 'Structure', need: () => !!STAGING || !!PICKED, fields: [
    {path: 'system.source.kind', label: 'Read it as', type: 'select', options: () => Object.keys(KINDS), text: k => KINDS[k]},
    {path: 'system.source.pdb_id', label: 'PDB id', type: 'text', when: s => s.system.source.kind === 'rcsb'},
    {path: 'system.source.assembly', label: 'Assembly', type: 'text', when: s => s.system.source.kind === 'rcsb', more: true},
    {path: 'system.source.ligand_path', label: 'Partner file', type: 'path', when: s => s.system.source.kind === 'files', wide: true},
    {path: 'system.source.pose', label: s => s.system.source.kind === 'alphafold' ? 'Model' : 'Pose', type: 'number', when: s => ['poseviewer', 'alphafold'].includes(s.system.source.kind)},
    {type: 'batch', when: s => ['poseviewer'].includes(s.system.source.kind), wide: true},
    {path: 'system.receptor_chains', label: 'Receptor chains', type: 'list', mono: true, when: s => COMPLEX(s.system.source.kind)},
    {path: 'system.partner_chains', label: 'Partner chains', type: 'list', mono: true, when: s => COMPLEX(s.system.source.kind)},
    {path: 'system.source.ligand', label: 'Bound ligand', type: 'select', mono: true, text: ligandText, wide: true,
     options: () => [''].concat((SPEC.system.ligands_found || []).filter(l => !l.refused).map(l => l.key)),
     when: s => COMPLEX(s.system.source.kind) && (s.system.ligands_found || []).some(l => !l.refused)},
    {path: 'system.chains', label: 'Chains kept', type: 'list', mono: true, when: s => !COMPLEX(s.system.source.kind)},
    {path: 'system.ph', label: 'pH', type: 'number', step: 0.1},
    {path: 'system.ligand_class', label: 'Partner', type: 'detected', text: lab},
    {path: 'system.ligand_charge', label: s => s.system.ligand_formal_charge != null ? 'Net charge, from the file' : 'Net charge', type: 'number',
     readonly: s => s.system.ligand_formal_charge != null, when: s => s.system.ligand_class === 'small_molecule'},
    {path: 'system.ligand_charge_confirmed', label: 'Charge is right', type: 'check', when: s => s.system.ligand_class === 'small_molecule'},
    {path: 'system.environment', label: 'Environment', type: 'select', options: () => CHOICES.environment || ['water', 'membrane'], text: lab},
    {type: 'load', wide: true},
    {path: 'system.membrane.lipid', label: 'Lipid', type: 'select', options: () => CHOICES.lipid || LIPIDS, when: MEMBRANE, mono: true},
    {path: 'system.membrane.padding_A', label: 'Water padding (\u00c5)', type: 'number', step: 1, when: MEMBRANE},
    {path: 'system.membrane.orientation', label: 'Orientation', type: 'select', options: () => CHOICES.orientation || ['as_imported', 'opm', 'annotation'],
     text: v => ({as_imported: 'As imported', opm: 'Fetch the OPM orientation', annotation: 'From the annotation'}[v] || lab(v)), when: MEMBRANE},
    {path: 'system.keep_waters', label: 'Crystal waters', type: 'select', options: () => ['none', 'near_ligand', 'all'], text: v => ({near_ligand: 'Near the partner', none: 'None', all: 'All'})[v] || v, when: s => !COMPLEX(s.system.source.kind), more: true},
    {path: 'system.keep_metals', label: 'Keep metal ions', type: 'check', when: s => !COMPLEX(s.system.source.kind), more: true},
    {path: 'system.membrane.center_z_A', label: 'Bilayer center z (\u00c5)', type: 'number', step: 1, when: MEMBRANE, more: true},
    {path: 'system.membrane.segments', label: 'Typed segments', type: 'text', mono: true, when: MEMBRANE, more: true, wide: true},
    {type: 'tmapply', when: s => MEMBRANE(s) && !!(s.membrane && s.membrane.segments || '').trim(), more: true, wide: true},
    {type: 'note', when: MEMBRANE, text: () => 'Check in the viewer that the bilayer normal is z.'},
  ]},
  {key: 'prep', title: 'Prepare', when: s => COMPLEX(s.system.source.kind), fields: [
    {path: 'system.prep.protonation', label: 'Protonation', type: 'select', options: () => CHOICES.protonation || ['propka', 'pdbfixer', 'none'], text: lab},
    {path: 'system.prep.model_loops_max', label: 'Model loops up to', type: 'number', step: 1},
    {path: 'system.prep.minimise', label: 'Minimize afterwards', type: 'check'},
    {path: 'system.prep.drop_waters', label: 'Waters', type: 'select', options: () => ['all', 'far', 'none'], text: v => ({none: 'Keep all', all: 'Drop all', far: 'Drop the far ones'})[v] || lab(v)},
    {path: 'system.prep.drop_buffers', label: 'Drop additives', type: 'check', more: true},
    {path: 'system.prep.keep_cofactors', label: 'Keep cofactors', type: 'check', more: true},
    {path: 'system.prep.keep_other_hetero', label: 'Keep other ligands', type: 'check', more: true},
    {path: 'system.prep.drop_hetero', label: 'Also drop', type: 'list', mono: true, more: true},
    {path: 'system.prep.altloc', label: 'Alternate location', type: 'text', mono: true, more: true},
    {path: 'system.prep.cap_breaks', label: 'Cap open gaps', type: 'check', more: true},
    {path: 'system.prep.trim_plddt', label: 'Trim below pLDDT', type: 'number', step: 5, when: s => s.system.confidence && s.system.confidence.plddt, more: true},
    {path: 'system.prep.minimise_steps', label: 'Minimization steps', type: 'number', step: 100, more: true},
    {type: 'prepare', wide: true},
  ]},
  {key: 'forcefield', title: 'Engine and force field', fields: [
    {path: 'forcefield.engine', label: 'Engine', type: 'select', options: () => CHOICES.engine || [], text: k => ((SITE && SITE.engines[k]) || k) + (SITE && SITE.engines_ready && !SITE.engines_ready.includes(k) ? ' · not written yet' : '')},
    {path: 'forcefield.protein', label: 'Protein', type: 'select', options: () => CHOICES.protein || [], mono: true,
     text: v => { const n = ffNote(SPEC.forcefield.engine, v); return n ? v + ' · ' + n[0] : v; }},
    {path: 'forcefield.water', label: 'Water', type: 'select', options: () => CHOICES.water || [], mono: true},
    {path: 'forcefield.nucleic', label: 'Nucleic acid', type: 'select', options: () => CHOICES.nucleic || [], when: s => hasNucleic(s) && (CHOICES.nucleic || []).length > 0, mono: true},
    {path: 'forcefield.ligand', label: 'Ligand parameters', type: 'select', options: () => CHOICES.ligand || [], text: lab, when: s => s.system.ligand_class === 'small_molecule' && (CHOICES.ligand || []).length > 0},
    {type: 'warn', key: 'engine_note', when: s => SITE && SITE.engines_ready && !SITE.engines_ready.includes(s.forcefield.engine), text: () => 'This engine has no renderer yet, so Submit stays off.'},
    {type: 'warn', key: 'ff_note', when: s => !!ffNote(s.forcefield.engine, s.forcefield.protein), text: s => ffNote(s.forcefield.engine, s.forcefield.protein)[1]},
    {type: 'warn', key: 'desmond_note', when: s => s.forcefield.engine === 'desmond', text: () => 'Desmond needs a license of its own: an account not on the cluster\'s Desmond list stops at the build.'},
  ]},
  {key: 'box', title: 'Solvent and ions', fields: [
    {path: 'box.shape', label: 'Box', type: 'select', options: () => CHOICES.box || [], text: lab},
    {path: 'box.padding_A', label: 'Padding (\u00c5)', type: 'number', step: 0.5, when: s => !MEMBRANE(s)},
    {path: 'box.salt', label: 'Salt', type: 'select', options: () => CHOICES.salt || [], mono: true},
    {path: 'box.salt_mM', label: 'Concentration (mM)', type: 'number', step: 10},
  ]},
  {key: 'protocol', title: 'Protocol', fields: [
    {type: 'presets', wide: true},
    {path: 'protocol.length_ns', label: 'Length (ns)', type: 'number', step: 1},
    {path: 'protocol.replicates', label: 'Replicates', type: 'number', step: 1},
    {path: 'protocol.timestep_fs', label: 'Timestep (fs)', type: 'select', options: () => CHOICES.timestep_fs || ['2', '4'], numeric: true},
    {path: 'protocol.save_ps', label: 'Save every (ps)', type: 'number', step: 5},
    {path: 'protocol.temperature_K', label: 'Temperature (K)', type: 'number', step: 1},
    {path: 'protocol.pressure_bar', label: 'Pressure (bar)', type: 'number', step: 0.1, more: true},
    {path: 'protocol.seed', label: 'Seed', type: 'number', step: 1, more: true},
    {type: 'restrain', wide: true},
  ]},
  {key: 'analysis', title: 'Analysis', fields: [
    {path: 'analysis.mmgbsa', label: 'MM-GBSA binding energy', type: 'check', when: s => s.system.ligand_class !== 'none' && !LINKS(s).length, wide: true},
    {path: 'analysis.mmgbsa_from_ns', label: 'From (ns)', type: 'number', step: 1, when: s => MMGBSA_ON(s)},
    {path: 'analysis.mmgbsa_to_ns', label: 'To (ns)', type: 'number', step: 1, when: s => MMGBSA_ON(s)},
    {path: 'analysis.mmgbsa_stride_ps', label: 'One frame every (ps)', type: 'number', step: 10, when: s => MMGBSA_ON(s)},
    {type: 'warn', key: 'mmgbsa_linked', when: s => s.system.ligand_class !== 'none' && LINKS(s).length, text: s => `No MM-GBSA: the disulfide${LINKS(s).length === 1 ? '' : 's'} ${LINKS(s).join(', ')} make${LINKS(s).length === 1 ? 's' : ''} the run one molecule.`},
    {type: 'note', when: s => MMGBSA_ON(s), text: s => mmgbsaFramesText(s)},
    {type: 'note', when: s => s.system.ligand_class === 'none', text: () => 'No partner: RMSD, RMSF, Rg and contacts are computed.'},
  ]},
  {key: 'compute', title: 'Resources', where: 'rail', fields: [
    {path: 'compute.device', label: 'Run on', type: 'select', options: () => CHOICES.device || ['gpu', 'cpu'], text: lab},
    {path: 'compute.gpu', label: 'GPU', type: 'select', options: () => ['', 'auto'].concat((CHOICES.gpu || []).filter(g => g !== 'auto')),
     text: v => v === '' ? 'Default (' + (SITE ? SITE.default_gpu : '') + ')' : (v === 'auto' ? 'Fastest free now' : v), when: s => s.compute.device !== 'cpu', mono: true},
    {path: 'compute.walltime', label: 'Walltime', type: 'text'},
    /* Only once somebody has typed a walltime of their own: until then the field follows the
       estimate and there is nothing to put back. It sat beside Submit as "Estimate", a noun
       with no object, which nobody could read as an action on the box above it. */
    {type: 'wallback', when: () => !WALL_AUTO},
    {path: 'compute.partition', label: 'Partition', type: 'select', options: () => [''].concat(CHOICES.partition || []), text: v => v || 'Default', mono: true, more: true},
    {path: 'compute.gpus_per_replicate', label: 'GPUs per replicate', type: 'number', step: 1, when: s => s.compute.device !== 'cpu', more: true},
    {path: 'compute.max_concurrent', label: 'Max at once', type: 'number', step: 1, more: true},
    {path: 'compute.segment_hours', label: 'Segment cap (h)', type: 'number', step: 1, more: true},
    {path: 'compute.tidy', label: 'Pack small files', type: 'check', more: true, wide: true},
  ]},
  {key: 'where', title: 'Where it goes', where: 'rail', fields: [
    {path: 'name', label: 'Run name', type: 'text', wide: true},
  ]},
];
/* the disulfides that join the receptor to the partner (system.split_disulfides): the build bonds them and MM-GBSA is not run */
const LINKS = s => ((s.system || {}).split_disulfides || []).map(d => `${d.receptor}–${d.partner}`);
const MMGBSA_ON = s => s.system.ligand_class !== 'none' && !!s.analysis.mmgbsa && !LINKS(s).length;
/* what a force-field choice needs beyond the engine's module (OPLS4 on Desmond: the OPLS license), as the
   engine says it: [the words its label carries, the note], or null */
const ffNote = (engine, ff) => ((((SITE && SITE.forcefield_notes) || {})[engine] || {})[ff]) || null;
function mmgbsaWindow(s) {
  const p = s.protocol, a = s.analysis;
  const a0 = Math.max(0, Number(a.mmgbsa_from_ns) || 0), a1 = Math.min(Number(a.mmgbsa_to_ns) || p.length_ns, p.length_ns);
  const st = Math.max(Number(a.mmgbsa_stride_ps) || 200, p.save_ps);
  return {a0, a1, st, frames: Math.max(0, Math.floor((a1 - a0) * 1000 / st))};
}
function mmgbsaFramesText(s) { const w = mmgbsaWindow(s); return `${w.frames} frames per replicate (${w.a0}–${w.a1} ns)`; }

/* ------------------------------------------------------------------ the form */
const fieldKey = f => f.path || ('_' + f.type + (f.key ? '-' + f.key : ''));
const cardOf = key => $('step-' + key);
/* A group whose fields are built into the rail rather than into a card. */
const railGroup = g => g.where === 'rail';

function drawSteps() {
  const main = GROUPS.filter(g => !railGroup(g));
  $('steps').innerHTML = main.map((g, i) => '<li data-key="' + esc(g.key) + '">'
    + (i ? '<span class="sep"></span>' : '')
    + '<button class="st" type="button" data-goto="' + esc(g.key) + '"><span class="n">' + (i + 1)
    + '</span><b>' + esc(g.title) + '</b></button></li>').join('');
  $('steps').querySelectorAll('[data-goto]').forEach(b => b.onclick = () => goToCard(b.dataset.goto));
}
function goToCard(key) {
  const card = cardOf(key);
  if (!card || card.hidden) return;
  card.scrollIntoView({block: 'start', behavior: 'smooth'});
  card.classList.add('lit');
  setTimeout(() => card.classList.remove('lit'), 1400);
}

function renderForm() {
  for (const g of GROUPS) {
    const main = $('body-' + g.key), more = $('xbody-' + g.key);
    if (!main) continue;
    main.innerHTML = '';
    if (more) more.innerHTML = '';
    for (const f of g.fields) {
      if (f.type === 'note' || f.type === 'warn') continue;      // the card's one note line, and its warnings: paint() places them
      const wrap = document.createElement('div');
      wrap.dataset.path = fieldKey(f);
      wrap.className = 'fld' + (f.wide ? ' wide' : '');
      (f.more && more ? more : main).appendChild(wrap);
      buildField(wrap, f);
    }
  }
  drawSteps();
}

function buildField(wrap, f) {
  if (f.type === 'load') {
    wrap.innerHTML = '<button class="btn sm quiet" id="load" type="button" title="Read the source again with the settings above">Read it again</button><p class="why" id="loadnote"></p>';
    wrap.querySelector('#load').onclick = doImport; return;
  }
  if (f.type === 'batch') {
    wrap.innerHTML = '<label class="f">' + labelHtml('One run per pose', 'batch') + '</label>'
      + '<div class="row"><button class="btn sm" id="batchlist" type="button">Read the source again</button></div>'
      + '<div id="batchbody" class="why">Choose the run folder above and what it holds is listed here.</div>';
    wrap.querySelector('#batchlist').onclick = listBatch;
    if (SPEC.system.source.path) setTimeout(listBatch, 0);
    return;
  }
  if (f.type === 'presets') {
    wrap.innerHTML = '<label class="f">' + labelHtml('Preset', 'presets') + '</label><div class="presets">'
      + ['quick', 'standard', 'long', 'cpu_check'].map(k => '<button class="pill" type="button" data-preset="' + k + '">' + esc(lab(k)) + '</button>').join('') + '</div>';
    wrap.querySelectorAll('[data-preset]').forEach(b => b.onclick = async () => {
      const r = await api('/api/preset', {spec: SPEC, preset: b.dataset.preset}); absorb(r);
    }); return;
  }
  if (f.type === 'wallback') {
    wrap.innerHTML = '<button class="btn sm quiet" id="estimate" type="button">Use the estimate</button>';
    wrap.querySelector('#estimate').onclick = () => {
      WALL_AUTO = true;
      const w = DERIVED.walltime_estimate || DERIVED.walltime;
      if (w) { SPEC.compute.walltime = w; refresh(); } else { paint(); }
    };
    return;
  }
  /* The button stands at the left margin at its own width (.fld>.btn): a primary button
     stretched across the card read as the card's own banner, not as the thing to press. */
  if (f.type === 'prepare') {
    wrap.innerHTML = '<button class="btn pri" id="prepare" type="button">Prepare</button><p class="why" id="prepnote"></p>';
    wrap.querySelector('#prepare').onclick = doPrepare; return;
  }
  if (f.type === 'tmapply') {
    wrap.innerHTML = '<button class="btn sm" id="tmapply" type="button">Use these segments</button><p class="why" id="tmnote"></p>';
    wrap.querySelector('#tmapply').onclick = () => applyTM(SPEC.system.membrane.segments); return;
  }
  if (f.type === 'restrain') {
    wrap.innerHTML = '<label class="f">' + labelHtml('Restrained in production', 'restrain') + '</label><div class="pchains" id="restrain"></div>'; return;
  }
  const id = 'f-' + f.path.replace(/\./g, '-');
  /* No box anybody types into is monospace: a path, a name and a walltime are the page's own
     type. Monospace is kept for a sequence, a residue list and a log. */
  /* A checkbox is a box with the setting's own words beside it, and nothing else: it was a
     field-shaped box holding the word "Yes", which reads as something to type in. Something
     the page worked out is shown as a value marked "detected", not as a field switched off. */
  let ctl;
  if (f.type === 'check') {
    wrap.innerHTML = '<div class="tickrow"><input type="checkbox" id="' + id + '">'
      + '<label class="tlab" for="' + id + '"><span class="fname"></span></label>'
      + '<span class="qslot"></span></div><div class="errmsg" hidden></div>';
  } else {
    if (f.type === 'select') ctl = '<select class="inp" id="' + id + '"></select>';
    else if (f.type === 'detected') ctl = '<div class="shown" id="' + id + '" data-tag="detected"></div>';
    else if (f.type === 'path') ctl = '<div class="withbtn"><input class="inp" id="' + id + '" type="text" placeholder="None"><button class="btn sm" type="button" data-browse="' + f.path + '">Choose</button></div>';
    else ctl = '<input class="inp" id="' + id + '" type="' + (f.type === 'number' ? 'number' : 'text') + '"' + (f.step ? ' step="' + f.step + '"' : '') + '>';
    wrap.innerHTML = '<label class="f" for="' + id + '"></label>' + ctl + '<div class="errmsg" hidden></div>';
  }
  const el = wrap.querySelector('#' + id);
  if (f.type === 'detected') return;
  const commit = () => {
    let v;
    if (f.type === 'check') v = el.checked;
    else if (f.type === 'number') v = el.value === '' ? null : Number(el.value);
    else if (f.type === 'list') v = el.value.split(/[,\s]+/).filter(Boolean);
    else if (f.type === 'select' && f.numeric) v = Number(el.value);
    else v = el.value;
    const before = get(SPEC, f.path);
    set(SPEC, f.path, v);
    // another structure: the bound ligands listed, and the one chosen, were the last one's
    if (['system.source.kind', 'system.source.path', 'system.source.pdb_id', 'system.source.assembly'].includes(f.path)) { SPEC.system.source.ligand = ''; SPEC.system.ligands_found = []; }
    if (f.path === 'system.source.ligand') { chooseLigand(before); return; }    // the import refreshes when it is back
    if (REIMPORTED.includes(f.path) && STAGING && !COMPLEX(SPEC.system.source.kind)) { reimport(f.path, before); return; }
    if (f.path === 'system.source.kind') { STAGING = null; SPEC.system.prepared = false; SPEC.system.chains_found = []; ADCPLIST = []; renderForm(); }
    if (f.path === 'system.source.path') onSourcePathChanged();
    if (f.path === 'system.ph' && STAGING) drawChecks();          // a bound ligand's acids and bases were set for pH 7.4
    if (f.path === 'name') NAME_AUTO = !String(v).trim();
    if (f.path === 'compute.walltime') WALL_AUTO = !String(v || '').trim();   // an emptied field goes back to following the estimate
    if (f.path === 'system.environment' || f.path.startsWith('system.membrane.')) styleStructure(false);   // the bilayer slab follows the fields
    if (f.path === 'protocol.length_ns' || f.path === 'protocol.replicates' || f.path === 'protocol.timestep_fs' || f.path === 'protocol.save_ps') SPEC.protocol.preset = 'custom';
    schedule();
  };
  el.addEventListener(f.type === 'select' || f.type === 'check' ? 'change' : 'input', commit);
  const b = wrap.querySelector('[data-browse]');
  if (b) b.onclick = () => pickInto(f.path);
}
const fmtDur = sec => sec >= 3600 ? (sec / 3600).toFixed(1) + ' h' : Math.max(1, Math.round(sec / 60)) + ' min';
/* what to expect, stage by stage: the build, the ladder, the analysis and MM-GBSA from finished
   jobs on this cluster (timings.tsv), the production from the ns/day estimate */
function stageLine(d) {
  const st = d.stage_seconds, parts = [];
  if (st.build) parts.push(`build ${fmtDur(st.build)}`);
  if (st.equil) parts.push(`equil ${fmtDur(st.equil)}`);
  parts.push(`production ${fmtDur(st.prod)}` + (d.segments > 1 ? ` (${fmtDur(st.prod_segment)} per segment)` : ''));
  if (st.analysis) parts.push(`analysis ${fmtDur(st.analysis)}`);
  if (st.mmgbsa) parts.push(`MM-GBSA ${fmtDur(st.mmgbsa)}`);
  return 'expect: ' + parts.join(' · ') + ` → about ${fmtDur(d.total_seconds)} in all`;
}

let timer = null;
function schedule() { clearTimeout(timer); timer = setTimeout(refresh, 250); }

async function refresh() {
  if (!SPEC) return;
  if (NAME_AUTO) SPEC.name = '';          // the server fills a default from the source
  const r = await api('/api/validate', {spec: SPEC});
  absorb(r);
}

function absorb(r) {
  if (r.error) { $('subnote').textContent = r.error; return; }
  SPEC = r.spec;
  CHOICES = r.choices || {}; ERRORS = r.errors || []; DERIVED = r.derived || {};
  // the walltime fills itself from the estimate (the settings above decide it) until the user types one
  const west = DERIVED.walltime_estimate || DERIVED.walltime;
  if (WALL_AUTO && west && SPEC.compute.walltime !== west) { SPEC.compute.walltime = west; schedule(); }
  paint();
}

/* A step is done when none of its fields is red and what it is for has happened: a structure is
   loaded (Structure), the structure is prepared (Prepare); the others are done when nothing is red */
const LOADED = () => (SPEC.system.chains_found || []).length > 0 || SPEC.system.solute_atoms > 0;
function groupState(g, bad) {
  const owns = f => f.path && Object.keys(bad).some(k => k === f.path || k.startsWith(f.path + '.') || k.split('.').slice(0, 2).join('.') === f.path.split('.').slice(0, 2).join('.'));
  if (g.fields.some(owns)) return 'bad';
  if (g.key === 'system') return LOADED() ? 'done' : 'todo';
  // Nothing is settled before there is a structure: four green ticks on an empty form told
  // somebody who had not touched the page that four of its steps were finished.
  if (!LOADED()) return 'idle';
  if (g.key === 'prep') return SPEC.system.prepared ? 'done' : 'todo';
  if (g.key === 'compute') return SPEC.compute.walltime && (DERIVED.gres || SPEC.compute.device === 'cpu') ? 'done' : 'todo';
  return 'done';
}
const STATE_WORD = {done: 'Ready', bad: 'Check this', todo: 'Needed', idle: ''};

function paint() {
  const bad = {}; for (const e of ERRORS) bad[e.field] = e.message;
  let followed = false, shownSteps = 0;
  for (const g of GROUPS) {
    const shown = !g.when || g.when(SPEC);
    const card = cardOf(g.key);
    if (card) card.hidden = !shown;
    const li = document.querySelector('#steps li[data-key="' + g.key + '"]');
    if (li) li.hidden = !shown;
    if (!shown) continue;
    const st = groupState(g, bad);
    const pill = $('st-' + g.key);
    if (pill) {
      pill.hidden = st === 'idle';
      pill.className = 'cstate ' + (st === 'done' ? 'done' : 'need');
      pill.textContent = STATE_WORD[st];
    }
    const step = document.querySelector('#steps .st[data-goto="' + g.key + '"]');
    if (step) {
      step.classList.toggle('done', st === 'done'); step.classList.toggle('bad', st === 'bad');
      shownSteps += 1;
      const no = step.querySelector('.n');
      if (no && st !== 'done') no.textContent = String(shownSteps);
      if (no && st === 'done') no.textContent = '\u2713';
    }
    for (const f of g.fields) {
      if (f.type === 'note' || f.type === 'warn') continue;
      const wrap = document.querySelector('.fld[data-path="' + fieldKey(f) + '"]');
      if (!wrap) continue;
      const show = (!g.need || g.need()) && (!f.when || f.when(SPEC));
      wrap.hidden = !show;
      if (!show || !f.path) continue;
      const lt = typeof f.label === 'function' ? f.label(SPEC) : f.label;
      const label = wrap.querySelector(f.type === 'check' ? '.tlab' : 'label.f');
      if (label && f.type === 'check') {
        wrap.querySelector('.fname').textContent = String(lt || '');
        wrap.querySelector('.qslot').innerHTML = helpBtn(f.path, lt);
      } else if (label) label.innerHTML = labelHtml(lt, f.path);
      const el = wrap.querySelector('#f-' + f.path.replace(/\./g, '-'));
      if (f.readonly) { const ro = !!f.readonly(SPEC); el.readOnly = ro; el.classList.toggle('off', ro); }   // shown, not offered as an edit the checks would refuse
      const v = get(SPEC, f.path);
      if (f.type === 'select') {
        const opts = f.options(); const cur = v == null ? '' : String(v);
        if (el.dataset.opts !== opts.join('|')) {
          el.innerHTML = opts.map(o => '<option value="' + esc(o) + '">' + esc(f.text ? f.text(o) : o) + '</option>').join('');
          el.dataset.opts = opts.join('|');
        }
        if (opts.includes(cur)) el.value = cur;
        else if (opts.length) {          // the list filtered the value out (engine → force field → water): the value follows the list
          el.value = opts[0]; set(SPEC, f.path, f.numeric ? Number(opts[0]) : opts[0]); followed = true;
          // always re-validate, an unset value too (forcefield.ligand null after a small-molecule import
          // would keep its stale error otherwise); the server echoes the value, so the next paint finds it
        } else el.value = '';
        if (opts.length === 1 && f.path === 'protocol.timestep_fs') el.title = 'Fixed by the engine';
        // the whole of what is chosen, for a name longer than the box it is shown in
        else el.title = el.selectedOptions && el.selectedOptions[0] ? el.selectedOptions[0].textContent : '';
      } else if (f.type === 'check') el.checked = !!v;
      else if (f.type === 'detected') el.textContent = f.text ? f.text(v) : v;
      else if (f.type === 'list') { if (document.activeElement !== el) el.value = (v || []).join(', '); }
      else if (document.activeElement !== el) el.value = v == null ? '' : v;
      const msg = wrap.querySelector('.errmsg');
      const isBad = !!bad[f.path];
      el.classList.toggle('bad', isBad);
      if (f.type === 'check') wrap.querySelector('.tickrow').classList.toggle('bad', isBad);
      else if (label) label.classList.toggle('bad', isBad);
      if (msg) { msg.hidden = !isBad; msg.textContent = bad[f.path] || ''; }
    }
    // The one note a card is allowed, and the warnings, which are not notes: a warning says the
    // run will stop or will compute something else, and belongs where it can be seen.
    const note = g.fields.find(f => f.type === 'note' && (!f.when || f.when(SPEC)));
    const nEl = $('note-' + g.key);
    if (nEl) { nEl.hidden = !note; if (note) nEl.textContent = note.text(SPEC); }
    const wEl = $('warn-' + g.key);
    if (wEl) wEl.innerHTML = g.fields.filter(f => f.type === 'warn' && (!f.when || f.when(SPEC)))
      .map(f => '<p class="cardwarn">' + esc(f.text(SPEC)) + '</p>').join('');
    const more = $('more-' + g.key), xbody = $('xbody-' + g.key);
    if (more && xbody) more.hidden = ![...xbody.children].some(c => !c.hidden);
  }
  // restraint chips
  const rs = $('restrain');
  if (rs) {
    rs.innerHTML = SPEC.protocol.restrain.length
      ? SPEC.protocol.restrain.map(k => '<span class="chip" data-k="' + esc(k) + '">' + esc(k) + ' &#10005;</span>').join('')
      : '<span class="why">None</span>';
    rs.querySelectorAll('.chip').forEach(c => c.onclick = () => toggleRestrain(c.dataset.k));
  }
  drawPicked();
  drawPlan();
  drawFolder();
  $('submit').disabled = ERRORS.length > 0 || !STAGING;
  const needsPrep = COMPLEX(SPEC.system.source.kind) && !SPEC.system.prepared;
  const lic = ffNote(SPEC.forcefield.engine, SPEC.forcefield.protein);   // said again where Submit is pressed
  const why = $('subnote');
  if (ERRORS.length) { why.className = 'gowhy'; why.textContent = ERRORS[0].message + (ERRORS.length > 1 ? ' (' + (ERRORS.length - 1) + ' more above)' : ''); }
  else if (!STAGING) { why.className = 'gowhy'; why.textContent = 'Choose a structure first.'; }
  else if (lic) { why.className = 'gowhy'; why.textContent = lic[1]; }
  else if (needsPrep) { why.className = 'gowhy ok'; why.textContent = 'Submit queues Prepare first, then the build.'; }
  else if (SITE && SITE.dry_run) { why.className = 'gowhy ok'; why.textContent = 'Dry run: the files are written, nothing is queued.'; }
  else { why.className = 'gowhy ok'; why.textContent = 'Ready to submit.'; }
  if (followed) schedule();             // re-validate with the values the lists chose
}

/* Where this run's folder will be: under the box it is named in, whole, and the only copy on
   the page. The same path used to be on screen three times - in the header, in the rail and in
   the plan - and the two in the rail were both cut, which is the one thing the rail is for. */
function drawFolder() {
  const root = (SITE && SITE.runs_root) || '';
  const el = $('planfolder');
  if (!el) return;
  const name = String(SPEC.name || '').trim();
  el.innerHTML = root
    ? 'This run goes in <b>' + esc(root + '/' + (name || 'a name taken from the structure')) + '</b>'
    : '';
}

/* What was chosen, in the Structure card: its name, where it came from, and its chains as chips
   with their kind and their count. */
function drawPicked() {
  const chains = (SPEC.system.chains_found || []);
  const has = !!STAGING || !!PICKED;
  $('picked').hidden = !has;
  $('pickzone').hidden = has;
  $('viewerbox').hidden = !has;
  $('checks').hidden = !has;
  $('checkshead').hidden = !has;
  $('checkwarn').hidden = !has;
  if (!has) return;
  const rec = new Set(SPEC.system.receptor_chains || []), lig = new Set(SPEC.system.partner_chains || []);
  $('pname').textContent = (PICKED && PICKED.name) || structureFileName();
  $('ppath').textContent = (PICKED && PICKED.where) || SPEC.system.source.path || '';
  $('ppath').title = $('ppath').textContent;
  // The chip drawn in the partner's color says so: a second color with nothing to read it by
  // looks like a warning about the chain.
  const role = id => rec.has(id) ? 'receptor' : lig.has(id) ? 'partner' : '';
  $('pchains').innerHTML = chains.length
    ? chains.map(c => '<span class="pchain' + (rec.has(c.id) ? ' rec' : lig.has(c.id) ? ' lig' : '') + '">'
        + esc(c.id) + ' <span class="pk">' + esc(c.kind) + ', ' + c.residues + '</span>'
        + (role(c.id) ? '<span class="pr">' + role(c.id) + '</span>' : '') + '</span>').join('')
    : '';
}
const structureFileName = () => SPEC.system.source.peptide || SPEC.system.source.pdb_id
  || (SPEC.system.source.path || '').split(/[\\/]/).filter(Boolean).pop() || 'structure';

/* ------------------------------------------------------------------ the run plan
   What will happen, in numbered steps with a time and a place, in the rail above Submit. It
   appears only once there is something to run. */
function planSteps(d) {
  const cpu = (SITE && SITE.cpu_partition) || 'the CPU partition';
  const where = d.device === 'cpu' ? `${d.partition}, ${d.cores} cores` : `${d.partition}, ${d.gres || 'a GPU'}`;
  const st = d.stage_seconds || {};
  const out = [];
  if (COMPLEX(SPEC.system.source.kind) && !SPEC.system.prepared)
    out.push(['Prepare', 'Clean, model, protonate, split · ' + ((SITE && SITE.prep_partition) || cpu),
              st.prepare ? fmtDur(st.prepare) : '']);
  out.push(['Build', 'Topology, box, water, ions · ' + cpu, st.build ? fmtDur(st.build) : '']);
  out.push(['Equilibrate', 'Minimize, heat, hold · ' + where, st.equil ? fmtDur(st.equil) : '']);
  out.push(['Production', `${SPEC.protocol.length_ns} ns × ${SPEC.protocol.replicates} · ` + where
    + (d.ns_per_day ? ` · ${d.ns_per_day} ns/day` : '') + (d.segments > 1 ? ` · ${d.segments} segments` : '')
    + (d.gpu_note ? ` · ${d.gpu_note}` : ''), st.prod ? fmtDur(st.prod) : '']);
  out.push(['Analysis', 'RMSD, RMSF, Rg, contacts · ' + cpu, st.analysis ? fmtDur(st.analysis) : '']);
  if (st.mmgbsa) out.push(['MM-GBSA', 'Binding energy over frames · ' + cpu, fmtDur(st.mmgbsa)]);
  if (SPEC.compute.tidy) out.push(['Tidy', 'Pack the small files · ' + cpu, st.tidy ? fmtDur(st.tidy) : '']);
  return out;
}
function drawPlan() {
  const d = DERIVED;
  const ready = !!(d && d.atoms && STAGING);
  $('plan').hidden = !ready;
  if (!ready) return;
  $('phases').innerHTML = planSteps(d).map((p, i) =>
    '<li><span class="pn">' + (i + 1) + '</span><span class="pw"><b>' + esc(p[0]) + '</b><span>'
    + esc(p[1]) + '</span></span>' + (p[2] ? '<span class="pt">' + esc(p[2]) + '</span>'
      : '<span class="pt none" title="no finished job of this stage on this cluster to estimate from">\u2014</span>') + '</li>').join('');
  // Two short facts rather than one sixteen-word line: what is built, and what it costs.
  $('planwhere').textContent = `${fmtInt(d.atoms)} atoms · ${d.box}`;
  $('plandisk').textContent = (d.total_seconds ? `about ${fmtDur(d.total_seconds)} in all` : '')
    + (d.total_seconds && d.disk_gb_per_replicate ? ' · ' : '')
    + (d.disk_gb_per_replicate ? `${d.disk_gb_per_replicate} GB a replicate` : '');
  // The quota is only said when it is close or over: a line that always says "there is room" is
  // a line nobody reads when it stops saying so.
  const quota = d.quota && d.quota.level !== 'ok' ? d.quota.text : '';
  $('planquota').hidden = !quota;
  $('planquota').textContent = quota;
  $('planquota').style.color = quota ? 'var(--rust)' : '';
}
const fmtInt = n => (n == null ? '—' : Number(n).toLocaleString('en-US').replace(/,/g, ' '));

/* ------------------------------------------------------------------ import + viewer */
/* ---- one run per pose: a poseviewer's poses or a docking run's peptides, each imported into its own
   staging folder and submitted with the settings on the page (the loaded pose's Submit must be green) */
let BATCH = null, ADCPLIST = [];
function onSourcePathChanged() {
  const k = SPEC.system.source.kind;
  if (k === 'poseviewer' && SPEC.system.source.path && $('batchbody')) listBatch();
}
async function listBatch() {
  const src = SPEC.system.source;
  const box = $('batchbody'); if (!box) return;
  if (!src.path) { box.textContent = 'pick the run folder or poseviewer file above: what it holds is listed here'; return; }
  const d = await api('/api/poses?kind=' + encodeURIComponent(src.kind) + '&path=' + encodeURIComponent(src.path || ''));
  if (d.error) { box.textContent = d.error; return; }
  BATCH = d;
  let html = '';
  if (d.kind === 'poseviewer') {
    html += `<div class="why">receptor: ${esc(d.receptor || 'first structure')} · ${d.poses.length} pose${d.poses.length === 1 ? '' : 's'} <span class="help" title="the first structure of a poseviewer file is the receptor; each one after it is a docked pose, in the file's order">?</span></div>`;
    html += d.poses.map(p => `<label class="ctckind"><input type="checkbox" data-pose="${p.pose}"> pose ${p.pose} <span class="why">${esc(p.title.slice(0, 40))}</span></label>`).join('<br>');
  }
  html += '<div class="btns"><button class="btn sm" id="batchall">tick all</button><button class="btn pri sm" id="batchgo">Submit one run per selection</button></div><pre id="batchout" class="log" hidden></pre>';
  box.innerHTML = html;
  $('batchall').onclick = () => box.querySelectorAll('input[type=checkbox]').forEach(c => { c.checked = true; });
  $('batchgo').onclick = submitBatch;
}
async function submitBatch() {
  const box = $('batchbody'); const sels = [];
  box.querySelectorAll('input[data-pose]:checked').forEach(c => sels.push({pose: +c.dataset.pose}));
  const out = $('batchout'); out.hidden = false;
  if (!sels.length) { out.textContent = 'nothing ticked'; return; }
  if (ERRORS.length) { out.textContent = 'fix the red fields first: the settings on the page are what every run gets'; return; }
  if (!confirmForceField()) { out.textContent = 'not submitted'; return; }
  out.textContent = 'importing and submitting ' + sels.length + ' run' + (sels.length === 1 ? '' : 's') + '…';
  const r = await api('/api/submit_batch', {spec: SPEC, selections: sels});
  out.textContent = r.error ? r.error : ((r.dry_run ? 'DRY RUN — nothing queued\n' : '') + r.log.join('\n') + ((r.notes || []).length ? '\n\nnotes:\n- ' + r.notes.join('\n- ') : ''));
  if (!r.error) loadRuns();
}

/* the transmembrane annotation that arrives later (BLAST in the server's background) or is typed:
   merged into the spec, drawn, and the staged structure re-oriented when the run asked for it */
async function applyTM(typed) {
  if (!STAGING) { $('loadnote').textContent = 'load a structure first'; return; }
  const r = await api('/api/tm_orient', {staging: STAGING, segments: typed || '', tm_annotation: typed ? null : SPEC.system.tm_annotation,
                                          meta: SPEC.system.meta, orient: MEMBRANE(SPEC) && SPEC.system.membrane.orientation === 'annotation'});
  if (r.error) { $('loadnote').textContent = r.error; return; }
  Object.assign(SPEC.system, r.system, {membrane: Object.assign({}, SPEC.system.membrane, r.system.membrane || {})});
  if (r.structure) { STRUCT = r.structure; drawStructure(); }
  drawChecks();
  $('loadnote').textContent = 'segments applied' + (r.note ? ' · ' + r.note : '');
  await refresh();
}
async function pollTM(staged) {
  const t0 = Date.now();
  while (Date.now() - t0 < 5 * 60000) {
    await new Promise(ok => setTimeout(ok, 5000));
    if (STAGING !== staged) return;
    const st = await api('/api/tm_status?staging=' + encodeURIComponent(staged));
    if (st.pending) continue;
    if (st.error) { SPEC.system.tm_annotation = Object.assign({}, SPEC.system.tm_annotation, {pending: false, note: st.error}); drawChecks(); return; }
    SPEC.system.tm_annotation = st.tm_annotation;
    if (MEMBRANE(SPEC) && SPEC.system.membrane.orientation === 'annotation' && st.tm_annotation.membrane) await applyTM('');
    else { drawChecks(); styleStructure(false); }
    return;
  }
}

async function doImport() {
  $('loadnote').textContent = 'Reading\u2026';
  const src = Object.assign({}, SPEC.system.source);   // what is imported is kept with it, whatever a reply that lands meanwhile holds
  const r = await api('/api/import', {source: src, environment: SPEC.system.environment, membrane: SPEC.system.membrane, ph: SPEC.system.ph,   // membrane carries the orientation and any typed segments; the pH, what a bound ligand's note says of it
                                      chains: SPEC.system.chains, keep_waters: SPEC.system.keep_waters, keep_metals: SPEC.system.keep_metals});   // what an import of files applies
  if (r.error) { $('loadnote').textContent = r.error; return false; }
  const prep = SPEC.system.prep, ph = SPEC.system.ph, environment = SPEC.system.environment;
  const membrane = Object.assign({}, SPEC.system.membrane || {}, r.system.membrane || {});   // the import may set the center (OPM: 0)
  Object.assign(SPEC.system, r.system, {prep, ph, source: src, environment, membrane}); STAGING = r.system.staging;
  // What was read is in the chips under the viewer; a bare "Loaded." under a button said
  // nothing that was not already on the card.
  $('loadnote').textContent = r.note || '';
  STRUCT = r.structure;
  drawStructure();
  drawChecks();
  await refresh();
  if (SPEC.system.tm_annotation && SPEC.system.tm_annotation.pending) pollTM(STAGING);
  return true;
}

/* the receptor chains, crystal waters and metal ions an import of files keeps are applied by the import: a change
   imports the files again (typed chains once the typing stops), and one the import refuses goes back to what it was */
const REIMPORTED = ['system.chains', 'system.keep_waters', 'system.keep_metals'];
let REIMPORT = null, REIMPORT_BEFORE = {};
function reimport(path, before) {
  if (!(path in REIMPORT_BEFORE)) REIMPORT_BEFORE[path] = before;
  clearTimeout(REIMPORT);
  REIMPORT = setTimeout(async () => {
    const prev = REIMPORT_BEFORE; REIMPORT_BEFORE = {};
    if (!await doImport()) { for (const [p, v] of Object.entries(prev)) set(SPEC, p, v); paint(); }
  }, path === 'system.chains' ? 700 : 0);
}

/* a bound ligand of the entry as the partner, or none: the structure is imported again with that
   choice; one the server cannot type is refused with the reason, and the previous choice stays */
async function chooseLigand(prev) {
  if (!await doImport()) { SPEC.system.source.ligand = prev || ''; paint(); }
}
function ligandText(v) {
  if (!v) return 'none: the partner chains';
  const l = (SPEC.system.ligands_found || []).find(x => x.key === v);
  return l ? `${l.key} · ${l.heavy_atoms} heavy atoms` : v;
}

/* What was read off the structure, in two kinds that look like two kinds: a fact, which is
   flat and square and does nothing when pressed, and a warning, which is a line of its own
   underneath in amber or rust. A row of identically shaped pills mixing a chain list, a
   warning and two findings gave nobody a way to tell which was which.

   A fact is short. What is behind it - the method, the threshold, the caveat - is on the
   heading's ?, not glued to the end of the chip. */
function chk(words, extra) {
  return `<span class="chk"${extra ? ' title="' + extra + '"' : ''}>${words}</span>`;
}
function drawChecks() {
  const s = SPEC.system, out = [], warn = [];
  const bad = (level, words) => warn.push(`<p class="cardwarn${level === 'w' ? ' soft' : ''}">${words}</p>`);
  const chains = (s.chains_found || []);
  out.push(chk(`chains ${chains.map(c => c.id + ' <b>' + c.kind + ' ' + c.residues + (c.missing ? ' (' + c.missing + ' missing)' : '') + '</b>').join(' · ') || 'none'}`,
               "the chains found in the imported structure: letter, kind, residues resolved (and missing ones the file declares)"));
  for (const c of chains) if ((c.modified || []).length) bad('bad', `Chain ${esc(c.id)} has modified nucleotides (${esc(c.modified.slice(0, 6).join(', '))}${c.modified.length > 6 ? ' …' : ''}). The force fields here have no parameters for them: leave the chain out.`);
  for (const c of chains) if ((c.charged_modified || []).length) bad('bad', `Chain ${esc(c.id)} has modified amino acids (${esc(c.charged_modified.slice(0, 6).join(', '))}${c.charged_modified.length > 6 ? ' …' : ''}). The force fields here have no parameters for them: leave the chain out.`);
  if ((s.breaks || []).length) {
    const capped = s.breaks.filter(b => (s.capped || []).includes(b));
    const how = capped.length === s.breaks.length ? 'capped with ACE and NME' : capped.length ? `${capped.length} capped with ACE and NME, the rest ended with TER` : 'each ended with TER';
    bad('w', `${s.breaks.length} chain break${s.breaks.length === 1 ? '' : 's'} (${esc(s.breaks.slice(0, 4).join(', '))}${s.breaks.length > 4 ? ' …' : ''}), ${how}.`);
  }
  if (COMPLEX(SPEC.system.source.kind)) {
    if (s.meta && s.meta.title) out.push(chk(esc(s.meta.title.slice(0, 48)) + (s.meta.resolution_A ? ' · <b>' + s.meta.resolution_A + ' Å</b>' : '')));
    const c = s.confidence || {};
    if (c.iptm != null && c.iptm < 0.6) bad('w', `ipTM ${c.iptm} (pTM ${c.ptm}): the predicted interface is a doubtful one before a single step is run.`);
    else if (c.iptm != null) out.push(chk(`ipTM <b>${c.iptm}</b> · pTM <b>${c.ptm}</b>`));
    if (c.plddt) out.push(chk(`mean pLDDT <b>${c.plddt.mean}</b>`,
      "B-factors read as pLDDT: " + esc(c.plddt.basis || '')
      + (Object.values(c.plddt.below_50 || {}).some(v => v) ? '. Low tails: ' + Object.entries(c.plddt.below_50).map(([k, v]) => k + ' ' + v).join(', ') : '')));
    const het = (s.hetero_found || []);
    if (het.length) out.push(chk(`hetero <b>${het.map(h => h.resname + ' ' + h.count).join(', ')}</b>`,
      "what each one is: " + esc(het.map(h => h.resname + ' ' + h.class).join(', '))));
    const notOffered = (s.ligands_found || []).filter(l => l.refused);
    if (notOffered.length) out.push(chk(`<b>${notOffered.length}</b> ligand${notOffered.length === 1 ? '' : 's'} not offered`,
      "bound ligands that cannot be run as the partner, and why: " + esc(notOffered.map(l => l.key + ' — ' + l.refused).join('; '))));
    if (s.ligand_class === 'small_molecule') bad(s.ligand_charge == null ? 'bad' : 'w',
      `${esc(s.source.ligand || 'The ligand')} is the partner. Its net charge ${s.ligand_charge == null ? 'is unknown: set it below.' : 'reads as ' + (s.ligand_charge >= 0 ? '+' : '') + s.ligand_charge + (s.meta && s.meta.ligand_protonation ? ' (' + esc(s.meta.ligand_protonation) + ')' : '') + ': confirm it below.'}`
      + (s.source.ligand && Math.abs(Number(s.ph) - 7.4) > 1 + 1e-9 ? ` Its acids and bases were set for pH 7.4, not for this run's pH of ${esc(s.ph)}.` : ''));
    if (s.waters) out.push(chk(`<b>${s.waters}</b> waters`));
    if (s.prepared && s.prep_report && s.prep_report.log) {
      const pl = s.prep_report.placement || {};
      for (const line of s.prep_report.log) {
        const k = line === pl.note ? (pl.clashes ? 'bad' : (pl.min_distance_A > 6 || pl.min_distance_A == null ? 'w' : '')) : (/not|failed|skipped/i.test(line) ? 'w' : '');
        if (k) bad(k, esc(line)); else out.push(chk('<span class="ck">✓</span> ' + esc(line)));
      }
      if (s.prep_report.clashes) out.push(chk(`clashes <b>${s.prep_report.clashes.before} → ${s.prep_report.clashes.after}</b>`));
    } else bad('w', 'Not prepared yet: set the options below and press Prepare.');
  } else {
    out.push(chk(`partner <b>${esc(lab(s.ligand_class))}</b>`,
      s.ligand_class === 'peptide' ? 'the force field covers it, nothing to parameterize' : ''));
    if ((s.waters_kept || []).length) out.push(chk(`<b>${s.waters_kept.length}</b> crystal water${s.waters_kept.length === 1 ? '' : 's'} kept`,
      "the build adds them after the solute as its water model"));
    if ((s.ions_kept || []).length) out.push(chk(`ions kept <b>${esc(s.ions_kept.map(i => i.group).join(', '))}</b>`,
      "the ions the run keeps, as free ions where the files have them"));
    if (s.ligand_class === 'small_molecule') bad(s.ligand_charge == null ? 'bad' : 'w',
      `The partner's net charge ${s.ligand_charge == null ? 'is unknown: set it below.' : 'reads as ' + (s.ligand_charge >= 0 ? '+' : '') + s.ligand_charge + ': confirm it below.'}`);
  }
  const tm = s.tm_annotation || {};
  if (tm.summary) {
    const wrong = tm.membrane && !MEMBRANE(SPEC);
    const none = !tm.membrane && MEMBRANE(SPEC) && (tm.method === 'annotation' || tm.method === 'predicted');
    // The chip says what was found. How it was found, and what it does not mean, is the ?'s
    // job: nineteen words in a chip is a sentence nobody reads standing up.
    const how = [tm.summary, tm.note || '',
                 tm.pending ? 'Swiss-Prot is being searched with BLAST for the sequence; about a minute.' : ''].filter(Boolean).join(' · ');
    TM_WHY = how;
    const short = tm.membrane ? 'membrane protein' : 'no membrane span';
    if (wrong) bad('w', `This is a membrane protein (${esc(tm.summary)}). Choose the lipid bilayer environment below.`);
    else if (none) bad('w', `No transmembrane segments were found, and a bilayer is being set up all the same. Check it in the viewer.`);
    else out.push(chk(`<b>${short}</b>${tm.pending ? ' · looking it up…' : ''}`, esc(how)));
  }
  if (MEMBRANE(SPEC) && s.meta && s.meta.orientation === 'annotation') out.push(chk('<span class="ck">✓</span> oriented on its segments', "their axis is z, their center z = 0"));
  else if (MEMBRANE(SPEC) && s.meta && s.meta.opm_half_thickness_A != null) out.push(chk(`<span class="ck">✓</span> OPM orientation`, `membrane planes at z = ±${s.meta.opm_half_thickness_A} Å`));
  else if (MEMBRANE(SPEC)) bad('w', 'The bilayer is along z as imported. Check in the viewer that the membrane normal really is z.');
  out.push(chk(`<b>${(s.disulfides || []).length}</b> disulfide${(s.disulfides || []).length === 1 ? '' : 's'}`,
    "cysteine pairs close enough to be bonded; the build makes the bonds"));
  const links = LINKS({system: s});
  if (links.length) bad('w', `Receptor and partner are covalently linked (${esc(links.join(', '))}). The build bonds them, the run is one molecule, and MM-GBSA is not run.`);
  out.push(chk(`<b>${fmtInt(s.solute_atoms)}</b> solute atoms`, "what the water box and the run time are estimated from"));
  out.push(chk(`extent <b>${(s.extent_A || []).map(x => Math.round(x)).join(' × ')} Å</b>`, "the solute's bounding box"));
  $('checks').innerHTML = out.join('');
  $('checkwarn').innerHTML = warn.join('');
  $('checkshead').hidden = !out.length;
}
//: how the transmembrane segments were found, for the heading's ?
let TM_WHY = '';

async function doPrepare() {
  if (!STAGING) { $('prepnote').textContent = 'load a structure first'; return; }
  $('prepnote').textContent = 'preparing… (loops, protonation, minimization; up to a minute)';
  $('prepare').disabled = true;
  const staged = STAGING;
  let r = await api('/api/prepare', {spec: SPEC, staging: STAGING});
  if (r.queued) {
    // on the cluster Prepare is a job on the short CPU partition: nothing heavy runs on a login node
    const t0 = Date.now(); let state = 'queued';
    while (true) {
      $('prepnote').textContent = `Prepare is job ${r.job} on ${r.partition} · ${state} · ${Math.round((Date.now() - t0) / 1000)} s`;
      await new Promise(ok => setTimeout(ok, 3000));
      if (STAGING !== staged) { $('prepare').disabled = false; return; }     // the person imported something else meanwhile
      const st = await api('/api/prepare_status?staging=' + encodeURIComponent(staged));
      if (st.state === 'done' || st.state === 'failed' || st.error) { r = st; break; }
      state = st.state || state;
    }
  }
  $('prepare').disabled = false;
  if (r.error) { $('prepnote').textContent = r.error + (r.log ? ' — see the job log' : ''); if (r.log) { const o = $('subout'); o.hidden = false; o.textContent = r.log; } return; }
  const keep = {source: SPEC.system.source, prep: SPEC.system.prep, ph: SPEC.system.ph, chains_found: SPEC.system.chains_found, hetero_found: SPEC.system.hetero_found, gaps_found: SPEC.system.gaps_found, confidence: SPEC.system.confidence, meta: SPEC.system.meta, waters: SPEC.system.waters, tm_annotation: SPEC.system.tm_annotation};
  Object.assign(SPEC.system, r.system, keep);
  STRUCT = r.structure; drawStructure(); drawChecks();
  $('prepnote').textContent = 'prepared: ' + (r.report.log || []).length + ' steps logged' + ((r.report.placement || {}).note ? ' · ' + r.report.placement.note : '');   // as an import of files says where the partner sits
  await refresh();
}

/* ---- the viewer: 3Dmol by default, Mol* on request; the same bar the lab's other structure
   viewers have ("the 3D panel"): color modes with
   custom colors, draw, surfaces, show, contacts with kinds and distances, focus on a residue,
   figure mode, label modes and label dragging (vendor/drag.js), reset / spin / save, engine.
   The structure arrives as two models: 0 the receptor, 1 the ligand or partner. Restraint
   picking is 3Dmol's; Mol* is for looking. Behavior copied from the lab's other viewers, not reinvented. */
const VENDOR = window.HOPEMD_VENDOR || '/static/vendor/';
const CUSTOM = {rec: '#b9c9c9', lig: '#c2603a', bg: '#dfe3e6'};   // bg: everything outside a close-up
const SURF = {rec: 0, lig: 0};                      // 0 off, 1 solid, 2 see-through, 3 mesh
const SURFNAME = ['off', 'solid', 'see-through', 'mesh'];
let ENGINE = '3dmol', MV = null, MVBUSY = false, SPIN = false;
try { const e = localStorage.getItem('hopemd.viewer'); if (e === 'molstar' || e === '3dmol') ENGINE = e; } catch (e) {}
const cssVar = n => getComputedStyle(document.body).getPropertyValue(n).trim();

/* @contacts-start */
/* Contacts, as the lab's other viewers classify them: H-bond ≤ 3.5 Å between N/O/S, salt bridge
   ≤ 4.0 Å between the charged groups, ring stacking ≤ 5.0 Å between aromatic carbons, van der
   Waals ≤ 4.0 Å. One line per residue pair, not per atom pair: the closest atom pair wins the
   distance and the most specific kind wins the color. */
const CKINDS = [['hbond', 'H-bond', '#2B83BA'], ['ionic', 'Salt bridge', '#F0C814'], ['stack', 'Ring stacking', '#8CB366'], ['vdw', 'van der Waals', '#808080']];
const SPECIFIC = ['ionic', 'stack', 'hbond', 'vdw'];
const KINDON = {hbond: 1, ionic: 1, stack: 1, vdw: 0};
const HBOND_A = 3.5, IONIC_A = 4.0, STACK_A = 5.0, VDW_A = 4.0;
const POLAR = {N: 1, O: 1, S: 1};
const BASIC = {ARG: ['NE', 'NH1', 'NH2'], LYS: ['NZ'], HIS: ['ND1', 'NE2'], HID: ['ND1', 'NE2'], HIE: ['ND1', 'NE2'], HIP: ['ND1', 'NE2']};
const ACIDIC = {ASP: ['OD1', 'OD2'], GLU: ['OE1', 'OE2']};
const RING = {PHE: 1, TYR: 1, TRP: 1, HIS: 1, HID: 1, HIE: 1, HIP: 1};
const colourOfKind = k => (CKINDS.find(x => x[0] === k) || [0, 0, '#888'])[2];
const kindOf = r => SPECIFIC.find(k => r.kinds[k]) || 'vdw';

function parseAtoms(text) {
  const out = [];
  for (const L of text.split('\n')) {
    if (L.slice(0, 4) !== 'ATOM' && L.slice(0, 6) !== 'HETATM') continue;
    const el = (L.slice(76, 78).trim() || L.slice(12, 16).trim()[0] || '').toUpperCase();
    if (el === 'H') continue;
    // a residue is its number and its insertion code (thrombin's 60, 60A ... 60I are ten): rid names it, ic selects it
    const resi = parseInt(L.slice(22, 26), 10), ic = L.length > 26 ? L[26] : ' ';
    out.push({name: L.slice(12, 16).trim(), resn: L.slice(17, 20).trim(), ch: L[21] || ' ', resi, ic, rid: resi + ic.trim(), el,
              x: +L.slice(30, 38), y: +L.slice(38, 46), z: +L.slice(46, 54)});
  }
  return out;
}
function classifyContacts(recText, ligText) {
  const rec = parseAtoms(recText), lig = parseAtoms(ligText);
  // the receptor on a 5 Å grid: a protein partner against a large receptor is millions of atom pairs otherwise
  const cell = STACK_A, grid = new Map();
  const cellKey = (x, y, z) => Math.floor(x / cell) + ',' + Math.floor(y / cell) + ',' + Math.floor(z / cell);
  for (const a of rec) { const k = cellKey(a.x, a.y, a.z); const g = grid.get(k); if (g) g.push(a); else grid.set(k, [a]); }
  const pairs = [], byres = {}, seenPair = {};
  for (const b of lig) {
    const gx = Math.floor(b.x / cell), gy = Math.floor(b.y / cell), gz = Math.floor(b.z / cell);
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) for (let dz = -1; dz <= 1; dz++) {
      const bucket = grid.get((gx + dx) + ',' + (gy + dy) + ',' + (gz + dz));
      if (!bucket) continue;
      for (const a of bucket) {
        const d = Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
        if (d > STACK_A) continue;
        const k = {};
        if (d <= VDW_A) k.vdw = 1;
        if (d <= HBOND_A && POLAR[a.el] && POLAR[b.el]) k.hbond = 1;
        if (d <= IONIC_A && ((BASIC[a.resn] || []).includes(a.name) && (ACIDIC[b.resn] || []).includes(b.name)
                          || (ACIDIC[a.resn] || []).includes(a.name) && (BASIC[b.resn] || []).includes(b.name))) k.ionic = 1;
        if (d <= STACK_A && RING[a.resn] && RING[b.resn] && a.el === 'C' && b.el === 'C') k.stack = 1;
        if (!Object.keys(k).length) continue;
        const kind = SPECIFIC.find(s => k[s]) || 'vdw';
        const pk = a.ch + ':' + a.rid + '|' + b.ch + ':' + b.rid;
        const have = seenPair[pk];
        if (have) {
          if (d < have.d) { have.d = d; have.ax = a.x; have.ay = a.y; have.az = a.z; have.bx = b.x; have.by = b.y; have.bz = b.z; }
          if (SPECIFIC.indexOf(kind) < SPECIFIC.indexOf(have.kind)) have.kind = kind;
        } else {
          const p = {chain: a.ch, resi: a.resi, ic: a.ic, rid: a.rid, resn: a.resn, ax: a.x, ay: a.y, az: a.z,
                     bchain: b.ch, bresi: b.resi, bic: b.ic, brid: b.rid, bresn: b.resn, bx: b.x, by: b.y, bz: b.z, d, kind};
          seenPair[pk] = p; pairs.push(p);
        }
        const rk = a.ch + ':' + a.rid;
        if (!byres[rk]) byres[rk] = {chain: a.ch, resi: a.resi, ic: a.ic, rid: a.rid, resn: a.resn, kinds: {}, d, x: a.x, y: a.y, z: a.z};
        byres[rk].kinds[kind] = 1;
        if (d < byres[rk].d) { byres[rk].d = d; byres[rk].x = a.x; byres[rk].y = a.y; byres[rk].z = a.z; }
      }
    }
  }
  return {res: Object.values(byres).sort((p, q) => p.d - q.d), pairs};
}
/* @contacts-end */

const PUB_BOND = '#111111';
let CONTACTS = {res: [], pairs: []};
let RCONTACT = true, RDIST = true, RPUB = false, RLABEL = 'auto', RFOCUS = [], RFOCUSFRAMED = false, RPUBFRAMED = false, DRAG = null;
const focusKey = (c, rid) => c + ':' + rid;
const partnerLike = () => SPEC.system.ligand_class === 'protein' || SPEC.system.ligand_class === 'nucleic_acid';
const recHex = () => COLOR === 'custom' ? CUSTOM.rec : (cssVar('--rec') || CUSTOM.rec);
const ligHex = () => COLOR === 'custom' ? CUSTOM.lig : (cssVar('--rust') || CUSTOM.lig);
// one residue exactly: its number and, when known, its insertion code (a blank one is ' ', as 3Dmol reads it)
const icSel = ic => (ic === undefined ? {} : {icode: ic || ' '});
const recSel = r => ({model: 0, ...(r.chain && r.chain.trim() ? {chain: r.chain} : {}), resi: r.resi, ...icSel(r.ic)});
const ligSel = (c, r, ic) => ({model: 1, ...(c && c.trim() ? {chain: c} : {}), resi: r, ...icSel(ic)});

function carbonScheme(hex) {
  try { const base = ($3Dmol.elementColors && $3Dmol.elementColors.defaultColors) || {}; return {prop: 'elem', map: Object.assign({}, base, {C: hex})}; }
  catch (e) { return 'grayCarbon'; }
}
/* A label on the canvas is drawn in pixels, so it keeps the page's smallest size and grows
   with the text-size control like everything else: at 150% these were still 9 and 11 px. */
function labelBox(col, extra) {
  return Object.assign({fontSize: drawnPx(14), fontColor: '#14202b', backgroundColor: '#ffffff', backgroundOpacity: 0.92, borderThickness: 0.8, borderColor: col || '#9aa6b1', inFront: true}, extra || {});
}
function labelBare(col, extra) {
  return Object.assign({fontSize: drawnPx(14), fontColor: col || cssVar('--ink') || '#14202b', showBackground: false, inFront: true}, extra || {});
}
function labelFor(bare, col, extra) {
  const m = RLABEL === 'auto' ? (bare ? 'plain' : 'boxed') : RLABEL;
  if (m === 'none') return null;
  return m === 'plain' ? labelBare(col, extra) : labelBox(col, extra);
}

/* ---- chains: draw one differently from the rest (a peptide as sticks beside a cartoon
   receptor, or hide a chain). The Setup viewer has chain letters; the Runs viewer draws the
   stripped trajectory, which has none, so the server sends each chain's residue range (on
   Desmond, whose trajectory keeps the input's letters and numbers, the chain's runs in those) ---- */
let CHAINDRAW = {};                  // chain id → 'cartoon' | 'trace' | 'sticks' | 'off'; unset = the draw buttons
const CHAIN_MODES = ['cartoon', 'trace', 'sticks', 'off'];
function chainList() {
  const sys = SPEC.system, partner = new Set(sys.partner_chains || []);
  return (sys.chains_found || []).filter(c => ['protein', 'nucleic', 'nucleic_acid'].includes(c.kind))
    .map(c => ({id: c.id, model: partner.has(c.id) && HAS_LIG ? 1 : 0, part: partner.has(c.id) ? 'lig' : 'rec'}));
}
function drawChainPicker(el, grp, chains, state, onchange) {
  el.innerHTML = chains.map(c => `<select class="vsel" data-chain="${esc(c.id)}" title="chain ${esc(c.id)}${c.approx ? ' (residue range by count: approximate)' : ''}"><option value="">${esc(c.id)}: as draw</option>`
    + CHAIN_MODES.map(k => `<option value="${k}"${state[c.id] === k ? ' selected' : ''}>${esc(c.id)}: ${k}</option>`).join('') + '</select>').join('');
  grp.hidden = chains.length < 1;
  el.querySelectorAll('select').forEach(sl => sl.onchange = () => { if (sl.value) state[sl.dataset.chain] = sl.value; else delete state[sl.dataset.chain]; onchange(); });
}
function chainStyle(mode, colour) {
  if (mode === 'off') return {};
  if (mode === 'sticks') return {stick: {radius: 0.15, ...colour}};
  return {cartoon: {...(mode === 'trace' ? {style: 'trace'} : {}), ...colour}};
}

function drawStructure() {
  if (!STRUCT) return;
  showEngine();
  $('vlabel').textContent = structureName();
  const [rec, lig] = STRUCT.split('REMARK 999 HOPEMD LIGAND');
  HAS_LIG = !!(lig && lig.trim());
  STRUCTKEY = {rec: textHash(rec), lig: HAS_LIG ? textHash(lig) : ''};
  CONTACTS = HAS_LIG ? classifyContacts(rec, lig) : {res: [], pairs: []};
  RFOCUS = []; RFOCUSFRAMED = false; RPUBFRAMED = false;
  if (ENGINE === 'molstar') { drawContactList(RCONTACT ? CONTACTS.res : []); drawLid(RCONTACT ? CONTACTS : null); drawMolstar(); return; }
  if (typeof $3Dmol === 'undefined') return;
  if (!V) {
    V = $3Dmol.createViewer($('viewer'), {backgroundColor: cssVar('--card') || 'white'});
    // label dragging: the helper the lab's other viewers share,
    // vendored whole (keeping a dragged label inside the canvas, drawing its leader into a PNG)
    try { if (window.makeLabelDrag) DRAG = window.makeLabelDrag(V, $('viewer'), {}); } catch (e) { DRAG = null; }
  }
  msmsClear(V, MSMS_SETUP);           // before removeAllShapes, which would leave them listed at stale places
  V.removeAllModels(); V.removeAllShapes(); V.removeAllLabels();
  try { V.removeAllSurfaces(); } catch (e) {}
  V.addModel(rec, 'pdb');
  if (HAS_LIG) V.addModel(lig, 'pdb');
  drawChainPicker($('chainsel'), $('chaingrp'), chainList(), CHAINDRAW, () => styleStructure(false));
  styleStructure(true);
  // the key names the residue as the importer does, its insertion code with its number ("H:60A")
  V.setClickable({model: 0}, true, a => { if (a && a.resi != null && !a.hetflag) toggleRestrain((a.chain || 'A') + ':' + a.resi + String(a.icode || '').trim()); });   // an ion is not a residue to hold
}

const structureName = () => structureFileName() + ' · ' + lab(SPEC.system.ligand_class) + (SPEC.system.prepared ? ' · prepared' : ' · as imported');
function colourFor(isLig) {
  if (COLOR === 'custom') return {color: isLig ? CUSTOM.lig : CUSTOM.rec};
  if (COLOR === 'element') return {colorscheme: 'default'};
  if (COLOR === 'plain') return {color: isLig ? cssVar('--rust') : cssVar('--rec')};
  return isLig ? {color: cssVar('--rust')} : {colorscheme: 'chain'};     // chain: receptor by chain, the partner rust
}
function contactResidueStyle(col) {
  if (DRAW === 'sticks') return {stick: {radius: 0.17, color: col}};
  return {stick: {radius: 0.17, color: col}, cartoon: Object.assign({color: col}, DRAW === 'trace' ? {style: 'trace'} : {})};
}
/* the bilayer as a translucent sand slab, 40 Å thick, centered on the membrane center, wider
   than the protein: a picture of what the build embeds the protein in, so a wrong orientation
   (a membrane normal that is not z) shows before anything is submitted */
function drawMembraneSlab() {
  if (!V || !STRUCT || !MEMBRANE(SPEC) || typeof V.addBox !== 'function') return;
  const atoms = parseAtoms(STRUCT);
  if (!atoms.length) return;
  const xs = atoms.map(a => a.x), ys = atoms.map(a => a.y);
  const pad = Math.max(15, Number(get(SPEC, 'system.membrane.padding_A')) || 10);
  const cz = Number(get(SPEC, 'system.membrane.center_z_A')) || 0;
  const x0 = Math.min(...xs) - pad, y0 = Math.min(...ys) - pad;
  try {
    V.addBox({corner: {x: x0, y: y0, z: cz - BILAYER_A / 2}, dimensions: {w: Math.max(...xs) + pad - x0, h: Math.max(...ys) + pad - y0, d: BILAYER_A},
              color: '#d9bf73', opacity: 0.28});
  } catch (e) {}
}
function styleStructure(frame) {
  if (!V) return;
  msmsClear(V, MSMS_SETUP);
  V.removeAllShapes(); V.removeAllLabels();
  drawMembraneSlab();
  const recStyle = {}, ligStyle = {};
  const rc = colourFor(false), lc = colourFor(true);
  if (DRAW === 'cartoon') recStyle.cartoon = {...rc}; else if (DRAW === 'trace') recStyle.cartoon = {style: 'trace', ...rc}; else recStyle.stick = {radius: 0.15, ...rc};
  if (partnerLike()) {
    // a partner chain is drawn like the receptor, in the color of the thing being simulated
    if (DRAW === 'sticks') ligStyle.stick = {radius: 0.15, ...lc};
    else ligStyle.cartoon = {...(DRAW === 'trace' ? {style: 'trace'} : {}), ...lc};
  } else {
    ligStyle.stick = {radius: 0.25, ...(COLOR === 'element' ? {colorscheme: 'default'} : lc)};
  }
  V.setStyle({model: 0}, SHOW.rec ? recStyle : {});
  if (HAS_LIG) V.setStyle({model: 1}, SHOW.lig ? ligStyle : {});
  for (const c of chainList()) {
    const mode = CHAINDRAW[c.id];
    if (!mode || (c.model === 1 ? !SHOW.lig : !SHOW.rec)) continue;
    V.setStyle({model: c.model, chain: c.id}, chainStyle(mode, colourFor(c.model === 1)));
  }
  // the transmembrane segments, in sand, on top of the receptor's color
  const tmc = (SPEC.system.tm_annotation || {}).chains || {};
  const hasTM = Object.values(tmc).some(c => (c.segments || []).length);
  $('vtm').hidden = !hasTM;
  if (hasTM && SHOWTM && SHOW.rec && DRAW !== 'sticks') for (const [ch, info] of Object.entries(tmc)) for (const seg of info.segments || []) {
    const list = []; for (let i = seg.start; i <= seg.end; i++) list.push(i);
    V.setStyle({model: 0, chain: ch, resi: list}, {cartoon: {...(DRAW === 'trace' ? {style: 'trace'} : {}), color: '#c9a24a'}});
  }
  const shown = CONTACTS.res.filter(r => CKINDS.some(([k]) => KINDON[k] && r.kinds[k]));
  const focused = applyFocus();
  const pub = !focused && RPUB ? applyPublication(shown) : false;
  if (RCONTACT && HAS_LIG && SHOW.lig && !focused && !pub) {
    shown.forEach(r => {
      const col = colourOfKind(kindOf(r));
      if (SHOW.rec) V.setStyle(recSel(r), contactResidueStyle(col));
      const lb = labelFor(false, col, {position: {x: r.x, y: r.y, z: r.z}});
      if (lb) V.addLabel(r.resn + r.rid, lb);
    });
    CONTACTS.pairs.forEach(pp => {
      if (!KINDON[pp.kind]) return;
      const col = colourOfKind(pp.kind);
      V.addCylinder({start: {x: pp.ax, y: pp.ay, z: pp.az}, end: {x: pp.bx, y: pp.by, z: pp.bz}, radius: pp.kind === 'vdw' ? 0.035 : 0.055,
                     dashed: true, dashLength: 0.25, gapLength: 0.22, fromCap: 1, toCap: 1, color: col});
      // the distance on the midpoint of the line it measures, one decimal, in the contact's color
      if (!RDIST) return;
      const dl = labelFor(false, col, {fontSize: drawnPx(13), position: {x: (pp.ax + pp.bx) / 2, y: (pp.ay + pp.by) / 2, z: (pp.az + pp.bz) / 2}});
      if (dl) V.addLabel(pp.d.toFixed(1) + ' Å', dl);
    });
  }
  // restrained residues: the site blue (brand.py), on top of everything else
  // a key is chain:number with any insertion code ("H:60A"), as a click names the residue
  for (const k of SPEC.protocol.restrain) {
    const m = /^(.*?):(-?\d+)(\S*)$/.exec(k);
    if (m) V.setStyle({model: 0, chain: m[1], resi: +m[2], icode: m[3] || ' '}, {cartoon: {color: '#1a56db'}, stick: {radius: 0.22, color: '#1a56db'}});
  }
  applySurfaces();
  drawContactList(RCONTACT ? CONTACTS.res : []);
  drawLid(RCONTACT ? CONTACTS : null);
  if (frame && !RFOCUS.length && !RPUB) V.zoomTo();
  V.render();
}
function surfStyle(mode, hex) {
  if (mode === 1) return {opacity: 0.92, color: hex};
  if (mode === 2) return {opacity: 0.5, color: hex};
  return {opacity: 0.95, color: hex, wireframe: true, linewidth: 0.5};
}
/* ---- surfaces: computed by MSMS on the server (/api/surface, hopemd/surface.py) from the file the
   viewer drew, and drawn as the mesh it gives (msmsSurface and msmsAdd, /static/msms.js). Each answer
   is asked for once per structure. The mesh is custom shapes, kept in a list and taken out with
   removeShape on every redraw: removeAllSurfaces does not reach a shape, and removeAllShapes would
   take the contacts and the bilayer with it. Where MSMS gives no surface, the viewer draws its own and
   a quiet note says why. */
let MSMS_SETUP = [], STRUCTKEY = {rec: '', lig: ''};
/* a short fingerprint of a text (FNV-1a over its characters, and its length): the surface asked for is
   the surface of the text drawn, so a structure that changes is surfaced again */
function textHash(t) {
  let h = 0x811c9dc5;
  for (let i = 0; i < t.length; i++) { h ^= t.charCodeAt(i); h = Math.imul(h, 0x01000193); }
  return (h >>> 0).toString(36) + '.' + t.length.toString(36);
}
function msmsClear(viewer, shapes) {
  shapes.forEach(sh => { try { viewer.removeShape(sh); } catch (e) {} });
  shapes.length = 0;
}
function surfNote(el, pending, why) {
  const text = pending.length ? 'Computing the surface' + (pending.length > 1 ? 's' : '') + ' with MSMS'
    : why.length ? 'Surface drawn by the viewer: ' + [...new Set(why)].join('; ') + '.' : '';
  el.textContent = text; el.hidden = !text;
}
/* One surface: MSMS's mesh, or the viewer's own with the reason. `ask` gives the key and the URL of
   the answer, or null when what is drawn has no file to surface; `then` redraws when it arrives. */
function drawSurface(viewer, shapes, style, sel, ask, then, pending, why) {
  const own = reason => { try { viewer.addSurface($3Dmol.SurfaceType.VDW, style, sel); } catch (e) {} why.push(reason); };
  if (!ask) return own('what is shown has no file on the server to surface');
  const d = msmsSurface(ask.key, ask.url + '&t=' + encodeURIComponent(T), then);
  if (d === null) { pending.push(ask.key); return; }
  if (d.source !== 'msms') return own(d.why || 'MSMS gave none');
  try { shapes.push(...msmsAdd(viewer, d, style)); } catch (e) { own('the mesh could not be drawn (' + e.message + ')'); }
}
function applySurfaces() {
  if (!V) return;
  msmsClear(V, MSMS_SETUP);
  try { V.removeAllSurfaces(); } catch (e) {}
  const pending = [], why = [];
  const ask = part => STAGING ? {key: 'setup|' + STAGING + '|' + part + '|' + STRUCTKEY[part],
                                 url: '/api/surface?staging=' + encodeURIComponent(STAGING) + '&part=' + part} : null;
  const then = () => { if (V && STRUCT) { applySurfaces(); V.render(); } };
  if (SURF.rec && SHOW.rec) drawSurface(V, MSMS_SETUP, surfStyle(SURF.rec, recHex()), {model: 0}, ask('rec'), then, pending, why);
  if (SURF.lig && SHOW.lig && HAS_LIG) drawSurface(V, MSMS_SETUP, surfStyle(SURF.lig, ligHex()), {model: 1}, ask('lig'), then, pending, why);
  surfNote($('surfnote'), pending, why);
}
function toggleRestrain(k) {
  const i = SPEC.protocol.restrain.indexOf(k);
  if (i < 0) SPEC.protocol.restrain.push(k); else SPEC.protocol.restrain.splice(i, 1);
  paint(); styleStructure(false); schedule();
}

/* ---- focus: one receptor residue (or several) and what it touches, the rest as a pale ground ---- */
function applyFocus() {
  if (!RFOCUS.length || !V || !HAS_LIG) return false;
  const want = {}; RFOCUS.forEach(f => { want[focusKey(f.chain, f.rid)] = 1; });
  const mine = CONTACTS.pairs.filter(pp => want[focusKey(pp.chain, pp.rid)]);
  if (!mine.length) { RFOCUS = []; return false; }
  V.setStyle({model: 0}, {cartoon: {color: CUSTOM.bg, opacity: 0.55}});
  V.setStyle({model: 1}, partnerLike() ? {cartoon: {color: CUSTOM.bg, opacity: 0.55}} : {stick: {radius: 0.12, color: CUSTOM.bg, opacity: 0.55}});
  RFOCUS.forEach(f => V.setStyle(recSel(f), {stick: {radius: 0.2, colorscheme: carbonScheme(recHex())}}));
  const far = {}; mine.forEach(pp => { far[pp.bchain + ':' + pp.brid] = pp; });
  Object.values(far).forEach(pp => {
    V.setStyle(ligSel(pp.bchain, pp.bresi, pp.bic), {stick: {radius: 0.2, colorscheme: carbonScheme(ligHex())}});
    const lb = labelFor(false, ligHex(), {position: {x: pp.bx, y: pp.by, z: pp.bz}});
    if (lb) V.addLabel(pp.bresn + pp.brid, lb);
  });
  const seen = {};
  mine.forEach(pp => {
    const k = focusKey(pp.chain, pp.rid);
    if (seen[k]) return; seen[k] = 1;
    const rl = labelFor(false, recHex(), {position: {x: pp.ax, y: pp.ay, z: pp.az}});
    if (rl) V.addLabel(pp.resn + pp.rid, rl);
  });
  mine.forEach(pp => {
    if (!KINDON[pp.kind]) return;
    const col = colourOfKind(pp.kind);
    V.addCylinder({start: {x: pp.ax, y: pp.ay, z: pp.az}, end: {x: pp.bx, y: pp.by, z: pp.bz}, radius: 0.07, dashed: true, dashLength: 0.25, gapLength: 0.22, fromCap: 1, toCap: 1, color: col});
    if (!RDIST) return;
    const dl = labelFor(false, col, {fontSize: drawnPx(13), position: {x: (pp.ax + pp.bx) / 2, y: (pp.ay + pp.by) / 2, z: (pp.az + pp.bz) / 2}});
    if (dl) V.addLabel(pp.d.toFixed(1) + ' Å', dl);
  });
  if (!RFOCUSFRAMED) {
    RFOCUSFRAMED = true;
    const parts = RFOCUS.map(recSel).concat(Object.values(far).map(pp => ligSel(pp.bchain, pp.bresi, pp.bic)));
    try { V.zoomTo(parts.length === 1 ? parts[0] : {or: parts}); } catch (e) { try { V.zoomTo(parts[0]); } catch (e2) {} }
    V.zoom(0.6);
  }
  return true;
}

/* ---- figure mode: the active-site view for a paper ---- */
function applyPublication(shown) {
  if (!V || !HAS_LIG) return false;
  V.setStyle({model: 0}, {cartoon: {color: CUSTOM.bg, opacity: 0.65}});
  if (partnerLike()) V.setStyle({model: 1}, {cartoon: {color: CUSTOM.bg, opacity: 0.65}});
  else V.setStyle({model: 1}, {stick: {radius: 0.2, colorscheme: carbonScheme(ligHex())}});
  shown.forEach(r => {
    V.setStyle(recSel(r), {stick: {radius: 0.16, colorscheme: carbonScheme(recHex())}, cartoon: {color: CUSTOM.bg, opacity: 0.65}});
    const lb = labelFor(true, recHex(), {position: {x: r.x, y: r.y, z: r.z}});
    if (lb) V.addLabel(r.resn + r.rid, lb);
  });
  const named = {};
  CONTACTS.pairs.forEach(pp => {
    if (!KINDON[pp.kind]) return;
    const k = pp.bchain + ':' + pp.brid;
    if (named[k]) return; named[k] = 1;
    if (partnerLike()) V.setStyle(ligSel(pp.bchain, pp.bresi, pp.bic), {stick: {radius: 0.16, colorscheme: carbonScheme(ligHex())}, cartoon: {color: CUSTOM.bg, opacity: 0.65}});
    const lb = labelFor(true, ligHex(), {position: {x: pp.bx, y: pp.by, z: pp.bz}});
    if (lb) V.addLabel(pp.bresn + pp.brid, lb);
  });
  CONTACTS.pairs.forEach(pp => {
    if (!KINDON[pp.kind]) return;
    V.addCylinder({start: {x: pp.ax, y: pp.ay, z: pp.az}, end: {x: pp.bx, y: pp.by, z: pp.bz}, radius: 0.032, dashed: true, dashLength: 0.30, gapLength: 0.26, fromCap: 1, toCap: 1, color: PUB_BOND});
  });
  if (shown.length && !RPUBFRAMED) {
    RPUBFRAMED = true;
    try { V.zoomTo({or: shown.map(recSel)}); } catch (e) {}
  }
  return true;
}

/* ---- the contact list under the viewer: a tick per kind, a pill per residue that focuses on it ---- */
function drawContactList(res) {
  const box = $('contactbox');
  if (!box) return;
  if (!RCONTACT || !res.length) { box.innerHTML = ''; box.hidden = true; return; }
  box.hidden = false;
  const on = res.filter(r => CKINDS.some(([k]) => KINDON[k] && r.kinds[k]));
  const count = k => res.filter(r => r.kinds[k]).length;
  const showChain = partnerLike() && new Set(res.map(r => r.chain)).size > 1;
  box.innerHTML = '<div class="ctckinds"><span class="lb">' + (partnerLike() ? 'interface' : 'contacts') + '</span>'
    + CKINDS.map(([k, name, col]) => '<label class="ctckind" style="color:' + col + '"><input type="checkbox" data-kind="' + k + '"' + (KINDON[k] ? ' checked' : '') + '> ' + esc(name) + ' (' + count(k) + ')</label>').join('') + '</div>'
    + (on.length ? '<div class="ctcrows">' + on.map(r => '<button class="pill ctcrow' + (RFOCUS.some(f => f.chain === r.chain && f.rid === r.rid) ? ' on' : '')
        + '" data-go="' + esc(r.chain) + ':' + esc(r.rid) + '" title="focus on this residue and what it touches; click again to release">'
        + esc(r.resn) + esc(r.rid) + (showChain ? ' ' + esc(r.chain.trim()) : '') + ' · ' + r.d.toFixed(1) + ' Å</button>').join('') + '</div>'
      : '<span class="why">nothing in the kinds that are ticked</span>');
  box.querySelectorAll('input[data-kind]').forEach(b => b.onchange = () => { KINDON[b.dataset.kind] = b.checked ? 1 : 0; styleStructure(false); });
  box.querySelectorAll('.ctcrow').forEach(b => b.onclick = () => {
    const i = b.dataset.go.indexOf(':'); const c = b.dataset.go.slice(0, i), rid = b.dataset.go.slice(i + 1);
    const key = focusKey(c, rid);
    const at = RFOCUS.findIndex(f => focusKey(f.chain, f.rid) === key);
    const r = CONTACTS.res.find(x => x.chain === c && x.rid === rid);
    if (at >= 0) RFOCUS.splice(at, 1); else if (r) RFOCUS.push({chain: c, resi: r.resi, ic: r.ic, rid});
    RFOCUSFRAMED = false;
    styleStructure(false);
  });
}

/* ---- the ligand interaction diagram: the ligand along the middle, each receptor residue over
   the one it is closest to, lines by kind. Flat, labeled, printable; saved as SVG. Lanes are
   assigned before the canvas is sized so nothing is drawn outside the viewBox. ---- */
function bestKind(links) { for (const k of SPECIFIC) if (links.some(l => l.kind === k)) return k; return 'vdw'; }
function drawLid(cls) {
  const box = $('diagwrap');
  if (!box) return;
  const pairs = ((cls && cls.pairs) || []).filter(pp => KINDON[pp.kind]);
  if (!pairs.length) { box.innerHTML = ''; box.hidden = true; return; }
  box.hidden = false;
  const pep = {};
  pairs.forEach(pp => { const k = pp.bchain + ':' + pp.brid; if (!pep[k]) pep[k] = {chain: pp.bchain, resi: pp.bresi, ic: pp.bic, rid: pp.brid, resn: pp.bresn}; });
  const pepList = Object.values(pep).sort((a, b) => (a.chain > b.chain) - (a.chain < b.chain) || a.resi - b.resi || ((a.ic || ' ') > (b.ic || ' ')) - ((a.ic || ' ') < (b.ic || ' ')));
  const pepAt = {}; pepList.forEach((r, i) => { pepAt[r.chain + ':' + r.rid] = i; });
  const rec = {};
  pairs.forEach(pp => {
    const k = pp.chain + ':' + pp.rid;
    const e = rec[k] || (rec[k] = {chain: pp.chain, resi: pp.resi, rid: pp.rid, resn: pp.resn, d: 1e9, at: 0, links: []});
    e.links.push(pp);
    if (pp.d < e.d) { e.d = pp.d; const i = pepAt[pp.bchain + ':' + pp.brid]; e.at = i === undefined ? 0 : i; }
  });
  const recList = Object.values(rec).sort((a, b) => a.at - b.at || a.d - b.d);
  const SLOT = 102, LANE = 46, RX = 35, RY = 14, PAD = RX + 8;
  const above = [], below = [];
  recList.forEach((r, i) => { (i % 2 ? below : above).push(r); });
  const lane = list => { const used = {}; let deepest = 0; list.forEach(r => { let L = 0; while (used[L + ':' + r.at]) L++; used[L + ':' + r.at] = 1; r.lane = L; if (L > deepest) deepest = L; }); return deepest; };
  const deepA = lane(above), deepB = lane(below);
  const W = PAD * 2 + Math.max(pepList.length, 1) * SLOT;
  const midY = PAD + (deepA + 1) * LANE + RY;
  const H = midY + 30 + (deepB + 1) * LANE + RY + PAD - RX + 8;
  const px = i => PAD + i * SLOT + SLOT / 2;
  above.forEach(r => { r.x = px(r.at); r.y = midY - 30 - (r.lane + 1) * LANE; });
  below.forEach(r => { r.x = px(r.at); r.y = midY + 30 + (r.lane + 1) * LANE; });
  const rust = '#c2603a', rustSoft = '#f7ece7', rustInk = '#8a4620';
  /* The drawing is laid out in its own units and then drawn at the page's scale: the residue
     names inside it are SVG text, which no type token reaches, and at 150% they stayed 13px
     beside 22px body text. Scaling the whole viewBox grows the boxes with the names, so
     nothing overflows its ellipse. */
  const ds = drawScale();
  const out = ['<svg viewBox="0 0 ' + W + ' ' + H + '" width="' + Math.round(W * ds) + '" height="' + Math.round(H * ds) + '" role="img" aria-label="ligand interaction diagram">'];
  recList.forEach(r => r.links.forEach(pp => {
    const i = pepAt[pp.bchain + ':' + pp.brid];
    if (i === undefined) return;
    out.push('<line x1="' + r.x.toFixed(1) + '" y1="' + r.y.toFixed(1) + '" x2="' + px(i).toFixed(1) + '" y2="' + midY + '" stroke="' + colourOfKind(pp.kind)
      + '" stroke-width="1.6" stroke-dasharray="4 3" opacity="0.85"><title>' + esc((r.resn || '?') + r.rid + ' - ' + (pp.bresn || '?') + pp.brid + '  ' + pp.d.toFixed(2) + ' Å') + '</title></line>');
  }));
  pepList.forEach((r, i) => {
    const x = px(i);
    out.push('<rect x="' + (x - 34) + '" y="' + (midY - 16) + '" width="68" height="32" rx="7" fill="' + rustSoft + '" stroke="' + rust + '" stroke-width="1.3"></rect>');
    out.push('<text x="' + x + '" y="' + (midY + 5) + '" text-anchor="middle" font-size="13" font-weight="600" fill="' + rustInk + '">' + esc((r.resn || '?') + r.rid) + '</text>');
  });
  out.push('<line x1="' + PAD + '" y1="' + midY + '" x2="' + (W - PAD) + '" y2="' + midY + '" stroke="' + rust + '" stroke-width="1" opacity="0.35"></line>');
  recList.forEach(r => {
    const col = colourOfKind(bestKind(r.links));
    out.push('<ellipse cx="' + r.x.toFixed(1) + '" cy="' + r.y.toFixed(1) + '" rx="' + RX + '" ry="' + RY + '" fill="#ffffff" stroke="' + col + '" stroke-width="1.6"></ellipse>');
    out.push('<text x="' + r.x.toFixed(1) + '" y="' + (r.y + 4.5).toFixed(1) + '" text-anchor="middle" font-size="13" fill="#20262b">' + esc((r.resn || '?') + r.rid) + '</text>');
  });
  out.push('</svg>');
  const what = partnerLike() ? 'Interface' : 'Ligand interaction';
  box.innerHTML = '<h3>' + what + ' diagram'
    + '<button class="q" type="button" data-helpkey="lid" aria-label="About this diagram">?</button>'
    + '<button class="pill" id="lidsave" title="Save it as an SVG, which goes into a figure at any size">Save SVG</button>'
    + '<span class="why">Hover a line for its distance.</span></h3>'
    + '<div class="diagbox">' + out.join('') + '</div>';
  $('lidsave').onclick = () => {
    const svg = box.querySelector('svg'); if (!svg) return;
    const copy = svg.cloneNode(true); copy.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');   // a white ground: a transparent one picks up whatever is behind it
    bg.setAttribute('x', '0'); bg.setAttribute('y', '0'); bg.setAttribute('width', '100%'); bg.setAttribute('height', '100%'); bg.setAttribute('fill', '#ffffff');
    copy.insertBefore(bg, copy.firstChild);
    const txt = '<?xml version="1.0" encoding="UTF-8"?>\n' + new XMLSerializer().serializeToString(copy);
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([txt], {type: 'image/svg+xml'}));
    a.download = (SPEC && SPEC.name ? SPEC.name : 'complex') + '_interactions.svg'; a.click();
  };
}

/* ---- Mol*: loaded the first time it is asked for (5 MB), then kept ---- */
function loadScript(src) { return new Promise((ok, bad) => { const sc = document.createElement('script'); sc.src = src; sc.onload = ok; sc.onerror = () => bad(new Error('could not load ' + src)); document.head.appendChild(sc); }); }
function loadCss(href) { if (document.querySelector('link[href="' + href + '"]')) return; const l = document.createElement('link'); l.rel = 'stylesheet'; l.href = href; document.head.appendChild(l); }
function showEngine() {
  const mol = ENGINE === 'molstar';
  $('viewer').hidden = mol; $('viewerM').hidden = !mol;
  const sel = $('vengine'); if (sel && sel.value !== ENGINE) sel.value = ENGINE;
  $('vhint').textContent = mol ? 'Mol* is for looking: switch to 3Dmol to click residues' : 'Click a residue to restrain it · click again to release';
  // the controls that are 3Dmol's own say so rather than sitting there inert
  document.querySelectorAll('#vbar [data-show],#vbar [data-draw],#vbar [data-col],#vbar2 [data-surf],#vbar2 .cpick,#vspin,#vshot,#vcontact,#vdist,#vpub,#vlabels,#vdrag,#vdragoff').forEach(el => { el.style.opacity = mol ? 0.45 : 1; });
  if (!mol && V) { V.resize(); V.render(); }
}
async function drawMolstar() {
  if (MVBUSY) return; MVBUSY = true;
  try {
    if (typeof molstar === 'undefined') { $('vlabel').textContent = 'Loading Mol*…'; loadCss(VENDOR + 'molstar.css'); await loadScript(VENDOR + 'molstar.js'); }
    // extensions: [] keeps Mol* from calling out to its data services on start; everything it draws here is local
    if (!MV) MV = await molstar.Viewer.create($('viewerM'), {layoutIsExpanded: false, layoutShowControls: false, layoutShowSequence: true, layoutShowLog: false, viewportShowExpand: true, collapseLeftPanel: true, extensions: []});
    molstarBackground();
    await MV.plugin.clear();
    const pdb = STRUCT.replace('REMARK 999 HOPEMD LIGAND', '').split('\n').filter(l => !/^END\s*$/.test(l)).join('\n') + '\nEND\n';
    await MV.loadStructureFromData(pdb, 'pdb');
    $('vlabel').textContent = structureName() + ' · Mol*';
  } catch (e) { $('vlabel').textContent = 'Mol* could not draw this: ' + e.message; }
  finally { MVBUSY = false; }
}
function molstarBackground() {
  if (!MV) return;
  try { MV.plugin.canvas3d.setProps({renderer: {backgroundColor: parseInt(cssVar('--card').replace('#', ''), 16)}}); } catch (e) {}
}
/* The rest of the viewer's controls open on request: a bar of twenty buttons is what made this
   page feel like a cockpit. */
function moreBar(btn, bar) {
  $(btn).onclick = () => {
    const show = $(bar).hidden;
    $(bar).hidden = !show;
    $(btn).classList.toggle('on', show);
    $(btn).setAttribute('aria-expanded', show ? 'true' : 'false');
    if (V) { V.resize(); V.render(); }
    if (VR) { VR.resize(); VR.render(); }
  };
}
moreBar('vmore', 'vbar2');
moreBar('vmoreR', 'vbarR2');

$('vengine').onchange = () => { ENGINE = $('vengine').value; try { localStorage.setItem('hopemd.viewer', ENGINE); } catch (e) {} if (STRUCT) drawStructure(); else showEngine(); };

/* ---- the bar ---- */
document.querySelectorAll('[data-col]').forEach(b => b.onclick = () => { COLOR = b.dataset.col; document.querySelectorAll('[data-col]').forEach(x => x.classList.toggle('on', x === b)); styleStructure(false); });
document.querySelectorAll('[data-draw]').forEach(b => b.onclick = () => { DRAW = b.dataset.draw; document.querySelectorAll('[data-draw]').forEach(x => x.classList.toggle('on', x === b)); styleStructure(false); });
document.querySelectorAll('[data-show]').forEach(b => b.onclick = () => { SHOW[b.dataset.show] = !SHOW[b.dataset.show]; b.classList.toggle('on', SHOW[b.dataset.show]); styleStructure(false); });
const partWord = k => k === 'rec' ? 'Receptor' : 'Ligand';
document.querySelectorAll('[data-surf]').forEach(b => b.onclick = () => { const k = b.dataset.surf; SURF[k] = (SURF[k] + 1) % 4; b.classList.toggle('on', !!SURF[k]); b.textContent = partWord(k) + ' ' + SURFNAME[SURF[k]]; styleStructure(false); });
for (const [id, key] of [['ccol', 'rec'], ['lcol', 'lig']]) $(id).oninput = () => { CUSTOM[key] = $(id).value; COLOR = 'custom'; document.querySelectorAll('[data-col]').forEach(x => x.classList.toggle('on', x.dataset.col === 'custom')); styleStructure(false); };
$('bcol').oninput = () => { CUSTOM.bg = $('bcol').value; styleStructure(false); };
const onOff = (id, on) => { $(id).classList.toggle('on', on); $(id).textContent = on ? 'On' : 'Off'; };
$('vcontact').onclick = () => {
  RCONTACT = !RCONTACT; onOff('vcontact', RCONTACT);
  if (!RCONTACT) { RFOCUS = []; RPUB = false; RPUBFRAMED = false; onOff('vpub', false); }
  styleStructure(false);
};
$('vdist').onclick = () => { RDIST = !RDIST; onOff('vdist', RDIST); styleStructure(false); };
$('vpub').onclick = () => {
  RPUB = !RPUB; RPUBFRAMED = false; onOff('vpub', RPUB);
  if (RPUB && !RCONTACT) { RCONTACT = true; onOff('vcontact', true); }
  styleStructure(false);
};
$('vlabels').onchange = () => { RLABEL = $('vlabels').value; styleStructure(false); };
$('vdrag').onclick = () => {
  if (!DRAG) { $('vhint').textContent = 'label dragging could not start: the 3D viewer itself may not have loaded'; return; }
  const on = !DRAG.isEnabled(); if (on) DRAG.enable(); else DRAG.disable();
  $('vdrag').classList.toggle('on', on);
};
$('vdragoff').onclick = () => { if (!DRAG) return; DRAG.reset(); if (DRAG.isEnabled()) DRAG.disable(); $('vdrag').classList.remove('on'); };
$('vreset').onclick = () => {
  if (ENGINE === 'molstar') { try { MV && MV.plugin.managers.camera.reset(); } catch (e) {} return; }
  if (!V) return;
  RFOCUS = []; RPUB = false; RPUBFRAMED = false; onOff('vpub', false);
  SPIN = false; $('vspin').classList.remove('on'); try { V.spin(false); } catch (e) {}
  styleStructure(true);
};
$('vspin').onclick = () => { if (!V) return; SPIN = !SPIN; $('vspin').classList.toggle('on', SPIN); try { V.spin(SPIN ? 'y' : false); } catch (e) {} };
$('vshot').onclick = () => { if (V) savePng(); };
function savePng() {
  // the leader lines of dragged labels are SVG, which the canvas capture never sees: drawn in by hand
  const name = (SPEC && SPEC.name ? SPEC.name : 'structure') + '.png';
  const download = href => { const a = document.createElement('a'); a.href = href; a.download = name; a.click(); };
  let segs = []; try { segs = DRAG ? DRAG.leaderSegments() : []; } catch (e) {}
  let uri; try { uri = V.pngURI(); } catch (e) { return; }
  if (!segs.length) { download(uri); return; }
  const img = new Image();
  img.onload = () => {
    const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
    const ctx = c.getContext('2d'); ctx.drawImage(img, 0, 0);
    const k = img.width / Math.max(1, $('viewer').clientWidth);
    segs.forEach(s => { ctx.strokeStyle = s.color || '#777777'; ctx.lineWidth = Math.max(1, k); ctx.beginPath(); ctx.moveTo(s.x1 * k, s.y1 * k); ctx.lineTo(s.x2 * k, s.y2 * k); ctx.stroke(); });
    download(c.toDataURL('image/png'));
  };
  img.src = uri;
}

/* ---- light / dark: the OS setting until the button is pressed, then remembered per browser ---- */
function applyTheme(t) {
  const root = document.documentElement;
  if (t === 'dark' || t === 'light') root.dataset.theme = t; else delete root.dataset.theme;
  try { if (t) localStorage.setItem('hopemd.theme', t); else localStorage.removeItem('hopemd.theme'); } catch (e) {}
  const dark = t === 'dark' || (!t && matchMedia('(prefers-color-scheme: dark)').matches);
  $('theme').textContent = dark ? 'Light' : 'Dark';
  const bg = cssVar('--card') || (dark ? '#141d1f' : '#ffffff');
  for (const v of [V, VR]) if (v) { try { v.setBackgroundColor(bg); v.render(); } catch (e) {} }
  if (V && STRUCT && COLOR !== 'custom' && COLOR !== 'element') styleStructure(false);   // theme colors moved
  if (VR && FRAMES) styleRun();
  molstarBackground();
}
$('theme').onclick = () => { const dark = document.documentElement.dataset.theme === 'dark' || (!document.documentElement.dataset.theme && matchMedia('(prefers-color-scheme: dark)').matches); applyTheme(dark ? 'light' : 'dark'); };
(function bootTheme() { let t = ''; try { t = localStorage.getItem('hopemd.theme') || ''; } catch (e) {} applyTheme(t); })();

/* ------------------------------------------------------------------ the structure browser
   /static/picker.js: the dialog the HOPE Labs pages carry, byte for byte, so somebody who has
   used one has learned this one. Places down the left (This computer, which uploads the file;
   PDB code; My scratch; Home; HOPE-MD runs; the other tools' run folders), breadcrumbs and the
   folder on the right. The dialog only chooses; what a choice does is here, through this page's
   own routes, and a reason it cannot be read is shown in the dialog with the folder still open. */
const STRUCTURE_KINDS = ['.pdb', '.ent', '.cif', '.mmcif', '.gz', '.mae', '.maegz', '.zip', '.sdf', '.mol2', '.mol'];

/* What a chosen file is read as before anything is sent. "Read it as" on the card can still say
   otherwise: this is the first guess, not the decision. */
function kindForPath(path) {
  const p = String(path || '').toLowerCase();
  if (p.endsWith('.mae') || p.endsWith('.maegz')) return 'poseviewer';
  if (p.endsWith('.zip')) return 'alphafold';
  if (p.endsWith('.sdf') || p.endsWith('.mol2') || p.endsWith('.mol')) return 'files';
  return 'complex';
}

/* One chosen thing into the spec, then the import. A refusal is thrown back at the dialog, which
   stays open where it was with the reason under it. */
async function loadFrom(source, shown) {
  Object.assign(SPEC.system.source, {path: '', pdb_id: '', peptide: '', ligand: ''}, source);
  SPEC.system.ligands_found = [];
  SPEC.system.prepared = false;
  SPEC.system.chains_found = [];
  STAGING = null; ADCPLIST = [];
  PICKED = shown;
  renderForm();
  drawPicked();              // the viewer's box is on the page before 3Dmol measures it
  if (!await doImport()) throw new Error($('loadnote').textContent || 'That could not be read.');
  return true;
}

/* A file from the person's own computer: sent up as base64, so a zip or a .maegz arrives whole,
   kept under the runs root, and read from there like any other path. */
function fileData(file) {
  return new Promise((ok, bad) => {
    const r = new FileReader();
    r.onload = () => { const s = String(r.result || ''); ok(s.slice(s.indexOf(',') + 1)); };
    r.onerror = () => bad(new Error('That file could not be read from this computer.'));
    r.readAsDataURL(file);
  });
}
async function uploadFile(file) {
  const r = await api('/api/upload', {name: file.name, data: await fileData(file)});
  if (r.error) throw new Error(r.error);
  return r.path;
}

function pickerOptions(extra) {
  return Object.assign({
    api: (path, body) => api(path, body),
    places: '/api/places',
    browse: '/api/browse',
    accept: '.pdb,.ent,.cif,.mmcif,.mae,.maegz,.zip',
    kinds: STRUCTURE_KINDS,
    key: 'hopemd',
    acceptSays: 'A PDB or mmCIF file, a Maestro poseviewer, or an AlphaFold zip.',
    mkdir: '/api/mkdir',
  }, extra || {});
}

function chooseStructure(startWith) {
  return HLPicker.open(pickerOptions({
    title: 'Choose a structure',
    startWith: startWith || '',
    onFile: async f => loadFrom({kind: kindForPath(f.name), path: await uploadFile(f)},
                                {name: f.name, where: 'Sent from this computer'}),
    onCode: code => loadFrom({kind: 'rcsb', pdb_id: code}, {name: code, where: 'Fetched from the RCSB'}),
    onPath: p => loadFrom({kind: kindForPath(p), path: p}, {name: p.split('/').filter(Boolean).pop(), where: p}),
    // A run folder of ours: the browse answer says which tool made it and what to read from it.
    onRun: (at, run) => loadFrom({kind: run.kind || 'complex', path: run.kind && run.kind !== 'complex' ? at : run.structure},
                                 {name: run.name, where: (run.tool ? run.tool + ' run · ' : '') + at}),
  }));
}

/* The same dialog for one field that holds a path (the partner file): it fills the field, and
   the source is read again. */
function pickInto(path) {
  return HLPicker.open(pickerOptions({
    title: path === 'system.source.ligand_path' ? 'Choose a partner file' : 'Choose a file',
    key: 'hopemd-' + path,
    onFile: async f => { set(SPEC, path, await uploadFile(f)); paint(); schedule(); },
    onPath: async p => { set(SPEC, path, p); paint(); if (path === 'system.source.path') onSourcePathChanged(); schedule(); },
  }));
}

$('choose').onclick = () => chooseStructure();
$('codebtn').onclick = () => chooseStructure('code');
$('changepick').onclick = () => chooseStructure();
/* A file dropped on the card is the same as choosing it on This computer. */
const zone = $('pickzone');
zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('over'); });
zone.addEventListener('dragleave', () => zone.classList.remove('over'));
zone.addEventListener('drop', async e => {
  e.preventDefault(); zone.classList.remove('over');
  const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
  if (!f) return;
  $('pickerr').hidden = true;
  try { await loadFrom({kind: kindForPath(f.name), path: await uploadFile(f)}, {name: f.name, where: 'Sent from this computer'}); }
  catch (err) { $('pickerr').hidden = false; $('pickerr').textContent = err.message; }
});

/* ------------------------------------------------------------------ the reason behind a ?
   Every sentence that used to sit in a gray box on the form opens here, in the words it had. */
function helpFor(key) {
  // A card's ? is titled with the card; a setting's ? with that setting's own label.
  const card = GROUPS.find(g => g.key === key);
  let title = card ? card.title : {plan: 'Run plan', choose: 'Choosing a structure', where: 'Where it goes',
                                   lid: 'The diagram', checks: 'What the file holds'}[key];
  if (!title) {
    for (const g of GROUPS) {
      const f = g.fields.find(one => one.path === key);
      if (f) { title = typeof f.label === 'function' ? f.label(SPEC) : f.label; break; }
    }
  }
  const parts = [HELPTEXT(key)];
  if (key === 'system') {
    parts.push(KIND_HELP[SPEC.system.source.kind] || '');
    if (MEMBRANE(SPEC)) parts.push('The membrane normal must be z and the bilayer centered on z = '
      + (get(SPEC, 'system.membrane.center_z_A') ?? 0) + ' Å. An OPM entry (opm.phar.umich.edu) is oriented that way; a file from elsewhere is not until you orient it.');
  }
  if (key === 'forcefield') {
    if (hasNucleic(SPEC) && !(CHOICES.nucleic || []).length) parts.push('Nucleic acid: typed by the System Builder with ' + SPEC.forcefield.protein + ', the force field of the whole system. OL21, bsc1 and OL3 are for the Amber-topology engines.');
    if (SPEC.system.ligand_class === 'small_molecule' && !(CHOICES.ligand || []).length) parts.push("Small molecule: typed by the System Builder with the run's force field, from the ligand file's bond orders and formal charges; nothing to choose.");
    if (SPEC.system.ligand_class !== 'small_molecule') parts.push(SPEC.system.ligand_class === 'none'
      ? (hasNucleic(SPEC) ? 'No partner: the nucleic acid alone, covered by the force fields above.' : 'Apo: nothing to parameterize.')
      : lab(SPEC.system.ligand_class) + ': the force field covers it, nothing to parameterize.');
  }
  if (key === 'analysis' && MMGBSA_ON(SPEC)) parts.push('Scored by ' + ((SITE && SITE.mmgbsa_tools || {})[SPEC.forcefield.engine] || 'MMPBSA.py')
    + (SPEC.forcefield.engine === 'desmond' ? '.' : ': the same force field that ran the dynamics scores the binding.'));
  if (key === 'plan' && DERIVED.stage_note) parts.push(DERIVED.stage_note + (DERIVED.ns_per_day_note ? ' · ' + DERIVED.ns_per_day_note : ''));
  // how the transmembrane segments were decided, which is too long to carry in a chip
  if (key === 'checks' && TM_WHY) parts.push('Membrane: ' + TM_WHY + '.');
  return {title: title || 'About this setting', body: parts.filter(Boolean)};
}
function openHelp(key) {
  const got = helpFor(key);
  $('helptitle').textContent = got.title;
  $('helpbody').innerHTML = got.body.map(p => '<p>' + esc(p) + '</p>').join('') || '<p>Nothing more to say about this one.</p>';
  if (!$('helpdlg').open) $('helpdlg').showModal();
}
document.addEventListener('click', e => {
  const b = e.target.closest && e.target.closest('[data-help],[data-helpkey]');
  if (!b) return;
  e.preventDefault();
  openHelp(b.dataset.help || b.dataset.helpkey);
});
$('helpx').onclick = $('helpclose').onclick = () => $('helpdlg').close();

/* ------------------------------------------------------------------ Show the job
   The stages the queue will be asked for and the lines each script carries. /api/render with
   nothing submitted: it renders in memory and writes no run folder. */
$('showjob').onclick = async () => {
  $('jobtext').textContent = 'Rendering…';
  $('jobnote').textContent = '';
  if (!$('jobdlg').open) $('jobdlg').showModal();
  const r = await api('/api/render', {spec: SPEC});
  if (r.errors) { $('jobtext').textContent = r.errors.map(e => e.message).join('\n'); return; }
  if (r.error) { $('jobtext').textContent = r.error; return; }
  const byPath = {}; for (const f of r.files || []) byPath[f.path] = f.text;
  const lines = [];
  for (const j of r.jobs || []) {
    lines.push('# ' + j.key + (j.array ? ' (' + j.array + ' tasks)' : '')
      + (j.depends && j.depends.length ? ' — after ' + j.depends.map(d => d[0]).join(', ') : '')
      + (j.description ? '\n#   ' + j.description : ''));
    lines.push('sbatch ' + j.script);
    for (const line of String(byPath[j.script] || '').split('\n')) if (line.startsWith('#SBATCH')) lines.push('  ' + line);
    lines.push('');
  }
  $('jobtext').textContent = lines.join('\n') || 'Nothing to run yet.';
  $('jobnote').textContent = (r.notes || []).join(' · ');
};
$('jobx').onclick = $('jobclose').onclick = () => $('jobdlg').close();
/* ------------------------------------------------------------------ submit */
/* a force field the site's license may not cover is asked about once more, at the button */
function confirmForceField() {
  const lic = ffNote(SPEC.forcefield.engine, SPEC.forcefield.protein);
  return !lic || window.confirm(lic[1] + '\n\nSubmit with ' + SPEC.forcefield.protein + ' all the same?');
}
async function submit() {
  if (!confirmForceField()) return;
  $('submit').disabled = true; $('subnote').textContent = 'rendering and submitting…';
  const r = await api('/api/submit', {spec: SPEC, staging: STAGING});
  if (r.errors) { ERRORS = r.errors; paint(); $('subnote').textContent = r.errors.map(e => e.message).join(' '); return; }
  if (r.error) { $('subnote').textContent = r.error; return; }
  const out = $('subout'); out.hidden = false;
  out.textContent = (r.submitted.dry_run ? 'DRY RUN — nothing queued\n' : '') + r.submitted.log.join('\n') + '\n\nrun folder: ' + r.rundir + '\nfiles: ' + r.files.map(f => f.path).join(', ') + (r.notes.length ? '\n\nnotes:\n- ' + r.notes.join('\n- ') : '');
  $('subnote').textContent = 'submitted as ' + r.name;
  await loadRuns();
  showScreen('runs'); selectRun(r.name);
}

/* The card's name for a device, not the vendor's: "NVIDIA A100-PCIE-40GB" held the jobs table
   276px wider than the box it sits in, and was read as "NVIDIA A100-PCIE-4". The whole string
   is on the cell. */
const deviceShort = name => String(name || '').replace(/^NVIDIA\s+/i, '').replace(/-(\d+)GB$/i, ' $1GB');

/* ------------------------------------------------------------------ runs */
let RUNS = [], CURRENT = null, FRAMES = 0, PLAYING = null, SUMMARY = null, REP = 1, RUNSPEC = null, DENSE = [], VIEWFILE = '', CHAINS = [], PARTS = null, WATERS = null;
async function loadRuns() {
  const d = await api('/api/runs');
  RUNS = d.runs || [];
  $('runsroot2').textContent = d.root || '';
  const active = RUNS.filter(r => r.submitted && !r.dry_run).length;
  $('runcount').hidden = !active; $('runcount').textContent = active;
  const dotOf = r => ({running: 'run', done: 'fin', pending: 'q', failed: 'stop', dry: 'q dry', unknown: 'unk'}[r.state] || '');
  $('runlist').innerHTML = RUNS.length ? RUNS.map(r => '<div class="runrow' + (r.name === CURRENT ? ' on' : '') + '" data-run="' + esc(r.name) + '">'
    + '<span class="dot ' + dotOf(r) + '" title="' + esc(r.state || 'not submitted') + '"></span>'
    + '<span class="what"><span class="name" title="' + esc(r.name) + '">' + esc(r.name) + '</span>'
    + '<span class="when">' + esc(r.engine) + ' &middot; ' + esc(r.modified.slice(5)) + '</span></span></div>').join('') : '<p class="why">No runs yet.</p>';
  $('runlist').querySelectorAll('[data-run]').forEach(el => el.onclick = () => selectRun(el.dataset.run));
}
async function selectRun(name) {
  CURRENT = name; loadRuns();
  const d = await api('/api/run?name=' + encodeURIComponent(name));
  if (d.error) { $('progress').textContent = d.error; return; }
  const s = d.spec, p = d.progress;
  RUNSPEC = s;
  $('runname').textContent = name;
  // the parts that have something to say, joined once: an empty last part left the line
  // ending on a separator with nothing after it
  $('rundesc').textContent = [
    `${SITE ? SITE.engines[s.forcefield.engine] : s.forcefield.engine} ${s.forcefield.protein}${hasNucleic(s) && s.forcefield.engine !== 'desmond' && s.forcefield.nucleic ? ', ' + s.forcefield.nucleic : ''} / ${s.forcefield.water}`,
    MEMBRANE(s) ? (get(s, 'system.membrane.lipid') || 'lipid') + ' bilayer' : '',
    `${s.protocol.length_ns} ns × ${s.protocol.replicates}`,
    p.system ? fmtInt(p.system.atoms) + ' atoms' + (p.system.lipids ? ', ' + fmtInt(p.system.lipids) + ' lipids' : '') : 'not built yet',
    s.compute.device === 'cpu' ? 'CPU check' : '',
  ].filter(Boolean).join(' · ');
  // progress rail: one row per job of the graph and per replicate of an array job, in the server's order (queue.stage_rows)
  const st = p.states || {};
  const stateOf = k => { const m = st[k] || {}; const keys = Object.keys(m); if (!keys.length) return (p.jobs && p.jobs[k]) ? (String(p.jobs[k]).startsWith('dry') ? 'dry run' : 'queued') : '—'; if (m.RUNNING) return 'running'; if (keys.every(x => x === 'COMPLETED')) return 'done'; if (keys.some(x => /FAIL|CANCEL|TIMEOUT|OUT_OF/.test(x))) return 'failed'; return keys.join('/').toLowerCase(); };
  const bar = (frac, cls) => `<div class="bar"><i class="${cls || ''}" style="width:${Math.round(frac * 100)}%"></i></div>`;
  const stg = (key, task, label, right, frac, cls) => `<div class="stg${STAGE_VIEW && STAGE_VIEW.key === key && STAGE_VIEW.task === task ? ' on' : ''}" data-stage="${esc(key)}" data-task="${task}" title="click to follow this job's log"><b>${esc(label)}</b><span class="r">${esc(right)}</span></div>` + bar(frac, cls);
  $('progress').innerHTML = (p.stages || []).map(r => stg(r.key, r.task, r.label, r.text, r.frac, r.bad ? 'bad' : '')).join('') || 'Not submitted';
  $('progress').querySelectorAll('[data-stage]').forEach(el => el.onclick = () => followStage(el.dataset.stage, +el.dataset.task));
  // jobs table with what ran where and for how long (jobs/timing.json), and the stages a resubmission can start from;
  // every job the run has had, those a resubmission replaced dimmed, the graph's ids alone while the accounting is away
  const tm = d.timing || {};
  const tjobs = (tm.jobs || []).length ? tm.jobs : Object.entries(p.jobs || {}).map(([k, v]) => ({key: k, job: v}));
  $('jobs').querySelector('tbody').innerHTML = tjobs.map(j => `<tr${j.replaced ? ' style="opacity:.55" title="replaced by a later job of this stage"' : ''}><td>${esc(j.key)}${j.task ? ' r' + esc(j.task) : ''}</td><td>${esc(j.job)}</td><td>${esc(j.state ? j.state.toLowerCase() : stateOf(j.key))}</td>`
    + `<td>${esc(j.node || '')}</td><td title="${esc(j.gpu || '')}">${esc(deviceShort(j.gpu) || (j.cpus ? j.cpus + ' CPU' : ''))}</td><td>${j.steps ? fmtInt(j.steps) : (j.frames ? fmtInt(j.frames) + ' frames' : '')}</td>`
    + `<td>${esc(j.elapsed || '')}</td><td>${j.ns_per_day || ''}</td></tr>`).join('') || '<tr><td colspan="8">Not submitted</td></tr>';
  const est = tm.estimate || {};
  $('timingnote').textContent = tm.jobs && tm.jobs.length
    ? `whole graph ${tm.wall || '—'} from first start to last end · ${tm.busy || '—'} of node time · estimated at submit: ${est.ns_per_day} ns/day (${est.note}), ${est.hours_per_replicate} h of production per replicate, walltime ${est.walltime} per segment` + (tm.complete ? '' : ' · still running: times so far')
    : '';
  CHAINS = d.chains || []; PARTS = d.parts || null;
  drawChainPicker($('chainselR'), $('chaingrpR'), CHAINS, RV.chaindraw, styleRun);
  const keys = Object.keys(p.jobs || {}); const cur = $('refrom').value;
  $('refrom').innerHTML = keys.map(k => `<option value="${esc(k)}">${esc(k)}</option>`).join('');
  if (keys.includes(cur)) $('refrom').value = cur; else if (keys.some(k => k.startsWith('prod_s'))) $('refrom').value = keys.filter(k => k.startsWith('prod_s')).pop();
  LOGTAIL = d.log_tail || '';
  if (!STAGE_VIEW) $('logtail').textContent = LOGTAIL || 'No logs yet.';
  // the Log tail's list: the newest log, or any job's (queue.run_logs)
  LOGS = d.logs || []; const pick = $('logpick'), was = pick.value;
  pick.innerHTML = '<option value="">newest log</option>' + LOGS.map(l => `<option value="${esc(l.stage + '|' + l.task + '|' + l.job)}">${esc(l.stage + (l.array ? ' r' + l.task : '') + ' · ' + l.job)}</option>`).join('');
  pick.value = [...pick.options].some(o => o.value === was) ? was : '';
  drawVideos(d.videos || {});
  drawTidy(d.tidy || null);
  RUN_SPEC = d.spec; $('mmgbsaform').hidden = true;
  DENSE = d.dense || [];
  WATERS = d.waters || null;
  // summary tiles
  SUMMARY = d.summary || null; REP = 1;
  drawTiles(s);
  const reps = SUMMARY ? Object.keys(SUMMARY.replicates) : [];
  $('reppick').innerHTML = reps.map(r => `<option value="${r}">r${r}</option>`).join('') || '<option>—</option>';
  $('reppick').onchange = () => { REP = +$('reppick').value; VIEWFILE = ''; drawViewPick(); drawWaterPill(); loadTrajectory(); };
  drawViewPick();
  drawWaterPill();
  loadTrajectory();
}
function drawTiles(s) {
  const T = $('tiles'); const reps = SUMMARY ? SUMMARY.replicates : {};
  const first = reps['1'] || Object.values(reps)[0];
  if (!first) { T.innerHTML = '<div class="tile"><div class="tk">Analysis</div><div class="sub">The plots appear when the analysis job has run.</div></div>'; return; }
  const cols = ['var(--teal)', 'var(--rust)', 'var(--green)', 'var(--amber)', 'var(--dim)'];
  const tile = (title, val, svg) => `<div class="tile"><div class="tk">${title}</div><div class="tv">${val}</div>${svg}</div>`;
  const series = key => Object.entries(reps).filter(([, r]) => r[key]).map(([i, r], k) => ({x: r[key].ns, y: r[key].values, col: cols[k % cols.length]}));
  let html = '';
  const rr = series('rmsd_rec'); if (rr.length) html += tile('Receptor ' + (s ? traceWords(partKinds(s, 'rec')) : 'Cα') + ' RMSD', `${first.rmsd_rec.mean} Å <small>r1 mean</small>`, spark(rr, 'ns', 'Å'));
  const partner = s && (s.system.ligand_class === 'protein' || s.system.ligand_class === 'nucleic_acid');
  // the heading names the measure; what it was fitted on is in the small line under the number
  const rl = series('rmsd_lig'); if (rl.length) html += tile(partner ? 'Partner RMSD' : 'Ligand RMSD', `${first.rmsd_lig.mean} Å <small>r1 mean · max ${first.rmsd_lig.max} · receptor fitted</small>`, spark(rl, 'ns', 'Å'));
  const ri = series('rmsd_iface'); if (ri.length) html += tile('Interface ' + (s ? traceWords(partKinds(s, '')) : 'Cα') + ' RMSD', `${first.rmsd_iface.mean} Å <small>r1 mean · max ${first.rmsd_iface.max} · receptor fitted</small>`, spark(ri, 'ns', 'Å'));
  const rb = series('bsa'); if (rb.length) html += tile('Buried surface', `${fmtInt(Math.round(first.bsa.mean))} Å² <small>r1 mean · started at ${fmtInt(Math.round(first.bsa.first))}</small>`, spark(rb, 'ns', 'Å²'));
  const ra = series('area_per_lipid'); if (ra.length) html += tile('Area per lipid', `${first.area_per_lipid.mean} Å² <small>r1 mean · box x·y over half the lipids, the solute's footprint included</small>`, spark(ra, 'ns', 'Å²'));
  if (first.rmsf) html += tile('RMSF per residue', `${first.rmsf.median} Å <small>median${first.rmsf.peak ? ' · highest at ' + esc(first.rmsf.peak.residue) + ', ' + first.rmsf.peak.value + ' Å' : ''}</small>`, spark([{x: first.rmsf.residue, y: first.rmsf.values, col: cols[0], fill: true}], 'res', 'Å'));
  if (SUMMARY.confidence && SUMMARY.confidence.iptm != null) html += `<div class="tile"><div class="tk">AlphaFold confidence</div><div class="big" style="color:${SUMMARY.confidence.iptm < 0.6 ? 'var(--amber)' : 'var(--teal)'}">ipTM ${SUMMARY.confidence.iptm}</div><div class="sub">pTM ${SUMMARY.confidence.ptm ?? '—'} · the prediction's own confidence, not the simulation's: a low ipTM is a doubtful interface before a single step ran</div></div>`;
  if (first.contacts) html += `<div class="tile"><div class="tk">${partner ? 'Interface contacts kept' : 'Docking contacts kept'}</div><div class="big">${first.contacts.kept} / ${first.contacts.total}</div><div class="sub">present in more than ${Math.round(first.contacts.threshold * 100)}% of r1's frames${first.contacts.lost.length ? '. Lost: ' + first.contacts.lost.slice(0, 3).map(c => esc(c.contact)).join(', ') : ''}</div></div>`;
  T.innerHTML = html;
  $('mmgbsa').querySelector('tbody').innerHTML = Object.entries(reps).map(([i, r]) => r.mmgbsa ? `<tr><td>r${i}</td><td>${r.mmgbsa.frames ?? '—'}</td><td>${r.mmgbsa.mean.toFixed(1)}</td><td>${r.mmgbsa.sem.toFixed(1)}</td></tr>` : `<tr><td>r${i}</td><td>—</td><td>—</td><td>—</td></tr>`).join('');
  // a row of dashes says nothing about why there is no number. The reasons the page can know are
  // written under the table; a run that simply has not got there yet says that too.
  $('mmgbsanote').textContent = Object.values(reps).some(r => r.mmgbsa) ? ''
    : !s ? ''
    : s.system.ligand_class === 'none' ? 'Nothing to score: the run has no partner or ligand.'
    : LINKS(s).length ? `No MM-GBSA: the disulfide${LINKS(s).length === 1 ? '' : 's'} ${LINKS(s).join(', ')} make${LINKS(s).length === 1 ? 's' : ''} the run one molecule.`
    : !s.analysis.mmgbsa ? 'This run was submitted without MM-GBSA. Run it with the button below, over the window you choose.'
    : 'The MM-GBSA job has not written its numbers yet.';
}
/* A sparkline, and its numbers beside it in HTML: text inside a viewBox is scaled by the box,
   so it cannot keep to the page's smallest size. */
function spark(series, xl, yl) {
  const W = 200, H = 56, x0 = 2, x1 = 198, y0 = 52, y1 = 4;
  const xs = series.flatMap(s => s.x), ys = series.flatMap(s => s.y);
  const xmin = Math.min(...xs), xmax = Math.max(...xs), ymax = Math.max(1, ...ys);
  const X = x => x0 + (x - xmin) / (xmax - xmin || 1) * (x1 - x0), Y = y => y0 - y / ymax * (y0 - y1);
  let out = `<svg class="spark" viewBox="0 0 ${W} ${H}"><line class="ax" x1="${x0}" y1="${y0}" x2="${x1}" y2="${y0}"/>`;
  for (const s of series) {
    const pts = s.x.map((x, i) => `${X(x).toFixed(1)},${Y(s.y[i]).toFixed(1)}`).join(' ');
    if (s.fill) out += `<polygon class="fill" points="${pts} ${X(s.x[s.x.length - 1]).toFixed(1)},${y0} ${x0},${y0}"/>`;
    out += `<polyline class="ln" style="stroke:${s.col}" points="${pts}"/>`;
  }
  return out + '</svg>'
    + `<div class="axes"><span>0 &ndash; ${esc(ymax.toFixed(1))} ${esc(yl)}</span>`
    + `<span>${xmin < 1 ? 0 : Math.round(xmin)} &ndash; ${Math.round(xmax)} ${esc(xl)}</span></div>`;
}
/* ---- the Runs viewer's bar: the same color / draw / surface / show / view controls on the
   stripped trajectory, which is one model with the receptor residues first and the ligand or
   partner after (tleap's numbering, spec.system.receptor_residues / ligand_residues). Desmond's
   keeps the input's chain letters and residue numbers instead, and the server sends the parts
   in those (RV.parts: {rec, lig}, each [{chain, range}]) ---- */
/* A metal ion and a water are residues of their own, so neither falls in the receptor's or the
   partner's residue range and neither was drawn at all: a metalloprotein run opened as a bare
   cartoon with nothing at the site. They are drawn here as spheres, the ions in the colors the
   report's figures and the videos use, so the same ion is the same color wherever it is looked at.
   Element colors put iron and copper in neighbouring browns, which is what made the first videos
   unreadable. */
const METAL_COLOR = {CU: '#b87333', CU1: '#b87333', CU2: '#b87333', FE: '#1a7a3c', FE2: '#1a7a3c', FE3: '#1a7a3c',
                     MN: '#9c27b0', ZN: '#8d6e63', MG: '#2e7d32', CA: '#546e7a', NI: '#4caf50', CO: '#e91e63',
                     NA: '#f6c344', 'NA+': '#f6c344', K: '#f6c344', 'K+': '#f6c344', CL: '#7fb069', 'CL-': '#7fb069'};
const METAL_NAME = {CU: 'Cu', CU1: 'Cu(I)', CU2: 'Cu(II)', FE: 'Fe', FE2: 'Fe(II)', FE3: 'Fe(III)', MN: 'Mn',
                    ZN: 'Zn', MG: 'Mg', CA: 'Ca', NI: 'Ni', CO: 'Co',
                    NA: 'Na\u207a', 'NA+': 'Na\u207a', K: 'K\u207a', 'K+': 'K\u207a', CL: 'Cl\u207b', 'CL-': 'Cl\u207b'};
/* the viewer matches a residue name as the file spells it ("Na+", not "NA+"), so the spellings
   found in the frames are what is selected on; the color and the name are looked up in upper case */
const ionKey = resn => String(resn).toUpperCase();
const WATER_RESN = ['WAT', 'HOH', 'TIP3', 'SOL', 'SPC', 'T3P', 'T4P', 'T4PE'];
const RV = {color: 'chain', draw: 'cartoon', show: {rec: true, lig: true, wat: true}, surf: {rec: 0, lig: 0}, custom: {rec: '#b9c9c9', lig: '#c2603a'}, spin: false, rec: null, lig: null, parts: null, chaindraw: {},
            file: '', hash: '', sframe: 0};      // the trajectory file shown, its fingerprint, and the frame its surfaces are of
function ownSel(runs) {
  const one = p => { const list = []; for (let i = p.range[0]; i <= p.range[1]; i++) list.push(i); return p.chain ? {chain: p.chain, resi: list} : {resi: list}; };
  return runs.length === 1 ? one(runs[0]) : {or: runs.map(one)};
}
function runSel(which) {
  if (RV.parts) { const own = RV.parts[which]; return own && own.length ? ownSel(own) : null; }
  const rng = which === 'rec' ? RV.rec : RV.lig;
  if (!rng || rng.length !== 2) return null;
  const list = []; for (let i = rng[0]; i <= rng[1]; i++) list.push(i);
  return {resi: list};
}
/* the same residues as runSel, as /api/surface reads them: "1-85", or "A:25-109,L:17-29" by chain */
function runSelText(which) {
  if (RV.parts) { const own = RV.parts[which]; return own && own.length ? own.map(p => (p.chain ? p.chain + ':' : '') + p.range[0] + '-' + p.range[1]).join(',') : ''; }
  const rng = which === 'rec' ? RV.rec : RV.lig;
  return rng && rng.length === 2 ? rng[0] + '-' + rng[1] : '';
}
/* The Runs viewer's surfaces are MSMS's for the frame shown when they were drawn, as the viewer's own
   were; a frame's answer is kept while it is one of the last few asked for. */
let MSMS_RUN = [], RUNKEYS = [];
function runSurfaceAsk(which) {
  if (!CURRENT || !RV.file) return null;
  const sel = runSelText(which);
  const key = 'run|' + CURRENT + '|' + RV.file + '|' + RV.hash + '|' + RV.sframe + '|' + sel;
  RUNKEYS = RUNKEYS.filter(k => k !== key).concat([key]);
  while (RUNKEYS.length > 8) { const old = RUNKEYS.shift(); if (MSMS_SURFS[old]) delete MSMS_SURFS[old]; }
  return {key, url: '/api/surface?name=' + encodeURIComponent(CURRENT) + '&path=' + encodeURIComponent(RV.file)
                    + '&frame=' + RV.sframe + '&sel=' + encodeURIComponent(sel)};
}
function styleRun(arrived) {
  if (!VR) return;
  msmsClear(VR, MSMS_RUN);
  try { VR.removeAllSurfaces(); } catch (e) {}
  // an answer arriving redraws the frame it was asked for, not the one the slider has reached since
  if (arrived !== true) RV.sframe = Math.max(0, +$('frame').value || 0);
  const partner = !!(RUNSPEC && (RUNSPEC.system.ligand_class === 'protein' || RUNSPEC.system.ligand_class === 'nucleic_acid'));
  const colour = isLig => RV.color === 'custom' ? {color: isLig ? RV.custom.lig : RV.custom.rec}
    : RV.color === 'element' ? {colorscheme: 'default'}
    : RV.color === 'plain' ? {color: isLig ? cssVar('--rust') : cssVar('--rec')}
    : (isLig ? {color: cssVar('--rust')} : {colorscheme: 'chain'});
  const style = isLig => {
    const c = colour(isLig);
    if (RV.draw === 'sticks' || (isLig && !partner)) return {stick: {radius: isLig && !partner ? 0.25 : 0.15, ...c}};
    return {cartoon: {...(RV.draw === 'trace' ? {style: 'trace'} : {}), ...c}};
  };
  const rec = runSel('rec'), lig = runSel('lig');
  VR.setStyle({}, {});
  if (rec) VR.setStyle(rec, RV.show.rec ? style(false) : {}); else VR.setStyle({}, RV.show.rec ? style(false) : {});
  if (lig) VR.setStyle(lig, RV.show.lig ? style(true) : {});
  for (const c of CHAINS) {                       // one chain drawn its own way, by residue range
    const mode = RV.chaindraw[c.id], isLig = c.part === 'lig';
    if (!mode || (isLig ? !RV.show.lig : !RV.show.rec) || !c.range) continue;
    const list = []; for (let i = c.range[0]; i <= c.range[1]; i++) list.push(i);
    VR.setStyle(c.own ? ownSel(c.own) : {resi: list}, chainStyle(mode, colour(isLig)));
  }
  // the ions and the water the trajectory kept: outside every residue range, so drawn on their own
  for (const resn of (RV.ions || [])) VR.setStyle({resn: resn}, {sphere: {radius: 1.1, color: METAL_COLOR[ionKey(resn)] || cssVar('--amber')}});
  if (RV.waters) VR.setStyle({resn: WATER_RESN.flatMap(w => [w, w.toLowerCase()])}, RV.show.wat
    ? {sphere: {radius: 0.28, colorscheme: 'default'}, stick: {radius: 0.08, colorscheme: 'default'}} : {});
  const hex = k => RV.color === 'custom' ? RV.custom[k] : cssVar(k === 'rec' ? '--rec' : '--rust');
  const pending = [], why = [], then = () => { if (VR) styleRun(true); };
  if (RV.surf.rec && RV.show.rec && rec) drawSurface(VR, MSMS_RUN, surfStyle(RV.surf.rec, hex('rec')), rec, runSurfaceAsk('rec'), then, pending, why);
  if (RV.surf.lig && RV.show.lig && lig) drawSurface(VR, MSMS_RUN, surfStyle(RV.surf.lig, hex('lig')), lig, runSurfaceAsk('lig'), then, pending, why);
  surfNote($('surfnoteR'), pending, why);
  VR.render();
}
document.querySelectorAll('[data-rcol]').forEach(b => b.onclick = () => { RV.color = b.dataset.rcol; document.querySelectorAll('[data-rcol]').forEach(x => x.classList.toggle('on', x === b)); styleRun(); });
document.querySelectorAll('[data-rdraw]').forEach(b => b.onclick = () => { RV.draw = b.dataset.rdraw; document.querySelectorAll('[data-rdraw]').forEach(x => x.classList.toggle('on', x === b)); styleRun(); });
document.querySelectorAll('[data-rshow]').forEach(b => b.onclick = () => { const k = b.dataset.rshow; RV.show[k] = !RV.show[k]; b.classList.toggle('on', RV.show[k]); styleRun(); });
document.querySelectorAll('[data-rsurf]').forEach(b => b.onclick = () => { const k = b.dataset.rsurf; RV.surf[k] = (RV.surf[k] + 1) % 4; b.classList.toggle('on', !!RV.surf[k]); b.textContent = partWord(k) + ' ' + SURFNAME[RV.surf[k]]; styleRun(); });
for (const [id, key] of [['ccolR', 'rec'], ['lcolR', 'lig']]) $(id).oninput = () => { RV.custom[key] = $(id).value; RV.color = 'custom'; document.querySelectorAll('[data-rcol]').forEach(x => x.classList.toggle('on', x.dataset.rcol === 'custom')); styleRun(); };
$('vtm').onclick = () => { SHOWTM = !SHOWTM; $('vtm').classList.toggle('on', SHOWTM); styleStructure(false); };
$('vresetR').onclick = () => { if (!VR) return; RV.spin = false; $('vspinR').classList.remove('on'); try { VR.spin(false); } catch (e) {} VR.zoomTo(); VR.render(); };
$('vspinR').onclick = () => { if (!VR) return; RV.spin = !RV.spin; $('vspinR').classList.toggle('on', RV.spin); try { VR.spin(RV.spin ? 'y' : false); } catch (e) {} };
$('vshotR').onclick = () => { if (!VR) return; try { const a = document.createElement('a'); a.href = VR.pngURI(); a.download = (CURRENT || 'trajectory') + '_r' + REP + '_frame' + (+$('frame').value + 1) + '.png'; a.click(); } catch (e) {} };
/* Add to report: the frame as it is drawn now, kept in the run's report folder, where the
   report page shows it with a caption written from the run's own numbers and room for the
   reader's own words. */
$('vaddR').onclick = async () => {
  if (!VR || !CURRENT) return;
  const btn = $('vaddR'), was = btn.textContent;
  let png; try { png = VR.pngURI(); } catch (e) { btn.textContent = 'could not read the view'; setTimeout(() => { btn.textContent = was; }, 2500); return; }
  btn.disabled = true; btn.textContent = 'adding\u2026';
  const r = await api('/api/report/add', {name: CURRENT, image: png, rep: String(REP),
                                           frame: (+$('frame').value + 1),
                                           what: $('frameinfo').textContent || ''});
  btn.disabled = false;
  btn.textContent = r && r.error ? r.error : 'added to the report';
  setTimeout(() => { btn.textContent = was; }, 2500);
};

/* the strided view, or one of the dense windows cut for this replicate */
function drawViewPick() {
  const mine = DENSE.filter(w => +w.rep === REP);
  const stride = SUMMARY ? SUMMARY.save_ps * viewStride() : 400;
  const opts = [`<option value="">strided · every ${stride} ps</option>`].concat(mine.map(w => w.ready
    ? `<option value="${esc(w.file)}">dense ${w.centre_ns - w.half_ns}–${w.centre_ns + w.half_ns} ns · ${w.frames} frames · ${w.size_mb} MB</option>`
    : `<option value="" disabled>dense around ${w.centre_ns} ns · job ${esc(w.job)} ${esc(w.state || 'queued')}</option>`));
  $('viewpick').innerHTML = opts.join('');
  if (mine.some(w => w.ready && w.file === VIEWFILE)) $('viewpick').value = VIEWFILE; else VIEWFILE = '';
}
$('viewpick').onchange = () => {
  VIEWFILE = $('viewpick').value;
  if (VIEWFILE && WANT_WATERS) { WANT_WATERS = false; drawWaterPill(); }      // a dense window is cut without water
  loadTrajectory();
};

/* Water: one button for the two waters a viewer trajectory can hold. A run that carries a metal
   keeps the waters nearest its sites in the view itself (the analysis job's second pass); any
   other run's view is stripped, and the first press cuts the waters nearest the partner into a
   view of their own (hopemd/analysis/waters.py). What is shown, and what is not, is said in the
   tip beside the button rather than left to be found out. */
let WANT_WATERS = false;
function waterView(ready) {
  const v = WATERS && (WATERS.views || []).find(w => +w.rep === REP);
  return v && (!ready || v.ready) ? v : null;
}
function drawWaterPill() {
  const b = $('vwaterR'), tip = $('vwatertip');
  if (!b) return;
  const v = waterView(false), n = (WATERS && WATERS.nearest) || 30, near = (WATERS && WATERS.near) || 'the partner';
  const site = RV.waters && !(v && v.ready && RV.file === v.file);
  const waiting = !site && v && !v.ready && v.state && v.state !== 'gone' && !/FAIL|CANCEL|TIMEOUT/i.test(v.state);
  const on = !!(RV.waters && RV.show.wat);
  b.textContent = waiting ? 'Water · ' + String(v.state).toLowerCase() : 'Water';
  b.classList.toggle('on', on);
  b.setAttribute('aria-pressed', on ? 'true' : 'false');
  const limits = 'not the whole box. They are picked again every frame, so no one water is followed through time.';
  const held = RV.ions && RV.ions.length ? 'the metal sites' : 'the interface';
  tip.textContent = site ? `The ${RV.nwat || 'few'} waters nearest ${held} in each frame, ${limits}`
    : `The ${n} waters nearest ${near} in each frame, ${limits}`
      + (v && v.ready ? '' : waiting ? ' The job that cuts them is ' + String(v.state).toLowerCase() + '.'
                                     : ' The first press queues a short cpptraj job to cut them.');
}
$('vwaterR').onclick = async () => {
  if (!CURRENT) return;
  const v = waterView(false);
  if (RV.waters) {
    // the view on the screen holds water: shown or hidden; the partner's, a view of its own, is
    // put away for the stripped one, so the label says what is on the screen
    if (RV.show.wat && v && v.ready && RV.file === v.file) { WANT_WATERS = false; drawWaterPill(); loadTrajectory(); return; }
    RV.show.wat = !RV.show.wat;
    drawWaterPill();
    styleRun();
    return;
  }
  if (v && v.ready) {
    WANT_WATERS = true; RV.show.wat = true;
    if (VIEWFILE) { VIEWFILE = ''; $('viewpick').value = ''; }   // water is on the strided view
    loadTrajectory();
    return;
  }
  if (v && v.state && v.state !== 'gone' && !/FAIL|CANCEL|TIMEOUT/i.test(v.state)) {
    $('logtail').hidden = false;
    $('logtail').textContent = `the waters job ${v.job} is ${String(v.state).toLowerCase()}: Water turns on once it has written ${v.file}`;
    return;
  }
  const r = await api('/api/waters', {name: CURRENT, rep: REP});
  $('logtail').hidden = false;
  $('logtail').textContent = r.error ? r.error : `waters job ${r.job}: the ${r.nearest} waters nearest ${r.near} in each of ${r.frames} frames → ${r.output}`;
  if (!r.error) { WANT_WATERS = true; RV.show.wat = true; }
  selectRun(CURRENT);
};
$('dense').onclick = async () => {
  if (!CURRENT || !FRAMES) return;
  const ns = +((+$('frame').value) * frameNs());
  const r = await api('/api/dense', {name: CURRENT, rep: REP, ns: ns});
  $('logtail').hidden = false; $('logtail').textContent = r.error ? r.error : `dense window job ${r.job}: ${r.ns0}–${r.ns1} ns, ${r.frames} frames → ${r.output}`;
  selectRun(CURRENT);
};
let FRAME_NS = 0;                           // ns per frame of what is loaded
/* the analysis job's own viewer_stride, which a run may have written at something other than
   20: a frame number is turned into ns with it, and reading it as 20 put every frame of a
   run strided by 40 at half the time it was taken */
const viewStride = () => (SUMMARY && SUMMARY.view_stride) || 20;
const frameNs = () => FRAME_NS || (SUMMARY ? SUMMARY.save_ps * viewStride() / 1000 : 0.4);
async function loadTrajectory() {
  if (PLAYING) { clearInterval(PLAYING); PLAYING = null; }
  const r = SUMMARY && SUMMARY.replicates[String(REP)];
  const s = RUNSPEC || SPEC;
  if (!r || !r.view || typeof $3Dmol === 'undefined') { $('vlabelR').textContent = 'The trajectory appears after the analysis job'; $('frame').max = 0; RV.file = ''; return; }
  const dense = DENSE.find(w => w.ready && w.file === VIEWFILE && +w.rep === REP);
  const wet = !dense && WANT_WATERS ? waterView(true) : null;
  const path = dense ? dense.file : wet ? wet.file : r.view;
  FRAME_NS = dense ? (dense.half_ns * 2 / Math.max(1, dense.frames - 1)) : SUMMARY.save_ps * viewStride() / 1000;
  $('vlabelR').textContent = 'Reading r' + REP + (dense ? ', dense window' : '') + '…';
  const pdb = await api('/api/file?name=' + encodeURIComponent(CURRENT) + '&path=' + encodeURIComponent(path));
  if (!VR) VR = $3Dmol.createViewer($('viewerR'), {backgroundColor: cssVar('--card') || 'white'});
  VR.removeAllModels();
  const m = VR.addModelsAsFrames(pdb.text, 'pdb');
  FRAMES = m.getNumFrames ? m.getNumFrames() : (pdb.text.match(/^MODEL/gm) || []).length;
  RV.rec = s.system.receptor_residues || null; RV.lig = s.system.ligand_residues || null; RV.parts = PARTS;
  RV.file = path; RV.hash = textHash(pdb.text || '');
  // which ions and whether there is any water: read off the frames themselves, not off the spec,
  // so the legend names what is on the screen and nothing else
  const resn = new Set((pdb.text || '').split('\n').filter(L => L.startsWith('ATOM') || L.startsWith('HETATM')).map(L => L.slice(17, 20).trim()));
  RV.ions = [...resn].filter(r => METAL_NAME[ionKey(r)]).sort();
  RV.waters = [...resn].some(r => WATER_RESN.includes(ionKey(r)));
  const first = (pdb.text || '').split(/^ENDMDL/m)[0];
  RV.nwat = new Set(first.split('\n').filter(L => (L.startsWith('ATOM') || L.startsWith('HETATM'))
    && WATER_RESN.includes(ionKey(L.slice(17, 20).trim()))).map(L => L.slice(21, 27))).size;
  drawWaterPill();
  // the first frame is shown, and the surfaces are that frame's
  $('frame').max = Math.max(0, FRAMES - 1); $('frame').value = 0;
  VR.setFrame(0);
  styleRun();
  VR.zoomTo(); VR.render();
  frameInfo(0);
  const what = dense ? `r${REP} · dense ${dense.centre_ns - dense.half_ns}–${dense.centre_ns + dense.half_ns} ns · ${Math.round(FRAME_NS * 1000)} ps between frames`
                     : `r${REP} · ${wet ? `the ${wet.nearest} waters nearest ${(WATERS && WATERS.near) || 'the partner'}` : RV.waters ? `solute and the water at ${RV.ions.length ? 'the metal sites' : 'the interface'}` : 'stripped'} · every ${SUMMARY.save_ps * viewStride()} ps`;
  // the legend: each ion on the screen, in the color it is drawn, so nothing is read off a guess
  $('vlabelR').innerHTML = esc(what) + RV.ions.map(r =>
    `<span class="vkey"><i style="background:${esc(METAL_COLOR[ionKey(r)] || '')}"></i>${esc(METAL_NAME[ionKey(r)])}</span>`).join('')
    + (RV.waters ? '<span class="vkey"><i style="background:#d62728"></i>water O</span>' : '');
}
function frameInfo(i) {
  const dense = DENSE.find(w => w.ready && w.file === VIEWFILE && +w.rep === REP);
  const ns = dense ? (dense.centre_ns - dense.half_ns + i * FRAME_NS).toFixed(2) : (i * frameNs()).toFixed(1);
  $('frameinfo').textContent = `frame ${i + 1} / ${FRAMES} · ${ns} ns`;
}
$('frame').oninput = () => { const i = +$('frame').value; if (VR) { VR.setFrame(i); VR.render(); } frameInfo(i); };
$('play').onclick = () => {
  if (PLAYING) { clearInterval(PLAYING); PLAYING = null; $('play').textContent = '▶'; return; }
  if (!FRAMES) return; $('play').textContent = '❚❚';
  PLAYING = setInterval(() => { let i = (+$('frame').value + 1) % FRAMES; $('frame').value = i; VR.setFrame(i); VR.render(); frameInfo(i); }, 80);
};
$('rescan').onclick = loadRuns;
$('runsbrowse').onclick = () => HLPicker.open(pickerOptions({
  title: 'Open a runs folder', key: 'hopemd-runs', folders: true, useLabel: 'Open this folder',
  start: $('runsroot2').textContent,
  onFolder: async p => {
    const r = await api('/api/runs_root', {path: p});
    if (r.error) throw new Error(r.error);
    CURRENT = null; RUNS = []; $('progress').textContent = 'Pick a run'; $('tiles').innerHTML = ''; $('runname').textContent = '—';
    $('sitenote').textContent = SITE.name + ' · runs in ' + r.root + (r.writable ? '' : ' · read-only') + (SITE.dry_run ? ' · dry run' : '');
    await loadRuns();
  },
}));
$('showlog').onclick = () => { $('logtail').hidden = !$('logtail').hidden; if ($('logtail').hidden) stopFollow(); };
let LOGS = [], LOGTAIL = '';
$('logpick').onchange = () => {
  const v = $('logpick').value;
  if (!v) { stopFollow(); $('logtail').hidden = false; $('logtail').textContent = LOGTAIL || 'No logs yet.'; return; }
  const [key, task, job] = v.split('|');
  followStage(key, +task, job);
};

/* ---- follow one stage: its log tail, refreshed while the job runs ---- */
let STAGE_VIEW = null, STAGE_TIMER = null;
function stopFollow() { if (STAGE_TIMER) { clearInterval(STAGE_TIMER); STAGE_TIMER = null; } STAGE_VIEW = null; document.querySelectorAll('.stg.on').forEach(e => e.classList.remove('on')); }
async function followStage(key, task, job) {
  if (STAGE_VIEW && STAGE_VIEW.key === key && STAGE_VIEW.task === task && STAGE_VIEW.job === (job || '')) { stopFollow(); $('logtail').textContent = ''; $('logtail').hidden = true; $('logpick').value = ''; return; }
  stopFollow(); STAGE_VIEW = {key, task, job: job || '', run: CURRENT};
  if (!job) $('logpick').value = '';
  document.querySelectorAll('.stg').forEach(e => e.classList.toggle('on', !job && e.dataset.stage === key && +e.dataset.task === task));
  $('logtail').hidden = false;
  await refreshStage();
  STAGE_TIMER = setInterval(refreshStage, 5000);
}
async function refreshStage() {
  if (!STAGE_VIEW || STAGE_VIEW.run !== CURRENT) { stopFollow(); return; }
  const d = await api('/api/stage_log?name=' + encodeURIComponent(CURRENT) + '&stage=' + encodeURIComponent(STAGE_VIEW.key) + '&task=' + STAGE_VIEW.task + (STAGE_VIEW.job ? '&job=' + STAGE_VIEW.job : ''));
  if (d.error) { $('logtail').textContent = d.error; stopFollow(); return; }
  const head = `${STAGE_VIEW.key}${STAGE_VIEW.task > 1 || /equil|prod|mmgbsa/.test(STAGE_VIEW.key) ? ' r' + STAGE_VIEW.task : ''} · job ${d.job || '—'} · ${(d.state || 'not in the queue').toLowerCase()}${d.node ? ' on ' + d.node : ''}${d.elapsed ? ' · ' + d.elapsed + ' elapsed' : ''}${d.file ? ' · ' + d.file : ''}${d.done ? '' : ' · following, every 5 s'}`;
  const pane = $('logtail'); const atBottom = pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 8;
  pane.textContent = head + '\n' + (d.lines && d.lines.length ? d.lines.join('\n') : '(no log written yet)');
  if (atBottom) pane.scrollTop = pane.scrollHeight;
  if (d.done && STAGE_TIMER) { clearInterval(STAGE_TIMER); STAGE_TIMER = null; }
}
function drawVideos(v) {
  const box = $('videobox'); const keys = Object.keys(v);
  if (!keys.length) { box.hidden = true; box.innerHTML = ''; return; }
  box.hidden = false;
  const media = f => '/api/media?name=' + encodeURIComponent(CURRENT) + '&path=' + encodeURIComponent(f) + '&t=' + encodeURIComponent(T);
  const one = (k, f, mb, label) => f ? `<div><b>${esc(k)}</b> · ${esc(label)} · ${mb} MB · <a href="${media(f)}" target="_blank">open the video</a></div>`
    + `<video controls preload="metadata" style="width:100%;max-height:320px;border-radius:8px;margin:4px 0 8px;background:#000" src="${media(f)}"></video>` : '';
  box.innerHTML = keys.sort().map(k => {
    const x = v[k];
    // the waiting line belongs to a job that exists: a film put in the folder by hand has none,
    // and announcing a queued job for it said a second video was coming that nobody had asked for
    const pending = !x.job || (x.file && x.water_file) ? ''
      : `<div><b>${esc(k)}</b> · video job ${esc(x.job)} · ${esc(x.state || 'queued')}${x.submitted ? ' · submitted ' + esc(x.submitted) : ''}${x.file || x.water_file ? ' · the other video is on its way' : ' · two videos: the solute alone, and in its water'}</div>`;
    return one(k, x.file, x.size_mb, 'without water') + one(k, x.water_file, x.water_size_mb, 'with water and ions') + pending;
  }).join('');
}
$('video').onclick = async () => {
  if (!CURRENT) return;
  const r = await api('/api/video', {name: CURRENT, rep: REP});
  $('logtail').hidden = false; $('logtail').textContent = r.error ? r.error : ((r.dry_run ? 'DRY RUN — nothing queued\n' : '') + 'video job ' + r.job + ' for r' + REP + ' → ' + r.output + '\nfiles: ' + r.files.join(', '));
  selectRun(CURRENT);
};
let RUN_SPEC = null;
function mmgbsaCount() {
  if (!RUN_SPEC) return;
  const s = JSON.parse(JSON.stringify(RUN_SPEC));
  s.analysis = Object.assign({}, s.analysis, {mmgbsa: true, mmgbsa_from_ns: +$('mmfrom').value || 0, mmgbsa_to_ns: +$('mmto').value || 0, mmgbsa_stride_ps: +$('mmevery').value || 200});
  const w = mmgbsaWindow(s); $('mmcount').textContent = `= ${w.frames} frames per replicate`;
}
$('mmgbsabtn').onclick = () => {
  if (!CURRENT || !RUN_SPEC) return;
  const f = $('mmgbsaform'); f.hidden = !f.hidden;
  if (!f.hidden) {
    const a = RUN_SPEC.analysis || {};
    $('mmfrom').value = a.mmgbsa_from_ns || 0; $('mmto').value = a.mmgbsa_to_ns || 0; $('mmevery').value = a.mmgbsa_stride_ps || 200;
    if (RUN_SPEC.system.ligand_class === 'none') { $('logtail').hidden = false; $('logtail').textContent = 'An apo run has no partner or ligand to score.'; }
    mmgbsaCount();
  }
};
['mmfrom', 'mmto', 'mmevery'].forEach(id => $(id).addEventListener('input', mmgbsaCount));
$('mmgo').onclick = async () => {
  if (!CURRENT) return;
  const r = await api('/api/mmgbsa', {name: CURRENT, from_ns: +$('mmfrom').value || 0, to_ns: +$('mmto').value || 0, stride_ps: +$('mmevery').value || 200});
  $('logtail').hidden = false;
  $('logtail').textContent = r.error ? r.error : ((r.dry_run ? 'DRY RUN — nothing queued\n' : '') + 'MM-GBSA queued: ' + r.log.join('\n') + '\nwindow ' + r.window.from_ns + '–' + (r.window.to_ns || 'end') + ' ns every ' + r.window.stride_ps + ' ps; the result joins the tiles when the job is done');
  if (!r.error) { $('mmgbsaform').hidden = true; setTimeout(() => selectRun(CURRENT), 1500); }
};
$('tidy').onclick = async () => {
  if (!CURRENT) return;
  const undo = $('tidy').dataset.undo === '1';
  const r = await api('/api/tidy', {name: CURRENT, undo});
  $('logtail').hidden = false; $('logtail').textContent = r.error ? r.error : ((r.dry_run ? 'DRY RUN — nothing queued\n' : '') + (undo ? 'unpacking queued' : 'tidy queued') + ' as job ' + r.job + '; the ' + r.key + ' stage in the rail follows it');
  if (!r.error) setTimeout(() => selectRun(CURRENT), 1500);
};
function drawTidy(t) {
  const b = $('tidy'); if (!b) return;
  const packed = t && t.archives;
  b.dataset.undo = packed ? '1' : '0';
  b.textContent = packed ? 'Undo tidy' : 'Tidy';
  b.title = packed ? `${t.packed} file(s) are packed in archive/ (${t.inodes_before} -> ${t.inodes_after} files, ${t.last}); Undo puts them back with a short queue job` : b.title;
}
$('report').onclick = () => { if (CURRENT) window.open('/report?name=' + encodeURIComponent(CURRENT) + '&t=' + encodeURIComponent(T) + '&print=1', '_blank'); };
$('resubmit').onclick = async () => {
  if (!CURRENT || !$('refrom').value) return;
  const r = await api('/api/resubmit', {name: CURRENT, from: $('refrom').value});
  $('logtail').hidden = false; $('logtail').textContent = r.error ? r.error : ((r.dry_run ? 'DRY RUN — nothing queued\n' : '') + 'resubmitted ' + r.resubmitted.join(', ') + '\n' + r.log.join('\n'));
  selectRun(CURRENT);
};
$('cancel').onclick = async () => { if (!CURRENT) return; const r = await api('/api/cancel', {name: CURRENT}); $('logtail').hidden = false; $('logtail').textContent = (r.log || [r.error]).join('\n'); selectRun(CURRENT); };
$('runagain').onclick = async () => {
  if (!CURRENT) return;
  const d = await api('/api/run?name=' + encodeURIComponent(CURRENT));
  if (d.error) return;
  SPEC = d.spec; STAGING = null; PICKED = null; SPEC.name = ''; NAME_AUTO = true; SPEC.compute.gpu_chosen = ''; showScreen('setup'); renderForm(); refresh();
  $('subnote').textContent = 'Settings copied from ' + CURRENT + '. Choose the structure again, then Submit.';
};

/* ------------------------------------------------------------------ screens + boot */
function showScreen(k) {
  $('screen-setup').hidden = k !== 'setup'; $('screen-runs').hidden = k !== 'runs';
  $('tab-setup').classList.toggle('on', k === 'setup'); $('tab-runs').classList.toggle('on', k === 'runs');
  if (k === 'runs') loadRuns();
  if (k === 'setup' && V) { V.resize(); V.render(); }
  if (k === 'runs' && VR) { VR.resize(); VR.render(); }
}
document.querySelectorAll('.tab').forEach(b => b.onclick = () => showScreen(b.dataset.screen));

/* The rails are as wide as the text asks for (--railw in the stylesheet), so they grow with the
   text-size control instead of being dragged to a width that the next step would break. */
/* The text-size control grows the rails and the cards, so the viewers' boxes change width: the
   canvases are told, or what they draw and where the mouse thinks it is stop agreeing. */
window.addEventListener('hl-zoom', () => {
  requestAnimationFrame(() => {
    if (V) { V.resize(); V.render(); }
    if (VR) { VR.resize(); VR.render(); }
    // the labels on the canvas and the names inside the diagram are drawn at a size, not
    // styled at one, so they are drawn again when the size changes
    try { if (V && STRUCT) styleStructure(false); } catch (e) {}
    try { if (VR && CURRENT) styleRun(); } catch (e) {}
  });
});
if (window.ResizeObserver) {          // the 3D canvases follow their boxes, whatever resized them
  const ro = new ResizeObserver(() => { if (V && !$('screen-setup').hidden) { V.resize(); V.render(); } if (VR && !$('screen-runs').hidden) { VR.resize(); VR.render(); } });
  ro.observe($('viewer')); ro.observe($('viewerR'));
}

(async function boot() {
  const d = await api('/api/site');
  if (d.error || d.status === 403) { document.body.innerHTML = '<p style="padding:20px">' + esc(d.error || d.text) + '</p>'; return; }
  SITE = d; SPEC = d.default_spec; if (d.source_kinds) KINDS = d.source_kinds;
  // The cluster and whether anything will be queued. Where the runs go is in the rail, under
  // the box the run is named in, and said once.
  $('sitenote').textContent = d.name + (d.dry_run ? ' · dry run: nothing is queued' : '');
  $('sitenote').title = 'Runs go in ' + d.runs_root;
  // The bar along the bottom carries the build string and one line of copyright: that is what
  // fits on one line, and all of it is readable. The five sentences naming whose tools do the
  // work, and the copyright in full, are behind Credits, which is beside it.
  const build = 'HOPE-MD ' + d.version + (d.commit ? ' (' + d.commit + ')' : '');
  $('credittext').textContent = build + (d.copyright_short ? ' · ' + d.copyright_short : '');
  $('creditbar').title = build;
  $('citelink').onclick = e => {
    e.preventDefault();
    $('citebuild').textContent = build + ' · ' + (SITE ? SITE.name : '');
    $('citecredits').innerHTML = (d.credits || []).map(c => '<li>' + esc(c) + '</li>').join('');
    $('citetext').textContent = d.how_to_cite || ''; $('citeack').textContent = d.acknowledge || ''; $('citecopy').textContent = d.copyright || '';
    // what a figure from the viewer cites: the viewer that drew it, and MSMS for a surface
    $('citeviewer').innerHTML = (d.viewer_references || []).map(r => '<li><b>' + esc(r.name) + '</b>, for ' + esc(r.when) + ': ' + esc(r.reference)
      + ' <a href="' + esc(encodeURI('https://doi.org/' + r.doi)) + '" target="_blank" rel="noopener">doi:' + esc(r.doi) + '</a></li>').join('');
    if (!$('citebox').open) $('citebox').showModal();
  };
  $('citeclose').onclick = $('citex').onclick = () => $('citebox').close();
  $('submit').onclick = submit;
  renderForm(); await refresh(); loadRuns();
  // Opened on a run, when HOPE Labs' Results asked for one: the Runs screen with that run shown.
  // Runs are chosen here by name, so a folder handed over is reduced to its last part.
  const asked = new URLSearchParams(location.search).get('open');
  if (asked) { showScreen('runs'); selectRun(asked.split('/').filter(Boolean).pop()); }
  setInterval(() => { if (!$('screen-runs').hidden && CURRENT) selectRun(CURRENT); }, 30000);
})();
