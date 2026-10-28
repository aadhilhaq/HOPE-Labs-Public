#!/usr/bin/env python
"""Design binders with RFD4-Proteina and record what came out.

Run by the Python inside RFD4-Proteina's own image, in a GPU job, not by the flow's. The sibling
helpers for RFdiffusion and RFdiffusion3 run in their tools' environments for the same reason;
this one's environment is a container because that is how the tool is installed (hope_labs/images.py).

    rfd4_design.py <plan.json>

The plan is what the driver worked out; the record left behind is <out>/designs.json, in the shape
hope_flow/adapters.py documents.

WHAT THIS BUILDS, AND WHY IT IS A YAML FILE
RFD4-Proteina is driven by a condition spec: a small YAML document naming a starting structure, a
contig that says what to keep and what to design, and conditions that say what the model is told
about the parts it keeps. Binder design is four of its keys:

    input         the target. It must be a CIF: a condition spec refuses a PDB outright, because
                  a PDB does not reliably carry the bonds and formal charges the model consumes.
                  So the target is converted first, below
    contig        "70-100, /0, A17-131" - the binder's length range, a chain break, then the
                  target's own chains and the residues of them to keep
    C_CRDGRP      the target's backbone atoms, all in one coordinate group, which retains their
                  geometry relative to each other while leaving their side chains free. This is
                  what the authors ask for in a protein-protein run, in preference to C_CRD: it
                  holds the target rigid without revealing side chains the binder would then be
                  designed to mimic
    C_SEQ         the target's own sequence, which it is allowed to know
    C_HOT         the residues the binder has to reach, where any were named

A target with no hotspots is a real case - somebody may want a binder anywhere on it - so C_HOT is
left out rather than the run refused.

THE NUMBERS IN A CONTIG ARE THE TARGET'S OWN
res_id in a contig and in a selection is the input's numbering, which for a structure converted
from a PDB is the author numbering the file carries: measured here against the image itself, a PDB
whose chain A runs 17, 18, 30, 31 comes back out of the conversion as res_id 17, 18, 30, 31, and a
hotspot asked for as residue 30 is set on the residue that was 30 in the file. So the residue
numbers the target card carries are written into the contig as they are. The designed output is
renumbered from 1 per chain, which is why the record below reads the chains back off the structure
rather than assuming them.

A GAPPED CHAIN GOES IN ONE RUN, BECAUSE THE ALTERNATIVE IS REFUSED
Nearly every crystal structure has holes in it, and the design cards are handed the receptor
cleaned to its polymer residues, which keeps them. RFdiffusion3's grammar can say "same chain, no
bond across this hole" by putting a chain break between the two stretches. RFD4's cannot: a break
starts a new output chain, and two runs that both name chain A are refused outright -

    ValueError: Contig produces two separate output chains both named 'A'.

- so every stretch of one input chain is written into one run, comma separated, and the model is
told the chain runs straight through. That is not a choice between two expressible things; it is
the only one the grammar has, and it is written down here so the next person does not spend an
afternoon rediscovering the refusal.
"""
import json
import os
import re
import subprocess
import sys


#: The backbone atoms a coordinate group is set on. Backbone only, and deliberately: a group over
#: every atom of the target would condition on its side chains too, which the authors warn produces
#: binders that look like the target.
BACKBONE = "[N,CA,C,O]"


def say(*bits):
    print(*bits, flush=True)


def run(cmd, where=None, env=None):
    """Run a command, letting its output through. Raises on a non-zero exit."""
    say("  $", " ".join(cmd[:6]), "..." if len(cmd) > 6 else "")
    r = subprocess.run(cmd, cwd=where, env=env)
    if r.returncode:
        raise SystemExit("%s exited %d" % (os.path.basename(cmd[0]), r.returncode))


#: The twenty, for reading a structure without a library. The page that previews a target runs in
#: the launcher's own environment, which has no structure library, so anything it calls has to
#: manage on the standard library alone.
AMINO = {"ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q", "GLU": "E",
         "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F",
         "PRO": "P", "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V"}
