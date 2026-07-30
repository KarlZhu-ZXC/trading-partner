"""Port for immutable local historical-validation artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PreparedValidationArtifact:
    validation_id: str
    artifact_directory: Path
    main_file: Path
    manifest_file: Path
    runbook_file: Path
    code_sha256: str
    manifest_sha256: str
    duplicate: bool


@dataclass(frozen=True, slots=True)
class ImportedValidationArtifact:
    validation_id: str
    result_file: Path
    summary_file: Path
    result_sha256: str
    duplicate: bool


class HistoricalValidationArtifactRepository(Protocol):
    def prepare(
        self,
        *,
        validation_id: str,
        idempotency_key: str,
        request_sha256: str,
        strategy_code: str,
        manifest: Mapping[str, object],
        runbook: str,
    ) -> PreparedValidationArtifact: ...

    def load_manifest(self, validation_id: str) -> dict[str, object]: ...

    def import_result(
        self,
        *,
        validation_id: str,
        idempotency_key: str,
        request_sha256: str,
        source_path: Path,
        summary: Mapping[str, object],
    ) -> ImportedValidationArtifact: ...
