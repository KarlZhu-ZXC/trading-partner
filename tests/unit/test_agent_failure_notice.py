from __future__ import annotations

from application.services.agent_failure_notice import agent_failure_notice


def test_rate_limit_notice_exposes_safe_actionable_fields() -> None:
    value = agent_failure_notice(
        code="PROVIDER_RATE_LIMIT_ERROR",
        provider_id="opencode_zen",
        model="big-pickle",
        http_status=429,
        retryable=True,
        attempts=2,
    )

    assert value == {
        "schema_version": 1,
        "kind": "provider_request_error",
        "title": "Provider Rate Limited",
        "code": "PROVIDER_RATE_LIMIT_ERROR",
        "provider_id": "opencode_zen",
        "model": "big-pickle",
        "http_status": 429,
        "retryable": True,
        "attempts": 2,
        "explanation": (
            "The Provider rejected the model request because its quota or shared "
            "capacity limit was reached."
        ),
        "next_action": "Retry after the Provider reset window or choose another model.",
    }


def test_notice_drops_untrusted_provider_and_model_text() -> None:
    value = agent_failure_notice(
        code="PROVIDER_UNAVAILABLE_ERROR",
        provider_id="https://secret.example/token?key=x",
        model="api key secret value",
        http_status=503,
        retryable=True,
        attempts=None,
    )

    assert value["provider_id"] is None
    assert value["model"] is None
    assert "secret.example" not in repr(value)


def test_http_403_notice_distinguishes_model_forbidden_from_bad_key() -> None:
    value = agent_failure_notice(
        code="PROVIDER_AUTHENTICATION_ERROR",
        provider_id="opencode_go",
        model="gpt-5.6-luna",
        http_status=403,
        retryable=False,
        attempts=None,
    )

    assert value["title"] == "Model Access Forbidden"
    assert "regional policy" in value["explanation"]


def test_http_400_notice_is_request_rejected_and_not_unavailable() -> None:
    value = agent_failure_notice(
        code="PROVIDER_REQUEST_REJECTED",
        provider_id="opencode_zen",
        model="hy3-free",
        http_status=400,
        retryable=False,
        attempts=None,
    )

    assert value["title"] == "Provider Rejected Request"
    assert "unsupported model capability" in value["explanation"]


def test_tavily_quota_notice_is_explicit_and_not_retryable() -> None:
    value = agent_failure_notice(
        code="PROVIDER_QUOTA_EXCEEDED",
        provider_id="tavily",
        model=None,
        http_status=432,
        retryable=False,
        attempts=1,
    )

    assert value["title"] == "Provider Quota Exhausted"
    assert value["retryable"] is False
    assert "usage allowance" in value["explanation"]
