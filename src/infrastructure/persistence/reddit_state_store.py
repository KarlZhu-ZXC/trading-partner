"""SQLite and in-memory state stores for resilient Reddit RSS reads."""

from __future__ import annotations

import json
import threading
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import case, inspect
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.dto.reddit_state import RedditSampleCacheEntry
from application.dto.us_context import USSentimentSampleDTO
from application.ports.clock import Clock
from application.ports.reddit_state_store import RedditStateStore
from application.ports.secret_redactor import SecretRedactor
from domain.common.errors import DataContractError, PersistenceError
from domain.common.time import require_aware_datetime
from domain.us_context.models import USSentimentSample
from infrastructure.persistence.orm import RedditCooldownRow, RedditSampleCacheRow
from infrastructure.persistence.repositories._mapping import dt_from_db, dt_to_db

_COOLDOWN_SCOPE = "anonymous_rss"
_TABLES = frozenset({"reddit_sample_cache", "reddit_provider_cooldown"})


def _encode_samples(samples: tuple[USSentimentSample, ...]) -> str:
    payload = [
        USSentimentSampleDTO.model_validate(sample).model_dump(mode="json") for sample in samples
    ]
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _decode_samples(payload_json: str) -> tuple[USSentimentSample, ...]:
    try:
        payload = json.loads(payload_json)
        if not isinstance(payload, list):
            raise ValueError
        return tuple(
            USSentimentSample(
                instrument_id=dto.instrument_id,
                source=dto.source,
                published_at=dto.published_at,
                text=dto.text,
                direction=dto.direction,
                label_origin=dto.label_origin,
                score=dto.score,
                likes=dto.likes,
                comments=dto.comments,
                url=dto.url,
                classifier_version=dto.classifier_version,
            )
            for dto in (USSentimentSampleDTO.model_validate(item) for item in payload)
        )
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError):
        raise DataContractError(
            "Reddit sample cache payload is invalid",
            details={"field": "payload_json"},
        ) from None


def _persistence_error(operation: str, exc: Exception) -> PersistenceError:
    return PersistenceError(
        f"Failed to {operation} Reddit provider state",
        details={"error_type": type(exc).__name__},
    )


class InMemoryRedditStateStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._samples: dict[tuple[str, str], RedditSampleCacheEntry] = {}
        self._cooldown_until: datetime | None = None

    def get_samples(self, instrument_id: str, config_key: str) -> RedditSampleCacheEntry | None:
        with self._lock:
            return self._samples.get((instrument_id, config_key))

    def set_samples(self, entry: RedditSampleCacheEntry) -> None:
        with self._lock:
            self._samples[(entry.instrument_id, entry.config_key)] = entry

    def get_cooldown_until(self) -> datetime | None:
        with self._lock:
            return self._cooldown_until

    def set_cooldown_until(self, until: datetime, *, updated_at: datetime) -> None:
        require_aware_datetime(until, field_name="until")
        require_aware_datetime(updated_at, field_name="updated_at")
        with self._lock:
            if self._cooldown_until is None or until > self._cooldown_until:
                self._cooldown_until = until


class SqlAlchemyRedditStateStore:
    def __init__(
        self,
        engine: Engine,
        clock: Clock,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._secret_redactor = secret_redactor

    def get_samples(self, instrument_id: str, config_key: str) -> RedditSampleCacheEntry | None:
        try:
            with Session(self._engine) as session:
                row = session.get(RedditSampleCacheRow, (instrument_id, config_key))
                if row is None:
                    return None
                return RedditSampleCacheEntry(
                    instrument_id=row.instrument_id,
                    config_key=row.config_key,
                    samples=_decode_samples(row.payload_json),
                    fetched_at=dt_from_db(row.fetched_at, field_name="fetched_at"),
                    expires_at=dt_from_db(row.expires_at, field_name="expires_at"),
                )
        except (DataContractError, PersistenceError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise _persistence_error("read", exc) from None

    def set_samples(self, entry: RedditSampleCacheEntry) -> None:
        try:
            payload = self._secret_redactor.redact_text(_encode_samples(entry.samples))
            json.loads(payload)
            updated_at = dt_to_db(self._clock.now())
            with Session(self._engine) as session:
                statement = sqlite_insert(RedditSampleCacheRow).values(
                    instrument_id=entry.instrument_id,
                    config_key=entry.config_key,
                    payload_json=payload,
                    fetched_at=dt_to_db(entry.fetched_at),
                    expires_at=dt_to_db(entry.expires_at),
                    updated_at=updated_at,
                )
                session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["instrument_id", "config_key"],
                        set_={
                            "payload_json": statement.excluded.payload_json,
                            "fetched_at": statement.excluded.fetched_at,
                            "expires_at": statement.excluded.expires_at,
                            "updated_at": statement.excluded.updated_at,
                        },
                    )
                )
                session.commit()
        except (DataContractError, PersistenceError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise _persistence_error("write", exc) from None

    def get_cooldown_until(self) -> datetime | None:
        try:
            with Session(self._engine) as session:
                row = session.get(RedditCooldownRow, _COOLDOWN_SCOPE)
                if row is None:
                    return None
                return dt_from_db(row.cooldown_until, field_name="cooldown_until")
        except (DataContractError, PersistenceError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise _persistence_error("read", exc) from None

    def set_cooldown_until(self, until: datetime, *, updated_at: datetime) -> None:
        require_aware_datetime(until, field_name="until")
        require_aware_datetime(updated_at, field_name="updated_at")
        try:
            with Session(self._engine) as session:
                statement = sqlite_insert(RedditCooldownRow).values(
                    scope=_COOLDOWN_SCOPE,
                    cooldown_until=dt_to_db(until),
                    updated_at=dt_to_db(updated_at),
                )
                session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["scope"],
                        set_={
                            "cooldown_until": case(
                                (
                                    RedditCooldownRow.cooldown_until
                                    < statement.excluded.cooldown_until,
                                    statement.excluded.cooldown_until,
                                ),
                                else_=RedditCooldownRow.cooldown_until,
                            ),
                            "updated_at": statement.excluded.updated_at,
                        },
                    )
                )
                session.commit()
        except (DataContractError, PersistenceError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise _persistence_error("write", exc) from None


def build_reddit_state_store(
    engine: Engine,
    clock: Clock,
    secret_redactor: SecretRedactor,
) -> RedditStateStore:
    try:
        tables = set(inspect(engine).get_table_names())
    except Exception:
        tables = set()
    if _TABLES.issubset(tables):
        return SqlAlchemyRedditStateStore(engine, clock, secret_redactor)
    return InMemoryRedditStateStore()
