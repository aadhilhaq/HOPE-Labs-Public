#!/usr/bin/env python
"""Prepare every molecule of every structure the preparation card was given, and put them back
together.

Run by HOPE-MD's own Python, not by the flow's: the chemistry here is HOPE-MD's Prepare stage,
called once per molecule, and nothing of it is written again. What this file adds is the splitting
and the putting back together, the record of what was decided for each molecule, and the charge
check over the result.

Why per molecule at all. A predicted or docked structure is coordinates, not chemistry: its
histidines have no state, its termini and its breaks are not settled, its disulfides are
unmeasured, and its gaps are holes. Protonating the assembled complex answers those questions for
the interface as well, which is the one place where the answer depends on how the two molecules
happen to have been put together. So the receptor and the partner are each prepared on its own, at
one pH, and the complex is reassembled from the results - after which HOPE-MD is told not to do it
again (system.prep.prepared_per_molecule).

A molecule here is a side, not a chain. A receptor of several chains - a dimer held by disulfides
between its chains - is one molecule, and preparing its chains apart would protonate
those cysteines as free thiols and lose the bonds; a duplex partner is two strands of one partner.
So the receptor's chains are prepared together, the partner's together, and a chain on neither side
on its own.

What goes into each molecule's preparation is the molecule as it was deposited, whatever has been
done to it since. Its chains' SEQRES and REMARK 465 records go with it, because the sequence is
what PDBFixer finds a gap by: without them it finds none, and a flow built no loop where HOPE-MD
run on the same file builds sixteen residues in five (7A3R chain A). And what an earlier
preparation added comes off: every hydrogen, and the OXT at a break inside a chain (not the one at
its true C terminus). Left on, pdb2pqr read each piece of a broken chain as a chain of its own and
gave it a new letter, so a structure prepared twice was not the structure prepared once. The names
that carry a protonation state (HID, CYX ...) stay: pdb2pqr reads them as the states they are, so
the states a second preparation gives are the first one's.

The waters and the hetero groups are the card below's question, and the plan says which card that
is (below). Into HOPE-MD they are carried past the molecules as they arrived, since HOPE-MD's own
Prepare already decides which to keep and which side each belongs to. Into a docking card they
are not: a docking has no use for a crystal water or a co-crystallised inhibitor, and either left
in the site is something the partner is docked against. There only the ions the docking tool can
use are kept, and what was dropped is written down.

A receptor for the docking is the same work in one shape (the plan's `receptor`, written by the
Prepare receptor card). The target's own chains are the one molecule, and every other chain of the
file is left out rather than prepared beside it: a chain the target does not name is not part of
what is docked against, and an inhibitor's chain kept in the file is a partner sitting in the site.
The waters and hetero groups go as they do into any docking. And the hotspots are checked against
what came out, residue by residue, because they are written in the deposited numbering and the
preparation is where a numbering could be lost: each is present (the same chain, number, insertion
code and residue, a protonation-state name apart), built (a residue the file was missing and the
loop modelling put back), or missing.

    python prepare_molecules.py plan.json

The plan names where to work, the pH, HOPE-MD's own prep settings, the card below, and the
structures to prepare. prepared.json is written in that folder, best first, and is what the next
card reads.
"""
import json
import os
import re
import shutil
import sys
from pathlib import Path

#: What each kind of residue contributes to the net charge, by the names a prepared structure
#: carries. These are pdb2pqr's AMBER names, which is what HOPE-MD's Prepare writes: the neutral
#: forms (ASH, GLH, LYN, HID, HIE, CYS, CYX) are named and worth nothing, and only the charged
#: ones are counted. A nucleic acid is not in here at all: its charge is its phosphates, which are
#: counted as atoms, so a strand that lost its 5'-phosphate is one less rather than one too many.
RESIDUE_CHARGE = {"ASP": -1, "GLU": -1, "LYS": 1, "ARG": 1, "HIP": 1, "CYM": -1}
POLYMER = ("protein", "nucleic")
#: Every chain letter a recombined structure can use, in the order a free one is taken.
CHAIN_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

#: The names a prepared residue may carry in place of the one it was deposited with: its
#: protonation state, in AMBER's names and CHARMM's for histidine, and selenomethionine, which
#: PDBFixer makes methionine. A hotspot named either way is the same residue.
SAME_RESIDUE = {"HID": "HIS", "HIE": "HIS", "HIP": "HIS", "HSD": "HIS", "HSE": "HIS",
                "HSP": "HIS", "CYX": "CYS", "CYM": "CYS", "ASH": "ASP", "GLH": "GLU",
                "LYN": "LYS", "MSE": "MET"}
