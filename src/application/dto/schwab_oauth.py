"""Safe Schwab OAuth health facts exposed to operational workflows."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class SchwabOAuthHealthState(StrEnum):
    DISABLED = "DISABLED"
    VALID = "VALID"
    EXPIRING = "EXPIRING"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    UNAVAILABLE = "UNAVAILABLE"


class SchwabOAuthHealthDTO(BaseModel):
    """Credential-free token-age diagnostics.

    No token value, path, client ID, account identifier, or OAuth state may be
    included in this DTO.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: SchwabOAuthHealthState
    checked_at: datetime
    token_created_at: datetime | None = None
    token_age_seconds: int | None = None
    reauthorization_due_at: datetime | None = None
    seconds_until_reauthorization: int | None = None
    warning_codes: tuple[str, ...] = ()
    action_required: bool = False
