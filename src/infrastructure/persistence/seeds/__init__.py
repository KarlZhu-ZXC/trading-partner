"""Packaged instrument master seed resources (Phase 1D)."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def default_instruments_seed_path() -> Path:
    """Filesystem path to the canonical packaged instruments seed JSON.

    Works for editable installs and wheels that ship package data next to this
    module. Prefer :func:`read_instruments_seed_text` when only content is needed.
    """
    return Path(__file__).resolve().parent / "instruments_seed.json"


def read_instruments_seed_text() -> str:
    """Read seed JSON via importlib.resources (packaging-safe)."""
    return (
        files("infrastructure.persistence.seeds")
        .joinpath("instruments_seed.json")
        .read_text(encoding="utf-8")
    )