#: The caps on the two ends of a gap left open. They are numbered as the missing residues next to
#: the gap, so a hotspot that names one of those finds a cap, not the residue.
CAPS = frozenset({"ACE", "NME", "NMA"})

#: The plan this run was given, read in main(). A module global because what was asked for - the
#: pH, the prep settings - is read by the steps rather than threaded through every one of them.
PLAN = {}


def say(*what):
    print(*what, file=sys.stderr, flush=True)


def one_molecule(text, chains):
    """The polymer residues of these chains, as a PDB of their own, each chain its own TER block,
    with the records of those chains that say what was deposited (SEQRES, REMARK 465).

    Only the polymer: the ions, the cofactors and the waters are not molecules anybody protonates,
    and what becomes of them is decided where the structure goes next (see the module's
    docstring). What an earlier preparation added is taken off (as_deposited), so preparing a
    prepared structure starts from what preparing the original started from.
    """
    from hopemd.structure import classify
    head, out, last = [], [], None
    for line in text.splitlines():
        if line.startswith("SEQRES") and (line[11:12].strip() or "A") in chains:
            head.append(line)
        elif line.startswith("REMARK 465") and _missing_residue(line) and line[19] in chains:
            head.append(line)
        elif line.startswith(("ATOM", "HETATM")):
            cid = line[21].strip() or "A"
            if cid in chains and classify(line[17:20]) in POLYMER:
                if last is not None and cid != last and not out[-1].startswith("TER"):
                    out.append("TER")
                out.append(line)
                last = cid
        elif line.startswith("TER") and out and not out[-1].startswith("TER"):
            out.append("TER")
    out = as_deposited(out)
    if not any(l.startswith(("ATOM", "HETATM")) for l in out):
        return ""
    if not out[-1].startswith("TER"):
        out.append("TER")
    return "\n".join(head + out) + "\nEND\n"


def _missing_residue(line):
    """Whether a REMARK 465 line names a missing residue (rather than being its table's heading)."""
    return (len(line) >= 26 and line[15:18].strip().isalpha()
            and line[21:26].strip().lstrip("-").isdigit())


def _hydrogen(line):
    element = line[76:78].strip().upper()
    if element:
        return element in ("H", "D")
    name = line[12:16].strip()
    return name[:1] in ("H", "D") or (name[:1].isdigit() and name[1:2] in ("H", "D"))


def as_deposited(lines):
    """These coordinate lines without what a preparation adds: every hydrogen, and the OXT at a
    break inside a chain.

    The OXT at a chain's true C terminus stays, as a deposited structure has it. One on any other
    residue is a break an earlier preparation ended, and pdb2pqr reads a residue with an OXT as the
    end of a chain: it gave the piece after each break a chain letter of its own, so restraints
    missed, atoms moved and a two-chain receptor came back with chains it did not have. The break
    is found again where the residues are, and ended again the same way.
    """
    atoms = [l for l in lines if l.startswith(("ATOM", "HETATM"))]
    last = {}
    for line in atoms:
        last[line[21]] = line[22:27]
    out = []
    for line in lines:
        if line.startswith(("ATOM", "HETATM")):
            if _hydrogen(line):
                continue
            if line[12:16].strip() == "OXT" and line[22:27] != last.get(line[21]):
                continue
        out.append(line)
    return out


def hetero_lines(text):
    """The lines that are not polymer: ions, metals, cofactors, waters, in file order."""
    from hopemd.structure import classify
    return [l for l in text.splitlines()
            if l.startswith(("ATOM", "HETATM")) and classify(l[17:20]) not in POLYMER]


