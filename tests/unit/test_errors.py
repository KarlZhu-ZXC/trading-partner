"""Typed error contract tests."""

from __future__ import annotations

from application.dto.error_mapper import to_error_info
from domain.common.errors import (
    AppendOnlyViolation,
    CandidateAlreadyResolved,
    CandidateNotFound,
    ConfigurationError,
    DataContractError,
    DuplicateIdempotencyKey,
    InputValidationError,
    InvalidationConditionNarrowingForbidden,
    InvalidInstrument,
    InvestmentCaseNotFound,
    MigrationError,
    NoMarketData,
    OpenQuestionNotFound,
    PartialDataError,
    PersistenceError,
    ProviderAuthenticationError,
    ProviderNotConfigured,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    StaleMarketData,
    StrictReviewRequired,
    ThesisNotFound,
    ThesisRevisionNotFound,
    UnauthorizedReviewer,
    WatchlistItemNotFound,
)
from infrastructure.system.redactor import DefaultSecretRedactor


def test_error_codes_and_retryable() -> None:
    cases: list[tuple[type, str, bool]] = [
        (ConfigurationError, "CONFIGURATION_ERROR", False),
        (ProviderNotConfigured, "PROVIDER_NOT_CONFIGURED", False),
        (ProviderAuthenticationError, "PROVIDER_AUTHENTICATION_ERROR", False),
        (ProviderRateLimitError, "PROVIDER_RATE_LIMIT_ERROR", True),
        (ProviderTimeoutError, "PROVIDER_TIMEOUT_ERROR", True),
        (ProviderUnavailableError, "PROVIDER_UNAVAILABLE_ERROR", True),
        (NoMarketData, "NO_MARKET_DATA", False),
        (StaleMarketData, "STALE_MARKET_DATA", True),
        (InvalidInstrument, "INVALID_INSTRUMENT", False),
        (DataContractError, "DATA_CONTRACT_ERROR", False),
        (PartialDataError, "PARTIAL_DATA_ERROR", True),
        (PersistenceError, "PERSISTENCE_ERROR", True),
        (MigrationError, "MIGRATION_ERROR", False),
        (InvestmentCaseNotFound, "INVESTMENT_CASE_NOT_FOUND", False),
        (ThesisNotFound, "THESIS_NOT_FOUND", False),
        (ThesisRevisionNotFound, "THESIS_REVISION_NOT_FOUND", False),
        (WatchlistItemNotFound, "WATCHLIST_ITEM_NOT_FOUND", False),
        (OpenQuestionNotFound, "OPEN_QUESTION_NOT_FOUND", False),
        (CandidateNotFound, "CANDIDATE_NOT_FOUND", False),
        (CandidateAlreadyResolved, "CANDIDATE_ALREADY_RESOLVED", False),
        (UnauthorizedReviewer, "UNAUTHORIZED_REVIEWER", False),
        (
            InvalidationConditionNarrowingForbidden,
            "INVALIDATION_CONDITION_NARROWING_FORBIDDEN",
            False,
        ),
        (StrictReviewRequired, "STRICT_REVIEW_REQUIRED", False),
        (DuplicateIdempotencyKey, "DUPLICATE_IDEMPOTENCY_KEY", False),
        (InputValidationError, "INPUT_VALIDATION_ERROR", False),
        (AppendOnlyViolation, "APPEND_ONLY_VIOLATION", False),
    ]
    for cls, code, retryable in cases:
        exc = cls("msg")
        assert exc.code == code
        assert exc.retryable is retryable


def test_error_mapper_redacts_secrets() -> None:
    redactor = DefaultSecretRedactor()
    exc = ConfigurationError(
        "bad api_key=test-secret-value",
        details={"api_key": "test-secret-value", "safe": "ok"},
    )
    info = to_error_info(exc, redactor)
    assert info.code == "CONFIGURATION_ERROR"
    assert "test-secret-value" not in info.message
    assert info.details["api_key"] == "***REDACTED***"
    assert info.details["safe"] == "ok"