#: A residue a preparation names by its state, read as the residue it is.
STATE_NAMES = {"HID": "HIS", "HIE": "HIS", "HIP": "HIS", "HSD": "HIS", "HSE": "HIS", "HSP": "HIS",
               "CYX": "CYS", "CYM": "CYS", "ASH": "ASP", "GLH": "GLU", "LYN": "LYS"}


def chains_of(path):
    """{chain: [residue numbers]} for the amino acids of a PDB, in file order.

    The design page reads this to show what is in a target and to check that the chains somebody
    named are really there, so every tool it serves offers it under this name. Written against PDB
    columns and nothing else, exactly as the sibling helpers are, because the page's own
    environment has no structure library in it. A residue is its number and its insertion code, so
    60, 60A and 60B are three; a prepared structure's HID or CYX is the residue it names.
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


def coded_why(text):
    """Why each hotspot carrying an insertion code is refused, in words for the person who typed it.

    RFD4 names a residue in a selection by chain and number - sel('A/*/[45, 46]') - and the
    bracketed field is numeric. There is nowhere in it to put the A of H60A. Worse, the conversion
    the target goes through does not survive insertion codes at all: a structure with 20 and 20A in
    one chain fails inside the standardizer, because both residues end up under res_id 20 and their
    atoms collide. So a coded hotspot is refused against the box it was typed into rather than
    dropped on the way into a run that would then aim at the residue of the same number.

    RFdiffusion and BoltzGen refuse them for their own reasons and in these same words, so the
    three pages say one thing about insertion codes.
    """
    out = []
    for bit in re.split(r"[,;\s]+", str(text or "")):
        if bit and re.match(r"^[A-Za-z]?:?\d+[A-Za-z]$", bit):
            out.append("%s carries an insertion code, which RFD4-Proteina cannot name in a "
                       "selection; crop the target or renumber it." % bit)
    return out


def contig_of(plan, target_chains, spans):
    """The contig: the binder's length range, then one run per target chain.

    THE BINDER FIRST, AND WITH NO CHAIN OF ITS OWN NAMED
    An expandable segment in a run with no preserved segment takes a fresh chain id, picked by the
    tool. So the binder is written first, on its own between the start and the first break, and
    comes back under whatever letter was free - measured on a target of chains A and B, the binder
    is C. That is the opposite of RFdiffusion3, which letters everything from A in contig order and
    gives the binder A; which is why the record below finds the binder by elimination rather than
    by taking the first chain.
    """
    lo = max(1, int(plan.get("binder_min") or 70))
    hi = max(lo, int(plan.get("binder_max") or 100))
    runs = []
    for chain in target_chains:
        if chain not in spans:
            continue
        # Every stretch of this chain in one run, comma separated. See the module docstring: a
        # break between them is refused, because both runs would name the same output chain.
        runs.append(", ".join("%s%d-%d" % (chain, a, b) for a, b in spans[chain]))
    if not runs:
        raise SystemExit("none of the target chains %s are in %s"
                         % (",".join(target_chains), plan["target"]))
    return "%d-%d, /0, %s" % (lo, hi, ", /0, ".join(runs))


def hotspots_of(plan, spans):
    """{chain: [residue numbers]} from the target card's hotspots, or {} when it named none.

    The card takes them as 54,56,66-70 or A54,B12-16, and A:54 is read too. A residue with no chain
    is put on the target's first chain and a range is spelled out, because a selection names its
    residues individually.
    """
    first = ([c.strip() for c in str(plan.get("chains") or "A").split(",") if c.strip()]
             or sorted(spans) or ["A"])[0]
    out = {}
    for bit in re.split(r"[,;\s]+", str(plan.get("hotspots") or "")):
        if not bit:
            continue
        found = re.match(r"^([A-Za-z]?):?(\d+)(?:-(\d+))?$", bit)
        if not found:
            if not re.match(r"^[A-Za-z]?:?\d+[A-Za-z]$", bit):
                say("  hotspot %r is not a residue as the target card writes one; left out" % bit)
            continue
        chain, low = found.group(1) or first, int(found.group(2))
        for n in range(low, int(found.group(3) or low) + 1):
            if n not in out.setdefault(chain, []):
                out[chain].append(n)
    return {c: sorted(ns) for c, ns in out.items()}


def residues_of(spans):
    """{chain: {residue numbers}} for the residues the spans actually cover."""
    return {c: {n for a, b in runs for n in range(a, b + 1)} for c, runs in spans.items()}


def absent(hot, spans):
    """([the hotspots the target has no residue for], [why each of them is not one]).

    What refuse_missing refuses, worked out without raising it, because the design page asks the
    same question of a box somebody is still typing in. One reading of the structure and one set of
    words, so the page says what the job would say rather than nearly it.
    """
    have = residues_of(spans)
    names, told = [], []
    for chain in sorted(hot):
        for n in hot[chain]:
            name = "%s%d" % (chain, n)
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

    A selection that matches no atom is not an error to RFD4: it sets the condition on nothing and
    designs away happily for hours against a site nobody asked for. That is the whole reason this
    is checked here rather than left to the tool.
    """
    told = absent(hot, spans)[1]
    if told:
        raise SystemExit("these hotspots are not residues of the target, so nothing was run:\n  "
                         + "\n  ".join(told))


