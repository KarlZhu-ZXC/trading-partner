"""SQLAlchemy ORM declarations grouped by persistence capability."""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.persistence.metadata import Base


class FuturesProductRow(Base):
    __tablename__ = "futures_products"
    __table_args__ = (
        UniqueConstraint("product_key", name="uq_futures_products_product_key"),
        CheckConstraint(
            "market IN ('CME','DCE','US','LME')",
            name="ck_futures_products_market",
        ),
        Index("ix_futures_products_market_root", "market", "root"),
    )

    product_id: Mapped[str] = mapped_column(Text, primary_key=True)
    product_key: Mapped[str] = mapped_column(Text, nullable=False)
    market: Mapped[str] = mapped_column(Text, nullable=False)
    root: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class FuturesProductVersionRow(Base):
    __tablename__ = "futures_product_versions"
    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "version",
            name="uq_futures_product_versions_product_version",
        ),
        CheckConstraint("version >= 1", name="ck_futures_product_versions_version"),
        CheckConstraint(
            "settlement_method IN ('physical','cash','unknown')",
            name="ck_futures_product_versions_settlement_method",
        ),
        Index(
            "ix_futures_product_versions_product_valid",
            "product_id",
            "valid_from",
        ),
    )

    version_id: Mapped[str] = mapped_column(Text, primary_key=True)
    product_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("futures_products.product_id", ondelete="RESTRICT"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    exchange: Mapped[str] = mapped_column(Text, nullable=False)
    commodity: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    price_unit: Mapped[str] = mapped_column(Text, nullable=False)
    multiplier: Mapped[str] = mapped_column(Text, nullable=False)
    tick_size: Mapped[str] = mapped_column(Text, nullable=False)
    settlement_method: Mapped[str] = mapped_column(Text, nullable=False)
    session_calendar_id: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[str] = mapped_column(Text, nullable=False)
    valid_to: Mapped[str | None] = mapped_column(Text)
    definition_as_of: Mapped[str] = mapped_column(Text, nullable=False)


class FuturesContractRow(Base):
    __tablename__ = "futures_contracts"
    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "contract_month",
            name="uq_futures_contracts_product_month",
        ),
        Index("ix_futures_contracts_product", "product_id", "contract_month"),
    )

    instrument_id: Mapped[str] = mapped_column(Text, primary_key=True)
    product_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("futures_products.product_id", ondelete="RESTRICT"),
        nullable=False,
    )
    contract_month: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class FuturesContractVersionRow(Base):
    __tablename__ = "futures_contract_versions"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "version",
            name="uq_futures_contract_versions_instrument_version",
        ),
        CheckConstraint("version >= 1", name="ck_futures_contract_versions_version"),
        CheckConstraint(
            "status IN ('listed','active','expired','delisted','unknown')",
            name="ck_futures_contract_versions_status",
        ),
        Index(
            "ix_futures_contract_versions_instrument_asof",
            "instrument_id",
            "definition_as_of",
        ),
    )

    version_id: Mapped[str] = mapped_column(Text, primary_key=True)
    instrument_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("futures_contracts.instrument_id", ondelete="RESTRICT"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    listed_at: Mapped[str | None] = mapped_column(Text)
    first_trade_at: Mapped[str | None] = mapped_column(Text)
    last_trade_at: Mapped[str | None] = mapped_column(Text)
    expiration_at: Mapped[str | None] = mapped_column(Text)
    first_notice_at: Mapped[str | None] = mapped_column(Text)
    delivery_start: Mapped[str | None] = mapped_column(Text)
    delivery_end: Mapped[str | None] = mapped_column(Text)
    settlement_at: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    definition_as_of: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)


class FuturesContractStatisticsRow(Base):
    __tablename__ = "futures_contract_statistics"
    __table_args__ = (
        CheckConstraint(
            "settlement_status IN ('preliminary','final','unknown')",
            name="ck_futures_contract_statistics_status",
        ),
        Index(
            "ix_futures_contract_statistics_trade_date",
            "trade_date",
            "instrument_id",
        ),
    )

    instrument_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("futures_contracts.instrument_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    trade_date: Mapped[str] = mapped_column(Text, primary_key=True)
    published_at: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    settlement: Mapped[str | None] = mapped_column(Text)
    settlement_status: Mapped[str] = mapped_column(Text, nullable=False)
    session_volume: Mapped[str | None] = mapped_column(Text)
    open_interest: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[str] = mapped_column(Text, nullable=False)


class ContinuousSeriesDefinitionRow(Base):
    __tablename__ = "continuous_series_definitions"
    __table_args__ = (
        CheckConstraint("rank >= 0", name="ck_continuous_series_rank"),
        CheckConstraint(
            "roll_rule IN ('calendar','volume','open_interest')",
            name="ck_continuous_series_roll_rule",
        ),
        CheckConstraint("adjustment = 'none'", name="ck_continuous_series_adjustment"),
        Index(
            "ix_continuous_series_product",
            "product_id",
            "roll_rule",
            "rank",
        ),
    )

    instrument_id: Mapped[str] = mapped_column(Text, primary_key=True)
    product_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("futures_products.product_id", ondelete="RESTRICT"),
        nullable=False,
    )
    roll_rule: Mapped[str] = mapped_column(Text, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    adjustment: Mapped[str] = mapped_column(Text, nullable=False)
    provider_methodology_version: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[str] = mapped_column(Text, nullable=False)
    valid_to: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class ContinuousContractMappingRow(Base):
    __tablename__ = "continuous_contract_mappings"
    __table_args__ = (
        UniqueConstraint(
            "continuous_instrument_id",
            "effective_from",
            name="uq_continuous_mapping_effective_from",
        ),
        Index(
            "ix_continuous_contract_mappings_series",
            "continuous_instrument_id",
            "effective_from",
        ),
    )

    mapping_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    continuous_instrument_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("continuous_series_definitions.instrument_id", ondelete="RESTRICT"),
        nullable=False,
    )
    contract_instrument_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("futures_contracts.instrument_id", ondelete="RESTRICT"),
        nullable=False,
    )
    effective_from: Mapped[str] = mapped_column(Text, nullable=False)
    effective_to: Mapped[str | None] = mapped_column(Text)
    mapping_source: Mapped[str] = mapped_column(Text, nullable=False)
