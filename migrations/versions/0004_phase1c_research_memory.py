"""Phase 1C research memory tables, search documents, and FTS5 index.

Revision ID: 0004_phase1c_research_memory
Revises: 0003_phase1d_instrument_provider
Create Date: 2026-07-17

Creates seven business tables, four search document tables, one FTS5 virtual
table with external-content INSERT/DELETE/UPDATE triggers, indexes, checks,
FKs, and unique constraints. Writes schema_versions.phase1c_research_memory.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0004_phase1c_research_memory"
down_revision: str | Sequence[str] | None = "0003_phase1d_instrument_provider"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PHASE1C_VERSION = "phase1c_research_memory"
_PHASE1C_DESCRIPTION = (
    "Phase 1C research memory: evidence, assessments, reports, events, "
    "decisions, journal, search documents + FTS5"
)

_HEX64 = (
    "length({col}) = 64 AND {col} = lower({col}) "
    "AND {col} NOT GLOB '*[^0-9a-f]*'"
)

_EVIDENCE_TYPE_IN = (
    "'market_snapshot','fundamental_snapshot','financial_statement',"
    "'company_action','company_news','global_news','research_report',"
    "'technical_signal','sentiment','macro','account_snapshot',"
    "'portfolio_snapshot','user_observation',"
    "'a_share_announcement','a_share_interactive_qa','a_share_analyst_report',"
    "'a_share_consensus_estimate','a_share_capital_flow',"
    "'a_share_northbound_flow','a_share_chip_distribution',"
    "'a_share_dragon_tiger','a_share_margin_financing','a_share_block_trade',"
    "'a_share_shareholder_count','a_share_unlock','a_share_dividend',"
    "'a_share_order_book','a_share_tick','a_share_limit_ecology',"
    "'a_share_market_heat','a_share_concept_heat','a_share_option_snapshot',"
    "'sec_filing','sec_company_fact','us_insider_activity','us_10b5_1',"
    "'us_pre_post_market','us_news_sentiment','fred_macro',"
    "'stocktwits_sentiment','reddit_sentiment','prediction_market',"
    "'correction'"
)

_FTS_PROBE_TABLE = "__tp_fts5_capability_probe__"


def _require_fts5_support() -> None:
    """Fail closed if FTS5 is unavailable; never leave probe objects behind."""
    bind = op.get_bind()
    compile_ok = False
    with suppress(Exception):
        compile_ok = (
            bind.execute(
                sa.text("SELECT sqlite_compileoption_used('ENABLE_FTS5')")
            ).scalar()
            == 1
        )

    if compile_ok:
        return

    try:
        bind.execute(
            sa.text(f"CREATE VIRTUAL TABLE {_FTS_PROBE_TABLE} USING fts5(x)")
        )
        bind.execute(sa.text(f"DROP TABLE {_FTS_PROBE_TABLE}"))
    except Exception as exc:
        with suppress(Exception):
            bind.execute(sa.text(f"DROP TABLE IF EXISTS {_FTS_PROBE_TABLE}"))
        msg = (
            "SQLite FTS5 is required for Phase 1C research_search_fts "
            "(ENABLE_FTS5 not available)"
        )
        raise RuntimeError(msg) from exc


def upgrade() -> None:
    # --- research_evidence ---
    op.create_table(
        "research_evidence",
        sa.Column("evidence_id", sa.Text(), nullable=False),
        sa.Column("evidence_type", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("structured_data_json", sa.Text(), nullable=True),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("source_vendor", sa.Text(), nullable=True),
        sa.Column("source_record_id", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.Text(), nullable=True),
        sa.Column("observed_at", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.Text(), nullable=True),
        sa.Column("effective_to", sa.Text(), nullable=True),
        sa.Column(
            "instrument_ids_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "topic_tags_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("quality", sa.Text(), nullable=False),
        sa.Column("reliability", sa.Text(), nullable=False),
        sa.Column("confidence_decimal", sa.Text(), nullable=True),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("supersedes_evidence_id", sa.Text(), nullable=True),
        sa.Column("recorded_by", sa.Text(), nullable=False),
        sa.Column(
            "schema_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.PrimaryKeyConstraint("evidence_id", name="pk_research_evidence"),
        sa.ForeignKeyConstraint(
            ["supersedes_evidence_id"],
            ["research_evidence.evidence_id"],
            name="fk_research_evidence_supersedes",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "content_sha256", name="uq_research_evidence_content_sha256"
        ),
        sa.CheckConstraint(
            f"evidence_type IN ({_EVIDENCE_TYPE_IN})",
            name="type",
        ),
        sa.CheckConstraint(
            "origin IN ('external_fact','user_observation','system_derived')",
            name="origin",
        ),
        sa.CheckConstraint(
            "quality IN ('primary','secondary','tertiary','unverified')",
            name="quality",
        ),
        sa.CheckConstraint(
            "reliability IN ('high','medium','low','unknown')",
            name="reliability",
        ),
        sa.CheckConstraint(
            "schema_version = 1",
            name="schema_version",
        ),
        sa.CheckConstraint(
            "confidence_decimal IS NULL OR ("
            "CAST(confidence_decimal AS REAL) >= 0 "
            "AND CAST(confidence_decimal AS REAL) <= 1)",
            name="confidence",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_from IS NULL "
            "OR effective_to >= effective_from",
            name="effective_order",
        ),
        sa.CheckConstraint(
            "evidence_type != 'correction' OR supersedes_evidence_id IS NOT NULL",
            name="correction_supersedes",
        ),
        sa.CheckConstraint(
            _HEX64.format(col="content_sha256"),
            name="content_sha256",
        ),
    )
    op.create_index(
        "ix_evidence_observed_at", "research_evidence", ["observed_at"]
    )
    op.create_index("ix_evidence_type", "research_evidence", ["evidence_type"])

    # --- case_evidence_links ---
    op.create_table(
        "case_evidence_links",
        sa.Column("link_id", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("evidence_id", sa.Text(), nullable=False),
        sa.Column("linked_at", sa.Text(), nullable=False),
        sa.Column("linked_by", sa.Text(), nullable=False),
        sa.Column(
            "schema_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.PrimaryKeyConstraint("link_id", name="pk_case_evidence_links"),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["investment_cases.case_id"],
            name="fk_case_evidence_links_case",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["research_evidence.evidence_id"],
            name="fk_case_evidence_links_evidence",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "case_id",
            "evidence_id",
            name="uq_case_evidence_links_case_evidence",
        ),
        sa.CheckConstraint(
            "schema_version = 1",
            name="schema_version",
        ),
    )
    op.create_index(
        "ix_links_case_linked_at",
        "case_evidence_links",
        ["case_id", "linked_at"],
    )

    # --- evidence_assessments ---
    op.create_table(
        "evidence_assessments",
        sa.Column("assessment_id", sa.Text(), nullable=False),
        sa.Column("evidence_id", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("thesis_id", sa.Text(), nullable=True),
        sa.Column("thesis_revision_id", sa.Text(), nullable=True),
        sa.Column("stance", sa.Text(), nullable=False),
        sa.Column("materiality_decimal", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("assessed_at", sa.Text(), nullable=False),
        sa.Column("assessed_by", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.Text(), nullable=False),
        sa.Column(
            "schema_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.PrimaryKeyConstraint("assessment_id", name="pk_evidence_assessments"),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["research_evidence.evidence_id"],
            name="fk_evidence_assessments_evidence",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["investment_cases.case_id"],
            name="fk_evidence_assessments_case",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["thesis_id"],
            ["theses.thesis_id"],
            name="fk_evidence_assessments_thesis",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["thesis_revision_id"],
            ["thesis_revisions.revision_id"],
            name="fk_evidence_assessments_thesis_revision",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "stance IN ('supports','contradicts','neutral','uncertain')",
            name="stance",
        ),
        sa.CheckConstraint(
            "schema_version = 1",
            name="schema_version",
        ),
        sa.CheckConstraint(
            "CAST(materiality_decimal AS REAL) >= 0 "
            "AND CAST(materiality_decimal AS REAL) <= 1",
            name="materiality",
        ),
    )
    op.create_index(
        "ix_assessment_case_stance",
        "evidence_assessments",
        ["case_id", "stance"],
    )
    op.create_index(
        "ix_assessment_thesis_stance",
        "evidence_assessments",
        ["thesis_id", "stance"],
    )
    op.create_index(
        "ix_assessments_evidence_assessed_at",
        "evidence_assessments",
        ["evidence_id", "assessed_at"],
    )

    # --- research_reports ---
    op.create_table(
        "research_reports",
        sa.Column("report_id", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("report_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("content_markdown", sa.Text(), nullable=False),
        sa.Column("as_of", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("research_run_id", sa.Text(), nullable=True),
        sa.Column(
            "evidence_ids_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "thesis_revision_ids_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("supersedes_report_id", sa.Text(), nullable=True),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column(
            "schema_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.PrimaryKeyConstraint("report_id", name="pk_research_reports"),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["investment_cases.case_id"],
            name="fk_research_reports_case",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_report_id"],
            ["research_reports.report_id"],
            name="fk_research_reports_supersedes",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "content_sha256", name="uq_research_reports_content_sha256"
        ),
        sa.CheckConstraint(
            "report_type IN ("
            "'deep_dive','catalyst_review','a_share_market_review',"
            "'us_market_review','portfolio_review','ad_hoc')",
            name="type",
        ),
        sa.CheckConstraint(
            "schema_version = 1",
            name="schema_version",
        ),
        sa.CheckConstraint(
            "as_of <= created_at",
            name="as_of_order",
        ),
        sa.CheckConstraint(
            _HEX64.format(col="content_sha256"),
            name="content_sha256",
        ),
    )
    op.create_index(
        "ix_reports_case_created_at",
        "research_reports",
        ["case_id", "created_at"],
    )
    op.create_index(
        "ix_reports_supersedes",
        "research_reports",
        ["supersedes_report_id"],
    )

    # --- research_events ---
    op.create_table(
        "research_events",
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.Text(), nullable=False),
        sa.Column("published_at", sa.Text(), nullable=True),
        sa.Column(
            "instrument_ids_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "evidence_ids_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "report_ids_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("related_entity_type", sa.Text(), nullable=True),
        sa.Column("related_entity_id", sa.Text(), nullable=True),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column(
            "schema_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_research_events"),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["investment_cases.case_id"],
            name="fk_research_events_case",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "event_type IN ("
            "'company','earnings','regulatory','corporate_action',"
            "'industry','macro','policy','market_structure',"
            "'capital_market','other')",
            name="type",
        ),
        sa.CheckConstraint(
            "schema_version = 1",
            name="schema_version",
        ),
        sa.CheckConstraint(
            "(related_entity_type IS NULL) = (related_entity_id IS NULL)",
            name="related_entity_pair",
        ),
    )
    op.create_index(
        "ix_events_case_occurred_at",
        "research_events",
        ["case_id", "occurred_at"],
    )
    op.create_index(
        "ix_events_case_recorded_at",
        "research_events",
        ["case_id", "recorded_at"],
    )

    # --- decision_records ---
    op.create_table(
        "decision_records",
        sa.Column("decision_id", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("decision_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.Text(), nullable=False),
        sa.Column("confirmation_mode", sa.Text(), nullable=False),
        sa.Column("primary_instrument_id", sa.Text(), nullable=True),
        sa.Column(
            "thesis_revision_ids_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "evidence_ids_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "report_ids_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("supersedes_decision_id", sa.Text(), nullable=True),
        sa.Column("position_context_snapshot_id", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("idempotency_payload_sha256", sa.Text(), nullable=False),
        sa.Column(
            "schema_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.PrimaryKeyConstraint("decision_id", name="pk_decision_records"),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["investment_cases.case_id"],
            name="fk_decision_records_case",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_decision_id"],
            ["decision_records.decision_id"],
            name="fk_decision_records_supersedes",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_decision_records_idempotency_key"
        ),
        sa.CheckConstraint(
            "decision_type IN ("
            "'watch','no_action','initiate_intent','add_intent','hold',"
            "'reduce_intent','exit_intent','avoid','research_more')",
            name="type",
        ),
        sa.CheckConstraint(
            "confirmation_mode IN ('normal','strict_review')",
            name="confirmation_mode",
        ),
        sa.CheckConstraint(
            "schema_version = 1",
            name="schema_version",
        ),
        sa.CheckConstraint(
            "decided_at <= recorded_at",
            name="decided_order",
        ),
        sa.CheckConstraint(
            "("
            "decision_type IN ("
            "'initiate_intent','add_intent','hold',"
            "'reduce_intent','exit_intent','avoid'"
            ") AND confirmation_mode = 'strict_review'"
            ") OR ("
            "decision_type IN ('watch','no_action','research_more') "
            "AND confirmation_mode IN ('normal','strict_review')"
            ")",
            name="confirmation_matrix",
        ),
        sa.CheckConstraint(
            _HEX64.format(col="idempotency_payload_sha256"),
            name="idempotency_hash",
        ),
    )
    op.create_index(
        "ix_decisions_case_recorded_at",
        "decision_records",
        ["case_id", "recorded_at"],
    )
    op.create_index(
        "ix_decisions_supersedes",
        "decision_records",
        ["supersedes_decision_id"],
    )

    # --- journal_entries ---
    op.create_table(
        "journal_entries",
        sa.Column("journal_id", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=True),
        sa.Column("entry_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body_markdown", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("authored_by", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.Text(), nullable=False),
        sa.Column(
            "instrument_ids_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "topic_tags_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("related_entity_type", sa.Text(), nullable=True),
        sa.Column("related_entity_id", sa.Text(), nullable=True),
        sa.Column("supersedes_journal_id", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("idempotency_payload_sha256", sa.Text(), nullable=False),
        sa.Column(
            "schema_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.PrimaryKeyConstraint("journal_id", name="pk_journal_entries"),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["investment_cases.case_id"],
            name="fk_journal_entries_case",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_journal_id"],
            ["journal_entries.journal_id"],
            name="fk_journal_entries_supersedes",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_journal_entries_idempotency_key"
        ),
        sa.CheckConstraint(
            "entry_type IN ("
            "'note','observation','reflection','postmortem','question')",
            name="type",
        ),
        sa.CheckConstraint(
            "schema_version = 1",
            name="schema_version",
        ),
        sa.CheckConstraint(
            "(related_entity_type IS NULL) = (related_entity_id IS NULL)",
            name="related_entity_pair",
        ),
        sa.CheckConstraint(
            _HEX64.format(col="idempotency_payload_sha256"),
            name="idempotency_hash",
        ),
    )
    op.create_index(
        "ix_journal_case_created_at",
        "journal_entries",
        ["case_id", "created_at"],
    )
    op.create_index(
        "ix_journal_supersedes",
        "journal_entries",
        ["supersedes_journal_id"],
    )

    # --- search documents (no ORM rows in C2a) ---
    op.create_table(
        "research_search_documents",
        sa.Column("rowid", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("instrument_ids_text", sa.Text(), nullable=False),
        sa.Column("topic_tags", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("visible_at", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.Text(), nullable=True),
        sa.Column("superseded_by_id", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("rowid", name="pk_research_search_documents"),
        sa.UniqueConstraint(
            "entity_id", name="uq_research_search_documents_entity_id"
        ),
        sa.CheckConstraint(
            "entity_type IN ("
            "'evidence','report','event','decision','journal')",
            name="entity_type",
        ),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_search_documents_visible_at",
        "research_search_documents",
        ["visible_at"],
    )

    op.create_table(
        "research_search_document_cases",
        sa.Column("document_rowid", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("membership_visible_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "document_rowid",
            "case_id",
            name="pk_research_search_document_cases",
        ),
        sa.ForeignKeyConstraint(
            ["document_rowid"],
            ["research_search_documents.rowid"],
            name="fk_search_document_cases_document",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["investment_cases.case_id"],
            name="fk_search_document_cases_case",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_search_document_cases_case",
        "research_search_document_cases",
        ["case_id"],
    )

    op.create_table(
        "research_search_document_instruments",
        sa.Column("document_rowid", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "document_rowid",
            "instrument_id",
            name="pk_research_search_document_instruments",
        ),
        sa.ForeignKeyConstraint(
            ["document_rowid"],
            ["research_search_documents.rowid"],
            name="fk_search_document_instruments_document",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_search_document_instruments_instrument",
        "research_search_document_instruments",
        ["instrument_id"],
    )

    op.create_table(
        "research_search_document_tags",
        sa.Column("document_rowid", sa.Integer(), nullable=False),
        sa.Column("topic_tag", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "document_rowid",
            "topic_tag",
            name="pk_research_search_document_tags",
        ),
        sa.ForeignKeyConstraint(
            ["document_rowid"],
            ["research_search_documents.rowid"],
            name="fk_search_document_tags_document",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_search_document_tags_tag",
        "research_search_document_tags",
        ["topic_tag"],
    )

    # --- FTS5 virtual table + external-content triggers ---
    _require_fts5_support()
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            CREATE VIRTUAL TABLE research_search_fts USING fts5(
                title,
                body,
                topic_tags,
                content='research_search_documents',
                content_rowid='rowid',
                tokenize='unicode61'
            )
            """
        )
    )
    bind.execute(
        sa.text(
            """
            CREATE TRIGGER research_search_documents_ai
            AFTER INSERT ON research_search_documents BEGIN
                INSERT INTO research_search_fts(rowid, title, body, topic_tags)
                VALUES (new.rowid, new.title, new.body, new.topic_tags);
            END
            """
        )
    )
    bind.execute(
        sa.text(
            """
            CREATE TRIGGER research_search_documents_ad
            AFTER DELETE ON research_search_documents BEGIN
                INSERT INTO research_search_fts(
                    research_search_fts, rowid, title, body, topic_tags
                )
                VALUES (
                    'delete', old.rowid, old.title, old.body, old.topic_tags
                );
            END
            """
        )
    )
    bind.execute(
        sa.text(
            """
            CREATE TRIGGER research_search_documents_au
            AFTER UPDATE ON research_search_documents BEGIN
                INSERT INTO research_search_fts(
                    research_search_fts, rowid, title, body, topic_tags
                )
                VALUES (
                    'delete', old.rowid, old.title, old.body, old.topic_tags
                );
                INSERT INTO research_search_fts(rowid, title, body, topic_tags)
                VALUES (new.rowid, new.title, new.body, new.topic_tags);
            END
            """
        )
    )

    schema_versions = sa.table(
        "schema_versions",
        sa.column("version", sa.Text()),
        sa.column("applied_at", sa.Text()),
        sa.column("description", sa.Text()),
    )
    op.execute(
        schema_versions.insert().values(
            version=_PHASE1C_VERSION,
            applied_at=datetime.now(UTC).isoformat(),
            description=_PHASE1C_DESCRIPTION,
        )
    )


