"""Phase 3A-0 formal futures definition tables and market/asset append.

Revision ID: 0017_phase3a_futures_definitions
Revises: 0016_monitor_valid_until
Create Date: 2026-07-25

Append-only Market/AssetType wire values on instrument registry constraints and
durable futures product/contract/continuous definition tables. Does not rewrite
existing future:US:* Yahoo continuous-proxy identities.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0017_phase3a_futures_definitions"
down_revision: str | Sequence[str] | None = "0016_monitor_valid_until"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VERSION = revision
_DESCRIPTION = "Phase 3A-0 futures definitions and append-only market/asset types"

_MARKET_CHECK = "market IN ('A_SHARE','US','CME','DCE','OTC','LME')"
_ASSET_TYPE_CHECK = (
    "asset_type IN ("
    "'equity','etf','index','option','future',"
    "'commodity_spot','cfd','benchmark')"
)
_MARKET_CHECK_PREV = "market IN ('A_SHARE','US')"
_ASSET_TYPE_CHECK_PREV = "asset_type IN ('equity','etf','index','option','future')"


def upgrade() -> None:
    with op.batch_alter_table("instruments") as batch:
        batch.drop_constraint("ck_instruments_market", type_="check")
        batch.create_check_constraint("ck_instruments_market", _MARKET_CHECK)
        batch.drop_constraint("ck_instruments_asset_type", type_="check")
        batch.create_check_constraint("ck_instruments_asset_type", _ASSET_TYPE_CHECK)

    with op.batch_alter_table("instrument_aliases") as batch:
        batch.drop_constraint("ck_instrument_aliases_market", type_="check")
        batch.create_check_constraint("ck_instrument_aliases_market", _MARKET_CHECK)

    op.create_table(
        "futures_products",
        sa.Column("product_id", sa.Text(), primary_key=True),
        sa.Column("product_key", sa.Text(), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("root", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("product_key", name="uq_futures_products_product_key"),
        sa.CheckConstraint(
            "market IN ('CME','DCE','US','LME')",
            name="ck_futures_products_market",
        ),
    )
    op.create_index("ix_futures_products_market_root", "futures_products", ["market", "root"])

    op.create_table(
        "futures_product_versions",
        sa.Column("version_id", sa.Text(), primary_key=True),
        sa.Column(
            "product_id",
            sa.Text(),
            sa.ForeignKey("futures_products.product_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("exchange", sa.Text(), nullable=False),
        sa.Column("commodity", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("price_unit", sa.Text(), nullable=False),
        sa.Column("multiplier", sa.Text(), nullable=False),
        sa.Column("tick_size", sa.Text(), nullable=False),
        sa.Column("settlement_method", sa.Text(), nullable=False),
        sa.Column("session_calendar_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.Text(), nullable=False),
        sa.Column("valid_to", sa.Text(), nullable=True),
        sa.Column("definition_as_of", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "product_id",
            "version",
            name="uq_futures_product_versions_product_version",
        ),
        sa.CheckConstraint("version >= 1", name="ck_futures_product_versions_version"),
        sa.CheckConstraint(
            "settlement_method IN ('physical','cash','unknown')",
            name="ck_futures_product_versions_settlement_method",
        ),
    )
    op.create_index(
        "ix_futures_product_versions_product_valid",
        "futures_product_versions",
        ["product_id", "valid_from"],
    )

    op.create_table(
        "futures_contracts",
        sa.Column("instrument_id", sa.Text(), primary_key=True),
        sa.Column(
            "product_id",
            sa.Text(),
            sa.ForeignKey("futures_products.product_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("contract_month", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "product_id",
            "contract_month",
            name="uq_futures_contracts_product_month",
        ),
    )
    op.create_index(
        "ix_futures_contracts_product",
        "futures_contracts",
        ["product_id", "contract_month"],
    )

    op.create_table(
        "futures_contract_versions",
        sa.Column("version_id", sa.Text(), primary_key=True),
        sa.Column(
            "instrument_id",
            sa.Text(),
            sa.ForeignKey("futures_contracts.instrument_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("listed_at", sa.Text(), nullable=True),
        sa.Column("first_trade_at", sa.Text(), nullable=True),
        sa.Column("last_trade_at", sa.Text(), nullable=True),
        sa.Column("expiration_at", sa.Text(), nullable=True),
        sa.Column("first_notice_at", sa.Text(), nullable=True),
        sa.Column("delivery_start", sa.Text(), nullable=True),
        sa.Column("delivery_end", sa.Text(), nullable=True),
        sa.Column("settlement_at", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("definition_as_of", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "instrument_id",
            "version",
            name="uq_futures_contract_versions_instrument_version",
        ),
        sa.CheckConstraint("version >= 1", name="ck_futures_contract_versions_version"),
        sa.CheckConstraint(
            "status IN ('listed','active','expired','delisted','unknown')",
            name="ck_futures_contract_versions_status",
        ),
    )
    op.create_index(
        "ix_futures_contract_versions_instrument_asof",
        "futures_contract_versions",
        ["instrument_id", "definition_as_of"],
    )

    op.create_table(
        "continuous_series_definitions",
        sa.Column("instrument_id", sa.Text(), primary_key=True),
        sa.Column(
            "product_id",
            sa.Text(),
            sa.ForeignKey("futures_products.product_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("roll_rule", sa.Text(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("adjustment", sa.Text(), nullable=False),
        sa.Column("provider_methodology_version", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.Text(), nullable=False),
        sa.Column("valid_to", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint("rank >= 0", name="ck_continuous_series_rank"),
        sa.CheckConstraint(
            "roll_rule IN ('calendar','volume','open_interest')",
            name="ck_continuous_series_roll_rule",
        ),
        sa.CheckConstraint(
            "adjustment = 'none'",
            name="ck_continuous_series_adjustment",
        ),
    )
    op.create_index(
        "ix_continuous_series_product",
        "continuous_series_definitions",
        ["product_id", "roll_rule", "rank"],
    )

    op.create_table(
        "continuous_contract_mappings",
        sa.Column("mapping_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "continuous_instrument_id",
            sa.Text(),
            sa.ForeignKey(
                "continuous_series_definitions.instrument_id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column(
            "contract_instrument_id",
            sa.Text(),
            sa.ForeignKey("futures_contracts.instrument_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("effective_from", sa.Text(), nullable=False),
        sa.Column("effective_to", sa.Text(), nullable=True),
        sa.Column("mapping_source", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "continuous_instrument_id",
            "effective_from",
            name="uq_continuous_mapping_effective_from",
        ),
    )
    op.create_index(
        "ix_continuous_contract_mappings_series",
        "continuous_contract_mappings",
        ["continuous_instrument_id", "effective_from"],
    )

    op.execute(
        sa.text(
            "INSERT INTO schema_versions(version, applied_at, description) "
            "VALUES (:version, :applied_at, :description)"
        ).bindparams(
            version=_VERSION,
            applied_at=datetime.now(UTC).isoformat(),
            description=_DESCRIPTION,
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM schema_versions WHERE version = :version").bindparams(
            version=_VERSION
        )
    )
    op.drop_index(
        "ix_continuous_contract_mappings_series",
        table_name="continuous_contract_mappings",
    )
    op.drop_table("continuous_contract_mappings")
    op.drop_index("ix_continuous_series_product", table_name="continuous_series_definitions")
    op.drop_table("continuous_series_definitions")
    op.drop_index(
        "ix_futures_contract_versions_instrument_asof",
        table_name="futures_contract_versions",
    )
    op.drop_table("futures_contract_versions")
    op.drop_index("ix_futures_contracts_product", table_name="futures_contracts")
    op.drop_table("futures_contracts")
    op.drop_index(
        "ix_futures_product_versions_product_valid",
        table_name="futures_product_versions",
    )
    op.drop_table("futures_product_versions")
    op.drop_index("ix_futures_products_market_root", table_name="futures_products")
    op.drop_table("futures_products")

    with op.batch_alter_table("instrument_aliases") as batch:
        batch.drop_constraint("ck_instrument_aliases_market", type_="check")
        batch.create_check_constraint(
            "ck_instrument_aliases_market",
            _MARKET_CHECK_PREV,
        )

    with op.batch_alter_table("instruments") as batch:
        batch.drop_constraint("ck_instruments_market", type_="check")
        batch.create_check_constraint("ck_instruments_market", _MARKET_CHECK_PREV)
        batch.drop_constraint("ck_instruments_asset_type", type_="check")
        batch.create_check_constraint(
            "ck_instruments_asset_type",
            _ASSET_TYPE_CHECK_PREV,
        )
