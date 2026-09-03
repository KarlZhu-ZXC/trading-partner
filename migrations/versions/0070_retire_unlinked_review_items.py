"""Retire Unlinked Activity as a Review Queue source."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0070_retire_unlinked_review_items"
down_revision: str | None = "0069_journal_instrument_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(
        "UPDATE review_item_occurrences SET "
        f"resolved_at=COALESCE(resolved_at,{_NOW}), "
        "resolved_by=COALESCE(resolved_by,'system'), resolution_mode='AUTO' "
        "WHERE resolved_at IS NULL AND review_item_id IN ("
        "SELECT review_item_id FROM review_items "
        "WHERE source_type='UNLINKED_ACTIVITY' "
        "AND status IN ('OPEN','ACKNOWLEDGED'))"
    ))
    connection.execute(sa.text(
        "UPDATE review_items SET status='AUTO_RESOLVED', active_at_source=0, "
        f"resolved_at=COALESCE(resolved_at,{_NOW}), "
        "resolved_by=COALESCE(resolved_by,'system'), "
        "resolution_note=COALESCE(resolution_note,"
        "'Unlinked Activity retired from the Review Queue.'), "
        "resolution_ref=COALESCE(resolution_ref,"
        "'policy:unlinked-activity-not-review-queue'), version=version+1 "
        "WHERE source_type='UNLINKED_ACTIVITY' "
        "AND status IN ('OPEN','ACKNOWLEDGED')"
    ))


def downgrade() -> None:
    # Policy retirement is intentionally irreversible. Reopening historical
    # queue items would reintroduce work the product no longer materializes.
    pass
