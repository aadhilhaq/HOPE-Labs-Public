#!/usr/bin/env python
"""Fold each design back from its sequence alone, and rank the designs by whether it worked.

By Aadhil Haq

RFD4-Proteina places a binder on the target; it says nothing about whether that binder's sequence
would fold into that shape. Until something does, the table of designs is in the order they came
out in, which is not a ranking, and a design that looks superb and folds into a different protein
is indistinguishable from one that works. This is the check that tells them apart.

WHY SINGLE SEQUENCE, AND NOT AN ALIGNMENT
Boltz-2 folds far better with an alignment, and a de novo binder has none: it is a sequence no
organism has ever carried, so a search returns its own target's homologues or nothing. Single
sequence is also what makes this answer mean something. The question is not "what is the best
structure anyone could get for this sequence", it is "does this sequence, on its own, specify the
backbone that was designed" - which is the self-consistency check the design field has used since
RFdiffusion, with AlphaFold2 single-sequence or ESMFold in the same place.

It is also the only thing that can run here. A design's sequence exists only once the design job
has run, on a compute node, and a compute node on this cluster reaches no network, so the
alignment server cannot be asked. Folding single sequence keeps the whole thing to one job and
one queue wait.

WHAT IS MEASURED
For each design, Boltz-2 is given the binder's sequence alone, with `msa: empty`, which is
Boltz-2's own word for single-sequence mode. Then:

    pLDDT       the model's own confidence in the structure it folded, 0 to 100. Below about 70
                the fold is not being predicted with any conviction and the rest means little.
    RMSD        alpha carbons of the refolded binder onto the designed binder, after alignment,
                in angstroms. This is the number that matters: under about 2 A the sequence
                folds into the shape it was designed for.

Both go into designs.json beside the design, and the designs are sorted by RMSD with the
unfolded ones last. Nothing is thrown away: a design that failed to fold keeps its row and says
so, because a run of eight designs where seven fail is a result about the run.

    rfd4_rank.py <plan.json>
"""
from __future__ import annotations

import glob
import json
import math
import os
import subprocess
import sys

#: What a design has to reach to be worth carrying on with, as the two numbers everybody quotes.
#: Not enforced: a design under neither is kept, marked, and sorted last, because the whole point
#: of the measurement is to see how many of them there are.
GOOD_RMSD = 2.0
GOOD_PLDDT = 70.0

#: Boltz-2's own word for folding from the sequence alone. A protein chain with no msa line at
#: all is one Boltz-2 would fetch an alignment for, and it refuses to mix the two.
NO_MSA = "empty"


def say(*what):
    print(" ".join(str(w) for w in what), flush=True)


def yaml_for(sequence):
    """One chain, folded from its sequence alone.

    Written by hand rather than with a YAML library: this runs inside RFD4-Proteina's image,
    which has no pyyaml, and the document is four lines.
    """
    return ("version: 1\nsequences:\n  - protein:\n      id: A\n      sequence: %s\n"
            "      msa: %s\n" % (sequence, NO_MSA))


def fold(plan, designs, where):
    """Fold every design's sequence, one Boltz-2 call for all of them, and say where each landed.

    One call rather than one per design: Boltz-2 loads a checkpoint of several gigabytes, and
    loading it eight times to fold eight short chains would cost more than the folding.
    """
    boltz = plan.get("boltz") or ""
    exe = os.path.join(boltz, "bin", "boltz")
    if not os.path.isfile(exe):
        raise SystemExit("Boltz-2 is not installed at %s, so the designs cannot be folded back. "
                         "Install it from the Set up page, or take the tick off." % boltz)
    inputs = os.path.join(where, "refold")
    os.makedirs(inputs, exist_ok=True)
    for one in designs:
        with open(os.path.join(inputs, "%s.yaml" % one["name"]), "w") as fh:
            fh.write(yaml_for(one["sequence"]))
    out = os.path.join(where, "refold_out")
    argv = [exe, "predict", inputs,
            # --cache, always: without it boltz falls back to ~/.boltz, finds nothing, and tries
            # to download six gigabytes from a node with no route out.
            "--cache", os.path.join(boltz, "cache"),
            "--out_dir", out, "--accelerator", "gpu", "--devices", "1",
            "--recycling_steps", "3", "--sampling_steps", "200", "--diffusion_samples", "1",
            "--output_format", "pdb", "--override"]
    if plan.get("seed") not in (None, ""):
        argv += ["--seed", str(int(plan["seed"]))]
    if not _has_kernels(boltz):
        argv.append("--no_kernels")
    say("  $ " + " ".join(argv))
    done = subprocess.run(argv, cwd=where)
    if done.returncode != 0:
        raise SystemExit("Boltz-2 refused to fold the designs (status %d). The designs "
                         "themselves are written and unaffected." % done.returncode)
    return out


