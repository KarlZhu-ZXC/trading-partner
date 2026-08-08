"""Research Subject candidate-Instrument selection persistence."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.persistence.metadata import Base
from infrastructure.persistence.orm.common import JsonStringTuple


class WatchlistItemRow(Base):
    """Legacy Research WatchlistItem evolved into a confirmed candidate pool."""

    __tablename__ = "watchlist_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('watching','triggered','shortlisted','selected','rejected',"
            "'promoted_to_case','expired','archived')",
            name="ck_watchlist_status",
        ),
        CheckConstraint(
            "(status = 'promoted_to_case') = (promoted_to_case_id IS NOT NULL)",
            name="ck_watchlist_promoted",
        ),
        CheckConstraint(
            "(status = 'triggered') = (triggered_at IS NOT NULL)",
            name="ck_watchlist_triggered",
        ),
        CheckConstraint(
            "(status = 'triggered') = (triggered_reason IS NOT NULL)",
            name="ck_watchlist_triggered_reason",
        ),
        CheckConstraint("updated_at >= created_at", name="ck_watchlist_updated_at"),
        CheckConstraint("market IN ('A_SHARE','US')", name="ck_watchlist_market"),
        CheckConstraint(
            "(status IN ('selected','rejected')) = (selection_reason IS NOT NULL)",
            name="ck_watchlist_selection_reason",
        ),
        Index("ix_watchlist_status", "status"),
        Index("ix_watchlist_case_id", "case_id"),
        Index("ix_watchlist_market_symbol", "market", "symbol"),
        Index(
            "uq_watchlist_selected_per_case",
            "case_id",
            unique=True,
            sqlite_where=text("status = 'selected' AND case_id IS NOT NULL"),
        ),
    )

    item_id: Mapped[str] = mapped_column(Text, primary_key=True)
    market: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    instrument_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    thesis_hint: Mapped[str] = mapped_column(Text, nullable=False)
    triggers_json: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False, default=()
    )
    subject_id: Mapped[str | None] = mapped_column(
        "case_id",
        Text,
        ForeignKey("investment_cases.case_id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    promoted_to_subject_id: Mapped[str | None] = mapped_column(
        "promoted_to_case_id",
        Text,
        ForeignKey("investment_cases.case_id", ondelete="SET NULL"),
        nullable=True,
    )
    triggered_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    selection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
