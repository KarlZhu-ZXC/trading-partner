"""Composite Monitor feature extraction, bounded LLM judgment, and deduplication."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from application.dto.us_market import MarketGetBarsInput
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.monitor_judgment_provider import (
    MonitorJudgmentProvider,
    MonitorJudgmentRequest,
    MonitorJudgmentResponse,
)
from application.ports.monitor_repository import MonitorRepository
from application.services.market_tool_coordinator import MarketToolCoordinator
from domain.common.errors import TradingPartnerError
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
    ) -> None:
        self._repository = repository
        self._market = market
        self._provider = provider
        self._clock = clock
        self._ids = id_generator

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
        now = self._clock.now()
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
                    summary="Deterministic feature state is unchanged; LLM call skipped.",
                    evidence_feature_ids=previous.evidence_feature_ids,
                    next_trigger=previous.next_trigger,
                    invalidation=previous.invalidation,
                    feature_signature=signature,
                    result_fingerprint=previous.result_fingerprint,
                    provider=self._provider.provider_name,
                    model=self._provider.model,
                    reasoning_effort=previous.reasoning_effort,
                    prompt_version=policy.prompt_version,
                    warning_codes=("MONITOR_JUDGMENT_UNCHANGED",),
                    error_codes=(),
                    created_at=now,
                ),
                event=None,
                notification=None,
            )
        try:
            raw = await self._provider.judge(
                MonitorJudgmentRequest(
                    playbook=policy.playbook,
                    confirmed_state_json=policy.confirmed_state_json,
                    feature_snapshot_json=json.dumps(
                        features, separators=(",", ":"), sort_keys=True
                    ),
                    allowed_feature_ids=allowed_ids,
                )
            )
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
                provider=self._provider.provider_name,
                model=self._provider.model,
                reasoning_effort=normalized.reasoning_effort_used,
                prompt_version=policy.prompt_version,
                warning_codes=tuple(
                    dict.fromkeys(
                        (
                            *features["warning_codes"],
                            *(
                                ("LLM_WEB_SEARCH_USED",)
                                if normalized.web_search_used
                                else ()
                            ),
                        )
                    )
                ),
                error_codes=(),
                created_at=now,
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
                summary=(
                    "Composite judgment is unavailable; deterministic rule results remain valid."
                ),
                evidence_feature_ids=(),
                next_trigger=None,
                invalidation=None,
                feature_signature=signature,
                result_fingerprint=None,
                provider=self._provider.provider_name,
                model=self._provider.model,
                reasoning_effort=self._provider.reasoning_effort,
                prompt_version=policy.prompt_version,
                warning_codes=tuple(features["warning_codes"]),
                error_codes=(exc.code,),
                created_at=now,
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
                judgment, event, self._notification(monitor, judgment, event) if event else None
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
            hourly, daily = await asyncio.gather(
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
            hbars = tuple(hourly.data.bars) if hourly.ok and hourly.data is not None else ()
            dbars = tuple(daily.data.bars) if daily.ok and daily.data is not None else ()
            item = {
                "instrument_id": instrument_id,
                "latest_price": str(hbars[-1].close if hbars else dbars[-1].close)
                if hbars or dbars
                else None,
                "price_time": (hbars[-1].timestamp if hbars else dbars[-1].timestamp).isoformat()
                if hbars or dbars
                else None,
                "return_1h_pct": _return(hbars, 1),
                "return_4h_pct": _return(hbars, 4),
                "return_1d_pct": _return(dbars, 1),
                "return_3d_pct": _return(dbars, 3),
                "source_names": tuple(
                    dict.fromkeys(
                        item.name for envelope in (hourly, daily) for item in envelope.sources
                    )
                ),
                "available": bool(hbars or dbars),
            }
            warnings = tuple(
                dict.fromkeys(
                    str(cast(Any, item).code)
                    for envelope in (hourly, daily)
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
        timestamps = [
            datetime.fromisoformat(str(item["price_time"]))
            for item in instruments.values()
            if item["price_time"] is not None
        ]
        sessions_aligned = (
            len(timestamps) == len(instruments)
            and bool(timestamps)
            and max(timestamps) - min(timestamps) <= timedelta(hours=2)
        )
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
            "warning_codes": warning_codes,
        }
        ids = tuple(
            [
                f"{instrument_id}.{metric}"
                for instrument_id, item in instruments.items()
                for metric in item
                if metric.startswith("return_") or metric in {"latest_price", "price_time"}
            ]
            + [
                f"relative_strength.{name}.{metric}"
                for name, item in relative.items()
                for metric in item
                if metric.startswith("return_")
            ]
            + [f"rule.{code}.state" for code in rules]
            + ["sessions_aligned"]
        )
        qualitative = {
            "instruments": {
                key: {
                    metric: _direction(value)
                    for metric, value in item.items()
                    if metric.startswith("return_")
                }
                for key, item in instruments.items()
            },
            "relative_strength": {
                key: {
                    metric: _direction(value)
                    for metric, value in item.items()
                    if metric.startswith("return_")
                }
                for key, item in relative.items()
            },
            "rules": {key: item["state"] for key, item in rules.items()},
            "sessions_aligned": sessions_aligned,
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
        divergence = raw.divergence
        quantity_min = raw.quantity_min
        quantity_max = raw.quantity_max
        if not features["sessions_aligned"] and divergence != "NONE":
            divergence = "NONE"
            if conclusion in {
                MonitorJudgmentConclusion.REDUCE,
                MonitorJudgmentConclusion.BUY,
                MonitorJudgmentConclusion.BUY_SMALL,
                MonitorJudgmentConclusion.BUY_AGGRESSIVELY,
            }:
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
        quantity = (
            "0，等待确认"
            if not judgment.quantity_max
            else f"{judgment.quantity_min}–{judgment.quantity_max}"
        )
        body = "\n".join(
            (
                f"{judgment.urgency or 'WATCH'} · {judgment.phase or '未定义阶段'}",
                f"结论：{judgment.conclusion.value if judgment.conclusion else '判断不可用'}",
                f"状态：{judgment.market_state or judgment.summary}",
                f"背离：{judgment.divergence or 'UNKNOWN'}",
                f"建议数量：{quantity}",
                f"依据：{', '.join(judgment.evidence_feature_ids) or '确定性规则仍可单独查看'}",
                f"下一触发：{judgment.next_trigger or '等待数据恢复'}",
                f"失效：{judgment.invalidation or '等待重新评估'}",
                "确认持仓/阶段未被自动修改。",
            )
        )
        return NotificationMessage(
            notification_id=self._ids.new(EntityIdPrefix.MONITOR_NOTIFICATION),
            source_type=NotificationSourceType.MONITOR_EVENT,
            source_id=event.event_id,
            channel=NotificationChannel.TELEGRAM,
            title=(
                f"{monitor.name} · "
                f"{judgment.conclusion.value if judgment.conclusion else 'JUDGMENT UNAVAILABLE'}"
            ),
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
