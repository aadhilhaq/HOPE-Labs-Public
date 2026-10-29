"""The sentences a run's report is made of: the methods paragraph, its references, the caveats
that are true of this run only, and the plots.

What the report says and how it is laid out are elsewhere: hopemd.export.reportdata builds the
document (its sections, tables and captions) and reportkit, the module the lab's tools share, lays
it out. This file holds the writing that reads a run folder and turns it into prose, which is the
part no other tool has.

    python3 -m hopemd.export.report <rundir>      # writes <rundir>/export/report.html, on its own
    GET /report?name=<run>                        # the page, from the Report (PDF) button
    GET /api/report?name=<run>                    # the document the page is drawn from

Everything comes from spec.json, build/prep_report.json, build/system.json and the analysis files,
read and never written: a report only reads a run folder.
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
from pathlib import Path

from .. import __version__, COPYRIGHT, VIEWER_REFERENCES
from ..brand import PALETTE, NAME
from ..spec import Spec, CUTOFF_A, ENGINE_LABELS, FIXED_TIMESTEP, has_nucleic, part_kinds, split_links, mmgbsa_refused
from ..analysis.summarise import summarise
from ..engines.namd import PISTON_PERIOD_FS, PISTON_DECAY_FS

E = html.escape

#: NAMD's license asks for this sentence, in these words, in anything reporting work it did. It
#: is not a citation and does not replace one; both go in.
NAMD_ACKNOWLEDGEMENT = (
    "NAMD was developed by the Theoretical and Computational Biophysics Group in the Beckman "
    "Institute for Advanced Science and Technology at the University of Illinois at "
    "Urbana-Champaign.")

REFERENCES = {
    "amber":    ("Amber 24, pmemd.cuda", "Case, D. A. et al. Amber 2024. University of California, San Francisco (2024). "
                 "Salomon-Ferrer, R., Götz, A. W., Poole, D., Le Grand, S. & Walker, R. C. J. Chem. Theory Comput. 9, 3878–3888 (2013)."),
    "openmm":   ("OpenMM 8", "Eastman, P. et al. J. Phys. Chem. B 128, 109–116 (2024)."),
    "gromacs":  ("GROMACS", "Abraham, M. J. et al. SoftwareX 1–2, 19–25 (2015). Páll, S. et al. J. Chem. Phys. 153, 134110 (2020)."),
    "namd":     ("NAMD 3", "Phillips, J. C. et al. J. Chem. Phys. 153, 044130 (2020)."),
    "desmond":  ("Desmond", "Bowers, K. J. et al. Scalable algorithms for molecular dynamics simulations on commodity clusters. Proc. ACM/IEEE SC06 (2006)."),
    "ff19SB":   ("ff19SB", "Tian, C. et al. J. Chem. Theory Comput. 16, 528–552 (2020)."),
    "OL21":     ("OL21 (DNA)", "Zgarbová, M., Šponer, J. & Otyepka, M. J. Chem. Theory Comput. 17, 6292–6301 (2021)."),
    "OL15":     ("OL15 (DNA)", "Zgarbová, M. et al. J. Chem. Theory Comput. 11, 5723–5736 (2015)."),
    "bsc1":     ("parmbsc1 (DNA)", "Ivani, I. et al. Nat. Methods 13, 55–58 (2016)."),
    "OL3":      ("χOL3 (RNA)", "Zgarbová, M. et al. J. Chem. Theory Comput. 7, 2886–2902 (2011)."),
    "ff14SB":   ("ff14SB", "Maier, J. A. et al. J. Chem. Theory Comput. 11, 3696–3713 (2015)."),
    "opls2005": ("OPLS_2005", "Banks, J. L. et al. J. Comput. Chem. 26, 1752–1780 (2005)."),
    "OPC":      ("OPC water", "Izadi, S., Anandakrishnan, R. & Onufriev, A. V. J. Phys. Chem. Lett. 5, 3863–3871 (2014)."),
    "TIP3P":    ("TIP3P water", "Jorgensen, W. L., Chandrasekhar, J., Madura, J. D., Impey, R. W. & Klein, M. L. J. Chem. Phys. 79, 926–935 (1983)."),
    "TIP4P-Ew": ("TIP4P-Ew water", "Horn, H. W. et al. J. Chem. Phys. 120, 9665–9678 (2004)."),
    "SPC":      ("SPC water", "Berendsen, H. J. C., Postma, J. P. M., van Gunsteren, W. F. & Hermans, J. in Intermolecular Forces (ed. Pullman, B.) 331–342 (Reidel, 1981)."),
    # the sources frcmod.ionslm_126_opc and frcmod.ions234lm_126_* name for each ion (Amber 24)
    "ions":     ("Li/Merz 12-6 monovalent ion parameters for OPC", "Sengupta, A., Li, Z., Song, L. F., Li, P. & Merz, K. M. J. Chem. Inf. Model. 61, 869–880 (2021)."),
    "ions_opc_mg": ("Li/Merz 12-6 divalent ion parameters for OPC", "Li, Z., Song, L. F., Li, P. & Merz, K. M. J. Chem. Theory Comput. 16, 4429–4442 (2020)."),
    "ions_mg":  ("Li/Merz 12-6 divalent ion parameters", "Li, P., Roberts, B. P., Chakravorty, D. K. & Merz, K. M. J. Chem. Theory Comput. 9, 2733–2748 (2013)."),
    "hmr":      ("hydrogen mass repartitioning", "Hopkins, C. W., Le Grand, S., Walker, R. C. & Roitberg, A. E. J. Chem. Theory Comput. 11, 1864–1874 (2015)."),
    "vrescale": ("V-rescale thermostat", "Bussi, G., Donadio, D. & Parrinello, M. J. Chem. Phys. 126, 014101 (2007)."),
    "crescale": ("C-rescale barostat", "Bernetti, M. & Bussi, G. J. Chem. Phys. 153, 114107 (2020)."),
    "langevin_piston": ("Nosé–Hoover Langevin piston", "Feller, S. E., Zhang, Y., Pastor, R. W. & Brooks, B. R. J. Chem. Phys. 103, 4613–4621 (1995)."),
    "mtk":      ("Martyna–Tobias–Klein barostat", "Martyna, G. J., Tobias, D. J. & Klein, M. L. J. Chem. Phys. 101, 4177–4189 (1994)."),
    "pdbfixer": ("PDBFixer (OpenMM)", "Eastman, P. et al. PLoS Comput. Biol. 13, e1005659 (2017)."),
    "pdb2pqr":  ("PDB2PQR and PROPKA", "Jurrus, E. et al. Protein Sci. 27, 112–128 (2018). "
                 "Olsson, M. H. M., Søndergaard, C. R., Rostkowski, M. & Jensen, J. H. J. Chem. Theory Comput. 7, 525–537 (2011)."),
    "parmed":   ("ParmEd", "ParmEd, https://github.com/ParmEd/ParmEd."),
    "cpptraj":  ("cpptraj", "Roe, D. R. & Cheatham, T. E. J. Chem. Theory Comput. 9, 3084–3095 (2013)."),
    "mmpbsa":   ("MMPBSA.py", "Miller, B. R. et al. J. Chem. Theory Comput. 8, 3314–3321 (2012)."),
    "prime_mmgbsa": ("Prime MM-GBSA and VSGB solvation (Prime 2025-3 runs VSGB 2.1)", "Jacobson, M. P. et al. Proteins 55, 351–367 (2004). Li, J. et al. Proteins 79, 2794–2812 (2011)."),
    "lipid17":  ("Lipid17 (the 2017 extension of Lipid14 by the same group, unpublished; cite Lipid14)",
                 "Dickson, C. J. et al. J. Chem. Theory Comput. 10, 865–879 (2014)."),
    # cited for OpenMM's amber14/tip3p.xml (the bilayer build) and for tleap's frcmod.ionsjc_* (a TIP3P or TIP4P-Ew box)
    "ions_jc":  ("Joung–Cheatham monovalent ion parameters", "Joung, I. S. & Cheatham, T. E. J. Phys. Chem. B 112, 9020–9041 (2008)."),
    "gaff2":    ("GAFF2 with AM1-BCC charges", "Wang, J., Wolf, R. M., Caldwell, J. W., Kollman, P. A. & Case, D. A. J. Comput. Chem. 25, 1157–1174 (2004). "
                 "Jakalian, A., Jack, D. B. & Bayly, C. I. J. Comput. Chem. 23, 1623–1641 (2002)."),
}

#: The lines of a plot, in the brand's colors, one per replicate. Presentation attributes, not a
#: stylesheet: the SVG goes into the report page and into the PDF, where it stays vector, and must
#: carry its own colors with it.
COLOURS = [PALETTE["teal"][0], PALETTE["rust"][0], PALETTE["green"][0], PALETTE["amber"][0], PALETTE["dim"][0]]
INK, LINE, DIM = PALETTE["ink"][0], PALETTE["line"][0], PALETTE["dim"][0]
FONT = "IBM Plex Sans, Helvetica, Arial, sans-serif"

#: The sheet is A4 with 15 mm margins (reportkit.css: #rk-doc), so a figure is 180 mm wide
#: whatever the window is: 680 CSS pixels. A chart is therefore drawn in 680 user units, not in
#: some other number that is then stretched to fit, and one user unit is one pixel on the screen
#: and one 96th of an inch on paper. That is the whole of the fix: a label's size in units is the
#: size it is drawn at, so the floor the lab keeps - nothing under 13 px - can be kept here by
#: writing 13.
#:
#: It was not so before. Every chart was drawn 640 units wide and stretched to its box, so the
#: 11-unit labels landed at 11.7 px across the page and at 5.8 px in a half-width panel, which is
#: the smallest type in the product on the page a tutorial film shows after a run. Raising 11 to
#: 24.8 would have reached 13 px in a panel and collided with everything around it; drawing the
#: chart at the width it will occupy costs nothing and collides with nothing.
FIG = 680

#: .rk-panels{gap:var(--sp-2)} - the gutter between two panels of one figure.
GAP = 8

#: Nothing the lab draws is under 13 px. A chart's furniture is set at the floor, which is the
#: smallest the lab allows and leaves the most room for the data.
LABEL = 13


def panel_width(columns: int) -> int:
    """How wide one panel of a figure of this many columns is drawn, in pixels."""
    columns = max(1, min(int(columns or 1), 4))
    return int((FIG - GAP * (columns - 1)) // columns)


def _wide(says, size: float = LABEL) -> float:
    """About how wide a label is drawn, in pixels. Digits, a point and a minus sign average a
    little under two thirds of the size in the report's sans-serif, and a margin worked out from
    that is never the reason a label is clipped."""
    return 0.62 * size * len(str(says))


def _fmt(v, nd=1):
    return "\u2014" if v is None else (f"{v:,.{nd}f}".replace(",", " ") if isinstance(v, float) else f"{v:,}".replace(",", " "))


def plot(series: list[dict], xl: str, yl: str, box: int = FIG) -> str:
    """One SVG line plot, drawn at the width it will be given: series = [{x, y, col, name?,
    fill?}], `box` the width of the figure or of the panel it goes in, in pixels.

    The user space is that many units wide, so one unit is one pixel and the labels are drawn at
    the size they are written at. Everything around the lines - the gutter for the y labels, how
    many ticks there is room for, where the legend wraps - is worked out from that size and from
    the width, so a chart in a half-width panel is a chart, not a chart photographed small.

    Every color, size and font is an attribute of the elements, so the figure looks the same on
    the page, in the PDF and in a file saved on its own."""
    if not series:
        return ""
    xs = [v for s in series for v in s["x"]]
    ys = [v for s in series for v in s["y"]]
    if not xs or not ys:
        return ""
    W, fs = max(160, int(box)), LABEL
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(0.0, min(ys)), max(1e-9, max(ys))
    tick = lambda v: f"{v:,.2f}".rstrip("0").rstrip(".").replace(",", "\u202f") if abs(v) < 100 else f"{round(v):,}".replace(",", "\u202f")   # noqa: E731

    # The plot stands on the type: a band under it for the x labels and another for the x title,
    # a gutter at the left wide enough for the widest y label beside the rotated y title.
    bottom = 2 * fs + 18                                    # the x labels, the x title a clear line under them, the descenders
    gut = max(_wide(tick(ymin)), _wide(tick(ymax)), _wide(tick((ymin + ymax) / 2.0)))
    x0 = int(fs + 6 + gut + 6)
    right = max(10, int(_wide(tick(xmax)) / 2) + 4)         # the last x label is centered on it
    x1 = W - right
    # The legend is a row of its own above the plot, and wraps when the names do not fit across.
    named = [s for s in series if s.get("name")]
    slots = [(s, 20 + 6 + _wide(s["name"]) + 14) for s in named] if len(named) > 1 else []
    rows, line, used = [], [], 0.0
    for one, wide in slots:
        if line and used + wide > (x1 - x0):
            rows.append(line)
            line, used = [], 0.0
        line.append((one, wide))
        used += wide
    if line:
        rows.append(line)
    top = 10 + len(rows) * (fs + 5)
    deep = max(104, min(210, int(W * 0.30)))
    H = top + deep + bottom
    y1, y0 = top, top + deep
    X = lambda x: x0 + (x - xmin) / ((xmax - xmin) or 1) * (x1 - x0)          # noqa: E731
    Y = lambda y: y0 - (y - ymin) / ((ymax - ymin) or 1) * (y0 - y1)          # noqa: E731
    # As many ticks as there is room for at this size, never so many that two labels touch.
    ny = 5 if deep >= 150 else 4
    nx = 5 if (x1 - x0) >= 420 else 4 if (x1 - x0) >= 240 else 3

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
           f'style="max-width:{W}px;margin-inline:auto" font-family="{FONT}" font-size="{fs}" '
           f'role="img" aria-label="{E(yl)} against {E(xl)}">',
           f'<rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>']
    for i in range(ny):                                     # the horizontal rules, and the y labels on them
        v = ymin + (ymax - ymin) * i / (ny - 1.0)
        y = Y(v)
        out.append(f'<line x1="{x0}" x2="{x1}" y1="{y:.1f}" y2="{y:.1f}" stroke="{LINE}" stroke-width="1"/>')
        out.append(f'<text x="{x0 - 6}" y="{y + fs * 0.35:.1f}" text-anchor="end" fill="{DIM}">{tick(v)}</text>')
    out.append(f'<line x1="{x0}" x2="{x0}" y1="{y1}" y2="{y0}" stroke="{INK}" stroke-width="1"/>')
    for i in range(nx):                                     # the x labels, under the axis; the first
        v = xmin + (xmax - xmin) * i / (nx - 1.0)           # and the last pulled in so neither is cut off
        anchor = "start" if i == 0 else "end" if i == nx - 1 else "middle"
        out.append(f'<text x="{X(v):.1f}" y="{y0 + fs + 4}" text-anchor="{anchor}" fill="{DIM}">{tick(v)}</text>')
    out.append(f'<text x="{(x0 + x1) / 2:.0f}" y="{y0 + 2 * fs + 11}" text-anchor="middle" fill="{INK}">{E(xl)}</text>')
    mid = (y0 + y1) / 2
    out.append(f'<text x="{fs}" y="{mid:.0f}" text-anchor="middle" fill="{INK}" '
               f'transform="rotate(-90 {fs} {mid:.0f})">{E(yl)}</text>')
    for s in series:
        pts = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in zip(s["x"], s["y"]))
        if s.get("fill") and s["x"]:
            out.append(f'<polygon points="{pts} {X(s["x"][-1]):.1f},{Y(ymin):.1f} {x0},{Y(ymin):.1f}" '
                       f'fill="{s["col"]}" opacity="0.10"/>')
        out.append(f'<polyline points="{pts}" fill="none" stroke="{s["col"]}" stroke-width="1.4" '
                   'stroke-linejoin="round"/>')
    for r, line in enumerate(rows):
        x, y = x0 + 2, 10 + r * (fs + 5) + fs * 0.75
        for one, wide in line:
            out.append(f'<line x1="{x:.0f}" x2="{x + 20:.0f}" y1="{y - fs * 0.3:.0f}" y2="{y - fs * 0.3:.0f}" '
                       f'stroke="{one["col"]}" stroke-width="2.4"/>')
            out.append(f'<text x="{x + 26:.0f}" y="{y:.0f}" fill="{INK}">{E(one["name"])}</text>')
            x += wide
    out.append("</svg>")
    return "".join(out)


def _series(reps: dict, key: str) -> list[dict]:
    return [{"x": r[key]["ns"], "y": r[key]["values"], "col": COLOURS[i % len(COLOURS)]}
            for i, (k, r) in enumerate(sorted(reps.items(), key=lambda kv: int(kv[0]))) if key in r]


def _load(rundir: Path) -> tuple[Spec, dict, dict | None, dict, dict]:
    spec = Spec.from_json((rundir / "spec.json").read_text())
    prep = _json(rundir / "build" / "prep_report.json") or {}
    system = _json(rundir / "build" / "system.json")
    try:
        # write=False: a report reads a run folder. A stored summary is the analysis job's and the
        # MM-GBSA job's to rewrite, not the reader's; one rewritten here would also lose the
        # MM-GBSA the job added after it, which is how a stale summary came to be read as fresh.
        summary = summarise(rundir, write=False)
    except Exception:
        summary = _json(rundir / "analysis" / "summary.json") or {"replicates": {}}
    ids = _json(rundir / "jobs" / "ids.json") or {}
    return spec, prep, system, summary, ids


def _json(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def alphafold_model(spec: Spec) -> str:
    """An AlphaFold model as its result names it (AlphaFold 3's "the top-ranked model" or "seed-1_sample-0", or a
    numbered model_0), else model_<n> from the page's model field, whose 1 is the top-ranked."""
    return (spec.system.meta or {}).get("model") or f"model_{max(0, int(spec.system.source.pose or 1) - 1)}"


