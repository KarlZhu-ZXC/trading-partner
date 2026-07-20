"""Coordinator that resolves instruments and routes via RoutedMarketSnapshotService."""

from __future__ import annotations

from datetime import datetime

from application.dto.error_mapper import to_error_info
from application.dto.market import VerifiedMarketSnapshotDTO
from application.dto.tool_envelope import ToolEnvelope
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services.mock_instrument_resolver import MockInstrumentResolver
from application.services.routed_market_snapshot_service import (
    RoutedMarketSnapshotService,
)
from domain.common.enums import Freshness, Market
from domain.common.errors import InvalidInstrument
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime


class MockMarketSnapshotCoordinator:
    """MCP-facing market application service for Phase 1A mocks (Router-backed)."""

    def __init__(
        self,
        resolver: MockInstrumentResolver,
        routed_service: RoutedMarketSnapshotService,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._resolver = resolver
        self._routed_service = routed_service
        self._clock = clock
        self._id_generator = id_generator
        self._secret_redactor = secret_redactor

    async def get_snapshot(
        self,
        market: Market,
        symbol: str,
        as_of: datetime,
    ) -> ToolEnvelope[VerifiedMarketSnapshotDTO]:
        require_aware_datetime(as_of, field_name="as_of")

        try:
            instrument = self._resolver.resolve(market, symbol)
            if instrument.market is not market:
                raise InvalidInstrument(
                    "Resolved instrument market does not match requested market",
                    details={
                        "requested_market": market.value,
                        "instrument_market": instrument.market.value,
                        "symbol": symbol,
                    },
                )
        except InvalidInstrument as exc:
            request_id = self._id_generator.new(EntityIdPrefix.REQ)
            fetched_at = self._clock.now()
            return ToolEnvelope.failure(
                request_id=request_id,
                market=market,
                as_of=as_of,
                fetched_at=fetched_at,
                freshness=Freshness.UNKNOWN,
                errors=[to_error_info(exc, self._secret_redactor)],
            )

        return await self._routed_service.get_snapshot(instrument, as_of)
