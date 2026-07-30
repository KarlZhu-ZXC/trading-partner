"""SQLAlchemy ORM declarations grouped by persistence capability."""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.persistence.metadata import Base


class InstrumentRow(Base):
    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint(
            "asset_type",
            "market",
            "symbol",
            name="uq_instruments_asset_type_market_symbol",
        ),
        CheckConstraint(
            "market IN ('A_SHARE','US','CME','DCE','OTC','LME')",
            name="ck_instruments_market",
        ),
        CheckConstraint(
            "asset_type IN ("
            "'equity','etf','index','option','future',"
            "'commodity_spot','cfd','benchmark')",
            name="ck_instruments_asset_type",
        ),
        CheckConstraint("is_active IN (0, 1)", name="ck_instruments_is_active"),
        CheckConstraint(
            "updated_at >= created_at",
            name="ck_instruments_updated_at",
        ),
        Index("ix_instruments_market_name", "market", "name"),
    )

    instrument_id: Mapped[str] = mapped_column(Text, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    market: Mapped[str] = mapped_column(Text, nullable=False)
    exchange: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(Text, nullable=False)
    asset_type: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False)
    listing_status: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str | None] = mapped_column(Text, nullable=True)
    mic: Mapped[str | None] = mapped_column(Text, nullable=True)
    underlying_instrument_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("instruments.instrument_id", ondelete="RESTRICT"),
        nullable=True,
    )
    multiplier: Mapped[str | None] = mapped_column(Text, nullable=True)
    tick_size: Mapped[str | None] = mapped_column(Text, nullable=True)
    lot_size: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class InstrumentAliasRow(Base):
    __tablename__ = "instrument_aliases"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "alias_type",
            "alias_value",
            name="uq_instrument_aliases_instrument_type_value",
        ),
        CheckConstraint(
            "is_primary IN (0, 1)",
            name="ck_instrument_aliases_is_primary",
        ),
        CheckConstraint(
            "market IN ('A_SHARE','US','CME','DCE','OTC','LME')",
            name="ck_instrument_aliases_market",
        ),
        Index("ix_instrument_aliases_value", "market", "alias_value"),
        Index("ix_instrument_aliases_instrument", "instrument_id"),
        Index(
            "uq_instrument_aliases_one_primary",
            "instrument_id",
            "alias_type",
            unique=True,
            sqlite_where=text("is_primary = 1"),
        ),
    )

    alias_id: Mapped[str] = mapped_column(Text, primary_key=True)
    instrument_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("instruments.instrument_id", ondelete="RESTRICT"),
        nullable=False,
    )
    alias_type: Mapped[str] = mapped_column(Text, nullable=False)
    alias_value: Mapped[str] = mapped_column(Text, nullable=False)
    alias_value_raw: Mapped[str] = mapped_column(Text, nullable=False)
    market: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    is_primary: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class ProviderCacheRow(Base):
    __tablename__ = "provider_cache"
    __table_args__ = (
        Index("ix_provider_cache_expires", "expires_at"),
        Index("ix_provider_cache_lookup", "market", "category", "instrument_id"),
    )

    cache_key: Mapped[str] = mapped_column(Text, primary_key=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    market: Mapped[str] = mapped_column(Text, nullable=False)
    instrument_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    vendor: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    as_of: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    freshness: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class ProviderHealthRow(Base):
    __tablename__ = "provider_health"
    __table_args__ = (
        CheckConstraint(
            "state IN ('ok','degraded','error')",
            name="ck_provider_health_state",
        ),
        CheckConstraint(
            "circuit_state IN ('closed','open','half_open')",
            name="ck_provider_health_circuit_state",
        ),
    )

    vendor: Mapped[str] = mapped_column(Text, primary_key=True)
    category: Mapped[str] = mapped_column(Text, primary_key=True)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False)
    last_success_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_failure_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    circuit_state: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class ProviderRateLimitRow(Base):
    __tablename__ = "provider_rate_limits"

    vendor: Mapped[str] = mapped_column(Text, primary_key=True)
    category: Mapped[str] = mapped_column(Text, primary_key=True)
    window_start: Mapped[str] = mapped_column(Text, primary_key=True)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)
    limit_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class RedditSampleCacheRow(Base):
    __tablename__ = "reddit_sample_cache"
    __table_args__ = (Index("ix_reddit_sample_cache_expires", "expires_at"),)

    instrument_id: Mapped[str] = mapped_column(Text, primary_key=True)
    config_key: Mapped[str] = mapped_column(Text, primary_key=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class RedditCooldownRow(Base):
    __tablename__ = "reddit_provider_cooldown"

    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    cooldown_until: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