def source_sentence(spec: Spec) -> str:
    src, s = spec.system.source, spec.system
    kind = src.kind
    if kind == "rcsb":
        meta = s.meta or {}
        what = f"PDB entry {src.pdb_id.upper()}" + (f", assembly {src.assembly}" if src.assembly and src.assembly != "asu" else ", asymmetric unit")
        if meta.get("title"):
            what += f" ({meta['title']}" + (f", {meta['resolution_A']} Å" if meta.get("resolution_A") else "") + ")"
    elif kind == "alphafold":
        # a single chain has no ipTM: the server leaves it empty, and so does this
        job = re.sub(r"(_model_\d+\.cif|_model\.cif|\.zip)$", "", (s.meta or {}).get("model_file") or Path(src.path).name)
        scores = [f"{label} {s.confidence[k]}" for label, k in (("ipTM", "iptm"), ("pTM", "ptm")) if s.confidence.get(k) is not None]
        what = f"an AlphaFold model ({alphafold_model(spec)} of {job}" + "".join(f", {x}" for x in scores) + ")"
    elif kind == "poseviewer":
        what = f"pose {src.pose} of the Maestro poseviewer file {Path(src.path).name}"
    elif kind == "complex":
        what = f"the complex file {Path(src.path).name}"
    elif kind == "files":
        partner = "ligand" if s.ligand_class == "small_molecule" else "partner"
        what = f"the receptor {Path(src.path).name}" + (f" with the {partner} {Path(src.ligand_path).name}" if src.ligand_path else " alone")
    else:
        what = f"a {kind} source"
    if s.receptor_chains or s.partner_chains:
        # no partner (a protein or a nucleic acid alone): its chains, not a receptor's
        what += f"; {'receptor ' if s.partner_chains else ''}chain{'s' if len(s.receptor_chains) != 1 else ''} {', '.join(s.receptor_chains)}"
        if s.partner_chains:
            what += f", partner chain{'s' if len(s.partner_chains) != 1 else ''} {', '.join(s.partner_chains)}"
    return what