def selections(chains, suffix=""):
    """One selection per chain, as a condition's list of them.

    Per chain rather than one selection naming them all: the bracketed list form is for residue
    numbers, and a condition takes a list of selections anyway, so this needs no guess about
    whether a chain field may hold a list.
    """
    return [{"select": "sel('%s/*/*/%s')" % (c, suffix)} if suffix
            else {"select": "sel('%s')" % c} for c in chains]


#: The three conditions this tool is driven by, and the key in the plan that decides whether each
#: one is written. All three default to on, which is what every run before them did and what a flow
#: drawn on the canvas still asks for: a plan with none of these keys in it is the plan this helper
#: has always been handed.
#:
#: They are switches rather than a fixed recipe because turning one off asks the model a different
#: question rather than the same one worse. Without C_HOT the binder may grow anywhere on the
#: target, which is the run to make when the site is what you are looking for rather than what you
#: are aiming at; without C_SEQ it is designing against a shape whose chemistry it was not told.
TOLD = (("C_CRDGRP", "hold_target"), ("C_SEQ", "reveal_sequence"), ("C_HOT", "use_hotspots"))


def told(plan, key):
    """Whether this condition is written, which it is unless the plan says otherwise."""
    return plan.get(key, True) not in (False, "false", "no", 0, "0", "")


def spec_of(plan, target_chains, spans, cif):
    """The condition spec this run is driven by, as the object that is written to YAML."""
    kept = [c for c in target_chains if c in spans]
    conditions = {}
    if told(plan, "hold_target"):
        # One positive group over every target backbone atom, across all of its chains: the
        # authors' own recipe for a rigid target, which retains the geometry of those atoms
        # relative to each other and leaves their side chains free.
        conditions["C_CRDGRP"] = {1: selections(kept, BACKBONE)}
    if told(plan, "reveal_sequence"):
        # The target is allowed to know its own sequence.
        conditions["C_SEQ"] = {True: selections(kept)}
    hot = hotspots_of(plan, spans) if told(plan, "use_hotspots") else {}
    if hot:
        refuse_missing(hot, spans)
        conditions["C_HOT"] = {True: [
            {"select": "sel('%s/*/[%s]')" % (c, ", ".join(str(n) for n in hot[c]))}
            for c in sorted(hot)]}
    return {"binder": {"input": cif,
                       "contig": contig_of(plan, target_chains, spans),
                       "conditions": conditions}}


