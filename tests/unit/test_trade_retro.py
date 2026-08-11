from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine

from application.dto.trade_retro import (
    TradeRetroFindingReviewInput,
    TradeRetroHistoryInput,
    TradeRetroReviewInput,
)
from application.services.trade_retro_service import TradeRetroService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import ResearchSubjectStatus, VendorId
from domain.portfolio.enums import (
    AccountActivityCoverageStatus,
    AccountTransactionKind,
    AccountTransactionSide,
)
from domain.portfolio.models import AccountActivityCoverageReceipt, AccountTransaction
from domain.trade_plan.enums import TradePlanStatus
from infrastructure.artifacts.trade_retro import ObsidianTradeRetroExporter
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.trade_retro_repository import SqlAlchemyTradeRetroRepository
from infrastructure.system.redactor import DefaultSecretRedactor
from interfaces.cli import trade_retro as trade_retro_cli
from interfaces.cli.trade_retro import _markdown_section, _weekly_windows

NOW = datetime(2026, 8, 9, 8, tzinfo=UTC)


class _CliEnvelope:
    def __init__(self, *, ok: bool = True, run_id: str | None = None) -> None:
        self.ok = ok
        self.data = SimpleNamespace(run_id=run_id) if run_id else None

    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        return {"ok": self.ok, "run_id": self.data.run_id if self.data else None}


class _CliRetroService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def history(self, request: TradeRetroHistoryInput) -> _CliEnvelope:
        self.calls.append(("history", request))
        return _CliEnvelope()

    def export(self, **kwargs: object) -> _CliEnvelope:
        self.calls.append(("export", kwargs))
        return _CliEnvelope()

    def prepare(self, **kwargs: object) -> _CliEnvelope:
        self.calls.append(("prepare", kwargs))
        return _CliEnvelope()

    async def run(self, **kwargs: object) -> _CliEnvelope:
        self.calls.append(("run", kwargs))
        return _CliEnvelope(run_id="retro_1")

    def import_legacy_markdown(self, **kwargs: object) -> _CliEnvelope:
        self.calls.append(("import", kwargs))
        return _CliEnvelope()


