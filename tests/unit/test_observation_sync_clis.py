from __future__ import annotations

import json
from argparse import Namespace
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from application.ports.external_note_provider import ObservationSourceCapability
from domain.external_note.enums import NoteSyncStatus
from domain.external_note.models import ExternalNoteSyncReceipt
from interfaces.cli import moomoo_notes_sync, observation_sync


class _ExternalNotes:
    def __init__(self, status: NoteSyncStatus = NoteSyncStatus.SUCCEEDED) -> None:
        self.status = status
        self.sync_sources: list[str | None] = []
        self.analysis_requests: list[tuple[int, bool, bool]] = []

    async def sync(self, *, analyze: bool, source_code: str | None) -> ExternalNoteSyncReceipt:
        assert analyze is False
        self.sync_sources.append(source_code)
        now = datetime(2026, 9, 3, tzinfo=UTC)
        return ExternalNoteSyncReceipt(
            receipt_id="external_note_sync_cli",
            status=self.status,
            cache_files_scanned=1,
            notes_seen=1,
            identities_created=0,
            revisions_created=1,
            unchanged_count=0,
            full_count=1,
            summary_only_count=0,
            interpretations_created=0,
            warning_codes=(),
            error_codes=("SOURCE_FAILED",) if self.status is NoteSyncStatus.FAILED else (),
            started_at=now,
            completed_at=now,
        )

    async def analyze_pending(
        self,
        *,
        limit: int,
        retry_failed: bool,
        reanalyze_succeeded: bool,
    ) -> tuple[Any, ...]:
        self.analysis_requests.append((limit, retry_failed, reanalyze_succeeded))
        return (
            SimpleNamespace(status="SUCCEEDED", error_code=None),
            SimpleNamespace(status="FAILED", error_code="INVALID_OUTPUT"),
        )

    def source_capabilities(self) -> tuple[ObservationSourceCapability, ...]:
        return (
            ObservationSourceCapability(
                source_code="LOCAL_OBSERVATION_BRIDGE",
                display_name="Local Observation Bridge",
                supports_full_text=True,
                supports_incremental_sync=True,
                requires_interactive_session=False,
                content_modes=("FULL_TEXT",),
            ),
        )


def _container(service: _ExternalNotes):
    @asynccontextmanager
    async def context():
        yield SimpleNamespace(services=SimpleNamespace(external_notes=service))

    return context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "source"),
    ((observation_sync, "LOCAL_OBSERVATION_BRIDGE"), (moomoo_notes_sync, "MOOMOO_NOTE")),
)
async def test_observation_sync_clis_report_bounded_analysis(
    module: Any,
    source: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = _ExternalNotes()
    monkeypatch.setattr(module, "application_container", _container(service))
    args = Namespace(
        source=source,
        analyze=True,
        analysis_limit=7,
        retry_failed=False,
        reanalyze_all=True,
    )

    assert await module._run(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["analysis_attempted"] == 2
    assert payload["analysis_succeeded"] == 1
    assert payload["analysis_failed"] == 1
    assert payload["analysis_error_codes"] == {"INVALID_OUTPUT": 1}
    assert service.sync_sources == [source]
    assert service.analysis_requests == [(7, True, True)]


@pytest.mark.asyncio
async def test_observation_sync_cli_failed_source_skips_analysis(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = _ExternalNotes(NoteSyncStatus.FAILED)
    monkeypatch.setattr(observation_sync, "application_container", _container(service))
    args = observation_sync._parser().parse_args([])

    assert await observation_sync._run(args) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["sources"][0]["source_code"] == "LOCAL_OBSERVATION_BRIDGE"
    assert payload["analysis_attempted"] == 0
    assert service.analysis_requests == []


def test_observation_sync_cli_parsers_keep_source_boundaries() -> None:
    observation_args = observation_sync._parser().parse_args(
        ["--source", "LOCAL_OBSERVATION_BRIDGE", "--retry-failed"]
    )
    moomoo_args = moomoo_notes_sync._parser().parse_args(["--analyze"])

    assert observation_args.source == "LOCAL_OBSERVATION_BRIDGE"
    assert observation_args.retry_failed is True
    assert moomoo_args.analyze is True
    assert not hasattr(moomoo_args, "source")