def spec_lines(spec):
    """The spec as the lines that go into the run folder.

    Written by hand rather than with a YAML library, because the one thing this has to get right
    is the quoting of the selections and the contig, and both are plain strings whose characters a
    dumper would be free to re-quote however it liked. The result is read back by the tool itself,
    which is the only check that matters.

    Its own function so the page can show the spec a run would be given without writing a file and
    without a second description of the format: the preview on the form and the file the job reads
    come off one implementation, which is the only arrangement in which they cannot drift apart.
    """
    body = spec["binder"]
    lines = ["binder:",
             "  input: %s" % json.dumps(body["input"]),
             "  contig: %s" % json.dumps(body["contig"])]
    if not body["conditions"]:
        # Every condition switched off is a legal spec and a real question: a binder of this
        # length on this target, with nothing held, nothing revealed and no site to reach.
        lines.append("  conditions: {}")
        return lines
    lines.append("  conditions:")
    for name, entries in body["conditions"].items():
        lines.append("    %s:" % name)
        for value, picks in entries.items():
            lines.append("      %s:" % ("true" if value is True else value))
            for pick in picks:
                lines.append("        - select: %s" % json.dumps(pick["select"]))
    return lines


def write_spec(plan, where, target_chains, spans, cif):
    """The spec, written into the run folder as YAML so it can be read back."""
    spec = spec_of(plan, target_chains, spans, cif)
    if "C_HOT" not in spec["binder"]["conditions"]:
        say("  no site on the target, so the binder may grow anywhere on it")
    lines = spec_lines(spec)
    path = os.path.join(where, "rfd4_spec.yaml")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    # The same spec as JSON, for everything downstream that reads it back rather than runs it.
    # The report is one stdlib-only process on a login node and has no YAML library; a regex over
    # the file above would be a second, looser description of this one. The tool reads the YAML,
    # which is the copy that matters, and both come off the same object.
    with open(os.path.join(where, "rfd4_spec.json"), "w") as fh:
        json.dump(spec, fh, indent=1, default=str)
    return path


def design_command(plan, out_dir, spec):
    """The command line RFD4-Proteina is given, as a list.

    Its own function so it can be read back without a structure to parse, as RFdiffusion3's is:
    the rest of this helper reads structures through atomworks, which lives in the image, and the
    command line is the part a test has to be able to check anywhere.

    NOTHING HERE IS QUOTED, AND THAT IS DELIBERATE
    These arguments are handed to the image as a list, with no shell between. The tool's own
    documentation writes '~metrics' in quotes because zsh would otherwise read it as a home
    folder, and a quoted copy of it arrives here with the quotes still attached and is refused by
    hydra's parser: LexerNoViableAltException: '~metrics'. So metrics are turned off by composing
    the preset that has none instead, which is also what the tool's own first run uses.
    """
    cmd = ["rfd4-generate",
           "inference_experiment=quickstart",
           "inputs=%s" % spec,
           "out_dir=%s" % out_dir,
           "num_replicates=%d" % max(1, int(plan.get("designs") or 8)),
           "batch_size=%d" % max(1, int(plan.get("batch_size") or 1)),
           "generation.args.nsteps=%d" % max(1, int(plan.get("nsteps") or 60)),
           # The task's own schedule, not the command line's default. The tool publishes a
           # recommendation per task and they differ: front-loaded recycling for a protein
           # against a protein, which is what this card designs, and flat for inverse folding.
           # The CLI's own default is thinking_medium, an alias for a front-loaded preset that
           # is not task aware, so the preset is named here rather than left to it.
           "recycle_strategy=%s" % (plan.get("recycling") or "medium/front_loaded")]
    if plan.get("compile"):
        # The warm-up costs minutes and pays for itself only over many designs, so it is off
        # unless a campaign asks for it. quickstart turns it off; naming the lengths puts it back.
        cmd.append("compile_lengths=[128, 160, 192, 224, 256, 288, 320, 352, 384, 448, 512]")
    if plan.get("seed") not in (None, ""):
        cmd.append("seed=%d" % int(plan["seed"]))
    return cmd


