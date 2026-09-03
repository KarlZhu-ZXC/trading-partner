from __future__ import annotations

import json
from io import StringIO

import pytest

from application.ports.external_note_credential_store import ExternalNoteCredentialStatus
from domain.common.errors import DataContractError
from interfaces.cli import moomoo_notes_cookie as cookie_cli


class _Service:
    def __init__(self) -> None:
        self.value: str | None = None

    def credential_status(self, source_code: str) -> ExternalNoteCredentialStatus:
        return ExternalNoteCredentialStatus(
            source_code=source_code,
            supported=True,
            configured=self.value is not None,
        )

    def configure_credential(self, source_code: str, value: str) -> None:
        assert source_code == "MOOMOO_NOTE"
        if "=" not in value:
            raise DataContractError("invalid")
        self.value = value.strip()



def test_cookie_cli_reads_stdin_and_never_echoes_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "session=must-not-leak"
    service = _Service()
    monkeypatch.setattr(cookie_cli.sys, "stdin", StringIO(secret))

    assert cookie_cli._set(service) == 0  # type: ignore[arg-type]

    output = capsys.readouterr().out
    assert secret not in output
    assert json.loads(output)["configured"] is True
    assert service.value == secret


def test_cookie_cli_status_reports_configuration_without_cookie_value(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = _Service()
    cookie_value = "session=do-not-echo-cookie-value-8f2"
    monkeypatch.setattr(cookie_cli.sys, "stdin", StringIO(cookie_value))
    assert cookie_cli._set(service) == 0  # type: ignore[arg-type]
    capsys.readouterr()

    assert cookie_cli._status(service) == 0  # type: ignore[arg-type]

    payload = json.loads(capsys.readouterr().out)
    assert payload["configured"] is True
    assert payload["source_code"] == "MOOMOO_NOTE"
    assert cookie_value not in json.dumps(payload)
