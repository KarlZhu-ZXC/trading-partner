"""Schwab OAuth token health and single-session browser authorization."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from io import StringIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from application.dto.schwab_oauth import (
    SchwabOAuthHealthDTO,
    SchwabOAuthHealthState,
)
from infrastructure.system.process_file_lock import ProcessFileLock

_REAUTHORIZATION_LIFETIME = timedelta(days=7)
_EARLY_WARNING_AGE = timedelta(days=5)


class SchwabOAuthFlowState(StrEnum):
    IDLE = "IDLE"
    ACTIVE = "ACTIVE"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


@dataclass(frozen=True, slots=True)
class SchwabOAuthFlowStatus:
    state: SchwabOAuthFlowState
    flow_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    message_code: str | None = None
    retry_requires_confirmation: bool = False


class SchwabOAuthTokenInspector:
    """Read only schwab-py's stable creation timestamp from its token wrapper."""

    def __init__(self, *, token_path: Path, enabled: bool) -> None:
        self._token_path = token_path
        self._enabled = enabled

    def inspect(self, *, now: datetime) -> SchwabOAuthHealthDTO:
        checked_at = _as_utc(now)
        if not self._enabled:
            return SchwabOAuthHealthDTO(
                state=SchwabOAuthHealthState.DISABLED,
                checked_at=checked_at,
            )
        if not self._token_path.is_file():
            return SchwabOAuthHealthDTO(
                state=SchwabOAuthHealthState.REAUTH_REQUIRED,
                checked_at=checked_at,
                warning_codes=("SCHWAB_OAUTH_REAUTH_REQUIRED",),
                action_required=True,
            )
        try:
            with self._token_path.open(encoding="utf-8") as handle:
                wrapped = json.load(handle)
            if not isinstance(wrapped, dict):
                raise ValueError("token wrapper is not an object")
            raw_created_at = wrapped.get("creation_timestamp")
            if isinstance(raw_created_at, bool) or not isinstance(
                raw_created_at, (int, float)
            ):
                raise ValueError("creation timestamp is missing")
            created_at = datetime.fromtimestamp(raw_created_at, tz=UTC)
            age = checked_at - created_at
            if age < timedelta(0):
                raise ValueError("creation timestamp is in the future")
        except (OSError, ValueError, TypeError, json.JSONDecodeError, OverflowError):
            return SchwabOAuthHealthDTO(
                state=SchwabOAuthHealthState.UNAVAILABLE,
                checked_at=checked_at,
                warning_codes=("SCHWAB_OAUTH_STATUS_UNAVAILABLE",),
                action_required=True,
            )

        due_at = created_at + _REAUTHORIZATION_LIFETIME
        remaining = max(0, int((due_at - checked_at).total_seconds()))
        age_seconds = int(age.total_seconds())
        warnings: tuple[str, ...]
        if age >= _REAUTHORIZATION_LIFETIME:
            state = SchwabOAuthHealthState.REAUTH_REQUIRED
            warnings = ("SCHWAB_OAUTH_REAUTH_REQUIRED",)
            action_required = True
        elif age >= _EARLY_WARNING_AGE:
            state = SchwabOAuthHealthState.EXPIRING
            warnings = ("SCHWAB_OAUTH_REAUTH_DUE_SOON",)
            action_required = False
        else:
            state = SchwabOAuthHealthState.VALID
            warnings = ()
            action_required = False
        return SchwabOAuthHealthDTO(
            state=state,
            checked_at=checked_at,
            token_created_at=created_at,
            token_age_seconds=age_seconds,
            reauthorization_due_at=due_at,
            seconds_until_reauthorization=remaining,
            warning_codes=warnings,
            action_required=action_required,
        )