def to_cif(pdb, where):
    """The target as a CIF, which is the only thing a condition spec will read.

    A condition spec refuses a PDB outright and says why: a PDB does not reliably encode the bonds
    and formal charges the model consumes. The conversion is atomworks' own parse and writer, which
    is what the refusal tells you to use, and it is the same code the tool would have used on a CIF
    - so missing atoms are completed and MSE becomes MET here rather than halfway through a run.
    """
    from atomworks.io.parser import parse
    from atomworks.io.utils.io_utils import to_cif_file
    if pdb.lower().endswith((".cif", ".cif.gz", ".cif.zst", ".bcif")):
        return pdb
    out = os.path.join(where, "target.cif")
    try:
        to_cif_file(parse(pdb)["asym_unit"][0], out)
    except Exception as why:                                 # atomworks raises its own types
        # The one failure worth naming: a structure with insertion codes loses them in the
        # conversion, its residues collide under one number, and the standardizer raises about an
        # atom that does not belong to the residue it ended up in. Unreadable unless somebody says
        # what it means.
        raise SystemExit("the target could not be converted to a CIF, which is the only form "
                         "RFD4-Proteina reads: %s\nA target carrying insertion codes (60A beside "
                         "60) fails this way; crop or renumber it first." % str(why)[:400])
    return out


def designs_in(out_dir):
    """Every design the run wrote, by the tool's own discovery.

    Its own helpers rather than a glob: a run may write plain CIF or Zstandard depending on how it
    was asked, and iter_structure_files knows both and refuses a directory holding two copies of
    one design.
    """
    from rfproteina.utils.structure_files import iter_structure_files, structure_file_stem
    found = sorted(iter_structure_files(out_dir, stem_suffix="_generated"), key=str)
    return [(str(p), structure_file_stem(p)) for p in found]


def resolved_settings(out_dir):
    """What the run actually used, from the metrics table the tool writes beside its designs.

    The table is called per_example_metrics.csv and holds no metrics in the sense of scores: with
    no oracle composed there is nothing to score with, and every column is a setting as the tool
    resolved it. That is worth keeping anyway, and one column especially.

    THE SEED, WHICH EXISTS EVEN WHEN NOBODY ASKED FOR ONE
    The tool draws a seed when none is given and records it here. So a run made without a seed can
    still be repeated exactly, and a report written from the plan alone would say the opposite. The
    sibling helpers hit the same trap from the other direction: RFdiffusion's plan carries a
    timestep count of 0 meaning "the tool's own default", and a methods sentence written from it
    printed "0 timesteps".
    """
    import csv
    path = os.path.join(out_dir, "inference_metrics", "per_example_metrics.csv")
    try:
        with open(path, newline="") as fh:
            rows = list(csv.DictReader(fh))
    except (OSError, csv.Error):
        return {}
    if not rows:
        return {}
    first = rows[0]

    def whole(key):
        try:
            return int(float(first[key]))
        except (KeyError, TypeError, ValueError):
            return None

    out = {"seed": whole("seed"), "nsteps": whole("generation.args.nsteps"),
           "recycles": whole("generation.n_recycle"),
           "replicates": whole("generation.dataset.num_replicates"),
           "checkpoint": str(first.get("ckpt_path") or ""),
           "checkpoint_file": str(first.get("ckpt_name") or ""),
           "table": os.path.relpath(path, out_dir)}
    return {k: v for k, v in out.items() if v not in (None, "")}


#: How close two heavy atoms have to be to count as touching, in angstroms. The lab's docking
#: reports use 4.5 for a contact, so a design's reach measured here and an interface read off a
#: docked pose there are the same rule, and the page takes a site off a bound partner by it too.
CONTACT = 4.5


