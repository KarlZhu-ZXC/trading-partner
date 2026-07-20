"""Secret redactor tests."""

from __future__ import annotations

from infrastructure.system.redactor import DefaultSecretRedactor


def test_redact_mapping_keys() -> None:
    r = DefaultSecretRedactor()
    out = r.redact_mapping(
        {
            "api_key": "secret",
            "broker_api_secret": "s2",
            "symbol": "NVDA",
            "nested": {"token": "abc", "n": 1},
        }
    )
    assert out["api_key"] == "***REDACTED***"
    assert out["broker_api_secret"] == "***REDACTED***"
    assert out["symbol"] == "NVDA"
    nested = out["nested"]
    assert isinstance(nested, dict)
    assert nested["token"] == "***REDACTED***"
    assert nested["n"] == 1


def test_redact_text_patterns() -> None:
    r = DefaultSecretRedactor()
    text = "Authorization: Bearer abcdefghijklmnop api_key=test-secret-value"
    redacted = r.redact_text(text)
    assert "***REDACTED***" in redacted
    assert "abcdefghijklmnop" not in redacted
    assert "test-secret-value" not in redacted


def test_redact_text_credentials_and_env_secrets() -> None:
    r = DefaultSecretRedactor()
    text = "database=postgresql://user:SuperSecretPass@localhost/db BROKER_API_SECRET=broker-secret"
    redacted = r.redact_text(text)
    assert "SuperSecretPass" not in redacted
    assert "broker-secret" not in redacted
    assert "***REDACTED***" in redacted