def subject(spec: Spec) -> str:
    """What the methods paragraph calls the solute: a complex when there is a partner or ligand, else what
    the chains are made of."""
    kinds = part_kinds(spec.system)
    if spec.system.ligand_class != "none":
        return "The complex"
    return {frozenset({"nucleic"}): "The nucleic acid", frozenset({"protein"}): "The protein"}.get(frozenset(kinds), "The system")


def trace_words(kinds: set[str]) -> str:
    """The atoms the analysis traces these chains by (engines/amber.py, spec.trace_mask), in words."""
    if kinds == {"nucleic"}:
        return "P and C4′"
    return "Cα, P and C4′" if "nucleic" in kinds else "Cα"


def breaks_sentence(breaks: list[str], capped: list[str] | None = None) -> str:
    """The methods' sentence on the chain breaks: those Prepare capped (capped), then those whose pieces kept their own
    termini."""
    if not breaks:
        return ""
    caps = [b for b in breaks if b in set(capped or [])]
    rest = [b for b in breaks if b not in set(caps)]
    out = ""
    if caps:
        what = (("its break" if len(caps) == 1 else f"each of its {len(caps)} breaks") if not rest
                else ("one break" if len(caps) == 1 else f"{len(caps)} breaks"))
        out += (f" The chain was capped at {what} ({', '.join(caps[:6])}{', …' if len(caps) > 6 else ''}) with an "
                "N-methylamide (NME) before the gap and an acetyl (ACE) after it, so that no gap ended in charged termini.")
    if rest:
        what = (("its break" if len(rest) == 1 else f"each of its {len(rest)} breaks") if not caps
                else ("its other break" if len(rest) == 1 else f"its other {len(rest)} breaks"))
        out += (f" The chain was ended with TER at {what} ({', '.join(rest[:6])}{', …' if len(rest) > 6 else ''}), "
                "so each piece kept its own termini.")
    return out


