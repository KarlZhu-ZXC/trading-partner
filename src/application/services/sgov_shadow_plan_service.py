"""Scheduled SGOV cash planning with one closed automatic-buy exception."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from application.dto.broker_execution import (
    BrokerOrderIntentPreviewInput,
    BrokerOrderPreviewInput,
    SgovAutoOrderOutcome,
    SgovAutoOrderReceiptDTO,
    SgovBlockedStage,
    SgovProviderDiagnosticDTO,
    SgovShadowPlanDisposition,
    SgovShadowPlanDTO,
    SgovShadowPlanPhase,
)
from application.dto.portfolio import AccountGetSnapshotInput
from application.ports.clock import Clock
from application.ports.market_session_calendar import MarketSession, MarketSessionCalendar
from application.services.broker_order_service import BrokerOrderService
from application.services.cash_sweep_shadow_service import CashSweepShadowService
from application.services.notification_service import NotificationService
from application.services.portfolio_tool_coordinator import PortfolioToolCoordinator
from domain.common.enums import VendorId
from domain.execution.models import (
    BrokerOrderDuration,
    BrokerOrderInstruction,
    BrokerOrderSession,
    BrokerOrderType,
)

_NEW_YORK = ZoneInfo("America/New_York")
_NORMAL_RUN_TIME = time(hour=15, minute=45)
_EARLY_CLOSE_LEAD = timedelta(minutes=15)
_COMPLETION_LEAD = timedelta(minutes=5)


class SgovShadowPlanService:
    """Refresh Schwab, plan twice, and optionally buy SGOV at completion."""

    def __init__(
        self,
        *,
        calendar: MarketSessionCalendar,
        portfolio: PortfolioToolCoordinator,
        preview: CashSweepShadowService,
        broker_orders: BrokerOrderService | None = None,
        notifications: NotificationService,
        clock: Clock,
        hard_cash_floor: Decimal = Decimal("3000"),
        operational_buffer: Decimal = Decimal("200"),
        minimum_order_notional: Decimal = Decimal("1000"),
        max_quote_age_seconds: int = 30,
        max_spread: Decimal = Decimal("0.02"),
        read_retry_attempts: int = 3,
        read_retry_delay_seconds: float = 1.0,
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
        self._read_retry_attempts = max(1, min(read_retry_attempts, 3))
        self._read_retry_delay_seconds = max(0.0, read_retry_delay_seconds)

    async def run_if_due(self, *, auto_execute: bool = False) -> SgovShadowPlanDTO:
        """Run one due phase; automatic execution is completion-phase SGOV only."""

        now = self._clock.now()
        session = self._calendar.session_at(now)
        if session is None:
            return SgovShadowPlanDTO(disposition=SgovShadowPlanDisposition.SKIPPED_NON_TRADING_DAY)
        scheduled_for = self.scheduled_for(session)
        completion_check_at = self.completion_check_at(session)
        phase = (
            SgovShadowPlanPhase.COMPLETION
            if now >= completion_check_at
            else SgovShadowPlanPhase.PASSIVE_BID
        )
        key = self._idempotency_key(session, phase, auto_execute=auto_execute)
        existing = self._notifications.system_entry_by_idempotency_key(key)
        if existing is not None:
            return SgovShadowPlanDTO(
                disposition=SgovShadowPlanDisposition.SKIPPED_ALREADY_COMPLETED,
                phase=phase,
                market_session_date=session.session_date,
                scheduled_for=scheduled_for,
                completion_check_at=completion_check_at,
                generated_at=existing.created_at,
                notification_id=existing.notification_id,
                notification_status=existing.status.value,
                automation_enabled=auto_execute,
                execution_due=auto_execute and phase is SgovShadowPlanPhase.COMPLETION,
            )
        if now < scheduled_for:
            return SgovShadowPlanDTO(
                disposition=SgovShadowPlanDisposition.SKIPPED_NOT_DUE,
                phase=SgovShadowPlanPhase.PASSIVE_BID,
                market_session_date=session.session_date,
                scheduled_for=scheduled_for,
                completion_check_at=completion_check_at,
                automation_enabled=auto_execute,
            )
        if now >= session.close_at:
            closed_result = SgovShadowPlanDTO(
                disposition=SgovShadowPlanDisposition.SKIPPED_WINDOW_CLOSED,
                phase=SgovShadowPlanPhase.COMPLETION,
                market_session_date=session.session_date,
                scheduled_for=scheduled_for,
                completion_check_at=completion_check_at,
                warning_codes=("SGOV_SHADOW_PLAN_WINDOW_CLOSED",),
                automation_enabled=auto_execute,
                execution_due=auto_execute,
            )
            return (
                await self._enqueue(
                    closed_result,
                    session=session,
                    key=self._idempotency_key(
                        session,
                        SgovShadowPlanPhase.COMPLETION,
                        auto_execute=auto_execute,
                    ),
                )
                if auto_execute
                else closed_result
            )
        result = await self._calculate(
            disposition=SgovShadowPlanDisposition.EXECUTED,
            phase=phase,
            market_session_date=session.session_date,
            scheduled_for=scheduled_for,
            completion_check_at=completion_check_at,
            automation_enabled=auto_execute,
        )
        if auto_execute and phase is SgovShadowPlanPhase.COMPLETION:
            result = await self._execute_completion(result, session=session)
        if auto_execute and phase is SgovShadowPlanPhase.PASSIVE_BID:
            # Preparation remains durable and idempotent, but the user receives
            # one notification only after the completion decision is made.
            return result
        return await self._enqueue(result, session=session, key=key)

    async def preview_now(self) -> SgovShadowPlanDTO:
        """Refresh and calculate immediately without scheduling or notification writes."""

        return await self._calculate(
            disposition=SgovShadowPlanDisposition.EXECUTED,
            phase=None,
            market_session_date=None,
            scheduled_for=None,
            completion_check_at=None,
            automation_enabled=False,
        )

    @staticmethod
    def scheduled_for(session: MarketSession) -> datetime:
        fixed = datetime.combine(
            session.session_date,
            _NORMAL_RUN_TIME,
            tzinfo=_NEW_YORK,
        ).astimezone(session.close_at.tzinfo)
        return min(fixed, session.close_at - _EARLY_CLOSE_LEAD)

    @staticmethod
    def completion_check_at(session: MarketSession) -> datetime:
        return session.close_at - _COMPLETION_LEAD

    async def _calculate(
        self,
        *,
        disposition: SgovShadowPlanDisposition,
        phase: SgovShadowPlanPhase | None,
        market_session_date: date | None,
        scheduled_for: datetime | None,
        completion_check_at: datetime | None,
        automation_enabled: bool,
    ) -> SgovShadowPlanDTO:
        generated_at = self._clock.now()
        refresh_attempt = 0
        while True:
            refresh_attempt += 1
            refreshed = await self._portfolio.get_account_snapshot(
                AccountGetSnapshotInput(providers=(VendorId.SCHWAB,))
            )
            if (
                refreshed.ok
                or refresh_attempt >= self._read_retry_attempts
                or not _has_retryable_error(refreshed.errors)
            ):
                break
            await asyncio.sleep(self._read_retry_delay_seconds * refresh_attempt)
        warning_codes = [item.code for item in refreshed.warnings]
        if refresh_attempt > 1:
            warning_codes.append("SGOV_ACCOUNT_REFRESH_RETRIED")
        error_codes = [item.code for item in refreshed.errors]
        if not refreshed.ok or refreshed.data is None:
            return SgovShadowPlanDTO(
                disposition=disposition,
                phase=phase,
                market_session_date=market_session_date,
                scheduled_for=scheduled_for,
                completion_check_at=completion_check_at,
                generated_at=generated_at,
                account_refresh_ok=False,
                automation_enabled=automation_enabled,
                blocked_stage=SgovBlockedStage.ACCOUNT_REFRESH,
                provider_diagnostics=_provider_diagnostics(
                    refreshed.errors,
                    stage=SgovBlockedStage.ACCOUNT_REFRESH,
                    default_provider="schwab",
                    attempt_count=refresh_attempt,
                ),
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
                phase=phase,
                market_session_date=market_session_date,
                scheduled_for=scheduled_for,
                completion_check_at=completion_check_at,
                generated_at=generated_at,
                account_refresh_ok=False,
                automation_enabled=automation_enabled,
                blocked_stage=SgovBlockedStage.ACCOUNT_REFRESH,
                refreshed_snapshot_ids=snapshot_ids,
                warning_codes=tuple(dict.fromkeys(warning_codes)),
                error_codes=("SCHWAB_ACCOUNT_REFRESH_EMPTY",),
            )

        preview_attempt = 0
        while True:
            preview_attempt += 1
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
            if (
                preview.ok
                or preview_attempt >= self._read_retry_attempts
                or not _has_retryable_error(preview.errors)
            ):
                break
            await asyncio.sleep(self._read_retry_delay_seconds * preview_attempt)
        warning_codes.extend(item.code for item in preview.warnings)
        if preview_attempt > 1:
            warning_codes.append("SGOV_QUOTE_AND_SIZING_RETRIED")
        error_codes.extend(item.code for item in preview.errors)
        return SgovShadowPlanDTO(
            disposition=disposition,
            phase=phase,
            market_session_date=market_session_date,
            scheduled_for=scheduled_for,
            completion_check_at=completion_check_at,
            generated_at=generated_at,
            account_refresh_ok=True,
            automation_enabled=automation_enabled,
            refreshed_snapshot_ids=snapshot_ids,
            preview=preview.data if preview.ok else None,
            blocked_stage=(
                None if preview.ok else SgovBlockedStage.QUOTE_AND_SIZING
            ),
            provider_diagnostics=(
                ()
                if preview.ok
                else _provider_diagnostics(
                    preview.errors,
                    stage=SgovBlockedStage.QUOTE_AND_SIZING,
                    default_provider="schwab",
                    attempt_count=preview_attempt,
                )
            ),
            warning_codes=tuple(dict.fromkeys(warning_codes)),
            error_codes=tuple(dict.fromkeys(error_codes)),
        )

    async def _execute_completion(
        self,
        result: SgovShadowPlanDTO,
        *,
        session: MarketSession,
    ) -> SgovShadowPlanDTO:
        """Submit at most one exact SGOV BUY per eligible Schwab account."""

        if self._broker_orders is None:
            return result.model_copy(
                update={
                    "execution_due": True,
                    "shadow_only": False,
                    "error_codes": tuple(
                        dict.fromkeys(
                            result.error_codes + ("SGOV_AUTO_EXECUTION_NOT_CONFIGURED",)
                        )
                    ),
                }
            )
        preview = result.preview
        if preview is None:
            return result.model_copy(update={"execution_due": True, "shadow_only": False})

        quote = preview.quote
        quote_usable = (
            quote.instrument_id == "etf:US:SGOV"
            and quote.symbol.upper() == "SGOV"
            and quote.bid is not None
            and quote.ask is not None
            and quote.age_seconds <= self._max_quote_age_seconds
            and quote.spread is not None
            and Decimal(0) <= quote.spread <= self._max_spread
        )
        receipts: list[SgovAutoOrderReceiptDTO] = []
        reserve = self._hard_cash_floor + self._operational_buffer
        for account in preview.accounts:
            if (
                not quote_usable
                or account.status != "INDICATIVE"
                or account.quantity <= 0
                or account.blocker_codes
            ):
                receipts.append(
                    SgovAutoOrderReceiptDTO(
                        account_ref=account.account_ref,
                        outcome=SgovAutoOrderOutcome.SKIPPED,
                        quantity=account.quantity,
                        limit_price=quote.ask,
                        error_codes=(
                            tuple(account.blocker_codes)
                            or (("SGOV_AUTO_QUOTE_NOT_EXECUTABLE",) if not quote_usable else ())
                            or ("SGOV_AUTO_BELOW_THRESHOLD",)
                        ),
                    )
                )
                continue

            account_key = hashlib.sha256(account.account_ref.encode()).hexdigest()[:16]
            key_prefix = f"sgov-auto:{session.session_date.isoformat()}:{account_key}"
            order_preview = await self._broker_orders.preview(
                BrokerOrderIntentPreviewInput(
                    account_ref=account.account_ref,
                    instrument_id="etf:US:SGOV",
                    instruction=BrokerOrderInstruction.BUY,
                    quantity=account.quantity,
                    order_type=BrokerOrderType.LIMIT,
                    session=BrokerOrderSession.NORMAL,
                    duration=BrokerOrderDuration.DAY,
                    limit_price=quote.ask,
                    idempotency_key=f"{key_prefix}:preview",
                    preview_ttl_seconds=120,
                )
            )
            if not order_preview.ok or order_preview.data is None:
                receipts.append(
                    SgovAutoOrderReceiptDTO(
                        account_ref=account.account_ref,
                        outcome=SgovAutoOrderOutcome.FAILED,
                        quantity=account.quantity,
                        limit_price=quote.ask,
                        error_codes=tuple(item.code for item in order_preview.errors),
                    )
                )
                continue

            submitted = await self._broker_orders.submit_sgov_cash_sweep(
                order_intent_id=order_preview.data.order_intent_id,
                idempotency_key=f"{key_prefix}:submit",
                minimum_cash_reserve=reserve,
                max_quote_age_seconds=self._max_quote_age_seconds,
                max_spread=self._max_spread,
            )
            durable = submitted.data
            status = durable.status if durable is not None else None
            if status == "SUBMITTED":
                outcome = SgovAutoOrderOutcome.SUBMITTED
            elif status in {"SUBMITTING", "UNKNOWN"}:
                outcome = SgovAutoOrderOutcome.RECONCILIATION_REQUIRED
            else:
                outcome = SgovAutoOrderOutcome.FAILED
            receipts.append(
                SgovAutoOrderReceiptDTO(
                    account_ref=account.account_ref,
                    outcome=outcome,
                    quantity=account.quantity,
                    limit_price=quote.ask,
                    order_intent_id=(durable.order_intent_id if durable is not None else None),
                    durable_status=status,
                    provider_status=(durable.provider_status if durable is not None else None),
                    error_codes=tuple(item.code for item in submitted.errors),
                    execution_effect=(
                        status in {"SUBMITTED", "SUBMITTING", "UNKNOWN"}
                    ),
                )
            )

        execution_effect = any(item.execution_effect for item in receipts)
        return result.model_copy(
            update={
                "execution_due": True,
                "orders": tuple(receipts),
                "execution_effect": execution_effect,
                "shadow_only": False,
            }
        )

    async def _enqueue(
        self,
        result: SgovShadowPlanDTO,
        *,
        session: MarketSession,
        key: str,
    ) -> SgovShadowPlanDTO:
        if result.automation_enabled:
            phase_label = (
                "自动买入结果"
                if result.phase is SgovShadowPlanPhase.COMPLETION
                else "自动买入准备"
            )
            title = f"💵 SGOV · {phase_label} · {session.session_date.isoformat()}"
        else:
            phase_label = (
                "收盘前完成复核"
                if result.phase is SgovShadowPlanPhase.COMPLETION
                else "先挂 bid"
            )
            title = f"💵 SGOV Shadow · {phase_label} · {session.session_date.isoformat()}"
        expires_at = (
            session.close_at + timedelta(hours=24)
            if result.automation_enabled
            and result.phase is SgovShadowPlanPhase.COMPLETION
            else session.close_at
        )
        entry = await self._notifications.enqueue_system_text(
            source_id=key,
            title=title,
            body=self.notification_body(result),
            idempotency_key=key,
            expires_at=expires_at,
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
        if result.automation_enabled:
            return SgovShadowPlanService._compact_automation_notification_body(result)
        generated = (
            result.generated_at.astimezone(_NEW_YORK).strftime("%Y-%m-%d %H:%M:%S ET")
            if result.generated_at is not None
            else "不可用"
        )
        lines = [f"生成时间：{generated}"]
        preview = result.preview
        if preview is None:
            action = (
                "自动买入：未提交任何订单。"
                if result.automation_enabled
                else "仅为 Shadow 计划；未下单。"
            )
            lines.extend(
                (
                    "状态：BLOCKED（未生成购买数量）",
                    "阻断步骤：" + _blocked_stage_label(result.blocked_stage),
                    "错误：" + ", ".join(result.error_codes or ("UNKNOWN",)),
                    *_diagnostic_lines(result.provider_diagnostics),
                    "后续步骤：未读取可执行报价、未计算数量、未创建订单意图。"
                    if result.blocked_stage is SgovBlockedStage.ACCOUNT_REFRESH
                    else "后续步骤：未创建或提交任何订单。",
                    action,
                )
            )
            return "\n".join(lines)

        lines.extend(
            (
                f"SGOV bid / ask：${preview.quote.bid} / ${preview.quote.ask}",
                f"价差：${preview.quote.spread}",
                f"报价时间：{preview.quote.quote_at.astimezone(_NEW_YORK).isoformat()}",
                f"报价来源：{preview.quote.source}",
                "数量口径：按当前 ask 保守计算 · 整股",
            )
        )
        quote_blockers = {
            "BROKER_QUOTE_STALE",
            "BROKER_QUOTE_SPREAD_TOO_WIDE",
        }.intersection(result.warning_codes)
        if quote_blockers or preview.quote.bid is None or preview.quote.ask is None:
            reason = ", ".join(sorted(quote_blockers)) or "BID_ASK_UNAVAILABLE"
            lines.extend(
                (
                    f"执行建议：BLOCKED（{reason}）",
                    "不建议根据该报价挂 bid 或追 ask。",
                )
            )
        elif result.phase is SgovShadowPlanPhase.COMPLETION:
            lines.extend(
                (
                    f"完成阶段：若仍未成交且仍需今日部署，"
                    f"用当前 ask ${preview.quote.ask} 作为 BUY LIMIT。",
                    "不使用旧 bid+0.01；必须以本次刷新后的 ask 为准。",
                )
            )
        else:
            deadline = (
                result.completion_check_at.astimezone(_NEW_YORK).strftime("%H:%M ET")
                if result.completion_check_at is not None
                else "收盘前5分钟"
            )
            lines.extend(
                (
                    f"被动阶段：先用当前 bid ${preview.quote.bid} 挂 BUY LIMIT · DAY · NORMAL。",
                    f"等待至：{deadline}；未成交则等待第二次完成复核通知。",
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
                f"合计：{preview.total_quantity} 股 / "
                f"${preview.total_estimated_notional}",
            )
        )
        lines.append("仅为 Shadow 购买计划；未提交、修改或撤销任何订单。")
        return "\n".join(lines)

    @staticmethod
    def _compact_automation_notification_body(result: SgovShadowPlanDTO) -> str:
        """Render the one final SGOV automation card sent to Telegram."""

        date_label = (
            result.market_session_date.isoformat()
            if result.market_session_date is not None
            else "—"
        )
        if result.preview is None:
            lines = [
                f"SGOV · 自动买入结果 · {date_label}",
                "状态：BLOCKED",
                f"阶段：{_blocked_stage_label(result.blocked_stage)}",
                f"错误：{', '.join(result.error_codes or ('UNKNOWN',))}",
            ]
            if result.provider_diagnostics:
                lines.append(
                    "诊断："
                    + ", ".join(
                        f"{item.error_code}/{item.provider}"
                        for item in result.provider_diagnostics
                    )
                )
            return "\n".join(lines)

        submitted = sum(
            item.outcome is SgovAutoOrderOutcome.SUBMITTED for item in result.orders
        )
        reconciling = sum(
            item.outcome is SgovAutoOrderOutcome.RECONCILIATION_REQUIRED
            for item in result.orders
        )
        failed = sum(
            item.outcome in {SgovAutoOrderOutcome.FAILED, SgovAutoOrderOutcome.SKIPPED}
            for item in result.orders
        )
        if reconciling:
            status = "RECONCILIATION_REQUIRED"
        elif submitted and failed:
            status = "PARTIAL"
        elif submitted:
            status = "SUBMITTED"
        else:
            status = "BLOCKED"
        lines = [
            f"SGOV · 自动买入结果 · {date_label}",
            f"状态：{status}",
        ]
        for order in result.orders:
            details = ", ".join(order.error_codes) or order.provider_status or order.durable_status
            line = (
                f"{order.account_ref}：{order.outcome.value} · "
                f"{order.quantity} 股 @ ${order.limit_price}"
            )
            if details:
                line += f" · {details}"
            lines.append(line)
            if (
                order.outcome is SgovAutoOrderOutcome.RECONCILIATION_REQUIRED
                and order.order_intent_id is not None
            ):
                lines.append(f"待核对意图：{order.order_intent_id}")
        if result.error_codes:
            lines.append("错误：" + ", ".join(result.error_codes))
        if reconciling:
            lines.append("待核对订单不会自动重试。")
        if not result.orders and not result.error_codes:
            lines.append("未生成可执行订单。")
        return "\n".join(lines)

    @staticmethod
    def _idempotency_key(
        session: MarketSession,
        phase: SgovShadowPlanPhase,
        *,
        auto_execute: bool,
    ) -> str:
        prefix = "sgov-auto-plan" if auto_execute else "sgov-shadow-plan"
        return f"{prefix}:{session.session_date.isoformat()}:{phase.value.lower()}"


def _provider_diagnostics(
    errors: object,
    *,
    stage: SgovBlockedStage,
    default_provider: str,
    attempt_count: int,
) -> tuple[SgovProviderDiagnosticDTO, ...]:
    """Project secret-safe error metadata into the durable SGOV receipt."""

    diagnostics: list[SgovProviderDiagnosticDTO] = []
    for error in errors if isinstance(errors, (tuple, list)) else ():
        details = getattr(error, "details", {})
        if not isinstance(details, dict):
            details = {}
        status_code = details.get("status_code")
        diagnostics.append(
            SgovProviderDiagnosticDTO(
                stage=stage,
                provider=str(details.get("vendor") or default_provider),
                error_code=str(getattr(error, "code", "UNKNOWN")),
                retryable=bool(getattr(error, "retryable", False)),
                attempt_count=attempt_count,
                operation=(
                    str(details["operation"]) if details.get("operation") is not None else None
                ),
                error_type=(
                    str(details["error_type"])
                    if details.get("error_type") is not None
                    else None
                ),
                status_class=(
                    str(details["status_class"])
                    if details.get("status_class") is not None
                    else None
                ),
                status_code=(status_code if type(status_code) is int else None),
            )
        )
    return tuple(diagnostics)


def _has_retryable_error(errors: object) -> bool:
    if not isinstance(errors, (tuple, list)):
        return False
    return any(bool(getattr(error, "retryable", False)) for error in errors)


def _blocked_stage_label(stage: SgovBlockedStage | None) -> str:
    if stage is None:
        return "未知阶段"
    return {
        SgovBlockedStage.ACCOUNT_REFRESH: "Schwab 账户刷新",
        SgovBlockedStage.QUOTE_AND_SIZING: "SGOV 报价与数量计算",
        SgovBlockedStage.PRE_SUBMIT: "提交前安全复核",
        SgovBlockedStage.SUBMISSION: "Schwab 订单提交",
    }[stage]


def _diagnostic_lines(
    diagnostics: tuple[SgovProviderDiagnosticDTO, ...],
) -> tuple[str, ...]:
    if not diagnostics:
        return ()
    first = diagnostics[0]
    details = [f"Provider：{first.provider}", f"尝试：{first.attempt_count} 次"]
    if first.operation:
        details.append(f"操作：{first.operation}")
    if first.error_type:
        details.append(f"类型：{first.error_type}")
    if first.status_code is not None:
        details.append(f"HTTP：{first.status_code}")
    details.append("可重试：是" if first.retryable else "可重试：否")
    return ("诊断：" + " · ".join(details),)
