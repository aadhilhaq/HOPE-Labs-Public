#!/usr/bin/env python
"""Design binders with RFdiffusion, give them sequences with ProteinMPNN, and record what came out.

Run by RFdiffusion's own Python (SE3nv, 3.9), inside a GPU job, not by the flow's. Written for
3.9: no match statements, no newer typing.

Unlike every other tool a flow starts, these two have no launcher of their own to go through -
no page, no submitting script, nothing that already knows how to queue them. So this is that
missing piece rather than a second way to do something the lab already had, and it is deliberately
the whole of it: the two steps, and the record the next card reads.

    rfdiffusion_design.py <plan.json>

The plan is what the driver worked out; the record left behind is <out>/designs.json, in the shape
hope_flow/adapters.py documents.
"""
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


def say(*what):
    print(*what)
    sys.stdout.flush()


def run(args, where=None, env=None):
    say("$ " + " ".join(args[:8]) + (" ..." if len(args) > 8 else ""))
    done = subprocess.run(args, cwd=where, env=env)
    if done.returncode != 0:
        raise SystemExit("%s exited %d" % (os.path.basename(args[0]), done.returncode))


def chains_of(path):
    """{chain: [residue numbers]} for the amino-acid residues of a PDB, in file order."""
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
    """The one-letter sequence of one chain, in file order."""
    out = []
    seen = set()
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


