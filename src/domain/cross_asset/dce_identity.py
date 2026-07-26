"""DCE live-hog (LH) futures contract identity grammar.

Pure domain helpers. Phase 3A-4 supports product ``DCE:LH`` only. Specific
contracts use ``LH`` + four-digit ``YYMM`` (e.g. ``LH2609``). Never invents
expiries or continuous main-contract substitutions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from domain.common.enums import AssetType, Market
from domain.common.errors import DataContractError, InvalidInstrument
from domain.common.values import build_instrument_id, parse_instrument_id

DCE_LH_ROOT = "LH"
DCE_LH_PRODUCT_KEY = "DCE:LH"
# Official DCE live-hog contract months: odd months only.
DCE_LH_CONTRACT_MONTHS: frozenset[int] = frozenset({1, 3, 5, 7, 9, 11})

_CONTRACT_CODE_RE = re.compile(r"^(?P<root>LH)(?P<yy>\d{2})(?P<mm>\d{2})$")


@dataclass(frozen=True, slots=True)
class DceLhContractCode:
    """Parsed specific DCE live-hog futures contract code."""

    root: str
    year: int
    month: int
    contract_month: str  # YYYY-MM
    symbol: str  # e.g. LH2609

    @property
    def exchange(self) -> str:
        return "DCE"

    @property
    def instrument_id(self) -> str:
        return build_instrument_id(AssetType.FUTURE, Market.DCE, self.symbol)

    @property
    def product_key(self) -> str:
        return DCE_LH_PRODUCT_KEY


def parse_dce_lh_contract_code(symbol: str) -> DceLhContractCode:
    """Parse ``LHYYMM``; fail closed on invalid grammar or disallowed months."""
    if not isinstance(symbol, str) or not symbol.strip():
        raise InvalidInstrument(
            "DCE LH contract code must be a non-blank string",
            details={"field": "symbol", "rule": "dce_lh_contract_grammar"},
        )
    compact = symbol.strip().upper()
    match = _CONTRACT_CODE_RE.fullmatch(compact)
    if match is None:
        raise InvalidInstrument(
            "DCE LH contract code must match LH + YYMM",
            details={
                "field": "symbol",
                "rule": "dce_lh_contract_grammar",
                "root": DCE_LH_ROOT,
            },
        )
    yy = int(match.group("yy"))
    mm = int(match.group("mm"))
    if mm not in DCE_LH_CONTRACT_MONTHS:
        raise InvalidInstrument(
            "DCE LH contract month must be one of 01/03/05/07/09/11",
            details={
                "field": "symbol",
                "rule": "dce_lh_contract_months",
                "month": mm,
                "allowed_months": sorted(DCE_LH_CONTRACT_MONTHS),
            },
        )
    # Two-digit years 00-79 → 2000-2079; 80-99 → 1980-1999 (futures horizon).
    year = 2000 + yy if yy < 80 else 1900 + yy
    return DceLhContractCode(
        root=DCE_LH_ROOT,
        year=year,
        month=mm,
        contract_month=f"{year:04d}-{mm:02d}",
        symbol=compact,
    )


def parse_dce_lh_instrument_id(instrument_id: str) -> DceLhContractCode:
    """Parse ``future:DCE:LHYYMM`` into a validated contract code."""
    try:
        asset_type, market, symbol = parse_instrument_id(instrument_id)
    except DataContractError as exc:
        raise InvalidInstrument(
            "DCE LH instrument_id must be well-formed",
            details={
                "field": "instrument_id",
                "rule": "instrument_id_syntax",
            },
        ) from exc
    if asset_type is not AssetType.FUTURE or market is not Market.DCE:
        raise InvalidInstrument(
            "DCE LH mapping requires future:DCE:* specific contracts",
            details={
                "instrument_id": instrument_id,
                "rule": "dce_lh_specific_only",
            },
        )
    return parse_dce_lh_contract_code(symbol)


def normalize_dce_contract_id(raw: object) -> str | None:
    """Normalize a DCE wire ``contractId`` to ``LHYYMM`` or return None if skippable.

    Accepts lowercase/uppercase LH rows. Rejects options, spreads, blanks, and
    summary markers without raising (callers may hard-fail malformed futures).
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    # Options / series use dashes or non-LH alphanumerics.
    if "-" in text or "/" in text or " " in text:
        return None
    compact = text.upper()
    if compact in {"小计", "总计", "合计"}:
        return None
    try:
        return parse_dce_lh_contract_code(compact).symbol
    except InvalidInstrument:
        return None
