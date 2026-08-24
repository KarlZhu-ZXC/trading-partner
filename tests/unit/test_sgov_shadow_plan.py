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
    SgovBlockedStage,
    SgovShadowPlanDisposition,
    SgovShadowPlanPhase,
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
    def __init__(self, response: Any = None) -> None:
        self.calls = 0
        self.response = response

    async def get_account_snapshot(self, request: Any) -> Any:
        self.calls += 1
        assert request.providers == (VendorId.SCHWAB,)
        if self.response is not None:
            return self.response
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


class _BrokerOrders:
    def __init__(self) -> None:
        self.preview_requests: list[Any] = []
        self.submit_requests: list[dict[str, Any]] = []

    async def preview(self, request: Any) -> Any:
        self.preview_requests.append(request)
        suffix = request.account_ref.rsplit("-", 1)[-1]
        return SimpleNamespace(
            ok=True,
            data=SimpleNamespace(order_intent_id=f"broker_order_{suffix}", status="PREVIEWED"),
            errors=(),
        )

    async def submit_sgov_cash_sweep(self, **kwargs: Any) -> Any:
        self.submit_requests.append(kwargs)
        return SimpleNamespace(
            ok=True,
            data=SimpleNamespace(
                order_intent_id=kwargs["order_intent_id"],
                status="SUBMITTED",
                provider_status="SUBMITTED",
            ),
            errors=(),
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
            hard_cash_floor=Decimal("3000"),
            operational_buffer=Decimal("200"),
            open_buy_order_reserve=Decimal(0),
            reserved_cash=Decimal("3200"),
            surplus_cash=cash - Decimal("3200"),
            minimum_order_notional=Decimal("1000"),
            quantity=quantity,
            estimated_notional=notional,
            projected_cash_after_all_open_buys=cash - notional,
            residual_above_reserve=cash - notional - Decimal("3200"),
            status="INDICATIVE",
            blocker_codes=(),
            schwab_order_payload={"orderType": "LIMIT"},
        )
        for suffix, cash, quantity, notional in (
            ("a", Decimal("7881.64"), 46, Decimal("4622.54")),
            ("b", Decimal("17087.44"), 138, Decimal("13867.62")),
        )
    )
    return BrokerOrderPreviewDTO(
        mode="cash_sweep_shadow",
        policy={"cash_source": "currentBalances.cashBalance"},
        quote=quote,
        accounts=accounts,
        total_quantity=184,
        total_estimated_notional=Decimal("18490.16"),
    )


def _service(
    *,
    now: datetime,
    session: MarketSession | None,
    existing: Any = None,
    broker_orders: Any = None,
    preview_value: BrokerOrderPreviewDTO | None = None,
    portfolio_response: Any = None,
) -> tuple[SgovShadowPlanService, _Portfolio, _Preview, _Notifications]:
    portfolio = _Portfolio(portfolio_response)
    preview = _Preview(preview_value or _preview_value())
    notifications = _Notifications(existing=existing)
    service = SgovShadowPlanService(
        calendar=_Calendar(session),  # type: ignore[arg-type]
        portfolio=portfolio,  # type: ignore[arg-type]
        preview=preview,  # type: ignore[arg-type]
        broker_orders=broker_orders,
        notifications=notifications,  # type: ignore[arg-type]
        clock=_Clock(now),  # type: ignore[arg-type]
        read_retry_delay_seconds=0,
    )
    return service, portfolio, preview, notifications


