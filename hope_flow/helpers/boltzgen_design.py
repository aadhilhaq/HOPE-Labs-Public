#!/usr/bin/env python
"""Generate binders with BoltzGen, and record what came out.

Run by BoltzGen's own Python (3.12), inside a GPU job, not by the flow's.

Two things about BoltzGen's design spec are easy to get wrong, and both are handled here rather
than left to whoever fills the card in:

  - Hotspots are numbered by POSITION IN THE CHAIN, not by the numbering the structure file uses.
    hPD-L1's own file is numbered from 18, so residue 56 in the file is position 39 to BoltzGen.
    Every other tool in the lab takes the file's own numbering, so the card does too and this
    converts. Getting it wrong does not fail: it designs against a different patch of the surface
    for twelve hours. A hotspot the target has no residue for is refused rather than converted
    and dropped, in the same words the two RFdiffusion helpers refuse one in, and so is one
    written with an insertion code, which this reading of the box cannot carry and which
    RFdiffusion refuses in those same words - RFdiffusion3 is the one of the three that keeps it.
  - `total_len` is the length of the WHOLE complex, target included, and it is a resample-until-it
    -fits loop. The binder's own length belongs in its sequence as "70..100", which is what this
    writes; total_len is left out.

The conversion is checked before anything expensive starts: `boltzgen check` writes the spec as a
structure with the binding residues marked, and this reads them back and refuses if they are not
the residues that were asked for.

    boltzgen_design.py <plan.json>

Leaves <out>/designs.json, in the shape hope_flow/adapters.py documents.
"""
import csv
import json
import os
import re
import subprocess
import sys

AMINO = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q", "GLU": "E",
    "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F",
    "PRO": "P", "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
}
#: A residue named for its protonation state, as a prepared structure names it, is still its own
#: amino acid. The flow hands these tools a cleaned target, with the standard names, so these are
#: for a structure that reaches them some other way: read as nothing, a HID or a CYX was a residue
#: missing from the chain's numbering and from its sequence.
STATE_NAMES = {"HID": "HIS", "HIE": "HIS", "HIP": "HIS", "HSD": "HIS", "HSE": "HIS", "HSP": "HIS",
               "CYX": "CYS", "CYM": "CYS", "ASH": "ASP", "GLH": "GLU", "LYN": "LYS"}


def _amino(name):
    """The standard residue a three-letter name is, or "" for one that is not an amino acid."""
    name = STATE_NAMES.get(name, name)
    return name if name in AMINO else ""


#: The chain id asked for in the spec. It is not what comes out: BoltzGen renames the chains of
#: what it writes, so which chain is the binder is read back from each design rather than assumed.
BINDER_CHAIN = "Zz"

#: BoltzGen's two design models, as its own --design_checkpoints takes them (cli/boltzgen.py,
#: ARTIFACTS). Both are in the group's cache. "both" is the tool's own default - half the
#: designs from each - and is an empty tuple here because the flag is then not passed at all.
CHECKPOINTS = {
    "both": (),
    "diverse": ("huggingface:boltzgen/boltzgen-1:boltzgen1_diverse.ckpt",),
    "adherence": ("huggingface:boltzgen/boltzgen-1:boltzgen1_adherence.ckpt",),
}


#: The protocols BoltzGen leaves cysteine out of by itself (cli/boltzgen.py, the default for
#: --inverse_fold_avoid: "none for protein design, 'C' for peptide and antibody/nanobody
#: design"). Naming any letters at all replaces that default, so these are the protocols where
#: a list without a C quietly allows cysteine back in.
CYSTEINE_FREE = ("peptide-anything", "nanobody-anything", "antibody-anything")


def avoid_letters(text):
    """The letters BoltzGen's --inverse_fold_avoid is given: one-letter amino acid codes.

    Its own option takes them as a bare string, "KEC". A comma or a space between them is how a
    person writes a list, so anything that is not one of the twenty is dropped rather than sent
    on to be refused by the inverse-folding step at the end of a long run.
    """
    return "".join(sorted({c for c in str(text or "").upper() if c in set(AMINO.values())}))


def say(*what):
    print(*what)
    sys.stdout.flush()


