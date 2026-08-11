from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from application.dto.broker_execution import (
    BrokerOrderPreviewAccountDTO,
    BrokerOrderPreviewDTO,
    BrokerQuotePreviewDTO,
    SgovShadowPlanDisposition,
)
from application.dto.notifications import NotificationFlushDisposition
from application.ports.market_session_calendar import MarketSession
from application.services.sgov_shadow_plan_service import SgovShadowPlanService
from domain.common.enums import VendorId
from domain.notifications.enums import NotificationStatus
from interfaces.cli.sgov_shadow_plan import _render_table
from interfaces.cli.sgov_shadow_scheduler import LABEL, _launchd_payload


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.value = now

    def now(self) -> datetime:
        return self.value


class _Calendar:
    def __init__(self, session: MarketSession | None) -> None:
        self.session = session

    def session_at(self, moment: datetime) -> MarketSession | None:
        return self.session


class _Portfolio:
    def __init__(self) -> None:
        self.calls = 0

    async def get_account_snapshot(self, request: Any) -> Any:
        self.calls += 1
        assert request.providers == (VendorId.SCHWAB,)
        return SimpleNamespace(
            ok=True,
            data=SimpleNamespace(
                snapshots=(
                    SimpleNamespace(
                        provider=VendorId.SCHWAB,
                        snapshot_id="snapshot_a",
                        account_ref="schwab-account-a",
                    ),
                    SimpleNamespace(
                        provider=VendorId.SCHWAB,
                        snapshot_id="snapshot_b",
                        account_ref="schwab-account-b",
                    ),
                )
            ),
            warnings=(),
            errors=(),
        )


class _Preview:
    def __init__(self, value: BrokerOrderPreviewDTO) -> None:
        self.value = value
        self.calls = 0
        self.request: Any = None

    async def preview(self, request: Any) -> Any:
        self.calls += 1
        self.request = request
        return SimpleNamespace(ok=True, data=self.value, warnings=(), errors=())


class _Notifications:
    def __init__(self, *, existing: Any = None) -> None:
        self.existing = existing
        self.enqueued: list[dict[str, Any]] = []
        self.flush_calls = 0

    def system_entry_by_idempotency_key(self, key: str) -> Any:
        return self.existing

    async def enqueue_system_text(self, **kwargs: Any) -> Any:
        self.enqueued.append(kwargs)
        return SimpleNamespace(
            notification_id="notification_00000000-0000-7000-8000-000000000001",
            status=NotificationStatus.PENDING,
        )

    async def flush_pending(self) -> Any:
        self.flush_calls += 1
        return SimpleNamespace(
            disposition=NotificationFlushDisposition.ATTEMPTED,
            error_codes=(),
        )


def _preview_value() -> BrokerOrderPreviewDTO:
    quote = BrokerQuotePreviewDTO(
        instrument_id="etf:US:SGOV",
        symbol="SGOV",
        source="schwab",
        quote_at=datetime(2026, 8, 10, 19, 45, tzinfo=UTC),
        bid=Decimal("100.48"),
        ask=Decimal("100.49"),
        last=Decimal("100.48"),
        price_basis="ask",
        reference_limit_price=Decimal("100.49"),
        spread=Decimal("0.01"),
        age_seconds=2,
    )
    accounts = tuple(
        BrokerOrderPreviewAccountDTO(
            account_ref=f"schwab-account-{suffix}",
            snapshot_id=f"snapshot_{suffix}",
            snapshot_as_of=datetime(2026, 8, 10, 19, 44, 58, tzinfo=UTC),
            cash_balance=cash,
            hard_cash_floor=Decimal("2000"),
            operational_buffer=Decimal("200"),
            open_buy_order_reserve=Decimal(0),
            reserved_cash=Decimal("2200"),
            surplus_cash=cash - Decimal("2200"),
            minimum_order_notional=Decimal("1000"),
            quantity=quantity,
            estimated_notional=notional,
            projected_cash_after_all_open_buys=cash - notional,
            residual_above_reserve=cash - notional - Decimal("2200"),
            status="INDICATIVE",
            blocker_codes=(),
            schwab_order_payload={"orderType": "LIMIT"},
        )
        for suffix, cash, quantity, notional in (
            ("a", Decimal("7881.64"), 56, Decimal("5627.44")),
            ("b", Decimal("17087.44"), 148, Decimal("14872.52")),
        )
    )
    return BrokerOrderPreviewDTO(
        mode="cash_sweep_shadow",
        policy={"cash_source": "currentBalances.cashBalance"},
        quote=quote,
        accounts=accounts,
        total_quantity=204,
        total_estimated_notional=Decimal("20499.96"),
    )