def _atoms_of(cif):
    """(chain, residue number, coordinates) for every atom of a CIF worth measuring.

    TWO THINGS THAT MAKE A DISTANCE NaN, BOTH MEASURED ON A REAL DESIGN
    A generated structure carries every atom of every residue, placed or not, and the ones the
    model did not place are NaN rather than absent: of 2,393 atoms in a 58-residue design on an
    85-residue target, 1,219 were NaN - all 1,217 hydrogens, and two OXT. Numpy's own minimum
    propagates a NaN, so two unplaced terminal oxygens turned every distance in the run into one
    and the first measurement said no design reached anything.

    So hydrogens go, as they would anyway (a deposited target has none, and a reach that changed
    when somebody protonated their file would be a number nobody could reproduce), and so does
    anything whose coordinates are not finite.
    """
    import numpy as np
    from atomworks.io.parser import parse
    arr = parse(cif)["asym_unit"][0]
    xyz = np.asarray(arr.coord, dtype=float)
    element = np.array([str(e).upper() for e in arr.element])
    keep = (element != "H") & np.isfinite(xyz).all(axis=1)
    chain = np.array([str(c) for c in arr.chain_id])[keep]
    res = np.array([int(n) for n in arr.res_id])[keep]
    name = np.array([str(n).upper() for n in arr.res_name])[keep]
    return chain, res, xyz[keep], name


def renumbered(design_cif, target_cif, chains):
    """{chain: {the target's own residue number: the number the design carries for it}}.

    THE DESIGN IS NOT NUMBERED LIKE THE TARGET
    A preserved segment keeps its chain id and loses its numbering: 1YCR's chain A arrives as
    residues 25 to 109 and comes back as 1 to 85. So a site marked as A93 names no residue of the
    design at all, and the first measurement of what a design reached looked up three residues
    that were not there and four that were the wrong ones.

    The order survives, which is what makes the mapping possible: the design's first target
    residue is the target's first. That is checked rather than assumed - the two sequences are
    compared residue by residue - because a mapping that silently slipped by one would report a
    design reaching a site it missed, which is worse than reporting nothing.
    """
    want_chain, want_res, _want_xyz, want_name = _atoms_of(target_cif)
    got_chain, got_res, _got_xyz, got_name = _atoms_of(design_cif)

    def ordered(chain, res, name, which):
        seen, out = set(), []
        for i in range(len(res)):
            if chain[i] != which or (chain[i], res[i]) in seen:
                continue
            seen.add((chain[i], res[i]))
            out.append((int(res[i]), AMINO.get(STATE_NAMES.get(str(name[i]), str(name[i])), "X")))
        return out

    out = {}
    for one in chains:
        theirs = ordered(want_chain, want_res, want_name, one)
        ours = ordered(got_chain, got_res, got_name, one)
        if len(theirs) != len(ours) or any(a[1] != b[1] for a, b in zip(theirs, ours)):
            raise SystemExit(
                "chain %s of the design is not the target's chain %s renumbered: the target has "
                "%d residues and the design %d, and their sequences differ. The site cannot be "
                "mapped onto the design, so what each one reached would be measured against the "
                "wrong residues." % (one, one, len(theirs), len(ours)))
        out[one] = {a[0]: b[0] for a, b in zip(theirs, ours)}
    return out