def run(args, where=None):
    say("$ " + " ".join(os.path.basename(a) if i == 0 else a for i, a in enumerate(args))[:400])
    done = subprocess.run(args, cwd=where)
    if done.returncode != 0:
        raise SystemExit("%s exited %d" % (os.path.basename(args[0]), done.returncode))


def chains_of(path):
    """{chain: [author residue numbers]} for the amino acids of a structure, in file order."""
    found, seen = {}, set()
    with open(path, errors="replace") as fh:
        for line in fh:
            if not line.startswith("ATOM") or line[12:16].strip() != "CA":
                continue
            if not _amino(line[17:20].strip()):
                continue
            chain = line[21].strip() or "A"
            try:
                number = int(line[22:26])
            except ValueError:
                continue
            # One entry per residue, and a residue is its number and its insertion code: 60, 60A
            # and 60B are three. Keyed on the number alone they were one, and every position after
            # them in the chain was counted short by as many.
            key = (chain, line[22:27])
            if key in seen:
                continue
            seen.add(key)
            found.setdefault(chain, []).append(number)
    return found


def sequence_of(path, chain):
    """The one-letter sequence of one chain of a PDB, in file order."""
    out, seen = [], set()
    with open(path, errors="replace") as fh:
        for line in fh:
            if not line.startswith("ATOM") or line[12:16].strip() != "CA":
                continue
            if (line[21].strip() or "A") != chain:
                continue
            key = line[22:27]
            if key in seen:
                continue
            seen.add(key)
            out.append(AMINO.get(_amino(line[17:20].strip()), "X"))
    return "".join(out)


def wanted(text, default_chain):
    """{chain: [author residue numbers]} from "54,56,66-70", "A54,B12-16" or "A:54"."""
    out = {}
    for token in (t.strip() for t in (text or "").split(",") if t.strip()):
        found = re.match(r"^([A-Za-z]?):?(\d+)(?:-(\d+))?$", token)
        if not found:
            continue
        chain = found.group(1) or default_chain
        low = int(found.group(2))
        high = int(found.group(3) or low)
        out.setdefault(chain, []).extend(range(low, high + 1))
    return out


def positions(author_numbers, asked):
    """The asked-for residues as positions in the chain, 1-based, which is what BoltzGen wants."""
    # The first residue of a number, where insertion codes give it several (60, 60A, 60B): a hotspot
    # written as a bare number is that one, as the other tools read it.
    where = {}
    for i, n in enumerate(author_numbers):
        where.setdefault(n, i + 1)
    found, missing = [], []
    for n in asked:
        if n in where:
            found.append(where[n])
        else:
            missing.append(n)
    return sorted(set(found)), missing


#: A hotspot written with an insertion code: H60A, 60A, or H:60A as the docking tools write one.
CODED = re.compile(r"^([A-Za-z]?):?(\d+)([A-Za-z])$")


def coded(text):
    """The hotspots written with an insertion code, as they were typed.

    BoltzGen's spec marks a binding residue by its POSITION in the chain, and the card's box is
    read here as a chain, a number and at most a range: wanted()'s pattern matches neither 60A
    nor H60A, so one was dropped without a word, and positions() then put a bare 60 on the first
    residue numbered 60 - which is 60, not 60A. A structure numbered with codes has both, so the
    campaign was aimed at a residue nobody typed in and ran its twelve hours there.

    This is the third reading of the one box. RFdiffusion has nowhere to put a code either and
    refuses one by name; RFdiffusion3 keeps it on its residue. So the box meant three things to
    three tools, and this was the one that said nothing about it.
    """
    return [bit for bit in (b.strip() for b in re.split(r"[,;\s]+", text or ""))
            if bit and CODED.match(bit)]


def coded_why(text):
    """One sentence per insertion-coded hotspot, saying why it cannot be asked for.

    Word for word rfdiffusion_design.coded_why's, and tool-neutral for that reason: the two
    helpers cannot share a module, because each is run by its own tool's Python, so the words are
    carried twice and tests/test_design_hotspots.py holds them to the same string. The one tool
    that does keep a code is named in the sentence, so the box has somewhere to go.
    """
    return ["%s: the code is lost on the way in and the run would aim at residue %s instead. "
            "RFdiffusion3 is the one tool that keeps a code on its residue, if that is the site"
            % (bit, CODED.match(bit).group(2))
            for bit in coded(text)]


