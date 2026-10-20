"""Conservative, labelled option-symbol context; never an execution contract."""

import re
from datetime import date
from decimal import Decimal


def option_symbol_context(instrument_id: str, underlying_id: str) -> dict[str, object] | None:
    # The canonical US ledger accepts compact Moomoo and padded OCC strikes.
    # Adjusted roots (digits), foreign contracts and ambiguous aliases fail closed.
    parts = underlying_id.split(":", 2)
    if len(parts) != 3 or parts[0] not in {"equity", "etf"} or parts[1] != "US":
        return None
    match = re.fullmatch(r"option:US:([A-Z.]+)(\d{6})([CP])(\d{1,8})", instrument_id)
    if match is None or match[1] != parts[2]:
        return None
    try:
        expiry = date(2000 + int(match[2][:2]), int(match[2][2:4]), int(match[2][4:]))
    except ValueError:
        return None
    return {
        "underlying_instrument_id": underlying_id,
        "expiration_date": expiry.isoformat(),
        "option_type": "call" if match[3] == "C" else "put",
        "strike": str(Decimal(match[4]) / Decimal(1000)),
        "identity_basis": "CANONICAL_SYMBOL_DERIVED_UNVERIFIED",
        "multiplier": None,
        "delta": None,
        "warning_codes": ["OPTION_CONTRACT_METADATA_UNVERIFIED"],
    }
