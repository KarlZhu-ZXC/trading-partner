"""Bounded Provider route receipt contracts and persistence tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text

from application.dto.provider_route_history import ProviderRouteReceipt
from application.dto.provider_routing import ProviderAttemptRecord
from domain.common.enums import (
    CacheDisposition,
    DataCategory,
    DataCriticality,
    Market,
    ProviderAttemptOutcome,
    SourceRole,
    VendorId,
)
from domain.common.errors import DataContractError
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.provider_route_history_store import (
    InMemoryProviderRouteHistoryStore,
    SqlAlchemyProviderRouteHistoryStore,
)

NOW = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)


def _receipt(**overrides: object) -> ProviderRouteReceipt:
    values: dict[str, object] = {
        "route_id": "provider_route_00000000-0000-7000-8000-000000000001",
        "recorded_at": NOW,
        "market": Market.US,
        "category": DataCategory.MARKET_SNAPSHOT,
        "operation_name": "market.quote",
        "instrument_id": "equity:US:NVDA",
        "criticality": DataCriticality.CORE,
        "requested_chain": (VendorId.YFINANCE, VendorId.ALPHA_VANTAGE),
        "ok": True,
        "selected_vendor": VendorId.ALPHA_VANTAGE,
        "selected_role": SourceRole.FALLBACK,
        "cache_disposition": CacheDisposition.MISS,
        "attempts": (
            ProviderAttemptRecord(
                vendor=VendorId.YFINANCE,
                outcome=ProviderAttemptOutcome.FAILURE,
                error_code="PROVIDER_UNAVAILABLE_ERROR",
                duration_ms=12,
                message=None,
            ),
            ProviderAttemptRecord(
                vendor=VendorId.ALPHA_VANTAGE,
                outcome=ProviderAttemptOutcome.SUCCESS,
                error_code=None,
                duration_ms=23,
                message=None,
            ),
        ),
        "warning_codes": ("FALLBACK_VENDOR_USED",),
        "final_error_code": None,
    }
    values.update(overrides)
    return ProviderRouteReceipt(**values)  # type: ignore[arg-type]


def test_receipt_rejects_free_text_attempt_messages() -> None:
    unsafe = replace(_receipt().attempts[0], message="token=secret")
    with pytest.raises(DataContractError, match="must not store messages"):
        _receipt(attempts=(unsafe,))


def test_in_memory_store_is_bounded_by_age_and_newest_first() -> None:
    store = InMemoryProviderRouteHistoryStore()
    old = _receipt(recorded_at=NOW - timedelta(days=31))
    current = _receipt(
        route_id="provider_route_00000000-0000-7000-8000-000000000002"
    )
    store.append(old)
    store.append(current)

    assert store.is_durable is False
    assert store.list_since(NOW - timedelta(days=60), limit=10) == (current,)


def test_sql_store_round_trip_prunes_age_and_omits_free_text(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'routes.db'}")
    Base.metadata.create_all(engine)
    try:
        store = SqlAlchemyProviderRouteHistoryStore(engine)
        old = _receipt(recorded_at=NOW - timedelta(days=31))
        current = _receipt(
            route_id="provider_route_00000000-0000-7000-8000-000000000002"
        )
        store.append(old)
        store.append(current)

        assert store.is_durable is True
        assert store.list_since(NOW - timedelta(days=60), limit=10) == (current,)
        with engine.connect() as connection:
            raw = connection.execute(
                text("SELECT attempts_json FROM provider_route_receipts")
            ).scalar_one()
        assert "message" not in raw
        assert "secret" not in raw
    finally:
        engine.dispose()
