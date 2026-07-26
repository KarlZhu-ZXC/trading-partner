"""CME metal futures contract and continuous-series identity grammar.

Pure domain helpers. Accepts only roots GC/MGC/SI/HG/PL/PA and validated
contract-code / continuous-series symbols. Never rewrites legacy ``future:US:*``
Yahoo continuous proxies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from domain.common.enums import AssetType, Market
from domain.common.errors import DataContractError, InvalidInstrument
from domain.common.values import build_instrument_id, parse_instrument_id
from domain.cross_asset.enums import RollRule

# CME month codes (Globex) → calendar month number.
_MONTH_CODE_TO_NUM: dict[str, int] = {
    "F": 1,
    "G": 2,
    "H": 3,
    "J": 4,
    "K": 5,
    "M": 6,
    "N": 7,
    "Q": 8,
    "U": 9,
    "V": 10,
    "X": 11,
    "Z": 12,
}
_NUM_TO_MONTH_CODE: dict[int, str] = {v: k for k, v in _MONTH_CODE_TO_NUM.items()}

CME_METAL_ROOTS: frozenset[str] = frozenset({"GC", "MGC", "SI", "HG", "PL", "PA"})

# Yahoo active-contract exchange suffix. COMEX metals use .CMX; NYMEX use .NYM.
_YAHOO_EXCHANGE_SUFFIX: dict[str, str] = {
    "GC": "CMX",
    "MGC": "CMX",
    "SI": "CMX",
    "HG": "CMX",
    "PL": "NYM",
    "PA": "NYM",
}

_ROOT_EXCHANGE: dict[str, str] = {
    "GC": "COMEX",
    "MGC": "COMEX",
    "SI": "COMEX",
    "HG": "COMEX",
    "PL": "NYMEX",
    "PA": "NYMEX",
}

# Contract: ROOT + month-code + two-digit year, e.g. GCZ26 / MGCZ26.
_CONTRACT_CODE_RE = re.compile(
    r"^(?P<root>GC|MGC|SI|HG|PL|PA)(?P<month>[FGHJKMNQUVXZ])(?P<yy>\d{2})$"
)
# Continuous: ROOT.c.0 / ROOT.v.0 / ROOT.oi.0
_CONTINUOUS_CODE_RE = re.compile(
    r"^(?P<root>GC|MGC|SI|HG|PL|PA)\.(?P<rule>c|v|oi)\.(?P<rank>\d+)$"
)
_ROLL_RULE_WIRE: dict[str, RollRule] = {
    "c": RollRule.CALENDAR,
    "v": RollRule.VOLUME,
    "oi": RollRule.OPEN_INTEREST,
}
_ROLL_RULE_TO_WIRE: dict[RollRule, str] = {
    RollRule.CALENDAR: "c",
    RollRule.VOLUME: "v",
    RollRule.OPEN_INTEREST: "oi",
}


@dataclass(frozen=True, slots=True)
class CmeContractCode:
    """Parsed specific CME metal futures contract code."""

    root: str
    month_code: str
    year: int
    contract_month: str  # YYYY-MM
    symbol: str  # e.g. GCZ26

    @property
    def exchange(self) -> str:
        return _ROOT_EXCHANGE[self.root]

    @property
    def yahoo_symbol(self) -> str:
        return f"{self.symbol}.{_YAHOO_EXCHANGE_SUFFIX[self.root]}"

    @property
    def instrument_id(self) -> str:
        return build_instrument_id(AssetType.FUTURE, Market.CME, self.symbol)

    @property
    def product_key(self) -> str:
        return f"CME:{self.root}"


@dataclass(frozen=True, slots=True)
class CmeContinuousCode:
    """Parsed ruled continuous series identity under Market.CME."""

    root: str
    roll_rule: RollRule
    rank: int
    symbol: str  # e.g. GC.v.0

    @property
    def instrument_id(self) -> str:
        return build_instrument_id(AssetType.FUTURE, Market.CME, self.symbol)

    @property
    def product_key(self) -> str:
        return f"CME:{self.root}"


def is_legacy_us_continuous_proxy(instrument_id: str) -> bool:
    """True for existing Yahoo continuous proxies such as ``future:US:GC=F``."""
    try:
        asset_type, market, symbol = parse_instrument_id(instrument_id)
    except DataContractError:
        return False
    return (
        asset_type is AssetType.FUTURE
        and market is Market.US
        and symbol.endswith("=F")
    )


def parse_cme_contract_code(symbol: str) -> CmeContractCode:
    """Parse a specific CME metal contract code; fail closed on invalid grammar."""
    if not isinstance(symbol, str) or not symbol.strip():
        raise InvalidInstrument(
            "CME contract code must be a non-blank string",
            details={"field": "symbol", "rule": "cme_contract_grammar"},
        )
    compact = symbol.strip().upper()
    match = _CONTRACT_CODE_RE.fullmatch(compact)
    if match is None:
        raise InvalidInstrument(
            "CME contract code must match ROOT+month+YY for GC/MGC/SI/HG/PL/PA",
            details={
                "field": "symbol",
                "rule": "cme_contract_grammar",
                "allowed_roots": sorted(CME_METAL_ROOTS),
            },
        )
    root = match.group("root")
    month_code = match.group("month")
    yy = int(match.group("yy"))
    # Two-digit years 00-79 → 2000-2079; 80-99 → 1980-1999 (futures horizon).
    year = 2000 + yy if yy < 80 else 1900 + yy
    month_num = _MONTH_CODE_TO_NUM[month_code]
    return CmeContractCode(
        root=root,
        month_code=month_code,
        year=year,
        contract_month=f"{year:04d}-{month_num:02d}",
        symbol=compact,
    )


def parse_cme_continuous_code(symbol: str) -> CmeContinuousCode:
    """Parse a ruled continuous series symbol such as ``GC.v.0``."""
    if not isinstance(symbol, str) or not symbol.strip():
        raise InvalidInstrument(
            "CME continuous symbol must be a non-blank string",
            details={"field": "symbol", "rule": "cme_continuous_grammar"},
        )
    parts = symbol.strip().upper().split(".")
    if len(parts) != 3 or parts[1] not in {"C", "V", "OI"}:
        raise InvalidInstrument(
            "CME continuous symbol must match ROOT.{c|v|oi}.RANK",
            details={
                "field": "symbol",
                "rule": "cme_continuous_grammar",
                "allowed_roots": sorted(CME_METAL_ROOTS),
            },
        )
    rule_wire = {"C": "c", "V": "v", "OI": "oi"}[parts[1]]
    normalized = f"{parts[0]}.{rule_wire}.{parts[2]}"
    match = _CONTINUOUS_CODE_RE.fullmatch(normalized)
    if match is None:
        raise InvalidInstrument(
            "CME continuous symbol must match ROOT.{c|v|oi}.RANK",
            details={
                "field": "symbol",
                "rule": "cme_continuous_grammar",
                "allowed_roots": sorted(CME_METAL_ROOTS),
            },
        )
    root = match.group("root")
    roll_rule = _ROLL_RULE_WIRE[match.group("rule")]
    rank = int(match.group("rank"))
    symbol_out = f"{root}.{match.group('rule')}.{rank}"
    return CmeContinuousCode(
        root=root,
        roll_rule=roll_rule,
        rank=rank,
        symbol=symbol_out,
    )


def build_cme_continuous_symbol(root: str, roll_rule: RollRule, rank: int) -> str:
    """Build a continuous-series symbol from root, roll rule, and rank."""
    root_text = root.strip().upper()
    if root_text not in CME_METAL_ROOTS:
        raise InvalidInstrument(
            "unsupported CME metal root",
            details={"root": root_text, "allowed_roots": sorted(CME_METAL_ROOTS)},
        )
    if type(rank) is not int or rank < 0:
        raise DataContractError(
            "rank must be a nonnegative int",
            details={"rank": rank},
        )
    if not isinstance(roll_rule, RollRule):
        raise DataContractError("roll_rule must be RollRule")
    return f"{root_text}.{_ROLL_RULE_TO_WIRE[roll_rule]}.{rank}"


def yahoo_symbol_for_cme_instrument(instrument_id: str) -> str:
    """Map ``future:CME:GCZ26`` → ``GCZ26.CMX``. Rejects continuous and legacy US."""
    if is_legacy_us_continuous_proxy(instrument_id):
        raise InvalidInstrument(
            "legacy future:US:* continuous proxy is not a CME specific contract",
            details={"instrument_id": instrument_id, "rule": "no_us_proxy_rewrite"},
        )
    asset_type, market, symbol = parse_instrument_id(instrument_id)
    if asset_type is not AssetType.FUTURE or market is not Market.CME:
        raise InvalidInstrument(
            "Yahoo active-contract mapping requires future:CME:* specific contracts",
            details={
                "instrument_id": instrument_id,
                "rule": "cme_specific_only",
            },
        )
    # Continuous series are not Yahoo active-contract symbols.
    if "." in symbol:
        raise InvalidInstrument(
            "continuous series cannot map to a Yahoo active contract",
            details={"instrument_id": instrument_id, "rule": "not_specific_contract"},
        )
    return parse_cme_contract_code(symbol).yahoo_symbol


def contract_month_from_month_code(month_code: str, year: int) -> str:
    """Return YYYY-MM from a CME month code and full year."""
    code = month_code.strip().upper()
    if code not in _MONTH_CODE_TO_NUM:
        raise DataContractError(
            "unknown CME month code",
            details={"month_code": code},
        )
    if type(year) is not int or year < 1900 or year > 2100:
        raise DataContractError(
            "year must be a plausible four-digit year",
            details={"year": year},
        )
    return f"{year:04d}-{_MONTH_CODE_TO_NUM[code]:02d}"


def month_code_from_contract_month(contract_month: str) -> str:
    """Return CME month code for a YYYY-MM contract month."""
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", contract_month):
        raise DataContractError(
            "contract_month must use YYYY-MM",
            details={"contract_month": contract_month},
        )
    month_num = int(contract_month.split("-", 1)[1])
    return _NUM_TO_MONTH_CODE[month_num]


def contract_code_from_parts(root: str, contract_month: str) -> str:
    """Build e.g. GCZ26 from root GC and contract_month 2026-12."""
    root_text = root.strip().upper()
    if root_text not in CME_METAL_ROOTS:
        raise InvalidInstrument(
            "unsupported CME metal root",
            details={"root": root_text},
        )
    year = int(contract_month[:4])
    month_code = month_code_from_contract_month(contract_month)
    return f"{root_text}{month_code}{year % 100:02d}"


def exchange_for_root(root: str) -> str:
    root_text = root.strip().upper()
    if root_text not in _ROOT_EXCHANGE:
        raise InvalidInstrument(
            "unsupported CME metal root",
            details={"root": root_text},
        )
    return _ROOT_EXCHANGE[root_text]


def require_not_legacy_us_proxy(instrument_id: str, *, field: str = "instrument_id") -> None:
    """Refuse silent rewrites of legacy Yahoo continuous proxies."""
    if is_legacy_us_continuous_proxy(instrument_id):
        raise DataContractError(
            "legacy future:US:* continuous proxy must not be rewritten",
            details={"field": field, "instrument_id": instrument_id},
        )


def as_of_date_ny(as_of_date_value: date) -> date:
    """Identity helper for tests / call sites that need an explicit date type."""
    if type(as_of_date_value) is not date:
        raise DataContractError(
            "expected date",
            details={"type": type(as_of_date_value).__name__},
        )
    return as_of_date_value
