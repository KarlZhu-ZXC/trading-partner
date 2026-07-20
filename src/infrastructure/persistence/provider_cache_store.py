"""SQLAlchemy short-session ProviderCacheStore (Phase 1D D5a)."""

from __future__ import annotations

import json

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.dto.provider_state import CacheEntry
from application.ports.clock import Clock
from application.ports.secret_redactor import SecretRedactor
from domain.common.enums import DataCategory, Freshness, Market, VendorId
from domain.common.errors import DataContractError, PersistenceError
from domain.common.time import require_aware_datetime
from domain.providers.cache_key import (
    parse_cache_key,
    require_cache_key_matches_fields,
)
from infrastructure.persistence.models import ProviderCacheRow
from infrastructure.persistence.repositories._mapping import dt_from_db, dt_to_db


def _row_to_entry(row: ProviderCacheRow) -> CacheEntry:
    return CacheEntry(
        key=row.cache_key,
        category=DataCategory(row.category),
        market=Market(row.market),
        instrument_id=row.instrument_id,
        vendor=VendorId(row.vendor),
        payload_json=row.payload_json,
        as_of=dt_from_db(row.as_of, field_name="as_of"),
        fetched_at=dt_from_db(row.fetched_at, field_name="fetched_at"),
        expires_at=dt_from_db(row.expires_at, field_name="expires_at"),
        freshness=Freshness(row.freshness),
    )


def _persistence_error(operation: str, error_type: str) -> PersistenceError:
    """Build a PersistenceError with only a safe error_type (no raw chain)."""
    return PersistenceError(
        f"Failed to {operation} provider cache",
        details={"error_type": error_type},
    )


class SqlAlchemyProviderCacheStore:
    """Engine-bound cache store: short-lived Session per public method."""

    def __init__(
        self,
        engine: Engine,
        clock: Clock,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._secret_redactor = secret_redactor

    def get(self, key: str) -> CacheEntry | None:
        # Only syntactically valid v1 keys; never echo rejected key/secret.
        parse_cache_key(key)
        error_type: str | None = None
        result: CacheEntry | None = None
        try:
            with Session(self._engine) as session:
                row = session.get(ProviderCacheRow, key)
                if row is not None:
                    result = _row_to_entry(row)
        except DataContractError:
            raise
        except PersistenceError:
            raise
        except Exception as exc:  # noqa: BLE001 — map to PersistenceError
            error_type = type(exc).__name__
        if error_type is not None:
            # Raise outside except so raw SQLAlchemy/DBAPI is not public cause/context.
            raise _persistence_error("get", error_type)
        return result

    def set(self, key: str, entry: CacheEntry) -> None:
        # Key must be valid v1 and field-coherent with entry (no key echo).
        require_cache_key_matches_fields(
            key,
            entry_key=entry.key,
            market=entry.market,
            category=entry.category,
            instrument_id=entry.instrument_id,
            as_of=entry.as_of,
        )
        # Re-validate aware timestamps at the persistence boundary.
        require_aware_datetime(entry.as_of, field_name="as_of")
        require_aware_datetime(entry.fetched_at, field_name="fetched_at")
        require_aware_datetime(entry.expires_at, field_name="expires_at")

        redact_error_type: str | None = None
        redacted: str | None = None
        try:
            redacted = self._secret_redactor.redact_text(entry.payload_json)
        except Exception as exc:  # noqa: BLE001 — safe typed wrap
            redact_error_type = type(exc).__name__
        if redact_error_type is not None:
            raise DataContractError(
                "payload_json redaction failed",
                details={"field": "payload_json", "error_type": redact_error_type},
            )
        assert redacted is not None

        json_error_type: str | None = None
        try:
            json.loads(redacted)
        except json.JSONDecodeError as exc:
            json_error_type = type(exc).__name__
        if json_error_type is not None:
            raise DataContractError(
                "payload_json must remain valid JSON after redaction",
                details={"field": "payload_json", "error_type": json_error_type},
            )

        clock_error_type: str | None = None
        created_at: str | None = None
        try:
            created_at = dt_to_db(self._clock.now())
        except Exception as exc:  # noqa: BLE001
            clock_error_type = type(exc).__name__
        if clock_error_type is not None:
            raise _persistence_error("set", clock_error_type)
        assert created_at is not None

        error_type: str | None = None
        try:
            with Session(self._engine) as session:
                try:
                    row = session.get(ProviderCacheRow, key)
                    if row is None:
                        session.add(
                            ProviderCacheRow(
                                cache_key=key,
                                category=entry.category.value,
                                market=entry.market.value,
                                instrument_id=entry.instrument_id,
                                vendor=entry.vendor.value,
                                payload_json=redacted,
                                as_of=dt_to_db(entry.as_of),
                                fetched_at=dt_to_db(entry.fetched_at),
                                expires_at=dt_to_db(entry.expires_at),
                                freshness=entry.freshness.value,
                                created_at=created_at,
                            )
                        )
                    else:
                        row.category = entry.category.value
                        row.market = entry.market.value
                        row.instrument_id = entry.instrument_id
                        row.vendor = entry.vendor.value
                        row.payload_json = redacted
                        row.as_of = dt_to_db(entry.as_of)
                        row.fetched_at = dt_to_db(entry.fetched_at)
                        row.expires_at = dt_to_db(entry.expires_at)
                        row.freshness = entry.freshness.value
                        # Preserve original created_at on update.
                    session.commit()
                except Exception:
                    session.rollback()
                    raise
        except DataContractError:
            raise
        except PersistenceError:
            raise
        except Exception as exc:  # noqa: BLE001 — map to PersistenceError
            error_type = type(exc).__name__
        if error_type is not None:
            raise _persistence_error("set", error_type)

    def delete(self, key: str) -> None:
        # Only syntactically valid v1 keys; never echo rejected key/secret.
        parse_cache_key(key)
        error_type: str | None = None
        try:
            with Session(self._engine) as session:
                try:
                    row = session.get(ProviderCacheRow, key)
                    if row is not None:
                        session.delete(row)
                    session.commit()
                except Exception:
                    session.rollback()
                    raise
        except DataContractError:
            raise
        except PersistenceError:
            raise
        except Exception as exc:  # noqa: BLE001 — map to PersistenceError
            error_type = type(exc).__name__
        if error_type is not None:
            raise _persistence_error("delete", error_type)