def _has_kernels(install):
    """Whether this Boltz-2 was installed with its CUDA kernels, as the folding page asks."""
    return bool(glob.glob(os.path.join(install, "lib", "python3*", "site-packages",
                                       "cuequivariance_torch*")))


def folded_of(out, name):
    """(the structure Boltz-2 wrote for this design, its pLDDT) or ("", None)."""
    folder = os.path.join(out, "boltz_results_refold", "predictions", name)
    found = sorted(glob.glob(os.path.join(folder, "*_model_0.pdb"))) \
        or sorted(glob.glob(os.path.join(folder, "*.pdb")))
    if not found:
        return "", None
    plddt = None
    for path in sorted(glob.glob(os.path.join(folder, "confidence_*.json"))):
        try:
            with open(path) as fh:
                got = json.load(fh)
            value = got.get("complex_plddt")
            if isinstance(value, (int, float)):
                # Boltz-2's pLDDT is a fraction of one; everybody quotes it out of a hundred.
                plddt = round(value * 100, 1)
                break
        except (OSError, ValueError):
            continue
    return found[0], plddt


def _ca(path, chain=""):
    """The alpha carbons of a structure, in file order, as [(x, y, z)].

    A PDB or an mmCIF: RFD4-Proteina writes its designs as mmCIF and Boltz-2 is asked for PDB,
    so both arrive here. In an mmCIF from the design tool the atoms it never placed carry NaN for
    every coordinate (1,219 of 2,393 in a real MDM2 design, measured), and those are skipped: an
    alpha carbon is always placed, but a NaN taken in would make every sum here NaN.
    """
    if path.lower().endswith((".cif", ".mmcif")):
        return _ca_cif(path, chain)
    out = []
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                if line.startswith("ENDMDL"):
                    break
                if not line.startswith("ATOM") or line[12:16].strip() != "CA":
                    continue
                if chain and line[21:22].strip() != chain:
                    continue
                alt = line[16:17].strip()
                if alt and alt not in ("A", "1"):
                    continue
                try:
                    out.append((float(line[30:38]), float(line[38:46]), float(line[46:54])))
                except ValueError:
                    continue
    except OSError:
        return []
    return out


