"""Exports from a run folder: the report, the portfolio, the videos and the snapshots.

report: reportdata.py builds the document a run's report is (its sections, tables, figures and
captions) from spec.json, the Prepare report, system.json and the analysis files, and reportkit,
the module the lab's tools share, lays it out at /report and prints it to PDF; report.py holds the
methods paragraph, the references, the caveats and the plots it is written from, and writes the
whole thing as one file for the command line. portfolio: portfolio.py, one PDF across runs. video and snapshots: video.py
and snapshots.py, CPU queue jobs in which cpptraj cuts the frames from the production
trajectory, VMD in text mode writes them as POV-Ray scenes in the page's color convention,
POV-Ray renders them and FFmpeg writes the two MP4s (the solute alone, and in its water) and
the first and last frames as PNGs into export/.
"""
from .report import report_html, write_report
from .reportdata import document, page

__all__ = ["report_html", "write_report", "document", "page"]
