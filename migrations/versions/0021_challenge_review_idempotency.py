"""Challenge Review start/resolution idempotency.

Revision ID: 0021_challenge_review_idempotency
Revises: 0020_phase3d_plan_controls
Create Date: 2026-07-26
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_challenge_review_idempotency"
down_revision: str | Sequence[str] | None = "0020_phase3d_plan_controls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HEX64_CHECK = (
    "length({col}) = 64 AND {col} = lower({col}) "
    "AND {col} NOT GLOB '*[^0-9a-f]*'"
)


def _sha(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upgrade() -> None:
    with op.batch_alter_table("challenge_reviews") as batch:
        batch.add_column(sa.Column("start_idempotency_key", sa.Text()))
        batch.add_column(sa.Column("start_payload_sha256", sa.Text()))
        batch.create_unique_constraint(
            "uq_challenge_reviews_start_idempotency_key",
            ["start_idempotency_key"],
        )
        batch.create_check_constraint(
            "ck_challenge_reviews_start_idempotency",
            "(start_idempotency_key IS NULL AND start_payload_sha256 IS NULL) OR "
            "(start_idempotency_key IS NOT NULL AND "
            f"{_HEX64_CHECK.format(col='start_payload_sha256')})",
        )
    op.create_table(
        "challenge_review_resolutions",
        sa.Column("resolution_id", sa.Text(), primary_key=True),
        sa.Column(
            "review_id",
            sa.Text(),
            sa.ForeignKey("challenge_reviews.review_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("payload_sha256", sa.Text(), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.Text(), nullable=False),
        sa.Column("resolved_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("review_id", name="uq_challenge_review_resolutions_review"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_challenge_review_resolutions_idempotency_key",
        ),
        sa.CheckConstraint(
            "resolution IN ('accept','revise','reject','defer')",
            name="ck_challenge_review_resolutions_resolution",
        ),
        sa.CheckConstraint(
            "confirmed_by IN ('user','external_agent')",
            name="ck_challenge_review_resolutions_confirmed_by",
        ),
        sa.CheckConstraint(
            _HEX64_CHECK.format(col="payload_sha256"),
            name="ck_challenge_review_resolutions_payload_sha256",
        ),
    )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT review_id, resolution, resolution_rationale, confirmed_by, resolved_at "
            "FROM challenge_reviews WHERE status = 'resolved'"
        )
    ).mappings()
    for row in rows:
        payload = {
            "review_id": row["review_id"],
            "resolution": row["resolution"],
            "rationale": row["resolution_rationale"],
            "confirmed_by": row["confirmed_by"],
        }
        connection.execute(
            sa.text(
                "INSERT INTO challenge_review_resolutions "
                "(resolution_id, review_id, idempotency_key, payload_sha256, resolution, "
                "rationale, confirmed_by, resolved_at) VALUES "
                "(:resolution_id, :review_id, :idempotency_key, :payload_sha256, "
                ":resolution, :rationale, :confirmed_by, :resolved_at)"
            ),
            {
                "resolution_id": f"legacy_resolution_{row['review_id']}",
                "review_id": row["review_id"],
                "idempotency_key": f"legacy-resolution-{row['review_id']}",
                "payload_sha256": _sha(payload),
                "resolution": row["resolution"],
                "rationale": row["resolution_rationale"],
                "confirmed_by": row["confirmed_by"],
                "resolved_at": row["resolved_at"],
            },
        )

    op.execute(
        sa.text(
            "INSERT INTO schema_versions(version, applied_at, description) VALUES "
            "('0021_challenge_review_idempotency', '2026-07-26T00:00:00+00:00', "
            "'Challenge Review start and resolution idempotency')"
        )
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM schema_versions WHERE version = '0021_challenge_review_idempotency'"
    )
    op.drop_table("challenge_review_resolutions")
    with op.batch_alter_table("challenge_reviews") as batch:
        batch.drop_constraint(
            "ck_challenge_reviews_start_idempotency", type_="check"
        )
        batch.drop_constraint(
            "uq_challenge_reviews_start_idempotency_key", type_="unique"
        )
        batch.drop_column("start_payload_sha256")
        batch.drop_column("start_idempotency_key")
