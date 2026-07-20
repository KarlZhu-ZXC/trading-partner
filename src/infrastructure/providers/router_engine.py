"""ProviderRouterEngine — vendor chain resilience orchestration (Phase 1D D6b2).

Implements ``ProviderRouterEnginePort``. Does not read YAML or resolve tool
policy; chain and criticality are already resolved by ``ProviderRouter``.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timedelta

from application.dto.provider_routing import (
    ProviderAttemptRecord,
    ProviderSuccess,
    RouterExecutionResult,
)
from application.dto.provider_state import CacheEntry
from application.dto.tool_envelope import WarningInfo
from application.ports.category_provider import CategoryProvider
from application.ports.clock import Clock
from application.ports.provider_cache import ProviderCacheStore
from application.ports.provider_cache_codec import ProviderCacheCodec
from application.ports.provider_health_store import ProviderHealthStore
from application.ports.provider_router_settings import ProviderRouterSettings
from domain.common.enums import (
    CacheDisposition,
    CircuitState,
    DataCategory,
    DataCriticality,
    Market,
    ProviderAttemptOutcome,
    SourceRole,
    VendorId,
)
from domain.common.errors import (
    DataContractError,
    NoMarketData,
    ProviderAuthenticationError,
    ProviderNotConfigured,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    StaleMarketData,
    TradingPartnerError,
)
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument
from domain.market.models import VerifiedMarketSnapshot
from domain.market.stale_guard import StaleGuardConfig, assert_ohlcv_not_stale
from domain.providers.cache_key import build_cache_key
from infrastructure.providers.common.circuit_breaker import CircuitBreaker
from infrastructure.providers.common.rate_limiter import ProviderRateLimiter
from infrastructure.providers.common.retry import (
    DEFAULT_RETRYABLE_ERROR_TYPES,
    RetryPolicy,
    run_with_retry,
)
from infrastructure.providers.common.timeout import run_with_timeout
from infrastructure.providers.registry import VendorRegistry

# operation_name grammar — same as cache fingerprint operation segment.
_OPERATION_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")

# Fixed warning messages (rule 18) — never splice adapter/Store/raw exceptions.
_MSG_CACHE_SERVED = "Result served from provider cache"
_MSG_CACHE_UNAVAILABLE = "Provider cache store unavailable"
_MSG_CACHE_ENTRY_REJECTED = "Cached provider entry rejected"
_MSG_STALE_DATA_REJECTED = "Stale market data rejected"
_MSG_PARTIAL_VENDOR_CHAIN = "Vendor missing from registry"
_MSG_RATE_LIMIT_DEGRADED = "Vendor skipped due to rate limit"
_MSG_CIRCUIT_OPEN_SKIPPED = "Vendor skipped because circuit is open"
_MSG_PROVIDER_HEALTH_UNAVAILABLE = "Provider health projection unavailable"
_MSG_FALLBACK_VENDOR_USED = "Fallback vendor served the request"
_MSG_OPTIONAL_DATA_UNAVAILABLE = "Optional data category unavailable"

# Chain-exhaustion aggregation priority (highest first) — design §11.3.
_ERROR_PRIORITY: tuple[type[TradingPartnerError], ...] = (
    DataContractError,
    ProviderAuthenticationError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderRateLimitError,
    StaleMarketData,
    NoMarketData,
    ProviderNotConfigured,
)


class _RateLimitDenied(Exception):
    """Internal: fixed-window limiter denied admission (not a public error)."""


class ProviderRouterEngine:
    """Infrastructure engine: cache, retry, breaker, rate-limit, health, fallback."""

    def __init__(
        self,
        *,
        registry: VendorRegistry,
        cache_store: ProviderCacheStore,
        health_store: ProviderHealthStore,
        rate_limiter: ProviderRateLimiter,
        circuit_breaker: CircuitBreaker,
        clock: Clock,
        settings: ProviderRouterSettings,
    ) -> None:
        self._registry = registry
        self._cache_store = cache_store
        self._health_store = health_store
        self._rate_limiter = rate_limiter
        self._circuit_breaker = circuit_breaker
        self._clock = clock
        self._settings = settings

    async def execute[T](
        self,
        *,
        market: Market,
        category: DataCategory,
        chain: tuple[VendorId, ...],
        criticality: DataCriticality,
        call: Callable[[CategoryProvider], Awaitable[ProviderSuccess[T]]],
        operation_name: str,
        request_fingerprint: str,
        instrument: Instrument | None,
        as_of: datetime,
        bypass_cache: bool,
        cache_codec: ProviderCacheCodec[T] | None,
        result_validator: Callable[[ProviderSuccess[T]], None] | None,
    ) -> RouterExecutionResult[T]:
        require_aware_datetime(as_of, field_name="as_of")
        self._validate_fingerprint_inputs(operation_name, request_fingerprint)
        call_fingerprint = self._build_call_fingerprint(
            operation_name, request_fingerprint
        )

        cache_enabled = (
            bool(self._settings.enable_provider_cache)
            and not bypass_cache
            and cache_codec is not None
        )

        warnings: list[WarningInfo] = []
        attempts: list[ProviderAttemptRecord] = []
        collected_errors: list[TradingPartnerError] = []

        instrument_id = (
            instrument.instrument_id if instrument is not None else None
        )

        if cache_enabled:
            assert cache_codec is not None
            cache_hit = await self._try_cache_hit(
                market=market,
                category=category,
                instrument_id=instrument_id,
                as_of=as_of,
                call_fingerprint=call_fingerprint,
                operation_name=operation_name,
                cache_codec=cache_codec,
                result_validator=result_validator,
                criticality=criticality,
                warnings=warnings,
            )
            if cache_hit is not None:
                return cache_hit

        if not chain:
            return self._failure_result(
                criticality=criticality,
                attempts=tuple(attempts),
                warnings=tuple(warnings),
                error=ProviderNotConfigured(
                    "Vendor chain is empty",
                    details={
                        "category": category.value,
                        "market": market.value,
                    },
                ),
                operation_name=operation_name,
                category=category,
            )

        for index, vendor_id in enumerate(chain):
            started = time.monotonic()
            attempt, success, stop_chain = await self._attempt_vendor(
                vendor_id=vendor_id,
                chain_index=index,
                market=market,
                category=category,
                call=call,
                as_of=as_of,
                operation_name=operation_name,
                cache_enabled=cache_enabled,
                cache_codec=cache_codec,
                result_validator=result_validator,
                instrument_id=instrument_id,
                call_fingerprint=call_fingerprint,
                warnings=warnings,
                collected_errors=collected_errors,
            )
            duration_ms = max(0, int((time.monotonic() - started) * 1000))
            attempts.append(
                ProviderAttemptRecord(
                    vendor=vendor_id,
                    outcome=attempt.outcome,
                    error_code=attempt.error_code,
                    duration_ms=duration_ms,
                    message=attempt.message,
                )
            )
            if success is not None:
                return RouterExecutionResult(
                    value=success.value,
                    ok=True,
                    criticality=criticality,
                    meta=success.meta,
                    attempts=tuple(attempts),
                    warnings=tuple(warnings),
                    error=None,
                )
            if stop_chain:
                break

        error = self._aggregate_errors(
            collected_errors,
            market=market,
            category=category,
            chain=chain,
            instrument_id=instrument_id,
        )
        return self._failure_result(
            criticality=criticality,
            attempts=tuple(attempts),
            warnings=tuple(warnings),
            error=error,
            operation_name=operation_name,
            category=category,
        )

    # --- fingerprint / cache -------------------------------------------------

    @staticmethod
    def _validate_fingerprint_inputs(
        operation_name: object, request_fingerprint: object
    ) -> None:
        if not isinstance(operation_name, str) or not _OPERATION_NAME_RE.fullmatch(
            operation_name
        ):
            raise DataContractError(
                "operation_name must match cache fingerprint operation grammar",
                details={"field": "operation_name"},
            )
        if not isinstance(request_fingerprint, str) or not request_fingerprint:
            # Never echo request_fingerprint (may contain secrets).
            raise DataContractError(
                "request_fingerprint must be a non-empty string",
                details={"field": "request_fingerprint"},
            )

    @staticmethod
    def _build_call_fingerprint(
        operation_name: str, request_fingerprint: str
    ) -> str:
        digest = hashlib.sha256(request_fingerprint.encode("utf-8")).hexdigest()[
            :16
        ]
        return f"{operation_name}|{digest}"

    async def _try_cache_hit[T](
        self,
        *,
        market: Market,
        category: DataCategory,
        instrument_id: str | None,
        as_of: datetime,
        call_fingerprint: str,
        operation_name: str,
        cache_codec: ProviderCacheCodec[T],
        result_validator: Callable[[ProviderSuccess[T]], None] | None,
        criticality: DataCriticality,
        warnings: list[WarningInfo],
    ) -> RouterExecutionResult[T] | None:
        try:
            cache_key = build_cache_key(
                market, category, instrument_id, as_of, call_fingerprint
            )
        except DataContractError:
            # Invalid identity/fingerprint already validated; re-raise contract.
            raise

        entry: CacheEntry | None
        try:
            entry = self._cache_store.get(cache_key)
        except Exception:
            self._add_warning_once(
                warnings,
                code="CACHE_UNAVAILABLE",
                message=_MSG_CACHE_UNAVAILABLE,
                category=category,
                operation_name=operation_name,
            )
            return None

        if entry is None:
            return None

        now = require_aware_datetime(self._clock.now(), field_name="clock.now")
        if now >= entry.expires_at:
            self._best_effort_cache_delete(
                cache_key,
                warnings=warnings,
                category=category,
                operation_name=operation_name,
            )
            return None

        try:
            decoded = cache_codec.decode(entry)
            self._validate_cache_success_coherence(
                decoded, category=category, as_of=as_of
            )
            if result_validator is not None:
                result_validator(decoded)
            self._apply_stale_guard(
                decoded, category=category, as_of=as_of
            )
        except StaleMarketData:
            self._best_effort_cache_delete(
                cache_key,
                warnings=warnings,
                category=category,
                operation_name=operation_name,
            )
            self._add_warning_once(
                warnings,
                code="STALE_DATA_REJECTED",
                message=_MSG_STALE_DATA_REJECTED,
                category=category,
                operation_name=operation_name,
                vendor=entry.vendor.value,
            )
            return None
        except (DataContractError, TradingPartnerError):
            self._best_effort_cache_delete(
                cache_key,
                warnings=warnings,
                category=category,
                operation_name=operation_name,
            )
            self._add_warning_once(
                warnings,
                code="CACHE_ENTRY_REJECTED",
                message=_MSG_CACHE_ENTRY_REJECTED,
                category=category,
                operation_name=operation_name,
                vendor=entry.vendor.value,
            )
            return None
        except Exception:
            self._best_effort_cache_delete(
                cache_key,
                warnings=warnings,
                category=category,
                operation_name=operation_name,
            )
            self._add_warning_once(
                warnings,
                code="CACHE_ENTRY_REJECTED",
                message=_MSG_CACHE_ENTRY_REJECTED,
                category=category,
                operation_name=operation_name,
                vendor=entry.vendor.value,
            )
            return None

        self._add_warning(
            warnings,
            code="CACHE_SERVED",
            message=_MSG_CACHE_SERVED,
            category=category,
            operation_name=operation_name,
            vendor=decoded.meta.vendor.value,
        )
        return RouterExecutionResult(
            value=decoded.value,
            ok=True,
            criticality=criticality,
            meta=decoded.meta,
            attempts=(),
            warnings=tuple(warnings),
            error=None,
        )

    def _validate_cache_success_coherence[T](
        self,
        success: ProviderSuccess[T],
        *,
        category: DataCategory,
        as_of: datetime,
    ) -> None:
        if success.meta.category is not category:
            raise DataContractError(
                "cached meta.category must match request category",
                details={"field": "meta.category", "rule": "coherence"},
            )
        if success.meta.as_of > as_of:
            raise DataContractError(
                "cached meta.as_of must be <= requested as_of",
                details={"field": "meta.as_of", "rule": "as_of_window"},
            )

    def _best_effort_cache_delete(
        self,
        key: str,
        *,
        warnings: list[WarningInfo],
        category: DataCategory,
        operation_name: str,
    ) -> None:
        try:
            self._cache_store.delete(key)
        except Exception:
            self._add_warning_once(
                warnings,
                code="CACHE_UNAVAILABLE",
                message=_MSG_CACHE_UNAVAILABLE,
                category=category,
                operation_name=operation_name,
            )

    def _best_effort_cache_set(
        self,
        *,
        key: str,
        entry: CacheEntry,
        warnings: list[WarningInfo],
        category: DataCategory,
        operation_name: str,
    ) -> None:
        try:
            self._cache_store.set(key, entry)
        except Exception:
            self._add_warning_once(
                warnings,
                code="CACHE_UNAVAILABLE",
                message=_MSG_CACHE_UNAVAILABLE,
                category=category,
                operation_name=operation_name,
            )

    # --- vendor attempt ------------------------------------------------------

    async def _attempt_vendor[T](
        self,
        *,
        vendor_id: VendorId,
        chain_index: int,
        market: Market,
        category: DataCategory,
        call: Callable[[CategoryProvider], Awaitable[ProviderSuccess[T]]],
        as_of: datetime,
        operation_name: str,
        cache_enabled: bool,
        cache_codec: ProviderCacheCodec[T] | None,
        result_validator: Callable[[ProviderSuccess[T]], None] | None,
        instrument_id: str | None,
        call_fingerprint: str,
        warnings: list[WarningInfo],
        collected_errors: list[TradingPartnerError],
    ) -> tuple[_AttemptDraft, ProviderSuccess[T] | None, bool]:
        """Return (attempt draft without duration, success or None, stop_chain)."""
        adapter = self._registry.get_optional(vendor_id)
        if adapter is None:
            self._add_warning_once(
                warnings,
                code="PARTIAL_VENDOR_CHAIN",
                message=_MSG_PARTIAL_VENDOR_CHAIN,
                category=category,
                operation_name=operation_name,
                vendor=vendor_id.value,
            )
            return (
                _AttemptDraft(
                    outcome=ProviderAttemptOutcome.SKIPPED_UNSUPPORTED,
                    error_code=None,
                    message=None,
                ),
                None,
                False,
            )

        try:
            configured = adapter.is_configured()
        except TradingPartnerError as exc:
            # Preserve typed surface errors (e.g. DataContractError from exact-bool
            # / exception-safe adapter checks); only unknown exceptions wrap below.
            return self._typed_surface_failure(exc, collected_errors)
        except Exception as exc:
            wrapped = self._wrap_unknown(exc, vendor_id=vendor_id, category=category)
            collected_errors.append(wrapped)
            return (
                _AttemptDraft(
                    outcome=ProviderAttemptOutcome.FAILURE,
                    error_code=wrapped.code,
                    message=None,
                ),
                None,
                False,
            )
        if not isinstance(configured, bool):
            # Exact bool only — never truthiness; never echo the raw return value.
            wrapped = self._adapter_bool_contract_error("adapter.is_configured")
            collected_errors.append(wrapped)
            return (
                _AttemptDraft(
                    outcome=ProviderAttemptOutcome.CONTRACT_ERROR,
                    error_code=wrapped.code,
                    message=None,
                ),
                None,
                False,
            )
        if not configured:
            return (
                _AttemptDraft(
                    outcome=ProviderAttemptOutcome.SKIPPED_NOT_CONFIGURED,
                    error_code=None,
                    message=None,
                ),
                None,
                False,
            )

        try:
            supported = adapter.supports(market, category)
        except TradingPartnerError as exc:
            return self._typed_surface_failure(exc, collected_errors)
        except Exception as exc:
            wrapped = self._wrap_unknown(exc, vendor_id=vendor_id, category=category)
            collected_errors.append(wrapped)
            return (
                _AttemptDraft(
                    outcome=ProviderAttemptOutcome.FAILURE,
                    error_code=wrapped.code,
                    message=None,
                ),
                None,
                False,
            )
        if not isinstance(supported, bool):
            wrapped = self._adapter_bool_contract_error("adapter.supports")
            collected_errors.append(wrapped)
            return (
                _AttemptDraft(
                    outcome=ProviderAttemptOutcome.CONTRACT_ERROR,
                    error_code=wrapped.code,
                    message=None,
                ),
                None,
                False,
            )
        if not supported:
            return (
                _AttemptDraft(
                    outcome=ProviderAttemptOutcome.SKIPPED_UNSUPPORTED,
                    error_code=None,
                    message=None,
                ),
                None,
                False,
            )

        if self._settings.enable_circuit_breaker:
            try:
                state = self._circuit_breaker.state(vendor_id, category)
            except Exception as exc:
                wrapped = self._wrap_unknown(
                    exc, vendor_id=vendor_id, category=category
                )
                collected_errors.append(wrapped)
                return (
                    _AttemptDraft(
                        outcome=ProviderAttemptOutcome.FAILURE,
                        error_code=wrapped.code,
                        message=None,
                    ),
                    None,
                    False,
                )
            if state is CircuitState.OPEN:
                self._add_warning(
                    warnings,
                    code="CIRCUIT_OPEN_SKIPPED",
                    message=_MSG_CIRCUIT_OPEN_SKIPPED,
                    category=category,
                    operation_name=operation_name,
                    vendor=vendor_id.value,
                )
                return (
                    _AttemptDraft(
                        outcome=ProviderAttemptOutcome.SKIPPED_CIRCUIT_OPEN,
                        error_code=None,
                        message=None,
                    ),
                    None,
                    False,
                )

        policy = RetryPolicy(
            max_attempts=self._settings.provider_retry_max_attempts,
            base_delay_seconds=self._settings.provider_retry_base_delay_seconds,
            max_delay_seconds=self._settings.provider_retry_max_delay_seconds,
            retryable_error_types=DEFAULT_RETRYABLE_ERROR_TYPES,
        )

        try:
            success = await run_with_retry(
                lambda: self._single_real_call(
                    adapter=adapter,
                    vendor_id=vendor_id,
                    market=market,
                    category=category,
                    call=call,
                    as_of=as_of,
                    result_validator=result_validator,
                    warnings=warnings,
                    operation_name=operation_name,
                ),
                policy,
            )
        except _RateLimitDenied:
            self._add_warning(
                warnings,
                code="RATE_LIMIT_DEGRADED",
                message=_MSG_RATE_LIMIT_DEGRADED,
                category=category,
                operation_name=operation_name,
                vendor=vendor_id.value,
            )
            collected_errors.append(
                ProviderRateLimitError(
                    "Provider rate limit exceeded",
                    details={
                        "vendor": vendor_id.value,
                        "category": category.value,
                    },
                )
            )
            return (
                _AttemptDraft(
                    outcome=ProviderAttemptOutcome.SKIPPED_RATE_LIMITED,
                    error_code="PROVIDER_RATE_LIMIT_ERROR",
                    message=None,
                ),
                None,
                False,
            )
        except TradingPartnerError as exc:
            outcome = self._outcome_for_error(exc)
            if isinstance(exc, ProviderRateLimitError):
                self._add_warning(
                    warnings,
                    code="RATE_LIMIT_DEGRADED",
                    message=_MSG_RATE_LIMIT_DEGRADED,
                    category=category,
                    operation_name=operation_name,
                    vendor=vendor_id.value,
                )
            if isinstance(exc, StaleMarketData):
                self._add_warning(
                    warnings,
                    code="STALE_DATA_REJECTED",
                    message=_MSG_STALE_DATA_REJECTED,
                    category=category,
                    operation_name=operation_name,
                    vendor=vendor_id.value,
                )
            collected_errors.append(exc)
            stop = (
                isinstance(exc, ProviderAuthenticationError)
                and not self._settings.auth_failure_fallback
            )
            return (
                _AttemptDraft(
                    outcome=outcome,
                    error_code=exc.code,
                    message=None,
                ),
                None,
                stop,
            )
        except Exception as exc:
            wrapped = self._wrap_unknown(exc, vendor_id=vendor_id, category=category)
            collected_errors.append(wrapped)
            return (
                _AttemptDraft(
                    outcome=ProviderAttemptOutcome.FAILURE,
                    error_code=wrapped.code,
                    message=None,
                ),
                None,
                False,
            )

        rewritten = self._rewrite_success_meta(
            success,
            vendor_id=vendor_id,
            category=category,
            as_of=as_of,
            chain_index=chain_index,
            cache_enabled=cache_enabled,
        )

        if chain_index > 0:
            self._add_warning(
                warnings,
                code="FALLBACK_VENDOR_USED",
                message=_MSG_FALLBACK_VENDOR_USED,
                category=category,
                operation_name=operation_name,
                vendor=vendor_id.value,
            )

        if cache_enabled and cache_codec is not None:
            self._write_success_cache(
                success=rewritten,
                cache_codec=cache_codec,
                market=market,
                category=category,
                instrument_id=instrument_id,
                as_of=as_of,
                call_fingerprint=call_fingerprint,
                warnings=warnings,
                operation_name=operation_name,
            )

        return (
            _AttemptDraft(
                outcome=ProviderAttemptOutcome.SUCCESS,
                error_code=None,
                message=None,
            ),
            rewritten,
            False,
        )

    async def _single_real_call[T](
        self,
        *,
        adapter: CategoryProvider,
        vendor_id: VendorId,
        market: Market,
        category: DataCategory,
        call: Callable[[CategoryProvider], Awaitable[ProviderSuccess[T]]],
        as_of: datetime,
        result_validator: Callable[[ProviderSuccess[T]], None] | None,
        warnings: list[WarningInfo],
        operation_name: str,
    ) -> ProviderSuccess[T]:
        """One real provider attempt: rate → permit → timeout → validate → health."""
        del market  # reserved for future per-market rate policies
        decision = self._rate_limiter.check_and_consume(vendor_id, category)
        if not decision.allowed:
            raise _RateLimitDenied()

        permit = None
        if self._settings.enable_circuit_breaker:
            permit = self._circuit_breaker.before_call(vendor_id, category)

        try:
            timeout_s = float(self._settings.timeout_for(category))
            raw = await run_with_timeout(call(adapter), timeout_s)
            self._validate_provider_success(
                raw, vendor_id=vendor_id, category=category, as_of=as_of
            )
            if result_validator is not None:
                result_validator(raw)
            self._apply_stale_guard(raw, category=category, as_of=as_of)
        except asyncio.CancelledError as exc:
            # CancelledError is BaseException — must not leak an issued permit.
            if permit is not None:
                self._circuit_breaker.record_failure(permit)
                self._project_circuit_state(
                    vendor_id=vendor_id,
                    category=category,
                    warnings=warnings,
                    operation_name=operation_name,
                )
                self._record_health_failure(
                    vendor_id=vendor_id,
                    category=category,
                    error=exc,
                    warnings=warnings,
                    operation_name=operation_name,
                )
            raise
        except Exception as exc:
            if permit is not None:
                self._circuit_breaker.record_failure(permit)
                self._project_circuit_state(
                    vendor_id=vendor_id,
                    category=category,
                    warnings=warnings,
                    operation_name=operation_name,
                )
            # Health failure for every real call that failed after admission
            # (rate limit denial never reaches here).
            if not isinstance(exc, _RateLimitDenied):
                self._record_health_failure(
                    vendor_id=vendor_id,
                    category=category,
                    error=exc,
                    warnings=warnings,
                    operation_name=operation_name,
                )
            if isinstance(exc, TradingPartnerError):
                raise
            if isinstance(exc, Exception):
                raise self._wrap_unknown(
                    exc, vendor_id=vendor_id, category=category
                ) from None
            raise

        if permit is not None:
            self._circuit_breaker.record_success(permit)
            self._project_circuit_state(
                vendor_id=vendor_id,
                category=category,
                warnings=warnings,
                operation_name=operation_name,
            )
        self._record_health_success(
            vendor_id=vendor_id,
            category=category,
            warnings=warnings,
            operation_name=operation_name,
        )
        return raw

    def _validate_provider_success[T](
        self,
        success: ProviderSuccess[T],
        *,
        vendor_id: VendorId,
        category: DataCategory,
        as_of: datetime,
    ) -> None:
        if not isinstance(success, ProviderSuccess):
            raise DataContractError(
                "provider call must return ProviderSuccess",
                details={"field": "result", "rule": "type"},
            )
        if success.meta.vendor is not vendor_id:
            raise DataContractError(
                "meta.vendor must equal chain vendor",
                details={"field": "meta.vendor", "rule": "coherence"},
            )
        if success.meta.category is not category:
            raise DataContractError(
                "meta.category must equal request category",
                details={"field": "meta.category", "rule": "coherence"},
            )
        if success.meta.as_of > as_of:
            raise DataContractError(
                "meta.as_of must be <= requested as_of",
                details={"field": "meta.as_of", "rule": "as_of_window"},
            )

    def _rewrite_success_meta[T](
        self,
        success: ProviderSuccess[T],
        *,
        vendor_id: VendorId,
        category: DataCategory,
        as_of: datetime,
        chain_index: int,
        cache_enabled: bool,
    ) -> ProviderSuccess[T]:
        del as_of  # validated already
        role = SourceRole.PRIMARY if chain_index == 0 else SourceRole.FALLBACK
        disposition = (
            CacheDisposition.MISS if cache_enabled else CacheDisposition.BYPASS
        )
        meta = replace(
            success.meta,
            vendor=vendor_id,
            category=category,
            role=role,
            cache_disposition=disposition,
        )
        return ProviderSuccess(value=success.value, meta=meta)

    def _apply_stale_guard[T](
        self,
        success: ProviderSuccess[T],
        *,
        category: DataCategory,
        as_of: datetime,
    ) -> None:
        # D6b2 freeze: only MARKET_SNAPSHOT + VerifiedMarketSnapshot bar time.
        if category is not DataCategory.MARKET_SNAPSHOT:
            return
        if not isinstance(success.value, VerifiedMarketSnapshot):
            return
        now = require_aware_datetime(self._clock.now(), field_name="clock.now")
        config = StaleGuardConfig(
            max_age_seconds=self._settings.stale_guard_max_age_seconds,
            respect_session=self._settings.stale_guard_respect_session,
            allow_closed_session_last_bar=(
                self._settings.stale_guard_allow_closed_last_bar
            ),
        )
        assert_ohlcv_not_stale(
            latest_bar_time=success.value.latest_market_row.timestamp,
            now=now,
            as_of=as_of,
            session=success.meta.session,
            config=config,
        )

    def _write_success_cache[T](
        self,
        *,
        success: ProviderSuccess[T],
        cache_codec: ProviderCacheCodec[T],
        market: Market,
        category: DataCategory,
        instrument_id: str | None,
        as_of: datetime,
        call_fingerprint: str,
        warnings: list[WarningInfo],
        operation_name: str,
    ) -> None:
        try:
            # Encode requires MISS disposition (rewritten meta already MISS).
            payload = cache_codec.encode(success)
            key = build_cache_key(
                market, category, instrument_id, as_of, call_fingerprint
            )
            ttl = int(self._settings.cache_ttl_for(category))
            expires_at = success.meta.fetched_at + timedelta(seconds=ttl)
            entry = CacheEntry(
                key=key,
                category=category,
                market=market,
                instrument_id=instrument_id,
                vendor=success.meta.vendor,
                payload_json=payload,
                as_of=success.meta.as_of,
                fetched_at=success.meta.fetched_at,
                expires_at=expires_at,
                freshness=success.meta.freshness,
            )
        except Exception:
            self._add_warning_once(
                warnings,
                code="CACHE_UNAVAILABLE",
                message=_MSG_CACHE_UNAVAILABLE,
                category=category,
                operation_name=operation_name,
            )
            return
        self._best_effort_cache_set(
            key=entry.key,
            entry=entry,
            warnings=warnings,
            category=category,
            operation_name=operation_name,
        )

    # --- health / circuit projection (non-blocking) --------------------------

    def _project_circuit_state(
        self,
        *,
        vendor_id: VendorId,
        category: DataCategory,
        warnings: list[WarningInfo],
        operation_name: str,
    ) -> None:
        """Best-effort project process-local breaker state; never alters routing."""
        try:
            at = require_aware_datetime(self._clock.now(), field_name="clock.now")
            state = self._circuit_breaker.state(vendor_id, category)
            self._health_store.set_circuit_state(vendor_id, category, state, at)
        except Exception:
            self._add_warning_once(
                warnings,
                code="PROVIDER_HEALTH_UNAVAILABLE",
                message=_MSG_PROVIDER_HEALTH_UNAVAILABLE,
                category=category,
                operation_name=operation_name,
                vendor=vendor_id.value,
            )

    def _record_health_success(
        self,
        *,
        vendor_id: VendorId,
        category: DataCategory,
        warnings: list[WarningInfo],
        operation_name: str,
    ) -> None:
        try:
            at = require_aware_datetime(self._clock.now(), field_name="clock.now")
            self._health_store.record_success(vendor_id, category, at)
        except Exception:
            self._add_warning_once(
                warnings,
                code="PROVIDER_HEALTH_UNAVAILABLE",
                message=_MSG_PROVIDER_HEALTH_UNAVAILABLE,
                category=category,
                operation_name=operation_name,
                vendor=vendor_id.value,
            )

    def _record_health_failure(
        self,
        *,
        vendor_id: VendorId,
        category: DataCategory,
        error: BaseException,
        warnings: list[WarningInfo],
        operation_name: str,
    ) -> None:
        if isinstance(error, TradingPartnerError):
            error_code = error.code
        else:
            error_code = "PROVIDER_UNAVAILABLE_ERROR"
        # Grammar: only safe codes reach the store.
        if not re.fullmatch(r"^[A-Z][A-Z0-9_]{0,127}$", error_code):
            error_code = "PROVIDER_UNAVAILABLE_ERROR"
        try:
            at = require_aware_datetime(self._clock.now(), field_name="clock.now")
            self._health_store.record_failure(
                vendor_id, category, at, error_code
            )
        except Exception:
            self._add_warning_once(
                warnings,
                code="PROVIDER_HEALTH_UNAVAILABLE",
                message=_MSG_PROVIDER_HEALTH_UNAVAILABLE,
                category=category,
                operation_name=operation_name,
                vendor=vendor_id.value,
            )

    # --- errors / warnings / results -----------------------------------------

    def _typed_surface_failure(
        self,
        exc: TradingPartnerError,
        collected_errors: list[TradingPartnerError],
    ) -> tuple[_AttemptDraft, None, bool]:
        """Map a typed adapter-surface error; preserve identity/code and auth stop."""
        collected_errors.append(exc)
        stop = (
            isinstance(exc, ProviderAuthenticationError)
            and not self._settings.auth_failure_fallback
        )
        return (
            _AttemptDraft(
                outcome=self._outcome_for_error(exc),
                error_code=exc.code,
                message=None,
            ),
            None,
            stop,
        )

    @staticmethod
    def _adapter_bool_contract_error(field: str) -> DataContractError:
        """Surface non-bool adapter.is_configured/supports without raw value leak."""
        return DataContractError(
            "adapter surface must return exact bool",
            details={"field": field, "rule": "exact_bool"},
        )

    @staticmethod
    def _wrap_unknown(
        exc: BaseException,
        *,
        vendor_id: VendorId,
        category: DataCategory,
    ) -> ProviderUnavailableError:
        # from None: no raw adapter chain / secrets.
        return ProviderUnavailableError(
            "Provider call failed",
            details={
                "vendor": vendor_id.value,
                "category": category.value,
                "error_type": type(exc).__name__,
            },
        )

    @staticmethod
    def _outcome_for_error(exc: TradingPartnerError) -> ProviderAttemptOutcome:
        if isinstance(exc, ProviderTimeoutError):
            return ProviderAttemptOutcome.TIMEOUT
        if isinstance(exc, ProviderAuthenticationError):
            return ProviderAttemptOutcome.AUTH_ERROR
        if isinstance(exc, NoMarketData):
            return ProviderAttemptOutcome.NO_DATA
        if isinstance(exc, (DataContractError, StaleMarketData)):
            return ProviderAttemptOutcome.CONTRACT_ERROR
        if isinstance(exc, ProviderRateLimitError):
            return ProviderAttemptOutcome.SKIPPED_RATE_LIMITED
        return ProviderAttemptOutcome.FAILURE

    def _aggregate_errors(
        self,
        collected: list[TradingPartnerError],
        *,
        market: Market,
        category: DataCategory,
        chain: tuple[VendorId, ...],
        instrument_id: str | None,
    ) -> TradingPartnerError:
        if not collected:
            details: dict[str, object] = {
                "category": category.value,
                "market": market.value,
                "vendor_chain": [v.value for v in chain],
            }
            if instrument_id is not None:
                details["instrument_id"] = instrument_id
            return ProviderNotConfigured(
                "No provider could serve the request",
                details=details,
            )

        best: TradingPartnerError | None = None
        best_rank = len(_ERROR_PRIORITY)
        for err in collected:
            rank = len(_ERROR_PRIORITY)
            for idx, cls in enumerate(_ERROR_PRIORITY):
                if isinstance(err, cls):
                    rank = idx
                    break
            # Prefer higher priority (lower rank); ties → later (more recent).
            if best is None or rank < best_rank or rank == best_rank:
                best = err
                best_rank = rank
        assert best is not None
        return best

    def _failure_result[T](
        self,
        *,
        criticality: DataCriticality,
        attempts: tuple[ProviderAttemptRecord, ...],
        warnings: tuple[WarningInfo, ...],
        error: TradingPartnerError,
        operation_name: str,
        category: DataCategory,
    ) -> RouterExecutionResult[T]:
        warning_list = list(warnings)
        if criticality is DataCriticality.OPTIONAL:
            self._add_warning_once(
                warning_list,
                code="OPTIONAL_DATA_UNAVAILABLE",
                message=_MSG_OPTIONAL_DATA_UNAVAILABLE,
                category=category,
                operation_name=operation_name,
            )
        return RouterExecutionResult(
            value=None,
            ok=False,
            criticality=criticality,
            meta=None,
            attempts=attempts,
            warnings=tuple(warning_list),
            error=error,
        )

    @staticmethod
    def _warning_details(
        *,
        category: DataCategory,
        operation_name: str,
        vendor: str | None = None,
    ) -> dict[str, object]:
        details: dict[str, object] = {
            "category": category.value,
            "operation_name": operation_name,
        }
        if vendor is not None:
            details["vendor"] = vendor
        return details

    def _add_warning(
        self,
        warnings: list[WarningInfo],
        *,
        code: str,
        message: str,
        category: DataCategory,
        operation_name: str,
        vendor: str | None = None,
    ) -> None:
        warnings.append(
            WarningInfo(
                code=code,
                message=message,
                details=self._warning_details(
                    category=category,
                    operation_name=operation_name,
                    vendor=vendor,
                ),
            )
        )

    def _add_warning_once(
        self,
        warnings: list[WarningInfo],
        *,
        code: str,
        message: str,
        category: DataCategory,
        operation_name: str,
        vendor: str | None = None,
    ) -> None:
        if any(w.code == code for w in warnings):
            return
        self._add_warning(
            warnings,
            code=code,
            message=message,
            category=category,
            operation_name=operation_name,
            vendor=vendor,
        )


class _AttemptDraft:
    """Attempt fields excluding duration_ms (filled by caller with monotonic)."""

    __slots__ = ("outcome", "error_code", "message")

    def __init__(
        self,
        *,
        outcome: ProviderAttemptOutcome,
        error_code: str | None,
        message: str | None,
    ) -> None:
        self.outcome = outcome
        self.error_code = error_code
        self.message = message
