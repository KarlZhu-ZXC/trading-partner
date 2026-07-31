"""Ensure .env is gitignored."""

from __future__ import annotations

import subprocess
from pathlib import Path


def test_env_is_gitignored(project_root: Path) -> None:
    env_path = project_root / ".env"
    # File may or may not exist; check git check-ignore
    result = subprocess.run(
        ["git", "check-ignore", "-q", ".env"],
        cwd=project_root,
        check=False,
    )
    assert result.returncode == 0, ".env must be ignored by git"

    # .env.example must NOT be ignored
    result_example = subprocess.run(
        ["git", "check-ignore", "-q", ".env.example"],
        cwd=project_root,
        check=False,
    )
    assert result_example.returncode == 1, ".env.example must be tracked"

    # Pattern also covers if .env exists with content
    if env_path.exists():
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", ".env"],
            cwd=project_root,
            check=False,
            capture_output=True,
        )
        assert tracked.returncode != 0, ".env must not be tracked by git"


def test_runtime_secrets_databases_artifacts_and_backups_are_gitignored(
    project_root: Path,
) -> None:
    paths = (
        "data/trading_partner.db",
        "data/secrets/schwab_tokens.json",
        "data/artifacts/technical/chart.png",
        "data/backups/trading-partner-test.db",
    )
    result = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=project_root,
        input="\n".join(paths),
        text=True,
        check=False,
        capture_output=True,
    )
    assert result.returncode == 0
    assert set(result.stdout.splitlines()) == set(paths)
