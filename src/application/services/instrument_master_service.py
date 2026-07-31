"""Instrument master get / resolve / upsert (local Master only; no network).

Phase 1D design §13.3 / §13.3.1 (v1.5 D3b resolution order).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from application.ports.instrument_repository import InstrumentRepository
from application.ports.instrument_unit_of_work import InstrumentUnitOfWork
from domain.common.enums import AssetType, Market, ResolveMatchType
from domain.common.errors import DataContractError, InvalidInstrument, PersistenceError
from domain.common.values import parse_instrument_id
from domain.instruments.models import Instrument, InstrumentAlias
from domain.instruments.normalize import NormalizedSymbol, normalize_symbol_input

InstrumentUowFactory = Callable[[], InstrumentUnitOfWork]

# Bound for substring / ambiguous candidate previews (design: search_name limit=11).
_CANDIDATES_PREVIEW_LIMIT = 10
_NAME_SEARCH_LIMIT = 11


@dataclass(frozen=True, slots=True)
class InstrumentResolveOutcome:
    match_type: ResolveMatchType
    instrument: Instrument | None
    candidates: tuple[Instrument, ...]
    normalized: NormalizedSymbol | None
    alias_hit: InstrumentAlias | None


class InstrumentMasterService:
    """Local instrument master: PK get, deterministic resolve, atomic upsert."""

    def __init__(
        self,
        uow_factory: InstrumentUowFactory,
    ) -> None:
        self._uow_factory = uow_factory

    def get(self, instrument_id: str) -> Instrument:
        with self._uow_factory() as uow:
            instrument = uow.instruments.get_by_id(instrument_id)
            if instrument is None:
                raise InvalidInstrument(
                    "instrument not found",
                    details={"instrument_id": instrument_id},
                )
            return instrument

    def resolve(
        self,
        *,
        market: Market,
        query: str,
        asset_type_hint: AssetType | None = None,
    ) -> InstrumentResolveOutcome:
        if not isinstance(query, str):
            raise InvalidInstrument(
                "query must be a string",
                details={"query_type": type(query).__name__, "market": market.value},
            )
        # 1. NFKC + strip; empty → InvalidInstrument
        cleaned = unicodedata.normalize("NFKC", query).strip()
        if not cleaned:
            raise InvalidInstrument(
                "query must be non-empty",
                details={"query": query, "market": market.value},
            )

        with self._uow_factory() as uow:
            repo = uow.instruments

            # 2. instrument_id three-segment form (strict; never fall through)
            if _looks_like_instrument_id(cleaned):
                return self._resolve_instrument_id(
                    repo,
                    market=market,
                    cleaned=cleaned,
                    asset_type_hint=asset_type_hint,
                )

            # 3. Exact cleaned symbol before normalize (canonical option codes etc.)
            exact_hits = repo.find_by_symbol(
                market,
                cleaned,
                asset_type=asset_type_hint,
            )
            exact_hits = _sort_by_instrument_id(exact_hits)
            if len(exact_hits) == 1:
                hit = exact_hits[0]
                return InstrumentResolveOutcome(
                    match_type=ResolveMatchType.EXACT_SYMBOL,
                    instrument=hit,
                    candidates=(hit,),
                    normalized=None,
                    alias_hit=None,
                )
            if len(exact_hits) > 1:
                return InstrumentResolveOutcome(
                    match_type=ResolveMatchType.AMBIGUOUS,
                    instrument=None,
                    candidates=exact_hits,
                    normalized=None,
                    alias_hit=None,
                )
            # zero after filter → continue

            # 4. normalize_symbol_input (name-like failures continue to alias/name)
            normalized: NormalizedSymbol | None = None
            try:
                normalized = normalize_symbol_input(
                    market,
                    cleaned,
                    asset_type_hint=asset_type_hint,
                )
            except InvalidInstrument:
                normalized = None

            if normalized is not None:
                symbol_hits = repo.find_by_symbol(
                    market,
                    normalized.canonical_candidate,
                    asset_type=asset_type_hint,
                )
                if market is Market.KR and normalized.exchange_hint is not None:
                    symbol_hits = tuple(
                        instrument
                        for instrument in symbol_hits
                        if instrument.exchange.upper() == normalized.exchange_hint
                    )
                symbol_hits = _sort_by_instrument_id(symbol_hits)
                if len(symbol_hits) == 1:
                    hit = symbol_hits[0]
                    return InstrumentResolveOutcome(
                        match_type=ResolveMatchType.NORMALIZED_SYMBOL,
                        instrument=hit,
                        candidates=(hit,),
                        normalized=normalized,
                        alias_hit=None,
                    )
                if len(symbol_hits) > 1:
                    return InstrumentResolveOutcome(
                        match_type=ResolveMatchType.AMBIGUOUS,
                        instrument=None,
                        candidates=symbol_hits,
                        normalized=normalized,
                        alias_hit=None,
                    )
                # zero after filter → continue

            # 5–6. alias keys (ordered, deduped) then market-scoped alias lookup
            alias_keys = _build_alias_keys(cleaned, normalized)
            alias_instruments = self._collect_alias_instruments(
                repo,
                market=market,
                keys=alias_keys,
            )
            alias_instruments = _filter_by_asset_type(
                alias_instruments,
                asset_type_hint,
            )
            if len(alias_instruments) == 1:
                hit = alias_instruments[0]
                alias_hit = _pick_alias_hit(repo, hit, alias_keys)
                return InstrumentResolveOutcome(
                    match_type=ResolveMatchType.ALIAS,
                    instrument=hit,
                    candidates=(hit,),
                    normalized=normalized,
                    alias_hit=alias_hit,
                )
            if len(alias_instruments) > 1:
                return InstrumentResolveOutcome(
                    match_type=ResolveMatchType.AMBIGUOUS,
                    instrument=None,
                    candidates=alias_instruments,
                    normalized=normalized,
                    alias_hit=None,
                )

            # 7. name search: exact name match only for success; substring → preview
            name_hits = repo.search_name(
                market,
                cleaned,
                limit=_NAME_SEARCH_LIMIT,
            )
            name_hits = _filter_by_asset_type(name_hits, asset_type_hint)
            exact_names = _sort_by_instrument_id(
                tuple(
                    inst
                    for inst in name_hits
                    if inst.name.casefold() == cleaned.casefold()
                )
            )
            if len(exact_names) == 1:
                hit = exact_names[0]
                return InstrumentResolveOutcome(
                    match_type=ResolveMatchType.ALIAS,
                    instrument=hit,
                    candidates=(hit,),
                    normalized=normalized,
                    alias_hit=None,
                )
            if len(exact_names) > 1:
                return InstrumentResolveOutcome(
                    match_type=ResolveMatchType.AMBIGUOUS,
                    instrument=None,
                    candidates=exact_names,
                    normalized=normalized,
                    alias_hit=None,
                )

            # 8. Substring hits only as bounded candidates_preview (never silent success).
            preview = _sort_by_instrument_id(name_hits)[:_CANDIDATES_PREVIEW_LIMIT]
            return InstrumentResolveOutcome(
                match_type=ResolveMatchType.NOT_FOUND,
                instrument=None,
                candidates=preview,
                normalized=normalized,
                alias_hit=None,
            )

    def upsert(
        self,
        instrument: Instrument,
        aliases: Sequence[InstrumentAlias] = (),
    ) -> None:
        """Upsert instrument and aliases atomically in one UoW with explicit commit.

        Every alias must belong to the instrument aggregate before any write
        (design §13.3 v1.6): same ``instrument_id`` and ``market``.
        """
        for alias in aliases:
            if alias.instrument_id != instrument.instrument_id:
                raise DataContractError(
                    "alias instrument_id must match instrument",
                    details={
                        "instrument_id": instrument.instrument_id,
                        "alias_id": alias.alias_id,
                        "alias_instrument_id": alias.instrument_id,
                    },
                )
            if alias.market is not instrument.market:
                raise DataContractError(
                    "alias market must match instrument",
                    details={
                        "instrument_id": instrument.instrument_id,
                        "alias_id": alias.alias_id,
                        "instrument_market": instrument.market.value,
                        "alias_market": alias.market.value,
                    },
                )

        with self._uow_factory() as uow:
            try:
                uow.instruments.upsert_instrument(instrument)
                for alias in aliases:
                    uow.instruments.upsert_alias(alias)
                uow.commit()
            except PersistenceError:
                uow.rollback()
                raise
            except Exception as exc:  # noqa: BLE001 — map infra integrity errors
                uow.rollback()
                raise PersistenceError(
                    f"instrument upsert failed: {type(exc).__name__}",
                    details={"error_type": type(exc).__name__},
                ) from exc

    def _resolve_instrument_id(
        self,
        repo: InstrumentRepository,
        *,
        market: Market,
        cleaned: str,
        asset_type_hint: AssetType | None,
    ) -> InstrumentResolveOutcome:
        # parse failure → InvalidInstrument; do not treat as ordinary symbol.
        try:
            _asset, parsed_market, _symbol = parse_instrument_id(cleaned)
        except DataContractError as exc:
            raise InvalidInstrument(
                "instrument_id is invalid",
                details={"query": cleaned, "market": market.value},
            ) from exc

        if parsed_market is not market:
            raise InvalidInstrument(
                "instrument_id market does not match requested market",
                details={
                    "query": cleaned,
                    "market": market.value,
                    "instrument_id_market": parsed_market.value,
                },
            )

        instrument = repo.get_by_id(cleaned)
        if instrument is None:
            raise InvalidInstrument(
                "instrument_id not found in master",
                details={"query": cleaned, "market": market.value},
            )
        # Exact instrument_id is authoritative; asset_type_hint must not invent a match.
        if asset_type_hint is not None and instrument.asset_type is not asset_type_hint:
            raise InvalidInstrument(
                "instrument_id asset_type does not match asset_type_hint",
                details={
                    "query": cleaned,
                    "market": market.value,
                    "asset_type": instrument.asset_type.value,
                    "asset_type_hint": asset_type_hint.value,
                },
            )
        return InstrumentResolveOutcome(
            match_type=ResolveMatchType.EXACT_INSTRUMENT_ID,
            instrument=instrument,
            candidates=(instrument,),
            normalized=None,
            alias_hit=None,
        )

    def _collect_alias_instruments(
        self,
        repo: InstrumentRepository,
        *,
        market: Market,
        keys: tuple[str, ...],
    ) -> tuple[Instrument, ...]:
        by_id: dict[str, Instrument] = {}
        for key in keys:
            for inst in repo.find_by_alias(market, key):
                if inst.instrument_id not in by_id:
                    by_id[inst.instrument_id] = inst
        return _sort_by_instrument_id(tuple(by_id.values()))


def _looks_like_instrument_id(cleaned: str) -> bool:
    """True when query has ``asset_type:market:symbol`` three-segment shape."""
    # Symbols never contain ':'; two separators ⇒ three segments under maxsplit=2.
    return cleaned.count(":") >= 2


def _build_alias_keys(
    cleaned: str,
    normalized: NormalizedSymbol | None,
) -> tuple[str, ...]:
    """Ordered, deduplicated alias lookup keys (design §13.3.1 step 4)."""
    keys: list[str] = []
    seen: set[str] = set()

    def _add(value: str | None) -> None:
        if value is None:
            return
        if not value or value in seen:
            return
        seen.add(value)
        keys.append(value)

    _add(cleaned)  # NFKC + strip already applied by caller
    _add(unicodedata.normalize("NFC", cleaned))
    _add(cleaned.casefold())
    _add(cleaned.upper())
    if normalized is not None:
        _add(normalized.canonical_candidate)
        _add(normalized.local_code)
    return tuple(keys)


def _filter_by_asset_type(
    instruments: Sequence[Instrument],
    asset_type_hint: AssetType | None,
) -> tuple[Instrument, ...]:
    if asset_type_hint is None:
        return _sort_by_instrument_id(tuple(instruments))
    return _sort_by_instrument_id(
        tuple(inst for inst in instruments if inst.asset_type is asset_type_hint)
    )


def _sort_by_instrument_id(
    instruments: Sequence[Instrument],
) -> tuple[Instrument, ...]:
    return tuple(sorted(instruments, key=lambda i: i.instrument_id))


def _pick_alias_hit(
    repo: InstrumentRepository,
    instrument: Instrument,
    keys: Sequence[str],
) -> InstrumentAlias | None:
    """First alias on the instrument whose alias_value equals an ordered key."""
    aliases = repo.list_aliases(instrument.instrument_id)
    for key in keys:
        for alias in aliases:
            if alias.alias_value == key:
                return alias
    return None
