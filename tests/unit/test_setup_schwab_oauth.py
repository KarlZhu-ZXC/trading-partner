from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from application.dto.schwab_oauth import SchwabOAuthHealthState
from infrastructure.providers.account import schwab_oauth
from infrastructure.providers.account.schwab_oauth import (
    SchwabOAuthFlowManager,
    SchwabOAuthFlowState,
    SchwabOAuthTokenInspector,
)
from infrastructure.system.process_file_lock import ProcessFileLock
from interfaces.cli.schwab_oauth import _next_action


def test_macos_browser_requests_foreground_launch(monkeypatch) -> None:
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(schwab_oauth.subprocess, "run", run)
    assert schwab_oauth._ForegroundBrowser().open("https://example.invalid/authorize")
    args, options = calls[0]
    assert args == ["/usr/bin/open", "-u", "https://example.invalid/authorize"]
    assert options["stderr"] == subprocess.DEVNULL
    assert options["timeout"] == 15


@pytest.mark.parametrize("failure", ["exit", "timeout", "missing"])
def test_macos_browser_failure_is_immediate_and_redacted(monkeypatch, failure) -> None:
    url = "https://example.invalid/authorize?state=must-not-leak"

    def run(args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 15)
        if failure == "missing":
            raise OSError(url)
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(schwab_oauth.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="browser could not be opened") as error:
        schwab_oauth._ForegroundBrowser().open(url)
    assert "must-not-leak" not in str(error.value)


def test_macos_flow_selects_foreground_browser(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(schwab_oauth.sys, "platform", "darwin")

    def login_flow(*args, **kwargs):
        browser = schwab_oauth.webbrowser.get(kwargs["requested_browser"])
        assert isinstance(browser, schwab_oauth._ForegroundBrowser)
        _token(Path(args[3]), datetime.now(tz=UTC))

    assert _manager(tmp_path, login_flow=login_flow).renew().state == "SUCCEEDED"


def _token(path: Path, created_at: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "creation_timestamp": created_at.timestamp(),
                "token": {"redacted_test_fixture": True},
            }
        ),
        encoding="utf-8",
    )


def _manager(
    tmp_path: Path,
    *,
    login_flow,
) -> SchwabOAuthFlowManager:
    return SchwabOAuthFlowManager(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="https://127.0.0.1:8182",
        token_path=tmp_path / "data/secrets/schwab_tokens.json",
        login_flow=login_flow,
    )


def test_token_age_warns_at_day_five_and_requires_reauth_at_day_seven(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 27, 12, tzinfo=UTC)
    token_path = tmp_path / "data/secrets/schwab_tokens.json"
    inspector = SchwabOAuthTokenInspector(token_path=token_path, enabled=True)

    _token(token_path, now - timedelta(days=5))
    expiring = inspector.inspect(now=now)
    assert expiring.state is SchwabOAuthHealthState.EXPIRING
    assert expiring.token_age_seconds == 5 * 24 * 60 * 60
    assert expiring.seconds_until_reauthorization == 2 * 24 * 60 * 60
    assert expiring.warning_codes == ("SCHWAB_OAUTH_REAUTH_DUE_SOON",)

    _token(token_path, now - timedelta(days=7))
    expired = inspector.inspect(now=now)
    assert expired.state is SchwabOAuthHealthState.REAUTH_REQUIRED
    assert expired.seconds_until_reauthorization == 0
    assert expired.action_required is True


def test_concurrent_renew_reports_existing_flow_without_opening_another_tab(
    tmp_path: Path,
) -> None:
    login_calls = 0

    def login_flow(*_args: object, **_kwargs: object) -> object:
        nonlocal login_calls
        login_calls += 1
        return object()

    manager = _manager(tmp_path, login_flow=login_flow)
    outer_lock = ProcessFileLock(
        tmp_path / "data/locks/schwab_oauth.lock"
    )
    assert outer_lock.acquire() is True
    try:
        result = manager.renew()
    finally:
        outer_lock.release()

    assert result.state is SchwabOAuthFlowState.ACTIVE
    assert result.message_code == "SCHWAB_OAUTH_ALREADY_RUNNING"
    assert login_calls == 0