def for_the_card_below(lines, below):
    """(the hetero lines to put back, what was dropped), by the card the structure goes to.

    Into HOPE-MD every one goes back as it arrived: HOPE-MD's Prepare decides which waters, ions
    and cofactors stay and which side of the interface each belongs to. Into anything else - a
    docking card - only a single-atom ion whose name the plan lists as one that tool can use; every
    water and every other group goes, and is named. A plan that does not say which card is below
    is read as a docking: dropping is what can be put right, a co-crystallised inhibitor docked
    against is not.
    """
    from hopemd.structure import classify, group_label, hetero_groups
    if (below or {}).get("card") == "hopemd":
        return lines, {}
    usable = {str(n).strip().upper() for n in (below or {}).get("ions") or []}
    text = "\n".join(lines)
    waters = {(l[21], l[22:27]) for l in lines if classify(l[17:20]) == "water"}
    kept, gone = [], []
    for group in hetero_groups(text):
        if len(group["lines"]) == 1 and group["resname"].upper() in usable:
            kept.extend(group["lines"])
        else:
            gone.append(group_label(group))
    dropped = {"for": (below or {}).get("card") or "not said",
               "waters": len(waters), "groups": gone,
               "ions_kept": len(kept)}
    return kept, dropped


def on_chain(text, chain):
    """These lines with their chain letter set to this one, whatever came back from the tools."""
    out = []
    for line in text.splitlines():
        if line.startswith(("ATOM", "HETATM")) and len(line) > 21:
            out.append(line[:21] + chain + line[22:])
        else:
            out.append(line)
    return "\n".join(out)


def net_charge(text):
    """(the net charge of this structure, how it is made up).

    The charged residues by name, the phosphates by their phosphorus atoms, and the ions by the
    charge the build gives them. It is the number to look at before a run: a 20-mer DNA with OH
    termini is -19, and a complex that comes out at nothing when it should be strongly negative is
    a structure whose names are not what anybody thinks they are.
    """
    from hopemd.structure import classify, hetero_groups
    total, made = 0, {}
    seen = set()
    for line in text.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        name, kind = line[17:20].strip(), classify(line[17:20])
        key = (line[21], line[22:27], name)
        if kind == "protein" and key not in seen and name in RESIDUE_CHARGE:
            seen.add(key)
            total += RESIDUE_CHARGE[name]
            made[name] = made.get(name, 0) + 1
        elif kind == "nucleic":
            atom = line[12:16].strip()
            if atom == "P":
                total -= 1
                made["phosphate"] = made.get("phosphate", 0) + 1
    for group in hetero_groups(text):
        if group["ion"]:
            total += int(group["ion"][1])
            made[group["resname"]] = made.get(group["resname"], 0) + 1
    return total, made


def disulfides(path):
    """The SG-SG pairs in a structure, measured as HOPE-MD measures them at import.

    Measured on the complex and not on one molecule, because a disulfide can hold two molecules
    together. A pair that
    crosses two molecules is named as such in the record, since it means the two are one covalent
    object whatever the chains say.
    """
    from hopemd.importers.files import inspect_pdb
    try:
        return [list(pair) for pair in inspect_pdb(path).disulfides]
    except Exception as why:                                        # noqa: BLE001
        say("   the disulfides could not be measured (%s)" % why)
        return []


def prepared_molecule(text, chains, rundir, plan):
    """One molecule - a side, of one chain or several - through HOPE-MD's own Prepare.
    Returns (its prepared PDB text, the report).

    Through `hopemd.prepare.prepare` rather than anything of ours: it is the stage the lab's runs
    have always been prepared by - clean, PDBFixer's loops and missing atoms, propka at the pH,
    the termini and the breaks, the restrained minimisation - and a second implementation of that
    would be a second answer to every question it settles.
    """
    import hopemd.prepare
    from hopemd.prepare import prepare
    (rundir / "input").mkdir(parents=True, exist_ok=True)
    (rundir / "input" / "complex.pdb").write_text(text)
    prep = dict(plan.get("prep") or {})
    spec = {"name": "molecule_%s" % rundir.name,
            "system": {"ph": float(plan.get("ph") or 7.4), "prep": prep,
                       "receptor_chains": list(chains), "partner_chains": [],
                       "source": {"kind": "complex",
                                  "path": str(rundir / "input" / "complex.pdb")}}}
    # A loop that will not fit stops the preparation with a sentence that names the setting to lower;
    # here that is the card's own box (a HOPE-MD older than the shared loop method has no such name).
    # Only for this call: anything else in the same process, a test among them, keeps HOPE-MD's own.
    named = hasattr(hopemd.prepare, "LOOPS_SETTING")
    if named:
        was, hopemd.prepare.LOOPS_SETTING = hopemd.prepare.LOOPS_SETTING, "Longest gap to model, residues"
    try:
        report = prepare(rundir, spec)
    finally:
        if named:
            hopemd.prepare.LOOPS_SETTING = was
    got = rundir / "build" / "prepared.pdb"
    if not got.is_file():
        raise RuntimeError("Prepare left no build/prepared.pdb for chain(s) %s" % ",".join(chains))
    return got.read_text(), report