async def test_account_refresh_failure_reports_exact_blocked_stage_and_safe_diagnostic() -> None:
    session = MarketSession(
        session_date=date(2026, 8, 13),
        close_at=datetime(2026, 8, 13, 20, 0, tzinfo=UTC),
    )
    failure = SimpleNamespace(
        code="PROVIDER_UNAVAILABLE_ERROR",
        retryable=True,
        details={
            "vendor": "schwab",
            "operation": "account_snapshot",
            "error_type": "http_failure",
            "status_code": 503,
            "status_class": "5xx",
        },
    )
    service, _, preview, notifications = _service(
        now=datetime(2026, 8, 13, 19, 55, 15, tzinfo=UTC),
        session=session,
        portfolio_response=SimpleNamespace(
            ok=False,
            data=None,
            warnings=(),
            errors=(failure,),
        ),
    )

    result = await service.run_if_due(auto_execute=True)

    assert result.blocked_stage is SgovBlockedStage.ACCOUNT_REFRESH
    assert result.provider_diagnostics[0].status_code == 503
    assert preview.calls == 0
    body = notifications.enqueued[0]["body"]
    assert "阶段：Schwab 账户刷新" in body
    assert "诊断：PROVIDER_UNAVAILABLE_ERROR/schwab" in body
    assert "操作：account_snapshot" not in body
    assert "自动买入：未提交任何订单" not in body


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
    assert result.phase is SgovShadowPlanPhase.PASSIVE_BID
    assert result.execution_effect is False
    assert result.preview is not None and result.preview.total_quantity == 184
    assert result.refreshed_snapshot_ids == ("snapshot_a", "snapshot_b")
    assert portfolio.calls == preview.calls == notifications.flush_calls == 1
    assert preview.request.account_refs == ("schwab-account-a", "schwab-account-b")
    assert preview.request.hard_cash_floor == Decimal("3000")
    assert notifications.enqueued[0]["idempotency_key"] == (
        "sgov-shadow-plan:2026-08-10:passive_bid"
    )
    assert notifications.enqueued[0]["expires_at"] == session.close_at
    assert "合计：184 股 / $18490.16" in notifications.enqueued[0]["body"]
    assert "先用当前 bid $100.48" in notifications.enqueued[0]["body"]
    assert "等待至：15:55 ET" in notifications.enqueued[0]["body"]


async def test_completion_phase_refreshes_again_and_uses_current_ask_guidance() -> None:
    session = MarketSession(
        session_date=date(2026, 8, 10),
        close_at=datetime(2026, 8, 10, 20, 0, tzinfo=UTC),
    )
    service, portfolio, preview, notifications = _service(
        now=datetime(2026, 8, 10, 19, 55, 2, tzinfo=UTC),
        session=session,
    )

    result = await service.run_if_due()

    assert result.phase is SgovShadowPlanPhase.COMPLETION
    assert portfolio.calls == preview.calls == notifications.flush_calls == 1
    assert notifications.enqueued[0]["idempotency_key"] == (
        "sgov-shadow-plan:2026-08-10:completion"
    )
    body = notifications.enqueued[0]["body"]
    assert "用当前 ask $100.49 作为 BUY LIMIT" in body
    assert "不使用旧 bid+0.01" in body


async def test_auto_passive_phase_persists_without_sending_a_telegram_message() -> None:
    session = MarketSession(
        session_date=date(2026, 8, 10),
        close_at=datetime(2026, 8, 10, 20, 0, tzinfo=UTC),
    )
    service, _, _, notifications = _service(
        now=datetime(2026, 8, 10, 19, 45, 2, tzinfo=UTC),
        session=session,
    )

    result = await service.run_if_due(auto_execute=True)

    assert result.phase is SgovShadowPlanPhase.PASSIVE_BID
    assert result.automation_enabled is True
    assert notifications.enqueued == []
    assert notifications.flush_calls == 0


async def test_auto_two_phase_run_emits_only_one_final_notification() -> None:
    session = MarketSession(
        session_date=date(2026, 8, 10),
        close_at=datetime(2026, 8, 10, 20, 0, tzinfo=UTC),
    )
    broker_orders = _BrokerOrders()
    service, _, _, notifications = _service(
        now=datetime(2026, 8, 10, 19, 45, 2, tzinfo=UTC),
        session=session,
        broker_orders=broker_orders,
    )

    preparation = await service.run_if_due(auto_execute=True)
    service._clock.value = datetime(2026, 8, 10, 19, 55, 2, tzinfo=UTC)  # type: ignore[attr-defined]
    completion = await service.run_if_due(auto_execute=True)

    assert preparation.notification_id is None
    assert completion.notification_id is not None
    assert len(notifications.enqueued) == 1
    assert notifications.enqueued[0]["idempotency_key"] == (
        "sgov-auto-plan:2026-08-10:completion"
    )
    assert "状态：SUBMITTED" in notifications.enqueued[0]["body"]


