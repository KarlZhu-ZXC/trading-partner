from __future__ import annotations

from pathlib import Path

import pytest

from domain.common.enums import AssetType
from domain.common.errors import ConfigurationError
from infrastructure.providers.watchlist.moomoo_security_corrections import (
    MoomooSecurityCorrections,
)


def test_tracked_correction_file_contains_audited_spg_override() -> None:
    correction = MoomooSecurityCorrections.load_default().for_code("US.SPG")

    assert correction is not None
    assert correction.asset_type is AssetType.EQUITY
    assert correction.display_name == "Simon Property Group, Inc."


def test_tracked_correction_file_identifies_soxl_as_an_etf() -> None:
    correction = MoomooSecurityCorrections.load_default().for_code("US.SOXL")

    assert correction is not None
    assert correction.asset_type is AssetType.ETF
    assert correction.display_name == "Direxion Daily Semiconductor Bull 3X Shares"


def test_correction_file_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "corrections.yaml"
    path.write_text(
        "version: 1\ncorrections:\n  - provider_code: US.SPG\n"
        "    asset_type: EQUITY\n    display_name: Simon Property Group, Inc.\n"
        "    reason: verified\n    verified_on: 2026-07-19\n    typo: true\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="entry is invalid"):
        MoomooSecurityCorrections.load(path)