def molecule_record(chain, kind, text, report):
    """What was decided for this molecule, in the words of the report that decided it."""
    protonate = (report.get("steps") or {}).get("protonate") or {}
    model = (report.get("steps") or {}).get("model") or {}
    charge, made = net_charge(text)
    residues = len({(l[21], l[22:27]) for l in text.splitlines() if l.startswith("ATOM")})
    return {"chain": chain, "kind": kind, "residues": residues, "charge": charge,
            "charge_from": made,
            "protonation": {"method": protonate.get("method", ""),
                            "ph": protonate.get("ph"),
                            "states": protonate.get("states") or {}},
            # a chain's missing ends are never built: HOPE-MD builds only the gaps inside a chain
            "termini": "as the structure had them",
            "gaps_modelled": model.get("loops_modelled") or {},
            "built": model.get("built") or [],
            "loops_relaxed": model.get("relaxed") or {},
            "gaps_left": model.get("gaps_left") or {},
            "gaps_left_why": model.get("gaps_left_why") or {},
            "breaks": report.get("breaks") or [],
            "phosphates_5p": report.get("phosphates_5p") or [],
            "minimised": bool((report.get("steps") or {}).get("minimise", {}).get("done")),
            "log": list(report.get("log") or [])}


def staged(entry, work):
    """(the structure to prepare as PDB text, its receptor chains, its partner chains, notes).

    Two ways a structure arrives, and the one that is not a file is staged by HOPE-MD's own
    importer: an ADCP pose is a receptor and a partner in two files, which that importer is
    the one that knows how to find.

    The model is chosen here and nowhere else. read_structure takes one model out of a multi-model
    file and leaves the rest, so by the time anything is split there is only ever one: the card's
    Model to take has to be read at this call or it is read after the choice has been made for it,
    silently, as the first.
    """
    kind = str(entry.get("kind") or "complex")
    notes = []
    model = int((PLAN.get("prep") or {}).get("model") or 1)
    if kind == "complex":
        from hopemd.structure import read_structure
        text = read_structure(entry["path"], model=model)
        if model != 1:
            notes.append("model %d of the file is the one prepared" % model)
        rec = [c for c in (entry.get("receptor_chains") or []) if c]
        par = [c for c in (entry.get("partner_chains") or []) if c]
        if not rec and not par:
            from hopemd.structure import inventory, default_split
            rec, par, _ = default_split(inventory(text).chains)
            notes.append("the file does not say which chains are which, so the split is the one "
                         "the import would start with: %s against %s"
                         % (",".join(rec) or "none", ",".join(par) or "none"))
        return text, rec, par, notes
    target = work / "import"
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    (target / "input").mkdir(parents=True, exist_ok=True)
    if kind == "adcp":
        from hopemd.importers.adcp import import_adcp
        import_adcp(entry["run"], entry["peptide"], int(entry.get("pose") or 1), target)
    else:
        raise RuntimeError("nothing here stages a %r source" % kind)
    rec_text = (target / "input" / "receptor.pdb").read_text()
    lig = target / "input" / "ligand.pdb"
    if not lig.is_file():
        raise RuntimeError("the import left no partner for this pose, so there is no complex "
                           "to prepare (it wrote %s)"
                           % ", ".join(sorted(p.name for p in (target / "input").iterdir())))
    rec, par, text, said = joined(rec_text, lig.read_text())
    notes.extend(said)
    return text, rec, par, notes


def chains_of(text):
    """The chain letters this PDB text holds, in file order."""
    out = []
    for line in text.splitlines():
        if line.startswith(("ATOM", "HETATM")):
            cid = line[21].strip() or "A"
            if cid not in out:
                out.append(cid)
    return out