def refuse_coded(text):
    """Refuse a hotspot written with an insertion code, naming it."""
    told = coded_why(text)
    if told:
        raise SystemExit("these hotspots have an insertion code, which this tool cannot be "
                         "aimed at, so nothing was run:\n  " + "\n  ".join(told))


def refuse_missing(asked, keep):
    """Refuse any hotspot the target has no residue for, naming it.

    The same refusal as rfdiffusion_design's and rfd3_design's, in the same words, because the
    three tools are given the same box to read and a person should not have to learn which of
    them is strict about it.

    BoltzGen was the lenient one, and its own numbering is what made it so: a hotspot is a
    POSITION in the chain, and one the file has not got has no position at all, so it was named
    in the log and then simply left out of the spec. The run went on with the rest - twelve hours
    aimed at whichever part of the site happened to be resolved, or at the whole surface where
    none of it was - which is exactly the quiet failure the other two now refuse.
    """
    told = []
    for chain in sorted(asked):
        numbers = keep.get(chain)
        if not numbers:
            for n in sorted(set(asked[chain])):
                told.append("%s%d: the target has no chain %s (it has %s)"
                            % (chain, n, chain, ", ".join(sorted(keep)) or "none"))
            continue
        first, last = numbers[0], numbers[-1]
        for n in sorted(set(positions(numbers, asked[chain])[1])):
            where = ("a gap in the chain" if first < n < last
                     else "outside the chain, which is numbered %d to %d" % (first, last))
            told.append("%s%d: %s has no residue %d, it is %s" % (chain, n, chain, n, where))
    if told:
        raise SystemExit("these hotspots are not residues of the target, so nothing was run:\n  "
                         + "\n  ".join(told))


def spec_yaml(plan, keep, binding, not_binding=None):
    """The design specification, written by hand: it is five keys and a list."""
    not_binding = not_binding or {}
    lines = ["# Written by HOPE Labs for a flow. Residue numbers below are positions in the",
             "# chain, converted from the structure's own numbering.",
             "entities:",
             "  - file:",
             "      path: %s" % plan["target"],
             "      include:"]
    for chain in sorted(keep):
        lines.append("        - chain:")
        lines.append("            id: %s" % chain)
    if binding or not_binding:
        # One entry per chain carrying either kind, because BoltzGen reads binding and
        # not_binding off the same chain entry (data/parse/schema.py) and two entries for one
        # chain would leave the second overwriting the first.
        lines.append("      binding_types:")
        for chain in sorted(set(binding) | set(not_binding)):
            lines.append("        - chain:")
            lines.append("            id: %s" % chain)
            if binding.get(chain):
                lines.append('            binding: "%s"'
                             % ",".join(str(p) for p in binding[chain]))
            if not_binding.get(chain):
                lines.append('            not_binding: "%s"'
                             % ",".join(str(p) for p in not_binding[chain]))
    lines.append("  - protein:")
    lines.append("      id: %s" % BINDER_CHAIN)
    lines.append('      sequence: "%d..%d"' % (plan["binder_min"], plan["binder_max"]))
    if plan.get("cyclic"):
        lines.append("      cyclic: true")
    return "\n".join(lines) + "\n"


def marked(cif):
    """The residues `boltzgen check` marked as binding, as positions.

    It writes the spec as a structure whose B-factor is 80 per binding residue, and 100 more for
    a designed one. Reading them back is how the conversion above is checked without a GPU.
    """
    out = []
    try:
        with open(cif, errors="replace") as fh:
            for line in fh:
                if not line.startswith(("ATOM", "HETATM")):
                    continue
                bits = line.split()
                if len(bits) < 15:
                    continue
                try:
                    b = float(bits[14])
                    seq = int(bits[16]) if len(bits) > 16 else int(bits[8])
                except (ValueError, IndexError):
                    continue
                if 79.0 <= b <= 81.0 and seq not in out:
                    out.append(seq)
    except OSError:
        return []
    return sorted(out)


