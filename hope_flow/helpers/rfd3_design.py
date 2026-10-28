#!/usr/bin/env python
"""Design binders with RFdiffusion3, give them sequences with MPNN, and record what came out.

Run by RFdiffusion3's own Python (3.12, the rfd3 environment), inside a GPU job, not by the
flow's. The sibling helper for RFdiffusion is written for 3.9 because SE3nv is 3.9; this one is
not held to that, and the two cannot share an environment for the same reason.

    rfd3_design.py <plan.json>

The plan is what the driver worked out; the record left behind is <out>/designs.json, in the
shape hope_flow/adapters.py documents.

WHAT THIS BUILDS, AND WHY IT LOOKS NOTHING LIKE RFDIFFUSION'S
RFdiffusion is driven by command-line overrides. RFdiffusion3 is driven by an input JSON whose
central field is a contig string, and the whole of binder design is in three of its keys:

    contig               "70-100,/0,A17-131" - the binder's length range, a chain break, then
                         the target chain and the residues of it to keep. A target with gaps in
                         its chain takes a break at each of them, because a comma between two
                         stretches asks for a peptide bond across the hole
    select_hotspots      the residues the binder has to reach. The field takes atoms or whole
                         residues; a target card carries residue numbers, so whole residues is
                         what is sent, as "ALL"
    infer_ori_strategy   "hotspots", which puts the origin token 10 A out from the hotspots'
                         center of mass, so the binder grows off the face that was asked for
                         rather than wherever the target's center happens to be

A target with no hotspots is a real case - somebody may want a binder anywhere on it - so the
strategy falls back to "com" and no hotspots are sent, rather than the run failing.

ON THE SEQUENCE, AND WHOSE ProteinMPNN THIS IS
RFD3 writes a sequence of its own, and for RNA that sequence is the answer. For proteins the
authors' own pipeline redesigns the backbone with MPNN before anything is ordered, so that is
what happens here too - but with the lab's existing ProteinMPNN, in SE3nv, the one the
RFdiffusion helper beside this has always used.

Not the MPNN that ships in this package, though it is right there. Its own README asks that the
old repositories be used for anything being compared, because the re-implementation has not
been validated against them yet, and it wants a separate set of weights this install does not
have. A proven sequence designer we already run is worth more than a second copy of an
unvalidated one.

The RFD3 sequence is kept in the record beside ProteinMPNN's rather than thrown away: they are
two different claims about the same backbone, and the difference is worth being able to see.
"""
import glob
import gzip
import json
import os
import re
import shutil
import subprocess
import sys


#: The chain the binder comes back as. RFdiffusion3 letters the chains it assembles from A in
#: contig order and the contig puts the designed segment first, so the binder is A whatever the
#: target's own chains were called - measured on 1YCR, where chain A is 61 residues of design and
#: chain B is MDM2's 85, which went in as A25-109. The same letter is what cyclic_chains has to
#: name, since that field names a chain of the assembly rather than of the input file.
BINDER_CHAIN = "A"


def say(*bits):
    print(*bits, flush=True)


def run(cmd, where=None, env=None):
    """Run a command, letting its output through. Raises on a non-zero exit."""
    say("  $", " ".join(cmd[:6]), "..." if len(cmd) > 6 else "")
    r = subprocess.run(cmd, cwd=where, env=env)
    if r.returncode:
        raise SystemExit("%s exited %d" % (os.path.basename(cmd[0]), r.returncode))


#: The twenty, for reading a structure without a library. The page that previews a target runs
#: in the launcher's own environment, which has no biotite, so anything it calls has to manage
#: on the standard library alone.
AMINO = {"ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q", "GLU": "E",
         "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F",
         "PRO": "P", "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V"}
#: A residue a preparation names by its state, read as the residue it is.
STATE_NAMES = {"HID": "HIS", "HIE": "HIS", "HIP": "HIS", "HSD": "HIS", "HSE": "HIS", "HSP": "HIS",
               "CYX": "CYS", "CYM": "CYS", "ASH": "ASP", "GLH": "GLU", "LYN": "LYS"}