def joined(receptor, partner):
    """A receptor file and a partner file as one structure, with the partner's chains kept apart.

    (its receptor chains, its partner chains, the text, what was said). The two files are written
    by an importer that hands them to the build separately, so both can call their chain A: put
    together without a word, the two molecules would become one chain and be prepared as one.
    """
    rec = chains_of(receptor)
    said, renamed = [], {}
    lines = [l for l in receptor.splitlines() if l.startswith(("ATOM", "HETATM", "TER"))]
    used = list(rec)
    for cid in chains_of(partner):
        if cid not in used:
            renamed[cid] = cid
            used.append(cid)
            continue
        free = next((c for c in CHAIN_LETTERS if c not in used), "")
        if not free:
            raise RuntimeError("there is no free chain letter for the partner's chain %s" % cid)
        renamed[cid] = free
        used.append(free)
        said.append("the partner's chain %s is called %s here: the receptor already has a %s, and "
                    "two molecules sharing a letter would be prepared as one" % (cid, free, cid))
    if lines and not lines[-1].startswith("TER"):
        lines.append("TER")
    for line in partner.splitlines():
        if line.startswith(("ATOM", "HETATM")):
            lines.append(line[:21] + renamed.get(line[21].strip() or "A", line[21]) + line[22:])
        elif line.startswith("TER") and lines and not lines[-1].startswith("TER"):
            lines.append("TER")
    if lines and not lines[-1].startswith("TER"):
        lines.append("TER")
    return rec, [renamed[c] for c in chains_of(partner)], "\n".join(lines) + "\nEND\n", said


def hotspot_keys(text, default_chain):
    """[(chain, residue)] from a target card's hotspots, in the order they were typed, once each.

    The forms the card takes: 54, A54, A:54, a range 66-70, an insertion code after the number as
    H60A. A residue with no chain is the target's first chain's, as every docking driver reads it
    (drivers._site), so the residue checked here is the residue the docking will aim at.
    """
    out = []
    for part in re.split(r"[,;\s]+", str(text or "")):
        found = re.match(r"^([A-Za-z]?):?(-?\d+)(?:([A-Za-z])|-(-?\d+))?$", part.strip())
        if not found:
            continue
        chain = found.group(1) or default_chain
        first, code, last = int(found.group(2)), (found.group(3) or "").upper(), found.group(4)
        numbers = ["%d%s" % (first, code)] if code or not last else \
            ["%d" % n for n in range(first, int(last) + 1)]
        for number in numbers:
            if (chain, number) not in out:
                out.append((chain, number))
    return out


def residue_table(text, chains=None):
    """{(chain, number with its insertion code): (name, CA or None)} of the polymer ATOM records."""
    out = {}
    for line in text.splitlines():
        if not line.startswith(("ATOM", "HETATM")) or len(line) < 54:
            continue
        chain = line[21].strip() or "A"
        if chains and chain not in chains:
            continue
        key = (chain, line[22:27].strip())
        name, ca = (out.get(key) or (line[17:20].strip(), None))
        if line[12:16].strip() == "CA" and ca is None:
            try:
                ca = tuple(float(line[c:c + 8]) for c in (30, 38, 46))
            except ValueError:
                ca = None
        out[key] = (name, ca)
    return out


def check_hotspots(deposited, prepared, asked, chains):
    """Each hotspot, against the structure as it went in and as it came out.

    {"asked": what the card said, "residues": [{"residue": "A:54", "name": "ALA", "state":
    present | built | missing, "moved": the CA's move in Å, "why": for one that is missing}],
    "built": [...], "missing": [...]}. Present is the same chain, number, insertion code and
    residue, a protonation-state name apart; built is a residue the file did not have and the
    preparation does, which is a loop that was modelled; missing is everything else, with the
    reason.
    """
    before = residue_table(deposited, chains)
    after = residue_table(prepared, chains)
    rows = []
    for chain, number in hotspot_keys(asked, (chains or ["A"])[0]):
        key = (chain, number)
        label = "%s:%s" % key
        was, now = before.get(key), after.get(key)
        if now is None:
            rows.append({"residue": label, "name": was[0] if was else "", "state": "missing",
                         "why": ("was lost in the preparation" if was else
                                 "is not in chain %s of the structure" % chain
                                 if any(k[0] == chain for k in after) else
                                 "names chain %s, which the receptor has not got" % chain)})
        elif was is None and now[0] in CAPS:
            # A gap left open is capped, and the cap takes the number of the missing residue
            # whose backbone it stands in for: the residue itself is still missing.
            rows.append({"residue": label, "name": now[0], "state": "missing",
                         "why": "is missing: the gap it is in was left open, and %s caps it"
                                % now[0]})
        elif was is None:
            rows.append({"residue": label, "name": now[0], "state": "built"})
        elif SAME_RESIDUE.get(now[0], now[0]) != SAME_RESIDUE.get(was[0], was[0]):
            rows.append({"residue": label, "name": was[0], "state": "missing",
                         "why": "is a %s in the prepared receptor and a %s in the target"
                                % (now[0], was[0])})
        else:
            moved = None
            if was[1] and now[1]:
                moved = round(sum((a - b) ** 2 for a, b in zip(was[1], now[1])) ** 0.5, 3)
            rows.append({"residue": label, "name": was[0], "state": "present", "moved": moved})
    return {"asked": str(asked or ""), "residues": rows,
            "built": [r["residue"] for r in rows if r["state"] == "built"],
            "missing": [r["residue"] for r in rows if r["state"] == "missing"]}