def cif_sequences(path):
    """{chain: one-letter sequence} of a design BoltzGen wrote, in residue order."""
    found, seen = {}, set()
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                if not line.startswith(("ATOM", "HETATM")):
                    continue
                bits = line.split()
                if len(bits) < 9 or bits[3] != "CA":
                    continue
                chain, residue, number = bits[6], bits[5], bits[8]
                if (chain, number) in seen:
                    continue
                seen.add((chain, number))
                found[chain] = found.get(chain, "") + AMINO.get(residue.upper(), "X")
    except OSError:
        return {}
    return found


def split_chains(path, designed, target_seqs):
    """(the binder's chain, [the target's chains]) in a design, told apart by SEQUENCE.

    Not by chain id and not by length. BoltzGen renames the chains of what it writes, so the id
    asked for in the spec is not the id that comes out; and a binder can be the same length as a
    small target, which would make a length test quietly pick the wrong one. The sequences settle
    it: the designed sequence is in the metrics table, the target's is the one that went in, and
    both are matched against what is actually in the file.

    Returns ("", []) when the file does not say both plainly, which is a design skipped by name
    rather than a receptor and a partner handed over the wrong way about.
    """
    here = cif_sequences(path)
    if not here or not designed:
        return "", []
    designed = designed.upper()
    targets = {s.upper() for s in target_seqs if s}
    binder = [c for c, s in sorted(here.items()) if s.upper() == designed]
    target = [c for c, s in sorted(here.items()) if s.upper() in targets]
    # Exactly one chain carrying the designed sequence, and the target found beside it. A chain
    # that is both is a design identical to its target, which is not a binder worth passing on.
    if len(binder) != 1 or not target or binder[0] in target:
        return "", []
    return binder[0], target