def _service(
    *,
    now: datetime,
    session: MarketSession | None,
    existing: Any = None,
) -> tuple[SgovShadowPlanService, _Portfolio, _Preview, _Notifications]:
    portfolio = _Portfolio()
    preview = _Preview(_preview_value())
    notifications = _Notifications(existing=existing)
    service = SgovShadowPlanService(
        calendar=_Calendar(session),  # type: ignore[arg-type]
        portfolio=portfolio,  # type: ignore[arg-type]
        preview=preview,  # type: ignore[arg-type]
        notifications=notifications,  # type: ignore[arg-type]
        clock=_Clock(now),  # type: ignore[arg-type]
    )
    return service, portfolio, preview, notifications


async def test_due_run_refreshes_only_schwab_and_enqueues_one_shadow_plan() -> None:
    session = MarketSession(
        session_date=date(2026, 8, 10),
        close_at=datetime(2026, 8, 10, 20, 0, tzinfo=UTC),
    )
    service, portfolio, preview, notifications = _service(
        now=datetime(2026, 8, 10, 19, 45, 2, tzinfo=UTC),
        session=session,
    )

    result = await service.run_if_due()

    assert result.disposition is SgovShadowPlanDisposition.EXECUTED
    assert result.execution_effect is False
    assert result.preview is not None and result.preview.total_quantity == 204
    assert result.refreshed_snapshot_ids == ("snapshot_a", "snapshot_b")
    assert portfolio.calls == preview.calls == notifications.flush_calls == 1
    assert preview.request.account_refs == ("schwab-account-a", "schwab-account-b")
    assert preview.request.hard_cash_floor == Decimal("2000")
    assert notifications.enqueued[0]["idempotency_key"] == "sgov-shadow-plan:2026-08-10"
    assert notifications.enqueued[0]["expires_at"] == session.close_at
    assert "合计：204 股 / $20499.96" in notifications.enqueued[0]["body"]


async def test_due_selection_skips_provider_before_window_and_after_close() -> None:
    session = MarketSession(
        session_date=date(2026, 8, 10),
        close_at=datetime(2026, 8, 10, 20, 0, tzinfo=UTC),
    )
    before, portfolio, preview, _ = _service(
        now=datetime(2026, 8, 10, 19, 44, 59, tzinfo=UTC),
        session=session,
    )
    early = await before.run_if_due()
    assert early.disposition is SgovShadowPlanDisposition.SKIPPED_NOT_DUE
    assert portfolio.calls == preview.calls == 0

    after, portfolio, preview, _ = _service(
        now=datetime(2026, 8, 10, 20, 0, tzinfo=UTC),
        session=session,
    )
    late = await after.run_if_due()
    assert late.disposition is SgovShadowPlanDisposition.SKIPPED_WINDOW_CLOSED
    assert portfolio.calls == preview.calls == 0


async def test_existing_session_receipt_prevents_duplicate_provider_calls() -> None:
    session = MarketSession(
        session_date=date(2026, 8, 10),
        close_at=datetime(2026, 8, 10, 20, 0, tzinfo=UTC),
    )
    existing = SimpleNamespace(
        notification_id="notification_00000000-0000-7000-8000-000000000001",
        status=NotificationStatus.DELIVERED,
        created_at=datetime(2026, 8, 10, 19, 45, tzinfo=UTC),
    )
    service, portfolio, preview, _ = _service(
        now=datetime(2026, 8, 10, 19, 50, tzinfo=UTC),
        session=session,
        existing=existing,
    )

    result = await service.run_if_due()

    assert result.disposition is SgovShadowPlanDisposition.SKIPPED_ALREADY_COMPLETED
    assert portfolio.calls == preview.calls == 0


def test_early_close_runs_fifteen_minutes_before_session_close() -> None:
    session = MarketSession(
        session_date=date(2026, 11, 27),
        close_at=datetime(2026, 11, 27, 18, 0, tzinfo=UTC),
    )
    assert SgovShadowPlanService.scheduled_for(session) == datetime(
        2026, 11, 27, 17, 45, tzinfo=UTC
    )


async def test_immediate_preview_has_table_and_never_enqueues() -> None:
    service, _, _, notifications = _service(
        now=datetime(2026, 8, 9, 13, 0, tzinfo=UTC),
        session=None,
    )
    result = await service.preview_now()

    rendered = _render_table(result)
    assert "| Schwab 账户 | cashBalance |" in rendered
    assert "| schwab-account-a | $7,881.64 |" in rendered
    assert "合计：204 股 / $20,499.96" in rendered
    assert notifications.enqueued == []


def test_launchd_wakes_hourly_at_minute_45_without_codex() -> None:
    payload = _launchd_payload(
        project_root=Path("/project"),
        uv_path=Path("/usr/local/bin/uv"),
    )
    assert payload["Label"] == LABEL
    assert payload["StartCalendarInterval"] == {"Minute": 45}
    assert payload["ProgramArguments"][-3:] == [
        "trading-partner-sgov-plan",
        "run",
        "--json",
    ]
    assert "codex" not in " ".join(payload["ProgramArguments"]).lower()