def prep_sentences(prep: dict, spec: Spec) -> str:
    if not prep:
        return "The structure was used as imported." + breaks_sentence(spec.system.breaks, spec.system.capped)
    st = prep.get("steps", {})
    parts = []
    rm = (st.get("clean") or {}).get("removed") or {}
    if rm:
        names = {"water": "waters", "buffer": "buffer and cryoprotectant molecules", "ion": "ions", "ligand": "other ligands",
                 "glycan": "glycans", "altloc": "alternate locations", "other_chain": "atoms of chains not simulated", "cofactor": "cofactors", "metal": "metals"}
        one = {"water": "water", "buffer": "buffer or cryoprotectant molecule", "ion": "ion", "ligand": "other ligand", "glycan": "glycan",
               "altloc": "alternate location", "other_chain": "atom of a chain not simulated", "cofactor": "cofactor", "metal": "metal"}
        parts.append("removed " + ", ".join(f"{v} {(one if v == 1 else names).get(k, k)}" for k, v in rm.items()))
    tr = st.get("trim") or {}
    if tr.get("plddt") and tr.get("residues_per_chain"):
        parts.append(f"trimmed {sum(tr['residues_per_chain'].values())} terminal residues with pLDDT under {float(tr.get('threshold', 50)):g}")
    bound = (st.get("clean") or {}).get("bound_ions") or []
    if bound:
        parts.append(f"kept the {len(bound)} ion{'s' if len(bound) != 1 else ''} bound to the nucleic acid ({', '.join(bound)})")
    mapped = (st.get("clean") or {}).get("mapped") or []
    if mapped:
        parts.append(f"mapped {len(mapped)} modified residue{'s' if len(mapped) != 1 else ''} to {'its' if len(mapped) == 1 else 'their'} "
                     f"parent, keeping the parent's atoms where they were ({', '.join(mapped[:6])}{', …' if len(mapped) > 6 else ''})")
    mo = st.get("model") or {}
    if mo.get("available"):
        loops = mo.get("loops_modelled") or {}
        rx = mo.get("relaxed") or {}
        parts.append(f"modeled {len(loops)} missing loop{'s' if len(loops) != 1 else ''} and {mo.get('missing_atoms_added', 0)} missing atoms with PDBFixer"
                     + (f" (seed {rx['seed']}), relaxing the built residues with their {rx.get('stems', 0)} flanking residues on a "
                        f"{float(rx.get('spring_kcal', 10)):g} kcal/mol/Å² spring and checking every peptide junction"
                        if rx.get("seed") is not None else "")
                     + (f" (gaps of {', '.join(str(v) for v in (mo.get('gaps_left') or {}).values())} residues left open)" if mo.get("gaps_left") else ""))
    pr = st.get("protonate") or {}
    if pr.get("method") == "propka" and pr.get("states") is not None:
        parts.append(f"assigned protonation states with PROPKA at pH {pr.get('ph', spec.system.ph):g}"
                     + (" (" + ", ".join(f"{k} {v}" for k, v in pr["states"].items()) + ")" if pr["states"] else ""))
    if pr.get("carried"):
        parts.append("modeled the ions kept (" + ", ".join(c["group"] for c in pr["carried"]) + ") as free ions in place, "
                     "with no bonds to their sites")
    renamed = ((pr.get("metal_sites") or {}).get("renamed")) or []
    if renamed:
        # which tautomer a metal's histidine is in decides whether the ion stays in the site at all, and the
        # protonation step does not know the ion is there, so the methods have to say where the names came from
        parts.append("named the residues that hold them from the coordination geometry rather than the protonation "
                     "step's own choice (" + ", ".join(f"{e['was']} {e['chain']}:{e['resid']} as {e['now']}" for e in renamed) + ")")
    if prep.get("phosphates_5p"):
        parts.append(f"removed the 5′-terminal phosphate of {len(prep['phosphates_5p'])} strand{'s' if len(prep['phosphates_5p']) != 1 else ''}, "
                     "which the nucleic-acid force fields have no template for")
    if prep.get("breaks"):
        caps = {c["break"] for c in prep.get("caps") or []}
        capped = [b for b in prep["breaks"] if b in caps]
        rest = [b for b in prep["breaks"] if b not in caps]
        if capped:
            parts.append(f"capped {len(capped)} chain break{'s' if len(capped) != 1 else ''} ({', '.join(capped[:6])}"
                         f"{', …' if len(capped) > 6 else ''}) with an N-methylamide (NME) on the residue before the gap and an "
                         "acetyl (ACE) on the residue after it, so that no gap ended in charged termini")
        if rest:
            parts.append(f"ended the chain with TER at {len(rest)} break{'s' if len(rest) != 1 else ''} "
                         f"({', '.join(rest[:6])}{', …' if len(rest) > 6 else ''}), each piece with its own termini")
    if prep.get("stretched"):
        one = len(prep["stretched"]) == 1
        parts.append(f"kept {'a bond' if one else str(len(prep['stretched'])) + ' bonds'} the deposited model stretched, with no residue "
                     f"missing, for the minimization to close ({', '.join(prep['stretched'][:6])}{', …' if len(prep['stretched']) > 6 else ''})")
    mi = st.get("minimise") or {}
    if mi.get("done"):
        parts.append(f"relieved clashes by {mi.get('steps')} steps of restrained minimization in vacuum (input heavy atoms moved {mi.get('rmsd_A', 0):.2f} Å RMSD)")
    return ("The structure was prepared with HOPE-MD: " + "; ".join(parts) + ".") if parts else "The structure was used as imported."


BILAYER_A = 40.0          # the thickness the derived panel and the viewer assume for a POPC-class bilayer


def _mfield(mem, key: str, default):
    """spec.system.membrane as a dataclass or a plain dict (a spec.json read by an older Spec)."""
    if mem is None:
        return default
    v = mem.get(key) if isinstance(mem, dict) else getattr(mem, key, None)
    return default if v is None else v


def membrane_of(spec: Spec, system: dict | None) -> dict | None:
    """The bilayer the run was built with, or None for a water box. The spec says what was
    asked (lipid, padding); build/system.json says what the build made (lipids, box). The
    orientation is what the import did, not what was asked: the server writes
    system.meta["orientation"] = "opm" only when the OPM coordinates were used, and keeps the
    spec's request as it was when it fell back to the RCSB file."""
    sysd = system or {}
    env = getattr(spec.system, "environment", "water")
    if env != "membrane" and not sysd.get("membrane"):
        return None
    mem = getattr(spec.system, "membrane", None)
    meta = getattr(spec.system, "meta", None) or {}
    return {"lipid": sysd.get("lipid") or _mfield(mem, "lipid", "POPC"),
            "padding_A": float(_mfield(mem, "padding_A", 10.0)),
            "center_z_A": float(_mfield(mem, "center_z_A", 0.0)),
            "orientation": meta.get("orientation") if meta.get("orientation") in ("opm", "annotation") else "as_imported",
            "opm_half_thickness_A": meta.get("opm_half_thickness_A"),
            "lipids": sysd.get("lipids"), "box": sysd.get("box")}


#: the REFERENCES key of a force field whose name is not its key
FF_KEYS = {"OPLS_2005": "opls2005"}


def _tleap_ions(water: str, salt: str, cite) -> str:
    """The ions of a tleap build are the ones its water model's leaprc loads (Amber 24,
    dat/leap/cmd). leaprc.water.opc loads Li/Merz's 12-6 set for OPC for every ion
    (frcmod.ionslm_126_opc). leaprc.water.tip3p and .tip4pew load Joung and Cheatham's monovalent
    set (frcmod.ionsjc_*; TIP3P's after Li/Merz's, and the set loaded last is the one tleap uses: a
    Na+ built under leaprc.water.tip3p carries Joung and Cheatham's LJ) and Li/Merz's 12-6 set for
    Mg2+ (frcmod.ions234lm_126_*)."""
    if water == "OPC":
        if salt == "MgCl2":
            return f"Li/Merz ions for OPC, Mg²⁺ {cite('ions_opc_mg')} and Cl⁻ {cite('ions')}"
        return f"Li/Merz ions {cite('ions')}"
    if salt == "MgCl2":
        return f"Li/Merz Mg²⁺ {cite('ions_mg')} and Joung–Cheatham Cl⁻ {cite('ions_jc')}"
    return f"Joung–Cheatham ions {cite('ions_jc')}"