def reach_of(design_cif, binder_chain, marked, mapping, cutoff=CONTACT):
    """Which of the marked residues this design actually reaches, and how closely.

    THE QUESTION THE TABLE COULD NOT ANSWER
    A site is what the model was asked to reach, not what it did. The conditions are a request:
    the tool is free to return a binder that sits beside the site, and nothing in the run said so.
    Eight designs all looked alike in the table, and the only way to tell which had landed on the
    pocket was to open each one in a viewer and look at it.

    So every design is measured against the residues that were marked: the contacts each one has,
    and the design's own residue nearest to it. `mapping` is renumbered()'s, because the site is
    named in the target's numbering and the design does not use it.

    Returns [{residue, contacts, nearest, nearest_distance}] over the marked residues in the order
    they were marked, the ones it does not reach carrying nought contacts.
    """
    import numpy as np
    chain, res, xyz, _name = _atoms_of(design_cif)
    mine = chain == binder_chain
    if not mine.any():
        return []
    binder_xyz, binder_res = xyz[mine], res[mine]
    out = []
    for one in marked:
        want_chain, want_res = one[:1], int(one[1:])
        here = (mapping.get(want_chain) or {}).get(want_res)
        theirs = (chain == want_chain) & (res == here) if here is not None else None
        if here is None or not theirs.any():
            out.append({"residue": one, "contacts": 0, "nearest": "", "nearest_distance": None})
            continue
        # Every binder heavy atom against every heavy atom of this one residue: a residue is
        # twenty atoms at most and a binder a few hundred, so this is thousands of distances per
        # marked residue and needs no bucketing.
        gaps = np.linalg.norm(binder_xyz[:, None, :] - xyz[theirs][None, :, :], axis=2)
        at = int(np.unravel_index(int(gaps.argmin()), gaps.shape)[0])
        out.append({"residue": one, "contacts": int((gaps <= cutoff).sum()),
                    "nearest": str(int(binder_res[at])),
                    "nearest_distance": round(float(gaps.min()), 2)})
    return out


def chains_and_sequence(cif, want):
    """(every chain in the file, the one-letter sequence of `want`) from a generated CIF.

    Read with atomworks, which the image has because the tool itself depends on it, rather than by
    parsing the file here: a hand-rolled CIF reader is a bug waiting for the first structure with
    an unusual chain label.
    """
    from atomworks.io.parser import parse
    arr = parse(cif)["asym_unit"][0]
    chains, seq, seen = [], "", set()
    for i in range(arr.array_length()):
        chain = str(arr.chain_id[i])
        if chain not in chains:
            chains.append(chain)
        key = (chain, int(arr.res_id[i]))
        if key in seen or (want and chain != want):
            continue
        seen.add(key)
        name = str(arr.res_name[i]).upper()
        seq += AMINO.get(STATE_NAMES.get(name, name), "X")
    return chains, seq