def segments(numbers):
    """[(first, last), ...] - one span per unbroken run of residue numbers in one chain.

    A deposited structure is full of holes: residues nobody could see in the density are simply
    not in the file, and the numbering steps over them. One span per run is what the contig needs,
    because a span that reaches across a hole names residues the file has not got.

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


def residue_pairs(text, default_chain):
    """"54,56,66-70", "A54,B12-16" or "A:54" -> [("A", 54), ("A", 56), ...].

    RFdiffusion names every residue it is to aim at; it has no range form, so a range is spelled
    out. The chain is carried from the token before it when a token does not name one, which is
    how the other tools' hotspot boxes are written.
    """
    out = []
    for token in (t.strip() for t in (text or "").split(",") if t.strip()):
        found = re.match(r"^([A-Za-z]?):?(\d+)(?:-(\d+))?$", token)
        if not found:
            continue
        chain = found.group(1) or default_chain
        low = int(found.group(2))
        high = int(found.group(3) or low)
        for n in range(low, high + 1):
            out.append((chain, n))
    return out


def residues(text, default_chain):
    """The same hotspots as the names RFdiffusion wants them under: ["A54", "A56", ...]."""
    return ["%s%d" % (chain, n) for chain, n in residue_pairs(text, default_chain)]


#: A hotspot written with an insertion code: H60A, 60A, or H:60A as the docking tools write one.
CODED = re.compile(r"^([A-Za-z]?):?(\d+)([A-Za-z])$")


def coded(text):
    """The hotspots written with an insertion code, as they were typed.

    RFdiffusion keys a residue by its chain and its number and has nowhere to put a code: both its
    contig and ppi.hotspot_res are read as a letter and digits. So H60A cannot be asked for, and
    it is not the same residue as H60 - a structure numbered with insertion codes has both.
    residue_pairs matches neither form, and a code dropped without a word would
    aim a campaign at a residue nobody typed in. RFdiffusion3 keeps the code on its residue, so
    the same box meant two different things to the two tools; this is the one that cannot take it,
    and it now says so.
    """
    return [bit for bit in (b.strip() for b in re.split(r"[,;\s]+", text or ""))
            if bit and CODED.match(bit)]


def coded_why(text):
    """One sentence per insertion-coded hotspot, saying why it cannot be asked for.

    Said here rather than in the two places that need it, so the job and the page refuse the same
    box in the same words.
    """
    return ["%s: the code is lost on the way to RFdiffusion, which keys a hotspot by its chain "
            "and number alone, so it would aim at residue %s instead. RFdiffusion3 keeps the code "
            "on its residue, if that is the site" % (bit, CODED.match(bit).group(2))
            for bit in coded(text)]


def refuse_coded(text):
    """Refuse a hotspot written with an insertion code, naming it."""
    told = coded_why(text)
    if told:
        raise SystemExit("these hotspots have an insertion code, which RFdiffusion cannot be "
                         "asked for, so nothing was run:\n  " + "\n  ".join(told))


def absent(pairs, keep):
    """([the hotspots the target has no residue for], [why each of them is not one]).

    What refuse_missing refuses, worked out without raising it, so that the design page and the
    flow's own driver can say the same about a site before anything is queued. One reading of the
    structure and one set of words.
    """
    names, told = [], []
    for chain, n in pairs:
        if chain not in keep:
            names.append("%s%d" % (chain, n))
            told.append("%s%d: the target has no chain %s (it has %s)"
                        % (chain, n, chain, ", ".join(sorted(keep))))
        elif n not in keep[chain]:
            first, last = keep[chain][0], keep[chain][-1]
            where = ("a gap in the chain" if first < n < last
                     else "outside the chain, which is numbered %d to %d" % (first, last))
            names.append("%s%d" % (chain, n))
            told.append("%s%d: %s has no residue %d, it is %s" % (chain, n, chain, n, where))
    return names, told


def refuse_missing(pairs, keep):
    """Refuse any hotspot the target has no residue for, naming it.

    A hotspot in one of the structure's holes is the quiet failure worth being loud about:
    RFdiffusion marks the residues it is given and says nothing about one it cannot find, so a
    campaign aimed at a site that was never resolved runs for its full twelve hours and comes back
    with binders for somewhere else entirely.
    """
    told = absent(pairs, keep)[1]
    if told:
        raise SystemExit("these hotspots are not residues of the target, so nothing was run:\n  "
                         + "\n  ".join(told))


def split_chains(path, here, target_seqs, binder_min, binder_max):
    """(the binder's chain, [the target's chains]) in a design RFdiffusion wrote.

    Told apart by SEQUENCE, not by chain id and not by where the chains sit in the file.
    RFdiffusion writes the binder first and the target after it, the opposite way round from the
    contig that asked for them, and a design taken the wrong way round does not fail: ProteinMPNN
    redesigns the target instead and the run returns sequences for the protein that was already
    there. The target is kept verbatim, so it is recognised by its own sequence; what is left,
    and is the length that was asked for, is the binder.

    THE TARGET COMES BACK AS ONE CHAIN, WHATEVER IT WENT IN AS
    RFdiffusion writes everything it kept as a single output chain. A two-chain target goes in as
    two blocks of the contig and comes out as one chain carrying both of them, joined, in the
    order the contig named them. Matched chain by chain, no chain of the
    file is any of the target's own sequences, so EVERY design of a multi-chain target would
    be skipped with "cannot tell the binder from the target" and the run would end having
    written no sequence at all. So the target is looked for as the whole of itself, joined in
    contig order, as well as chain by chain - the single-chain case is the same statement with one
    chain in it.

    And by length where neither matches, which is honest here and nowhere else: the chain that is
    not the target is poly-glycine until ProteinMPNN has been over it, so a sequence comparison
    cannot be what tells them apart in the first place. Only where exactly one chain is the
    target's own length, so a binder that happens to be that long leaves the design skipped rather
    than handed over the wrong way about.
    """
    seqs = {c: sequence_of(path, c) for c in here}
    whole = "".join(target_seqs).upper()
    targets = {s.upper() for s in target_seqs if s}
    if whole:
        targets.add(whole)
    target = [c for c, s in sorted(seqs.items()) if s.upper() in targets]
    if not target and whole:
        target = [c for c, s in sorted(seqs.items()) if len(s) == len(whole)]
    left = [c for c in sorted(seqs) if c not in target]
    if not target or len(left) != 1:
        return "", []
    binder = left[0]
    # The diffused chain is poly-glycine until ProteinMPNN has been over it, so its length is what
    # there is to check it against; a design that is neither is skipped by name, not guessed at.
    if not (binder_min - 2) <= len(seqs[binder]) <= (binder_max + 2):
        return "", []
    return binder, target


def contig(target_chains, binder_min, binder_max):
    """The contig map: every target chain as it stands, a chain break, then the binder to design.

    "[A1-150/0 70-100]" is RFdiffusion's own example: keep A1-150, break the chain so the binder
    is not fused to the target, then diffuse a binder whose length is sampled in that range.

    ONE SPAN PER SEGMENT, NOT ONE PER CHAIN
    A span is expanded to every residue number in it and each one is then looked up in the pdb
    (contigs.py:384, `assert val in self.parsed_pdb["pdb_idx"]`). So a chain written first to last
    claims every number between, and the first unresolved residue brings the run down on an
    assertion - which is nearly every crystal structure, and the design tools are given the
    cleaned receptor, which keeps the holes. 7A3R's chain A is six segments, not one.

    The two separators are not interchangeable. Segments of ONE chain go in one block, divided by
    "/": RFdiffusion then shows the model the real distance between them (expand_sampled_mask
    works out the jump from the numbering), so it reads them as one chain with residues missing.
    Different chains take "/0 " between them, which is a 200-residue jump - the model is told they
    are separate molecules, which they are. Writing "/0 " inside a chain would say the same of two
    halves of one protein; writing "/" between chains is refused outright, because a block's
    fragments must come from the same chain.
    """
    blocks = []
    for chain in sorted(target_chains):
        spans = segments(target_chains[chain])
        if not spans:
            continue
        if any(spans[i][0] > spans[i + 1][0] for i in range(len(spans) - 1)):
            raise SystemExit("chain %s is not numbered in ascending order, so no contig can "
                             "describe it: %s" % (chain, spans))
        blocks.append("/".join("%s%d-%d" % (chain, a, b) for a, b in spans) + "/0")
    return "[%s %d-%d]" % (" ".join(blocks), binder_min, binder_max)


def main():
    plan = json.load(open(sys.argv[1]))
    out = plan["out"]
    rf_dir = os.path.join(out, "rfdiffusion")
    mpnn_dir = os.path.join(out, "proteinmpnn")
    for d in (rf_dir, mpnn_dir):
        if not os.path.isdir(d):
            os.makedirs(d)

    target = plan["target"]
    wanted = [c.strip() for c in (plan.get("chains") or "A").split(",") if c.strip()]
    present = chains_of(target)
    keep = {c: present[c] for c in wanted if c in present}
    if not keep:
        raise SystemExit("none of the chains %s are in %s (it has %s)"
                         % (",".join(wanted), target, ",".join(sorted(present)) or "none"))

    # ---- RFdiffusion: backbones, with the target kept beside them -------------------------
    refuse_coded(plan.get("hotspots"))
    asked = residue_pairs(plan.get("hotspots"), sorted(keep)[0])
    refuse_missing(asked, keep)
    hotspots = ["%s%d" % (c, n) for c, n in asked]
    contigs = contig(keep, plan["binder_min"], plan["binder_max"])
    holes = sum(len(segments(numbers)) - 1 for numbers in keep.values())
    if holes:
        say("the target has %d gap(s) in its chain(s), so the contig keeps each stretch of "
            "resolved residues on its own: %s" % (holes, contigs))
    args = [plan["se3nv_python"], os.path.join(plan["rfdiffusion"], "scripts", "run_inference.py"),
            "inference.output_prefix=" + os.path.join(rf_dir, "design"),
            "inference.input_pdb=" + target,
            "contigmap.contigs=" + contigs,
            "inference.num_designs=%d" % plan["designs"]]
    if hotspots:
        args.append("ppi.hotspot_res=[%s]" % ",".join(hotspots))
    if plan.get("ckpt"):
        args.append("inference.ckpt_override_path="
                    + os.path.join(plan["rfdiffusion"], "models", plan["ckpt"]))
    if plan.get("diffuser_T"):
        args.append("diffuser.T=%d" % int(plan["diffuser_T"]))
    # The example lowers both noise scales to 0 for binder design, which is what makes the designs
    # worth folding. A person who asks for noise gets it on both, as RFdiffusion's own flags are.
    noise = float(plan.get("noise_scale") or 0)
    args += ["denoiser.noise_scale_ca=%g" % noise, "denoiser.noise_scale_frame=%g" % noise,
             # Hydra writes its log and config under the folder it was invoked in, which is the
             # shared install; told where to put them, it leaves nothing there.
             "hydra.run.dir=" + os.path.join(rf_dir, "hydra"),
             # Two fifty-model trajectory files per design, which nothing downstream reads.
             "inference.write_trajectory=False"]
    # The environment's own egg-link resolves rfdiffusion to a personal scratch folder that only
    # its owner can read, so the group tree is put on the path explicitly. Without this the card
    # works for one person and fails for everybody else with an ImportError.
    env = dict(os.environ, PYTHONPATH=plan["rfdiffusion"])
    run(args, where=plan["rfdiffusion"], env=env)

    # The target's own sequences, to recognise it again in what RFdiffusion writes - in the order
    # the contig names its chains, because that is the order RFdiffusion joins them in when it
    # writes them back as the one output chain. contig() takes them sorted, so this does too.
    target_seqs = [sequence_of(target, c) for c in sorted(keep)]
    backbones = sorted(f for f in os.listdir(rf_dir) if f.endswith(".pdb"))
    if not backbones:
        raise SystemExit("RFdiffusion wrote no designs into %s" % rf_dir)
    say("rfdiffusion: %d backbone(s)" % len(backbones))

    # ---- ProteinMPNN: a sequence for the binder, with the target fixed --------------------
    # Which chain is the binder is not a guess, and it is not the chain order either: RFdiffusion
    # writes the diffused binder FIRST, as chain A, and the target it kept after it, so taking the
    # last chain designs the target and fixes the binder. The chains are matched against the
    # target's own sequence - the whole of it, however many chains it went in as, because what
    # comes back is one chain - and what is left over is the binder.
    mpnn = os.path.join(plan["dl_binder_design"], "mpnn_fr", "ProteinMPNN")
    designs = []
    for name in backbones:
        path = os.path.join(rf_dir, name)
        here = chains_of(path)
        binder, target_chains = split_chains(path, here, target_seqs,
                                             plan["binder_min"], plan["binder_max"])
        if not binder or not target_chains:
            say("  %s: skipped, cannot tell the binder from the target in %s"
                % (name, ", ".join("%s:%d" % (c, len(n)) for c, n in sorted(here.items()))))
            continue
        folder = os.path.join(mpnn_dir, os.path.splitext(name)[0])
        if not os.path.isdir(folder):
            os.makedirs(folder)
        run([plan["se3nv_python"], os.path.join(mpnn, "protein_mpnn_run.py"),
             "--pdb_path", path,
             "--pdb_path_chains", binder,
             "--out_folder", folder,
             "--num_seq_per_target", str(plan["seqs_per_backbone"]),
             "--sampling_temp", "0.1",
             "--seed", "37",
             "--batch_size", "1"], where=mpnn, env=env)
        # ProteinMPNN writes seqs/<name>.fa: the native sequence first, then its designs, each
        # with its score in the header. The best design is the one with the lowest score.
        fasta = os.path.join(folder, "seqs", os.path.splitext(name)[0] + ".fa")
        best, best_score = "", None
        try:
            with open(fasta, errors="replace") as fh:
                header = ""
                for line in fh:
                    line = line.strip()
                    if line.startswith(">"):
                        header = line
                        continue
                    if not line or "score=" not in header:
                        continue
                    try:
                        score = float(re.search(r"score=([\d.]+)", header).group(1))
                    except (AttributeError, ValueError):
                        continue
                    # The first record is the backbone's own sequence, not a design: it is the
                    # one with sample= absent from its header.
                    if "sample=" not in header:
                        continue
                    # The line is the whole complex, chain by chain, separated by "/". The
                    # binder is taken by its length: its place in the line follows the file's
                    # chain order, which is not the order anything else here works in.
                    want = len(here[binder])
                    part = next((p for p in line.split("/") if len(p) == want), "")
                    if part and (best_score is None or score < best_score):
                        best, best_score = part, score
        except OSError:
            say("  %s: ProteinMPNN left no sequences" % name)
            continue
        if not best:
            say("  %s: no designed sequence" % name)
            continue
        designs.append({"name": os.path.splitext(name)[0], "path": path,
                        "binder_chains": [binder], "target_chains": target_chains,
                        "sequence": best, "score": best_score})
        say("  %s: %d residues, score %.3f" % (name, len(best), best_score))

    # Best first, so the next card can take the top few without knowing what the number means.
    designs.sort(key=lambda d: d["score"])
    with open(os.path.join(out, "designs.json"), "w") as fh:
        json.dump({"tool": "rfdiffusion", "designs": designs}, fh, indent=1)
        fh.write("\n")
    say("wrote designs.json: %d design(s)" % len(designs))
    if not designs:
        raise SystemExit("no design came through with a sequence")


if __name__ == "__main__":
    main()
