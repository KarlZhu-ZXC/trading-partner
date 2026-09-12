import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from interfaces.console.account_aliases import read_account_aliases
from interfaces.console.api import account_aliases


def test_aliases_are_optional_and_preserve_exact_identity(tmp_path: Path) -> None:
    assert read_account_aliases(tmp_path) == {}
    folder = tmp_path / "data" / "console"
    folder.mkdir(parents=True)
    values = {"schwab_test_small": "Schwab IRA", "schwab_test_large": "Schwab Brokerage"}
    (folder / "account-aliases.json").write_text(json.dumps(values))
    assert read_account_aliases(tmp_path) == values


@pytest.mark.parametrize(
    "payload", ["[]", '{"account": 12}', '{"account": ""}', '{"account": "bad\\nlabel"}']
)
async def test_invalid_alias_file_returns_only_safe_error(tmp_path: Path, payload: str) -> None:
    folder = tmp_path / "data" / "console"
    folder.mkdir(parents=True)
    (folder / "account-aliases.json").write_text(payload)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                container=SimpleNamespace(settings=SimpleNamespace(runtime_root=tmp_path))
            )
        )
    )
    with pytest.raises(HTTPException) as error:
        await account_aliases(request)  # type: ignore[arg-type]
    assert error.value.status_code == 503
    assert error.value.detail == "Account aliases unavailable"
