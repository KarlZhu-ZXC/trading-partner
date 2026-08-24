"""Compact account and portfolio operation adapters."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import ValidationError

from application.dto.account_transactions import (
    AccountGetActivityCoverageInput,
    AccountGetTransactionsInput,
    TradeCycleQueryInput,
)
from application.dto.behavior import BehaviorSummaryQueryInput
from application.dto.performance import PerformanceSeriesQueryInput
from application.dto.performance_attribution import PerformanceAttributionInput
from application.dto.portfolio import (
    AccountGetPositionsInput,
    AccountGetSnapshotInput,
    PortfolioAnalyzeInput,
    PortfolioSimulateAdditionInput,
)
from application.dto.tool_envelope import ToolEnvelope, WarningInfo
from application.dto.trade_cycle_overrides import TradeCycleOverrideAppendInput
from application.dto.trade_retro import TradeRetroHistoryInput
from bootstrap import ApplicationContainer
from domain.common.enums import Freshness, VendorId
from domain.common.ids import EntityIdPrefix
from interfaces.mcp.validation import unexpected_failure as _unexpected_failure


def build_portfolio_adapters(container: ApplicationContainer) -> SimpleNamespace:
    """Build compact account and portfolio operation adapters."""

    # --------------------------------------------------- Phase 1I account/portfolio
    async def account_get(
        operation: Literal["positions", "refresh", "transactions"] = "positions",
        providers: tuple[str, ...] = (),
        as_of: datetime | None = None,
        snapshot_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Read durable positions, explicitly refresh accounts, or fetch transactions."""
        if operation == "positions":
            return account_get_positions(snapshot_id)
        if operation == "transactions":
            return await account_get_transactions(providers, start, end, limit)
        if operation != "refresh":
            raise ValueError("operation must be positions, refresh, or transactions")
        try:
            inp = AccountGetSnapshotInput.model_validate({"providers": providers, "as_of": as_of})
            envelope = await container.services.portfolio.get_account_snapshot(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def account_get_positions(snapshot_id: str | None = None) -> dict[str, Any]:
        """Return positions from one snapshot or the latest durable accounts."""
        try:
            inp = AccountGetPositionsInput.model_validate({"snapshot_id": snapshot_id})
            envelope = container.services.portfolio.get_account_positions(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def portfolio_analyze(
        account_snapshot_ids: tuple[str, ...] = (),
        base_currency: str = "USD",
    ) -> dict[str, Any]:
        """Compute deterministic gross exposure without implicit FX conversion."""
        try:
            inp = PortfolioAnalyzeInput.model_validate(
                {
                    "account_snapshot_ids": account_snapshot_ids,
                    "base_currency": base_currency,
                }
            )
            envelope = container.services.portfolio.analyze_portfolio(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def portfolio_simulate_addition(
        instrument_id: str,
        quantity: Decimal,
        assumed_price: Decimal,
        currency: str,
        account_snapshot_ids: tuple[str, ...] = (),
        base_currency: str = "USD",
    ) -> dict[str, Any]:
        """Compare gross exposure before/after a hypothetical non-executing addition."""
        try:
            inp = PortfolioSimulateAdditionInput.model_validate(
                {
                    "account_snapshot_ids": account_snapshot_ids,
                    "instrument_id": instrument_id,
                    "quantity": quantity,
                    "assumed_price": assumed_price,
                    "currency": currency,
                    "base_currency": base_currency,
                }
            )
            envelope = container.services.portfolio.simulate_addition(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def portfolio_get_coverage(
        providers: tuple[str, ...] = (),
        account_refs: tuple[str, ...] = (),
        limit: int = 50,
    ) -> dict[str, Any]:
        """Read durable activity and account-snapshot attribution coverage."""
        try:
            inp = AccountGetActivityCoverageInput.model_validate(
                {
                    "providers": providers,
                    "account_refs": account_refs,
                    "limit": limit,
                }
            )
            return container.services.account_transactions.get_coverage(inp).model_dump(
                mode="json"
            )
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def portfolio_get_performance_summary(
        start: datetime,
        end: datetime,
        cost_basis_method: Literal["FIFO", "BROKER_REPORTED"] = "FIFO",
        providers: tuple[str, ...] = (),
        account_refs: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Attribute durable account activity in native currency without implicit FX."""
        try:
            inp = PerformanceAttributionInput.model_validate(
                {
                    "start": start,
                    "end": end,
                    "cost_basis_method": cost_basis_method,
                    "providers": providers,
                    "account_refs": account_refs,
                }
            )
            return container.services.account_transactions.get_performance_attribution(
                inp
            ).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def portfolio_get_retro_history(
        run_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Read immutable Trade Retro Run/review history without contacting Providers."""
        try:
            inp = TradeRetroHistoryInput.model_validate(
                {"run_id": run_id, "limit": limit}
            )
            return container.services.trade_retro.history(inp).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def portfolio_get_trade_cycles(
        providers: tuple[str, ...] = (),
        account_refs: tuple[str, ...] = (),
        instrument_ids: tuple[str, ...] = (),
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Project deterministic long-only cycles from durable transactions."""

        try:
            request = TradeCycleQueryInput.model_validate(
                {
                    "providers": providers,
                    "account_refs": account_refs,
                    "instrument_ids": instrument_ids,
                    "start": start,
                    "end": end,
                    "limit": limit,
                }
            )
            return container.services.account_transactions.get_trade_cycles(
                request
            ).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def portfolio_get_performance_series(
        start: datetime,
        end: datetime,
        providers: tuple[str, ...] = (),
        account_refs: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Calculate durable native-currency TWR, MWR/XIRR, and drawdown."""

        try:
            request = PerformanceSeriesQueryInput.model_validate(
                {
                    "start": start,
                    "end": end,
                    "providers": providers,
                    "account_refs": account_refs,
                }
            )
            return container.services.account_transactions.get_performance_series(
                request
            ).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def portfolio_get_behavior_summary(
        case_id: str | None = None,
        providers: tuple[str, ...] = (),
        account_refs: tuple[str, ...] = (),
        instrument_ids: tuple[str, ...] = (),
        strategy_code: str | None = None,
        strategy_version: str | None = None,
        horizon: str | None = None,
        currency: str | None = None,
        classifications: tuple[str, ...] = (),
        minimum_sample_size: int = 3,
    ) -> dict[str, Any]:
        """Calculate explainable behavior metrics without an aggregate score."""

        try:
            request = BehaviorSummaryQueryInput.model_validate(locals())
            return container.services.account_transactions.get_behavior_summary(
                request
            ).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def portfolio_get_unlinked_activity(
        providers: tuple[str, ...] = (),
        account_refs: tuple[str, ...] = (),
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Read unmatched Broker trade activities and their durable Review items."""

        try:
            result = container.services.activity_annotations.list_unlinked(
                providers=tuple(VendorId(item) for item in providers),
                account_refs=account_refs,
                start=start,
                end=end,
                limit=limit,
            )
            now = container.context.clock.now()
            return ToolEnvelope.success(
                request_id=container.context.id_generator.new(EntityIdPrefix.REQ),
                market=None,
                as_of=now,
                fetched_at=now,
                freshness=Freshness.UNKNOWN,
                sources=(),
                data=result,
            ).model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def portfolio_get_journal_timeline(
        case_id: str,
        instrument_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Project Decision, order-intent/result, and Broker activity chronology."""

        try:
            if not 1 <= limit <= 500:
                raise ValueError("limit must be in [1,500]")
            research = container.services.research_timeline.get_timeline(
                subject_id=case_id,
                entity_types=(),
                occurred_from=start,
                occurred_to=end,
                as_of=end,
                limit=limit,
            )
            transactions = container.services.account_transactions.list_durable_transactions(
                AccountGetTransactionsInput(start=start, end=end, limit=limit)
            )
            annotations = container.services.activity_annotations.list_annotations(limit=500)
            annotation_map = {
                (item.provider.value, item.account_ref, item.provider_transaction_id): item
                for item in annotations
            }
            items: list[dict[str, Any]] = []
            if research.ok and research.data is not None:
                items.extend(
                    {
                        "entry_type": str(item.entity_type.value).upper(),
                        "source_type": "RESEARCH",
                        "source_id": item.entity_id,
                        "occurred_at": item.occurred_at,
                        "subject_id": item.subject_id,
                        "instrument_id": item.instrument_ids[0] if item.instrument_ids else None,
                        "title": item.title,
                        "summary": item.summary,
                        "quality_status": "SOURCE_FACT",
                    }
                    for item in research.data.items
                    if instrument_id is None
                    or not item.instrument_ids
                    or instrument_id in item.instrument_ids
                )
            if transactions.ok and transactions.data is not None:
                for item in transactions.data.transactions:
                    if instrument_id is not None and item.instrument_id != instrument_id:
                        continue
                    annotation = annotation_map.get(
                        (item.provider.value, item.account_ref, item.provider_transaction_id)
                    )
                    items.append(
                        {
                            "entry_type": item.kind.value,
                            "source_type": "BROKER_ACTIVITY",
                            "source_id": item.provider_transaction_id,
                            "occurred_at": item.occurred_at,
                            "subject_id": annotation.subject_id if annotation else None,
                            "instrument_id": item.instrument_id,
                            "decision_id": annotation.decision_id if annotation else None,
                            "trade_plan_id": annotation.trade_plan_id if annotation else None,
                            "trade_plan_version": (
                                annotation.trade_plan_version if annotation else None
                            ),
                            "order_intent_id": annotation.order_intent_id if annotation else None,
                            "status": annotation.status.value if annotation else "UNLINKED",
                            "title": (
                                f"{item.kind.value} · "
                                f"{item.side.value if item.side else ''}"
                            ).strip(),
                            "summary": {
                                "quantity": item.quantity,
                                "price": item.price,
                                "cash_amount": item.cash_amount,
                                "fees": item.fees,
                                "currency": item.currency,
                            },
                            "quality_status": (
                                "FULL_CHAIN"
                                if annotation
                                and annotation.order_intent_id
                                and annotation.decision_id
                                and annotation.trade_plan_id
                                else "RETROSPECTIVE_LINK"
                                if annotation
                                else "EXECUTION_ONLY"
                            ),
                        }
                    )
            for order in container.services.broker_orders.list_recent(limit=limit):
                if instrument_id is not None and order.instrument_id != instrument_id:
                    continue
                if order.case_id not in {None, case_id}:
                    continue
                items.append(
                    {
                        "entry_type": f"ORDER_{order.status}",
                        "source_type": "ORDER_INTENT",
                        "source_id": order.order_intent_id,
                        "occurred_at": order.submitted_at or order.created_at,
                        "subject_id": order.case_id,
                        "instrument_id": order.instrument_id,
                        "decision_id": order.decision_id,
                        "trade_plan_id": order.trade_plan_id,
                        "trade_plan_version": order.trade_plan_version,
                        "status": order.status,
                        "title": f"{order.instruction} · {order.symbol}",
                        "summary": {
                            "quantity": order.quantity,
                            "order_type": order.order_type,
                            "limit_price": order.limit_price,
                            "broker_order_id": order.broker_order_id,
                        },
                        "quality_status": (
                            "FULL_CHAIN"
                            if order.decision_id and order.trade_plan_id
                            else "INTENT_ONLY"
                        ),
                    }
                )
            items.sort(
                key=lambda item: (item["occurred_at"], str(item["source_id"])), reverse=True
            )
            selected = items[:limit]
            warnings = tuple(
                WarningInfo(code=code, message=message, details={})
                for code, message in (
                    (
                        "JOURNAL_RESEARCH_TIMELINE_UNAVAILABLE",
                        "Research timeline is unavailable; remaining durable sections are shown.",
                    ),
                    (
                        "JOURNAL_ACTIVITY_UNAVAILABLE",
                        "Broker activity ledger is unavailable; remaining durable "
                        "sections are shown.",
                    ),
                )
                if (code.startswith("JOURNAL_RESEARCH") and not research.ok)
                or (code.startswith("JOURNAL_ACTIVITY") and not transactions.ok)
            )
            now = container.context.clock.now()
            return ToolEnvelope.success(
                request_id=container.context.id_generator.new(EntityIdPrefix.REQ),
                market=None,
                as_of=end or now,
                fetched_at=now,
                freshness=Freshness.UNKNOWN,
                sources=(),
                data={
                    "case_id": case_id,
                    "instrument_id": instrument_id,
                    "items": selected,
                    "total": len(items),
                    "has_more": len(items) > limit,
                    "execution_effect": False,
                },
                degraded=bool(warnings),
                warnings=warnings,
            ).model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def portfolio_preview_trade_cycle_override(
        root_cycle_id: str,
        operation: str,
        cycle_ids: tuple[str, ...],
        activity_ids: tuple[str, ...] = (),
        split_groups: tuple[tuple[str, ...], ...] = (),
        target_cycle_id: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Preview one split/merge/relink revision without persisting it."""

        try:
            request = TradeCycleOverrideAppendInput.model_validate(
                {
                    "root_cycle_id": root_cycle_id,
                    "operation": operation,
                    "cycle_ids": cycle_ids,
                    "activity_ids": activity_ids,
                    "split_groups": split_groups,
                    "target_cycle_id": target_cycle_id,
                    "note": note,
                    "actor": "external_agent",
                    "authorization_note": "Read-only Trade Cycle override impact preview.",
                    "idempotency_key": "trade-cycle-override-preview",
                }
            )
            projection = (
                container.services.account_transactions.project_trade_cycles_for_override(
                    TradeCycleQueryInput(limit=500)
                )
            )
            value = container.services.trade_cycle_overrides.preview_revision(
                request, projection=projection
            )
            now = container.context.clock.now()
            return ToolEnvelope.success(
                request_id=container.context.id_generator.new(EntityIdPrefix.REQ),
                market=None,
                as_of=now,
                fetched_at=now,
                freshness=Freshness.UNKNOWN,
                sources=(),
                data=value,
            ).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def portfolio_get_behavior_review_history(limit: int = 50) -> dict[str, Any]:
        """Read append-only weekly/monthly/quarterly behavior action history."""

        try:
            if not 1 <= limit <= 200:
                raise ValueError("limit must be in [1,200]")
            values = container.services.behavior_reviews.history(limit=limit)
            now = container.context.clock.now()
            return ToolEnvelope.success(
                request_id=container.context.id_generator.new(EntityIdPrefix.REQ),
                market=None,
                as_of=now,
                fetched_at=now,
                freshness=Freshness.UNKNOWN,
                sources=(),
                data={"runs": values, "execution_effect": False},
            ).model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def portfolio_get_daily_equity(
        account_refs: tuple[str, ...] = (),
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Read durable source-referenced Daily Equity coverage and values."""

        try:
            values = container.services.daily_equity.history(
                account_refs=account_refs, start=start, end=end, limit=limit
            )
            activation = container.services.daily_equity.get_activation()
            now = container.context.clock.now()
            return ToolEnvelope.success(
                request_id=container.context.id_generator.new(EntityIdPrefix.REQ),
                market=None,
                as_of=end or now,
                fetched_at=now,
                freshness=Freshness.UNKNOWN,
                sources=(),
                data={
                    "journal_activation_at": (
                        activation.journal_activation_at if activation else None
                    ),
                    "items": values,
                    "execution_effect": False,
                },
            ).model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    # ------------------------------------------- Phase 1L transactions/workflows

    async def account_get_transactions(
        providers: tuple[str, ...] = (),
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Refresh and return normalized read-only historical account transactions."""
        try:
            inp = AccountGetTransactionsInput.model_validate(
                {"providers": providers, "start": start, "end": end, "limit": limit}
            )
            return (await container.services.account_transactions.get_transactions(inp)).model_dump(
                mode="json"
            )
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def account_list_transactions(
        providers: tuple[str, ...] = (),
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Read normalized durable transactions without contacting a broker."""
        try:
            inp = AccountGetTransactionsInput.model_validate(
                {"providers": providers, "start": start, "end": end, "limit": limit}
            )
            return container.services.account_transactions.list_durable_transactions(
                inp
            ).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    return SimpleNamespace(
        account_get=account_get,
        account_list_transactions=account_list_transactions,
        portfolio_analyze=portfolio_analyze,
        portfolio_simulate_addition=portfolio_simulate_addition,
        portfolio_get_coverage=portfolio_get_coverage,
        portfolio_get_performance_summary=portfolio_get_performance_summary,
        portfolio_get_retro_history=portfolio_get_retro_history,
        portfolio_get_trade_cycles=portfolio_get_trade_cycles,
        portfolio_get_performance_series=portfolio_get_performance_series,
        portfolio_get_behavior_summary=portfolio_get_behavior_summary,
        portfolio_get_unlinked_activity=portfolio_get_unlinked_activity,
        portfolio_get_journal_timeline=portfolio_get_journal_timeline,
        portfolio_preview_trade_cycle_override=portfolio_preview_trade_cycle_override,
        portfolio_get_behavior_review_history=portfolio_get_behavior_review_history,
        portfolio_get_daily_equity=portfolio_get_daily_equity,
    )
