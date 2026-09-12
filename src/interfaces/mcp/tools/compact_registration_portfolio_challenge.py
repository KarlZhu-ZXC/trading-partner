"""Compact MCP registrations for portfolio and workflow capabilities."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .compact import CapabilityRegistrar


def _register_portfolio_get(
    registry: CapabilityRegistrar,
    portfolio: SimpleNamespace,
) -> None:
    """Register durable account and portfolio reads as one grouped capability."""

    from .compact import READ_DURABLE, _all_fields, _register_flat_dispatch_tool, _spec

    _register_flat_dispatch_tool(
        registry,
        name="portfolio_get",
        description=(
            "Read durable account positions or transactions, portfolio exposure, "
            "activity coverage, performance attribution, deterministic Trade Cycles, "
            "Trade Retro history, or calculation-only portfolio views."
        ),
        variants=(
            _spec(
                "positions",
                portfolio.account_get,
                ("snapshot_id",),
                adapter_operation="positions",
            ),
            _spec(
                "transactions",
                portfolio.account_list_transactions,
                _all_fields(portfolio.account_list_transactions),
            ),
            _spec(
                "exposure",
                portfolio.portfolio_analyze,
                _all_fields(portfolio.portfolio_analyze),
            ),
            _spec(
                "coverage",
                portfolio.portfolio_get_coverage,
                _all_fields(portfolio.portfolio_get_coverage),
            ),
            _spec(
                "performance_summary",
                portfolio.portfolio_get_performance_summary,
                _all_fields(portfolio.portfolio_get_performance_summary),
            ),
            _spec(
                "performance_series",
                portfolio.portfolio_get_performance_series,
                _all_fields(portfolio.portfolio_get_performance_series),
            ),
            _spec(
                "behavior_summary",
                portfolio.portfolio_get_behavior_summary,
                _all_fields(portfolio.portfolio_get_behavior_summary),
            ),
            _spec(
                "unlinked_activity",
                portfolio.portfolio_get_unlinked_activity,
                _all_fields(portfolio.portfolio_get_unlinked_activity),
            ),
            _spec(
                "journal_timeline",
                portfolio.portfolio_get_journal_timeline,
                _all_fields(portfolio.portfolio_get_journal_timeline),
            ),
            _spec(
                "trade_cycle_override_preview",
                portfolio.portfolio_preview_trade_cycle_override,
                tuple(
                    field
                    for field in _all_fields(portfolio.portfolio_preview_trade_cycle_override)
                    if field != "operation"
                ),
                extra_fields={"override_operation": (str, ...)},
                adapter_operation_field="override_operation",
            ),
            _spec(
                "behavior_review_history",
                portfolio.portfolio_get_behavior_review_history,
                _all_fields(portfolio.portfolio_get_behavior_review_history),
            ),
            _spec(
                "daily_equity",
                portfolio.portfolio_get_daily_equity,
                _all_fields(portfolio.portfolio_get_daily_equity),
            ),
            _spec(
                "trade_cycles",
                portfolio.portfolio_get_trade_cycles,
                _all_fields(portfolio.portfolio_get_trade_cycles),
            ),
            _spec(
                "simulate_addition",
                portfolio.portfolio_simulate_addition,
                _all_fields(portfolio.portfolio_simulate_addition),
            ),
            _spec(
                "retro_history",
                portfolio.portfolio_get_retro_history,
                _all_fields(portfolio.portfolio_get_retro_history),
            ),
        ),
        policy=READ_DURABLE,
    )


def _register_portfolio_challenge_workflows(
    registry: CapabilityRegistrar,
    workflows: SimpleNamespace,
    view_review: SimpleNamespace,
) -> None:
    """Register durable workflow operations in order."""

    from .compact import (
        APPEND_OPEN_WORLD,
        _all_fields,
        _register_flat_dispatch_tool,
        _spec,
    )

    _register_flat_dispatch_tool(
        registry,
        name="research_workflow_run",
        description=(
            "Run one closed research, market, portfolio, or peer-comparison workflow, "
            "one durable Trade Retro, one deterministic "
            "Judgment Scorecard, or one explicitly requested escalated View review."
        ),
        variants=(
            _spec(
                "deep_dive",
                workflows.research_run_deep_dive,
                (
                    "idempotency_key",
                    "case_id",
                    "instrument_id",
                    "as_of",
                    "lookback_days",
                    "industry_cycle",
                    "industry_cycle_lookback_months",
                    "company_operating_lookback_months",
                    "company_operating_document_limit",
                ),
                overrides={"create_subject": False},
            ),
            _spec(
                "catalyst_review",
                workflows.research_run_catalyst_review,
                _all_fields(workflows.research_run_catalyst_review),
            ),
            _spec(
                "a_share_market_review",
                workflows.a_share_run_market_review,
                _all_fields(workflows.a_share_run_market_review),
            ),
            _spec(
                "us_market_review",
                workflows.us_run_market_review,
                _all_fields(workflows.us_run_market_review),
            ),
            _spec(
                "portfolio_review",
                workflows.portfolio_run_review,
                (
                    "idempotency_key",
                    "account_snapshot_ids",
                    "as_of",
                    "risk_lookback_sessions",
                    "max_risk_instruments",
                ),
                overrides={"refresh_accounts": False, "providers": ()},
            ),
            _spec(
                "peer_comparison",
                workflows.research_run_peer_comparison,
                _all_fields(workflows.research_run_peer_comparison),
            ),
            _spec(
                "trade_retro",
                workflows.trade_retro,
                _all_fields(workflows.trade_retro),
            ),
            _spec(
                "judgment_scorecard",
                workflows.judgment_scorecard,
                _all_fields(workflows.judgment_scorecard),
            ),
            _spec(
                "evaluate_view",
                view_review.view_review_run,
                _all_fields(view_review.view_review_run),
            ),
        ),
        policy=APPEND_OPEN_WORLD,
    )
