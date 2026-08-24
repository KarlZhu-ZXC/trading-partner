"""Secret-safe human projection for durable Agent turn failures."""

from __future__ import annotations

import re
from typing import Any

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")


def _safe_text(value: str | None) -> str | None:
    return value if isinstance(value, str) and _SAFE_ID.fullmatch(value) else None


def agent_failure_notice(
    *,
    code: str,
    provider_id: str | None,
    model: str | None,
    http_status: int | None,
    retryable: bool | None,
    attempts: int | None,
) -> dict[str, Any]:
    """Return one closed notification payload with no upstream free text."""

    if http_status in {432, 433} or code == "PROVIDER_QUOTA_EXCEEDED":
        title = "Provider Quota Exhausted"
        explanation = (
            "The Provider account reached its plan or pay-as-you-go usage allowance."
        )
        next_action = "Wait for the allowance reset or increase the Provider account limit."
    elif http_status == 429 or code == "PROVIDER_RATE_LIMIT_ERROR":
        title = "Provider Rate Limited"
        explanation = (
            "The Provider rejected the model request because its quota or shared "
            "capacity limit was reached."
        )
        next_action = "Retry after the Provider reset window or choose another model."
    elif http_status == 401:
        title = "Provider Authentication Failed"
        explanation = "The Provider did not accept the configured API credential."
        next_action = "Check that the correct Provider key is configured, then restart Console."
    elif http_status == 403:
        title = "Model Access Forbidden"
        explanation = (
            "The credential reached the Provider, but access to this model was denied. "
            "Common causes are model entitlement, regional policy, or account policy."
        )
        next_action = "Choose another enabled model or verify this model in the Provider console."
    elif http_status in {400, 404, 405, 422} or code == "PROVIDER_REQUEST_REJECTED":
        title = "Provider Rejected Request"
        explanation = (
            "The Provider rejected the request shape or an unsupported model capability."
        )
        next_action = "Choose a compatible model or remove unsupported reasoning/tool options."
    elif code == "PROVIDER_AUTHENTICATION_ERROR":
        title = "Provider Access Rejected"
        explanation = "The Provider rejected the credential or model entitlement."
        next_action = "Check the Provider key and model entitlement before retrying."
    elif http_status == 503:
        title = "Provider Temporarily Unavailable"
        explanation = "The Provider gateway or its upstream model returned HTTP 503."
        next_action = "Retry later or choose another currently available model."
    elif code == "PROVIDER_TIMEOUT_ERROR":
        title = "Provider Request Timed Out"
        explanation = "The model request did not finish within the configured timeout."
        next_action = "Retry once or select a faster model."
    elif code == "PROVIDER_ADMISSION_TIMEOUT":
        title = "Local Provider Queue Timed Out"
        explanation = "No Provider admission slot became available within the local wait budget."
        next_action = "Retry after other model requests finish."
    elif code == "PROVIDER_UNAVAILABLE_ERROR":
        title = "Provider Unavailable"
        explanation = "The Provider or its upstream model could not complete the request."
        next_action = "Retry later or choose another Provider/model."
    elif code == "DATA_CONTRACT_ERROR":
        title = "Invalid Provider Response"
        explanation = "The Provider response did not match the required Agent response contract."
        next_action = "Retry once or choose another model."
    else:
        title = "Agent Turn Failed"
        explanation = "The Agent turn ended before a valid answer was completed."
        next_action = "Retry the turn; if it repeats, inspect Operations diagnostics."

    return {
        "schema_version": 1,
        "kind": (
            "provider_request_error"
            if code.startswith("PROVIDER_")
            else "agent_runtime_error"
        ),
        "title": title,
        "code": code,
        "provider_id": _safe_text(provider_id),
        "model": _safe_text(model),
        "http_status": http_status,
        "retryable": retryable,
        "attempts": attempts,
        "explanation": explanation,
        "next_action": next_action,
    }


__all__ = ["agent_failure_notice"]
