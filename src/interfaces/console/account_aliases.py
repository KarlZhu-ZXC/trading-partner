"""Read owner-managed Console labels without changing broker identities."""

from __future__ import annotations

import json
from pathlib import Path


def read_account_aliases(runtime_root: Path) -> dict[str, str]:
    path = runtime_root / "data" / "console" / "account-aliases.json"
    try:
        with path.open("rb") as stream:
            raw = stream.read(65_537)
    except FileNotFoundError:
        return {}
    if len(raw) > 65_536:
        raise ValueError("Invalid account aliases")
    value = json.loads(raw)
    if not isinstance(value, dict) or len(value) > 200:
        raise ValueError("Invalid account aliases")
    if any(
        not isinstance(key, str)
        or not 1 <= len(key) <= 128
        or not isinstance(label, str)
        or not 1 <= len(label.strip()) <= 80
        or any(ord(char) < 32 for char in key + label)
        for key, label in value.items()
    ):
        raise ValueError("Invalid account aliases")
    return {key: label.strip() for key, label in value.items()}