def chains_of(path):
    """{chain: [residue numbers]} for the amino acids of a PDB, in file order.

    The design page reads this to show what is in a target and to check that the chains somebody
    named are really there, so every tool it serves offers it under this name. Written against
    PDB columns and nothing else, exactly as the sibling helpers are, because the page's own
    environment has no structure library in it. A residue is its number and its insertion code,
    so 60, 60A and 60B are three; a prepared structure's HID or CYX is the residue it names.
    """
    found, seen = {}, set()
    with open(path, errors="replace") as fh:
        for line in fh:
            if not line.startswith("ATOM") or line[12:16].strip() != "CA":
                continue
            name = line[17:20].strip()
            if STATE_NAMES.get(name, name) not in AMINO:
                continue
            chain = line[21].strip() or "A"
            try:
                number = int(line[22:26])
            except ValueError:
                continue
            key = (chain, line[22:27])
            if key in seen:
                continue
            seen.add(key)
            found.setdefault(chain, []).append(number)
    return found


def segments(numbers):
    """[(first, last), ...] - one span per unbroken run of residue numbers in one chain.

    A number repeated is the same residue under an insertion code (60, 60A, 60B), so it continues
    the run rather than breaking it.
    """
    out = []
    for n in numbers:
        if out and n in (out[-1][1], out[-1][1] + 1):
            out[-1][1] = n
            continue
        out.append([n, n])
    return [(a, b) for a, b in out]


def spans_of(path, chains):
    """{chain: [(first, last), ...]} - the stretches of resolved residues in each chain.

    The contig has to name ranges - "A17-131" - and a bare chain letter is refused. So they are
    read off the structure rather than assumed to start at 1: a cropped PDB keeps its original
    numbering, and guessing would design against the wrong residues while looking perfectly well
    formed.

    One span per stretch, not one per chain. RFD3 expands a span to every residue number in it
    (components.py, get_design_pattern_with_constraints) and then looks each one up in the input
    (parsing.py, fetch_mask_from_idx), so a chain written first to last claims residues nobody
    could see in the density and the run stops on the first of them. Nearly every crystal
    structure has such holes, and the design tools are given the cleaned receptor, which keeps
    them: 7A3R's chain A is six stretches, not one.
    """
    from biotite.structure.io.pdbx import CIFFile, get_structure as cif_structure
    from biotite.structure.io.pdb import PDBFile
    if path.lower().endswith((".cif", ".mmcif", ".cif.gz")):
        arr = cif_structure(CIFFile.read(path), model=1)
    else:
        arr = PDBFile.read(path).get_structure(model=1)
    has_codes = "ins_code" in arr.get_annotation_categories()
    out = {}
    for c in chains:
        here = arr[arr.chain_id == c]
        numbers, last = [], None
        for i in range(here.array_length()):
            key = (int(here.res_id[i]), str(here.ins_code[i]) if has_codes else "")
            if key == last:
                continue
            last = key
            numbers.append(key[0])
        if numbers:
            out[c] = segments(numbers)
    return out


def residues_of(spans):
    """{chain: {residue numbers}} for the residues the spans actually cover."""
    return {c: {n for a, b in runs for n in range(a, b + 1)} for c, runs in spans.items()}


def contig_of(plan, target_chains, spans):
    """The contig: binder length range, a chain break, then the target's resolved residues.

    EVERY STRETCH DIVIDED BY "/0", AND EVERY CHAIN TOO
    "/0" is the only break RFD3's contig grammar has: it says there is no peptide bond across it.
    Two components written side by side with a comma are bonded - the docs are explicit, "a bond
    will be created between A170 and A203" - and RFD3 renumbers them consecutively, so the model
    is told the backbone runs straight through a hole where it does not. Unlike RFdiffusion, whose
    grammar can state the real size of a gap, RFD3's choice is a bond or a break, and for residues
    nobody could see a break is the true one.

    Chains take the break for the same reason. Comma-joined, two target chains were fused into one
    output chain with a bond between the end of the first and the start of the second.
    """
    lo = max(1, int(plan.get("binder_min") or 70))
    hi = max(lo, int(plan.get("binder_max") or 100))
    kept = ["%s%d-%d" % (c, a, b) for c in target_chains if c in spans for a, b in spans[c]]
    if not kept:
        raise SystemExit("none of the target chains %s are in %s"
                         % (",".join(target_chains), plan["target"]))
    # RFD3 letters the output chains from A, one per break, so the binder and twenty-five
    # stretches are all the alphabet holds. A target in more pieces than that wants cropping to
    # the site first, which is what its authors ask for anyway.
    if len(kept) > 25:
        raise SystemExit("the target is in %d stretches of resolved residues, more than the "
                         "chain letters RFdiffusion3 has for them. Crop it to the site first."
                         % len(kept))
    return "%d-%d,/0,%s" % (lo, hi, ",/0,".join(kept))


