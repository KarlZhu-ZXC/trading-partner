from __future__ import annotations

import runpy
from pathlib import Path


def test_daily_equity_migration_has_stable_activation_projection_contract() -> None:
    root = Path(__file__).resolve().parents[2]
    values = runpy.run_path(str(root / "migrations/versions/0060_daily_equity_projection.py"))

    assert values["revision"] == "0060_daily_equity_projection"
    # The behavior-review agent owns 0059; this explicit dependency prevents a
    # second migration branch from silently becoming the Daily Equity parent.
    assert values["down_revision"] == "0059_behavior_reviews"
