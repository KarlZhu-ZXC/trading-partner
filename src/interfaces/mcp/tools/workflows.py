"""Compact research-workflow operation adapters."""

from datetime import date, datetime
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import ValidationError

from application.dto.peer_comparison import PeerComparisonRunInput
from application.dto.workflow import (
    AShareRunMarketReviewInput,
    PortfolioRunReviewInput,
    ResearchRunCatalystReviewInput,
    ResearchRunDeepDiveInput,
    USRunMarketReviewInput,
)
from bootstrap import ApplicationContainer
from interfaces.mcp.validation import unexpected_failure


def build_workflow_adapters(container: ApplicationContainer) -> SimpleNamespace:
    """Build compact workflow operation adapters."""

    async def research_run_deep_dive(
        idempotency_key: str,
        case_id: str | None = None,
        instrument_id: str | None = None,
        as_of: datetime | None = None,
        lookback_days: int = 365,
        create_case: bool = True,
        case_title: str | None = None,
        case_summary: str | None = None,
        case_topic_tags: list[str] | None = None,
        case_creation_confirmed_by: str | None = None,
        case_creation_idempotency_key: str | None = None,
        industry_cycle: Literal["hog"] | None = None,
        industry_cycle_lookback_months: int = 120,
        company_operating_lookback_months: int = 36,
        company_operating_document_limit: int = 20,
    ) -> dict[str, Any]:
        """Run Deep Research; by default create/reuse a Draft instrument research file."""
        try:
            request = ResearchRunDeepDiveInput.model_validate(
                {
                    "idempotency_key": idempotency_key,
                    "case_id": case_id,
                    "instrument_id": instrument_id,
                    "as_of": as_of,
                    "lookback_days": lookback_days,
                    "create_case": create_case,
                    "case_title": case_title,
                    "case_summary": case_summary,
                    "case_topic_tags": tuple(case_topic_tags or ()),
                    "case_creation_confirmed_by": case_creation_confirmed_by,
                    "case_creation_idempotency_key": case_creation_idempotency_key,
                    "industry_cycle": industry_cycle,
                    "industry_cycle_lookback_months": industry_cycle_lookback_months,
                    "company_operating_lookback_months": company_operating_lookback_months,
                    "company_operating_document_limit": company_operating_document_limit,
                }
            )
            envelope = await container.research_workflow_orchestrator.run_deep_dive(request)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    async def research_run_catalyst_review(
        idempotency_key: str,
        case_id: str | None = None,
        instrument_id: str | None = None,
        as_of: datetime | None = None,
        lookback_days: int = 365,
        topic: str | None = None,
    ) -> dict[str, Any]:
        """Run the cross-market catalyst fact recipe."""
        try:
            request = ResearchRunCatalystReviewInput.model_validate(
                {
                    "idempotency_key": idempotency_key,
                    "case_id": case_id,
                    "instrument_id": instrument_id,
                    "as_of": as_of,
                    "lookback_days": lookback_days,
                    "topic": topic,
                }
            )
            envelope = await container.research_workflow_orchestrator.run_catalyst_review(request)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    async def a_share_run_market_review(
        idempotency_key: str,
        trade_date: date | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Run the A-share market-board and limit-ecology review recipe."""
        try:
            request = AShareRunMarketReviewInput.model_validate(
                {
                    "idempotency_key": idempotency_key,
                    "trade_date": trade_date,
                    "as_of": as_of,
                }
            )
            envelope = await container.research_workflow_orchestrator.run_a_share_market_review(
                request
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    async def us_run_market_review(
        idempotency_key: str,
        as_of: datetime | None = None,
        prediction_topic: str | None = None,
    ) -> dict[str, Any]:
        """Run the US index, macro, news, and portfolio-impact recipe."""
        try:
            request = USRunMarketReviewInput.model_validate(
                {
                    "idempotency_key": idempotency_key,
                    "as_of": as_of,
                    "prediction_topic": prediction_topic,
                }
            )
            envelope = await container.research_workflow_orchestrator.run_us_market_review(request)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    async def portfolio_run_review(
        idempotency_key: str,
        refresh_accounts: bool = False,
        providers: tuple[str, ...] = (),
        account_snapshot_ids: tuple[str, ...] = (),
        as_of: datetime | None = None,
        risk_lookback_sessions: int = 126,
        max_risk_instruments: int = 12,
    ) -> dict[str, Any]:
        """Review durable accounts; refresh only when the user explicitly requests it."""
        try:
            request = PortfolioRunReviewInput.model_validate(
                {
                    "idempotency_key": idempotency_key,
                    "refresh_accounts": refresh_accounts,
                    "providers": providers,
                    "account_snapshot_ids": account_snapshot_ids,
                    "as_of": as_of,
                    "risk_lookback_sessions": risk_lookback_sessions,
                    "max_risk_instruments": max_risk_instruments,
                }
            )
            envelope = await container.research_workflow_orchestrator.run_portfolio_review(request)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    async def research_run_peer_comparison(
        idempotency_key: str,
        primary_instrument_id: str,
        peer_instrument_ids: list[str],
        period_mode: Literal["annual", "latest_reported"] = "annual",
        periods: int = 3,
        include_valuation: bool = True,
        include_operating_metrics: bool = False,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Compare one A-share/US equity with 1-5 explicit same-market peers."""
        try:
            request = PeerComparisonRunInput.model_validate(
                {
                    "idempotency_key": idempotency_key,
                    "primary_instrument_id": primary_instrument_id,
                    "peer_instrument_ids": tuple(peer_instrument_ids),
                    "period_mode": period_mode,
                    "periods": periods,
                    "include_valuation": include_valuation,
                    "include_operating_metrics": include_operating_metrics,
                    "as_of": as_of,
                }
            )
            envelope = await container.research_workflow_orchestrator.run_peer_comparison(
                request
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    return SimpleNamespace(
        research_run_deep_dive=research_run_deep_dive,
        research_run_catalyst_review=research_run_catalyst_review,
        a_share_run_market_review=a_share_run_market_review,
        us_run_market_review=us_run_market_review,
        portfolio_run_review=portfolio_run_review,
        research_run_peer_comparison=research_run_peer_comparison,
    )