def prepare_structure(entry, out):
    """One structure: split, prepare each molecule, put it back together, check the charge.

    In receptor mode (PLAN["receptor"]) the one molecule is the target's chains, nothing else of
    the file is kept, and the hotspots are checked against the result.
    """
    from hopemd.structure import inventory
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", str(entry.get("name") or "structure"))
    work = out / name
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    text, rec, par, notes = staged(entry, work)
    inv = inventory(text)
    chains = [c for c in inv.chains if c["kind"] in POLYMER]
    if not chains:
        raise RuntimeError("there is no protein or nucleic-acid chain in this structure")
    receptor = PLAN.get("receptor")
    if receptor is not None:
        # The target's chains alone: the chains it names, or every polymer chain where it names
        # none, which is what "empty takes every chain in the file" on the card says.
        named = [c for c in (receptor.get("chains") or []) if c]
        have = [c["id"] for c in chains]
        absent = [c for c in named if c not in have]
        if absent:
            raise RuntimeError("the target names chain(s) %s, and the structure has %s"
                               % (",".join(absent), ",".join(have)))
        rec, par = (named or have), []
        left = [c for c in have if c not in rec]
        if left:
            notes.append("chain(s) %s left out: the target does not name them, and a chain "
                         "kept beside the receptor is docked against" % ",".join(left))
            say("   %s" % notes[-1])
    elif not rec and not par:
        rec = [c["id"] for c in chains]
    elif not rec:
        rec = [c["id"] for c in chains if c["id"] not in par]
    elif not par:
        par = [c["id"] for c in chains if c["id"] not in rec]

    # One molecule per side: the receptor's chains together, the partner's together, and any
    # chain on neither side on its own (see the module's docstring for why not one per chain).
    # A receptor for the docking is its one side and nothing more.
    kind_of = {c["id"]: c["kind"] for c in chains}
    groups = [("receptor", [c for c in rec if c in kind_of]),
              ("partner", [c for c in par if c in kind_of])]
    if receptor is None:
        groups += [("chain_%s" % c, [c]) for c in kind_of if c not in rec and c not in par]
    pieces, molecules = [], []
    for side, ids in groups:
        mine = one_molecule(text, ids)
        if not ids or not mine.strip():
            continue
        kinds = sorted({kind_of[c] for c in ids})
        say("   %s: chain(s) %s, %s" % (side, ",".join(ids), " and ".join(kinds)))
        done, report = prepared_molecule(mine, ids, work / side, PLAN)
        if len(ids) == 1:
            done = on_chain(done, ids[0])
        came_back = set(chains_of(done))
        if not came_back <= set(ids):
            raise RuntimeError("the %s came back from Prepare with chain(s) %s, not its own %s"
                               % (side, ",".join(sorted(came_back - set(ids))), ",".join(ids)))
        kept = [l for l in done.splitlines() if l.startswith(("ATOM", "HETATM", "TER"))]
        while kept and kept[-1].startswith("TER"):
            kept.pop()                       # the join below puts one TER between molecules
        pieces.append("\n".join(kept))
        record = molecule_record(",".join(ids), kinds[0] if len(kinds) == 1 else "mixed",
                                 done, report)
        record["side"] = "partner" if side == "partner" else "receptor"
        record["chains"] = ids
        record["rundir"] = side
        molecules.append(record)
    if not molecules:
        raise RuntimeError("none of the chains of this structure held a molecule to prepare")

    # Put back together in the order the chains were in, each molecule its own TER block, then the
    # ions, cofactors and waters the card below takes (for_the_card_below): into HOPE-MD exactly as
    # they arrived, since its Prepare is where that question is already answered (which side of
    # the interface each belongs to, which the build has parameters for, which are dropped); into
    # a docking only the ions it can use, and the rest written down as dropped.
    hetero = hetero_lines(text)
    if receptor is not None:
        hetero = [l for l in hetero if (l[21].strip() or "A") in rec]
    carried, dropped = for_the_card_below(hetero, PLAN.get("below"))
    if dropped:
        say("   for the %s below: %d water(s) and %d other group(s) dropped%s, %d ion(s) kept"
            % (dropped["for"], dropped["waters"], len(dropped["groups"]),
               (" (%s%s)" % (", ".join(dropped["groups"][:8]),
                             ", ..." if len(dropped["groups"]) > 8 else ""))
               if dropped["groups"] else "", dropped["ions_kept"]))
    body = "\nTER\n".join(pieces) + "\nTER\n"
    if carried:
        body += "\n".join(carried) + "\nTER\n"
    got = work / "prepared.pdb"
    got.write_text(body + "END\n")

    # The charge check: what the molecules came to on their own, plus what the ions carried past
    # them are worth, against what the recombined structure actually holds. They have to agree -
    # if they do not, a residue lost its name or an atom went missing between the two, which is a
    # structure that would still build and still produce a number.
    charge, made = net_charge(got.read_text())
    apart = sum(m["charge"] for m in molecules)
    ions = net_charge("\n".join(carried))[0] if carried else 0
    check = {"net_charge": charge, "molecules_apart": apart, "from_carried": ions,
             "made_up_of": made, "agrees": apart + ions == charge}
    if not check["agrees"]:
        say("   the charge does not add up: the molecules come to %+d and what rode through them "
            "to %+d, and the structure holds %+d" % (apart, ions, charge))
    ss = disulfides(got)
    side_of = {c: m["rundir"] for m in molecules for c in m["chains"]}
    between = [pair for pair in ss
               if side_of.get(pair[0].split(":")[0]) != side_of.get(pair[1].split(":")[0])]
    for one in molecules:
        one["disulfides"] = [p for p in ss if p[0].split(":")[0] in one["chains"]
                             and p[1].split(":")[0] in one["chains"]]
    hotspots = {}
    if receptor is not None:
        hotspots = check_hotspots(one_molecule(text, rec), got.read_text(),
                                  receptor.get("hotspots"), rec)
        for row in hotspots["residues"]:
            say("   hotspot %s %s: %s%s" % (row["residue"], row["name"], row["state"],
                                           (" (%s)" % row["why"]) if row.get("why") else
                                           (", CA moved %.2f A" % row["moved"])
                                           if row.get("moved") is not None else ""))
    return {"name": name, "path": str(got), "from": entry.get("from", ""),
            "receptor": receptor is not None, "hotspots": hotspots,
            "receptor_chains": rec, "partner_chains": par,
            "score": entry.get("score"), "rank": entry.get("rank"),
            "source_kind": str(entry.get("kind") or "complex"),
            "molecules": molecules, "disulfides": ss, "disulfides_between_molecules": between,
            "carried_atoms": len(carried), "hetero_dropped": dropped, "charge": check,
            "notes": notes}