async def test_auto_completion_submits_only_exact_sgov_limits_and_reports_results() -> None:
    session = MarketSession(
        session_date=date(2026, 8, 10),
        close_at=datetime(2026, 8, 10, 20, 0, tzinfo=UTC),
    )
    broker_orders = _BrokerOrders()
    service, _, _, notifications = _service(
        now=datetime(2026, 8, 10, 19, 55, 2, tzinfo=UTC),
        session=session,
        broker_orders=broker_orders,
    )

    result = await service.run_if_due(auto_execute=True)

    assert result.automation_enabled is True
    assert result.execution_due is True
    assert result.execution_effect is True
    assert result.shadow_only is False
    assert [item.outcome.value for item in result.orders] == ["SUBMITTED", "SUBMITTED"]
    assert len(broker_orders.preview_requests) == len(broker_orders.submit_requests) == 2
    assert all(item.instrument_id == "etf:US:SGOV" for item in broker_orders.preview_requests)
    assert all(item.instruction.value == "BUY" for item in broker_orders.preview_requests)
    assert all(item.order_type.value == "LIMIT" for item in broker_orders.preview_requests)
    assert all(item.session.value == "NORMAL" for item in broker_orders.preview_requests)
    assert all(item.limit_price == Decimal("100.49") for item in broker_orders.preview_requests)
    assert all(
        item["minimum_cash_reserve"] == Decimal("3200")
        for item in broker_orders.submit_requests
    )
    notification = notifications.enqueued[0]
    assert notification["idempotency_key"] == "sgov-auto-plan:2026-08-10:completion"
    assert notification["expires_at"] == session.close_at.replace(day=11)
    assert "SGOV · 自动买入结果 · 2026-08-10" in notification["body"]
    assert "状态：SUBMITTED" in notification["body"]
    assert "schwab-account-a：SUBMITTED · 46 股 @ $100.49" in notification["body"]
    assert "SGOV bid / ask" not in notification["body"]
    assert "仅 SGOV BUY LIMIT" not in notification["body"]


async def test_auto_completion_fails_closed_on_stale_quote() -> None:
    session = MarketSession(
        session_date=date(2026, 8, 10),
        close_at=datetime(2026, 8, 10, 20, 0, tzinfo=UTC),
    )
    value = _preview_value()
    stale = value.model_copy(
        update={
            "quote": value.quote.model_copy(update={"age_seconds": 31}),
            "accounts": tuple(
                item.model_copy(update={"blocker_codes": ("BROKER_QUOTE_STALE",)})
                for item in value.accounts
            ),
        }
    )
    broker_orders = _BrokerOrders()
    service, _, _, notifications = _service(
        now=datetime(2026, 8, 10, 19, 55, 2, tzinfo=UTC),
        session=session,
        broker_orders=broker_orders,
        preview_value=stale,
    )

    result = await service.run_if_due(auto_execute=True)

    assert result.execution_effect is False
    assert [item.outcome.value for item in result.orders] == ["SKIPPED", "SKIPPED"]
    assert broker_orders.preview_requests == broker_orders.submit_requests == []
    assert "BROKER_QUOTE_STALE" in notifications.enqueued[0]["body"]


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
    assert SgovShadowPlanService.completion_check_at(session) == datetime(
        2026, 11, 27, 17, 55, tzinfo=UTC
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
    assert "合计：184 股 / $18,490.16" in rendered
    assert notifications.enqueued == []


def test_launchd_wakes_hourly_at_minutes_45_and_55_without_codex() -> None:
    payload = _launchd_payload(
        project_root=Path("/project"),
        uv_path=Path("/usr/local/bin/uv"),
    )
    assert payload["Label"] == LABEL
    assert payload["StartCalendarInterval"] == [{"Minute": 45}, {"Minute": 55}]
    assert payload["ProgramArguments"][-3:] == [
        "trading-partner-sgov-plan",
        "auto-run",
        "--json",
    ]
    assert "codex" not in " ".join(payload["ProgramArguments"]).lower()
