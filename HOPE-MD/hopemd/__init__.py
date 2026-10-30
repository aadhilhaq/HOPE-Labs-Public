"""HOPE-MD: one spec, several engines.

A run is a spec (see :mod:`hopemd.spec`). An engine renders the spec into the
input files and the Slurm job graph that engine wants (see
:mod:`hopemd.engines`). The server edits specs and submits renders
(:mod:`hopemd.server`). Nothing here knows how to simulate; the engines on the
cluster do that.
"""

import os as _os
import re as _re

__version__ = "0.1.0"

#: The repository root, for LICENSE. Absent in an installed copy.
_CHECKOUT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))

# The copyright is stated in one place, LICENSE, and read from there rather than retyped, so the
# page's bar, the documentation and a run's report cannot state something the license does not.
# The literal is the fallback for an installed copy with no LICENSE beside it.
_FALLBACK_COPYRIGHT = ("Copyright (c) 2026, Sandun Fernando, HOPE Lab, Texas A&M University. "
                       "Authored by Aadhil Haq, Samavath Mallawarachchi and Lasan Manujitha. "
                       "All rights reserved.")


def _licence_line():
    """Line 2 of LICENSE, which is the line carrying the copyright."""
    try:
        with open(_os.path.join(_CHECKOUT, "LICENSE"), encoding="utf-8") as fh:
            fh.readline()
            line = fh.readline().strip()
    except OSError:
        return _FALLBACK_COPYRIGHT
    return line or _FALLBACK_COPYRIGHT


#: What the page's bottom bar, each run's report and the desktop launcher state, as the lab's
#: other tools' bars do. LICENSE is where it is written; this is that line.
COPYRIGHT = _licence_line()
#: The holder and the authors, for the bar along the bottom of the window: everything but the
#: reservation of rights, which is behind Credits on the page, in every run's report, and in
#: LICENSE. It was the holder alone, so no page said who wrote HOPE-MD; the bar now wraps to a
#: second row on a narrow window rather than cutting a name at its edge.
COPYRIGHT_SHORT = _re.sub(r"\s*All rights reserved\.?\s*$", "", COPYRIGHT).strip()

#: Whose tools do the work, for the page's bottom bar; each run's report cites what that run used
CREDITS = ("simulations by Amber, OpenMM, GROMACS, NAMD or Desmond",
           "preparation by PDBFixer and pdb2pqr with PROPKA",
           "MM-GBSA by AmberTools or Prime",
           "viewers 3Dmol.js (Rego & Koes, Bioinformatics 2015) and Mol* (Sehnal et al., Nucleic Acids Res 2021)",
           "surfaces by MSMS (Sanner, Olson & Spehner, Biopolymers 1996)")
HOW_TO_CITE = ("Each run's report (Report, on Runs) names the engine, force field, water model and analysis tools "
               "that run used, with their references. A figure made in the viewer cites the viewer that drew it, "
               "and MSMS when it shows a surface:")
#: What a figure made in the page's viewer cites, beside what the run used: (name, when, reference,
#: doi). The page's How to cite, each run's report, the portfolio and the documentation list them
#: under "if a figure from the viewer is used". MSMS computes the surfaces the 3Dmol.js viewers of
#: the Setup and Runs screens draw (hopemd.surface).
VIEWER_REFERENCES = (
    ("3Dmol.js", "a figure from the viewer",
     "Rego N, Koes D. 3Dmol.js: molecular visualization with WebGL. Bioinformatics 31(8):1322-1324, 2015.",
     "10.1093/bioinformatics/btu829"),
    ("Mol*", "a figure from the Mol* viewer",
     "Sehnal D, Bittrich S, Deshpande M, et al. Mol* Viewer: modern web app for 3D visualization and analysis of "
     "large biomolecular structures. Nucleic Acids Res 49(W1):W431-W437, 2021.",
     "10.1093/nar/gkab314"),
    ("MSMS", "a figure with a surface from the viewer",
     "Sanner MF, Olson AJ, Spehner JC. Reduced surface: an efficient way to compute molecular surfaces. "
     "Biopolymers 38(3):305-320, 1996.",
     "10.1002/(SICI)1097-0282(199603)38:3<305::AID-BIP4>3.0.CO;2-Y"),
)
ACKNOWLEDGE = "Please acknowledge Texas A&M High Performance Research Computing (HPRC) for the computational resources."
