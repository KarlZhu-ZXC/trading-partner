"""Compact research-workflow operation adapters."""

from datetime import date, datetime
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import ValidationError

from application.dto.historical_validation import (
    QuantConnectImportInput,
    QuantConnectPrepareInput,
)
from application.dto.peer_comparison import PeerComparisonRunInput
from application.dto.trade_retro import (
    TradeRetroFindingReviewInput,
    TradeRetroReviewInput,
    TradeRetroWorkflowInput,
)
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
        create_subject: bool = True,
        subject_title: str | None = None,
        subject_summary: str | None = None,
        subject_topic_tags: list[str] | None = None,
        subject_creation_confirmed_by: str | None = None,
        subject_creation_idempotency_key: str | None = None,
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
                    "subject_id": case_id,
                    "instrument_id": instrument_id,
                    "as_of": as_of,
                    "lookback_days": lookback_days,
                    "create_subject": create_subject,
                    "subject_title": subject_title,
                    "subject_summary": subject_summary,
                    "subject_topic_tags": tuple(subject_topic_tags or ()),
                    "subject_creation_confirmed_by": subject_creation_confirmed_by,
                    "subject_creation_idempotency_key": subject_creation_idempotency_key,
                    "industry_cycle": industry_cycle,
                    "industry_cycle_lookback_months": industry_cycle_lookback_months,
                    "company_operating_lookback_months": company_operating_lookback_months,
                    "company_operating_document_limit": company_operating_document_limit,
                }
            )
            envelope = await container.services.workflows.run_deep_dive(request)
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
                    "subject_id": case_id,
                    "instrument_id": instrument_id,
                    "as_of": as_of,
                    "lookback_days": lookback_days,
                    "topic": topic,
                }
            )
            envelope = await container.services.workflows.run_catalyst_review(request)
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
            envelope = await container.services.workflows.run_a_share_market_review(
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
            envelope = await container.services.workflows.run_us_market_review(request)
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
            envelope = await container.services.workflows.run_portfolio_review(request)
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
            envelope = await container.services.workflows.run_peer_comparison(
                request
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    async def historical_validation_prepare(
        idempotency_key: str,
        strategy_name: str,
        hypothesis: str,
        symbols: list[str],
        start_date: date,
        end_date: date,
        strategy_code: str,
        resolution: Literal["hour", "daily"] = "hour",
        normalization_mode: Literal[
            "raw", "split_adjusted", "adjusted", "total_return"
        ] = "split_adjusted",
        initial_cash: str = "100000",
        benchmark: str = "SPY",
        parameters: dict[str, str] | None = None,
        case_id: str | None = None,
    ) -> dict[str, Any]:
        """Prepare an auditable LEAN package for a manual QuantConnect Free run."""
        try:
            request = QuantConnectPrepareInput.model_validate(
                {
                    "idempotency_key": idempotency_key,
                    "strategy_name": strategy_name,
                    "hypothesis": hypothesis,
                    "symbols": tuple(symbols),
                    "start_date": start_date,
                    "end_date": end_date,
                    "resolution": resolution,
                    "normalization_mode": normalization_mode,
                    "initial_cash": initial_cash,
                    "benchmark": benchmark,
                    "parameters": parameters or {},
                    "strategy_code": strategy_code,
                    "subject_id": case_id,
                }
            )
            envelope = container.services.historical_validation.prepare_quantconnect(request)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    async def historical_validation_import(
        idempotency_key: str,
        validation_id: str,
        results_path: str,
        backtest_url: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Import a user-downloaded QuantConnect result JSON without remote API access."""
        try:
            request = QuantConnectImportInput.model_validate(
                {
                    "idempotency_key": idempotency_key,
                    "validation_id": validation_id,
                    "results_path": results_path,
                    "backtest_url": backtest_url,
                    "notes": notes,
                }
            )
            envelope = container.services.historical_validation.import_quantconnect(request)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    async def trade_retro(
        action: Literal["prepare", "run", "review", "export"],
        idempotency_key: str,
        start: datetime | None = None,
        end: datetime | None = None,
        run_id: str | None = None,
        use_llm: bool = True,
        expected_version: int | None = None,
        review_status: Literal["OPEN", "ACCEPTED", "DISPUTED", "RESOLVED"] | None = None,
        note_markdown: str = "",
        action_items: tuple[str, ...] = (),
        finding_reviews: tuple[TradeRetroFindingReviewInput, ...] = (),
        confirmed_by: Literal["user", "external_agent"] | None = None,
        authorization_note: str | None = None,
    ) -> dict[str, Any]:
        """Prepare, generate, append a review revision, or safely export a Trade Retro."""
        try:
            request = TradeRetroWorkflowInput.model_validate(
                {
                    "action": action,
                    "idempotency_key": idempotency_key,
                    "start": start,
                    "end": end,
                    "run_id": run_id,
                    "use_llm": use_llm,
                    "expected_version": expected_version,
                    "review_status": review_status,
                    "note_markdown": note_markdown,
                    "action_items": action_items,
                    "finding_reviews": finding_reviews,
                    "confirmed_by": confirmed_by,
                    "authorization_note": authorization_note,
                }
            )
            if request.action == "prepare":
                assert request.start is not None and request.end is not None
                return container.services.trade_retro.prepare(
                    start=request.start,
                    end=request.end,
                    idempotency_key=request.idempotency_key,
                ).model_dump(mode="json")
            elif request.action == "run":
                assert request.start is not None and request.end is not None
                envelope = await container.services.trade_retro.run(
                    start=request.start,
                    end=request.end,
                    idempotency_key=request.idempotency_key,
                    use_llm=request.use_llm,
                )
                return envelope.model_dump(mode="json")
            elif request.action == "review":
                assert request.run_id is not None
                assert request.expected_version is not None
                assert request.review_status is not None
                assert request.confirmed_by is not None
                assert request.authorization_note is not None
                return container.services.trade_retro.review(
                    TradeRetroReviewInput(
                        run_id=request.run_id,
                        expected_version=request.expected_version,
                        status=request.review_status,
                        note_markdown=request.note_markdown,
                        action_items=request.action_items,
                        finding_reviews=request.finding_reviews,
                        confirmed_by=request.confirmed_by,
                        authorization_note=request.authorization_note,
                        idempotency_key=request.idempotency_key,
                    )
                ).model_dump(mode="json")
            else:
                assert request.run_id is not None
                return container.services.trade_retro.export(
                    run_id=request.run_id,
                    idempotency_key=request.idempotency_key,
                ).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    def judgment_scorecard(
        case_id: str,
        thesis_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Persist one deterministic, immutable scorecard for an exact current Thesis."""
        try:
            return container.services.scorecards.run(
                subject_id=case_id,
                thesis_id=thesis_id,
                idempotency_key=idempotency_key,
            ).model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    return SimpleNamespace(
        research_run_deep_dive=research_run_deep_dive,
        research_run_catalyst_review=research_run_catalyst_review,
        a_share_run_market_review=a_share_run_market_review,
        us_run_market_review=us_run_market_review,
        portfolio_run_review=portfolio_run_review,
        research_run_peer_comparison=research_run_peer_comparison,
        historical_validation_prepare=historical_validation_prepare,
        historical_validation_import=historical_validation_import,
        trade_retro=trade_retro,
        judgment_scorecard=judgment_scorecard,
    )