class _CliContainer:
    def __init__(self, service: _CliRetroService) -> None:
        self.services = SimpleNamespace(trade_retro=service)
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["history", "--run-id", "retro_1"], "history"),
        (["export", "--run-id", "retro_1", "--idempotency-key", "export-1"], "export"),
        (
            [
                "prepare",
                "--start",
                "2026-08-03",
                "--end",
                "2026-08-08",
                "--idempotency-key",
                "prepare-1",
            ],
            "prepare",
        ),
        (
            [
                "run",
                "--start",
                "2026-08-03",
                "--end",
                "2026-08-08",
                "--idempotency-key",
                "run-1",
                "--no-llm",
                "--export-obsidian",
            ],
            "run",
        ),
    ],
)
async def test_trade_retro_cli_routes_primary_commands(
    argv: list[str],
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = _CliRetroService()
    container = _CliContainer(service)
    monkeypatch.setattr(trade_retro_cli, "build_default_application", lambda: container)

    assert await trade_retro_cli.run(argv) == 0
    assert service.calls[0][0] == expected
    if expected == "run":
        assert service.calls[1][0] == "export"
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert container.closed


@pytest.mark.asyncio
async def test_trade_retro_weekly_runs_exports_and_prepares_next_window(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = _CliRetroService()
    container = _CliContainer(service)
    monkeypatch.setattr(trade_retro_cli, "build_default_application", lambda: container)
    monkeypatch.setattr(
        trade_retro_cli,
        "_weekly_windows",
        lambda: (
            datetime(2026, 8, 3, tzinfo=UTC),
            datetime(2026, 8, 8, tzinfo=UTC),
            datetime(2026, 8, 10, tzinfo=UTC),
            datetime(2026, 8, 15, tzinfo=UTC),
        ),
    )

    assert await trade_retro_cli.run(["weekly", "--no-llm", "--export-obsidian"]) == 0
    assert [name for name, _ in service.calls] == ["run", "export", "prepare"]
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["export"]["ok"] is True


@pytest.mark.asyncio
async def test_trade_retro_import_markdown_routes_bounded_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "Week31.md"
    path.write_text("# Week\n\n## 2. Retro\nlegacy\n", encoding="utf-8")
    service = _CliRetroService()
    container = _CliContainer(service)
    monkeypatch.setattr(trade_retro_cli, "build_default_application", lambda: container)

    result = await trade_retro_cli.run(
        [
            "import-markdown",
            "--path",
            str(path),
            "--start",
            "2026-07-27",
            "--end",
            "2026-08-01",
            "--idempotency-key",
            "legacy-1",
        ]
    )

    assert result == 0
    assert service.calls[0][0] == "import"
    assert service.calls[0][1]["summary_markdown"] == "## 2. Retro\nlegacy"


def test_weekly_windows_close_after_friday_and_prepare_next_week() -> None:
    review_start, review_end, prepare_start, prepare_end = _weekly_windows(NOW)

    assert review_start == datetime(2026, 8, 3, tzinfo=UTC)
    assert review_end == datetime(2026, 8, 8, tzinfo=UTC)
    assert prepare_start == datetime(2026, 8, 10, tzinfo=UTC)
    assert prepare_end == datetime(2026, 8, 15, tzinfo=UTC)

    before_close = _weekly_windows(datetime(2026, 8, 7, 23, 59, tzinfo=UTC))
    assert before_close[:2] == (
        datetime(2026, 7, 27, tzinfo=UTC),
        datetime(2026, 8, 1, tzinfo=UTC),
    )


def test_markdown_section_extracts_only_retro(tmp_path) -> None:
    path = tmp_path / "Week32.md"
    path.write_text(
        "# Week32\n\n"
        "## 1. Plan\nkeep out\n\n"
        "## 2. Retro\n\nlegacy body\n\n"
        "## 3. Actions\nkeep out\n",
        encoding="utf-8",
    )

    assert _markdown_section(path) == "## 2. Retro\n\nlegacy body"


def test_trade_retro_cli_rejects_invalid_periods_and_markdown(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="supplied together"):
        trade_retro_cli._period("2026-08-03", None)
    with pytest.raises(ValueError, match="non-empty"):
        trade_retro_cli._period("2026-08-08", "2026-08-03")

    text = tmp_path / "retro.txt"
    text.write_text("## 2. Retro\nlegacy", encoding="utf-8")
    with pytest.raises(ValueError, match="Markdown file"):
        _markdown_section(text)

    missing = tmp_path / "missing.md"
    missing.write_text("# Week", encoding="utf-8")
    with pytest.raises(ValueError, match="section was not found"):
        _markdown_section(missing)

    oversized = tmp_path / "oversized.md"
    oversized.write_text("## 2. Retro\n" + ("x" * 50_001), encoding="utf-8")
    with pytest.raises(ValueError, match="exceeds 50000"):
        _markdown_section(oversized)


def test_trade_retro_default_period_is_previous_complete_week() -> None:
    start, end = trade_retro_cli._period(None, None)

    assert end - start == timedelta(days=7)
    assert start.weekday() == 0
    assert end.weekday() == 0


class _Transactions:
    def __init__(self, trade: AccountTransaction, receipt: AccountActivityCoverageReceipt) -> None:
        self.trade = trade
        self.receipt = receipt

    def list(self, **_kwargs: object) -> tuple[AccountTransaction, ...]:
        return (self.trade,)

    def list_coverage(self, **_kwargs: object) -> tuple[AccountActivityCoverageReceipt, ...]:
        return (self.receipt,)


class _ResearchUow:
    def __init__(self) -> None:
        subject = SimpleNamespace(
            subject_id="case_00000000-0000-7000-8000-000000000001",
            title="NVDA",
        )
        plan = SimpleNamespace(
            plan_id="trade_plan_00000000-0000-7000-8000-000000000002",
            version=1,
            thesis_id="thesis_00000000-0000-7000-8000-000000000003",
            instrument_id="equity:US:NVDA",
            status=TradePlanStatus.ACTIVE,
            stop_price=Decimal("90"),
            max_position_percent=Decimal("10"),
            conditions=(),
        )
        self.subjects = SimpleNamespace(
            list=lambda **kwargs: (subject,)
            if kwargs.get("status") is ResearchSubjectStatus.ACTIVE
            else ()
        )
        self.trade_plans = SimpleNamespace(get_current_by_subject=lambda _subject_id: plan)
        self.decisions = SimpleNamespace(list_by_subject=lambda *_args, **_kwargs: ())

    def __enter__(self) -> _ResearchUow:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_trade_retro_uses_pretrade_snapshot_and_preserves_obsidian_text(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'retro.db'}")
    Base.metadata.create_all(engine)
    start = NOW + timedelta(days=1)
    end = start + timedelta(days=7)
    trade = AccountTransaction(
        provider_transaction_id="trade-1",
        account_ref="account-1",
        provider=VendorId.SCHWAB,
        instrument_id="equity:US:NVDA",
        kind=AccountTransactionKind.TRADE,
        side=AccountTransactionSide.BUY,
        quantity=Decimal("2"),
        price=Decimal("100"),
        fees=Decimal("0"),
        currency="USD",
        occurred_at=start + timedelta(days=1),
    )
    receipt = AccountActivityCoverageReceipt(
        receipt_id="activity_coverage_00000000-0000-7000-8000-000000000004",
        provider=VendorId.SCHWAB,
        account_ref="account-1",
        requested_start=start,
        requested_end=end,
        effective_start=start,
        effective_end=end,
        earliest_event_at=trade.occurred_at,
        latest_event_at=trade.occurred_at,
        event_count=1,
        inserted_count=1,
        duplicate_count=0,
        snapshot_count=0,
        earliest_snapshot_at=None,
        latest_snapshot_at=None,
        mapping_version="test-v1",
        supported_kinds=(AccountTransactionKind.TRADE,),
        unavailable_kinds=(),
        status=AccountActivityCoverageStatus.COMPLETE,
        gap_codes=(),
        fetched_at=NOW,
    )
    root = tmp_path / "journal"
    root.mkdir()
    target = root / f"Week{start.isocalendar().week}.md"
    target.write_text("# Handwritten week\n\nKeep me.\n", encoding="utf-8")
    clock = FixedClock(NOW)
    service = TradeRetroService(
        SqlAlchemyTradeRetroRepository(engine),
        _Transactions(trade, receipt),  # type: ignore[arg-type]
        lambda: _ResearchUow(),  # type: ignore[arg-type,return-value]
        ObsidianTradeRetroExporter(root),
        clock,
        SequentialIdGenerator(),
        DefaultSecretRedactor(),
    )

    prepared = service.prepare(start=start, end=end, idempotency_key="prepare-1")
    assert prepared.ok and prepared.data is not None
    prepare_conflict = service.prepare(
        start=start + timedelta(days=7),
        end=end + timedelta(days=7),
        idempotency_key="prepare-1",
    )
    assert not prepare_conflict.ok
    assert prepare_conflict.errors[0].code == "IDEMPOTENCY_CONFLICT"
    clock.set(end + timedelta(hours=1))
    retro = await service.run(
        start=start,
        end=end,
        idempotency_key="run-1",
        use_llm=False,
    )

    assert retro.ok and retro.data is not None
    assert retro.data.status == "COMPLETE"
    assert retro.data.plan_snapshot_id == prepared.data.snapshot_id
    assert [item.code for item in retro.data.findings] == ["ACTION_RECORD_MISMATCH"]
    run_conflict = await service.run(
        start=start,
        end=end,
        idempotency_key="run-1",
        use_llm=True,
    )
    assert not run_conflict.ok
    assert run_conflict.errors[0].code == "IDEMPOTENCY_CONFLICT"
    exported = service.export(run_id=retro.data.run_id, idempotency_key="export-1")
    assert exported.ok and exported.data is not None
    written = target.read_text(encoding="utf-8")
    assert "Keep me." in written
    assert "<!-- trading-partner:retro:start" in written
    assert retro.data.run_id in written
    history = service.history(TradeRetroHistoryInput())
    assert history.ok and history.data is not None
    assert history.data.runs[0].run_id == retro.data.run_id
    finding_key = retro.data.findings[0].finding_key

    clock.set(end + timedelta(hours=2))
    first_review = service.review(
        TradeRetroReviewInput(
            run_id=retro.data.run_id,
            expected_version=0,
            status="DISPUTED",
            note_markdown="这笔成交有口头计划，但没有在系统中留下事前记录。",
            action_items=("下次成交前先确认 Decision Record。",),
            finding_reviews=(
                TradeRetroFindingReviewInput(
                    finding_key=finding_key,
                    status="DISPUTED",
                    note="Finding 正确指出系统证据缺口，但不代表交易没有理由。",
                ),
            ),
            confirmed_by="user",
            authorization_note="User reviewed the retro in the local Console.",
            idempotency_key="review-1",
        )
    )
    assert first_review.ok and first_review.data is not None
    assert first_review.data.version == 1
    assert first_review.data.status == "DISPUTED"

    second_review = service.review(
        TradeRetroReviewInput(
            run_id=retro.data.run_id,
            expected_version=1,
            status="RESOLVED",
            note_markdown="已建立下一次交易前的记录动作。",
            action_items=("成交前确认 Trade Plan 与 Decision Record。",),
            finding_reviews=(
                TradeRetroFindingReviewInput(
                    finding_key=finding_key,
                    status="RESOLVED",
                    note="流程修正已记录。",
                ),
            ),
            confirmed_by="user",
            authorization_note="User saved the follow-up review in the local Console.",
            idempotency_key="review-2",
        )
    )
    assert second_review.ok and second_review.data is not None
    assert second_review.data.version == 2

    stale = service.review(
        TradeRetroReviewInput(
            run_id=retro.data.run_id,
            expected_version=1,
            status="OPEN",
            confirmed_by="user",
            authorization_note="Stale browser tab.",
            idempotency_key="review-stale",
        )
    )
    assert not stale.ok
    assert stale.errors[0].code == "TRADE_RETRO_REVIEW_VERSION_CONFLICT"

    history = service.history(TradeRetroHistoryInput(run_id=retro.data.run_id))
    assert history.ok and history.data is not None
    restored = history.data.runs[0]
    assert restored.latest_review is not None
    assert restored.latest_review.version == 2
    assert [item.version for item in restored.review_history] == [2, 1]

    reviewed_export = service.export(
        run_id=retro.data.run_id,
        idempotency_key="export-review-2",
    )
    assert reviewed_export.ok and reviewed_export.data is not None
    assert reviewed_export.data.review_version == 2
    written = target.read_text(encoding="utf-8")
    assert "人工复核 · v2 · RESOLVED" in written
    assert "成交前确认 Trade Plan 与 Decision Record。" in written
    assert "Keep me." in written


def test_trade_retro_imports_legacy_markdown_without_inventing_findings(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-retro.db'}")
    Base.metadata.create_all(engine)
    root = tmp_path / "journal"
    root.mkdir()
    service = TradeRetroService(
        SqlAlchemyTradeRetroRepository(engine),
        SimpleNamespace(),  # type: ignore[arg-type]
        lambda: _ResearchUow(),  # type: ignore[arg-type,return-value]
        ObsidianTradeRetroExporter(root),
        FixedClock(NOW),
        SequentialIdGenerator(),
        DefaultSecretRedactor(),
    )
    start = datetime(2026, 8, 3, tzinfo=UTC)
    end = datetime(2026, 8, 10, tzinfo=UTC)

    imported = service.import_legacy_markdown(
        start=start,
        end=end,
        generated_at=NOW,
        summary_markdown="## 2. Retro\n\n旧周记复盘原文。",
        idempotency_key="retro-week",
    )

    assert imported.ok and imported.data is not None
    assert imported.data.status == "INCOMPLETE"
    assert imported.data.findings == ()
    assert imported.data.algorithm_version == "trade-retro-legacy-markdown-import-v1"
    assert imported.data.warning_codes == (
        "IMPORTED_LEGACY_MARKDOWN_RETRO",
        "LEGACY_RETRO_FINDINGS_NOT_STRUCTURED",
        "TRANSACTION_COVERAGE_NOT_REVALIDATED",
    )
    duplicate = service.import_legacy_markdown(
        start=start,
        end=end,
        generated_at=NOW,
        summary_markdown="## 2. Retro\n\n旧周记复盘原文。",
        idempotency_key="retro-week",
    )
    assert duplicate.ok and duplicate.data is not None
    assert duplicate.data.run_id == imported.data.run_id
    assert tuple(item.code for item in duplicate.warnings) == (
        "DUPLICATE_IDEMPOTENCY_KEY",
    )
    conflict = service.import_legacy_markdown(
        start=start,
        end=end,
        generated_at=NOW,
        summary_markdown="different historical content",
        idempotency_key="retro-week",
    )
    assert not conflict.ok
    assert conflict.errors[0].code == "IDEMPOTENCY_CONFLICT"