def main(plan_path):
    with open(plan_path) as fh:
        plan = json.load(fh)
    out = plan["out"]
    os.makedirs(out, exist_ok=True)
    target_chains = [c.strip() for c in str(plan.get("chains") or "A").split(",") if c.strip()]

    env = dict(os.environ)
    # A compute node has no outbound network. The weights are on disk beside the image and saying
    # so turns a silent wait on the Hugging Face hub into an immediate, readable failure.
    env["HF_HUB_OFFLINE"] = "1"

    cif = to_cif(plan["target"], out)
    # The stretches are read off the target in the form it arrived in: by PDB columns where it is
    # a PDB, which is the same reading the page previewed, and through atomworks where it is a CIF
    # and there are no columns to read. Only the chains asked for, so the sentence below counts
    # the gaps that will be in the contig rather than every gap in the file.
    if plan["target"].lower().endswith(".pdb"):
        spans = {c: segments(ns) for c, ns in chains_of(plan["target"]).items()
                 if c in target_chains}
    else:
        spans = _spans_from_cif(cif, target_chains)
    holes = sum(len(runs) - 1 for runs in spans.values())
    if holes:
        say("  the target has %d gap(s) in its chain(s); RFD4-Proteina has no way to say that a "
            "chain stops and starts again, so each chain is kept as one piece" % holes)
    spec = write_spec(plan, out, target_chains, spans, cif)
    designs_dir = os.path.join(out, "rfd4")
    say("RFD4-Proteina: %d design(s), contig %s"
        % (plan.get("designs") or 8, contig_of(plan, target_chains, spans)))
    run(design_command(plan, designs_dir, spec), env=env)

    made = designs_in(os.path.join(designs_dir, "inference_samples"))
    if not made:
        raise SystemExit("RFD4-Proteina wrote no structure into %s"
                         % os.path.join(designs_dir, "inference_samples"))
    say("  %d design(s) written" % len(made))

    # What the run was told to reach, read back from the same reader the spec was built with, so
    # the residues each design is measured against are the residues the condition named.
    asked_site = hotspots_of(plan, spans) if told(plan, "use_hotspots") else {}
    designs = []
    for path, name in made:
        # WHICH CHAIN IS THE BINDER, AND WHY IT IS FOUND BY ELIMINATION.
        #
        # A preserved segment keeps its input chain id, so the target comes back as the chains the
        # card named. The binder is an expandable segment in a run of its own, which takes a fresh
        # id the tool picks - C, on a target of chains A and B. So the binder is what is left when
        # the target's chains are taken away, which is the opposite of RFdiffusion3, where the
        # binder is chain A and elimination would pick the target.
        #
        # The generated CIF also carries SA_CRDSRC on the conditioned atoms, which is the other
        # way to ask the same question; elimination needs no annotation preset to read it.
        chains, _ = chains_and_sequence(path, "")
        binder = [c for c in chains if c not in target_chains]
        if not binder:
            raise SystemExit("%s came back with chains %s, which are all target chains; nothing "
                             "in it is the design" % (name, ",".join(chains)))
        _, seq = chains_and_sequence(path, binder[0])
        # What it reached, against what it was asked to reach. Measured here rather than left to
        # whoever opens the run, because the answer is the first thing anybody wants of a design
        # and a viewer is a slow way to get it eight times over.
        marked = ["%s%d" % (c, n) for c in sorted(asked_site) for n in asked_site[c]]
        reach = reach_of(path, binder[0], marked,
                         renumbered(path, cif, sorted(asked_site))) if marked else []
        designs.append({
            "name": name,
            "path": path,
            "binder_chains": binder,
            "target_chains": [c for c in chains if c in target_chains],
            "sequence": seq,
            # The site, design by design: which marked residues it touches, how many contacts each
            # has, and the design's own residue nearest to it.
            "reach": reach,
            "reached": sum(1 for one in reach if one["contacts"]),
            "marked": len(marked),
            "contacts": sum(one["contacts"] for one in reach),
            # RFD4-Proteina designs the sequence with the backbone, so there is no separate
            # sequence step and nothing has scored these: the preset that keeps a run off the
            # refolding oracles is the one this helper composes. A score arrives when somebody
            # folds them, which is the next card in a flow.
            "score": None,
        })

    if asked_site:
        for one in designs:
            say("  %s reaches %d of %d marked residue(s), %d contact(s)"
                % (one["name"], one["reached"], one["marked"], one["contacts"]))
    record = {"tool": "rfd4", "designs": designs,
              # What the site was, so the table can draw a tick per marked residue even for a
              # design that reached none of them.
              "site": ["%s%d" % (c, n) for c in sorted(asked_site) for n in asked_site[c]],
              # What the tool resolved, beside what it was asked. The seed is the reason: it draws
              # one when none is given, so without this the report would say a run cannot be
              # repeated when it can.
              "resolved": resolved_settings(designs_dir)}
    with open(os.path.join(out, "designs.json"), "w") as fh:
        json.dump(record, fh, indent=1)
    say("recorded %d design(s) -> %s" % (len(designs), os.path.join(out, "designs.json")))


def _spans_from_cif(cif, chains):
    """The resolved stretches of each chain of a CIF, for a target that arrived as one.

    The PDB route reads columns, which needs no library and is what the page uses; a target given
    as a CIF has to be read properly, and the image has atomworks for it.
    """
    from atomworks.io.parser import parse
    arr = parse(cif)["asym_unit"][0]
    numbers, seen = {}, set()
    for i in range(arr.array_length()):
        chain = str(arr.chain_id[i])
        if chains and chain not in chains:
            continue
        key = (chain, int(arr.res_id[i]))
        if key in seen:
            continue
        seen.add(key)
        numbers.setdefault(chain, []).append(int(arr.res_id[i]))
    return {c: segments(ns) for c, ns in numbers.items()}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("rfd4_design.py <plan.json>")
    main(sys.argv[1])
