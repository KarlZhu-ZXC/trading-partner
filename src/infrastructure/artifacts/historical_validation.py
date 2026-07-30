"""Filesystem artifact repository for the QuantConnect Free bridge."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path

from application.ports.historical_validation_artifact_repository import (
    ImportedValidationArtifact,
    PreparedValidationArtifact,
)
from domain.common.errors import DataContractError, IdempotencyConflict, PersistenceError


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class FileHistoricalValidationArtifactRepository:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._keys = self._root / ".idempotency"

    def prepare(
        self,
        *,
        validation_id: str,
        idempotency_key: str,
        request_sha256: str,
        strategy_code: str,
        manifest: Mapping[str, object],
        runbook: str,
    ) -> PreparedValidationArtifact:
        key_path = self._key_path("prepare", idempotency_key)
        existing = self._read_key(key_path)
        if existing is not None:
            self._assert_same_request(existing, request_sha256)
            return self._prepared_record(str(existing["validation_id"]), duplicate=True)

        directory = self._root / validation_id
        if directory.exists():
            raise PersistenceError("Historical validation artifact directory already exists")
        directory.mkdir(mode=0o700, parents=True)
        code_payload = strategy_code.encode("utf-8")
        manifest_payload = _canonical_json(manifest)
        _atomic_write(directory / "main.py", code_payload)
        _atomic_write(directory / "manifest.json", manifest_payload)
        _atomic_write(directory / "RUNBOOK.md", runbook.encode("utf-8"))
        _atomic_write(
            key_path,
            _canonical_json(
                {
                    "operation": "prepare",
                    "request_sha256": request_sha256,
                    "validation_id": validation_id,
                }
            ),
        )
        return self._prepared_record(validation_id, duplicate=False)

    def load_manifest(self, validation_id: str) -> dict[str, object]:
        manifest_path = self._validation_directory(validation_id) / "manifest.json"
        try:
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise DataContractError("validation_id does not reference a prepared package") from exc
        except json.JSONDecodeError as exc:
            raise PersistenceError("Prepared validation manifest is corrupted") from exc
        if not isinstance(value, dict):
            raise PersistenceError("Prepared validation manifest has an invalid root")
        return value

    def import_result(
        self,
        *,
        validation_id: str,
        idempotency_key: str,
        request_sha256: str,
        source_path: Path,
        summary: Mapping[str, object],
    ) -> ImportedValidationArtifact:
        directory = self._validation_directory(validation_id)
        if not (directory / "manifest.json").is_file():
            raise DataContractError("validation_id does not reference a prepared package")
        key_path = self._key_path("import", idempotency_key)
        existing = self._read_key(key_path)
        if existing is not None:
            self._assert_same_request(existing, request_sha256)
            return self._imported_record(validation_id, duplicate=True)

        result_file = directory / "result.json"
        summary_file = directory / "result-summary.json"
        source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
        duplicate_result = result_file.exists()
        if result_file.exists():
            current_sha256 = hashlib.sha256(result_file.read_bytes()).hexdigest()
            if current_sha256 != source_sha256:
                raise IdempotencyConflict(
                    "Prepared validation already has a different imported result"
                )
            if not summary_file.exists() or summary_file.read_bytes() != _canonical_json(summary):
                raise IdempotencyConflict(
                    "Prepared validation result metadata differs from the immutable import"
                )
        else:
            descriptor, temporary = tempfile.mkstemp(
                prefix=".result.json.", dir=directory
            )
            os.close(descriptor)
            try:
                shutil.copyfile(source_path, temporary)
                os.chmod(temporary, 0o600)
                os.replace(temporary, result_file)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            _atomic_write(summary_file, _canonical_json(summary))
        _atomic_write(
            key_path,
            _canonical_json(
                {
                    "operation": "import",
                    "request_sha256": request_sha256,
                    "validation_id": validation_id,
                    "result_sha256": source_sha256,
                }
            ),
        )
        return self._imported_record(validation_id, duplicate=duplicate_result)

    def _prepared_record(
        self, validation_id: str, *, duplicate: bool
    ) -> PreparedValidationArtifact:
        directory = self._validation_directory(validation_id)
        main_file = directory / "main.py"
        manifest_file = directory / "manifest.json"
        return PreparedValidationArtifact(
            validation_id=validation_id,
            artifact_directory=directory,
            main_file=main_file,
            manifest_file=manifest_file,
            runbook_file=directory / "RUNBOOK.md",
            code_sha256=hashlib.sha256(main_file.read_bytes()).hexdigest(),
            manifest_sha256=hashlib.sha256(manifest_file.read_bytes()).hexdigest(),
            duplicate=duplicate,
        )

    def _imported_record(
        self, validation_id: str, *, duplicate: bool
    ) -> ImportedValidationArtifact:
        directory = self._validation_directory(validation_id)
        result_file = directory / "result.json"
        return ImportedValidationArtifact(
            validation_id=validation_id,
            result_file=result_file,
            summary_file=directory / "result-summary.json",
            result_sha256=hashlib.sha256(result_file.read_bytes()).hexdigest(),
            duplicate=duplicate,
        )

    def _validation_directory(self, validation_id: str) -> Path:
        candidate = (self._root / validation_id).resolve()
        if candidate.parent != self._root or not validation_id.startswith("validation_"):
            raise DataContractError("validation_id is invalid")
        return candidate

    def _key_path(self, operation: str, idempotency_key: str) -> Path:
        digest = hashlib.sha256(f"{operation}:{idempotency_key}".encode()).hexdigest()
        return self._keys / f"{digest}.json"

    @staticmethod
    def _read_key(path: Path) -> dict[str, object] | None:
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PersistenceError("Historical validation idempotency record is corrupted") from exc
        if not isinstance(value, dict):
            raise PersistenceError("Historical validation idempotency record is invalid")
        return value

    @staticmethod
    def _assert_same_request(existing: Mapping[str, object], request_sha256: str) -> None:
        if existing.get("request_sha256") != request_sha256:
            raise IdempotencyConflict("Historical validation idempotency key was reused")