def hotspots_of(plan):
    """{residue: "ALL"} from the target card's hotspots, or {} when it named none.

    The card takes them as 54,56,66-70 or A54,B12-16, and A:54 is read too. RFD3 names each
    residue it is to reach by its chain and number, A54, and has no range form, so a residue with
    no chain is put on the target's first chain and a range is spelled out; an insertion code stays
    on its residue, H60A. A bare 54 sent as it was would be no residue of the target at all.
    """
    first = ([c.strip() for c in str(plan.get("chains") or "A").split(",") if c.strip()]
             or ["A"])[0]
    out = {}
    for bit in re.split(r"[,;\s]+", str(plan.get("hotspots") or "")):
        if not bit:
            continue
        found = re.match(r"^([A-Za-z]?):?(\d+)(?:([A-Za-z])|-(\d+))?$", bit)
        if not found:
            say("  hotspot %r is not a residue as the target card writes one; left out" % bit)
            continue
        chain, low = found.group(1) or first, int(found.group(2))
        if found.group(3):
            out["%s%d%s" % (chain, low, found.group(3).upper())] = "ALL"
            continue
        for n in range(low, int(found.group(4) or low) + 1):
            out["%s%d" % (chain, n)] = "ALL"
    return out


def absent(hot, spans):
    """([the hotspots the target has no residue for], [why each of them is not one]).

    What refuse_missing refuses, worked out without raising it, because the design page asks the
    same question of a box somebody is still typing in. One reading of the structure and one set
    of words, so the page says what the job would say rather than nearly it.
    """
    have = residues_of(spans)
    names, told = [], []
    for name in sorted(hot):
        found = re.match(r"^([A-Za-z])(\d+)([A-Za-z]?)$", name)
        if not found:
            continue
        chain, n = found.group(1), int(found.group(2))
        if chain not in have:
            names.append(name)
            told.append("%s: the target has no chain %s (it has %s)"
                        % (name, chain, ", ".join(sorted(have)) or "none"))
        elif n not in have[chain]:
            runs = spans[chain]
            where = ("a gap in the chain" if runs[0][0] < n < runs[-1][1]
                     else "outside the chain, which is numbered %d to %d"
                          % (runs[0][0], runs[-1][1]))
            names.append(name)
            told.append("%s: %s has no residue %d, it is %s" % (name, chain, n, where))
    return names, told


def refuse_missing(hot, spans):
    """Refuse any hotspot the target has no residue for, naming it.

    RFD3 looks a hotspot up by chain and number and raises on one it cannot find, which is an
    error from the depths of its input parser about a residue nobody asked after. Said here, it is
    about the box the number was typed into - and it is said before the card is asked for.
    """
    told = absent(hot, spans)[1]
    if told:
        raise SystemExit("these hotspots are not residues of the target, so nothing was run:\n  "
                         + "\n  ".join(told))


def write_input(plan, where, target_chains, spans):
    """The one JSON RFD3 is driven by, written into the run folder so it can be read back."""
    spec = {
        "dialect": 2,
        "input": plan["target"],
        "contig": contig_of(plan, target_chains, spans),
        # Fewer loops. The authors expose this as the anti-loop flag and their own binder
        # examples set it; a loopier binder is the one that fails to fold. On unless the card
        # turns it off, which is how it was hardcoded here.
        "is_non_loopy": plan.get("non_loopy") is not False,
    }
    # A cyclic binder: the ends of the designed chain joined, by cyclic positional encoding.
    # RFD3 takes this for one complete de novo canonical peptide in dialect 2, and the binder is
    # that chain - it is the first block of the contig, and RFD3 letters the assembled chains
    # from A in contig order, so the binder is A whatever the target's own chains are called.
    if plan.get("cyclic"):
        spec["cyclic_chains"] = [BINDER_CHAIN]
    hot = hotspots_of(plan)
    if hot:
        refuse_missing(hot, spans)
        spec["select_hotspots"] = hot
        spec["infer_ori_strategy"] = "hotspots"
    else:
        # No hotspots named: design anywhere on the target rather than refuse the run.
        spec["infer_ori_strategy"] = "com"
        say("  no hotspots on the target, so the binder may grow anywhere on it")
    path = os.path.join(where, "rfd3_input.json")
    with open(path, "w") as fh:
        json.dump({"design": spec}, fh, indent=1)
    return path


