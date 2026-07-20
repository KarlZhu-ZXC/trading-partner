"""Provider State backend selection (Phase 1D D8b).

Schema-ready SQLite → all three SQL stores; otherwise all three in-memory.
Never mix SQL and memory for a single process — partial persistence would
split cache / health / rate-limit truth.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from application.ports.clock import Clock
from application.ports.provider_cache import ProviderCacheStore
from application.ports.provider_health_store import ProviderHealthStore
from application.ports.provider_rate_limit_store import ProviderRateLimitStore
from application.ports.secret_redactor import SecretRedactor
from infrastructure.persistence.in_memory_provider_state import (
    InMemoryProviderCacheStore,
    InMemoryProviderHealthStore,
    InMemoryProviderRateLimitStore,
)
from infrastructure.persistence.provider_cache_store import SqlAlchemyProviderCacheStore
from infrastructure.persistence.provider_health_store import (
    SqlAlchemyProviderHealthStore,
)
from infrastructure.persistence.provider_rate_limit_store import (
    SqlAlchemyProviderRateLimitStore,
)

# Exact three tables from migration 0003 — no other schema objects.
_PROVIDER_STATE_TABLES: frozenset[str] = frozenset(
    {
        "provider_cache",
        "provider_health",
        "provider_rate_limits",
    }
)


def provider_state_schema_ready(engine: Engine) -> bool:
    """Return True only when all three provider-state tables exist.

    Does not run migrations, create tables, or seed. Inspection failures
    return False safely — never leak database URL, SQL, or raw exception chains.
    """
    try:
        table_names = set(inspect(engine).get_table_names())
    except Exception:
        # from None: no public cause/context with URL/SQL/driver text.
        return False
    return _PROVIDER_STATE_TABLES.issubset(table_names)


@dataclass(frozen=True, slots=True)
class ProviderStateBackend:
    """All-or-nothing provider state store trio for Router wiring."""

    cache_store: ProviderCacheStore
    health_store: ProviderHealthStore
    rate_limit_store: ProviderRateLimitStore
    uses_sql: bool


def build_provider_state_backend(
    engine: Engine,
    clock: Clock,
    secret_redactor: SecretRedactor,
) -> ProviderStateBackend:
    """Select SQL or in-memory stores as a single coherent backend."""
    if provider_state_schema_ready(engine):
        return ProviderStateBackend(
            cache_store=SqlAlchemyProviderCacheStore(
                engine, clock, secret_redactor
            ),
            health_store=SqlAlchemyProviderHealthStore(engine),
            rate_limit_store=SqlAlchemyProviderRateLimitStore(engine),
            uses_sql=True,
        )
    return ProviderStateBackend(
        cache_store=InMemoryProviderCacheStore(secret_redactor),
        health_store=InMemoryProviderHealthStore(),
        rate_limit_store=InMemoryProviderRateLimitStore(),
        uses_sql=False,
    )
