"""Read-only source, schema, and Console build identity for diagnostics."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, Literal

from application import __version__
from application.ports.database import CURRENT_MIGRATION_HEAD

# A caller may pass an explicit expected head when it is serving another
# packaged release during a controlled rollout.
DEFAULT_MIGRATION_HEAD: Final[str] = CURRENT_MIGRATION_HEAD
_GIT_REVISION = re.compile(r"^[0-9a-fA-F]{7,64}$")
_BUILD_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


@dataclass(frozen=True, slots=True)
class RuntimeIdentitySnapshot:
    """Safe identifiers describing one running Console source/build snapshot."""

    application_version: str
    git_revision: str | None
    git_dirty: bool | None
    schema_expected_head: str
    schema_actual_heads: tuple[str, ...]
    schema_compatible: bool
    next_build_id: str | None
    identity_basis: Literal["on_disk"]

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-serializable snapshot without runtime internals."""

        return asdict(self)


def _default_source_root() -> Path:
    """Find the source checkout when running from the repository."""

    candidates = (Path.cwd().resolve(), *Path(__file__).resolve().parents)
    for candidate in candidates:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return Path.cwd().resolve()


def _git_revision(source_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ("git", "rev-parse", "--verify", "--short=12", "HEAD"),
            cwd=source_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    revision = result.stdout.strip()
    return revision if result.returncode == 0 and _GIT_REVISION.fullmatch(revision) else None


def _git_dirty(source_root: Path) -> bool | None:
    """Return a boolean dirty marker without reading or exposing file names."""

    try:
        result = subprocess.run(
            ("git", "status", "--porcelain=v1", "--untracked-files=normal"),
            cwd=source_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(result.stdout.strip()) if result.returncode == 0 else None


def _next_build_id(console_root: Path | None) -> str | None:
    if console_root is None:
        return None
    path = console_root.expanduser().resolve() / ".next" / "BUILD_ID"
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    return value if _BUILD_ID.fullmatch(value) else None


def build_runtime_identity(
    *,
    schema_actual_heads: Sequence[str] = (),
    schema_expected_head: str = DEFAULT_MIGRATION_HEAD,
    source_root: Path | None = None,
    console_root: Path | None = None,
) -> RuntimeIdentitySnapshot:
    """Build a safe, read-only identity snapshot.

    ``schema_actual_heads`` is supplied by the already-open local database
    reader. This helper deliberately does not open a database or perform a
    migration, so it can be used by the Console diagnostics layer without
    creating another persistence path.
    """

    source = source_root.expanduser().resolve() if source_root else _default_source_root()
    actual_heads = tuple(schema_actual_heads)
    resolved_console_root = console_root
    if resolved_console_root is None:
        candidate = source / "console"
        if candidate.is_dir():
            resolved_console_root = candidate
    return RuntimeIdentitySnapshot(
        application_version=__version__,
        git_revision=_git_revision(source),
        git_dirty=_git_dirty(source),
        schema_expected_head=schema_expected_head,
        schema_actual_heads=actual_heads,
        schema_compatible=len(actual_heads) == 1 and actual_heads[0] == schema_expected_head,
        next_build_id=_next_build_id(resolved_console_root),
        identity_basis="on_disk",
    )


__all__ = [
    "DEFAULT_MIGRATION_HEAD",
    "RuntimeIdentitySnapshot",
    "build_runtime_identity",
]