def _carried_sentence(spec: Spec, prep: dict, cite) -> str:
    """The parameters of the ions Prepare carried (or an import of files kept) into an Amber-topology build: the
    water model's own sets (as _tleap_ions; the bilayer build's amber14/tip3p.xml holds TIP3P's), Joung and
    Cheatham's or Li/Merz's for a monovalent ion, Li/Merz's divalent set for a divalent one. Desmond's OPLS types
    them with the rest of the system, which its build sentence says."""
    carried = (((prep or {}).get("steps") or {}).get("protonate") or {}).get("carried") or []
    who = "Prepare"
    if not carried and not prep:
        carried, who = spec.system.ions_kept or [], "the import"
    if not carried or spec.engine == "desmond":
        return ""
    water = spec.forcefield.water
    charges = {abs(int(c.get("charge", 0))) for c in carried}
    sets = []
    if 1 in charges:
        sets.append(f"Li/Merz's monovalent set {cite('ions')}" if water == "OPC" else f"Joung and Cheatham's monovalent set {cite('ions_jc')}")
    if 2 in charges:
        sets.append(f"Li/Merz's divalent set {cite('ions_opc_mg' if water == 'OPC' else 'ions_mg')}")
    if charges - {1, 2}:
        sets.append("Li/Merz's set for the higher charges")
    return (f"The ions {who} kept in place ({', '.join(c['group'] for c in carried)}) took the 12-6 parameters the {water} "
            f"water model loads: {' and '.join(sets)}.")


def _waters_sentence(spec: Spec) -> str:
    """The crystal waters the run kept (input/waters.pdb): how many, which, and as what they were built."""
    from ..engines.base import crystal_waters
    from ..spec import water_choice
    from ..structure import NEAR_WATER_A
    waters = crystal_waters(spec)
    if not waters:
        return ""
    which = f"those within {NEAR_WATER_A:g} Å of the partner" if water_choice(spec.system) == "near" else "every one"
    return (f"{len(waters)} crystal water{'s' if len(waters) != 1 else ''} of the structure ({which}) {'were' if len(waters) != 1 else 'was'} "
            f"kept in place and built as {spec.forcefield.water} water after the solute; the analysis treated "
            f"{'them' if len(waters) != 1 else 'it'} as solvent.")


def _build_sentence(spec: Spec, mem: dict | None, system: dict | None, cite, ref) -> str:
    """How the system was built: Desmond's System Builder (hopemd/engines/desmond.py), OpenMM's
    Modeller for a bilayer on the other four engines (hopemd/membrane.py), else tleap
    (hopemd/engines/amber.py); GROMACS and NAMD then run the Amber topology those two write."""
    ff, box, eng = spec.forcefield, spec.box, spec.engine
    sysd = system or {}
    counts, orient = "", ""
    if sysd.get("atoms"):
        counts = (f" ({_fmt(mem['lipids'])} lipids, " if mem else " (") + (
            f"{_fmt(sysd.get('waters'))} waters, "
            # build.sh's ion_pairs counts formula units: for MgCl2 that is one Mg2+ and two Cl-, not a pair
            + (f"{_fmt(sysd.get('ion_pairs'))} MgCl₂ units" if box.salt == "MgCl2" else f"{_fmt(sysd.get('ion_pairs'))} ion pairs")
            + f", {_fmt(sysd.get('atoms'))} atoms in all)")
    if mem:
        orient = (" in the orientation of the OPM database" if mem["orientation"] == "opm" else
                  " oriented on its annotated transmembrane segments" if mem["orientation"] == "annotation" else "")
    nucleic, kinds = has_nucleic(spec.system), part_kinds(spec.system)
    if eng == "desmond":
        # _build_msj: build_geometry with the spec's water and salt, neutralized, in an orthorhombic box with box.padding_A
        # of buffer whatever box.shape says; a bilayer through membrane_box, whose buffers around the solute in x, y and z
        # replace the box's (the install's stage/prepare/structure.py applies it after the box); then assign_forcefield
        # over the whole system, a ligand included
        if mem:
            return (f"The protein was embedded in a {mem['lipid']} bilayer with Schrödinger's System Builder {cite('desmond')}, the membrane "
                    f"normal along z{orient}, with a {mem['padding_A']:g} Å buffer around the solute in x, y and z, solvated with {ff.water} "
                    f"water{ref(ff.water)}, neutralized and brought to {box.salt_mM:g} mM {box.salt}, and parameterized with the {ff.protein} "
                    f"force field{ref(ff.protein)}{', which types the nucleic acid as well' if nucleic else ''}{counts}.")
        ligand = (", the ligand typed with the same force field from its bond orders and formal charges"
                  if spec.system.ligand_class == "small_molecule" else "")
        # assign_forcefield types every residue with the run's OPLS: the nucleic acid takes no force field of its own
        typed = ", which types the nucleic acid as well" if nucleic else ""
        return (f"The system was built with Schrödinger's System Builder {cite('desmond')} and parameterized with the {ff.protein} force "
                f"field{ref(ff.protein)}{typed}{ligand}: an orthorhombic box of {ff.water} water{ref(ff.water)} with a {box.padding_A:g} Å buffer between "
                f"solute and box edge, neutralized and brought to {box.salt_mM:g} mM {box.salt}{counts}.")
    def with_nucleic() -> str:
        """", the nucleic acid with OL21 [n] and OL3 [m]": cited where the text names them, after the protein."""
        if not (nucleic and ff.nucleic):
            return ""
        dna, _, rna = ff.nucleic.partition("+")
        return f", the nucleic acid with {dna}{ref(dna)}" + (f" and {rna}{ref(rna)}" if rna else "")
    if mem:
        text = (f"The protein was embedded in a {mem['lipid']} bilayer built with OpenMM's Modeller {cite('openmm')}: the protein with {ff.protein}"
                f"{ref(ff.protein)}{with_nucleic()} and the lipids with Lipid17 {cite('lipid17')}, the membrane normal along z{orient}, with {mem['padding_A']:g} Å "
                f"of {ff.water} water{ref(ff.water)} above and below the bilayer, neutralized and brought to {box.salt_mM:g} mM {box.salt} "
                f"with the Joung–Cheatham ions {cite('ions_jc')}{counts}")
    else:
        shape = {"compact": "truncated-octahedral", "cubic": "cubic", "rectangular": "rectangular"}[box.shape]
        # what the chains are: a nucleic acid alone is not "the protein with ff19SB"; cited in the order the text names them
        text = f"Topology and parameters were built with tleap {cite('amber')}: "
        protein = f"the protein with {ff.protein}{ref(ff.protein)}" if "protein" in kinds or not (nucleic and ff.nucleic) else ""
        text += ((protein + with_nucleic() if protein else with_nucleic()[2:])
                + (f", the ligand with {ff.ligand} {cite('gaff2')}" if spec.system.ligand_class == "small_molecule" and ff.ligand and ff.ligand.startswith("gaff2") else "")
                + f", in a {shape} box of {ff.water} water{ref(ff.water)} with at least {box.padding_A:g} Å between solute and box edge, "
                f"neutralized and brought to {box.salt_mM:g} mM {box.salt} with {_tleap_ions(ff.water, box.salt, cite)}{counts}")
    if eng == "gromacs":        # build/to_gromacs.py: the prmtop (the HMR one at 4 fs) through ParmEd
        text += f"; the topology was converted to GROMACS format with ParmEd {cite('parmed')}"
    elif eng == "namd":         # amber yes, parmfile: NAMD reads the prmtop itself
        text += "; NAMD read the Amber topology directly"
    return text + "."


