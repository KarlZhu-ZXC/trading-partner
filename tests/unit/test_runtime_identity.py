"""Focused tests for safe Console source/build identity snapshots."""

from __future__ import annotations

from pathlib import Path

from interfaces.console.runtime_identity import build_runtime_identity


def test_runtime_identity_reports_schema_and_next_build_id(tmp_path: Path) -> None:
    console_root = tmp_path / "console"
    (console_root / ".next").mkdir(parents=True)
    (console_root / ".next" / "BUILD_ID").write_text("build-20260908_abc\n", encoding="utf-8")

    snapshot = build_runtime_identity(
        schema_actual_heads=("head-1",),
        schema_expected_head="head-1",
        source_root=tmp_path,
        console_root=console_root,
    )

    assert snapshot.schema_actual_heads == ("head-1",)
    assert snapshot.schema_expected_head == "head-1"
    assert snapshot.schema_compatible is True
    assert snapshot.next_build_id == "build-20260908_abc"
    assert snapshot.identity_basis == "on_disk"
    assert snapshot.application_version


def test_runtime_identity_marks_schema_mismatch_and_unknown_build(tmp_path: Path) -> None:
    console_root = tmp_path / "console"
    (console_root / ".next").mkdir(parents=True)
    (console_root / ".next" / "BUILD_ID").write_text("invalid build id\n", encoding="utf-8")

    snapshot = build_runtime_identity(
        schema_actual_heads=("old-head",),
        schema_expected_head="new-head",
        source_root=tmp_path,
        console_root=console_root,
    )

    assert snapshot.schema_compatible is False
    assert snapshot.next_build_id is None


def test_runtime_identity_uses_source_console_build_by_default(tmp_path: Path) -> None:
    console_root = tmp_path / "console"
    (console_root / ".next").mkdir(parents=True)
    (console_root / ".next" / "BUILD_ID").write_text("source-build", encoding="utf-8")

    snapshot = build_runtime_identity(
        schema_actual_heads=("head",),
        schema_expected_head="head",
        source_root=tmp_path,
    )

    assert snapshot.next_build_id == "source-build"
