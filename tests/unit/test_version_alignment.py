"""Product-version identity stays aligned across Python and Console surfaces."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from application import __version__
from interfaces.console.api import app

_ROOT = Path(__file__).resolve().parents[2]


def test_python_distribution_and_console_share_application_version() -> None:
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    console_package = json.loads(
        (_ROOT / "console" / "package.json").read_text(encoding="utf-8")
    )

    assert pyproject["project"]["dynamic"] == ["version"]
    assert pyproject["tool"]["hatch"]["version"]["path"] == "src/application/__init__.py"
    assert app.version == __version__
    assert console_package["version"] == __version__
