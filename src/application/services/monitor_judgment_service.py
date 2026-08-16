"""Composite Monitor feature extraction, bounded LLM judgment, and deduplication."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from zoneinfo import ZoneInfo

from application.dto.us_market import MarketGetBarsInput, MarketGetSnapshotInput
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.monitor_judgment_provider import (
    MonitorJudgmentProvider,
    MonitorJudgmentRequest,
    MonitorJudgmentResponse,
)
from application.ports.monitor_repository import MonitorRepository
from application.services.market_tool_coordinator import MarketToolCoordinator
from domain.common.errors import (
    DataContractError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    TradingPartnerError,
)
from domain.common.ids import EntityIdPrefix
from domain.monitoring.enums import (
    MonitorEventType,
    MonitorJudgmentConclusion,
    MonitorSeverity,
)
from domain.monitoring.models import (
    MonitorDefinition,
    MonitorEvent,
    MonitorJudgment,
    MonitorRunObservation,
)
from domain.notifications.enums import NotificationChannel, NotificationSourceType
from domain.notifications.models import NotificationMessage
from domain.us_market.enums import USBarInterval

# Quote alignment is deliberately stricter than the broad source freshness
# labels returned by a Provider.  A quote older than this cannot establish an
# actionable cross-asset relationship, even when all legs happen to have the
# same timestamp.  Daily bars get a wider window because exchange closes are
# represented in local time and can differ by almost one calendar day.
_QUOTE_ALIGNMENT_MAX_AGE = timedelta(hours=2)
_HOURLY_RETURN_ALIGNMENT_MAX_AGE = timedelta(hours=2)
# Closed-market tolerance rather than an intraday freshness guarantee.  This
# spans a normal weekend and a short exchange holiday while the normalized
# trading-date check below still prevents mismatched daily windows.
_DAILY_RETURN_ALIGNMENT_MAX_AGE = timedelta(hours=96)
_ALIGNMENT_MAX_SKEW = timedelta(hours=2)
_US_EASTERN = ZoneInfo("America/New_York")
_KOREA = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True, slots=True)
class MonitorJudgmentResult:
    judgment: MonitorJudgment
    event: MonitorEvent | None
    notification: NotificationMessage | None


class MonitorJudgmentService:
    def __init__(
        self,
        repository: MonitorRepository,
        market: MarketToolCoordinator,
        provider: MonitorJudgmentProvider,
        clock: Clock,
        id_generator: IdGenerator,
        fallback_provider: MonitorJudgmentProvider | None = None,
    ) -> None:
        self._repository = repository
        self._market = market
        self._provider = provider
        self._clock = clock
        self._ids = id_generator
        self._fallback_provider = fallback_provider

    async def evaluate(
        self,
        *,
        run_id: str,
        monitor: MonitorDefinition,
        observations: tuple[MonitorRunObservation, ...],
        hard_transition: bool,
    ) -> MonitorJudgmentResult | None:
        policy = monitor.judgment_policy
        if policy is None:
            return None
        features, allowed_ids, signature = await self._features(monitor, observations)
        latest_receipt = self._repository.latest_judgment(monitor.monitor_id)
        previous = next(
            (
                item
                for item in self._repository.list_judgments(monitor.monitor_id, 20)
                if item.status == "SUCCEEDED"
            ),
            None,
        )
        selected_provider = self._provider
        fallback_warning_codes: tuple[str, ...] = ()
        if (
            not hard_transition
            and previous is not None
            and previous.status == "SUCCEEDED"
            and previous.feature_signature == signature
        ):
            return MonitorJudgmentResult(
                judgment=MonitorJudgment(
                    judgment_id=self._ids.new(EntityIdPrefix.MONITOR_JUDGMENT),
                    run_id=run_id,
                    monitor_id=monitor.monitor_id,
                    monitor_version=monitor.version,
                    status="SKIPPED",
                    urgency=previous.urgency,
                    phase=previous.phase,
                    market_state=previous.market_state,
                    divergence=previous.divergence,
                    conclusion=previous.conclusion,
                    quantity_min=previous.quantity_min,
                    quantity_max=previous.quantity_max,
                    summary="确定性特征状态未变化，已跳过模型调用。",
                    evidence_feature_ids=previous.evidence_feature_ids,
                    next_trigger=previous.next_trigger,
                    invalidation=previous.invalidation,
                    feature_signature=signature,
                    result_fingerprint=previous.result_fingerprint,
                    provider=previous.provider,
                    model=previous.model,
                    reasoning_effort=previous.reasoning_effort,
                    prompt_version=policy.prompt_version,
                    warning_codes=("MONITOR_JUDGMENT_UNCHANGED",),
                    error_codes=(),
                    created_at=self._clock.now(),
                ),
                event=None,
                notification=None,
            )
        try:
            judgment_request = MonitorJudgmentRequest(
                playbook=policy.playbook,
                confirmed_state_json=policy.confirmed_state_json,
                feature_snapshot_json=json.dumps(
                    features, separators=(",", ":"), sort_keys=True
                ),
                allowed_feature_ids=allowed_ids,
            )
            try:
                raw = await self._provider.judge(judgment_request)
            except (
                DataContractError,
                ProviderTimeoutError,
                ProviderRateLimitError,
                ProviderUnavailableError,
            ) as primary_error:
                if self._fallback_provider is None:
                    raise
                selected_provider = self._fallback_provider
                fallback_warning_codes = (
                    "MONITOR_JUDGMENT_FALLBACK_USED",
                    f"PRIMARY_{primary_error.code}",
                )
                try:
                    raw = await selected_provider.judge(judgment_request)
                except DataContractError:
                    # A malformed model payload has no execution effect and is
                    # safe to retry once. Keep the retry bounded; a second
                    # invalid payload remains an explicit failed judgment.
                    fallback_warning_codes += (
                        "MONITOR_JUDGMENT_FALLBACK_CONTRACT_RETRIED",
                    )
                    raw = await selected_provider.judge(judgment_request)
            normalized = self._validate(raw, policy.confirmed_state_json, features, allowed_ids)
            fingerprint = _hash(
                {
                    "urgency": normalized.urgency,
                    "phase": normalized.phase,
                    "divergence": normalized.divergence,
                    "conclusion": normalized.conclusion,
                    "quantity_min": normalized.quantity_min,
                    "quantity_max": normalized.quantity_max,
                }
            )
            judgment = MonitorJudgment(
                judgment_id=self._ids.new(EntityIdPrefix.MONITOR_JUDGMENT),
                run_id=run_id,
                monitor_id=monitor.monitor_id,
                monitor_version=monitor.version,
                status="SUCCEEDED",
                urgency=normalized.urgency,  # type: ignore[arg-type]
                phase=normalized.phase,
                market_state=normalized.market_state,
                divergence=normalized.divergence,  # type: ignore[arg-type]
                conclusion=MonitorJudgmentConclusion(normalized.conclusion),
                quantity_min=normalized.quantity_min,
                quantity_max=normalized.quantity_max,
                summary=normalized.summary,
                evidence_feature_ids=normalized.evidence_feature_ids,
                next_trigger=normalized.next_trigger,
                invalidation=normalized.invalidation,
                feature_signature=signature,
                result_fingerprint=fingerprint,
                provider=selected_provider.provider_name,
                model=selected_provider.model,
                reasoning_effort=normalized.reasoning_effort_used,
                prompt_version=policy.prompt_version,
                warning_codes=tuple(
                    dict.fromkeys(
                        (
                            *features["warning_codes"],
                            *fallback_warning_codes,
                            *(
                                ("LLM_WEB_SEARCH_USED",)
                                if normalized.web_search_used
                                else ()
                            ),
                        )
                    )
                ),
                error_codes=(),
                created_at=self._clock.now(),
                web_search_used=normalized.web_search_used,
                web_source_urls=normalized.web_source_urls,
            )
        except TradingPartnerError as exc:
            judgment = MonitorJudgment(
                judgment_id=self._ids.new(EntityIdPrefix.MONITOR_JUDGMENT),
                run_id=run_id,
                monitor_id=monitor.monitor_id,
                monitor_version=monitor.version,
                status="FAILED",
                urgency=None,
                phase=None,
                market_state=None,
                divergence=None,
                conclusion=None,
                quantity_min=None,
                quantity_max=None,
                summary="复合判断暂时不可用；确定性规则结果仍然有效。",
                evidence_feature_ids=(),
                next_trigger=None,
                invalidation=None,
                feature_signature=signature,
                result_fingerprint=None,
                provider=selected_provider.provider_name,
                model=selected_provider.model,
                reasoning_effort=selected_provider.reasoning_effort,
                prompt_version=policy.prompt_version,
                warning_codes=tuple(
                    dict.fromkeys((*features["warning_codes"], *fallback_warning_codes))
                ),
                error_codes=(exc.code,),
                created_at=self._clock.now(),
            )
            changed = (
                latest_receipt is None
                or latest_receipt.status != "FAILED"
                or latest_receipt.error_codes != judgment.error_codes
            )
            event = (
                self._event(monitor, judgment, MonitorEventType.JUDGMENT_UNAVAILABLE)
                if changed
                else None
            )
            return MonitorJudgmentResult(
                judgment,
                event,
                self._notification(monitor, judgment, event)
                if event is not None
                else None,
            )

        changed = previous is None or previous.result_fingerprint != judgment.result_fingerprint
        actionable = judgment.conclusion not in {
            MonitorJudgmentConclusion.WATCH,
            MonitorJudgmentConclusion.HOLD,
            MonitorJudgmentConclusion.WAIT,
        }
        event = (
            self._event(monitor, judgment, MonitorEventType.JUDGMENT_CHANGED)
            if changed and (previous is not None or actionable)
            else None
        )
        return MonitorJudgmentResult(
            judgment,
            event,
            self._notification(monitor, judgment, event) if event else None,
        )

    async def _features(
        self,
        monitor: MonitorDefinition,
        observations: tuple[MonitorRunObservation, ...],
    ) -> tuple[dict[str, Any], tuple[str, ...], str]:
        assert monitor.judgment_policy is not None
        now = self._clock.now()

        async def load(instrument_id: str) -> tuple[str, dict[str, Any], tuple[str, ...]]:
            quote, hourly, daily = await asyncio.gather(
                self._market.get_market_snapshot(
                    MarketGetSnapshotInput(
                        instrument_id=instrument_id,
                        as_of=now,
                    )
                ),
                self._market.get_market_bars(
                    MarketGetBarsInput(
                        instrument_id=instrument_id,
                        start=(now - timedelta(days=10)).date(),
                        end=now.date(),
                        interval=USBarInterval.SIXTY_MINUTES,
                        as_of=now,
                    )
                ),
                self._market.get_market_bars(
                    MarketGetBarsInput(
                        instrument_id=instrument_id,
                        start=(now - timedelta(days=14)).date(),
                        end=now.date(),
                        interval=USBarInterval.ONE_DAY,
                        as_of=now,
                    )
                ),
            )
            quote_data = quote.data if quote.ok and quote.data is not None else None
            hbars = tuple(hourly.data.bars) if hourly.ok and hourly.data is not None else ()
            dbars = tuple(daily.data.bars) if daily.ok and daily.data is not None else ()
            quote_price = _quote_price(quote_data)
            quote_time = getattr(quote_data, "quote_at", None)
            previous_regular_session_close = _valid_previous_regular_session_close(
                getattr(quote_data, "previous_close", None)
            )
            bar_price = hbars[-1].close if hbars else dbars[-1].close if dbars else None
            bar_time = hbars[-1].timestamp if hbars else dbars[-1].timestamp if dbars else None
            price_time = quote_time if quote_price is not None else bar_time
            item = {
                "instrument_id": instrument_id,
                "latest_price": str(quote_price if quote_price is not None else bar_price)
                if quote_price is not None or bar_price is not None
                else None,
                "price_time": price_time.isoformat() if price_time is not None else None,
                "price_session": _wire_value(getattr(quote_data, "session", None)),
                "price_basis": _wire_value(getattr(quote_data, "price_basis", None)),
                "price_source": "quote"
                if quote_price is not None
                else "hourly_bar"
                if hbars
                else "daily_bar"
                if dbars
                else None,
                "previous_regular_session_close": str(previous_regular_session_close)
                if previous_regular_session_close is not None
                else None,
                "return_from_previous_regular_session_close_pct": (
                    _return_from_previous_regular_session_close_pct(
                        quote_price, previous_regular_session_close
                    )
                ),
                "latest_price_age_seconds": max(0, int((now - price_time).total_seconds()))
                if price_time is not None
                else None,
                "return_1h_pct": _return(hbars, 1),
                "return_4h_pct": _return(hbars, 4),
                "return_1d_pct": _return(dbars, 1),
                "return_3d_pct": _return(dbars, 3),
                "hourly_return_as_of": hbars[-1].timestamp.isoformat() if hbars else None,
                "daily_return_as_of": dbars[-1].timestamp.isoformat() if dbars else None,
                "source_names": tuple(
                    dict.fromkeys(
                        item.name
                        for envelope in (quote, hourly, daily)
                        for item in envelope.sources
                    )
                ),
                "available": quote_price is not None or bool(hbars or dbars),
            }
            warnings = tuple(
                dict.fromkeys(
                    str(cast(Any, item).code)
                    for envelope in (quote, hourly, daily)
                    for item in (*envelope.warnings, *envelope.errors)
                )
            )
            return instrument_id, item, warnings

        loaded = await asyncio.gather(
            *(load(item) for item in monitor.judgment_policy.reference_instrument_ids)
        )
        instruments = {instrument_id: item for instrument_id, item, _warnings in loaded}
        warning_codes = tuple(
            dict.fromkeys(code for _id, _item, warnings in loaded for code in warnings)
        )
        quote_sessions_aligned = _quotes_aligned(instruments, now)
        hourly_returns_aligned = _returns_aligned(
            instruments,
            as_of_key="hourly_return_as_of",
            value_key="return_1h_pct",
            now=now,
            max_age=_HOURLY_RETURN_ALIGNMENT_MAX_AGE,
        )
        daily_returns_aligned = _daily_returns_aligned(instruments, now)
        # ``sessions_aligned`` is a compatibility alias used by older
        # providers/playbooks.  It intentionally follows the latest quote
        # window; the separate return flags are the source of truth for
        # hourly/daily evidence.
        sessions_aligned = quote_sessions_aligned
        relative: dict[str, Any] = {}
        for name, numerator, denominator in monitor.judgment_policy.relative_strength_pairs:
            relative[name] = {
                "numerator": numerator,
                "denominator": denominator,
                "return_1h_spread_pct": _spread(
                    instruments[numerator], instruments[denominator], "return_1h_pct"
                ),
                "return_4h_spread_pct": _spread(
                    instruments[numerator], instruments[denominator], "return_4h_pct"
                ),
                "return_1d_spread_pct": _spread(
                    instruments[numerator], instruments[denominator], "return_1d_pct"
                ),
                "return_from_previous_regular_session_close_spread_pct": _spread(
                    instruments[numerator],
                    instruments[denominator],
                    "return_from_previous_regular_session_close_pct",
                ),
            }
        rules = {
            item.rule_code: {
                "state": item.state.value,
                "observed_value": str(item.observed_value)
                if item.observed_value is not None
                else None,
                "threshold_value": str(item.threshold_value)
                if item.threshold_value is not None
                else None,
                "fact_as_of": item.fact_as_of.isoformat() if item.fact_as_of else None,
            }
            for item in observations
        }
        features: dict[str, Any] = {
            "instruments": instruments,
            "relative_strength": relative,
            "rules": rules,
            "sessions_aligned": sessions_aligned,
            "quote_sessions_aligned": quote_sessions_aligned,
            "hourly_returns_aligned": hourly_returns_aligned,
            "daily_returns_aligned": daily_returns_aligned,
            "warning_codes": warning_codes,
        }
        ids = tuple(
            [
                f"{instrument_id}.{metric}"
                for instrument_id, item in instruments.items()
                for metric in item
                if _is_return_metric(metric)
                or metric
                in {
                    "latest_price",
                    "price_time",
                    "price_session",
                    "price_basis",
                    "price_source",
                    "previous_regular_session_close",
                    "latest_price_age_seconds",
                    "hourly_return_as_of",
                    "daily_return_as_of",
                }
            ]
            + [
                f"relative_strength.{name}.{metric}"
                for name, item in relative.items()
                for metric in item
                if _is_return_metric(metric)
            ]
            + [f"rule.{code}.state" for code in rules]
            + [
                "sessions_aligned",
                "quote_sessions_aligned",
                "hourly_returns_aligned",
                "daily_returns_aligned",
            ]
        )
        qualitative = {
            "instruments": {
                key: {
                    metric: (
                        _direction(value)
                        if _is_return_metric(metric)
                        else value
                    )
                    for metric, value in item.items()
                    if _is_return_metric(metric)
                    or metric in {"price_session", "price_basis", "price_source"}
                }
                for key, item in instruments.items()
            },
            "relative_strength": {
                key: {
                    metric: _direction(value)
                    for metric, value in item.items()
                    if _is_return_metric(metric)
                }
                for key, item in relative.items()
            },
            "rules": {key: item["state"] for key, item in rules.items()},
            "sessions_aligned": sessions_aligned,
            "quote_sessions_aligned": quote_sessions_aligned,
            "hourly_returns_aligned": hourly_returns_aligned,
            "daily_returns_aligned": daily_returns_aligned,
        }
        return features, ids, _hash(qualitative)

    def _validate(
        self,
        raw: MonitorJudgmentResponse,
        confirmed_state_json: str,
        features: dict[str, Any],
        allowed_ids: tuple[str, ...],
    ) -> MonitorJudgmentResponse:
        if raw.urgency not in {"WATCH", "ACTION", "URGENT"}:
            raise TradingPartnerError(
                "LLM judgment urgency is invalid", code="MONITOR_JUDGMENT_INVALID"
            )
        if raw.divergence not in {"BULLISH", "BEARISH", "NONE"}:
            raise TradingPartnerError(
                "LLM judgment divergence is invalid", code="MONITOR_JUDGMENT_INVALID"
            )
        conclusion = MonitorJudgmentConclusion(raw.conclusion)
        if not set(raw.evidence_feature_ids).issubset(allowed_ids):
            raise TradingPartnerError(
                "LLM cited an unknown Monitor feature", code="MONITOR_JUDGMENT_INVALID"
            )
        if any(
            _uses_ambiguous_previous_close_language(value)
            for value in (
                raw.phase,
                raw.market_state,
                raw.summary,
                raw.next_trigger,
                raw.invalidation,
            )
        ):
            raise TradingPartnerError(
                "LLM used ambiguous previous-close language",
                code="MONITOR_JUDGMENT_INVALID",
            )
        divergence = raw.divergence
        quantity_min = raw.quantity_min
        quantity_max = raw.quantity_max
        # A fresh, synchronized quote window is sufficient for a price-based
        # divergence/action (including the previous-regular-close spread).  Do not
        # require regular-session or daily-bar alignment before allowing that
        # evidence.  Older feature snapshots only carry ``sessions_aligned``;
        # retain that fallback while new snapshots use the explicit field.
        quote_sessions_aligned = features.get("quote_sessions_aligned")
        if quote_sessions_aligned is None:
            quote_sessions_aligned = features.get("sessions_aligned", False)
        quote_sessions_aligned = bool(quote_sessions_aligned)
        evidence_aligned = _evidence_alignment_ok(
            raw.evidence_feature_ids,
            quote_sessions_aligned=quote_sessions_aligned,
            hourly_returns_aligned=bool(features.get("hourly_returns_aligned", False)),
            daily_returns_aligned=bool(features.get("daily_returns_aligned", False)),
        )
        actionable_conclusion = conclusion in {
            MonitorJudgmentConclusion.REDUCE,
            MonitorJudgmentConclusion.BUY,
            MonitorJudgmentConclusion.BUY_SMALL,
            MonitorJudgmentConclusion.BUY_AGGRESSIVELY,
        }
        if (not quote_sessions_aligned or not evidence_aligned) and (
            divergence != "NONE" or actionable_conclusion
        ):
            divergence = "NONE"
            if actionable_conclusion:
                conclusion = MonitorJudgmentConclusion.WAIT
                quantity_min = quantity_max = 0
        state = json.loads(confirmed_state_json)
        if conclusion is MonitorJudgmentConclusion.REDUCE:
            cap = max(
                0,
                _state_number(state, "confirmed_position")
                - _state_number(state, "runner_target_min"),
            )
            quantity_max = min(quantity_max, cap)
            quantity_min = min(quantity_min, quantity_max)
        elif conclusion in {
            MonitorJudgmentConclusion.BUY_SMALL,
            MonitorJudgmentConclusion.BUY,
            MonitorJudgmentConclusion.BUY_AGGRESSIVELY,
        }:
            cap = _state_number(state, "phase_B_remaining", "phase_B_remaining_max")
            quantity_max = min(quantity_max, cap)
            quantity_min = min(quantity_min, quantity_max)
        else:
            quantity_min = quantity_max = 0
        return replace(
            raw,
            divergence=divergence,
            conclusion=conclusion.value,
            quantity_min=quantity_min,
            quantity_max=quantity_max,
        )

    def _event(
        self, monitor: MonitorDefinition, judgment: MonitorJudgment, event_type: MonitorEventType
    ) -> MonitorEvent:
        severity = (
            MonitorSeverity.HIGH
            if judgment.urgency == "URGENT"
            else MonitorSeverity.MEDIUM
            if judgment.urgency == "ACTION"
            else MonitorSeverity.INFO
        )
        return MonitorEvent(
            event_id=self._ids.new(EntityIdPrefix.MONITOR_EVENT),
            monitor_id=monitor.monitor_id,
            monitor_version=monitor.version,
            rule_code="COMPOSITE_JUDGMENT",
            event_type=event_type,
            severity=severity,
            observed_value=None,
            threshold_value=None,
            fact_as_of=judgment.created_at,
            message=(
                f"{judgment.conclusion.value if judgment.conclusion else 'UNAVAILABLE'}: "
                f"{judgment.summary}"
            )[:1000],
            created_at=judgment.created_at,
        )

    def _notification(
        self, monitor: MonitorDefinition, judgment: MonitorJudgment, event: MonitorEvent
    ) -> NotificationMessage:
        if getattr(judgment, "status", None) == "FAILED" or judgment.conclusion is None:
            return self._failure_notification(monitor, judgment, event)
        quantity = (
            "0，等待确认"
            if not judgment.quantity_max
            else f"{judgment.quantity_min}–{judgment.quantity_max}"
        )
        conclusion = judgment.conclusion.value if judgment.conclusion else "判断不可用"
        symbol = (
            monitor.primary_instrument_id.rsplit(":", 1)[-1]
            if monitor.primary_instrument_id is not None
            else monitor.name[:24]
        )
        body = "\n".join(
            (
                f"监控：{monitor.name}",
                f"阶段：{judgment.phase or '未定义'} · {judgment.urgency or 'WATCH'}",
                f"结论：{conclusion} · 数量 {quantity}",
                f"市场：{judgment.market_state or judgment.summary}",
                f"背离：{judgment.divergence or 'UNKNOWN'}",
                f"依据：{', '.join(judgment.evidence_feature_ids) or '无可引用复合特征'}",
                f"关注：{judgment.next_trigger or '等待下一次有效评估'}",
                f"失效：{judgment.invalidation or '等待重新评估'}",
                "说明：仅更新判断记录；未修改持仓、阶段或订单。",
            )
        )
        return NotificationMessage(
            notification_id=self._ids.new(EntityIdPrefix.MONITOR_NOTIFICATION),
            source_type=NotificationSourceType.MONITOR_EVENT,
            source_id=event.event_id,
            channel=NotificationChannel.TELEGRAM,
            title=f"🧭 {symbol} · {conclusion}",
            body=body,
            created_at=judgment.created_at,
        )

    def _failure_notification(
        self,
        monitor: MonitorDefinition,
        judgment: MonitorJudgment,
        event: MonitorEvent,
    ) -> NotificationMessage:
        """Render model degradation without inventing a market judgment.

        Deterministic rule observations are assembled by ``MonitorEvaluationService``;
        this section carries only the operational status and typed model error so it
        can be appended to that same-run card.  In particular, it deliberately does
        not render placeholder phase, direction, or quantity values.
        """
        symbol = (
            monitor.primary_instrument_id.rsplit(":", 1)[-1]
            if monitor.primary_instrument_id is not None
            else monitor.name[:24]
        )
        error_codes = tuple(getattr(judgment, "error_codes", ()))
        warning_codes = tuple(getattr(judgment, "warning_codes", ()))
        rendered_errors = ", ".join(error_codes) or "MONITOR_JUDGMENT_UNAVAILABLE"
        lines = [
            "状态：复合判断暂时不可用；确定性规则结果仍然有效。",
            f"错误码：{rendered_errors}",
        ]
        provider = getattr(judgment, "provider", None)
        model = getattr(judgment, "model", None)
        if provider or model:
            lines.append(f"失败模型：{provider or '未知 Provider'} / {model or '未知模型'}")
        if "MONITOR_JUDGMENT_FALLBACK_USED" in warning_codes:
            primary_error = next(
                (
                    item.removeprefix("PRIMARY_")
                    for item in warning_codes
                    if item.startswith("PRIMARY_")
                ),
                "UNKNOWN",
            )
            lines.append(f"调用路径：主模型失败（{primary_error}），已尝试 fallback")
        if "MONITOR_JUDGMENT_FALLBACK_CONTRACT_RETRIED" in warning_codes:
            lines.append("fallback：首次输出未通过结构校验，已进行一次有界重试")
        if "DATA_CONTRACT_ERROR" in error_codes:
            lines.append("失败阶段：模型输出结构校验；未采用不合规结果")
        lines.extend(
            (
                "市场：未生成新的模型判断",
                "处理：等待下一轮自动重试；价格规则仍按确定性结果运行。",
            )
        )
        body = "\n".join(lines)
        return NotificationMessage(
            notification_id=self._ids.new(EntityIdPrefix.MONITOR_NOTIFICATION),
            source_type=NotificationSourceType.MONITOR_EVENT,
            source_id=event.event_id,
            channel=NotificationChannel.TELEGRAM,
            title=f"🧭 {symbol} · 判断不可用",
            body=body,
            created_at=judgment.created_at,
        )


def _return(bars: tuple[Any, ...], periods: int) -> str | None:
    if len(bars) <= periods:
        return None
    current = bars[-1].close
    prior = bars[-1 - periods].close
    if prior == 0:
        return None
    return str(((current / prior) - Decimal(1)) * Decimal(100))


def _quote_price(data: object | None) -> object | None:
    if data is None:
        return None
    display_price = getattr(data, "display_price", None)
    return display_price if display_price is not None else getattr(data, "last", None)


def _valid_previous_regular_session_close(value: object | None) -> Decimal | None:
    """Return a usable completed-regular-session baseline, never a sentinel."""

    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _return_from_previous_regular_session_close_pct(
    quote_price: object | None, previous_regular_session_close: Decimal | None
) -> str | None:
    if quote_price is None or previous_regular_session_close is None:
        return None
    try:
        current = Decimal(str(quote_price))
    except Exception:
        return None
    if current <= 0:
        return None
    return str(
        ((current / previous_regular_session_close) - Decimal(1)) * Decimal(100)
    )


def _is_return_metric(metric: str) -> bool:
    return metric.startswith("return_") or metric.startswith("quote_return_")


def _uses_ambiguous_previous_close_language(value: str) -> bool:
    normalized = re.sub(r"\s+", "", value).lower()
    return any(
        ambiguous in normalized
        for ambiguous in (
            "昨收",
            "昨日收盘",
            "昨天收盘",
            "上一收盘",
            "上次收盘",
            "上一根k线",
            "前一根k线",
            "上根k线",
            "yesterdayclose",
            "yesterday'sclose",
            "previousclose",
            "priorclose",
            "previouscandleclose",
        )
    )


def _evidence_alignment_ok(
    evidence_feature_ids: tuple[str, ...],
    *,
    quote_sessions_aligned: bool,
    hourly_returns_aligned: bool,
    daily_returns_aligned: bool,
) -> bool:
    """Ensure a model cannot use a stale return window via a fresh quote flag."""

    for feature_id in evidence_feature_ids:
        if "return_1h" in feature_id or "return_4h" in feature_id:
            if not hourly_returns_aligned:
                return False
        elif "return_1d" in feature_id or "return_3d" in feature_id:
            if not daily_returns_aligned:
                return False
        elif not quote_sessions_aligned and any(
            token in feature_id
            for token in (
                "return_from_previous_regular_session_close",
                ".latest_price",
                ".price_time",
                ".price_session",
                ".price_basis",
                ".price_source",
                ".previous_regular_session_close",
                "sessions_aligned",
            )
        ):
            return False
    return True


def _timestamp(value: object | None) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None and value.utcoffset() is not None else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
    return None


def _fresh_timestamp(value: object | None, now: datetime, max_age: timedelta) -> datetime | None:
    timestamp = _timestamp(value)
    if timestamp is None:
        return None
    age = now - timestamp
    if age < timedelta(0) or age > max_age:
        return None
    return timestamp


def _quotes_aligned(instruments: dict[str, dict[str, Any]], now: datetime) -> bool:
    timestamps: list[datetime] = []
    for item in instruments.values():
        if item.get("price_source") != "quote" or item.get("latest_price") is None:
            return False
        timestamp = _fresh_timestamp(
            item.get("price_time"), now, _QUOTE_ALIGNMENT_MAX_AGE
        )
        if timestamp is None:
            return False
        timestamps.append(timestamp)
    return bool(timestamps) and max(timestamps) - min(timestamps) <= _ALIGNMENT_MAX_SKEW


def _returns_aligned(
    instruments: dict[str, dict[str, Any]],
    *,
    as_of_key: str,
    value_key: str,
    now: datetime,
    max_age: timedelta,
) -> bool:
    timestamps: list[datetime] = []
    for item in instruments.values():
        if item.get(value_key) is None:
            return False
        timestamp = _fresh_timestamp(item.get(as_of_key), now, max_age)
        if timestamp is None:
            return False
        timestamps.append(timestamp)
    return bool(timestamps) and max(timestamps) - min(timestamps) <= _ALIGNMENT_MAX_SKEW


def _trading_date(instrument_id: str, timestamp: datetime) -> object:
    """Normalize daily-bar timestamps to the owning market's trading date."""

    market = instrument_id.split(":", 2)[1] if ":" in instrument_id else ""
    if market == "US":
        return timestamp.astimezone(_US_EASTERN).date()
    if market == "KR":
        return timestamp.astimezone(_KOREA).date()
    # Dukascopy OTC bars are bucketed in UTC; CME/DCE bars likewise retain
    # their timestamped settlement date.  A UTC date avoids comparing close
    # hours directly while preserving their exchange/bucket day.
    if market in {"OTC", "CME", "DCE"}:
        return timestamp.astimezone(UTC).date()
    return timestamp.date()


