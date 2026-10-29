"""One report across runs, as a PDF: what was simulated, how, what came out and how long it
took, for a reader who will not open the page.

    python3 -m hopemd.export.portfolio <runs_root> [--runs NAME ...] [--out FILE.pdf]
                                       [--title TEXT] [--author TEXT]

Every number, sentence and figure comes from the run folders (spec.json, build/, analysis/,
jobs/timing.json, export/snap_*.png), through the same code the per-run report uses; nothing
is typed. Sections: an overview table of every run; per run the system, the protocol, the
results with plots, the snapshots (first and last frame, solute alone and in its water or
bilayer), the timing table beside the estimate made at submit; then the methods paragraph
of each distinct protocol with its references, and where the files are.
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
import time
from pathlib import Path
from xml.sax.saxutils import escape as E     # every string that reaches a Paragraph: & and < are markup there

from .. import __version__, VIEWER_REFERENCES
from ..brand import PALETTE, NAME
from ..spec import Spec, ENGINE_LABELS, has_nucleic, part_kinds
from ..timing import timing
from .report import _load, source_sentence, prep_sentences, methods, build_seeded, REFERENCES, _fmt, trace_words, alphafold_model
from .snapshots import snapshots
from .video import videos

COLOURS = [PALETTE["teal"][0], PALETTE["rust"][0], PALETTE["green"][0], PALETTE["amber"][0], "#5f6f71"]


def _authors() -> str:
    """The authors and the first affiliation in CITATION.cff, as 'Name, Name · Affiliation', or nothing."""
    p = Path(__file__).resolve().parent.parent.parent / "CITATION.cff"
    try:
        text = p.read_text()
    except OSError:
        return ""
    names = re.findall(r"given-names:\s*(.+)\n\s*family-names:\s*(.+)", text)
    aff = re.search(r'affiliation:\s*"?([^"\n]+)"?', text)
    people = ", ".join(f"{g.strip()} {f.strip()}" for g, f in names)
    return people + (f" · {aff.group(1).strip()}" if aff else "")


def short_system(spec: Spec) -> str:
    src, s = spec.system.source, spec.system
    if src.kind == "rcsb":
        title = (s.meta or {}).get("title", "")
        return f"{src.pdb_id.upper()} · {title.title()[:44]}" if title else src.pdb_id.upper()
    if src.kind == "alphafold":
        return f"AlphaFold {alphafold_model(spec)}"
    return Path(src.path).name if src.path else src.kind


def short_gpu(name: str) -> str:
    m = re.search(r"(H100|A100|A40|A30|RTX|T4)", name or "", re.I)
    return m.group(1).upper() if m else (name or "")


def _crop(png: Path, out: Path, margin: float = 0.06) -> Path:
    """The renders are 1920×1080 with the molecule in the middle: crop to what was drawn."""
    from PIL import Image, ImageChops
    im = Image.open(png).convert("RGB")
    bg = Image.new("RGB", im.size, (255, 255, 255))
    diff = ImageChops.difference(im, bg).convert("L").point(lambda v: 255 if v > 12 else 0)
    box = diff.getbbox()
    if not box:
        return png
    w, h = im.size
    mx, my = int((box[2] - box[0]) * margin) + 8, int((box[3] - box[1]) * margin) + 8
    box = (max(0, box[0] - mx), max(0, box[1] - my), min(w, box[2] + mx), min(h, box[3] + my))
    im.crop(box).save(out)
    return out


def _fonts():
    """DejaVu Sans (it comes with matplotlib): the report's text has Å, ×, ± and superscripts."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    try:
        import matplotlib
        d = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
        pdfmetrics.registerFont(TTFont("Body", str(d / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("Body-Bold", str(d / "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFont(TTFont("Mono", str(d / "DejaVuSansMono.ttf")))
        return "Body", "Body-Bold", "Mono"
    except Exception:
        return "Helvetica", "Helvetica-Bold", "Courier"


def _plots(name: str, reps: dict, partner: bool, out_dir: Path, rec_trace: str = "Cα", trace: str = "Cα") -> Path | None:
    """One PNG with the time series of every replicate: RMSD (receptor, partner or ligand,
    interface), RMSF per residue, radius of gyration, buried surface or area per lipid. The traces
    name the atoms the RMSD is over (report.trace_words)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    first = reps.get("1") or (next(iter(reps.values())) if reps else {})
    panels = []
    if any("rmsd_rec" in r for r in reps.values()):
        panels.append(("RMSD, Å", [(f"receptor {rec_trace}", "rmsd_rec"), (("partner" if partner else "ligand") + " heavy atoms", "rmsd_lig"), (f"interface {trace}", "rmsd_iface")]))
    if "rmsf" in first:
        panels.append(("RMSF per residue, Å", [("", "rmsf")]))
    if any("rg" in r for r in reps.values()):
        panels.append(("radius of gyration, Å", [("", "rg")]))
    if any("bsa" in r for r in reps.values()):
        panels.append(("buried surface, Å²", [("", "bsa")]))
    if any("area_per_lipid" in r for r in reps.values()):
        panels.append(("area per lipid, Å² (solute footprint included)", [("", "area_per_lipid")]))
    if not panels:
        return None
    ncol = 2 if len(panels) > 2 else len(panels)
    nrow = (len(panels) + ncol - 1) // ncol
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.4 * ncol, 2.5 * nrow), dpi=170, squeeze=False)
    axes = [ax for row in axes for ax in row]
    for ax in axes[len(panels):]:
        ax.axis("off")
    for ax, (ylabel, series) in zip(axes, panels):
        k = 0
        for label, key in series:
            for i, (rep, r) in enumerate(sorted(reps.items(), key=lambda kv: int(kv[0]))):
                if key not in r:
                    continue
                xs = r[key].get("residue") if key == "rmsf" else r[key].get("ns")
                ys = r[key].get("values")
                if not xs or not ys:
                    continue
                lab = (label + " " if label else "") + (f"r{rep}" if len(reps) > 1 else "")
                ax.plot(xs, ys, lw=1.0, color=COLOURS[k % len(COLOURS)], label=lab.strip() or None)
                k += 1
        ax.set_xlabel("residue" if ylabel.startswith("RMSF") else "ns", fontsize=7)
        ax.set_ylabel(ylabel, fontsize=7)
        ax.tick_params(labelsize=6)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        if k > 1:
            ax.legend(fontsize=5.5, frameon=False)
    fig.tight_layout()
    p = out_dir / f"{name}_plots.png"
    fig.savefig(p)
    plt.close(fig)
    return p


def _run_block(rundir: Path, styles, fonts, tmp: Path, story_parts: dict) -> list:
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle, Image, PageBreak, KeepTogether
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    body, bold, mono = fonts
    spec, prep, system, summary, ids = _load(rundir)
    reps = summary.get("replicates", {})
    first = reps.get("1") or (next(iter(reps.values())) if reps else {})
    s, ff, box, pro, cmp = spec.system, spec.forcefield, spec.box, spec.protocol, spec.compute
    site = spec.site
    partner = s.ligand_class in ("protein", "nucleic_acid")
    rec_trace, trace = trace_words(part_kinds(s, "rec")), trace_words(part_kinds(s))
    H2, P, SMALL = styles["h2"], styles["body"], styles["small"]
    els = [PageBreak(), Paragraph(E(spec.name), styles["h1"])]
    # ---- system
    sysline = source_sentence(spec) + ". " + prep_sentences(prep, spec)
    if system:
        sysline += (f" Built system: {_fmt(system.get('atoms'))} atoms, {_fmt(system.get('waters'))} waters, "
                    f"{_fmt(system.get('ion_pairs'))} {box.salt} pairs, box {system.get('box', '')} Å" + (", HMR." if system.get("hmr") else "."))
        if system.get("lipids"):
            sysline += (f" Bilayer: {system.get('lipid', s.membrane.lipid)}, {system['lipids']} lipids, {s.membrane.padding_A:g} Å of water above and below, "
                        f"centered at z = {s.membrane.center_z_A:g} Å" + (f", OPM orientation (planes at ±{s.meta['opm_half_thickness_A']} Å)" if s.meta.get("opm_half_thickness_A") else "") + ".")
    els += [Paragraph("System", H2), Paragraph(E(sysline), P)]
    # ---- protocol
    rows = [["engine", f"{ENGINE_LABELS.get(spec.engine, spec.engine)} · {site.module_block(spec.engine).replace(chr(10), ' ; ')}"],
            ["force field · water", f"{ff.protein}" + ("" if not has_nucleic(s) else ", which types the nucleic acid as well" if spec.engine == "desmond"
                                                        else f" · nucleic acid {ff.nucleic or '—'}") + f" · {ff.water}" + (f" · ligand {ff.ligand}" if ff.ligand else "")],
            ["box · salt", f"{box.shape} · {box.padding_A:g} Å padding · {box.salt_mM:g} mM {box.salt}" if s.environment != "membrane" else f"rectangular (bilayer) · {box.salt_mM:g} mM {box.salt}"],
            ["ladder", f"{pro.minimise_steps} min steps · heat {pro.heat_ps:g} ps · NPT {pro.equil_ps:g} ps in 4 stages ({pro.restraint_kcal:g} → 1 → 0.1 → 0 kcal/mol/Å²)"],
            ["production", f"{pro.length_ns:g} ns × {pro.replicates} · {pro.temperature_K:g} K · {pro.pressure_bar:g} bar · {pro.timestep_fs:g} fs{' (HMR)' if spec.hmr else ''} · frames every {pro.save_ps:g} ps"],
            ["compute", f"{site.name} · {'CPU node' if spec.on_cpu else spec.gpu.upper()} · partition {cmp.partition or site.cpu_partition if spec.on_cpu else spec.gpu_partition}"]]
    els += [Paragraph("Protocol", H2), _table(rows, fonts, styles, [3.2 * cm, 13.3 * cm])]
    # ---- results
    els.append(Paragraph("Results", H2))
    if not first:
        els.append(Paragraph("No analysis yet.", P))
    else:
        res = []
        if "rmsd_rec" in first: res.append([f"receptor {rec_trace} RMSD (r1)", f"{first['rmsd_rec']['mean']} Å mean · max {first['rmsd_rec']['max']}"])
        if "rmsd_lig" in first: res.append([("partner" if partner else "ligand") + " RMSD, receptor fitted (r1)", f"{first['rmsd_lig']['mean']} Å mean · max {first['rmsd_lig']['max']}"])
        if "rmsd_iface" in first: res.append([f"interface {trace} RMSD (r1)", f"{first['rmsd_iface']['mean']} Å mean · max {first['rmsd_iface']['max']}"])
        if "bsa" in first: res.append(["buried surface (r1)", f"{_fmt(round(first['bsa']['mean']))} Å² mean · started at {_fmt(round(first['bsa']['first']))}"])
        if "rmsf" in first: res.append(["RMSF per residue", f"{first['rmsf']['median']} Å median"])
        if "rg" in first: res.append(["radius of gyration (r1)", f"{first['rg']['mean']} Å mean"])
        if "area_per_lipid" in first: res.append(["area per lipid (r1)", f"{first['area_per_lipid']['mean']} Å² mean, over half the lipids with the solute's footprint included"])
        if "contacts" in first:
            c = first["contacts"]
            res.append([("interface" if partner else "docking") + " contacts kept", f"{c['kept']} / {c['total']} present in more than {round(c['threshold'] * 100)}% of r1's frames"
                        + ("; lost: " + ", ".join(x["contact"] for x in c.get("lost", [])[:4]) if c.get("lost") else "")])
        for k, r in sorted(reps.items(), key=lambda kv: int(kv[0])):
            m = r.get("mmgbsa")
            if m: res.append([f"MM-GBSA r{k}", f"{m['mean']:.1f} ± {m['sem']:.1f} kcal/mol (SEM over {_fmt(m['frames'])} frames; SD {m['std']:.1f})"])
        if s.confidence.get("iptm") is not None:
            res.append(["AlphaFold confidence", f"ipTM {s.confidence['iptm']} · pTM {s.confidence.get('ptm', '—')}"])
        if res:
            els.append(_table(res, fonts, styles, [5.2 * cm, 11.3 * cm]))
        png = _plots(spec.name, reps, partner, tmp, rec_trace, trace)
        if png:
            from PIL import Image as PILImage
            w, h = PILImage.open(png).size
            width = min(16.5 * cm, 16.5 * cm)
            els += [Spacer(1, 4), Image(str(png), width=width, height=width * h / w)]
    # ---- snapshots
    snaps = snapshots(rundir).get("r1", {})
    if snaps:
        cells, caps = [], []
        for key, cap in (("first_dry", "first frame, solute"), ("last_dry", "last frame, solute"),
                         ("first_water", "first frame, in water" + (" and lipids" if system and system.get("lipids") else "")),
                         ("last_water", "last frame, in water" + (" and lipids" if system and system.get("lipids") else ""))):
            if key in snaps:
                p = rundir / snaps[key]
                from PIL import Image as PILImage
                w, h = PILImage.open(p).size
                p = _crop(p, tmp / f"{spec.name}_{key}.png")
                w, h = PILImage.open(p).size
                width = 8.1 * cm; height = width * h / w
                if height > 7.5 * cm:
                    height = 7.5 * cm; width = height * w / h
                cells.append(Image(str(p), width=width, height=height)); caps.append(Paragraph(E(cap), SMALL))
        grid = [cells[i:i + 2] for i in range(0, len(cells), 2)]
        capgrid = [caps[i:i + 2] for i in range(0, len(caps), 2)]
        rows2 = []
        for g, c in zip(grid, capgrid):
            rows2.append(g); rows2.append(c)
        t = Table(rows2, colWidths=[8.25 * cm, 8.25 * cm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        els += [Paragraph("Snapshots", H2), Paragraph("Receptor gray, the thing being simulated rust, the interface teal; water pale blue, ions amber, lipids sand. 1920×1080 renders by VMD and POV-Ray, scaled.", SMALL), t]
    # ---- timing
    try:
        tm = timing(rundir)
    except Exception:
        tm = None
    if tm and tm.get("jobs"):
        head = ["stage", "job", "state", "node", "device", "steps / frames", "wall", "ns/day"]
        trows = [head]
        for j in tm["jobs"]:
            dev = j.get("gpu") or (f"{j.get('cpus', '')} CPU" if j.get("cpus") else "")
            trows.append([j["key"] + (f" r{j['task']}" if j.get("task") else ""), j.get("job", ""), (j.get("state") or "").lower(), j.get("node", ""), dev,
                          _fmt(j.get("steps")) if j.get("steps") else (_fmt(j.get("frames")) + " frames" if j.get("frames") else "—"), j.get("elapsed", ""), _fmt(j.get("ns_per_day"))])
        est = tm.get("estimate", {})
        prod = [j for j in tm["jobs"] if j["key"].startswith("prod_s") and j.get("ns_per_day")]
        measured = (sum(j["ns_per_day"] for j in prod) / len(prod)) if prod else None
        els += [Paragraph("Timing", H2), _table(trows, fonts, styles, None, header=True),
                Paragraph(E(f"Whole graph {tm.get('wall', '—')} from the first job's start to the last job's end; {tm.get('busy', '—')} of node time. "
                            f"Estimated at submit: {est.get('ns_per_day', '—')} ns/day ({est.get('note', '')}), walltime asked {est.get('walltime', '—')} per segment"
                            + (f"; measured {measured:.1f} ns/day." if measured else ".")), SMALL)]
    vids = videos(rundir).get("r1", {})
    files = [v for k, v in vids.items() if k in ("file", "water_file")]
    els.append(Paragraph(E("Files: " + str(rundir) + " (spec.json, build/, r1/, analysis/, export/report.html" + (", " + ", ".join(files) if files else "") + ")"), SMALL))
    # for the methods section
    meth, refs = methods(spec, prep, system, summary, seeded=build_seeded(rundir))
    for r in refs:
        if r not in story_parts["_refs"]:
            story_parts["_refs"].append(r)
    # the paragraph cites its own [n]; the report has one reference list, so renumber against it
    meth = re.sub(r"\[(\d+)\]", lambda m: f"[{story_parts['_refs'].index(refs[int(m.group(1)) - 1]) + 1}]" if 0 < int(m.group(1)) <= len(refs) else m.group(0), meth)
    story_parts.setdefault(meth, []).append(spec.name)
    story_parts["_overview"].append([spec.name, short_system(spec), {"amber": "Amber", "openmm": "OpenMM", "gromacs": "GROMACS", "namd": "NAMD", "desmond": "Desmond"}.get(spec.engine, spec.engine) + (" · bilayer" if system and system.get("lipids") else ""),
                                     _fmt((system or {}).get("atoms")) if system else "—", f"{pro.length_ns:g} ns × {pro.replicates}",
                                     ("CPU node" if spec.on_cpu else short_gpu(next((j.get("gpu") for j in (tm or {}).get("jobs", []) if j.get("gpu")), spec.gpu))) if tm else spec.gpu.upper(),
                                     _fmt(next((j.get("ns_per_day") for j in (tm or {}).get("jobs", []) if j.get("ns_per_day")), None)) if tm else "—",
                                     (tm or {}).get("wall", "—") if tm else "—",
                                     f"{first['rmsd_rec']['mean']} Å" if first and "rmsd_rec" in first else "—"])
    return els


def _table(rows, fonts, styles, widths, header=False):
    from reportlab.platypus import Table, TableStyle, Paragraph
    from reportlab.lib import colors
    body, bold, mono = fonts
    cell = styles["cell"]
    data = [[Paragraph(E(str(c)), cell) for c in r] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#d7e2e2")),
          ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("LEFTPADDING", (0, 0), (-1, -1), 3)]
    if header:
        st += [("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#0d5c6b")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#5f6f71"))]
    else:
        st += [("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#5f6f71"))]
    t.setStyle(TableStyle(st))
    return t


def build(runs_root: str | Path, out: str | Path, names: list[str] | None = None, title: str = "", author: str = "") -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
    root = Path(runs_root)
    dirs = [root / n for n in names] if names else sorted(p for p in root.iterdir() if (p / "spec.json").exists() and not p.name.startswith("_"))
    dirs = [d for d in dirs if (d / "spec.json").exists()]
    fonts = _fonts()
    body, bold, mono = fonts
    styles = {
        "title": ParagraphStyle("t", fontName=bold, fontSize=20, leading=24, textColor=colors.HexColor("#0f1719")),
        "sub": ParagraphStyle("s", fontName=body, fontSize=10, leading=14, textColor=colors.HexColor("#5f6f71")),
        "h1": ParagraphStyle("h1", fontName=bold, fontSize=15, leading=19, spaceAfter=4, textColor=colors.HexColor("#0d5c6b")),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=9, leading=12, spaceBefore=9, spaceAfter=3, textColor=colors.HexColor("#5f6f71")),
        "body": ParagraphStyle("b", fontName=body, fontSize=9, leading=12.5),
        "small": ParagraphStyle("sm", fontName=body, fontSize=7.5, leading=10, textColor=colors.HexColor("#5f6f71")),
        "cell": ParagraphStyle("c", fontName=body, fontSize=7.8, leading=10),
    }
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    title = title or f"{NAME}: runs on {Spec.from_json((dirs[0] / 'spec.json').read_text()).site.name if dirs else 'the cluster'}"
    author = author or _authors()
    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.6 * cm, bottomMargin=1.6 * cm,
                            title=title, author=author.split(" · ")[0] if author else "", subject=f"{NAME} {__version__} report")
    parts: dict = {"_refs": [], "_overview": []}
    story = [Paragraph(E(title), styles["title"]), Spacer(1, 4),
             Paragraph(E((author + " · " if author else "") + time.strftime("%d %B %Y") + f" · {NAME} {__version__}"), styles["sub"]), Spacer(1, 10),
             Paragraph(E(f"{NAME} runs molecular dynamics of docked and predicted complexes on the cluster from one specification per run: import, prepare, build, equilibrate, produce, analyze, "
                       f"with Amber, OpenMM, GROMACS, NAMD or Desmond as the engine. This report gathers {len(dirs)} run{'s' if len(dirs) != 1 else ''} from {root}: what was simulated, how, what came out, and how long each step took on which node. "
                       "Every number and sentence comes from the run folders through the same code the page and the per-run reports use."), styles["body"]), Spacer(1, 8)]
    with tempfile.TemporaryDirectory(dir=str(out.parent)) as tmpdir:
        tmp = Path(tmpdir)
        blocks = []
        for d in dirs:
            try:
                blocks += _run_block(d, styles, fonts, tmp, parts)
            except Exception as e:
                blocks += [PageBreak(), Paragraph(E(d.name), styles["h1"]), Paragraph(E(f"This run could not be read: {e}"), styles["body"])]
        head = [["run", "system", "engine", "atoms", "length", "device", "ns/day", "whole graph", "receptor RMSD"]]
        story += [Paragraph("Overview", styles["h2"]),
                  _table(head + parts["_overview"], fonts, styles, [3.5 * cm, 3.8 * cm, 2.1 * cm, 1.3 * cm, 1.6 * cm, 1.5 * cm, 1.3 * cm, 1.4 * cm, 1.4 * cm], header=True)]
        story += blocks
        story += [PageBreak(), Paragraph("Methods", styles["h1"])]
        for meth, names_ in parts.items():
            if meth.startswith("_"):
                continue
            story += [Paragraph(E(", ".join(names_)), styles["h2"]), Paragraph(E(meth), styles["body"])]
        story += [Paragraph("References", styles["h2"])]
        for i, k in enumerate(parts["_refs"], 1):
            story.append(Paragraph(E(f"{i}. {REFERENCES[k][0]}: {REFERENCES[k][1]}"), styles["small"]))
        # a figure made in the page's viewer cites the viewer, and MSMS when it shows a surface
        story += [Paragraph("If a figure from the viewer is used", styles["h2"])]
        for n, w, r, d in VIEWER_REFERENCES:
            story.append(Paragraph(E(f"{n}, for {w}: {r} doi:{d}"), styles["small"]))
        story += [Paragraph("Where the files are", styles["h2"]),
                  Paragraph(E(f"Each run is a folder under {root}: spec.json (the run as submitted), build/ (topology), r1/ (trajectory, checkpoints, engine output), "
                            "analysis/ (cpptraj series, MM-GBSA, summary.json, the strided trajectory for the viewer), jobs/ (the sbatch scripts, ids.json, timing.json), "
                            "logs/ (Slurm output), export/ (report.html, the two videos, the snapshots), archive/ (the small files the tidy job packed). Open them in the page through the desktop launcher (Runs tab, Browse to this folder) or on a login node with "
                            "python3 -m hopemd.server --runs <folder>."), styles["small"])]
        doc.build(story)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="one PDF across runs")
    ap.add_argument("runs_root")
    ap.add_argument("--runs", nargs="*", default=None)
    ap.add_argument("--out", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--author", default="")
    a = ap.parse_args(argv)
    out = a.out or str(Path(a.runs_root) / f"{NAME.replace(' ', '_')}_report_{time.strftime('%Y-%m-%d')}.pdf")
    p = build(a.runs_root, out, a.runs, a.title, a.author)
    print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
