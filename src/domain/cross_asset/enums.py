"""Cross-asset futures/spot/basis enums (Phase 3A).

Wire-format values are the member ``value`` strings. Once persisted or exposed
in Tool Schema they must not change without a migration.
"""

from enum import StrEnum


class SettlementMethod(StrEnum):
    PHYSICAL = "physical"
    CASH = "cash"
    UNKNOWN = "unknown"


class ContractLifecycleStatus(StrEnum):
    LISTED = "listed"
    ACTIVE = "active"
    EXPIRED = "expired"
    DELISTED = "delisted"
    UNKNOWN = "unknown"


class SettlementStatus(StrEnum):
    PRELIMINARY = "preliminary"
    FINAL = "final"
    UNKNOWN = "unknown"


class RollRule(StrEnum):
    CALENDAR = "calendar"
    VOLUME = "volume"
    OPEN_INTEREST = "open_interest"


class ContinuousAdjustment(StrEnum):
    """Phase 3A supports unadjusted continuous series only."""

    NONE = "none"


class PriceBasis(StrEnum):
    LAST = "last"
    MID = "mid"
    SETTLEMENT = "settlement"


class CurveShape(StrEnum):
    CONTANGO = "CONTANGO"
    BACKWARDATION = "BACKWARDATION"
    MIXED = "MIXED"
    NOT_EVALUATED = "NOT_EVALUATED"


class CurveCompleteness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    EMPTY = "empty"


class SpotVenueBasis(StrEnum):
    """How a spot observation is sourced; never re-label broker feed as LBMA."""

    DUKASCOPY_SWFX = "dukascopy_swfx"
    AGGREGATED_OTC = "aggregated_otc"
    EXCHANGE_REFERENCE = "exchange_reference"
    UNKNOWN = "unknown"


class OfferSide(StrEnum):
    """Dukascopy historicalPrices offerSide wire values (bid/ask book side)."""

    BID = "B"
    ASK = "A"


class SpotVolumeBasis(StrEnum):
    """How bar volume is defined; Dukascopy is not exchange traded volume."""

    BEST_BID_ASK_VOLUME = "best_bid_ask_volume"
    EXCHANGE_TRADED = "exchange_traded"
    UNKNOWN = "unknown"


class BasisComparability(StrEnum):
    COMPARABLE = "COMPARABLE"
    INDICATIVE_ONLY = "INDICATIVE_ONLY"
    NOT_COMPARABLE = "NOT_COMPARABLE"