def _daily_returns_aligned(instruments: dict[str, dict[str, Any]], now: datetime) -> bool:
    dates: list[object] = []
    timestamps: list[datetime] = []
    for instrument_id, item in instruments.items():
        if item.get("return_1d_pct") is None:
            return False
        timestamp = _fresh_timestamp(
            item.get("daily_return_as_of"), now, _DAILY_RETURN_ALIGNMENT_MAX_AGE
        )
        if timestamp is None:
            return False
        timestamps.append(timestamp)
        dates.append(_trading_date(instrument_id, timestamp))
    # Daily closes can be many hours apart (for example XAU OTC versus NYSE),
    # so compare normalized trading dates rather than wall-clock skew.
    return bool(timestamps) and len(set(dates)) == 1


def _wire_value(value: object | None) -> object | None:
    return getattr(value, "value", value)


def _spread(left: dict[str, Any], right: dict[str, Any], key: str) -> str | None:
    if left[key] is None or right[key] is None:
        return None
    return str(Decimal(str(left[key])) - Decimal(str(right[key])))


def _direction(value: object) -> str:
    if value is None:
        return "UNAVAILABLE"
    number = Decimal(str(value))
    return "UP" if number > 0 else "DOWN" if number < 0 else "FLAT"


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _state_number(state: dict[str, object], *keys: str) -> int:
    for key in keys:
        value = state.get(key)
        if isinstance(value, (int, float)):
            return max(0, int(value))
        if isinstance(value, str):
            numbers = [int(item) for item in re.findall(r"\d+", value)]
            if numbers:
                return max(numbers)
    return 0
