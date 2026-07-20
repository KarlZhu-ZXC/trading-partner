"""Phase 2 watchlist hub source/group/membership mutation tables.

Revision ID: 0009_phase2_watchlist_hub
Revises: 0011_reddit_rss_resilience
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_phase2_watchlist_hub"
down_revision: str | Sequence[str] | None = "0011_reddit_rss_resilience"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watchlist_groups",
        sa.Column("group_id", sa.Text(), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_group_key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("group_type", sa.Text(), nullable=False),
        sa.Column("writable", sa.Integer(), nullable=False),
        sa.Column("active", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.Text(), nullable=False),
        sa.Column("removed_at", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "source",
            "source_group_key",
            name="uq_watchlist_group_source_key",
        ),
        sa.CheckConstraint(
            "source IN ('MOOMOO','MANUAL_CSV')",
            name="ck_watchlist_groups_source",
        ),
        sa.CheckConstraint(
            "group_type IN ('SYSTEM','CUSTOM','MANUAL')",
            name="ck_watchlist_groups_type",
        ),
        sa.CheckConstraint("writable IN (0, 1)", name="ck_watchlist_groups_writable"),
        sa.CheckConstraint("active IN (0, 1)", name="ck_watchlist_groups_active_bool"),
        sa.CheckConstraint(
            "(active = 1) = (removed_at IS NULL)",
            name="ck_watchlist_groups_active",
        ),
        sa.CheckConstraint(
            "last_seen_at >= first_seen_at",
            name="ck_watchlist_groups_seen_order",
        ),
        sa.CheckConstraint(
            "last_synced_at >= last_seen_at",
            name="ck_watchlist_groups_synced_after_seen",
        ),
        sa.CheckConstraint(
            "removed_at IS NULL OR removed_at >= last_seen_at",
            name="ck_watchlist_groups_removed_after_seen",
        ),
    )
    op.create_index("ix_watchlist_groups_source", "watchlist_groups", ["source"])
    op.create_index(
        "ix_watchlist_groups_source_active",
        "watchlist_groups",
        ["source", "active"],
    )
    op.create_index(
        "ix_watchlist_groups_last_synced",
        "watchlist_groups",
        ["last_synced_at"],
    )

    op.create_table(
        "watchlist_memberships",
        sa.Column("membership_id", sa.Text(), primary_key=True),
        sa.Column("group_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("provider_code", sa.Text(), nullable=False),
        sa.Column("instrument_id", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("provider_asset_type", sa.Text(), nullable=True),
        sa.Column("research_supported", sa.Integer(), nullable=False),
        sa.Column("active", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.Text(), nullable=False),
        sa.Column("removed_at", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["watchlist_groups.group_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "group_id",
            "provider_code",
            name="uq_watchlist_memberships_group_code",
        ),
        sa.CheckConstraint(
            "source IN ('MOOMOO','MANUAL_CSV')",
            name="ck_watchlist_memberships_source",
        ),
        sa.CheckConstraint(
            "active IN (0, 1)",
            name="ck_watchlist_memberships_active_bool",
        ),
        sa.CheckConstraint(
            "research_supported IN (0, 1)",
            name="ck_watchlist_memberships_research_bool",
        ),
        sa.CheckConstraint(
            "(active = 1) = (removed_at IS NULL)",
            name="ck_watchlist_memberships_active",
        ),
        sa.CheckConstraint(
            "(research_supported = 1 AND instrument_id IS NOT NULL) OR (research_supported = 0)",
            name="ck_watchlist_memberships_research_instrument",
        ),
        sa.CheckConstraint(
            "last_seen_at >= first_seen_at",
            name="ck_watchlist_memberships_seen_order",
        ),
        sa.CheckConstraint(
            "last_synced_at >= last_seen_at",
            name="ck_watchlist_memberships_synced_after_seen",
        ),
        sa.CheckConstraint(
            "removed_at IS NULL OR removed_at >= last_seen_at",
            name="ck_watchlist_memberships_removed_after_seen",
        ),
    )
    op.create_index(
        "ix_watchlist_memberships_group",
        "watchlist_memberships",
        ["group_id"],
    )
    op.create_index(
        "ix_watchlist_memberships_group_active",
        "watchlist_memberships",
        ["group_id", "active"],
    )
    op.create_index(
        "ix_watchlist_memberships_provider_code",
        "watchlist_memberships",
        ["provider_code"],
    )

    op.create_table(
        "watchlist_mutations",
        sa.Column("mutation_id", sa.Text(), primary_key=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("group_name", sa.Text(), nullable=False),
        sa.Column("provider_code", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_watchlist_mutations_idempotency_key",
        ),
        sa.CheckConstraint(
            "source IN ('MOOMOO','MANUAL_CSV')",
            name="ck_watchlist_mutations_source",
        ),
        sa.CheckConstraint(
            "action IN ('ADD','REMOVE')",
            name="ck_watchlist_mutations_action",
        ),
        sa.CheckConstraint(
            "requested_by IN ('user','external_agent')",
            name="ck_watchlist_mutations_requested_by",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','SUCCEEDED','PARTIAL','FAILED')",
            name="ck_watchlist_mutations_status",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= requested_at",
            name="ck_watchlist_mutations_completed_after_requested",
        ),
        sa.CheckConstraint(
            "(((status = 'PENDING') AND completed_at IS NULL AND error_code IS NULL)"
            " OR ((status = 'SUCCEEDED') AND completed_at IS NOT NULL AND error_code IS NULL)"
            " OR ((status IN ('PARTIAL','FAILED')) AND completed_at IS NOT NULL"
            " AND error_code IS NOT NULL))",
            name="ck_watchlist_mutations_status_state",
        ),
    )
    op.create_index(
        "ix_watchlist_mutations_status", "watchlist_mutations", ["status"]
    )
    op.create_index(
        "ix_watchlist_mutations_requested",
        "watchlist_mutations",
        ["requested_at"],
    )

    op.execute(
        """
        INSERT INTO schema_versions(version, applied_at, description)
        VALUES ('0009_phase2_watchlist_hub', '2026-07-18T00:00:00+00:00',
        'Phase 2 watchlist hub tables: groups, memberships, and mutations')
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM schema_versions WHERE version = '0009_phase2_watchlist_hub'")
    op.drop_index("ix_watchlist_mutations_requested", table_name="watchlist_mutations")
    op.drop_index("ix_watchlist_mutations_status", table_name="watchlist_mutations")
    op.drop_table("watchlist_mutations")
    op.drop_index(
        "ix_watchlist_memberships_provider_code",
        table_name="watchlist_memberships",
    )
    op.drop_index(
        "ix_watchlist_memberships_group_active",
        table_name="watchlist_memberships",
    )
    op.drop_index(
        "ix_watchlist_memberships_group",
        table_name="watchlist_memberships",
    )
    op.drop_table("watchlist_memberships")
    op.drop_index("ix_watchlist_groups_last_synced", table_name="watchlist_groups")
    op.drop_index("ix_watchlist_groups_source_active", table_name="watchlist_groups")
    op.drop_index("ix_watchlist_groups_source", table_name="watchlist_groups")
    op.drop_table("watchlist_groups")