class SchwabOAuthFlowManager:
    """Own one foreground browser flow and durable, credential-free status."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        token_path: Path,
        login_flow: Callable[..., Any] | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._token_path = token_path
        self._lock_path = token_path.parent.parent / "locks/schwab_oauth.lock"
        self._status_path = token_path.parent.parent / "locks/schwab_oauth_status.json"
        self._login_flow = login_flow

    def status(self) -> SchwabOAuthFlowStatus:
        stored = self._read_status()
        probe = ProcessFileLock(self._lock_path)
        if not probe.acquire():
            if stored.state is SchwabOAuthFlowState.ACTIVE:
                return stored
            return SchwabOAuthFlowStatus(
                state=SchwabOAuthFlowState.ACTIVE,
                flow_id=stored.flow_id,
                started_at=stored.started_at,
                message_code="SCHWAB_OAUTH_ALREADY_RUNNING",
            )
        probe.release()
        if stored.state is SchwabOAuthFlowState.ACTIVE:
            return SchwabOAuthFlowStatus(
                state=SchwabOAuthFlowState.INTERRUPTED,
                flow_id=stored.flow_id,
                started_at=stored.started_at,
                finished_at=stored.finished_at,
                message_code="SCHWAB_OAUTH_FLOW_INTERRUPTED",
                retry_requires_confirmation=True,
            )
        return stored

    def token_health(self, *, now: datetime | None = None) -> SchwabOAuthHealthDTO:
        return SchwabOAuthTokenInspector(
            token_path=self._token_path,
            enabled=True,
        ).inspect(now=now or datetime.now(tz=UTC))

    def renew(self, *, confirm_retry_after_failure: bool = False) -> SchwabOAuthFlowStatus:
        previous = self.status()
        if previous.state is SchwabOAuthFlowState.ACTIVE:
            return previous
        if (
            previous.state
            in {SchwabOAuthFlowState.FAILED, SchwabOAuthFlowState.INTERRUPTED}
            and not confirm_retry_after_failure
        ):
            return SchwabOAuthFlowStatus(
                state=previous.state,
                flow_id=previous.flow_id,
                started_at=previous.started_at,
                finished_at=previous.finished_at,
                message_code="SCHWAB_OAUTH_RETRY_REQUIRES_CONFIRMATION",
                retry_requires_confirmation=True,
            )

        lock = ProcessFileLock(self._lock_path)
        if not lock.acquire():
            return self.status()
        flow_id = f"oauth_{uuid4()}"
        started_at = datetime.now(tz=UTC).isoformat()
        active = SchwabOAuthFlowStatus(
            state=SchwabOAuthFlowState.ACTIVE,
            flow_id=flow_id,
            started_at=started_at,
            message_code="SCHWAB_OAUTH_BROWSER_FLOW_ACTIVE",
        )
        self._write_status(active)
        try:
            self._token_path.parent.mkdir(parents=True, exist_ok=True)
            self._token_path.parent.chmod(0o700)
            login_flow = self._login_flow
            if login_flow is None:
                from schwab.auth import client_from_login_flow

                login_flow = client_from_login_flow
            # schwab-py prints the authorization URL (including OAuth state) to
            # stdout. The browser still opens, but raw OAuth parameters must not
            # enter Codex/automation logs.
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                login_flow(
                    self._client_id,
                    self._client_secret,
                    self._redirect_uri,
                    str(self._token_path),
                    callback_timeout=300,
                    interactive=False,
                )
            self._token_path.chmod(0o600)
        except (Exception, KeyboardInterrupt):
            failed = SchwabOAuthFlowStatus(
                state=SchwabOAuthFlowState.FAILED,
                flow_id=flow_id,
                started_at=started_at,
                finished_at=datetime.now(tz=UTC).isoformat(),
                message_code="SCHWAB_OAUTH_BROWSER_FLOW_FAILED",
                retry_requires_confirmation=True,
            )
            self._write_status(failed)
            return failed
        finally:
            lock.release()

        succeeded = SchwabOAuthFlowStatus(
            state=SchwabOAuthFlowState.SUCCEEDED,
            flow_id=flow_id,
            started_at=started_at,
            finished_at=datetime.now(tz=UTC).isoformat(),
            message_code="SCHWAB_OAUTH_REAUTHORIZED",
        )
        self._write_status(succeeded)
        return succeeded

    def _read_status(self) -> SchwabOAuthFlowStatus:
        if not self._status_path.is_file():
            return SchwabOAuthFlowStatus(state=SchwabOAuthFlowState.IDLE)
        try:
            raw = json.loads(self._status_path.read_text(encoding="utf-8"))
            return SchwabOAuthFlowStatus(
                state=SchwabOAuthFlowState(str(raw["state"])),
                flow_id=_optional_string(raw.get("flow_id")),
                started_at=_optional_string(raw.get("started_at")),
                finished_at=_optional_string(raw.get("finished_at")),
                message_code=_optional_string(raw.get("message_code")),
                retry_requires_confirmation=bool(
                    raw.get("retry_requires_confirmation", False)
                ),
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return SchwabOAuthFlowStatus(
                state=SchwabOAuthFlowState.FAILED,
                message_code="SCHWAB_OAUTH_STATUS_UNAVAILABLE",
                retry_requires_confirmation=True,
            )

    def _write_status(self, status: SchwabOAuthFlowStatus) -> None:
        self._status_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._status_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(asdict(status), ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(self._status_path)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(UTC)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
