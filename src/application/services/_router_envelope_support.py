"""Shared provider-envelope helpers for tool coordinators.

Coordinators aggregate Router execution results into ToolEnvelope values with
the same rules everywhere: worst-freshness across provider metas, deduplicated
warning codes with a domain-specific message, and redacted error mapping for
router failures and exceptions. Only that shared shape lives here; anything
coordinator-specific stays local.
"""

from __future__ import annotations

from datetime import datetime

from application.dto.error_mapper import to_error_info, to_error_info_from_exception
from application.dto.provider_routing import ProviderResultMeta, RouterExecutionResult
from application.dto.tool_envelope import SourceReference, ToolEnvelope, WarningInfo
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from domain.common.enums import Freshness, Market, SourceRole
from domain.common.errors import DataContractError, TradingPartnerError
from domain.common.ids import EntityIdPrefix

FRESHNESS_ORDER = {
    Freshness.FRESH: 0,
    Freshness.DELAYED: 1,
    Freshness.STALE: 2,
    Freshness.UNKNOWN: 3,
}


def begin_request(
    requested: datetime | None,
    *,
    clock: Clock,
    ids: IdGenerator,
) -> tuple[str, datetime]:
    now = clock.now()
    as_of = requested or now
    if as_of > now:
        raise DataContractError("as_of must not be in the future")
    return ids.new(EntityIdPrefix.REQ), as_of


def warnings_from_results(
    results: tuple[RouterExecutionResult[object], ...],
    metas: tuple[ProviderResultMeta, ...],
    extra_codes: tuple[str, ...],
    *,
    message: str,
) -> tuple[WarningInfo, ...]:
    codes = [warning.code for result in results for warning in result.warnings]
    codes.extend(extra_codes)
    codes.extend("FALLBACK_US_SOURCE" for meta in metas if meta.role is SourceRole.FALLBACK)
    return tuple(
        WarningInfo(code=code, message=message, details={})
        for code in dict.fromkeys(codes)
    )


def success_envelope[T](
    *,
    request_id: str,
    as_of: datetime,
    data: T,
    results: tuple[RouterExecutionResult[object], ...],
    clock: Clock,
    market: Market | None,
    extra_codes: tuple[str, ...] = (),
    warning_message: str,
) -> ToolEnvelope[T]:
    metas = tuple(result.meta for result in results if result.meta is not None)
    warnings = warnings_from_results(results, metas, extra_codes, message=warning_message)
    fetched_at = max((meta.fetched_at for meta in metas), default=clock.now())
    freshness = max(
        (meta.freshness for meta in metas),
        key=lambda value: FRESHNESS_ORDER[value],
        default=Freshness.UNKNOWN,
    )
    sources = tuple(
        SourceReference(
            name=meta.vendor.value,
            role=meta.role,
            url=None,
            retrieved_at=meta.fetched_at,
            data_delay_seconds=meta.data_delay_seconds,
        )
        for meta in dict.fromkeys(metas)
    )
    return ToolEnvelope.success(
        request_id=request_id,
        market=market,
        as_of=as_of,
        fetched_at=fetched_at,
        freshness=freshness,
        sources=sources,
        data=data,
        degraded=bool(warnings),
        warnings=warnings,
    )


def router_failure_envelope[T](
    *,
    request_id: str,
    as_of: datetime,
    result: RouterExecutionResult[object],
    clock: Clock,
    redactor: SecretRedactor,
    market: Market | None,
) -> ToolEnvelope[T]:
    error = result.error
    mapped = (
        to_error_info(error, redactor)
        if isinstance(error, TradingPartnerError)
        else to_error_info_from_exception(error or RuntimeError("router failure"), redactor)
    )
    return ToolEnvelope.failure(
        request_id=request_id,
        market=market,
        as_of=as_of,
        fetched_at=clock.now(),
        freshness=Freshness.UNKNOWN,
        sources=(),
        errors=[mapped],
        degraded=True,
        warnings=result.warnings,
        data=None,
    )


def exception_envelope[T](
    *,
    request_id: str,
    as_of: datetime,
    exc: BaseException,
    clock: Clock,
    redactor: SecretRedactor,
    market: Market | None = None,
) -> ToolEnvelope[T]:
    mapped = (
        to_error_info(exc, redactor)
        if isinstance(exc, TradingPartnerError)
        else to_error_info_from_exception(exc, redactor)
    )
    return ToolEnvelope.failure(
        request_id=request_id,
        market=market,
        as_of=as_of,
        fetched_at=clock.now(),
        freshness=Freshness.UNKNOWN,
        sources=(),
        errors=(mapped,),
        degraded=True,
        warnings=(),
        data=None,
    )
