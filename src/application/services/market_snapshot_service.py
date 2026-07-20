"""Market snapshot use-case service wrapping a single provider."""

from __future__ import annotations

from datetime import datetime

from application.dto.error_mapper import to_error_info, to_error_info_from_exception
from application.dto.market import VerifiedMarketSnapshotDTO
from application.dto.tool_envelope import (
    SourceReference,
    ToolEnvelope,
    WarningInfo,
)
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.market_snapshot_provider import MarketSnapshotProvider
from application.ports.secret_redactor import SecretRedactor
from domain.common.enums import Freshness, SourceRole
from domain.common.errors import ProviderNotConfigured, TradingPartnerError
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument

MOCK_DATA_WARNING = WarningInfo(
    code="MOCK_DATA",
    message="Response contains deterministic mock data.",
    details={},
)


class MarketSnapshotService:
    def __init__(
        self,
        provider: MarketSnapshotProvider,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._provider = provider
        self._clock = clock
        self._id_generator = id_generator
        self._secret_redactor = secret_redactor

    async def get_snapshot(
        self,
        instrument: Instrument,
        as_of: datetime,
    ) -> ToolEnvelope[VerifiedMarketSnapshotDTO]:
        require_aware_datetime(as_of, field_name="as_of")
        request_id = self._id_generator.new(EntityIdPrefix.REQ)

        if not self._provider.supports(instrument.market):
            err = ProviderNotConfigured(
                f"Provider {self._provider.provider_name!r} does not support "
                f"market={instrument.market.value}",
                details={
                    "provider": self._provider.provider_name,
                    "market": instrument.market.value,
                },
            )
            fetched_at = self._clock.now()
            return ToolEnvelope.failure(
                request_id=request_id,
                market=instrument.market,
                as_of=as_of,
                fetched_at=fetched_at,
                errors=[to_error_info(err, self._secret_redactor)],
            )

        try:
            snapshot = await self._provider.get_snapshot(instrument, as_of)
        except TradingPartnerError as exc:
            fetched_at = self._clock.now()
            return ToolEnvelope.failure(
                request_id=request_id,
                market=instrument.market,
                as_of=as_of,
                fetched_at=fetched_at,
                errors=[to_error_info(exc, self._secret_redactor)],
            )
        except Exception as exc:  # noqa: BLE001 — convert to failure envelope
            fetched_at = self._clock.now()
            return ToolEnvelope.failure(
                request_id=request_id,
                market=instrument.market,
                as_of=as_of,
                fetched_at=fetched_at,
                errors=[to_error_info_from_exception(exc, self._secret_redactor)],
            )

        fetched_at = self._clock.now()
        source = SourceReference(
            name=self._provider.provider_name,
            role=SourceRole.PRIMARY,
            url=None,
            retrieved_at=fetched_at,
        )
        data = VerifiedMarketSnapshotDTO.from_domain(snapshot)
        return ToolEnvelope.success(
            request_id=request_id,
            market=instrument.market,
            as_of=as_of,
            fetched_at=fetched_at,
            freshness=Freshness.FRESH,
            sources=(source,),
            data=data,
            degraded=True,
            warnings=(MOCK_DATA_WARNING,),
        )
