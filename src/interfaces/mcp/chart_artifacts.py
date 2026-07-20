"""Local artifact promotion for MCP clients that do not surface image blocks."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_SAFE_STEM = re.compile(r"^[A-Za-z0-9._-]{1,160}$")


@dataclass(frozen=True, slots=True)
class LocalChartArtifact:
    path: Path
    mime_type: str = "image/png"

    @property
    def markdown(self) -> str:
        return f"![Technical chart](<{self.path}>)"


def persist_chart_png(
    png: bytes,
    *,
    request_id: str,
    root: Path | None = None,
) -> LocalChartArtifact:
    """Atomically persist one PNG and return an absolute local reference."""
    if not png.startswith(_PNG_SIGNATURE):
        raise ValueError("technical chart payload is not a PNG")

    stem = (
        request_id
        if _SAFE_STEM.fullmatch(request_id)
        else hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:32]
    )
    artifact_root = (root or Path.cwd() / "data" / "artifacts" / "technical").resolve()
    artifact_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = artifact_root / f"{stem}.png"

    descriptor, temporary_name = tempfile.mkstemp(
        dir=artifact_root,
        prefix=f".{stem}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(png)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, destination)
        destination.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)

    return LocalChartArtifact(path=destination)
