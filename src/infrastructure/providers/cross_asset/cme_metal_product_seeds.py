"""Versioned built-in CME metal product reference seeds.

Every field carries an explicit official CME contract-specs URL. Seeds supply
product-level metadata only (multiplier, tick, settlement method). They never
invent contract expiries, settlements, volume, or open interest.

Output consumers must disclose ``CME_PUBLIC_REFERENCE_ONLY`` and the seed source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from domain.common.enums import Market
from domain.cross_asset.enums import SettlementMethod
from domain.cross_asset.futures_models import FuturesProductDefinition

# Stable product UUIDs so seed rows are idempotent across restarts.
# Format: futures_product_<uuid7-compatible token>.
# uuid7-compatible tokens: hex only, version nibble 7, variant 8.
_PRODUCT_IDS: dict[str, str] = {
    "CME:GC": "futures_product_019f3a01-c0e0-7000-8000-0000000000a1",
    "CME:MGC": "futures_product_019f3a01-c0e0-7000-8000-0000000000a2",
    "CME:SI": "futures_product_019f3a01-c0e0-7000-8000-0000000000a3",
    "CME:HG": "futures_product_019f3a01-c0e0-7000-8000-0000000000a4",
    "CME:PL": "futures_product_019f3a01-c0e0-7000-8000-0000000000a5",
    "CME:PA": "futures_product_019f3a01-c0e0-7000-8000-0000000000a6",
}

_VERSION_IDS: dict[str, str] = {
    "CME:GC": "futures_product_version_019f3a01-c0e0-7000-8000-0000000000b1",
    "CME:MGC": "futures_product_version_019f3a01-c0e0-7000-8000-0000000000b2",
    "CME:SI": "futures_product_version_019f3a01-c0e0-7000-8000-0000000000b3",
    "CME:HG": "futures_product_version_019f3a01-c0e0-7000-8000-0000000000b4",
    "CME:PL": "futures_product_version_019f3a01-c0e0-7000-8000-0000000000b5",
    "CME:PA": "futures_product_version_019f3a01-c0e0-7000-8000-0000000000b6",
}

_SEED_VALID_FROM = datetime(2010, 1, 1, tzinfo=UTC)
_SEED_DEFINITION_AS_OF = datetime(2026, 7, 25, tzinfo=UTC)

# CME Globex product identifiers used by public CmeWS endpoints.
CME_GLOBEX_PRODUCT_IDS: dict[str, int] = {
    "GC": 437,
    "MGC": 10176,
    "SI": 458,
    "HG": 438,
    "PL": 446,
    "PA": 445,
}

# Official contract-spec pages (authority for product fields below).
OFFICIAL_CONTRACT_SPEC_URLS: dict[str, str] = {
    "GC": (
        "https://www.cmegroup.com/markets/metals/precious/gold.contractSpecs.html"
    ),
    "MGC": (
        "https://www.cmegroup.com/markets/metals/precious/"
        "micro-gold.contractSpecs.html"
    ),
    "SI": (
        "https://www.cmegroup.com/markets/metals/precious/silver.contractSpecs.html"
    ),
    "HG": (
        "https://www.cmegroup.com/markets/metals/base/copper.contractSpecs.html"
    ),
    "PL": (
        "https://www.cmegroup.com/markets/metals/precious/"
        "platinum.contractSpecs.html"
    ),
    "PA": (
        "https://www.cmegroup.com/markets/metals/precious/"
        "palladium.contractSpecs.html"
    ),
}

# Public delayed-quotes overview (discloses ≥10 minute delay).
CME_DELAYED_QUOTES_URL = (
    "https://www.cmegroup.com/market-data/browse-data/delayed-quotes.html"
)
CME_DAILY_SETTLEMENTS_URL = (
    "https://www.cmegroup.com/market-data/daily-settlements.html"
)
CME_EXPIRATION_CALENDAR_URL = (
    "https://www.cmegroup.com/tools-information/calendars/expiration-calendar.html"
)

SEED_SOURCE = "cme_public_seed"
SEED_SOURCE_DISCLOSURE = (
    "seeded product reference metadata from official CME contract specs; "
    "not live exchange MDP validation"
)


@dataclass(frozen=True, slots=True)
class CmeMetalProductSeed:
    product_key: str
    root: str
    exchange: str
    commodity: str
    currency: str
    price_unit: str
    multiplier: Decimal
    tick_size: Decimal
    settlement_method: SettlementMethod
    session_calendar_id: str
    official_spec_url: str
    cme_globex_product_id: int


# Fields below are taken from the official CME contract-spec pages linked above.
_SEEDS: tuple[CmeMetalProductSeed, ...] = (
    CmeMetalProductSeed(
        product_key="CME:GC",
        root="GC",
        exchange="COMEX",
        commodity="gold",
        currency="USD",
        price_unit="USD/troy_oz",
        multiplier=Decimal("100"),
        tick_size=Decimal("0.1"),
        settlement_method=SettlementMethod.PHYSICAL,
        session_calendar_id="CME_METALS",
        official_spec_url=OFFICIAL_CONTRACT_SPEC_URLS["GC"],
        cme_globex_product_id=CME_GLOBEX_PRODUCT_IDS["GC"],
    ),
    CmeMetalProductSeed(
        product_key="CME:MGC",
        root="MGC",
        exchange="COMEX",
        commodity="gold",
        currency="USD",
        price_unit="USD/troy_oz",
        multiplier=Decimal("10"),
        tick_size=Decimal("0.1"),
        settlement_method=SettlementMethod.PHYSICAL,
        session_calendar_id="CME_METALS",
        official_spec_url=OFFICIAL_CONTRACT_SPEC_URLS["MGC"],
        cme_globex_product_id=CME_GLOBEX_PRODUCT_IDS["MGC"],
    ),
    CmeMetalProductSeed(
        product_key="CME:SI",
        root="SI",
        exchange="COMEX",
        commodity="silver",
        currency="USD",
        price_unit="USD/troy_oz",
        multiplier=Decimal("5000"),
        tick_size=Decimal("0.005"),
        settlement_method=SettlementMethod.PHYSICAL,
        session_calendar_id="CME_METALS",
        official_spec_url=OFFICIAL_CONTRACT_SPEC_URLS["SI"],
        cme_globex_product_id=CME_GLOBEX_PRODUCT_IDS["SI"],
    ),
    CmeMetalProductSeed(
        product_key="CME:HG",
        root="HG",
        exchange="COMEX",
        commodity="copper",
        currency="USD",
        price_unit="USD/lb",
        multiplier=Decimal("25000"),
        tick_size=Decimal("0.0005"),
        settlement_method=SettlementMethod.PHYSICAL,
        session_calendar_id="CME_METALS",
        official_spec_url=OFFICIAL_CONTRACT_SPEC_URLS["HG"],
        cme_globex_product_id=CME_GLOBEX_PRODUCT_IDS["HG"],
    ),
    CmeMetalProductSeed(
        product_key="CME:PL",
        root="PL",
        exchange="NYMEX",
        commodity="platinum",
        currency="USD",
        price_unit="USD/troy_oz",
        multiplier=Decimal("50"),
        tick_size=Decimal("0.1"),
        settlement_method=SettlementMethod.PHYSICAL,
        session_calendar_id="CME_METALS",
        official_spec_url=OFFICIAL_CONTRACT_SPEC_URLS["PL"],
        cme_globex_product_id=CME_GLOBEX_PRODUCT_IDS["PL"],
    ),
    CmeMetalProductSeed(
        product_key="CME:PA",
        root="PA",
        exchange="NYMEX",
        commodity="palladium",
        currency="USD",
        price_unit="USD/troy_oz",
        multiplier=Decimal("100"),
        tick_size=Decimal("0.1"),
        settlement_method=SettlementMethod.PHYSICAL,
        session_calendar_id="CME_METALS",
        official_spec_url=OFFICIAL_CONTRACT_SPEC_URLS["PA"],
        cme_globex_product_id=CME_GLOBEX_PRODUCT_IDS["PA"],
    ),
)


def list_metal_product_seeds() -> tuple[CmeMetalProductSeed, ...]:
    return _SEEDS


def get_metal_product_seed(product_key: str) -> CmeMetalProductSeed | None:
    for seed in _SEEDS:
        if seed.product_key == product_key:
            return seed
    return None


def seed_product_definition(product_key: str) -> FuturesProductDefinition | None:
    """Return a domain product definition for a known CME metal seed, or None."""
    seed = get_metal_product_seed(product_key)
    if seed is None:
        return None
    return FuturesProductDefinition(
        product_id=_PRODUCT_IDS[seed.product_key],
        product_key=seed.product_key,
        root=seed.root,
        market=Market.CME,
        exchange=seed.exchange,
        commodity=seed.commodity,
        currency=seed.currency,
        price_unit=seed.price_unit,
        multiplier=seed.multiplier,
        tick_size=seed.tick_size,
        settlement_method=seed.settlement_method,
        session_calendar_id=seed.session_calendar_id,
        source=SEED_SOURCE,
        valid_from=_SEED_VALID_FROM,
        definition_as_of=_SEED_DEFINITION_AS_OF,
        version_id=_VERSION_IDS[seed.product_key],
        version=1,
        valid_to=None,
    )


def all_seed_product_definitions() -> tuple[FuturesProductDefinition, ...]:
    return tuple(
        definition
        for key in (s.product_key for s in _SEEDS)
        if (definition := seed_product_definition(key)) is not None
    )
