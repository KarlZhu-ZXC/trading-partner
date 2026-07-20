"""MCP use-case: resolve locally, then discover validated missing instruments.

Phase 1D design §13.4 / §14.3.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from application.dto.error_mapper import to_error_info, to_error_info_from_exception
from application.dto.instrument import InstrumentDTO, InstrumentResolveResultDTO
from application.dto.tool_envelope import SourceReference, ToolEnvelope
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.instrument_directory_provider import InstrumentDirectoryProvider
from application.ports.secret_redactor import SecretRedactor
from application.services.instrument_master_service import (
    InstrumentMasterService,
    InstrumentResolveOutcome,
)
from domain.common.enums import (
    AssetType,
    Freshness,
    Market,
    ResolveMatchType,
    SourceRole,
    VendorId,
)
from domain.common.errors import (
    DataContractError,
    InvalidInstrument,
    NoMarketData,
    ProviderNotConfigured,
    TradingPartnerError,
)
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
from domain.instruments.models import Instrument

_CANDIDATES_PREVIEW_LIMIT = 10


class InstrumentResolveService:
    def __init__(
        self,
        master: InstrumentMasterService,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
        directories: Mapping[Market, Sequence[InstrumentDirectoryProvider]] | None = None,
    ) -> None:
        self._master = master
        self._clock = clock
        self._id_generator = id_generator
        self._secret_redactor = secret_redactor
        self._directories = {
            market: tuple(providers) for market, providers in (directories or {}).items()
        }

    async def resolve_dynamic(
        self,
        *,
        market: Market,
        query: str,
        asset_type_hint: AssetType | None = None,
        as_of: datetime | None = None,
    ) -> ToolEnvelope[InstrumentResolveResultDTO]:
        """Resolve local-first; on a true miss, discover, validate, and cache.

        Provider failures remain provider failures. A syntactically valid query
        becomes ``INVALID_INSTRUMENT`` only when configured directories return
        no matching candidate.
        """
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            effective_as_of = (
                require_aware_datetime(as_of, field_name="as_of")
                if as_of is not None
                else self._clock.now()
            )
            local_outcome = self._master.resolve(
                market=market,
                query=query,
                asset_type_hint=asset_type_hint,
            )
        except InvalidInstrument as exc:
            if _is_discoverable_instrument_id_miss(
                market=market,
                query=query,
                asset_type_hint=asset_type_hint,
            ):
                local_outcome = InstrumentResolveOutcome(
                    match_type=ResolveMatchType.NOT_FOUND,
                    instrument=None,
                    candidates=(),
                    normalized=None,
                    alias_hit=None,
                )
            else:
                return self._failure_exception(
                    request_id=request_id,
                    market=market,
                    as_of=self._clock.now(),
                    exc=exc,
                )
        except TradingPartnerError as exc:
            return self._failure_exception(
                request_id=request_id,
                market=market,
                as_of=self._clock.now(),
                exc=exc,
            )
        if local_outcome.match_type is not ResolveMatchType.NOT_FOUND:
            if local_outcome.match_type is ResolveMatchType.AMBIGUOUS:
                return self._failure_invalid(
                    request_id=request_id,
                    market=market,
                    as_of=effective_as_of,
                    fetched_at=self._clock.now(),
                    query=query,
                    outcome=local_outcome,
                    message="ambiguous instrument query",
                )
            return self._success(
                request_id=request_id,
                market=market,
                query=query,
                as_of=effective_as_of,
                fetched_at=self._clock.now(),
                outcome=local_outcome,
                source_vendor=VendorId.LOCAL_MASTER,
            )

        try:
            lookup_query, lookup_hint = _discovery_query(
                market=market,
                query=query,
                asset_type_hint=asset_type_hint,
            )
        except TradingPartnerError as exc:
            return self._failure_exception(
                request_id=request_id,
                market=market,
                as_of=effective_as_of,
                exc=exc,
            )
        providers = self._directories.get(market, ())
        if not providers:
            return self.resolve(
                market=market,
                query=query,
                asset_type_hint=asset_type_hint,
                as_of=effective_as_of,
            )

        first_failure: TradingPartnerError | None = None
        for provider in providers:
            try:
                discovered = await provider.lookup(
                    market=market,
                    query=lookup_query,
                    asset_type_hint=lookup_hint,
                    as_of=effective_as_of,
                )
            except (NoMarketData, ProviderNotConfigured):
                continue
            except TradingPartnerError as exc:
                if first_failure is None:
                    first_failure = exc
                continue

            candidates = _select_discovery_candidates(
                discovered.value,
                query=lookup_query,
                asset_type_hint=lookup_hint,
            )
            if len(candidates) > 1:
                return self._failure_invalid(
                    request_id=request_id,
                    market=market,
                    as_of=effective_as_of,
                    fetched_at=self._clock.now(),
                    query=query,
                    outcome=InstrumentResolveOutcome(
                        match_type=ResolveMatchType.AMBIGUOUS,
                        instrument=None,
                        candidates=candidates,
                        normalized=local_outcome.normalized,
                        alias_hit=None,
                    ),
                    message="ambiguous instrument query",
                )
            if len(candidates) != 1:
                continue

            instrument = candidates[0]
            self._master.upsert(instrument)
            return self._success(
                request_id=request_id,
                market=market,
                query=query,
                as_of=effective_as_of,
                fetched_at=discovered.meta.fetched_at,
                outcome=InstrumentResolveOutcome(
                    match_type=(
                        ResolveMatchType.EXACT_INSTRUMENT_ID
                        if ":" in query
                        else ResolveMatchType.NORMALIZED_SYMBOL
                    ),
                    instrument=instrument,
                    candidates=(instrument,),
                    normalized=local_outcome.normalized,
                    alias_hit=None,
                ),
                source_vendor=discovered.meta.vendor,
            )

        if first_failure is not None:
            return self._failure_exception(
                request_id=request_id,
                market=market,
                as_of=effective_as_of,
                exc=first_failure,
            )
        return self.resolve(
            market=market,
            query=query,
            asset_type_hint=asset_type_hint,
            as_of=effective_as_of,
        )

    def resolve(
        self,
        *,
        market: Market,
        query: str,
        asset_type_hint: AssetType | None = None,
        as_of: datetime | None = None,
    ) -> ToolEnvelope[InstrumentResolveResultDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            effective_as_of = (
                require_aware_datetime(as_of, field_name="as_of")
                if as_of is not None
                else self._clock.now()
            )
            outcome = self._master.resolve(
                market=market,
                query=query,
                asset_type_hint=asset_type_hint,
            )
        except TradingPartnerError as exc:
            now = self._clock.now()
            as_of_fail = as_of if as_of is not None and as_of.tzinfo is not None else now
            try:
                if as_of is not None:
                    as_of_fail = require_aware_datetime(as_of, field_name="as_of")
            except TradingPartnerError:
                as_of_fail = now
            return ToolEnvelope.failure(
                request_id=request_id,
                market=market,
                as_of=as_of_fail,
                fetched_at=now,
                freshness=Freshness.UNKNOWN,
                sources=(),
                errors=[to_error_info(exc, self._secret_redactor)],
            )
        except Exception as exc:  # noqa: BLE001 — use-case returns envelope
            now = self._clock.now()
            return ToolEnvelope.failure(
                request_id=request_id,
                market=market,
                as_of=now,
                fetched_at=now,
                freshness=Freshness.UNKNOWN,
                sources=(),
                errors=[to_error_info_from_exception(exc, self._secret_redactor)],
            )

        fetched_at = self._clock.now()

        if outcome.match_type is ResolveMatchType.AMBIGUOUS:
            return self._failure_invalid(
                request_id=request_id,
                market=market,
                as_of=effective_as_of,
                fetched_at=fetched_at,
                query=query,
                outcome=outcome,
                message="ambiguous instrument query",
            )

        if (
            outcome.match_type is ResolveMatchType.NOT_FOUND
            or outcome.instrument is None
        ):
            return self._failure_invalid(
                request_id=request_id,
                market=market,
                as_of=effective_as_of,
                fetched_at=fetched_at,
                query=query,
                outcome=outcome,
                message="instrument not found",
            )

        return self._success(
            request_id=request_id,
            market=market,
            query=query,
            as_of=effective_as_of,
            fetched_at=fetched_at,
            outcome=outcome,
            source_vendor=VendorId.LOCAL_MASTER,
        )

    def _success(
        self,
        *,
        request_id: str,
        market: Market,
        query: str,
        as_of: datetime,
        fetched_at: datetime,
        outcome: InstrumentResolveOutcome,
        source_vendor: VendorId,
    ) -> ToolEnvelope[InstrumentResolveResultDTO]:
        assert outcome.instrument is not None
        data = InstrumentResolveResultDTO(
            match_type=outcome.match_type,
            instrument=InstrumentDTO.from_domain(outcome.instrument),
            candidates=tuple(
                InstrumentDTO.from_domain(c) for c in outcome.candidates
            ),
            queried=query,
            normalized_symbol=(
                outcome.normalized.canonical_candidate
                if outcome.normalized is not None
                else None
            ),
            alias_type=(
                outcome.alias_hit.alias_type if outcome.alias_hit is not None else None
            ),
            alias_value=(
                outcome.alias_hit.alias_value if outcome.alias_hit is not None else None
            ),
        )
        source = SourceReference(
            name=source_vendor.value,
            role=SourceRole.PRIMARY,
            url=None,
            retrieved_at=fetched_at,
        )
        return ToolEnvelope.success(
            request_id=request_id,
            market=market,
            as_of=as_of,
            fetched_at=fetched_at,
            freshness=Freshness.FRESH,
            sources=(source,),
            data=data,
            degraded=False,
        )

    def _failure_exception(
        self,
        *,
        request_id: str,
        market: Market,
        as_of: datetime,
        exc: TradingPartnerError,
    ) -> ToolEnvelope[InstrumentResolveResultDTO]:
        return ToolEnvelope.failure(
            request_id=request_id,
            market=market,
            as_of=as_of,
            fetched_at=self._clock.now(),
            freshness=Freshness.UNKNOWN,
            sources=(),
            errors=[to_error_info(exc, self._secret_redactor)],
            degraded=True,
        )

    def _failure_invalid(
        self,
        *,
        request_id: str,
        market: Market,
        as_of: datetime,
        fetched_at: datetime,
        query: str,
        outcome: InstrumentResolveOutcome,
        message: str,
    ) -> ToolEnvelope[InstrumentResolveResultDTO]:
        preview = [
            _candidate_preview_item(c)
            for c in outcome.candidates[:_CANDIDATES_PREVIEW_LIMIT]
        ]
        details: dict[str, object] = {
            "query": query,
            "market": market.value,
            "match_type": outcome.match_type.value,
        }
        if preview:
            details["candidates_preview"] = preview
        if outcome.normalized is not None:
            details["normalized_symbol"] = outcome.normalized.canonical_candidate

        err = InvalidInstrument(message, details=details)
        return ToolEnvelope.failure(
            request_id=request_id,
            market=market,
            as_of=as_of,
            fetched_at=fetched_at,
            freshness=Freshness.UNKNOWN,
            sources=(),
            errors=[to_error_info(err, self._secret_redactor)],
            degraded=True,
        )


def _candidate_preview_item(instrument: Instrument) -> dict[str, object]:
    return {
        "instrument_id": instrument.instrument_id,
        "symbol": instrument.symbol,
        "name": instrument.name,
        "asset_type": instrument.asset_type.value,
        "market": instrument.market.value,
    }


def _discovery_query(
    *, market: Market, query: str, asset_type_hint: AssetType | None
) -> tuple[str, AssetType | None]:
    cleaned = query.strip()
    if ":" not in cleaned:
        return cleaned, asset_type_hint
    try:
        parsed_asset, parsed_market, symbol = parse_instrument_id(cleaned)
    except DataContractError as exc:
        raise InvalidInstrument(
            "instrument_id is invalid",
            details={"market": market.value},
        ) from exc
    if parsed_market is not market:
        raise InvalidInstrument(
            "instrument_id market does not match requested market",
            details={"market": market.value, "instrument_id_market": parsed_market.value},
        )
    if asset_type_hint is not None and parsed_asset is not asset_type_hint:
        raise InvalidInstrument(
            "instrument_id asset_type does not match asset_type_hint",
            details={
                "asset_type": parsed_asset.value,
                "asset_type_hint": asset_type_hint.value,
            },
        )
    return symbol, parsed_asset


def _is_discoverable_instrument_id_miss(
    *, market: Market, query: str, asset_type_hint: AssetType | None
) -> bool:
    cleaned = query.strip() if isinstance(query, str) else ""
    if ":" not in cleaned:
        return False
    try:
        parsed_asset, parsed_market, _symbol = parse_instrument_id(cleaned)
    except DataContractError:
        return False
    return parsed_market is market and (
        asset_type_hint is None or parsed_asset is asset_type_hint
    )


def _select_discovery_candidates(
    candidates: tuple[Instrument, ...],
    *,
    query: str,
    asset_type_hint: AssetType | None,
) -> tuple[Instrument, ...]:
    filtered = tuple(
        candidate
        for candidate in candidates
        if asset_type_hint is None or candidate.asset_type is asset_type_hint
    )
    exact_symbols = tuple(
        candidate for candidate in filtered if candidate.symbol.casefold() == query.casefold()
    )
    if exact_symbols:
        return tuple(sorted(exact_symbols, key=lambda item: item.instrument_id))
    exact_names = tuple(
        candidate for candidate in filtered if candidate.name.casefold() == query.casefold()
    )
    if exact_names:
        return tuple(sorted(exact_names, key=lambda item: item.instrument_id))
    return tuple(sorted(filtered, key=lambda item: item.instrument_id))
