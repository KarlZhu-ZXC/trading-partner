"""Load instrument master seed into a session-bound repository (no commit)."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from application.ports.instrument_repository import InstrumentRepository
from domain.common.enums import AliasType, AssetType, Market
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument, InstrumentAlias


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise DataContractError(
            f"seed field {key!r} must be a string",
            details={"key": key, "type": type(value).__name__},
        )
    return value


def _require_bool(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise DataContractError(
            f"seed field {key!r} must be a boolean",
            details={"key": key, "type": type(value).__name__},
        )
    return value


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise DataContractError(
            f"seed field {key!r} must be a string or null",
            details={"key": key, "type": type(value).__name__},
        )
    return value


def _parse_instrument(row: dict[str, Any]) -> Instrument:
    """Build a domain Instrument; validation runs in entity __post_init__."""
    asset_type = AssetType(_require_str(row, "asset_type"))
    market = Market(_require_str(row, "market"))
    metadata_version = row.get("metadata_version", 1)
    if not isinstance(metadata_version, int):
        raise DataContractError(
            "seed field 'metadata_version' must be an int",
            details={"type": type(metadata_version).__name__},
        )
    return Instrument(
        instrument_id=_require_str(row, "instrument_id"),
        symbol=_require_str(row, "symbol"),
        name=_require_str(row, "name"),
        market=market,
        exchange=_require_str(row, "exchange"),
        currency=_require_str(row, "currency"),
        timezone=_require_str(row, "timezone"),
        asset_type=asset_type,
        is_active=_require_bool(row, "is_active") if "is_active" in row else True,
        listing_status=(
            _require_str(row, "listing_status") if "listing_status" in row else "active"
        ),
        country=_optional_str(row, "country"),
        mic=_optional_str(row, "mic"),
        underlying_instrument_id=_optional_str(row, "underlying_instrument_id"),
        multiplier=_optional_decimal(row.get("multiplier")),
        tick_size=_optional_decimal(row.get("tick_size")),
        lot_size=_optional_decimal(row.get("lot_size")),
        metadata_version=metadata_version,
    )


def _parse_alias(
    alias_row: dict[str, Any],
    *,
    instrument: Instrument,
    default_created_at: datetime,
) -> InstrumentAlias:
    """Build a domain InstrumentAlias; validation runs in entity __post_init__."""
    created_raw = alias_row.get("created_at")
    if created_raw is None:
        created_at = default_created_at
    elif isinstance(created_raw, str):
        created_at = require_aware_datetime(
            datetime.fromisoformat(created_raw),
            field_name="created_at",
        )
    else:
        raise DataContractError(
            "alias created_at must be an ISO 8601 string when provided",
            details={"type": type(created_raw).__name__},
        )
    return InstrumentAlias(
        alias_id=_require_str(alias_row, "alias_id"),
        instrument_id=instrument.instrument_id,
        alias_type=AliasType(_require_str(alias_row, "alias_type")),
        alias_value=_require_str(alias_row, "alias_value"),
        alias_value_raw=_require_str(alias_row, "alias_value_raw"),
        market=instrument.market,
        source=_require_str(alias_row, "source"),
        is_primary=_require_bool(alias_row, "is_primary"),
        created_at=created_at,
    )


class InstrumentSeedLoader:
    """Load canonical seed instruments when the master table is empty.

    Does not commit; callers must use an InstrumentUnitOfWork and commit.
    Skips the entire load when any instrument already exists (``count() > 0``).
    """

    def load_if_empty(self, repo: InstrumentRepository, path: Path) -> int:
        if repo.count() != 0:
            return 0

        raw_text = path.read_text(encoding="utf-8")
        payload = json.loads(raw_text)
        if not isinstance(payload, dict):
            raise DataContractError(
                "instrument seed root must be a JSON object",
                details={"type": type(payload).__name__},
            )
        instruments_raw = payload.get("instruments")
        if not isinstance(instruments_raw, list):
            raise DataContractError(
                "instrument seed must contain an 'instruments' array",
                details={"type": type(instruments_raw).__name__},
            )

        seeded_at_raw = payload.get("seeded_at", "2026-07-16T00:00:00+00:00")
        if not isinstance(seeded_at_raw, str):
            raise DataContractError(
                "seeded_at must be an ISO 8601 string",
                details={"type": type(seeded_at_raw).__name__},
            )
        default_created_at = require_aware_datetime(
            datetime.fromisoformat(seeded_at_raw),
            field_name="seeded_at",
        )

        loaded = 0
        for item in instruments_raw:
            if not isinstance(item, dict):
                raise DataContractError(
                    "each seed instrument must be a JSON object",
                    details={"type": type(item).__name__},
                )
            instrument = _parse_instrument(item)
            repo.upsert_instrument(instrument)
            aliases_raw = item.get("aliases", [])
            if not isinstance(aliases_raw, list):
                raise DataContractError(
                    "instrument aliases must be a JSON array",
                    details={
                        "instrument_id": instrument.instrument_id,
                        "type": type(aliases_raw).__name__,
                    },
                )
            for alias_item in aliases_raw:
                if not isinstance(alias_item, dict):
                    raise DataContractError(
                        "each seed alias must be a JSON object",
                        details={
                            "instrument_id": instrument.instrument_id,
                            "type": type(alias_item).__name__,
                        },
                    )
                alias = _parse_alias(
                    alias_item,
                    instrument=instrument,
                    default_created_at=default_created_at,
                )
                repo.upsert_alias(alias)
            loaded += 1
        return loaded
