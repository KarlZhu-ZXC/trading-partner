"""Deterministic, no-score behavior analytics over durable domain records."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import Decimal

from domain.behavior.enums import BehaviorMetricAvailability
from domain.behavior.models import (
    BEHAVIOR_SUMMARY_ALGORITHM_VERSION,
    BehaviorCohort,
    BehaviorMetric,
    BehaviorScalar,
    BehaviorSummary,
)
from domain.common.enums import DecisionType
from domain.portfolio.enums import (
    ActivityAnnotationStatus,
    TradeCycleClassification,
    TradeCycleStatus,
)
from domain.portfolio.models import ActivityAnnotation, TradeCycle
from domain.research.memory_models import DecisionRecord
from domain.retro.models import TradeRetroFinding

_NO_DECISION_CODES = {"ACTION_RECORD_MISMATCH", "NO_PREFILL_DECISION", "MISSING_DECISION"}
_MISSING_INVALIDATION_CODES = {"MISSING_INVALIDATION", "NO_PRETRADE_INVALIDATION"}
_REENTRY_CODES = {"SAME_DAY_REENTRY"}
_THIRD_ATTEMPT_CODES = {
    "THIRD_ATTEMPT_WITHOUT_NEW_PLAN",
    "THIRD_ATTEMPT_NO_NEW_PLAN",
    "REPEATED_ATTEMPT_WITHOUT_NEW_PLAN",
}


def _unique_sorted(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


def _decision_horizon(value: DecisionRecord) -> str | None:
    """Read optional horizon metadata without changing the Phase 4A model.

    Horizon is deliberately not inferred from a strategy name.  The helper
    accepts compatible DTOs/future records that expose one of the explicit
    names, while historical DecisionRecord objects simply have no horizon.
    """

    for field in ("horizon", "time_horizon", "horizon_code"):
        candidate = getattr(value, field, None)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _plan_key(decision: DecisionRecord) -> str | None:
    if decision.trade_plan_id is None or decision.trade_plan_version is None:
        return None
    return f"{decision.trade_plan_id}:{decision.trade_plan_version}"


def _safe_ratio(numerator: int | Decimal, denominator: int, *, minimum: int) -> Decimal | None:
    if denominator < minimum or denominator == 0:
        return None
    value = Decimal(numerator) / Decimal(denominator)
    return value if value.is_finite() else None


def _median(values: list[int]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return Decimal(ordered[middle])
    return (Decimal(ordered[middle - 1]) + Decimal(ordered[middle])) / Decimal(2)


class BehaviorSummaryCalculator:
    """Calculate explainable behavior metrics without persistence or Providers.

    No metric combines native currencies.  Counts and distributions may span
    currencies, but monetary averages/payoff ratios return ``None`` with an
    explicit ``MULTIPLE_NATIVE_CURRENCIES`` note when the sample is mixed.
    """

    algorithm_version = BEHAVIOR_SUMMARY_ALGORITHM_VERSION

    def __init__(self, *, minimum_sample_size: int = 1) -> None:
        if type(minimum_sample_size) is not int or minimum_sample_size < 0:
            raise ValueError("minimum_sample_size must be a nonnegative int")
        self.minimum_sample_size = minimum_sample_size

    def calculate(
        self,
        cycles: tuple[TradeCycle, ...],
        decisions: tuple[DecisionRecord, ...],
        retro_findings: tuple[TradeRetroFinding, ...] = (),
        cohort: BehaviorCohort | None = None,
        *,
        strategy_code: str | None = None,
        strategy_version: str | None = None,
        horizon: str | None = None,
        instrument_ids: tuple[str, ...] = (),
        instrument_id: str | None = None,
        currency: str | None = None,
        classifications: tuple[TradeCycleClassification, ...] = (),
        minimum_sample_size: int | None = None,
        cycle_decision_links: Mapping[str, tuple[str, ...]] | None = None,
        cycle_plan_links: Mapping[str, tuple[tuple[str, int], ...]] | None = None,
        activity_annotations: tuple[ActivityAnnotation, ...] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> BehaviorSummary:
        """Return one deterministic summary.

        ``cohort`` is the preferred filter interface.  Keyword filters are
        accepted for small callers and preserve a compact application seam.
        Input order never affects output order or values.

        ``cycle_decision_links`` and ``cycle_plan_links`` are exact durable
        annotations supplied by the application layer.  When absent, the
        calculator never treats instrument/time proximity as a pre-fill link;
        the affected metrics are explicitly ``NOT_SUPPORTED``.
        """

        min_sample = (
            self.minimum_sample_size
            if minimum_sample_size is None
            else self._validate_minimum_sample_size(minimum_sample_size)
        )
        selected_cohort = self._cohort(
            cohort,
            strategy_code=strategy_code,
            strategy_version=strategy_version,
            horizon=horizon,
            instrument_ids=instrument_ids,
            instrument_id=instrument_id,
            currency=currency,
            classifications=classifications,
            start=start,
            end=end,
        )
        ordered_cycles = tuple(sorted(cycles, key=lambda item: item.cycle_id))
        ordered_decisions = tuple(sorted(decisions, key=lambda item: item.decision_id))
        ordered_findings = tuple(
            sorted(
                retro_findings,
                key=lambda item: (
                    item.code,
                    item.instrument_id or "",
                    item.transaction_ids,
                    item.plan_id or "",
                ),
            )
        )
        decision_links = self._normalize_decision_links(cycle_decision_links)
        plan_links = self._normalize_plan_links(cycle_plan_links)
        annotation_decision_links, annotation_plan_links = self._annotation_links(
            ordered_cycles,
            activity_annotations,
        )
        if decision_links is None:
            decision_links = annotation_decision_links
        if plan_links is None:
            plan_links = annotation_plan_links

        visible_decisions = tuple(
            item
            for item in ordered_decisions
            if selected_cohort.end is None or item.decided_at <= selected_cohort.end
        )
        cohort_decisions = tuple(
            item for item in visible_decisions if self._decision_in_cohort(item, selected_cohort)
        )
        selected_decisions = tuple(
            item
            for item in cohort_decisions
            if selected_cohort.start is None or item.decided_at >= selected_cohort.start
        )
        cycle_link_decisions = (
            visible_decisions if selected_cohort.horizon is not None else cohort_decisions
        )
        selected_cycles, cohort_excluded = self._select_cycles(
            ordered_cycles,
            cycle_link_decisions,
            selected_cohort,
            decision_links=decision_links,
        )
        closed_active, closed_excluded = self._closed_active_cycles(
            ordered_cycles,
            selected_cycles,
            cohort_excluded,
        )
        closed_ids = tuple(item.cycle_id for item in closed_active)
        base_excluded = tuple(sorted({*cohort_excluded, *closed_excluded}))
        base_reasons = self._reasons_for_cycles(
            ordered_cycles,
            selected_cycles,
            cohort_excluded,
            closed_excluded,
        )

        closed_metric = self._count_metric(
            "closed_active_trade_cycles",
            numerator=len(closed_active),
            denominator=len(selected_cycles),
            positive_cycle_ids=closed_ids,
            eligible_cycle_ids=closed_ids,
            excluded_cycle_ids=base_excluded,
            exclusion_reasons=base_reasons,
            minimum_sample_size=min_sample,
        )

        pnl_cycles = tuple(item for item in closed_active if item.net_realized_pnl is not None)
        pnl_ids = tuple(item.cycle_id for item in pnl_cycles)
        pnl_missing = tuple(
            item.cycle_id
            for item in closed_active
            if item.net_realized_pnl is None
        )
        pnl_excluded = tuple(sorted({*base_excluded, *pnl_missing}))
        pnl_reasons = self._reason_union(
            base_reasons,
            ("NET_PNL_UNAVAILABLE",) if pnl_missing else (),
        )
        wins = tuple(
            item
            for item in pnl_cycles
            if item.net_realized_pnl is not None and item.net_realized_pnl > 0
        )
        losses = tuple(
            item
            for item in pnl_cycles
            if item.net_realized_pnl is not None and item.net_realized_pnl < 0
        )
        flat = tuple(item for item in pnl_cycles if item.net_realized_pnl == 0)
        win_ids = tuple(item.cycle_id for item in wins)
        loss_ids = tuple(item.cycle_id for item in losses)
        flat_ids = tuple(item.cycle_id for item in flat)

        wins_metric = self._count_metric(
            "wins",
            numerator=len(wins),
            denominator=len(pnl_cycles),
            positive_cycle_ids=win_ids,
            eligible_cycle_ids=pnl_ids,
            excluded_cycle_ids=pnl_excluded,
            exclusion_reasons=pnl_reasons,
            minimum_sample_size=min_sample,
        )
        losses_metric = self._count_metric(
            "losses",
            numerator=len(losses),
            denominator=len(pnl_cycles),
            positive_cycle_ids=loss_ids,
            eligible_cycle_ids=pnl_ids,
            excluded_cycle_ids=pnl_excluded,
            exclusion_reasons=pnl_reasons,
            minimum_sample_size=min_sample,
        )
        flat_metric = self._count_metric(
            "flat",
            numerator=len(flat),
            denominator=len(pnl_cycles),
            positive_cycle_ids=flat_ids,
            eligible_cycle_ids=pnl_ids,
            excluded_cycle_ids=pnl_excluded,
            exclusion_reasons=pnl_reasons,
            minimum_sample_size=min_sample,
        )
        win_rate_metric = self._rate_metric(
            "win_rate",
            numerator=len(wins),
            denominator=len(pnl_cycles),
            positive_cycle_ids=win_ids,
            eligible_cycle_ids=pnl_ids,
            excluded_cycle_ids=pnl_excluded,
            exclusion_reasons=pnl_reasons,
            minimum_sample_size=min_sample,
        )

        avg_win = self._money_average_metric(
            "avg_win",
            wins,
            excluded_cycle_ids=tuple(sorted({*pnl_excluded, *loss_ids, *flat_ids})),
            exclusion_reasons=self._reason_union(pnl_reasons, ("NOT_WIN",)),
            minimum_sample_size=min_sample,
            positive=True,
        )
        avg_loss = self._money_average_metric(
            "avg_loss",
            losses,
            excluded_cycle_ids=tuple(sorted({*pnl_excluded, *win_ids, *flat_ids})),
            exclusion_reasons=self._reason_union(pnl_reasons, ("NOT_LOSS",)),
            minimum_sample_size=min_sample,
            positive=False,
        )
        payoff = self._payoff_metric(
            wins,
            losses,
            pnl_excluded=pnl_excluded,
            pnl_reasons=pnl_reasons,
            minimum_sample_size=min_sample,
        )
        durations = tuple(
            item
            for item in closed_active
            if item.holding_duration_seconds is not None
        )
        duration_missing = tuple(
            item.cycle_id for item in closed_active if item.holding_duration_seconds is None
        )
        median_metric = self._scalar_metric(
            "median_holding_duration",
            numerator=None,
            denominator=len(durations),
            value=_median(
                [
                    item.holding_duration_seconds
                    for item in durations
                    if item.holding_duration_seconds is not None
                ]
            )
            if len(durations) >= min_sample
            else None,
            positive_cycle_ids=tuple(item.cycle_id for item in durations),
            eligible_cycle_ids=tuple(item.cycle_id for item in durations),
            excluded_cycle_ids=tuple(sorted({*base_excluded, *duration_missing})),
            exclusion_reasons=self._reason_union(
                base_reasons,
                ("HOLDING_DURATION_UNAVAILABLE",) if duration_missing else (),
            ),
            minimum_sample_size=min_sample,
        )
        average_metric = self._scalar_metric(
            "average_holding_duration",
            numerator=(
                sum(
                    (
                        Decimal(item.holding_duration_seconds or 0)
                        for item in durations
                    ),
                    Decimal(0),
                )
                if durations
                else Decimal(0)
            ),
            denominator=len(durations),
            value=(
                sum(
                    (
                        Decimal(item.holding_duration_seconds or 0)
                        for item in durations
                    ),
                    Decimal(0),
                )
                / Decimal(len(durations))
                if len(durations) >= min_sample
                else None
            ),
            positive_cycle_ids=tuple(item.cycle_id for item in durations),
            eligible_cycle_ids=tuple(item.cycle_id for item in durations),
            excluded_cycle_ids=tuple(sorted({*base_excluded, *duration_missing})),
            exclusion_reasons=self._reason_union(
                base_reasons,
                ("HOLDING_DURATION_UNAVAILABLE",) if duration_missing else (),
            ),
            minimum_sample_size=min_sample,
            note="Duration is measured from TradeCycle opened_at to closed_at/as_of fact.",
        )
        active_cycles = tuple(
            item
            for item in selected_cycles
            if item.classification is not TradeCycleClassification.CASH_MANAGEMENT
            and item.instrument_id is not None
        )
        entry_attempt_metric = self._entry_attempt_metric(
            active_cycles,
            minimum_sample_size=min_sample,
        )

        plan_metric = self._coverage_metric(
            "plan_coverage",
            closed_active,
            cohort_decisions,
            ordered_findings,
            base_excluded=base_excluded,
            base_reasons=base_reasons,
            minimum_sample_size=min_sample,
            decision_links=decision_links,
            plan_links=plan_links,
            require_any_links=True,
            predicate=lambda item, linked, finding_codes, plan_keys: bool(plan_keys),
        )
        decision_metric = self._coverage_metric(
            "pre_fill_decision_coverage",
            closed_active,
            cohort_decisions,
            ordered_findings,
            base_excluded=base_excluded,
            base_reasons=base_reasons,
            minimum_sample_size=min_sample,
            decision_links=decision_links,
            plan_links=plan_links,
            require_decision_links=True,
            predicate=lambda item, linked, finding_codes, plan_keys: bool(linked)
            and not bool(finding_codes & _NO_DECISION_CODES),
        )
        invalidation_metric = self._coverage_metric(
            "pre_fill_invalidation_proxy",
            closed_active,
            cohort_decisions,
            ordered_findings,
            base_excluded=base_excluded,
            base_reasons=base_reasons,
            minimum_sample_size=min_sample,
            decision_links=decision_links,
            plan_links=plan_links,
            require_any_links=True,
            predicate=lambda item, linked, finding_codes, plan_keys: bool(plan_keys)
            and not bool(finding_codes & _MISSING_INVALIDATION_CODES),
            note=(
                "Proxy only: exact Trade Plan ID/version; not proof that an invalidation fired."
            ),
        )
        reentry_metric = self._same_day_reentry_metric(
            ordered_cycles,
            selected_cycles,
            closed_active,
            base_excluded=base_excluded,
            base_reasons=base_reasons,
            findings=ordered_findings,
            minimum_sample_size=min_sample,
        )
        third_metric = self._third_attempt_metric(
            selected_cycles,
            cohort_decisions,
            ordered_findings,
            cohort_excluded=cohort_excluded,
            minimum_sample_size=min_sample,
            decision_links=decision_links,
            plan_links=plan_links,
        )
        scenario_metrics = self._scenario_metrics(
            selected_decisions,
            selected_cycles,
            minimum_sample_size=min_sample,
            decision_links=decision_links,
        )
        no_action_count, no_action_review = self._no_action_metrics(
            selected_decisions,
            visible_decisions,
            selected_cycles,
            minimum_sample_size=min_sample,
            decision_links=decision_links,
        )
        native_currencies = _unique_sorted(
            item.currency for item in selected_cycles if item.currency is not None
        )
        unsupported_metrics = self._unsupported_metrics(
            closed_active,
            base_excluded=base_excluded,
            native_currencies=native_currencies,
            minimum_sample_size=min_sample,
        )

        return BehaviorSummary(
            closed_active_trade_cycles=closed_metric,
            wins=wins_metric,
            losses=losses_metric,
            flat=flat_metric,
            win_rate=win_rate_metric,
            avg_win=avg_win,
            avg_loss=avg_loss,
            payoff_ratio=payoff,
            average_holding_duration=average_metric,
            median_holding_duration=median_metric,
            turnover=unsupported_metrics["turnover"],
            plan_coverage=plan_metric,
            pre_fill_decision_coverage=decision_metric,
            pre_fill_invalidation_proxy=invalidation_metric,
            invalidation_adherence=unsupported_metrics["invalidation_adherence"],
            same_day_reentry=reentry_metric,
            entry_attempt_count=entry_attempt_metric,
            same_entry_logic_attempt_count=unsupported_metrics[
                "same_entry_logic_attempt_count"
            ],
            third_attempt_without_new_plan=third_metric,
            add_confirmation_risk_control=unsupported_metrics[
                "add_confirmation_risk_control"
            ],
            planned_holding_period_mismatch=unsupported_metrics[
                "planned_holding_period_mismatch"
            ],
            scenario_action_distribution=scenario_metrics,
            no_action_count=no_action_count,
            no_action_review_completion=no_action_review,
            cohort=selected_cohort,
            cohort_cycle_ids=tuple(item.cycle_id for item in selected_cycles),
            cohort_excluded_cycle_ids=tuple(sorted(cohort_excluded)),
            cohort_exclusion_reasons=_unique_sorted(cohort_excluded.values()),
            native_currencies=native_currencies,
            algorithm_version=self.algorithm_version,
        )

    summarize = calculate

    @staticmethod
    def _validate_minimum_sample_size(value: int) -> int:
        if type(value) is not int or value < 0:
            raise ValueError("minimum_sample_size must be a nonnegative int")
        return value

    @staticmethod
    def _cohort(
        cohort: BehaviorCohort | None,
        *,
        strategy_code: str | None,
        strategy_version: str | None,
        horizon: str | None,
        instrument_ids: tuple[str, ...],
        instrument_id: str | None,
        currency: str | None,
        classifications: tuple[TradeCycleClassification, ...],
        start: datetime | None,
        end: datetime | None,
    ) -> BehaviorCohort:
        base = cohort or BehaviorCohort()
        selected_instruments = instrument_ids or base.instrument_ids
        if instrument_id is not None:
            selected_instruments = (instrument_id,)
        return BehaviorCohort(
            strategy_code=strategy_code if strategy_code is not None else base.strategy_code,
            strategy_version=(
                strategy_version if strategy_version is not None else base.strategy_version
            ),
            horizon=horizon if horizon is not None else base.horizon,
            instrument_ids=tuple(selected_instruments),
            currency=currency if currency is not None else base.currency,
            classifications=classifications or base.classifications,
            start=start if start is not None else base.start,
            end=end if end is not None else base.end,
        )

    @staticmethod
    def _decision_in_cohort(decision: DecisionRecord, cohort: BehaviorCohort) -> bool:
        if cohort.strategy_code is not None and decision.strategy_code != cohort.strategy_code:
            return False
        if (
            cohort.strategy_version is not None
            and decision.strategy_version != cohort.strategy_version
        ):
            return False
        if cohort.horizon is not None and _decision_horizon(decision) != cohort.horizon:
            return False
        return not (
            cohort.instrument_ids
            and decision.primary_instrument_id not in {*cohort.instrument_ids, None}
        )

    @classmethod
    def _select_cycles(
        cls,
        cycles: tuple[TradeCycle, ...],
        decisions: tuple[DecisionRecord, ...],
        cohort: BehaviorCohort,
        *,
        decision_links: dict[str, tuple[str, ...]] | None,
    ) -> tuple[tuple[TradeCycle, ...], dict[str, str]]:
        selected: list[TradeCycle] = []
        excluded: dict[str, str] = {}
        for cycle in cycles:
            cycle_at = cycle.closed_at or cycle.opened_at
            if cycle_at is None and (cohort.start is not None or cohort.end is not None):
                excluded[cycle.cycle_id] = "COHORT_DATE_UNAVAILABLE"
                continue
            if cohort.start is not None and cycle_at is not None and cycle_at < cohort.start:
                excluded[cycle.cycle_id] = "COHORT_DATE_BEFORE_START"
                continue
            if cohort.end is not None and cycle_at is not None and cycle_at > cohort.end:
                excluded[cycle.cycle_id] = "COHORT_DATE_AFTER_END"
                continue
            if cohort.instrument_ids and cycle.instrument_id not in cohort.instrument_ids:
                excluded[cycle.cycle_id] = "COHORT_INSTRUMENT_MISMATCH"
                continue
            if cohort.currency is not None and cycle.currency != cohort.currency:
                excluded[cycle.cycle_id] = "COHORT_CURRENCY_MISMATCH"
                continue
            if cohort.classifications and cycle.classification not in cohort.classifications:
                excluded[cycle.cycle_id] = "COHORT_CLASSIFICATION_MISMATCH"
                continue
            if (
                (cohort.strategy_code is not None or cohort.strategy_version is not None)
                and decision_links is None
            ):
                excluded[cycle.cycle_id] = "COHORT_LINK_FACT_UNAVAILABLE"
                continue
            linked = cls._linked_decisions(cycle, decisions, decision_links)
            if (
                (cohort.strategy_code is not None or cohort.strategy_version is not None)
                and not linked
            ):
                excluded[cycle.cycle_id] = "COHORT_STRATEGY_MISMATCH"
                continue
            if cohort.horizon is not None and decision_links is None:
                excluded[cycle.cycle_id] = "COHORT_LINK_FACT_UNAVAILABLE"
                continue
            if cohort.horizon is not None and not linked:
                excluded[cycle.cycle_id] = "COHORT_HORIZON_MISMATCH"
                continue
            if cohort.strategy_code is not None and not any(
                item.strategy_code == cohort.strategy_code for item in linked
            ):
                excluded[cycle.cycle_id] = "COHORT_STRATEGY_MISMATCH"
                continue
            if cohort.strategy_version is not None and not any(
                item.strategy_version == cohort.strategy_version for item in linked
            ):
                excluded[cycle.cycle_id] = "COHORT_STRATEGY_MISMATCH"
                continue
            if cohort.horizon is not None and not any(
                _decision_horizon(item) == cohort.horizon for item in linked
            ):
                excluded[cycle.cycle_id] = (
                    "COHORT_HORIZON_UNAVAILABLE"
                    if all(_decision_horizon(item) is None for item in linked)
                    else "COHORT_HORIZON_MISMATCH"
                )
                continue
            selected.append(cycle)
        return tuple(selected), excluded

    @staticmethod
    def _normalize_decision_links(
        links: Mapping[str, tuple[str, ...]] | None,
    ) -> dict[str, tuple[str, ...]] | None:
        if links is None:
            return None
        return {
            cycle_id: tuple(sorted(set(decision_ids)))
            for cycle_id, decision_ids in links.items()
        }

    @staticmethod
    def _normalize_plan_links(
        links: Mapping[str, tuple[tuple[str, int], ...]] | None,
    ) -> dict[str, tuple[tuple[str, int], ...]] | None:
        if links is None:
            return None
        return {
            cycle_id: tuple(sorted(set(plan_refs)))
            for cycle_id, plan_refs in links.items()
        }

    @staticmethod
    def _annotation_links(
        cycles: tuple[TradeCycle, ...],
        annotations: tuple[ActivityAnnotation, ...] | None,
    ) -> tuple[dict[str, tuple[str, ...]] | None, dict[str, tuple[tuple[str, int], ...]] | None]:
        if annotations is None:
            return None, None
        latest: dict[tuple[object, str, str], ActivityAnnotation] = {}
        for annotation in annotations:
            key = annotation.transaction_key
            current = latest.get(key)
            if current is None or (
                annotation.version,
                annotation.annotation_id,
            ) > (current.version, current.annotation_id):
                latest[key] = annotation
        decision_links: dict[str, tuple[str, ...]] = {}
        plan_links: dict[str, tuple[tuple[str, int], ...]] = {}
        for cycle in cycles:
            decisions: set[str] = set()
            plans: set[tuple[str, int]] = set()
            for activity_id in cycle.activity_ids:
                matched_annotation = latest.get(
                    (cycle.provider, cycle.account_ref, activity_id)
                )
                if (
                    matched_annotation is None
                    or matched_annotation.status
                    is not ActivityAnnotationStatus.LINKED_DECISION_PLAN
                ):
                    continue
                if matched_annotation.decision_id is not None:
                    decisions.add(matched_annotation.decision_id)
                if (
                    matched_annotation.trade_plan_id is not None
                    and matched_annotation.trade_plan_version is not None
                ):
                    plans.add(
                        (
                            matched_annotation.trade_plan_id,
                            matched_annotation.trade_plan_version,
                        )
                    )
            decision_links[cycle.cycle_id] = tuple(sorted(decisions))
            plan_links[cycle.cycle_id] = tuple(sorted(plans))
        return decision_links, plan_links

    @staticmethod
    def _linked_decisions(
        cycle: TradeCycle,
        decisions: tuple[DecisionRecord, ...],
        decision_links: dict[str, tuple[str, ...]] | None,
    ) -> tuple[DecisionRecord, ...]:
        if decision_links is None:
            return ()
        by_id = {item.decision_id: item for item in decisions}
        linked = tuple(
            by_id[decision_id]
            for decision_id in decision_links.get(cycle.cycle_id, ())
            if decision_id in by_id
        )
        if cycle.opened_at is None:
            return ()
        return tuple(item for item in linked if item.decided_at <= cycle.opened_at)

    @classmethod
    def _closed_active_cycles(
        cls,
        all_cycles: tuple[TradeCycle, ...],
        selected_cycles: tuple[TradeCycle, ...],
        cohort_excluded: dict[str, str],
    ) -> tuple[tuple[TradeCycle, ...], dict[str, str]]:
        excluded = dict(cohort_excluded)
        selected_ids = {item.cycle_id for item in selected_cycles}
        for cycle in all_cycles:
            if cycle.cycle_id not in selected_ids:
                continue
            if cycle.classification is TradeCycleClassification.CASH_MANAGEMENT:
                excluded[cycle.cycle_id] = "CASH_MANAGEMENT"
            elif cycle.instrument_id is None:
                excluded[cycle.cycle_id] = "MISSING_INSTRUMENT"
            elif cycle.status is TradeCycleStatus.OPEN:
                excluded[cycle.cycle_id] = "OPEN_CYCLE"
            elif cycle.status is TradeCycleStatus.UNRESOLVED:
                excluded[cycle.cycle_id] = "UNRESOLVED_CYCLE"
        return (
            tuple(
                item
                for item in selected_cycles
                if item.cycle_id not in excluded
                and item.status is TradeCycleStatus.CLOSED
                and item.classification is not TradeCycleClassification.CASH_MANAGEMENT
                and item.instrument_id is not None
            ),
            excluded,
        )

    @staticmethod
    def _reasons_for_cycles(
        all_cycles: tuple[TradeCycle, ...],
        selected_cycles: tuple[TradeCycle, ...],
        cohort_excluded: dict[str, str],
        other_excluded: dict[str, str],
    ) -> tuple[str, ...]:
        del selected_cycles
        del all_cycles
        return _unique_sorted((*cohort_excluded.values(), *other_excluded.values()))

    @staticmethod
    def _reason_union(*values: Iterable[str]) -> tuple[str, ...]:
        return _unique_sorted(code for group in values for code in group)

    @classmethod
    def _finding_codes(
        cls,
        cycle: TradeCycle,
        findings: tuple[TradeRetroFinding, ...],
    ) -> set[str]:
        activity_ids = set(cycle.activity_ids)
        return {
            item.code
            for item in findings
            if (
                bool(activity_ids.intersection(item.transaction_ids))
                if item.transaction_ids
                else item.instrument_id == cycle.instrument_id
            )
        }

    @classmethod
    def _count_metric(
        cls,
        name: str,
        *,
        numerator: int,
        denominator: int,
        positive_cycle_ids: tuple[str, ...],
        eligible_cycle_ids: tuple[str, ...],
        excluded_cycle_ids: tuple[str, ...],
        exclusion_reasons: tuple[str, ...],
        minimum_sample_size: int,
    ) -> BehaviorMetric:
        return BehaviorMetric(
            name=name,
            numerator=numerator,
            denominator=denominator,
            value=numerator,
            excluded_count=len(excluded_cycle_ids),
            exclusion_reasons=exclusion_reasons,
            cycle_ids=_unique_sorted(positive_cycle_ids),
            eligible_cycle_ids=_unique_sorted(eligible_cycle_ids),
            excluded_cycle_ids=_unique_sorted(excluded_cycle_ids),
            sample_sufficient=denominator >= minimum_sample_size,
            minimum_sample_size=minimum_sample_size,
        )

    @classmethod
    def _rate_metric(
        cls,
        name: str,
        *,
        numerator: int,
        denominator: int,
        positive_cycle_ids: tuple[str, ...],
        eligible_cycle_ids: tuple[str, ...],
        excluded_cycle_ids: tuple[str, ...],
        exclusion_reasons: tuple[str, ...],
        minimum_sample_size: int,
    ) -> BehaviorMetric:
        return BehaviorMetric(
            name=name,
            numerator=numerator,
            denominator=denominator,
            value=_safe_ratio(numerator, denominator, minimum=minimum_sample_size),
            excluded_count=len(excluded_cycle_ids),
            exclusion_reasons=exclusion_reasons,
            cycle_ids=_unique_sorted(positive_cycle_ids),
            eligible_cycle_ids=_unique_sorted(eligible_cycle_ids),
            excluded_cycle_ids=_unique_sorted(excluded_cycle_ids),
            sample_sufficient=denominator >= minimum_sample_size,
            minimum_sample_size=minimum_sample_size,
        )

    @classmethod
    def _scalar_metric(
        cls,
        name: str,
        *,
        numerator: BehaviorScalar,
        denominator: int,
        value: BehaviorScalar,
        positive_cycle_ids: tuple[str, ...],
        eligible_cycle_ids: tuple[str, ...],
        excluded_cycle_ids: tuple[str, ...],
        exclusion_reasons: tuple[str, ...],
        minimum_sample_size: int,
        native_currencies: tuple[str, ...] = (),
        note: str | None = None,
    ) -> BehaviorMetric:
        return BehaviorMetric(
            name=name,
            numerator=numerator,
            denominator=denominator,
            value=value,
            excluded_count=len(excluded_cycle_ids),
            exclusion_reasons=exclusion_reasons,
            cycle_ids=_unique_sorted(positive_cycle_ids),
            eligible_cycle_ids=_unique_sorted(eligible_cycle_ids),
            excluded_cycle_ids=_unique_sorted(excluded_cycle_ids),
            sample_sufficient=denominator >= minimum_sample_size,
            minimum_sample_size=minimum_sample_size,
            native_currencies=native_currencies,
            note=note,
        )

    @classmethod
    def _entry_attempt_metric(
        cls,
        cycles: tuple[TradeCycle, ...],
        *,
        minimum_sample_size: int,
    ) -> BehaviorMetric:
        attempts = sum(
            (item.opening_count + item.add_count for item in cycles),
            0,
        )
        denominator = len(cycles)
        value = (
            Decimal(attempts) / Decimal(denominator)
            if denominator >= minimum_sample_size and denominator
            else None
        )
        return cls._scalar_metric(
            "entry_attempt_count",
            numerator=attempts,
            denominator=denominator,
            value=value,
            positive_cycle_ids=tuple(item.cycle_id for item in cycles),
            eligible_cycle_ids=tuple(item.cycle_id for item in cycles),
            excluded_cycle_ids=(),
            exclusion_reasons=(),
            minimum_sample_size=minimum_sample_size,
            note=(
                "Observed opening_count + add_count; no same-entry-logic identity "
                "is inferred."
            ),
        )

    @classmethod
    def _unsupported_metrics(
        cls,
        cycles: tuple[TradeCycle, ...],
        *,
        base_excluded: tuple[str, ...],
        native_currencies: tuple[str, ...],
        minimum_sample_size: int,
    ) -> dict[str, BehaviorMetric]:
        specs = {
            "turnover": (
                "TURNOVER_NOT_SUPPORTED_NO_TRADE_NOTIONAL_FACT",
                "TradeCycle retains activity IDs and counts, not traded notional; "
                "turnover is unavailable.",
            ),
            "invalidation_adherence": (
                "INVALIDATION_ADHERENCE_NOT_SUPPORTED_NO_EXIT_REASON_FACT",
                "Existing facts do not identify whether an exit was caused by an "
                "invalidation condition.",
            ),
            "same_entry_logic_attempt_count": (
                "ENTRY_LOGIC_NOT_SUPPORTED_NO_LOGIC_ID",
                "TradeCycle add counts are observable, but no exact entry-logic "
                "identity is stored.",
            ),
            "add_confirmation_risk_control": (
                "ADD_RISK_CONTROL_NOT_SUPPORTED_NO_COMBINED_RISK_FACT",
                "Decision and Cycle facts do not prove a new ADD confirmation or "
                "unchanged combined risk.",
            ),
            "planned_holding_period_mismatch": (
                "PLAN_HORIZON_NOT_SUPPORTED_NO_PLAN_HORIZON_FACT",
                "Current exact inputs do not include a versioned planned holding "
                "horizon to compare.",
            ),
        }
        return {
            name: cls._unavailable_metric(
                name,
                cycles,
                base_excluded=base_excluded,
                reason=reason,
                native_currencies=native_currencies,
                note=note,
                minimum_sample_size=minimum_sample_size,
            )
            for name, (reason, note) in specs.items()
        }

    @classmethod
    def _money_average_metric(
        cls,
        name: str,
        cycles: tuple[TradeCycle, ...],
        *,
        excluded_cycle_ids: tuple[str, ...],
        exclusion_reasons: tuple[str, ...],
        minimum_sample_size: int,
        positive: bool,
    ) -> BehaviorMetric:
        valid_cycles = tuple(item for item in cycles if item.currency is not None)
        ids = tuple(item.cycle_id for item in valid_cycles)
        currencies = _unique_sorted(
            item.currency for item in valid_cycles if item.currency is not None
        )
        missing_currency = tuple(item.cycle_id for item in cycles if item.currency is None)
        all_excluded = tuple(sorted({*excluded_cycle_ids, *missing_currency}))
        reasons = cls._reason_union(
            exclusion_reasons,
            ("NATIVE_CURRENCY_UNAVAILABLE",) if missing_currency else (),
        )
        if len(currencies) != 1:
            return cls._scalar_metric(
                name,
                numerator=None,
                denominator=len(valid_cycles),
                value=None,
                positive_cycle_ids=ids,
                eligible_cycle_ids=ids,
                excluded_cycle_ids=all_excluded,
                exclusion_reasons=cls._reason_union(
                    reasons,
                    ("MULTIPLE_NATIVE_CURRENCIES",) if len(currencies) > 1 else (),
                ),
                minimum_sample_size=minimum_sample_size,
                native_currencies=currencies,
                note="Native-currency values are not converted or summed across currencies.",
            )
        values = [
            item.net_realized_pnl
            for item in valid_cycles
            if item.net_realized_pnl is not None
        ]
        total = sum(values, Decimal(0))
        value = (
            total / Decimal(len(values)) if len(values) >= minimum_sample_size and values else None
        )
        return cls._scalar_metric(
            name,
            numerator=total,
            denominator=len(values),
            value=value,
            positive_cycle_ids=ids,
            eligible_cycle_ids=ids,
            excluded_cycle_ids=all_excluded,
            exclusion_reasons=reasons,
            minimum_sample_size=minimum_sample_size,
            native_currencies=currencies,
        )

    @classmethod
    def _payoff_metric(
        cls,
        wins: tuple[TradeCycle, ...],
        losses: tuple[TradeCycle, ...],
        *,
        pnl_excluded: tuple[str, ...],
        pnl_reasons: tuple[str, ...],
        minimum_sample_size: int,
    ) -> BehaviorMetric:
        all_cycles = (*wins, *losses)
        valid_cycles = tuple(item for item in all_cycles if item.currency is not None)
        missing_currency = tuple(item.cycle_id for item in all_cycles if item.currency is None)
        currencies = _unique_sorted(
            item.currency for item in valid_cycles if item.currency is not None
        )
        ids = tuple(item.cycle_id for item in valid_cycles)
        reasons = cls._reason_union(pnl_reasons, ("FLAT_OR_NON_PAYOFF",))
        valid_wins = tuple(item for item in wins if item.currency is not None)
        valid_losses = tuple(item for item in losses if item.currency is not None)
        if len(currencies) != 1 or missing_currency:
            return cls._scalar_metric(
                "payoff_ratio",
                numerator=None,
                denominator=len(valid_losses),
                value=None,
                positive_cycle_ids=tuple(item.cycle_id for item in valid_wins),
                eligible_cycle_ids=ids,
                excluded_cycle_ids=tuple(sorted({*pnl_excluded, *missing_currency})),
                exclusion_reasons=cls._reason_union(
                    reasons,
                    ("NATIVE_CURRENCY_UNAVAILABLE",) if missing_currency else (),
                    ("MULTIPLE_NATIVE_CURRENCIES",) if len(currencies) > 1 else (),
                ),
                minimum_sample_size=minimum_sample_size,
                native_currencies=currencies,
                note="Payoff ratio is undefined without a single native currency and both sides.",
            )
        gross_wins = sum(
            (item.net_realized_pnl or Decimal(0) for item in valid_wins),
            Decimal(0),
        )
        gross_losses = sum(
            (abs(item.net_realized_pnl or Decimal(0)) for item in valid_losses),
            Decimal(0),
        )
        average_win = (
            gross_wins / Decimal(len(valid_wins)) if valid_wins else Decimal(0)
        )
        average_loss = (
            gross_losses / Decimal(len(valid_losses)) if valid_losses else Decimal(0)
        )
        value = (
            average_win / average_loss
            if len(valid_wins) >= minimum_sample_size
            and len(valid_losses) >= minimum_sample_size
            and average_loss != 0
            else None
        )
        return cls._scalar_metric(
            "payoff_ratio",
            numerator=gross_wins,
            denominator=len(valid_losses),
            value=value,
            positive_cycle_ids=tuple(item.cycle_id for item in wins),
            eligible_cycle_ids=ids,
            excluded_cycle_ids=pnl_excluded,
            exclusion_reasons=pnl_reasons,
            minimum_sample_size=minimum_sample_size,
            native_currencies=currencies,
            note=(
                "Payoff ratio is average winning-cycle P/L divided by absolute "
                "average losing-cycle P/L."
            ),
        )

    @classmethod
    def _coverage_metric(
        cls,
        name: str,
        cycles: tuple[TradeCycle, ...],
        decisions: tuple[DecisionRecord, ...],
        findings: tuple[TradeRetroFinding, ...],
        *,
        base_excluded: tuple[str, ...],
        base_reasons: tuple[str, ...],
        minimum_sample_size: int,
        predicate: object,
        note: str | None = None,
        decision_links: dict[str, tuple[str, ...]] | None,
        plan_links: dict[str, tuple[tuple[str, int], ...]] | None,
        require_decision_links: bool = False,
        require_any_links: bool = False,
    ) -> BehaviorMetric:
        if (require_decision_links and decision_links is None) or (
            require_any_links and decision_links is None and plan_links is None
        ):
            return cls._unavailable_metric(
                name,
                cycles,
                base_excluded=base_excluded,
                reason=(
                    "PREFILL_DECISION_LINK_FACT_UNAVAILABLE"
                    if require_decision_links
                    else "EXACT_PLAN_LINK_FACT_UNAVAILABLE"
                ),
                native_currencies=tuple(
                    sorted(
                        {
                            item.currency
                            for item in cycles
                            if item.currency is not None
                        }
                    )
                ),
                note=(
                    "No exact cycle-to-Decision annotation was supplied; temporal and "
                    "instrument proximity is not treated as a link."
                    if require_decision_links
                    else "No exact cycle-to-Plan/Decision annotation was supplied."
                ),
                availability=BehaviorMetricAvailability.UNAVAILABLE,
                minimum_sample_size=minimum_sample_size,
            )
        positive: list[str] = []
        eligible = tuple(item.cycle_id for item in cycles)
        for cycle in cycles:
            linked = cls._linked_decisions(cycle, decisions, decision_links)
            plan_keys = (
                tuple(
                    f"{plan_id}:{version}"
                    for plan_id, version in plan_links.get(cycle.cycle_id, ())
                )
                if plan_links is not None
                else tuple(plan_key for item in linked if (plan_key := _plan_key(item)))
            )
            codes = cls._finding_codes(cycle, findings)
            if predicate(cycle, linked, codes, plan_keys):  # type: ignore[operator]
                positive.append(cycle.cycle_id)
        return (
            cls._rate_metric(
                name,
                numerator=len(positive),
                denominator=len(cycles),
                positive_cycle_ids=tuple(positive),
                eligible_cycle_ids=eligible,
                excluded_cycle_ids=base_excluded,
                exclusion_reasons=base_reasons,
                minimum_sample_size=minimum_sample_size,
            )
            if note is None
            else BehaviorMetric(
                name=name,
                numerator=len(positive),
                denominator=len(cycles),
                value=_safe_ratio(len(positive), len(cycles), minimum=minimum_sample_size),
                excluded_count=len(base_excluded),
                exclusion_reasons=base_reasons,
                cycle_ids=_unique_sorted(positive),
                eligible_cycle_ids=_unique_sorted(eligible),
                excluded_cycle_ids=_unique_sorted(base_excluded),
                sample_sufficient=len(cycles) >= minimum_sample_size,
                minimum_sample_size=minimum_sample_size,
                note=note,
            )
        )

    @classmethod
    def _unavailable_metric(
        cls,
        name: str,
        cycles: tuple[TradeCycle, ...],
        *,
        base_excluded: tuple[str, ...],
        reason: str,
        native_currencies: tuple[str, ...] = (),
        note: str,
        availability: BehaviorMetricAvailability = BehaviorMetricAvailability.NOT_SUPPORTED,
        minimum_sample_size: int = 1,
    ) -> BehaviorMetric:
        excluded_ids = tuple(
            sorted({*base_excluded, *(item.cycle_id for item in cycles)})
        )
        return BehaviorMetric(
            name=name,
            numerator=None,
            denominator=None,
            value=None,
            excluded_count=len(excluded_ids),
            exclusion_reasons=(reason,),
            excluded_cycle_ids=excluded_ids,
            sample_sufficient=False,
            minimum_sample_size=minimum_sample_size,
            native_currencies=native_currencies,
            note=note,
            availability=availability,
            unavailable_reason=reason,
        )

    @classmethod
    def _same_day_reentry_metric(
        cls,
        all_cycles: tuple[TradeCycle, ...],
        selected_cycles: tuple[TradeCycle, ...],
        closed_active: tuple[TradeCycle, ...],
        *,
        base_excluded: tuple[str, ...],
        base_reasons: tuple[str, ...],
        findings: tuple[TradeRetroFinding, ...],
        minimum_sample_size: int,
    ) -> BehaviorMetric:
        parent_by_id = {item.cycle_id: item for item in all_cycles}
        positives: list[str] = []
        for cycle in closed_active:
            parent = parent_by_id.get(cycle.reentry_of_cycle_id or "")
            same_day = (
                cycle.opened_at is not None
                and parent is not None
                and parent.closed_at is not None
                and cycle.opened_at.date() == parent.closed_at.date()
            )
            if same_day or bool(cls._finding_codes(cycle, findings) & _REENTRY_CODES):
                positives.append(cycle.cycle_id)
        return cls._rate_metric(
            "same_day_reentry",
            numerator=len(positives),
            denominator=len(closed_active),
            positive_cycle_ids=tuple(positives),
            eligible_cycle_ids=tuple(item.cycle_id for item in closed_active),
            excluded_cycle_ids=base_excluded,
            exclusion_reasons=base_reasons,
            minimum_sample_size=minimum_sample_size,
        )

    @classmethod
    def _third_attempt_metric(
        cls,
        selected_cycles: tuple[TradeCycle, ...],
        decisions: tuple[DecisionRecord, ...],
        findings: tuple[TradeRetroFinding, ...],
        *,
        cohort_excluded: dict[str, str],
        minimum_sample_size: int,
        decision_links: dict[str, tuple[str, ...]] | None,
        plan_links: dict[str, tuple[tuple[str, int], ...]] | None,
    ) -> BehaviorMetric:
        active = tuple(
            item
            for item in selected_cycles
            if item.classification is not TradeCycleClassification.CASH_MANAGEMENT
            and item.instrument_id is not None
        )
        below = tuple(item.cycle_id for item in active if item.opening_count + item.add_count < 3)
        candidates = tuple(item for item in active if item.opening_count + item.add_count >= 3)
        if decision_links is None and plan_links is None:
            return cls._unavailable_metric(
                "third_attempt_without_new_plan",
                candidates,
                base_excluded=tuple(sorted(cohort_excluded)),
                reason="EXACT_PLAN_LINK_FACT_UNAVAILABLE",
                note=(
                    "Third-attempt behavior requires exact cycle Decision/Plan links; "
                    "missing links are not treated as a plan violation."
                ),
                availability=BehaviorMetricAvailability.UNAVAILABLE,
            )
        positives: list[str] = []
        for cycle in candidates:
            codes = cls._finding_codes(cycle, findings)
            linked = cls._linked_decisions(cycle, decisions, decision_links)
            decision_plan_keys = {
                plan_key
                for item in linked
                if (plan_key := _plan_key(item)) is not None
            }
            explicit_plan_keys = {
                f"{plan_id}:{version}"
                for plan_id, version in (
                    plan_links.get(cycle.cycle_id, ()) if plan_links is not None else ()
                )
            }
            plan_keys = explicit_plan_keys or decision_plan_keys
            if codes & _THIRD_ATTEMPT_CODES or len(plan_keys) <= 1:
                positives.append(cycle.cycle_id)
        excluded_cycle_ids = tuple(
            sorted({*cohort_excluded.keys(), *below})
        )
        reasons = cls._reason_union(
            tuple(cohort_excluded.values()),
            ("ATTEMPTS_BELOW_THIRD",) if below else (),
        )
        return cls._rate_metric(
            "third_attempt_without_new_plan",
            numerator=len(positives),
            denominator=len(candidates),
            positive_cycle_ids=tuple(positives),
            eligible_cycle_ids=tuple(item.cycle_id for item in candidates),
            excluded_cycle_ids=excluded_cycle_ids,
            exclusion_reasons=reasons,
            minimum_sample_size=minimum_sample_size,
        )

    @classmethod
    def _scenario_metrics(
        cls,
        decisions: tuple[DecisionRecord, ...],
        cycles: tuple[TradeCycle, ...],
        *,
        minimum_sample_size: int,
        decision_links: dict[str, tuple[str, ...]] | None,
    ) -> tuple[BehaviorMetric, ...]:
        with_scenario = tuple(item for item in decisions if item.scenario is not None)
        missing = tuple(item.decision_id for item in decisions if item.scenario is None)
        by_bucket: dict[tuple[str, str], list[DecisionRecord]] = defaultdict(list)
        for item in with_scenario:
            assert item.scenario is not None
            by_bucket[(item.scenario.value, item.decision_type.value)].append(item)
        union_cycle_ids = cls._cycle_ids_for_decisions(
            with_scenario,
            cycles,
            decision_links,
        )
        distribution = BehaviorMetric(
            name="scenario_action_distribution",
            numerator=len(with_scenario),
            denominator=len(decisions),
            value=len(with_scenario),
            excluded_count=len(missing),
            exclusion_reasons=("SCENARIO_UNAVAILABLE",) if missing else (),
            cycle_ids=union_cycle_ids,
            eligible_decision_ids=tuple(item.decision_id for item in with_scenario),
            excluded_decision_ids=missing,
            sample_sufficient=len(decisions) >= minimum_sample_size,
            minimum_sample_size=minimum_sample_size,
        )
        buckets: list[BehaviorMetric] = [distribution]
        for (scenario, action), bucket in sorted(by_bucket.items()):
            bucket_ids = tuple(item.decision_id for item in bucket)
            buckets.append(
                BehaviorMetric(
                    name=f"scenario_action:{scenario}:{action}",
                    numerator=len(bucket),
                    denominator=len(with_scenario),
                    value=len(bucket),
                    excluded_count=len(missing),
                    exclusion_reasons=("SCENARIO_UNAVAILABLE",) if missing else (),
                    cycle_ids=cls._cycle_ids_for_decisions(
                        tuple(bucket),
                        cycles,
                        decision_links,
                    ),
                    eligible_decision_ids=tuple(item.decision_id for item in with_scenario),
                    decision_ids=bucket_ids,
                    excluded_decision_ids=missing,
                    sample_sufficient=len(with_scenario) >= minimum_sample_size,
                    minimum_sample_size=minimum_sample_size,
                    note="Scenario/action distribution; no aggregate discipline score.",
                )
            )
        return tuple(buckets)

    @classmethod
    def _cycle_ids_for_decisions(
        cls,
        decisions: tuple[DecisionRecord, ...],
        cycles: tuple[TradeCycle, ...],
        decision_links: dict[str, tuple[str, ...]] | None,
    ) -> tuple[str, ...]:
        if decision_links is None:
            return ()
        decision_ids = {item.decision_id for item in decisions}
        ids: set[str] = set()
        for cycle in cycles:
            if decision_ids.intersection(decision_links.get(cycle.cycle_id, ())):
                ids.add(cycle.cycle_id)
        return tuple(sorted(ids))

    @classmethod
    def _no_action_metrics(
        cls,
        decisions: tuple[DecisionRecord, ...],
        all_decisions: tuple[DecisionRecord, ...],
        cycles: tuple[TradeCycle, ...],
        *,
        minimum_sample_size: int,
        decision_links: dict[str, tuple[str, ...]] | None,
    ) -> tuple[BehaviorMetric, BehaviorMetric]:
        no_actions = tuple(
            item for item in decisions if item.decision_type is DecisionType.NO_ACTION
        )
        no_action_ids = tuple(item.decision_id for item in no_actions)
        cycle_ids = cls._cycle_ids_for_decisions(no_actions, cycles, decision_links)
        count = BehaviorMetric(
            name="no_action_count",
            numerator=len(no_actions),
            denominator=len(decisions),
            value=len(no_actions),
            excluded_count=0,
            exclusion_reasons=(),
            cycle_ids=cycle_ids,
            decision_ids=no_action_ids,
            eligible_decision_ids=tuple(item.decision_id for item in decisions),
            sample_sufficient=len(decisions) >= minimum_sample_size,
            minimum_sample_size=minimum_sample_size,
        )
        with_review_due = tuple(item for item in no_actions if item.review_due_at is not None)
        missing_due = tuple(item.decision_id for item in no_actions if item.review_due_at is None)
        completed = tuple(
            item
            for item in with_review_due
            if any(
                candidate.supersedes_decision_id == item.decision_id
                and candidate.decided_at >= item.decided_at
                for candidate in all_decisions
            )
        )
        review = BehaviorMetric(
            name="no_action_review_completion",
            numerator=len(completed),
            denominator=len(with_review_due),
            value=_safe_ratio(len(completed), len(with_review_due), minimum=minimum_sample_size),
            excluded_count=len(missing_due),
            exclusion_reasons=("REVIEW_DUE_UNAVAILABLE",) if missing_due else (),
            cycle_ids=cls._cycle_ids_for_decisions(
                with_review_due,
                cycles,
                decision_links,
            ),
            eligible_decision_ids=tuple(item.decision_id for item in with_review_due),
            decision_ids=tuple(item.decision_id for item in completed),
            excluded_decision_ids=missing_due,
            sample_sufficient=len(with_review_due) >= minimum_sample_size,
            minimum_sample_size=minimum_sample_size,
        )
        return count, review


__all__ = ["BehaviorSummaryCalculator"]