def _relaxation_sentence(spec: Spec, mem: dict | None, cite) -> str:
    """Minimization (or what stands in for it), heating and the restraint ladder, as each renderer
    writes them. Restraint constants are the renderers' own numbers, in kcal/mol/A^2."""
    pro, eng = spec.protocol, spec.engine
    T, k = pro.temperature_K, pro.restraint_kcal
    if eng == "desmond" and mem:
        # _relax_msj: Schrodinger's membrane protocol as shipped, with the run's temperature
        # (mmshare-v6.8/data/desmond/desmond_membrane_relax.msj.template, Desmond 2024-4: 520 ps in seven stages);
        # minimise_steps, heat_ps, equil_ps and restraint_kcal are not used
        return (f"The system was relaxed with Schrödinger's membrane relaxation protocol for Desmond {cite('desmond')} (desmond_membrane_relax.msj, "
                "about 0.5 ns: Brownian dynamics at 10 K, restrained NPT and NPγT stages with the membrane held along z and barrier potentials "
                f"keeping water out of the bilayer, heating with the restraints gradually released, and unrestrained NVT and NPγT stages at {T:g} K).")
    ladder = (f"heated from {10 if eng == 'desmond' else 0} to {T:g} K over {pro.heat_ps:g} ps at constant volume and equilibrated for "
              f"{pro.equil_ps:g} ps at {pro.pressure_bar:g} bar in four stages with the restraints released from {k:g} to 1, 0.1 and 0 kcal mol⁻¹ Å⁻²")
    if eng == "desmond":
        # _relax_msj: GPU Desmond has no minimizer, so 100 ps of Brownian dynamics at 10 K stand in for one; heating and the
        # four NPT stages run Desmond's Langevin thermostat and barostat (production runs MTK)
        return (f"After 100 ps of Brownian dynamics at 10 K with the solute heavy atoms restrained ({k:g} kcal mol⁻¹ Å⁻²), "
                f"the system was {ladder} (Langevin thermostat and barostat).")
    if eng == "gromacs":        # md/01_min.mdp: integrator steep, emtol 100, the solute held at restraint_kcal
        first = (f"After steepest-descent minimization (at most {pro.minimise_steps} steps, to a maximum force of 100 kJ mol⁻¹ nm⁻¹) "
                 f"with the solute heavy atoms restrained ({k:g} kcal mol⁻¹ Å⁻²)")
    elif eng == "namd":         # md/01_min.namd: minimize N, the solute held at restraint_kcal
        first = f"After {pro.minimise_steps} steps of minimization with the solute heavy atoms restrained ({k:g} kcal mol⁻¹ Å⁻²)"
    else:                       # Amber's 01_min.in (restraint_wt=10.0) and OpenMM's minimizeEnergy, both at 10
        first = f"After {pro.minimise_steps} steps of minimization with the solute heavy atoms restrained (10 kcal mol⁻¹ Å⁻²)"
    return f"{first}, the system was {ladder}."


def _production_sentence(spec: Spec, mem: dict | None, cite) -> str:
    """The production stage as each renderer writes it: thermostat, barostat, time step,
    constraints, electrostatics. Citations are made in the order the text reads."""
    pro, eng, site = spec.protocol, spec.engine, spec.site
    hmr = lambda: f" with hydrogen mass repartitioning {cite('hmr')}" if spec.hmr else ""    # noqa: E731  (called in place: numbering follows the text)
    text = (f"Production ran for {pro.length_ns:g} ns" + (f" in {pro.replicates} independent replicates" if pro.replicates > 1 else "")
            + f" at {pro.temperature_K:g} K and {pro.pressure_bar:g} bar")
    if eng == "gromacs":
        # md/04_prod.mdp: integrator md (leap-frog), V-rescale tau_t 0.1, C-rescale tau_p 2.0 (semiisotropic for a bilayer, equal
        # reference pressures: zero surface tension), LINCS on h-bonds, PME with rcoulomb = CUTOFF_A / 10 nm
        text += (f" (V-rescale thermostat {cite('vrescale')}, time constant 0.1 ps; {'semi-isotropic ' if mem else ''}C-rescale barostat "
                 f"{cite('crescale')}, time constant 2 ps" + (", x and y coupled, z free, zero surface tension" if mem else "")
                 + f") with the leap-frog integrator and a {pro.timestep_fs:g} fs time step{hmr()}, bonds to hydrogen constrained with LINCS, "
                 f"particle-mesh Ewald electrostatics with a {CUTOFF_A / 10:g} nm real-space cutoff")
    elif eng == "namd":
        # md/04_prod.namd: langevinDamping 1, Langevin piston (useFlexibleCell with useConstantRatio for a bilayer, no surface
        # tension target: zero), rigidBonds all, PME, cutoff CUTOFF_A; fullElectFrequency 2 at 2 fs, 1 at 4 fs
        mts = f" (long-range electrostatics every {2 * pro.timestep_fs:g} fs)" if pro.timestep_fs <= 2.5 else ""
        text += (f" (Langevin dynamics, damping coefficient 1 ps⁻¹; Nosé–Hoover Langevin piston {cite('langevin_piston')}, period "
                 f"{PISTON_PERIOD_FS} fs, decay {PISTON_DECAY_FS} fs" + (", flexible cell, x and y in constant ratio, z free, zero surface tension" if mem else "")
                 + f") with a {pro.timestep_fs:g} fs time step{mts}{hmr()}, bonds to hydrogen constrained, "
                 f"particle-mesh Ewald electrostatics with a {CUTOFF_A:g} Å real-space cutoff")
    elif eng == "desmond":
        # _prod_msj: NPT (NPgT for a bilayer, Desmond's default surface tension of 0) with the MTK method, which Desmond runs
        # as a Nose-Hoover chain thermostat and the Martyna-Tobias-Klein barostat (thermostat.tau 1.0, barostat.tau 2.0);
        # timestep [0.002 0.002 0.006] (RESPA), no HMR; coulomb_method useries, cutoff_radius CUTOFF_A; bonds to H constrained
        text += (f" in the {'NPγT ensemble at zero surface tension' if mem else 'NPT ensemble'} (Nosé–Hoover chain thermostat, time constant 1 ps; "
                 f"Martyna–Tobias–Klein barostat {cite('mtk')}, time constant 2 ps) with a {FIXED_TIMESTEP['desmond']:g} fs time step (RESPA, "
                 f"the far-range electrostatics every 6 fs), bonds to hydrogen constrained, u-series electrostatics with a {CUTOFF_A:g} Å cutoff")
    else:
        # Amber's 04_prod.in (ntt=3, gamma_ln=1; barostat=2, ntp=3 and csurften=3 for a bilayer; ntc=2) and OpenMM's runner
        # (LangevinMiddleIntegrator at 1/ps; MonteCarloBarostat, MonteCarloMembraneBarostat for a bilayer; HBonds)
        barostat = ("semi-isotropic Monte Carlo barostat, x and y coupled, z free, zero surface tension" if mem else "Monte Carlo barostat")
        text += (f" (Langevin thermostat, collision frequency 1 ps⁻¹; {barostat}) with a {pro.timestep_fs:g} fs time step{hmr()}, "
                 f"bonds to hydrogen constrained, particle-mesh Ewald electrostatics with a {CUTOFF_A:g} Å real-space cutoff")
    label = "Amber (pmemd.MPI)" if eng == "amber" and spec.on_cpu else ENGINE_LABELS.get(eng, eng)    # the CPU check runs pmemd.MPI
    return (text + f", and coordinates saved every {pro.save_ps:g} ps, using {label}{(' ' + cite(eng)) if eng in REFERENCES else ''} on {site.name}"
            + (f" ({site.cpu_cores_per_node} CPU cores)" if spec.on_cpu else f" (one {spec.gpu.upper()} GPU per replicate)") + ".")