def design_command(plan, designs_dir, spec):
    """The command line RFdiffusion3 is given, as a list.

    Its own function so it can be read back without a structure to parse: the rest of this
    helper reads one through biotite, which lives in RFdiffusion3's environment and nowhere
    else, and the command line is the part a test has to be able to check anywhere.
    """
    cmd = [plan["rfd3"], "design",
           "out_dir=%s" % designs_dir,
           "inputs=%s" % spec,
           # The engine's config carries "# @package _global_", so its keys sit at the top
           # level and an "inference_engine." prefix is refused outright by hydra.
           "diffusion_batch_size=%d" % int(plan["designs"]),
           "inference_sampler.num_timesteps=%d" % int(plan["num_timesteps"]),
           "inference_sampler.step_scale=%s" % plan["step_scale"],
           # The rest of the sampler's own knobs, each at its configured figure unless the card
           # moved it. noise_scale is the noise put back at each step and gamma_0 is how much of
           # the step is re-noised before being denoised again; the engine's own config names
           # 0.0 for gamma as plain-equation sampling.
           "inference_sampler.noise_scale=%s" % plan.get("noise_scale", 1.003),
           "inference_sampler.gamma_0=%s" % plan.get("gamma_0", 0.6),
           # A campaign larger than one pass. diffusion_batch_size is what a card holds at once
           # and n_batches is how many times round, so the run writes the product of the two.
           "n_batches=%d" % max(1, int(plan.get("batches") or 1)),
           "skip_existing=False", "prevalidate_inputs=True"]
    if plan.get("cfg"):
        cmd += ["inference_sampler.use_classifier_free_guidance=True",
                # Left at 1 the sampler skips the guidance altogether, so the strength is sent
                # beside the switch rather than being a second thing to remember.
                "inference_sampler.cfg_scale=%s" % plan.get("cfg_strength", 1.5)]
    if plan.get("seed") not in (None, ""):
        cmd.append("seed=%d" % int(plan["seed"]))
    return cmd


