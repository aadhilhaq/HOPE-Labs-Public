"""Engines render a spec into files and a job graph. Nothing else."""
from __future__ import annotations

from .base import Engine, Render, RenderedFile, Job
from .amber import AmberEngine
from .openmm import OpenMMEngine
from .gromacs import GromacsEngine
from .namd import NamdEngine
from .desmond import DesmondEngine

ENGINES: dict[str, type[Engine]] = {
    "amber": AmberEngine,
    "openmm": OpenMMEngine,
    "gromacs": GromacsEngine,
    "namd": NamdEngine,
    "desmond": DesmondEngine,
}


def engine_for(key: str) -> Engine:
    try:
        return ENGINES[key]()
    except KeyError:
        raise ValueError(f"no engine called {key!r}; have {', '.join(ENGINES)}") from None


__all__ = ["Engine", "Render", "RenderedFile", "Job", "ENGINES", "engine_for"]