def _ca_cif(path, chain=""):
    """The alpha carbons of an mmCIF, by the atom_site loop's own column names."""
    try:
        with open(path, errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    order, first = [], None
    for n, line in enumerate(lines):
        bare = line.strip()
        if bare.lower().startswith("_atom_site."):
            order.append(bare.split(".", 1)[1].strip().lower())
        elif order and bare and not bare.startswith("#"):
            first = n
            break
    if first is None:
        return []
    at = {name: n for n, name in enumerate(order)}
    chain_col = at.get("auth_asym_id", at.get("label_asym_id"))
    out = []
    for line in lines[first:]:
        bare = line.strip()
        if not bare or bare.startswith(("#", "_", "loop_", "data_")):
            break
        parts = bare.split()
        if len(parts) < len(order):
            continue
        if parts[at["label_atom_id"]].strip('"') != "CA":
            continue
        if chain and chain_col is not None and parts[chain_col] != chain:
            continue
        try:
            xyz = tuple(float(parts[at[k]]) for k in ("cartn_x", "cartn_y", "cartn_z"))
        except (KeyError, ValueError):
            continue
        if all(math.isfinite(c) for c in xyz):
            out.append(xyz)
    return out


def rmsd(a, b):
    """Alpha-carbon RMSD of two equal-length chains after a best-fit superposition, in angstroms.

    Kabsch, written out with the standard library: this runs inside the design tool's image,
    where numpy is present, but the helper beside it (rfd4_design.py) has shown that the image's
    numpy is the tool's own and worth leaving alone. Three-by-three matrices do not need it.
    """
    if not a or len(a) != len(b):
        return None
    n = len(a)
    ca = [sum(p[i] for p in a) / n for i in range(3)]
    cb = [sum(p[i] for p in b) / n for i in range(3)]
    x = [[p[i] - ca[i] for i in range(3)] for p in a]
    y = [[p[i] - cb[i] for i in range(3)] for p in b]
    # The covariance matrix, and its optimal rotation by the quaternion method: the largest
    # eigenvalue of the 4x4 key matrix gives the best-fit residual directly, so no eigenvector
    # and no matrix inversion is needed for an RMSD.
    r = [[sum(x[k][i] * y[k][j] for k in range(n)) for j in range(3)] for i in range(3)]
    f = [
        [r[0][0] + r[1][1] + r[2][2], r[1][2] - r[2][1], r[2][0] - r[0][2], r[0][1] - r[1][0]],
        [r[1][2] - r[2][1], r[0][0] - r[1][1] - r[2][2], r[0][1] + r[1][0], r[0][2] + r[2][0]],
        [r[2][0] - r[0][2], r[0][1] + r[1][0], -r[0][0] + r[1][1] - r[2][2], r[1][2] + r[2][1]],
        [r[0][1] - r[1][0], r[0][2] + r[2][0], r[1][2] + r[2][1], -r[0][0] - r[1][1] + r[2][2]],
    ]
    top = _largest_eigenvalue(f)
    inner = sum(sum(v * v for v in p) for p in x) + sum(sum(v * v for v in p) for p in y)
    value = (inner - 2 * top) / n
    return round(math.sqrt(max(0.0, value)), 3)


def _largest_eigenvalue(m, rounds=400):
    """The largest eigenvalue of a symmetric 4x4, by power iteration on a shifted matrix.

    Power iteration finds the eigenvalue largest in MAGNITUDE, and this matrix's eigenvalues sum
    to nothing, so its most negative one can outweigh its largest and be returned instead. The
    matrix is shifted by its own Frobenius norm first, which bounds every eigenvalue's size, so
    every shifted one is non-negative and the largest is the one found; the shift is then taken
    off again.
    """
    shift = math.sqrt(sum(m[i][j] ** 2 for i in range(4) for j in range(4)))
    s = [[m[i][j] + (shift if i == j else 0.0) for j in range(4)] for i in range(4)]
    v = [0.5, 0.5, 0.5, 0.5]
    value = 0.0
    for _ in range(rounds):
        w = [sum(s[i][j] * v[j] for j in range(4)) for i in range(4)]
        size = math.sqrt(sum(c * c for c in w))
        if size < 1e-12:
            return -shift
        v = [c / size for c in w]
        value = sum(v[i] * sum(s[i][j] * v[j] for j in range(4)) for i in range(4))
    return value - shift


def rank(where, designs, out):
    """Measure every design against its refold, write it in, and sort them by it."""
    for one in designs:
        folded, plddt = folded_of(out, one["name"])
        one["plddt"] = plddt
        one["refold"] = os.path.relpath(folded, where) if folded else ""
        one["fold_rmsd"] = None
        if not folded:
            one["fold_note"] = "Boltz-2 wrote no structure for this one"
            continue
        designed = _ca(one["path"], one.get("binder_chains", [""])[0] if one.get("binder_chains") else "")
        refolded = _ca(folded)
        if len(designed) != len(refolded):
            one["fold_note"] = ("the refold has %d alpha carbons and the design has %d, so the "
                                "two were not compared" % (len(refolded), len(designed)))
            continue
        one["fold_rmsd"] = rmsd(designed, refolded)
        one["folds"] = bool(one["fold_rmsd"] is not None and one["fold_rmsd"] <= GOOD_RMSD
                            and (plddt or 0) >= GOOD_PLDDT)
        one["fold_note"] = ""
    # Sorted by how well the sequence folds back, which is the first ranking these designs have
    # ever had. A design that could not be measured sorts last rather than first, which is what
    # sorting by a missing number would otherwise do.
    designs.sort(key=lambda d: (d.get("fold_rmsd") is None, d.get("fold_rmsd") or 0,
                                -(d.get("plddt") or 0)))
    for n, one in enumerate(designs, 1):
        one["rank"] = n
    return designs


def main(plan_path):
    with open(plan_path) as fh:
        plan = json.load(fh)
    where = plan["out"]
    record = os.path.join(where, "designs.json")
    with open(record) as fh:
        doc = json.load(fh)
    designs = doc.get("designs") or []
    if not designs:
        raise SystemExit("there are no designs in %s to fold back" % record)
    say("folding %d design(s) back from their sequence alone" % len(designs))
    out = fold(plan, designs, where)
    rank(where, designs, out)
    doc["designs"] = designs
    doc["ranked_by"] = ("the alpha-carbon RMSD of each design's own sequence, folded by Boltz-2 "
                        "from the sequence alone, onto the design")
    doc["fold_thresholds"] = {"rmsd": GOOD_RMSD, "plddt": GOOD_PLDDT}
    with open(record, "w") as fh:
        json.dump(doc, fh, indent=1)
        fh.write("\n")
    good = sum(1 for one in designs if one.get("folds"))
    say("  %d of %d fold back within %.1f A at pLDDT %d or better"
        % (good, len(designs), GOOD_RMSD, GOOD_PLDDT))
    for one in designs:
        say("  %-22s rmsd %-7s plddt %-6s %s"
            % (one["name"], one.get("fold_rmsd"), one.get("plddt"),
               "folds" if one.get("folds") else (one.get("fold_note") or "")))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
