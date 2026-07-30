from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from application.dto.schwab_oauth import SchwabOAuthHealthState
from infrastructure.providers.account.schwab_oauth import (
    SchwabOAuthFlowManager,
    SchwabOAuthFlowState,
    SchwabOAuthTokenInspector,
)
from infrastructure.system.process_file_lock import ProcessFileLock


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
