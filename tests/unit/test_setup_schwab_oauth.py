from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from infrastructure.system.process_file_lock import ProcessFileLock

_SCRIPT_PATH = Path(__file__).parents[2] / "scripts/setup_schwab_oauth.py"
_SPEC = importlib.util.spec_from_file_location("setup_schwab_oauth", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
setup_schwab_oauth = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(setup_schwab_oauth)


def _settings(tmp_path: Path) -> SimpleNamespace:
    token_path = tmp_path / "data/secrets/schwab_tokens.json"
    return SimpleNamespace(
        schwab_client_id="client-id",
        schwab_client_secret="client-secret",
        schwab_redirect_uri="https://127.0.0.1:8182",
        schwab_token_path=token_path,
    )


def test_replace_runs_exactly_one_locked_browser_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    settings.schwab_token_path.parent.mkdir(parents=True)
    settings.schwab_token_path.write_text("existing", encoding="utf-8")
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_login(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        settings.schwab_token_path.write_text("replacement", encoding="utf-8")
        return object()

    monkeypatch.setattr(setup_schwab_oauth.AppSettings, "load", lambda: settings)
    monkeypatch.setattr("schwab.auth.client_from_login_flow", fake_login)
    monkeypatch.setattr(sys, "argv", ["setup_schwab_oauth.py", "--replace"])

    assert setup_schwab_oauth.main() == 0
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == (
        "client-id",
        "client-secret",
        "https://127.0.0.1:8182",
        str(settings.schwab_token_path),
    )
    assert kwargs == {"callback_timeout": 300, "interactive": False}
    assert settings.schwab_token_path.stat().st_mode & 0o777 == 0o600


def test_concurrent_setup_reuses_existing_flow_without_opening_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    settings.schwab_token_path.parent.mkdir(parents=True)
    settings.schwab_token_path.write_text("existing", encoding="utf-8")
    outer_lock = ProcessFileLock(setup_schwab_oauth._lock_path(settings.schwab_token_path))
    assert outer_lock.acquire() is True
    login_calls = 0

    def fake_login(*_args: object, **_kwargs: object) -> object:
        nonlocal login_calls
        login_calls += 1
        return object()

    monkeypatch.setattr(setup_schwab_oauth.AppSettings, "load", lambda: settings)
    monkeypatch.setattr("schwab.auth.client_from_login_flow", fake_login)
    monkeypatch.setattr(sys, "argv", ["setup_schwab_oauth.py", "--replace"])
    try:
        with pytest.raises(SystemExit, match="already running.*existing browser tab"):
            setup_schwab_oauth.main()
    finally:
        outer_lock.release()
    assert login_calls == 0