def main():
    plan = json.load(open(sys.argv[1]))
    out = plan["out"]
    boltzgen = plan["boltzgen"]
    cache = plan["cache"]

    present = chains_of(plan["target"])
    keep = [c.strip() for c in (plan.get("chains") or "A").split(",") if c.strip() and c.strip() in present]
    if not keep:
        raise SystemExit("none of the chains %s are in %s (it has %s)"
                         % (plan.get("chains"), plan["target"], ",".join(sorted(present)) or "none"))

    # The hotspots, converted from the file's numbering to BoltzGen's positions. Refused first,
    # by name, where the target has no such residue: one in a gap was named in this log and then
    # dropped, and the run went on aimed at whatever was left of the site. And where it carries an
    # insertion code, which this conversion cannot carry and which said nothing at all: the box
    # read 60A and the run aimed at 60.
    refuse_coded(plan.get("hotspots"))
    asked = wanted(plan.get("hotspots"), keep[0])
    refuse_missing(asked, {c: present[c] for c in keep})
    binding = {}
    for chain, numbers in asked.items():
        got = positions(present[chain], numbers)[0]
        if got:
            binding[chain] = got
    if asked and not binding:
        raise SystemExit("none of the hotspots given are in the target")
    for chain, got in sorted(binding.items()):
        first, last = present[chain][0], present[chain][-1]
        say("hotspots: chain %s %s -> positions %s (the chain is numbered %d to %d)"
            % (chain, ",".join(str(n) for n in sorted(set(asked.get(chain, [])))[:8]),
               ",".join(str(p) for p in got[:8]), first, last))

    # The coldspots, the same way round. BoltzGen has a NOT_BINDING type beside its BINDING one,
    # which is a residue the binder is asked to keep off rather than one it is told nothing
    # about, so this is the opposite of a hotspot rather than the absence of one. Refused on the
    # same grounds and converted by the same code, so the two boxes behave alike.
    refuse_coded(plan.get("coldspots"))
    cold_asked = wanted(plan.get("coldspots"), keep[0])
    refuse_missing(cold_asked, {c: present[c] for c in keep})
    not_binding = {}
    for chain, numbers in cold_asked.items():
        got = positions(present[chain], numbers)[0]
        if got:
            not_binding[chain] = got
    if cold_asked and not not_binding:
        raise SystemExit("none of the coldspots given are in the target")
    for chain, got in sorted(not_binding.items()):
        say("coldspots: chain %s -> positions %s, sent as not binding"
            % (chain, ",".join(str(p) for p in got[:8])))
    both = sorted("%s%d" % (c, p) for c in set(binding) & set(not_binding)
                  for p in set(binding[c]) & set(not_binding[c]))
    if both:
        raise SystemExit("these residues are given as hotspots and as coldspots, so what the "
                         "binder is to do with them has not been said: %s" % ", ".join(both))

    # No underscore in the name: BoltzGen takes everything before the first one as the target's
    # name, and every design it writes is named after it.
    stem = re.sub(r"[^A-Za-z0-9]", "", os.path.splitext(os.path.basename(plan["target"]))[0]) or "target"
    spec = os.path.join(out, stem + ".yaml")
    with open(spec, "w") as fh:
        fh.write(spec_yaml(plan, keep, binding, not_binding))
    say("wrote %s" % spec)

    # ---- check before anything expensive, and prove the conversion ------------------------
    checked = os.path.join(out, "checked")
    run([boltzgen, "check", spec, "--output", checked, "--cache", cache])
    if binding:
        want = sorted(p for got in binding.values() for p in got)
        got = marked(os.path.join(checked, stem + ".cif"))
        if got and got != want:
            raise SystemExit("the hotspots did not survive conversion: asked for positions %s, "
                             "the spec marks %s. Nothing has been queued."
                             % (want[:10], got[:10]))
        say("hotspots: %d residue(s) confirmed in the spec" % len(want))

    # ---- the design run ---------------------------------------------------------------------
    designs = int(plan["designs"])
    # --devices stays at 1 because the job has one card: the design jobs ask Slurm for
    # gpu:<kind>:1 (drivers.JOB), so a larger number would have BoltzGen look for cards that
    # were never booked.
    args = [boltzgen, "run", spec, "--protocol", plan["protocol"], "--output", out,
            "--num_designs", str(designs), "--cache", cache, "--devices", "1"]

    # THE FILTERING STAGE, WHICH USED TO DO NOTHING
    # BoltzGen generates many designs and its filtering stage then picks a diverse set of
    # --budget out of them. --budget was never passed, so it kept its own default of 30 while
    # the runs here asked for ten or fifty designs: the stage was being told to choose thirty out
    # of ten, which is every one of them. Passing the number of designs where the card names none
    # keeps that - every design that passed - but says so, and a card that names one gets the
    # selection it asked for.
    budget = plan.get("budget")
    # Never above what was generated, because the names of two output folders carry it and
    # BoltzGen would select everything anyway; never below one, because a flow edited by hand
    # can carry a zero the card's own bounds would not have allowed, and --budget 0 selects
    # nothing at all after a full run.
    budget = designs if budget in (None, "") else max(1, min(int(budget), designs))
    args += ["--budget", str(budget)]
    if plan.get("alpha") not in (None, ""):
        args += ["--alpha", "%g" % float(plan["alpha"])]
    if plan.get("filter_biased") is False:
        args += ["--filter_biased", "false"]

    if plan.get("sampling_steps"):
        args += ["--step_scale", str(plan["sampling_steps"])]
    if plan.get("noise_scale") not in (None, ""):
        args += ["--noise_scale", "%g" % float(plan["noise_scale"])]
    # One of the two design models rather than half the designs from each.
    picked = CHECKPOINTS.get(str(plan.get("checkpoints") or "both"))
    if picked is None:
        raise SystemExit("the model must be one of %s, not %r"
                         % (", ".join(sorted(CHECKPOINTS)), plan.get("checkpoints")))
    if picked:
        args += ["--design_checkpoints"] + list(picked)
    # The sequence step. BoltzGen's own default is one sequence per backbone; the sister cards
    # have always offered the number, and this one did not pass it at all.
    seqs = int(plan.get("seqs_per_backbone") or 1)
    if seqs > 1:
        args += ["--inverse_fold_num_sequences", str(seqs)]
    if plan.get("avoid"):
        letters = avoid_letters(plan["avoid"])
        if not letters:
            raise SystemExit("%r names no amino acid to leave out; it takes letters, as KEC"
                             % plan["avoid"])
        # This REPLACES the protocol's own list rather than adding to it, and three protocols
        # leave cysteine out by themselves. So a list that does not name C puts cysteines back
        # into peptides and nanobodies, which is not what anybody typing a letter in that box
        # meant to ask for. Said rather than corrected: the box is the person's to set.
        if "C" not in letters and plan.get("protocol") in CYSTEINE_FREE:
            say("  the %s protocol leaves cysteine out by itself, and these letters replace "
                "that list rather than adding to it: this run may design cysteines. Add C to "
                "keep them out." % plan.get("protocol"))
        args += ["--inverse_fold_avoid", letters]
    if not plan.get("fold", True):
        args += ["--steps", "design", "inverse_folding"]
    run(args)

    # ---- what came out ----------------------------------------------------------------------
    ranked = os.path.join(out, "final_ranked_designs")
    rows = []
    # BOTH OF THESE NAMES CARRY THE BUDGET, AND 30 WAS IT ONLY BY ACCIDENT
    # BoltzGen names the selected set after the figure it selected: final_{budget}_designs and
    # final_designs_metrics_{budget}.csv (task/filter/filter.py). The 30 written in here was
    # BoltzGen's own default budget, which held while nothing passed --budget; the moment one is
    # passed these names move with it, and a helper looking for 30 would read the fallback CSV
    # of every design and hand on an unselected list as though it had been chosen.
    #
    # The intermediate folder keeps its 10: top_budget is not a command-line option, only a
    # --config override, and nothing here sets one.
    for name in ("final_designs_metrics_%d.csv" % budget, "all_designs_metrics.csv"):
        path = os.path.join(ranked, name)
        if os.path.isfile(path):
            with open(path, newline="", errors="replace") as fh:
                rows = list(csv.DictReader(fh))
            say("read %s: %d row(s)" % (name, len(rows)))
            break
    if not rows:
        # The metrics are the ranking; without them there is nothing to hand on in order.
        raise SystemExit("BoltzGen left no metrics CSV in %s" % ranked)

    # The selected set first: the same design appears under both folders, and the one in
    # final_<budget>_designs is the re-folded structure the tool recommends.
    structures = {}
    for folder in ("final_%d_designs" % budget, "intermediate_ranked_10_designs"):
        here = os.path.join(ranked, folder)
        for f in sorted(os.listdir(here) if os.path.isdir(here) else []):
            if f.endswith(".cif"):
                structures.setdefault(re.sub(r"^rank\d+_", "", f[:-4]), os.path.join(here, f))

    # The target's own sequences, read from the file that went in, to recognize it on the way out.
    target_seqs = [sequence_of(plan["target"], c) for c in keep]
    designs, dropped = [], []
    for row in rows:
        name = (row.get("id") or os.path.splitext(row.get("file_name") or "")[0]).strip()
        seq = (row.get("designed_chain_sequence") or row.get("designed_sequence") or "").strip()
        if not name or not seq:
            continue
        path = structures.get(name)
        if not path:
            dropped.append((name, "no structure among the ranked designs"))
            continue
        binder, target_chains = split_chains(path, seq, target_seqs)
        if not binder:
            dropped.append((name, "no chain in the structure carries the designed sequence"))
            continue
        try:
            rank = int(float(row.get("final_rank") or row.get("secondary_rank") or 1e9))
        except ValueError:
            rank = 10 ** 9
        designs.append({"name": name, "path": path,
                        "binder_chains": [binder], "target_chains": target_chains,
                        "sequence": seq.upper(), "score": rank})
    for name, why in dropped:
        say("  %s: %s" % (name, why))
    designs.sort(key=lambda d: d["score"])
    with open(os.path.join(out, "designs.json"), "w") as fh:
        json.dump({"tool": "boltzgen", "designs": designs}, fh, indent=1)
        fh.write("\n")
    say("wrote designs.json: %d design(s)" % len(designs))
    if not designs:
        raise SystemExit("no design came through with both a structure and a sequence")


if __name__ == "__main__":
    main()