def build_protonation(rundir: Path) -> tuple[str, float | None] | None:
    """What an Amber-topology build did about the receptor's protonation when no Prepare stage ran (receptor +
    partner files, or a poseviewer pose), read from its Slurm log: ("propka", pH) when pdb2pqr ran,
    ("default", None) when it could not and tleap's defaults stood, None when the log says neither (Prepare
    set the states, the run is on Desmond, or there is no build log)."""
    logs = sorted((Path(rundir) / "logs").glob("*_build.*.out"), key=lambda p: p.stat().st_mtime)
    for p in reversed(logs):
        try:
            t = p.read_text(errors="replace")
        except OSError:
            continue
        m = re.search(r"\[hopemd\] protonation states from propka at pH ([0-9.]+)", t)
        if m:
            return ("propka", float(m.group(1)))
        if "[hopemd] pdb2pqr failed" in t or "[hopemd] pdb2pqr not found" in t:
            return ("default", None)
    return None


def build_seeded(rundir: Path) -> bool:
    """Whether the run's build draws its random choices from the run's seed: build/build.sh draws the ions
    tleap placed again from it, or hands it to the bilayer build. A build rendered before either did took
    its ions from the clock."""
    try:
        t = (Path(rundir) / "build" / "build.sh").read_text(errors="replace")
    except OSError:
        return False
    return "drawn again from the run's seed" in t or " --seed " in t


def seed_sentence(spec: Spec, seeded: bool) -> str:
    """What a fixed seed (protocol.seed, 0 or more) set in this run; '' for a new seed per replicate."""
    seed = spec.protocol.seed
    if seed < 0:
        return ""
    dyn = f"every replicate's starting velocities and thermostat were drawn from the fixed random seed {seed} (offset for each replicate and production segment)"
    if spec.engine == "desmond":       # the System Builder draws nothing at random: two builds of one input on Grace wrote the same system
        return f"{dyn[0].upper()}{dyn[1:]}; Desmond's System Builder places the ions the same way on every build."
    if seeded:
        return f"The build's ion placement and {dyn}, so the same seed builds the same system."
    return (f"{dyn[0].upper()}{dyn[1:]}; the build placed its ions where tleap's clock-seeded draw put them, so another build of "
            "this setup places them differently.")


def methods(spec: Spec, prep: dict, system: dict | None, summary: dict,
            build_prot: tuple[str, float | None] | None = None, half_restraints: bool = False,
            seeded: bool = False, ran_mmgbsa: bool | None = None) -> tuple[str, list[str]]:
    """The methods paragraph and the reference keys it cites, numbered in the order the text
    first cites them. The build, the relaxation and the production are described as the run's
    engine renders them (hopemd/engines/*.py), so nothing of Amber's protocol is claimed for
    GROMACS, NAMD or Desmond. build_prot is build_protonation(rundir): without a Prepare stage the
    Amber-topology build protonates the receptor itself, which the paragraph then says. seeded is
    build_seeded(rundir): whether a fixed seed placed the build's ions too.

    ran_mmgbsa says whether the binding energies exist: True writes the sentence in the past,
    False leaves it out, and None (nothing has been analyzed, so nothing is known) writes it as
    what the run was set up to do. It is the caller's to decide, from the run's own files, because
    an absent summary is not evidence that a job ran."""
    box = spec.box
    refs: list[str] = []
    cite = lambda k: (refs.append(k) if k not in refs else None) or f"[{refs.index(k) + 1}]"    # noqa: E731

    def ref(name: str) -> str:
        """' [n]' after a force field or water model that has a reference, '' after one that has not."""
        key = FF_KEYS.get(name, name)
        return f" {cite(key)}" if key in REFERENCES else ""

    eng = spec.engine
    what = subject(spec)
    text = [f"{what} was {source_sentence(spec)}. {prep_sentences(prep, spec)}"]
    prep_cites = []
    if prep and (prep.get("steps", {}).get("model") or {}).get("available"):
        prep_cites.append(cite("pdbfixer"))
    pr_ = (prep or {}).get("steps", {}).get("protonate") or {}
    if pr_.get("method") == "propka" and pr_.get("states") is not None:     # not when PROPKA failed: the step then records no states
        prep_cites.append(cite("pdb2pqr"))
    if prep_cites:
        text[-1] = text[-1].rstrip(".") + " " + " ".join(prep_cites) + "."
    if not prep and build_prot and spec.engine != "desmond":
        # "used as imported" would be only half true: the build's own pdb2pqr step (engines/amber.py) sets the receptor's states
        if build_prot[0] == "propka":
            text[-1] = (f"{what} was {source_sentence(spec)}. The structure was used as imported, except that the build assigned the "
                        f"receptor's protonation states with PROPKA at pH {build_prot[1]:g} through PDB2PQR {cite('pdb2pqr')}, after the "
                        "import had taken the files' waters and hetero groups out; the partner kept the states its residue names give."
                        + breaks_sentence(spec.system.breaks, spec.system.capped))
        else:
            text[-1] = (f"{what} was {source_sentence(spec)}. The structure was used as imported; PDB2PQR was not available to the "
                        "build, so the receptor took tleap's default protonation states (HIE, charged Asp, Glu, Lys and Arg), not "
                        f"states for pH {spec.system.ph:g}." + breaks_sentence(spec.system.breaks, spec.system.capped))
    mem = membrane_of(spec, system)
    text.append(_build_sentence(spec, mem, system, cite, ref))
    if link_sentence(spec, mem):
        text.append(link_sentence(spec, mem))
    for kept in (_carried_sentence(spec, prep, cite), _waters_sentence(spec)):
        if kept:
            text.append(kept)
    text.append(_relaxation_sentence(spec, mem, cite))
    if half_restraints:
        text.append("The restraints in these stages had the form E = ½k·d², with k as given, half as strong as the same k in "
                    "Amber's k·d² form.")
    text.append(_production_sentence(spec, mem, cite))
    if seed_sentence(spec, seeded):
        text.append(seed_sentence(spec, seeded))
    # a protein or nucleic-acid partner is an interface (engines/amber.py, _cpptraj); the chains are traced by their Cα, and a
    # nucleotide by its P and C4'
    partner = spec.system.ligand_class in ("protein", "nucleic_acid")
    rec_trace = trace_words(part_kinds(spec.system, "rec"))
    rmsd = f"receptor {rec_trace} RMSD" if rec_trace == "Cα" else f"receptor RMSD over its {rec_trace} atoms"
    rmsf = "per-residue Cα RMSF" if rec_trace == "Cα" else "per-residue RMSF of the same atoms"
    extra = ""
    if partner:
        extra = (f" the RMSD of the interface {trace_words(part_kinds(spec.system))} atoms (residues within 8 Å of the other chain group) "
                 "after the same fit, and the buried surface area (receptor + partner − complex Connolly surfaces),")
    if spec.system.ligand_class == "none":       # a lone protein or nucleic acid: nothing to measure against
        fit = "after fitting the receptor to the first frame" if rec_trace == "Cα" else "after fitting them to the first frame"
        rmsd = rmsd if rec_trace == "Cα" else f"the RMSD of the {rec_trace} atoms"
        text.append(f"Trajectories were analyzed with cpptraj {cite('cpptraj')}: {rmsd} {fit}, "
                    f"{rmsf} and the radius of gyration"
                    + (", and the area per lipid from the box the barostat relaxed to." if spec.system.environment == "membrane" else "."))
    else:
        text.append(f"Trajectories were analyzed with cpptraj {cite('cpptraj')}: {rmsd} after fitting the receptor to the first frame, "
                    f"{'partner' if partner else 'ligand'} heavy-atom RMSD in that frame without a fit of its own,{extra} {rmsf}, the radius of gyration, and the fraction "
                    f"of {'interface' if partner else 'docking'} contacts (heavy-atom pairs within 4.5 Å in the first frame) present in each frame.")
    reps_ = (summary or {}).get("replicates") or {}
    if ran_mmgbsa is None and reps_:
        ran_mmgbsa = any("mmgbsa" in r for r in reps_.values())
    # ran_mm: whether to write the sentence at all; was_ran: whether the energies exist. Nothing
    # analyzed leaves both unknown, and the sentence then says what the run was set up to do
    # rather than claim a job that has not run.
    ran_mm = ran_mmgbsa is not False
    was_ran = bool(ran_mmgbsa)
    if mmgbsa_refused(spec.system) and spec.system.ligand_class != "none":
        text.append("No MM-GBSA binding energy was computed: it scores the complex against the receptor and the partner taken "
                    "apart, which would cut the bond that joins them and count the bond's own terms as binding.")
    elif spec.analysis.get("mmgbsa", True) and spec.system.ligand_class != "none" and ran_mm:
        from ..spec import mmgbsa_window, mmgbsa_frames
        a0, a1, st = mmgbsa_window(spec)
        window = (f"on {mmgbsa_frames(spec)} frames taken every {st:g} ps between {a0:g} and {a1:g} ns of each replicate, "
                  "reported as the mean ± standard error over frames.")
        were = "were" if was_ran else "were to be"
        if eng == "desmond":
            # jobs/50_mmgbsa.sbatch runs thermal_mmgbsa.py, which deletes the water and the lipids (-membrane puts Prime's implicit
            # membrane in their place) and runs Prime's REAL_MIN job on each frame. No force field is named, so Prime scores with
            # its own S-OPLS whatever the run's force field was (Prime's log says "Using OPLS version S-OPLS for
            # force-field parameters").
            text.append(f"Binding free energies {were} estimated with Prime MM-GBSA {cite('prime_mmgbsa')} (thermal_mmgbsa.py: each frame "
                        "minimized and scored with Prime's S-OPLS (OPLS4) force field and VSGB solvation"
                        + (", the lipids replaced by Prime's implicit membrane" if mem else "") + f") {window}")
        else:
            text.append(f"Binding free energies {were} estimated with MMPBSA.py {cite('mmpbsa')} (GB-Neck2, igb = 8, {box.salt_mM / 1000:g} M salt) {window}")
    text.append(setup_sentence(spec))
    return " ".join(text), refs