def unpack(path):
    """RFD3 writes .cif.gz. Unpack it beside itself and return the plain file."""
    if not path.endswith(".gz"):
        return path
    plain = path[:-3]
    with gzip.open(path, "rb") as src, open(plain, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return plain


def chains_and_sequence(cif, binder_chain):
    """(every chain in the file, the binder's one-letter sequence) from a CIF.

    Read with biotite, which the environment already has because atomworks depends on it, rather
    than by parsing the file here: a hand-rolled CIF reader is a bug waiting for the first
    structure with an unusual chain label.
    """
    from biotite.structure.io.pdbx import CIFFile, get_structure
    from biotite.structure import residues as _res
    arr = get_structure(CIFFile.read(cif), model=1)
    chains = sorted(set(str(c) for c in arr.chain_id))
    three_to_one = {
        "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q", "GLU": "E",
        "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F",
        "PRO": "P", "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V"}
    seq = ""
    want = arr[arr.chain_id == binder_chain] if binder_chain in chains else arr
    seen = set()
    for atom in want:
        key = (str(atom.chain_id), int(atom.res_id))
        if key in seen:
            continue
        seen.add(key)
        seq += three_to_one.get(str(atom.res_name).upper(), "X")
    return chains, seq


def main(plan_path):
    with open(plan_path) as fh:
        plan = json.load(fh)
    out = plan["out"]
    os.makedirs(out, exist_ok=True)
    target_chains = [c.strip() for c in str(plan.get("chains") or "A").split(",") if c.strip()]

    env = dict(os.environ)
    env["FOUNDRY_CHECKPOINT_DIRS"] = plan["checkpoints"]
    # A compute node has no outbound network. Everything this needs is already on disk, and
    # saying so turns a silent twenty-minute hang into an immediate, readable failure.
    env["HF_HUB_OFFLINE"] = "1"
    env["WANDB_MODE"] = "disabled"

    spans = spans_of(plan["target"], target_chains)
    holes = sum(len(runs) - 1 for runs in spans.values())
    if holes:
        say("  the target has %d gap(s) in its chain(s), so each stretch of resolved residues "
            "is kept on its own" % holes)
    spec = write_input(plan, out, target_chains, spans)
    designs_dir = os.path.join(out, "rfd3")
    say("RFdiffusion3: %d design(s), contig %s"
        % (plan["designs"], contig_of(plan, target_chains, spans)))
    cmd = design_command(plan, designs_dir, spec)
    run(cmd, where=plan["foundry"], env=env)

    made = sorted(glob.glob(os.path.join(designs_dir, "*.cif.gz")))
    if not made:
        raise SystemExit("RFdiffusion3 wrote no structure into %s" % designs_dir)
    say("  %d design(s) written" % len(made))

    designs = []
    for i, gz in enumerate(made):
        cif = unpack(gz)
        # WHICH CHAIN IS THE BINDER, AND WHY IT IS NOT THE ONE THE CARD NAMED.
        #
        # RFdiffusion3 relabels and renumbers everything it writes, in contig order. The contig
        # puts the designed segment first, so the design comes back as chain A and the target
        # follows as B, C, ... whatever the target was called on the way in, and renumbered from
        # 1 whatever its own numbering was. Measured on 1YCR: chain A is 61 residues of design,
        # chain B is MDM2's 85, which went in as A25-109.
        #
        # So the binder cannot be found by eliminating the card's target chains - that rule
        # picks the TARGET, and the record would hand MDM2 to the next card as a design. It is
        # the first chain, because the contig put it there.
        #
        # A target with gaps comes back under more letters still, one per stretch of resolved
        # residues, because each is its own chain in the contig. The binder is the first of them
        # whatever their number, and the rest are the target, which is what target_chains holds.
        chains, _ = chains_and_sequence(cif, "")
        binder = chains[:1]
        _, seq = chains_and_sequence(cif, binder[0])
        designs.append({
            "name": os.path.basename(cif).replace(".cif", ""),
            "path": cif,
            "binder_chains": binder,
            # Everything after the design, in contig order: the target, under whatever letters
            # RFdiffusion3 gave it rather than the ones the card used.
            "target_chains": chains[1:],
            "sequence": seq,
            "rfd3_sequence": seq,
            "score": None,
        })

    # ProteinMPNN, on the backbones RFD3 just made, in its own environment. Kept separate from
    # the diffusion above so that a failure here leaves the structures on disk and says so,
    # rather than losing the GPU hour that made them.
    n_seqs = int(plan.get("seqs_per_backbone") or 0)
    if n_seqs and plan.get("proteinmpnn") and plan.get("se3nv_python"):
        mpnn_dir = os.path.join(out, "proteinmpnn")
        for d in designs:
            try:
                _proteinmpnn(d, plan, mpnn_dir, n_seqs, env)
            except SystemExit as exc:
                say("  %s: ProteinMPNN did not run (%s); it keeps RFdiffusion3's own sequence"
                    % (d["name"], exc))

    record = {"tool": "rfd3", "designs": designs}
    with open(os.path.join(out, "designs.json"), "w") as fh:
        json.dump(record, fh, indent=1)
    say("recorded %d design(s) -> %s" % (len(designs), os.path.join(out, "designs.json")))


def _proteinmpnn(design, plan, where, n_seqs, env):
    """Redesign one backbone's binder chain, and take the best sequence by ProteinMPNN's score.

    The binder chain is named explicitly rather than left to be worked out: RFD3 renumbers and
    relabels everything, so the only reliable statement about which chain is the design is the
    one made upstream, from the contig.
    """
    mpnn = os.path.join(plan["proteinmpnn"], "mpnn_fr", "ProteinMPNN")
    folder = os.path.join(where, design["name"])
    os.makedirs(folder, exist_ok=True)
    # ProteinMPNN reads PDB, and RFD3 writes CIF, so the structure is converted on the way.
    pdb = os.path.join(folder, design["name"] + ".pdb")
    _to_pdb(design["path"], pdb)
    run([plan["se3nv_python"], os.path.join(mpnn, "protein_mpnn_run.py"),
         "--pdb_path", pdb,
         "--pdb_path_chains", design["binder_chains"][0],
         "--out_folder", folder,
         "--num_seq_per_target", str(n_seqs),
         "--sampling_temp", "0.1",
         "--seed", "37",
         "--batch_size", "1"], where=mpnn, env=env)
    # seqs/<name>.fa: the native sequence first, then the designs, each with its score in the
    # header. The best design is the one with the lowest score.
    fasta = os.path.join(folder, "seqs", design["name"] + ".fa")
    best, best_score = "", None
    header = ""
    with open(fasta, errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith(">"):
                header = line
                continue
            if not line or "score=" not in header:
                continue
            found = re.search(r"score=([\d.]+)", header)
            if not found:
                continue
            score = float(found.group(1))
            if best_score is None or score < best_score:
                best, best_score = line, score
    if best:
        design["sequence"] = best
        design["score"] = best_score


def _to_pdb(cif, pdb):
    """RFD3's CIF as a PDB, because ProteinMPNN reads PDB."""
    from biotite.structure.io.pdbx import CIFFile, get_structure
    from biotite.structure.io.pdb import PDBFile
    out = PDBFile()
    out.set_structure(get_structure(CIFFile.read(cif), model=1))
    out.write(pdb)
    return pdb


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("rfd3_design.py <plan.json>")
    main(sys.argv[1])