def downgrade() -> None:
    # Design §17.2 / §11: drop FTS triggers / virtual table / mapping / document
    # first, then business tables in FK reverse order, then schema_versions row.
    bind = op.get_bind()
    bind.execute(sa.text("DROP TRIGGER IF EXISTS research_search_documents_au"))
    bind.execute(sa.text("DROP TRIGGER IF EXISTS research_search_documents_ad"))
    bind.execute(sa.text("DROP TRIGGER IF EXISTS research_search_documents_ai"))
    bind.execute(sa.text("DROP TABLE IF EXISTS research_search_fts"))

    op.drop_index(
        "ix_search_document_tags_tag",
        table_name="research_search_document_tags",
    )
    op.drop_table("research_search_document_tags")

    op.drop_index(
        "ix_search_document_instruments_instrument",
        table_name="research_search_document_instruments",
    )
    op.drop_table("research_search_document_instruments")

    op.drop_index(
        "ix_search_document_cases_case",
        table_name="research_search_document_cases",
    )
    op.drop_table("research_search_document_cases")

    op.drop_index(
        "ix_search_documents_visible_at",
        table_name="research_search_documents",
    )
    op.drop_table("research_search_documents")

    op.drop_index("ix_journal_supersedes", table_name="journal_entries")
    op.drop_index("ix_journal_case_created_at", table_name="journal_entries")
    op.drop_table("journal_entries")

    op.drop_index("ix_decisions_supersedes", table_name="decision_records")
    op.drop_index(
        "ix_decisions_case_recorded_at", table_name="decision_records"
    )
    op.drop_table("decision_records")

    op.drop_index("ix_events_case_recorded_at", table_name="research_events")
    op.drop_index("ix_events_case_occurred_at", table_name="research_events")
    op.drop_table("research_events")

    op.drop_index("ix_reports_supersedes", table_name="research_reports")
    op.drop_index("ix_reports_case_created_at", table_name="research_reports")
    op.drop_table("research_reports")

    op.drop_index(
        "ix_assessments_evidence_assessed_at",
        table_name="evidence_assessments",
    )
    op.drop_index(
        "ix_assessment_thesis_stance", table_name="evidence_assessments"
    )
    op.drop_index(
        "ix_assessment_case_stance", table_name="evidence_assessments"
    )
    op.drop_table("evidence_assessments")

    op.drop_index("ix_links_case_linked_at", table_name="case_evidence_links")
    op.drop_table("case_evidence_links")

    op.drop_index("ix_evidence_type", table_name="research_evidence")
    op.drop_index("ix_evidence_observed_at", table_name="research_evidence")
    op.drop_table("research_evidence")

    schema_versions = sa.table(
        "schema_versions",
        sa.column("version", sa.Text()),
    )
    op.execute(
        schema_versions.delete().where(
            schema_versions.c.version == _PHASE1C_VERSION
        )
    )
