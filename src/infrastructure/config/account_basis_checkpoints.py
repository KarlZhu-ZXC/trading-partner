"""Strict loader for owner-verified broker position-basis checkpoints."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from domain.attribution.models import PositionBasisCheckpoint
from domain.common.enums import VendorId
from domain.common.errors import ConfigurationError, DataContractError

_SUPPORTED_VERSION = 1
_ROOT_KEYS = frozenset({"version", "checkpoints"})
_ENTRY_KEYS = frozenset(
    {
        "checkpoint_id",
        "provider",
        "account_ref",
        "instrument_id",
        "currency",
        "effective_at",
        "quantity",
        "total_cost_basis",
        "source_type",
        "source_ref",
        "source_document_sha256",
        "replaces_activity_id",
    }
)
def _error(reason: str) -> ConfigurationError:
    return ConfigurationError(
        "Account basis checkpoint configuration is invalid",
        details={"reason": reason},
    )


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(f"invalid_{field}")
    return value.strip()


def load_account_basis_checkpoints(
    path: Path,
) -> tuple[PositionBasisCheckpoint, ...]:
    selected = path.expanduser().resolve()
    if not selected.exists():
        return ()
    try:
        payload = yaml.safe_load(selected.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise _error("file_unavailable_or_malformed") from exc
    if not isinstance(payload, dict) or set(payload) != _ROOT_KEYS:
        raise _error("invalid_root")
    if payload["version"] != _SUPPORTED_VERSION:
        raise _error("unsupported_version")
    entries = payload["checkpoints"]
    if not isinstance(entries, list):
        raise _error("invalid_checkpoints")
    checkpoints = tuple(_parse_entry(entry) for entry in entries)
    ids = tuple(item.checkpoint_id for item in checkpoints)
    if len(ids) != len(set(ids)):
        raise _error("duplicate_checkpoint_id")
    replacements = tuple(
        item.replaces_activity_id
        for item in checkpoints
        if item.replaces_activity_id is not None
    )
    if len(replacements) != len(set(replacements)):
        raise _error("duplicate_replacement_activity")
    return tuple(sorted(checkpoints, key=lambda item: (item.effective_at, item.checkpoint_id)))


def _parse_entry(entry: object) -> PositionBasisCheckpoint:
    if not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
        raise _error("invalid_entry")
    try:
        provider = VendorId(_text(entry["provider"], "provider").lower())
        effective_at = datetime.fromisoformat(_text(entry["effective_at"], "effective_at"))
        quantity = Decimal(str(entry["quantity"]))
        total_cost_basis = Decimal(str(entry["total_cost_basis"]))
        document_hash = entry["source_document_sha256"]
        replacement = entry["replaces_activity_id"]
        return PositionBasisCheckpoint(
            checkpoint_id=_text(entry["checkpoint_id"], "checkpoint_id"),
            provider=provider,
            account_ref=_text(entry["account_ref"], "account_ref"),
            instrument_id=_text(entry["instrument_id"], "instrument_id"),
            currency=_text(entry["currency"], "currency").upper(),
            effective_at=effective_at,
            quantity=quantity,
            total_cost_basis=total_cost_basis,
            source_type=_text(entry["source_type"], "source_type"),
            source_ref=_text(entry["source_ref"], "source_ref"),
            source_document_sha256=(
                _text(document_hash, "source_document_sha256").lower()
                if document_hash is not None
                else None
            ),
            replaces_activity_id=(
                _text(replacement, "replaces_activity_id")
                if replacement is not None
                else None
            ),
        )
    except (ValueError, InvalidOperation, DataContractError) as exc:
        raise _error("invalid_entry_value") from exc


__all__ = ["load_account_basis_checkpoints"]
