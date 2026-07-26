"""Versioned built-in DCE live-hog (LH) product reference seed.

Stable official contract specs only (multiplier, tick, settlement method,
contract months). Never seeds or invents a specific contract expiry or EOD
statistic. Consumers must disclose ``DCE_OFFICIAL_REFERENCE_ONLY``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from domain.common.enums import Market
from domain.cross_asset.dce_identity import DCE_LH_CONTRACT_MONTHS, DCE_LH_PRODUCT_KEY
from domain.cross_asset.enums import SettlementMethod
from domain.cross_asset.futures_models import FuturesProductDefinition

# Stable product UUIDs so seed rows are idempotent across restarts.
_PRODUCT_ID = "futures_product_019f3a02-c0e0-7000-8000-0000000000c1"
_VERSION_ID = "futures_product_version_019f3a02-c0e0-7000-8000-0000000000d1"

_SEED_VALID_FROM = datetime(2021, 1, 8, tzinfo=UTC)
_SEED_DEFINITION_AS_OF = datetime(2026, 7, 25, tzinfo=UTC)

# Official DCE live-hog product page (authority for product fields below).
OFFICIAL_LH_SPEC_URL = "https://www.dce.com.cn/dce/channel/list/129.html"
# Official market-data landing page (EOD statistics context).
OFFICIAL_DCE_MARKET_DATA_URL = (
    "https://www.dce.com.cn/dalianshangpin/xqsj/index.html"
)

SEED_SOURCE = "dce_official_seed"
SEED_SOURCE_DISCLOSURE = (
    "seeded product reference metadata from official DCE live-hog contract "
    "specs; specific contract expiries and EOD statistics come only from "
    "DCE official endpoints"
)


@dataclass(frozen=True, slots=True)
class DceLhProductSeed:
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
    contract_months: frozenset[int]


_SEED = DceLhProductSeed(
    product_key=DCE_LH_PRODUCT_KEY,
    root="LH",
    exchange="DCE",
    commodity="live_hogs",
    currency="CNY",
    price_unit="CNY/tonne",
    multiplier=Decimal("16"),
    tick_size=Decimal("5"),
    settlement_method=SettlementMethod.PHYSICAL,
    session_calendar_id="DCE_LH",
    official_spec_url=OFFICIAL_LH_SPEC_URL,
    contract_months=DCE_LH_CONTRACT_MONTHS,
)


def get_lh_product_seed(product_key: str) -> DceLhProductSeed | None:
    if product_key == _SEED.product_key:
        return _SEED
    return None


def seed_product_definition(product_key: str) -> FuturesProductDefinition | None:
    """Return the DCE:LH product definition seed, or None for other keys."""
    seed = get_lh_product_seed(product_key)
    if seed is None:
        return None
    return FuturesProductDefinition(
        product_id=_PRODUCT_ID,
        product_key=seed.product_key,
        root=seed.root,
        market=Market.DCE,
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
        version_id=_VERSION_ID,
        version=1,
        valid_to=None,
    )


def all_seed_product_definitions() -> tuple[FuturesProductDefinition, ...]:
    definition = seed_product_definition(DCE_LH_PRODUCT_KEY)
    return (definition,) if definition is not None else ()
