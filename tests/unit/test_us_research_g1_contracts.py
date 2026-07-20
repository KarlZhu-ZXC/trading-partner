"""Phase 1G G1: compact US research domain/DTO/ports/settings contract tests."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from application.dto.us_research import (
    EventsSearchInput,
    FundamentalGetSnapshotInput,
    FundamentalGetStatementsInput,
    ResearchGetCompanyUpdatesInput,
    USCompanyProfileDTO,
    USFinancialStatementsDTO,
    USFundamentalMetricsDTO,
    USFundamentalSnapshotDTO,
    USGetFilingsInput,
    USGetInsiderActivityInput,
    USStatementPeriodDTO,
)
from application.ports.category_provider import CategoryProvider
from application.ports.us_research_providers import (
    US_RESEARCH_RUNTIME_PROTOCOLS,
    USCorporateActionsProvider,
    USFilingsProvider,
    USFinancialStatementsProvider,
    USFundamentalProvider,
    USInsiderActivityProvider,
)
from application.services.criticality_policy import CriticalityPolicy
from application.services.us_research_tool_policies import (
    PHASE1G_US_RESEARCH_TOOL_NAMES,
    PHASE1G_US_RESEARCH_TOOL_POLICIES,
)
from domain.common.enums import AppEnvironment, DataCategory, DataCriticality, LogLevel
from domain.common.errors import DataContractError
from domain.us_research.enums import (
    USCorporateActionType,
    USExternalEventType,
    USFilingForm,
    USFundamentalBasis,
    USStatementFrequency,
    USStatementType,
)
from domain.us_research.models import (
    USCompanyProfile,
    USCorporateAction,
    USFinancialStatements,
    USFundamentalMetrics,
    USFundamentalSnapshot,
    USStatementPeriod,
)
from infrastructure.config.settings import AppSettings

UTC = ZoneInfo("UTC")
AS_OF = datetime(2026, 7, 17, 20, 0, tzinfo=UTC)
INSTRUMENT = "equity:US:NVDA"
D = Decimal


def _base_settings(**overrides: object) -> AppSettings:
    base: dict[str, object] = {
        "app_name": "tp",
        "app_env": AppEnvironment.TEST,
        "log_level": LogLevel.INFO,
        "database_url": "sqlite:////tmp/tp-g1.db",
        "mcp_server_name": "tp",
        "default_timezone": "UTC",
        "provider_timeout_seconds": 30.0,
    }
    base.update(overrides)
    return AppSettings(_env_file=None, **base)  # type: ignore[call-arg]


def _metrics(**overrides: object) -> USFundamentalMetrics:
    fields: dict[str, object] = {
        "trailing_pe": D("45.2"),
        "forward_pe": D("30.1"),
        "peg_ratio": None,
        "price_to_book": D("40.0"),
        "price_to_sales": None,
        "enterprise_to_ebitda": None,
        "dividend_yield": D("0.01"),
        "beta": D("1.7"),
        "eps_ttm": D("2.50"),
        "eps_forward": None,
        "book_value_per_share": D("3.00"),
        "revenue_per_share": None,
        "revenue": D("100000000000"),
        "gross_profit": None,
        "ebitda": None,
        "net_income": D("30000000000"),
        "profit_margin": D("0.30"),
        "operating_margin": None,
        "roe": D("0.90"),
        "roa": None,
        "debt_to_equity": D("0.4"),
        "current_ratio": D("3.5"),
        "revenue_growth": D("0.20"),
        "eps_growth": None,
        "estimate_revision": None,
        "share_count": D("25000000000"),
        "stock_based_compensation": None,
        "capital_expenditure": None,
        "free_cash_flow": D("20000000000"),
        "net_cash_or_debt": D("10000000000"),
        "period_end": date(2026, 4, 27),
        "filed_at": datetime(2026, 5, 28, 16, 0, tzinfo=UTC),
        "basis": USFundamentalBasis.TTM,
    }
    fields.update(overrides)
    return USFundamentalMetrics(**fields)  # type: ignore[arg-type]


def _profile(**overrides: object) -> USCompanyProfile:
    fields: dict[str, object] = {
        "instrument_id": INSTRUMENT,
        "legal_name": "NVIDIA Corporation",
        "description": None,
        "sector": "Technology",
        "industry": "Semiconductors",
        "country": "US",
        "website": "https://www.nvidia.com",
        "employees": 30000,
        "market_cap": D("3000000000000"),
    }
    fields.update(overrides)
    return USCompanyProfile(**fields)  # type: ignore[arg-type]


def _statement_period(**overrides: object) -> USStatementPeriod:
    fields: dict[str, object] = {
        "statement_type": USStatementType.INCOME,
        "frequency": USStatementFrequency.QUARTERLY,
        "fiscal_year": 2026,
        "fiscal_period": "Q1",
        "period_end": date(2026, 4, 27),
        "filed_at": datetime(2026, 5, 28, 16, 0, tzinfo=UTC),
        "currency": "USD",
        "line_items": (
            ("revenue", D("1000000000")),
            ("net_income", D("250000000")),
            ("missing_line", None),
        ),
    }
    fields.update(overrides)
    return USStatementPeriod(**fields)  # type: ignore[arg-type]


def test_valid_profile_metrics_snapshot_and_statements_round_trip() -> None:
    profile = _profile()
    metrics = _metrics()
    action = USCorporateAction(
        instrument_id=INSTRUMENT,
        action_type=USCorporateActionType.DIVIDEND,
        effective_date=date(2026, 6, 12),
        declared_date=date(2026, 5, 22),
        paid_date=date(2026, 6, 27),
        amount=D("0.04"),
        ratio=None,
        currency="USD",
        shares=None,
        description=None,
    )
    snapshot = USFundamentalSnapshot(
        instrument_id=INSTRUMENT,
        as_of=AS_OF,
        profile=profile,
        metrics=metrics,
        corporate_actions=(action,),
        degraded=False,
        warning_codes=(),
    )
    period = _statement_period()
    statements = USFinancialStatements(
        instrument_id=INSTRUMENT,
        as_of=AS_OF,
        frequency=USStatementFrequency.QUARTERLY,
        income=(period,),
        balance_sheet=(),
        cash_flow=(),
    )

    snap_json = USFundamentalSnapshotDTO.from_domain(snapshot).model_dump(mode="json")
    assert snap_json["profile"]["market_cap"] == "3000000000000"
    assert snap_json["metrics"]["trailing_pe"] == "45.2"
    assert isinstance(snap_json["metrics"]["trailing_pe"], str)
    assert snap_json["corporate_actions"][0]["amount"] == "0.04"

    stmt_json = USFinancialStatementsDTO.from_domain(statements).model_dump(mode="json")
    assert stmt_json["income"][0]["line_items"][0] == ["revenue", "1000000000"]
    assert stmt_json["income"][0]["line_items"][2] == ["missing_line", None]

    assert USCompanyProfileDTO.from_domain(profile).instrument_id == INSTRUMENT
    assert USFundamentalMetricsDTO.from_domain(metrics).basis is USFundamentalBasis.TTM
    assert USStatementPeriodDTO.from_domain(period).fiscal_period == "Q1"


def test_domain_rejects_float_naive_time_future_filed_and_duplicate_line_keys() -> None:
    with pytest.raises(DataContractError, match="float"):
        _metrics(trailing_pe=45.2)  # type: ignore[arg-type]
    with pytest.raises(DataContractError, match="timezone-aware"):
        _metrics(filed_at=datetime(2026, 5, 28, 16, 0))
    with pytest.raises(DataContractError, match="unique"):
        _statement_period(
            line_items=(("revenue", D("1")), ("revenue", D("2"))),
        )
    future_metrics = _metrics(filed_at=datetime(2026, 8, 1, 0, 0, tzinfo=UTC))
    with pytest.raises(DataContractError, match="metrics.filed_at"):
        USFundamentalSnapshot(
            instrument_id=INSTRUMENT,
            as_of=AS_OF,
            profile=None,
            metrics=future_metrics,
            corporate_actions=(),
            degraded=False,
            warning_codes=(),
        )
    future = _statement_period(
        filed_at=datetime(2026, 8, 1, 0, 0, tzinfo=UTC),
    )
    with pytest.raises(DataContractError, match="filed_at must be <= as_of"):
        USFinancialStatements(
            instrument_id=INSTRUMENT,
            as_of=AS_OF,
            frequency=USStatementFrequency.QUARTERLY,
            income=(future,),
            balance_sheet=(),
            cash_flow=(),
        )


def test_input_dto_accepts_valid_and_rejects_boundaries() -> None:
    snap = FundamentalGetSnapshotInput.model_validate(
        {"instrument_id": INSTRUMENT, "as_of": "2026-07-17T20:00:00+00:00"}
    )
    stmts = FundamentalGetStatementsInput.model_validate(
        {"instrument_id": INSTRUMENT, "frequency": "quarterly", "limit": 8}
    )
    filings = USGetFilingsInput.model_validate(
        {
            "instrument_id": INSTRUMENT,
            "forms": ["10-K", "10-Q"],
            "start": "2025-01-01",
            "end": "2026-07-17",
            "limit": 20,
        }
    )
    insider = USGetInsiderActivityInput.model_validate(
        {"instrument_id": INSTRUMENT, "start": "2026-01-01", "end": "2026-07-17"}
    )
    updates = ResearchGetCompanyUpdatesInput.model_validate(
        {
            "instrument_id": INSTRUMENT,
            "since": "2026-01-01T00:00:00+00:00",
            "as_of": "2026-07-17T20:00:00+00:00",
        }
    )
    events = EventsSearchInput.model_validate(
        {
            "instrument_id": INSTRUMENT,
            "event_types": ["filing", "insider_transaction"],
            "start": "2026-01-01",
            "end": "2026-07-17",
        }
    )
    assert snap.instrument_id == INSTRUMENT
    assert stmts.limit == 8
    assert filings.forms == (USFilingForm.FORM_10K, USFilingForm.FORM_10Q)
    assert insider.limit == 50
    assert updates.since is not None
    assert events.event_types[0] is USExternalEventType.FILING


@pytest.mark.parametrize(
    ("builder", "payload", "match"),
    [
        (
            FundamentalGetSnapshotInput.model_validate,
            {"instrument_id": "equity:A_SHARE:600519.SH"},
            "Market.US",
        ),
        (
            FundamentalGetSnapshotInput.model_validate,
            {"instrument_id": "etf:US:SPY"},
            "equity",
        ),
        (
            FundamentalGetSnapshotInput.model_validate,
            {
                "instrument_id": INSTRUMENT,
                "as_of": datetime(2026, 7, 17, 12, 0),
            },
            "timezone-aware",
        ),
        (
            FundamentalGetStatementsInput.model_validate,
            {"instrument_id": INSTRUMENT, "limit": 9},
            "limit",
        ),
        (
            FundamentalGetStatementsInput.model_validate,
            {"instrument_id": INSTRUMENT, "limit": 0},
            "limit",
        ),
        (
            USGetFilingsInput.model_validate,
            {
                "instrument_id": INSTRUMENT,
                "start": "2026-07-18",
                "end": "2026-07-17",
            },
            "end must be >= start",
        ),
        (
            USGetInsiderActivityInput.model_validate,
            {"instrument_id": INSTRUMENT, "limit": 101},
            "limit",
        ),
        (
            ResearchGetCompanyUpdatesInput.model_validate,
            {
                "instrument_id": INSTRUMENT,
                "since": "2026-07-18T00:00:00+00:00",
                "as_of": "2026-07-17T00:00:00+00:00",
            },
            "since must be <= as_of",
        ),
        (
            EventsSearchInput.model_validate,
            {
                "instrument_id": INSTRUMENT,
                "start": "2026-07-18",
                "end": "2026-07-17",
            },
            "end must be >= start",
        ),
    ],
)
def test_input_rejects_non_us_equity_naive_range_and_limits(
    builder: object, payload: dict[str, object], match: str
) -> None:
    with pytest.raises(ValidationError, match=match):
        builder(payload)  # type: ignore[operator]


def test_protocol_inventory_and_tool_policies() -> None:
    assert (
        USFundamentalProvider,
        USFinancialStatementsProvider,
        USFilingsProvider,
        USInsiderActivityProvider,
        USCorporateActionsProvider,
    ) == US_RESEARCH_RUNTIME_PROTOCOLS
    for protocol in US_RESEARCH_RUNTIME_PROTOCOLS:
        assert CategoryProvider in protocol.__mro__
        assert getattr(protocol, "_is_runtime_protocol", False) is True

    assert PHASE1G_US_RESEARCH_TOOL_NAMES == (
        "fundamental_get_snapshot",
        "fundamental_get_statements",
        "us_get_filings",
        "us_get_insider_activity",
        "research_get_company_updates",
        "events_search",
    )
    assert len(PHASE1G_US_RESEARCH_TOOL_POLICIES) == 6

    policy = CriticalityPolicy()
    assert policy.for_category(DataCategory.INSIDER_ACTIVITY, None) is DataCriticality.OPTIONAL
    assert (
        policy.for_category(DataCategory.INSIDER_ACTIVITY, PHASE1G_US_RESEARCH_TOOL_POLICIES[3])
        is DataCriticality.CORE
    )
    assert (
        policy.for_category(DataCategory.CORPORATE_ACTIONS, PHASE1G_US_RESEARCH_TOOL_POLICIES[0])
        is DataCriticality.OPTIONAL
    )


def test_g1_settings_defaults_ttl_and_sec_user_agent() -> None:
    s = _base_settings()
    assert s.sec_edgar_enabled is True
    assert s.sec_user_agent is None
    assert s.us_research_current_window_seconds == 21600
    assert s.cache_ttl_filings_seconds == 3600
    assert s.cache_ttl_insider_activity_seconds == 3600
    assert s.cache_ttl_for(DataCategory.FILINGS) == 3600
    assert s.cache_ttl_for(DataCategory.INSIDER_ACTIVITY) == 3600
    assert s.cache_ttl_for(DataCategory.FUNDAMENTALS) == 21600

    blank = _base_settings(sec_user_agent="   ")
    assert blank.sec_user_agent is None
    # Not a secret credential field; blank→None only.
    redacted = s.redacted_dict()
    assert "sec_user_agent" in redacted
    assert redacted["sec_user_agent"] is None

    with pytest.raises(ValidationError):
        _base_settings(us_research_current_window_seconds=-1)
    with pytest.raises(ValidationError):
        _base_settings(cache_ttl_filings_seconds=0)
    with pytest.raises(ValidationError):
        _base_settings(us_research_current_window_seconds=1.0)

    boundary = _base_settings(us_research_current_window_seconds=0)
    assert boundary.us_research_current_window_seconds == 0


def test_env_example_contains_phase1g_keys() -> None:
    root = Path(__file__).resolve().parents[2]
    text = (root / ".env.example").read_text(encoding="utf-8")
    required = [
        "SEC_EDGAR_ENABLED=true",
        "SEC_USER_AGENT=",
        "US_RESEARCH_CURRENT_WINDOW_SECONDS=21600",
        "CACHE_TTL_FILINGS_SECONDS=3600",
        "CACHE_TTL_INSIDER_ACTIVITY_SECONDS=3600",
    ]
    for key in required:
        assert key in text, f"missing .env.example key line: {key}"