def test_failed_flow_cannot_auto_open_a_replacement_tab(tmp_path: Path) -> None:
    login_calls = 0

    def failing_login(*_args: object, **_kwargs: object) -> object:
        nonlocal login_calls
        login_calls += 1
        raise TimeoutError

    manager = _manager(tmp_path, login_flow=failing_login)
    first = manager.renew()
    second = manager.renew()

    assert first.state is SchwabOAuthFlowState.FAILED
    assert second.message_code == "SCHWAB_OAUTH_RETRY_REQUIRES_CONFIRMATION"
    assert second.retry_requires_confirmation is True
    assert login_calls == 1


def test_confirmed_retry_runs_one_replacement_flow(tmp_path: Path) -> None:
    calls = 0

    def flaky_login(
        _client_id: str,
        _client_secret: str,
        _redirect_uri: str,
        token_path: str,
        **_kwargs: object,
    ) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError
        path = Path(token_path)
        _token(path, datetime.now(tz=UTC))
        return object()

    manager = _manager(tmp_path, login_flow=flaky_login)
    assert manager.renew().state is SchwabOAuthFlowState.FAILED

    result = manager.renew(confirm_retry_after_failure=True)

    assert result.state is SchwabOAuthFlowState.SUCCEEDED
    assert calls == 2
    assert (
        tmp_path / "data/secrets/schwab_tokens.json"
    ).stat().st_mode & 0o777 == 0o600


def test_login_flow_raw_oauth_output_is_not_forwarded(
    tmp_path: Path,
    capsys,
) -> None:
    def noisy_login(
        _client_id: str,
        _client_secret: str,
        _redirect_uri: str,
        token_path: str,
        **_kwargs: object,
    ) -> object:
        print("https://example.invalid/authorize?state=must-not-leak")
        _token(Path(token_path), datetime.now(tz=UTC))
        return object()

    result = _manager(tmp_path, login_flow=noisy_login).renew()

    assert result.state is SchwabOAuthFlowState.SUCCEEDED
    assert "must-not-leak" not in capsys.readouterr().out


def test_next_action_uses_current_health_for_status_and_command_for_renew() -> None:
    assert (
        _next_action("status", "SUCCEEDED", SchwabOAuthHealthState.VALID)
        == "NO_ACTION_TOKEN_VALID"
    )
    assert (
        _next_action("status", "SUCCEEDED", SchwabOAuthHealthState.EXPIRING)
        == "PLAN_ONE_RENEW_FLOW_BEFORE_DUE"
    )
    assert (
        _next_action("status", "SUCCEEDED", SchwabOAuthHealthState.REAUTH_REQUIRED)
        == "START_ONE_RENEW_FLOW"
    )
    assert (
        _next_action("renew", "SUCCEEDED", SchwabOAuthHealthState.VALID)
        == "RETRY_ACCOUNT_SYNC_ONCE"
    )


def test_next_action_keeps_flow_safety_and_no_retry_diagnostics() -> None:
    assert (
        _next_action("status", "ACTIVE", SchwabOAuthHealthState.VALID)
        == "COMPLETE_EXISTING_BROWSER_TAB_DO_NOT_RERUN"
    )
    assert (
        _next_action("status", "FAILED", SchwabOAuthHealthState.VALID)
        == "CLOSE_OLD_TAB_THEN_EXPLICITLY_CONFIRM_NEW_FLOW"
    )
    assert (
        _next_action("status", "SUCCEEDED", SchwabOAuthHealthState.DISABLED)
        == "NO_RETRY_OAUTH_DISABLED"
    )
    assert (
        _next_action("status", "SUCCEEDED", SchwabOAuthHealthState.UNAVAILABLE)
        == "NO_RETRY_TOKEN_STATUS_UNAVAILABLE"
    )
