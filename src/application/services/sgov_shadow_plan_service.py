"""Scheduled SGOV Shadow plans built from fresh Schwab facts; never place orders."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from application.dto.broker_execution import (
    BrokerOrderPreviewInput,
    SgovShadowPlanDisposition,
    SgovShadowPlanDTO,
)
from application.dto.portfolio import AccountGetSnapshotInput
from application.ports.clock import Clock
from application.ports.market_session_calendar import MarketSession, MarketSessionCalendar
from application.services.broker_order_service import BrokerOrderService
from application.services.cash_sweep_shadow_service import CashSweepShadowService
from application.services.notification_service import NotificationService
from application.services.portfolio_tool_coordinator import PortfolioToolCoordinator
from domain.common.enums import VendorId

_NEW_YORK = ZoneInfo("America/New_York")
_NORMAL_RUN_TIME = time(hour=15, minute=45)
_EARLY_CLOSE_LEAD = timedelta(minutes=15)


class SgovShadowPlanService:
    """Refresh Schwab, calculate a two-or-more-account plan, and notify once per session."""

    def __init__(
        self,
        *,
        calendar: MarketSessionCalendar,
        portfolio: PortfolioToolCoordinator,
        preview: CashSweepShadowService,
        broker_orders: BrokerOrderService | None = None,
        notifications: NotificationService,
        clock: Clock,
        hard_cash_floor: Decimal = Decimal("2000"),
        operational_buffer: Decimal = Decimal("200"),
        minimum_order_notional: Decimal = Decimal("1000"),
        max_quote_age_seconds: int = 30,
        max_spread: Decimal = Decimal("0.02"),
    ) -> None:
        self._calendar = calendar
        self._portfolio = portfolio
        self._preview = preview
        self._broker_orders = broker_orders
        self._notifications = notifications
        self._clock = clock
        self._hard_cash_floor = hard_cash_floor
        self._operational_buffer = operational_buffer
        self._minimum_order_notional = minimum_order_notional
        self._max_quote_age_seconds = max_quote_age_seconds
        self._max_spread = max_spread

    async def run_if_due(self) -> SgovShadowPlanDTO:
        """Run once inside the session's final 15 minutes, without catch-up after close."""

        now = self._clock.now()
        session = self._calendar.session_at(now)
        if session is None:
            return SgovShadowPlanDTO(disposition=SgovShadowPlanDisposition.SKIPPED_NON_TRADING_DAY)
        scheduled_for = self.scheduled_for(session)
        key = self._idempotency_key(session)
        existing = self._notifications.system_entry_by_idempotency_key(key)
        if existing is not None:
            return SgovShadowPlanDTO(
                disposition=SgovShadowPlanDisposition.SKIPPED_ALREADY_COMPLETED,
                market_session_date=session.session_date,
                scheduled_for=scheduled_for,
                generated_at=existing.created_at,
                notification_id=existing.notification_id,
                notification_status=existing.status.value,
            )
        if now < scheduled_for:
            return SgovShadowPlanDTO(
                disposition=SgovShadowPlanDisposition.SKIPPED_NOT_DUE,
                market_session_date=session.session_date,
                scheduled_for=scheduled_for,
            )
        if now >= session.close_at:
            return SgovShadowPlanDTO(
                disposition=SgovShadowPlanDisposition.SKIPPED_WINDOW_CLOSED,
                market_session_date=session.session_date,
                scheduled_for=scheduled_for,
                warning_codes=("SGOV_SHADOW_PLAN_WINDOW_CLOSED",),
            )
        result = await self._calculate(
            disposition=SgovShadowPlanDisposition.EXECUTED,
            market_session_date=session.session_date,
            scheduled_for=scheduled_for,
        )
        return await self._enqueue(result, session=session, key=key)

    async def preview_now(self) -> SgovShadowPlanDTO:
        """Refresh and calculate immediately without scheduling or notification writes."""

        return await self._calculate(
            disposition=SgovShadowPlanDisposition.EXECUTED,
            market_session_date=None,
            scheduled_for=None,
        )

    @staticmethod
    def scheduled_for(session: MarketSession) -> datetime:
        fixed = datetime.combine(
            session.session_date,
            _NORMAL_RUN_TIME,
            tzinfo=_NEW_YORK,
        ).astimezone(session.close_at.tzinfo)
        return min(fixed, session.close_at - _EARLY_CLOSE_LEAD)

    async def _calculate(
        self,
        *,
        disposition: SgovShadowPlanDisposition,
        market_session_date: date | None,
        scheduled_for: datetime | None,
    ) -> SgovShadowPlanDTO:
        generated_at = self._clock.now()
        refreshed = await self._portfolio.get_account_snapshot(
            AccountGetSnapshotInput(providers=(VendorId.SCHWAB,))
        )
        warning_codes = [item.code for item in refreshed.warnings]
        error_codes = [item.code for item in refreshed.errors]
        if not refreshed.ok or refreshed.data is None:
            return SgovShadowPlanDTO(
                disposition=disposition,
                market_session_date=market_session_date,
                scheduled_for=scheduled_for,
                generated_at=generated_at,
                account_refresh_ok=False,
                warning_codes=tuple(dict.fromkeys(warning_codes)),
                error_codes=tuple(dict.fromkeys(error_codes or ["SCHWAB_ACCOUNT_REFRESH_FAILED"])),
            )

        schwab_snapshots = tuple(
            item for item in refreshed.data.snapshots if item.provider is VendorId.SCHWAB
        )
        snapshot_ids = tuple(item.snapshot_id for item in schwab_snapshots)
        account_refs = tuple(item.account_ref for item in schwab_snapshots)
        if not account_refs:
            return SgovShadowPlanDTO(
                disposition=disposition,
                market_session_date=market_session_date,
                scheduled_for=scheduled_for,
                generated_at=generated_at,
                account_refresh_ok=False,
                refreshed_snapshot_ids=snapshot_ids,
                warning_codes=tuple(dict.fromkeys(warning_codes)),
                error_codes=("SCHWAB_ACCOUNT_REFRESH_EMPTY",),
            )

        preview = await self._preview.preview(
            BrokerOrderPreviewInput(
                account_refs=account_refs,
                hard_cash_floor=self._hard_cash_floor,
                operational_buffer=self._operational_buffer,
                minimum_order_notional=self._minimum_order_notional,
                max_quote_age_seconds=self._max_quote_age_seconds,
                max_spread=self._max_spread,
            )
        )
        warning_codes.extend(item.code for item in preview.warnings)
        error_codes.extend(item.code for item in preview.errors)
        return SgovShadowPlanDTO(
            disposition=disposition,
            market_session_date=market_session_date,
            scheduled_for=scheduled_for,
            generated_at=generated_at,
            account_refresh_ok=True,
            refreshed_snapshot_ids=snapshot_ids,
            preview=preview.data if preview.ok else None,
            warning_codes=tuple(dict.fromkeys(warning_codes)),
            error_codes=tuple(dict.fromkeys(error_codes)),
        )

    async def _enqueue(
        self,
        result: SgovShadowPlanDTO,
        *,
        session: MarketSession,
        key: str,
    ) -> SgovShadowPlanDTO:
        title = f"💵 SGOV Shadow 购买计划 · {session.session_date.isoformat()}"
        entry = await self._notifications.enqueue_system_text(
            source_id=key,
            title=title,
            body=self.notification_body(result),
            idempotency_key=key,
            expires_at=session.close_at,
        )
        flush = await self._notifications.flush_pending()
        return result.model_copy(
            update={
                "notification_id": entry.notification_id,
                "notification_status": entry.status.value,
                "notification_flush_disposition": flush.disposition.value,
                "warning_codes": tuple(dict.fromkeys(result.warning_codes + flush.error_codes)),
            }
        )

    @staticmethod
    def notification_body(result: SgovShadowPlanDTO) -> str:
        generated = (
            result.generated_at.astimezone(_NEW_YORK).strftime("%Y-%m-%d %H:%M:%S ET")
            if result.generated_at is not None
            else "不可用"
        )
        lines = [f"生成时间：{generated}"]
        preview = result.preview
        if preview is None:
            lines.extend(
                (
                    "状态：BLOCKED（未生成购买数量）",
                    "错误：" + ", ".join(result.error_codes or ("UNKNOWN",)),
                    "仅为 Shadow 计划；未下单。",
                )
            )
            return "\n".join(lines)

        lines.extend(
            (
                f"SGOV 限价参考：${preview.quote.reference_limit_price}",
                f"报价时间：{preview.quote.quote_at.astimezone(_NEW_YORK).isoformat()}",
                f"报价来源：{preview.quote.source} / {preview.quote.price_basis}",
                "订单口径：BUY · LIMIT · DAY · NORMAL · 整股",
            )
        )
        for index, account in enumerate(preview.accounts, start=1):
            blockers = ", ".join(account.blocker_codes) if account.blocker_codes else "无"
            lines.extend(
                (
                    "",
                    f"账户 {index} · {account.account_ref}",
                    f"cashBalance：${account.cash_balance}",
                    "保留现金："
                    f"${account.hard_cash_floor} + ${account.operational_buffer}"
                    f" + 未成交 BUY ${account.open_buy_order_reserve}",
                    f"Shadow 买入：{account.quantity} 股 / ${account.estimated_notional}",
                    f"预计剩余现金：${account.projected_cash_after_all_open_buys}",
                    f"状态：{account.status}；限制：{blockers}",
                )
            )
        lines.extend(
            (
                "",
                f"合计：{preview.total_quantity} 股 / ${preview.total_estimated_notional}",
                "仅为 Shadow 购买计划；未提交、修改或撤销任何订单。",
            )
        )
        return "\n".join(lines)

    @staticmethod
    def _idempotency_key(session: MarketSession) -> str:
        return f"sgov-shadow-plan:{session.session_date.isoformat()}"
