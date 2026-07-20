"""Map domain errors to Tool Envelope ErrorInfo with secret redaction."""

from __future__ import annotations

from application.dto.tool_envelope import ErrorInfo
from application.ports.secret_redactor import SecretRedactor
from domain.common.errors import TradingPartnerError


def _lookup(exc: TradingPartnerError) -> tuple[str, bool]:
    """Resolve ErrorInfo code and retryable flag for an exception instance."""
    # Prefer instance attributes set by TradingPartnerError subclasses.
    return exc.code, exc.retryable


def to_error_info(
    exc: TradingPartnerError,
    redactor: SecretRedactor,
) -> ErrorInfo:
    """Convert a TradingPartnerError into a redacted ErrorInfo."""
    code, retryable = _lookup(exc)
    message = redactor.redact_text(exc.message)
    details = redactor.redact_mapping(exc.details)
    return ErrorInfo(
        code=code,
        message=message,
        retryable=retryable,
        details=details,
    )


def to_error_info_from_exception(
    exc: BaseException,
    redactor: SecretRedactor,
    *,
    default_code: str = "UNEXPECTED_ERROR",
    default_retryable: bool = False,
) -> ErrorInfo:
    """Map any exception; TradingPartnerError uses typed codes."""
    if isinstance(exc, TradingPartnerError):
        return to_error_info(exc, redactor)
    message = redactor.redact_text(str(exc) or type(exc).__name__)
    return ErrorInfo(
        code=default_code,
        message=message,
        retryable=default_retryable,
        details={},
    )