def main(argv):
    global PLAN
    PLAN = json.loads(Path(argv[1]).read_text())
    out = Path(PLAN["out"])
    out.mkdir(parents=True, exist_ok=True)
    # Nothing of any of this may reach a home folder: the caches these tools write there run into
    # ten thousand files, and the quota is ten thousand files.
    os.environ.setdefault("MPLCONFIGDIR", str(out / "cache"))
    os.environ.setdefault("XDG_CACHE_HOME", str(out / "cache"))
    done, dropped = [], []
    for entry in PLAN.get("structures") or []:
        what = str(entry.get("name") or "structure")
        say("preparing %s" % what)
        try:
            done.append(prepare_structure(entry, out))
        except Exception as why:                                    # noqa: BLE001
            # One structure that cannot be prepared is one structure: the nineteen beside it are
            # still worth having, and the record says which was left behind and why.
            say("   left behind: %s" % str(why)[:300])
            dropped.append([what, str(why)[:300]])
    record = {"prepared": len(done), "dropped": dropped,
              "ph": PLAN.get("ph"), "prep": PLAN.get("prep") or {},
              "receptor": PLAN.get("receptor") is not None, "structures": done}
    (out / "prepared.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({"prepared": len(done), "dropped": len(dropped),
                      "record": str(out / "prepared.json")}))
    return 0 if done else 3


if __name__ == "__main__":
    sys.exit(main(sys.argv))
