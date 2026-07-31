"""Focused regression coverage for uniform first-use instrument access."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.dto.instrument import InstrumentDTO, InstrumentResolveResultDTO
from application.dto.tool_envelope import ErrorInfo, ToolEnvelope
from application.services.instrument_access_service import InstrumentAccessService
from domain.common.enums import AssetType, Freshness, Market, ResolveMatchType
from domain.common.errors import InvalidInstrument, TradingPartnerError
from domain.instruments.models import Instrument

NOW = datetime(2026, 7, 31, tzinfo=UTC)


def _instrument(instrument_id: str, asset_type: AssetType, market: Market) -> Instrument:
    return Instrument(
        instrument_id=instrument_id,
        symbol=instrument_id.rsplit(":", 1)[-1],
        name=instrument_id,
        market=market,
        exchange="TEST",
        currency="USD" if market is Market.US else "CNY",
        timezone="America/New_York" if market is Market.US else "Asia/Shanghai",
        asset_type=asset_type,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("instrument", "market", "asset_type"),
    (
        (_instrument("etf:US:IAU", AssetType.ETF, Market.US), Market.US, AssetType.ETF),
        (
            _instrument("equity:A_SHARE:600036.SH", AssetType.EQUITY, Market.A_SHARE),
            Market.A_SHARE,
            AssetType.EQUITY,
        ),
    ),
)
async def test_supported_master_miss_discovers_and_rereads_canonical_instrument(
    instrument: Instrument,
    market: Market,
    asset_type: AssetType,
) -> None:
    values: dict[str, Instrument] = {}
    master = MagicMock()

    def get(instrument_id: str) -> Instrument:
        if instrument_id not in values:
            raise InvalidInstrument("instrument not found")
        return values[instrument_id]

    master.get.side_effect = get
    resolver = MagicMock()

    async def resolve(**_: object) -> ToolEnvelope[InstrumentResolveResultDTO]:
        values[instrument.instrument_id] = instrument
        return ToolEnvelope.success(
            request_id="req_resolve",
            market=market,
            as_of=NOW,
            fetched_at=NOW,
            freshness=Freshness.FRESH,
            sources=(),
            data=InstrumentResolveResultDTO(
                match_type=ResolveMatchType.EXACT_INSTRUMENT_ID,
                instrument=InstrumentDTO.from_domain(instrument),
                candidates=(),
                queried=instrument.instrument_id,
                normalized_symbol=instrument.symbol,
                alias_type=None,
                alias_value=None,
            ),
        )

    resolver.resolve_dynamic = AsyncMock(side_effect=resolve)
    access = InstrumentAccessService(master, resolver)

    result = await access.get(instrument.instrument_id, as_of=NOW)

    assert result == instrument
    resolver.resolve_dynamic.assert_awaited_once_with(
        market=market,
        query=instrument.instrument_id,
        asset_type_hint=asset_type,
        as_of=NOW,
    )


@pytest.mark.asyncio
async def test_directory_failure_keeps_provider_error_type() -> None:
    master = MagicMock()
    master.get.side_effect = InvalidInstrument("instrument not found")
    resolver = MagicMock()
    resolver.resolve_dynamic = AsyncMock(
        return_value=ToolEnvelope.failure(
            request_id="req_resolve",
            market=Market.US,
            as_of=NOW,
            fetched_at=NOW,
            errors=(
                ErrorInfo(
                    code="PROVIDER_UNAVAILABLE_ERROR",
                    message="instrument directory unavailable",
                    retryable=True,
                    details={"vendor": "yfinance"},
                ),
            ),
        )
    )

    with pytest.raises(TradingPartnerError) as error:
        await InstrumentAccessService(master, resolver).get("etf:US:IAU", as_of=NOW)

    assert error.value.code == "PROVIDER_UNAVAILABLE_ERROR"
    assert error.value.retryable is True
