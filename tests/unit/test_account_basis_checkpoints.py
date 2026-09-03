from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from domain.common.enums import VendorId
from domain.common.errors import ConfigurationError
from infrastructure.config.account_basis_checkpoints import (
    load_account_basis_checkpoints,
)


def _write_valid_checkpoint(path: Path) -> None:
    path.write_text(
        """version: 1
checkpoints:
  - checkpoint_id: basis_example
    provider: schwab
    account_ref: account_example
    instrument_id: equity:US:EXAMPLE
    currency: USD
    effective_at: '2026-01-02T15:00:00+00:00'
    quantity: '10'
    total_cost_basis: '1000.00'
    source_type: BROKER_STATEMENT
    source_ref: statement_example
    source_document_sha256: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    replaces_activity_id: null
""",
        encoding="utf-8",
    )


def test_checkpoint_loader_preserves_owner_verified_evidence(tmp_path: Path) -> None:
    path = tmp_path / "checkpoints.yaml"
    _write_valid_checkpoint(path)

    values = load_account_basis_checkpoints(path)

    (statement,) = values
    assert statement.provider is VendorId.SCHWAB
    assert statement.instrument_id == "equity:US:EXAMPLE"
    assert statement.quantity == Decimal("10")
    assert statement.total_cost_basis == Decimal("1000.00")
    assert statement.source_document_sha256 == (
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    assert statement.replaces_activity_id is None


def test_missing_optional_checkpoint_file_is_empty(tmp_path: Path) -> None:
    assert load_account_basis_checkpoints(tmp_path / "missing.yaml") == ()


def test_checkpoint_loader_rejects_unknown_shape(tmp_path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("version: 1\ncheckpoints: []\nextra: true\n", encoding="utf-8")

    with pytest.raises(ConfigurationError):
        load_account_basis_checkpoints(path)