def link_sentence(spec: Spec, mem: dict | None) -> str:
    """The disulfides that join the receptor to the partner (System.split_disulfides), and how the run's build bonded
    them: tleap's bond on the Amber-topology engines (engines/amber.py), OpenMM's Modeller on a bilayer
    (membrane.bond_split_disulfides), an SSBOND record for Maestro's reader on Desmond (structure.ssbond_records)."""
    links = split_links(spec.system)
    if not links:
        return ""
    what = f"the disulfide {links[0]}" if len(links) == 1 else f"the disulfides {', '.join(links)}"
    if spec.engine == "desmond":
        how = "written as an SSBOND record, which Maestro's PDB reader bonds"
    elif mem:
        how = "bonded by OpenMM's Modeller when the bilayer was built, both cysteines as CYX"
    else:
        how = ("bonded with tleap's bond command, both cysteines as CYX, since pdb4amber, which reads the receptor and the "
               "partner apart, bonds neither")
    return (f"The receptor and the partner are covalently linked by {what}, which crosses the split between them: it was "
            f"{how}, and the two were simulated as one molecule.")


def setup_sentence(spec: Spec) -> str:
    """The methods paragraph's last sentence: the HOPE-MD that set the run up, and any other
    commit that re-rendered part of it later (Prepare's re-render, an MM-GBSA rerun)."""
    from ..provenance import describe
    entries = spec.rendered_by or []
    if not entries:
        return "The run was set up with HOPE-MD; the commit that rendered it was not recorded."
    first = describe(entries[0])
    later = []
    for e in entries[1:]:
        d = describe(e)
        if d != first and d not in later:
            later.append(f"{d} ({e.get('step', 'a later stage')})")
    return f"The run was set up with {first}" + (f", and re-rendered in part by {'; '.join(later)}" if later else "") + "."


def rendered_by_sentence(spec: Spec) -> str:
    """Which HOPE-MD rendered the run's inputs, from spec.rendered_by (hopemd.provenance); runs
    submitted before it was recorded say so rather than borrow the code reading them now."""
    from ..provenance import describe
    seen = []
    for e in spec.rendered_by or []:
        d = describe(e)
        if d not in seen:
            seen.append(d)
    if not seen:
        return "inputs rendered by a HOPE-MD whose commit was not recorded"
    return "inputs rendered by " + "; then ".join(seen)


def restraints_were_half(rundir: Path, spec: Spec) -> bool:
    """True when the run's inputs held the solute with E = 1/2 k d^2 for the spec's k (Amber and NAMD use k d^2):
    OpenMM's runner and GROMACS's position restraints did in earlier versions of HOPE-MD, so the same weights held
    half as strongly there."""
    try:
        if spec.engine == "openmm":
            return "0.5*k*periodicdistance" in (rundir / "md" / "run_openmm.py").read_text()
        if spec.engine == "gromacs":
            m = re.search(r"POSRES_FC=([0-9.eE+-]+)", (rundir / "md" / "03_eq1.mdp").read_text())
            return bool(m) and abs(float(m.group(1)) - spec.protocol.restraint_kcal * 418.4) < 1e-3
    except (OSError, ValueError):
        return False
    return False


def ladder_as_ran(rundir: Path, spec: Spec) -> tuple[float, float] | None:
    """The heating and NPT lengths (ps) the run's own rendered inputs ask for, when they differ from the spec's:
    earlier versions of the Amber and OpenMM renderers divided picoseconds by 2 instead of 0.002, so the runs they
    rendered heated for 0.2 ps and equilibrated for 1 ps whatever the spec said. None when the inputs match the spec
    or cannot be read (the other engines always ran the spec's lengths)."""
    heat = eq = None
    try:
        if spec.engine == "amber":
            h, e = (rundir / "md" / "02_heat.in").read_text(), (rundir / "md" / "03_eq1.in").read_text()
            steps = lambda t: int(re.search(r"nstlim\s*=\s*(\d+)", t).group(1))   # noqa: E731
            heat, eq = steps(h) * 0.002, 4 * steps(e) * 0.002
        elif spec.engine == "openmm":
            t = (rundir / "md" / "run_openmm.py").read_text()
            heat = int(re.search(r"^HEAT_STEPS = (\d+)", t, re.M).group(1)) * 0.002
            eq = 4 * int(re.search(r"^EQ_STEPS = (\d+)", t, re.M).group(1)) * 0.002
    except (OSError, AttributeError, ValueError):
        return None
    if heat is None or (abs(heat - spec.protocol.heat_ps) < 1e-6 and abs(eq - spec.protocol.equil_ps) < 1e-6):
        return None
    return round(heat, 4), round(eq, 4)


def report_html(rundir: str | Path, options: dict | None = None) -> str:
    """The whole report as one HTML file, for the command line: the module, its stylesheet and the
    run's document in one page that needs no server. The pictures and the 3D view are drawn from
    the run folder, which a file on its own cannot read, so those figures are left out and the
    section says so."""
    from .reportdata import offline_page
    return offline_page(rundir, options)


def write_report(rundir: str | Path) -> Path:
    """export/report.html: the report as a file beside the run's other exports. The page server's
    /report is the live one; this is the copy to send to someone."""
    rundir = Path(rundir)
    out = rundir / "export" / "report.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(report_html(rundir))
    return out


if __name__ == "__main__":
    print(write_report(sys.argv[1] if len(sys.argv) > 1 else "."))
