"""SQLAlchemy Core research search index (Phase 1C C3).

Projection tables are not ORM-mapped. Writes use parameterized Core SQL.
Business rows remain append-only; only the rebuildable search projection mutates.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from application.dto.research_memory import (
    ResearchSearchHitDTO,
    ResearchSearchPageDTO,
    ResearchSearchQuery,
)
from domain.common.enums import EvidenceStance, ResearchSearchEntityType
from domain.common.errors import ResearchMemoryNotFound, SearchBackendUnavailable
from infrastructure.persistence.models import (
    CaseEvidenceLinkRow,
    DecisionRecordRow,
    JournalEntryRow,
    ResearchEventRow,
    ResearchEvidenceRow,
    ResearchReportRow,
)
from infrastructure.persistence.repositories._mapping import dt_to_db
from infrastructure.persistence.repositories._research_search_normalization import (
    build_fts_match_query,
)
from infrastructure.persistence.repositories._research_search_projection import (
    SearchDocumentProjection,
    evidence_occurred_at,
    instrument_ids_text,
    project_decision,
    project_event,
    project_evidence,
    project_journal,
    project_report,
    stable_instrument_union,
)
from infrastructure.persistence.repositories.case_evidence_link import (
    _to_domain as link_to_domain,
)
from infrastructure.persistence.repositories.decision_record import (
    _to_domain as decision_to_domain,
)
from infrastructure.persistence.repositories.evidence import (
    _to_domain as evidence_to_domain,
)
from infrastructure.persistence.repositories.journal import (
    _to_domain as journal_to_domain,
)
from infrastructure.persistence.repositories.research_event import (
    _to_domain as event_to_domain,
)
from infrastructure.persistence.repositories.research_report import (
    _to_domain as report_to_domain,
)

_SNIPPET_MAX = 800

_ALL_ENTITY_TYPES: tuple[ResearchSearchEntityType, ...] = (
    ResearchSearchEntityType.EVIDENCE,
    ResearchSearchEntityType.REPORT,
    ResearchSearchEntityType.EVENT,
    ResearchSearchEntityType.DECISION,
    ResearchSearchEntityType.JOURNAL,
)

_THESIS_DEFAULT_ENTITY_TYPES: tuple[ResearchSearchEntityType, ...] = (
    ResearchSearchEntityType.EVIDENCE,
    ResearchSearchEntityType.REPORT,
    ResearchSearchEntityType.DECISION,
)

_SUCCESSOR_LOOKUP: dict[str, tuple[str, str, str]] = {
    ResearchSearchEntityType.EVIDENCE.value: (
        "research_evidence",
        "evidence_id",
        "supersedes_evidence_id",
    ),
    ResearchSearchEntityType.REPORT.value: (
        "research_reports",
        "report_id",
        "supersedes_report_id",
    ),
    ResearchSearchEntityType.DECISION.value: (
        "decision_records",
        "decision_id",
        "supersedes_decision_id",
    ),
    ResearchSearchEntityType.JOURNAL.value: (
        "journal_entries",
        "journal_id",
        "supersedes_journal_id",
    ),
}

_REBUILD_ORDER: tuple[tuple[ResearchSearchEntityType, type, str], ...] = (
    (ResearchSearchEntityType.DECISION, DecisionRecordRow, "decision_id"),
    (ResearchSearchEntityType.EVENT, ResearchEventRow, "event_id"),
    (ResearchSearchEntityType.EVIDENCE, ResearchEvidenceRow, "evidence_id"),
    (ResearchSearchEntityType.JOURNAL, JournalEntryRow, "journal_id"),
    (ResearchSearchEntityType.REPORT, ResearchReportRow, "report_id"),
)


def _wire(value: object) -> str:
    if isinstance(value, str):
        return value
    return str(getattr(value, "value", value))


def _truncate_snippet(value: str) -> str:
    if len(value) <= _SNIPPET_MAX:
        return value
    return value[:_SNIPPET_MAX]


def _backend_unavailable(message: str, error: BaseException) -> SearchBackendUnavailable:
    return SearchBackendUnavailable(
        message,
        details={
            "component": "research_search",
            "error_type": type(error).__name__,
        },
    )


def _is_sqlite_backend_error(exc: BaseException) -> bool:
    if isinstance(exc, SQLAlchemyError):
        return True
    module = type(exc).__module__
    return module == "sqlite3" or module.startswith("sqlite3.")


class SqlAlchemyResearchSearchIndex:
    """Session-bound FTS + structured research search projection."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def index(
        self,
        entity_type: ResearchSearchEntityType,
        entity_id: str,
    ) -> None:
        entity_wire = _wire(entity_type)
        if entity_wire not in {item.value for item in _ALL_ENTITY_TYPES}:
            raise ResearchMemoryNotFound(
                "unknown research search entity type",
                details={"entity_type": entity_wire, "entity_id": entity_id},
            )
        projection = self._build_projection(entity_wire, entity_id)
        self._upsert_projection(projection)
        if projection.supersedes_entity_id is not None:
            self._mark_predecessor_superseded(
                predecessor_id=projection.supersedes_entity_id,
                successor_id=projection.entity_id,
            )

    def refresh_evidence_membership(self, evidence_id: str) -> None:
        if self._session.get(ResearchEvidenceRow, evidence_id) is None:
            raise ResearchMemoryNotFound(
                "evidence not found",
                details={
                    "entity_type": ResearchSearchEntityType.EVIDENCE.value,
                    "entity_id": evidence_id,
                },
            )
        doc = self._session.execute(
            text(
                "SELECT rowid FROM research_search_documents "
                "WHERE entity_id = :entity_id"
            ),
            {"entity_id": evidence_id},
        ).first()
        if doc is None:
            raise ResearchMemoryNotFound(
                "search document not found for evidence",
                details={
                    "entity_type": ResearchSearchEntityType.EVIDENCE.value,
                    "entity_id": evidence_id,
                },
            )
        rowid = int(doc[0])
        self._session.execute(
            text(
                "DELETE FROM research_search_document_cases "
                "WHERE document_rowid = :rowid"
            ),
            {"rowid": rowid},
        )
        for link in self._load_evidence_links(evidence_id):
            self._session.execute(
                text(
                    "INSERT INTO research_search_document_cases("
                    "document_rowid, case_id, membership_visible_at"
                    ") VALUES (:rowid, :case_id, :membership_visible_at)"
                ),
                {
                    "rowid": rowid,
                    "case_id": link.case_id,
                    "membership_visible_at": dt_to_db(link.linked_at),
                },
            )
        self._session.flush()

    def search(self, query: ResearchSearchQuery) -> ResearchSearchPageDTO:
        effective_types = self._effective_entity_types(query)
        effective_wires = {_wire(item) for item in effective_types}
        if (
            query.evidence_types
            and ResearchSearchEntityType.EVIDENCE.value not in effective_wires
        ):
            return ResearchSearchPageDTO(
                items=(),
                total=0,
                limit=query.limit,
                offset=query.offset,
                has_more=False,
            )
        if (
            query.journal_entry_types
            and ResearchSearchEntityType.JOURNAL.value not in effective_wires
        ):
            return ResearchSearchPageDTO(
                items=(),
                total=0,
                limit=query.limit,
                offset=query.offset,
                has_more=False,
            )

        match_expr: str | None = None
        if query.text is not None:
            match_expr = build_fts_match_query(query.text)

        where_sql, params = self._build_filters(
            query=query,
            effective_types=effective_types,
            match_expr=match_expr,
        )

        try:
            if match_expr is not None:
                count_sql = (
                    "SELECT COUNT(*) FROM research_search_documents d "
                    "JOIN research_search_fts fts ON fts.rowid = d.rowid "
                    f"WHERE {where_sql}"
                )
                select_sql = (
                    "SELECT d.entity_type, d.entity_id, d.visible_at, "
                    "d.occurred_at, bm25(research_search_fts) AS score "
                    "FROM research_search_documents d "
                    "JOIN research_search_fts fts ON fts.rowid = d.rowid "
                    f"WHERE {where_sql} "
                    "ORDER BY score ASC, d.visible_at DESC, d.entity_id ASC "
                    "LIMIT :limit OFFSET :offset"
                )
            else:
                count_sql = (
                    "SELECT COUNT(*) FROM research_search_documents d "
                    f"WHERE {where_sql}"
                )
                select_sql = (
                    "SELECT d.entity_type, d.entity_id, d.visible_at, "
                    "d.occurred_at, NULL AS score "
                    "FROM research_search_documents d "
                    f"WHERE {where_sql} "
                    "ORDER BY d.visible_at DESC, d.entity_id ASC "
                    "LIMIT :limit OFFSET :offset"
                )

            page_params = {**params, "limit": query.limit, "offset": query.offset}
            total = int(self._session.execute(text(count_sql), params).scalar_one())
            rows = self._session.execute(text(select_sql), page_params).all()
        except Exception as exc:
            if _is_sqlite_backend_error(exc):
                raise _backend_unavailable(
                    "research search backend unavailable",
                    exc,
                ) from None
            raise

        hits: list[ResearchSearchHitDTO] = []
        for row in rows:
            score_raw = row[4]
            score: Decimal | None
            if score_raw is None:
                score = None
            else:
                score = Decimal(str(score_raw))
                if not score.is_finite():
                    raise _backend_unavailable(
                        "research search returned non-finite score",
                        ValueError("non-finite bm25"),
                    )
            hits.append(
                self._hydrate_hit(
                    entity_type=str(row[0]),
                    entity_id=str(row[1]),
                    score=score,
                    query=query,
                )
            )

        items = tuple(hits)
        return ResearchSearchPageDTO(
            items=items,
            total=total,
            limit=query.limit,
            offset=query.offset,
            has_more=query.offset + len(items) < total,
        )

    def rebuild(self) -> int:
        # Heal FTS drift first so content-row DELETE triggers can safely issue
        # external-content 'delete' commands (delete-all alone leaves AD unsafe).
        self._fts_command("rebuild")

        self._session.execute(text("DELETE FROM research_search_document_cases"))
        self._session.execute(text("DELETE FROM research_search_document_instruments"))
        self._session.execute(text("DELETE FROM research_search_document_tags"))
        self._session.execute(text("DELETE FROM research_search_documents"))
        self._session.flush()

        count = 0
        for entity_type, model, id_attr in _REBUILD_ORDER:
            id_column = getattr(model, id_attr)
            entity_ids = self._session.scalars(
                select(id_column).order_by(id_column.asc())
            ).all()
            for entity_id in entity_ids:
                self.index(entity_type, str(entity_id))
                count += 1

        self._fts_command("rebuild")
        self._session.flush()
        return count

    def _fts_command(self, command: str) -> None:
        try:
            self._session.execute(
                text(
                    "INSERT INTO research_search_fts(research_search_fts) "
                    "VALUES(:command)"
                ),
                {"command": command},
            )
        except Exception as exc:
            if _is_sqlite_backend_error(exc):
                raise _backend_unavailable(
                    f"research search FTS {command} failed",
                    exc,
                ) from None
            raise

    def probe(self) -> bool:
        try:
            self._session.execute(
                text(
                    "SELECT rowid FROM research_search_fts "
                    "WHERE research_search_fts MATCH :q LIMIT 1"
                ),
                {"q": '"__trading_partner_fts_probe__"'},
            ).first()
            return True
        except Exception as exc:
            if _is_sqlite_backend_error(exc):
                return False
            raise

    def _build_projection(
        self, entity_type: str, entity_id: str
    ) -> SearchDocumentProjection:
        if entity_type == ResearchSearchEntityType.EVIDENCE.value:
            evidence_row = self._session.get(ResearchEvidenceRow, entity_id)
            if evidence_row is None:
                raise ResearchMemoryNotFound(
                    "evidence not found",
                    details={"entity_type": entity_type, "entity_id": entity_id},
                )
            evidence = evidence_to_domain(evidence_row)
            return project_evidence(
                evidence, links=self._load_evidence_links(entity_id)
            )

        if entity_type == ResearchSearchEntityType.REPORT.value:
            report_row = self._session.get(ResearchReportRow, entity_id)
            if report_row is None:
                raise ResearchMemoryNotFound(
                    "report not found",
                    details={"entity_type": entity_type, "entity_id": entity_id},
                )
            report = report_to_domain(report_row)
            return project_report(
                report,
                referenced_instrument_ids=self._instruments_for_evidence_ids(
                    report.evidence_ids
                ),
            )

        if entity_type == ResearchSearchEntityType.EVENT.value:
            event_row = self._session.get(ResearchEventRow, entity_id)
            if event_row is None:
                raise ResearchMemoryNotFound(
                    "event not found",
                    details={"entity_type": entity_type, "entity_id": entity_id},
                )
            return project_event(event_to_domain(event_row))

        if entity_type == ResearchSearchEntityType.DECISION.value:
            decision_row = self._session.get(DecisionRecordRow, entity_id)
            if decision_row is None:
                raise ResearchMemoryNotFound(
                    "decision not found",
                    details={"entity_type": entity_type, "entity_id": entity_id},
                )
            decision = decision_to_domain(decision_row)
            return project_decision(
                decision,
                referenced_instrument_ids=self._instruments_for_evidence_ids(
                    decision.evidence_ids
                ),
            )

        if entity_type == ResearchSearchEntityType.JOURNAL.value:
            journal_row = self._session.get(JournalEntryRow, entity_id)
            if journal_row is None:
                raise ResearchMemoryNotFound(
                    "journal not found",
                    details={"entity_type": entity_type, "entity_id": entity_id},
                )
            return project_journal(journal_to_domain(journal_row))

        raise ResearchMemoryNotFound(
            "unknown research search entity type",
            details={"entity_type": entity_type, "entity_id": entity_id},
        )

    def _upsert_projection(self, projection: SearchDocumentProjection) -> None:
        superseded_by = self._lookup_successor_id(
            entity_type=projection.entity_type.value,
            entity_id=projection.entity_id,
        )
        existing = self._session.execute(
            text(
                "SELECT rowid FROM research_search_documents "
                "WHERE entity_id = :entity_id"
            ),
            {"entity_id": projection.entity_id},
        ).first()
        ids_text = instrument_ids_text(projection.instrument_ids)
        visible_at = dt_to_db(projection.visible_at)
        occurred_at = (
            None
            if projection.occurred_at is None
            else dt_to_db(projection.occurred_at)
        )
        payload = {
            "entity_type": projection.entity_type.value,
            "entity_id": projection.entity_id,
            "instrument_ids_text": ids_text,
            "topic_tags": projection.topic_tags_fts,
            "title": projection.title_fts,
            "body": projection.body_fts,
            "visible_at": visible_at,
            "occurred_at": occurred_at,
            "superseded_by_id": superseded_by,
        }
        if existing is None:
            self._session.execute(
                text(
                    "INSERT INTO research_search_documents("
                    "entity_type, entity_id, instrument_ids_text, topic_tags, "
                    "title, body, visible_at, occurred_at, superseded_by_id"
                    ") VALUES ("
                    ":entity_type, :entity_id, :instrument_ids_text, :topic_tags, "
                    ":title, :body, :visible_at, :occurred_at, :superseded_by_id"
                    ")"
                ),
                payload,
            )
            rowid = int(
                self._session.execute(
                    text(
                        "SELECT rowid FROM research_search_documents "
                        "WHERE entity_id = :entity_id"
                    ),
                    {"entity_id": projection.entity_id},
                ).scalar_one()
            )
        else:
            rowid = int(existing[0])
            self._delete_mappings(rowid)
            self._session.execute(
                text(
                    "UPDATE research_search_documents SET "
                    "entity_type = :entity_type, "
                    "instrument_ids_text = :instrument_ids_text, "
                    "topic_tags = :topic_tags, "
                    "title = :title, "
                    "body = :body, "
                    "visible_at = :visible_at, "
                    "occurred_at = :occurred_at, "
                    "superseded_by_id = :superseded_by_id "
                    "WHERE rowid = :rowid"
                ),
                {**payload, "rowid": rowid},
            )

        for membership in projection.case_memberships:
            self._session.execute(
                text(
                    "INSERT INTO research_search_document_cases("
                    "document_rowid, case_id, membership_visible_at"
                    ") VALUES (:rowid, :case_id, :membership_visible_at)"
                ),
                {
                    "rowid": rowid,
                    "case_id": membership.case_id,
                    "membership_visible_at": dt_to_db(
                        membership.membership_visible_at
                    ),
                },
            )
        for instrument_id in projection.instrument_ids:
            self._session.execute(
                text(
                    "INSERT INTO research_search_document_instruments("
                    "document_rowid, instrument_id"
                    ") VALUES (:rowid, :instrument_id)"
                ),
                {"rowid": rowid, "instrument_id": instrument_id},
            )
        for tag in projection.topic_tags:
            self._session.execute(
                text(
                    "INSERT INTO research_search_document_tags("
                    "document_rowid, topic_tag"
                    ") VALUES (:rowid, :topic_tag)"
                ),
                {"rowid": rowid, "topic_tag": tag},
            )
        self._session.flush()

    def _delete_mappings(self, rowid: int) -> None:
        self._session.execute(
            text(
                "DELETE FROM research_search_document_cases "
                "WHERE document_rowid = :rowid"
            ),
            {"rowid": rowid},
        )
        self._session.execute(
            text(
                "DELETE FROM research_search_document_instruments "
                "WHERE document_rowid = :rowid"
            ),
            {"rowid": rowid},
        )
        self._session.execute(
            text(
                "DELETE FROM research_search_document_tags "
                "WHERE document_rowid = :rowid"
            ),
            {"rowid": rowid},
        )

    def _mark_predecessor_superseded(
        self, *, predecessor_id: str, successor_id: str
    ) -> None:
        self._session.execute(
            text(
                "UPDATE research_search_documents "
                "SET superseded_by_id = :successor_id "
                "WHERE entity_id = :predecessor_id"
            ),
            {"successor_id": successor_id, "predecessor_id": predecessor_id},
        )

    def _lookup_successor_id(
        self, *, entity_type: str, entity_id: str
    ) -> str | None:
        lookup = _SUCCESSOR_LOOKUP.get(entity_type)
        if lookup is None:
            return None
        table, id_col, supersedes_col = lookup
        row = self._session.execute(
            text(
                f"SELECT {id_col} FROM {table} "
                f"WHERE {supersedes_col} = :entity_id "
                f"ORDER BY {id_col} ASC LIMIT 1"
            ),
            {"entity_id": entity_id},
        ).first()
        if row is None:
            return None
        return str(row[0])

    def _load_evidence_links(self, evidence_id: str) -> tuple[Any, ...]:
        rows = self._session.scalars(
            select(CaseEvidenceLinkRow)
            .where(CaseEvidenceLinkRow.evidence_id == evidence_id)
            .order_by(
                CaseEvidenceLinkRow.case_id.asc(),
                CaseEvidenceLinkRow.link_id.asc(),
            )
        ).all()
        return tuple(link_to_domain(row) for row in rows)

    def _instruments_for_evidence_ids(
        self, evidence_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        if not evidence_ids:
            return ()
        groups: list[tuple[str, ...]] = []
        for evidence_id in evidence_ids:
            row = self._session.get(ResearchEvidenceRow, evidence_id)
            if row is None:
                continue
            groups.append(tuple(row.instrument_ids_json))
        return stable_instrument_union(*groups)

    def _effective_entity_types(
        self, query: ResearchSearchQuery
    ) -> tuple[ResearchSearchEntityType, ...]:
        if query.entity_types:
            return tuple(
                ResearchSearchEntityType(_wire(item)) for item in query.entity_types
            )
        if query.stances:
            return (ResearchSearchEntityType.EVIDENCE,)
        if query.journal_entry_types:
            return (ResearchSearchEntityType.JOURNAL,)
        if query.thesis_id is not None:
            return _THESIS_DEFAULT_ENTITY_TYPES
        return _ALL_ENTITY_TYPES

    def _build_filters(
        self,
        *,
        query: ResearchSearchQuery,
        effective_types: tuple[ResearchSearchEntityType, ...],
        match_expr: str | None,
    ) -> tuple[str, dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {}

        type_placeholders: list[str] = []
        for index, entity_type in enumerate(effective_types):
            key = f"etype_{index}"
            type_placeholders.append(f":{key}")
            params[key] = _wire(entity_type)
        clauses.append(f"d.entity_type IN ({', '.join(type_placeholders)})")

        if match_expr is not None:
            clauses.append("research_search_fts MATCH :match_expr")
            params["match_expr"] = match_expr

        if query.case_id is not None:
            params["case_id"] = query.case_id
            if query.as_of is not None:
                params["case_as_of"] = dt_to_db(query.as_of)
                clauses.append(
                    "EXISTS ("
                    "SELECT 1 FROM research_search_document_cases c "
                    "WHERE c.document_rowid = d.rowid "
                    "AND c.case_id = :case_id "
                    "AND c.membership_visible_at <= :case_as_of"
                    ")"
                )
            else:
                clauses.append(
                    "EXISTS ("
                    "SELECT 1 FROM research_search_document_cases c "
                    "WHERE c.document_rowid = d.rowid "
                    "AND c.case_id = :case_id"
                    ")"
                )

        if query.instrument_id is not None:
            params["instrument_id"] = query.instrument_id
            clauses.append(
                "EXISTS ("
                "SELECT 1 FROM research_search_document_instruments i "
                "WHERE i.document_rowid = d.rowid "
                "AND i.instrument_id = :instrument_id"
                ")"
            )

        for tag_index, tag in enumerate(query.topic_tags):
            key = f"topic_tag_{tag_index}"
            params[key] = tag
            clauses.append(
                "EXISTS ("
                "SELECT 1 FROM research_search_document_tags t "
                f"WHERE t.document_rowid = d.rowid AND t.topic_tag = :{key}"
                ")"
            )

        if query.visible_from is not None:
            params["visible_from"] = dt_to_db(query.visible_from)
            clauses.append("d.visible_at >= :visible_from")
        if query.visible_to is not None:
            params["visible_to"] = dt_to_db(query.visible_to)
            clauses.append("d.visible_at <= :visible_to")

        if query.as_of is not None:
            params["as_of"] = dt_to_db(query.as_of)
            clauses.append("d.visible_at <= :as_of")
            clauses.append(
                "("
                "d.entity_type != 'report' OR EXISTS ("
                "SELECT 1 FROM research_reports r "
                "WHERE r.report_id = d.entity_id AND r.as_of <= :as_of"
                ")"
                ")"
            )

        if query.evidence_types:
            et_placeholders: list[str] = []
            for index, evidence_type in enumerate(query.evidence_types):
                key = f"evtype_{index}"
                et_placeholders.append(f":{key}")
                params[key] = _wire(evidence_type)
            clauses.append(
                "("
                "d.entity_type = 'evidence' AND EXISTS ("
                "SELECT 1 FROM research_evidence e "
                "WHERE e.evidence_id = d.entity_id "
                f"AND e.evidence_type IN ({', '.join(et_placeholders)})"
                ")"
                ")"
            )

        if query.journal_entry_types:
            jt_placeholders: list[str] = []
            for index, entry_type in enumerate(query.journal_entry_types):
                key = f"jtype_{index}"
                jt_placeholders.append(f":{key}")
                params[key] = _wire(entry_type)
            clauses.append(
                "("
                "d.entity_type = 'journal' AND EXISTS ("
                "SELECT 1 FROM journal_entries j "
                "WHERE j.journal_id = d.entity_id "
                f"AND j.entry_type IN ({', '.join(jt_placeholders)})"
                ")"
                ")"
            )

        if query.thesis_id is not None:
            params["thesis_id"] = query.thesis_id
            as_of_assess = " AND a.assessed_at <= :as_of" if query.as_of else ""
            as_of_rev = " AND tr.confirmed_at <= :as_of" if query.as_of else ""
            clauses.append(
                "("
                "("
                "d.entity_type = 'evidence' AND EXISTS ("
                "SELECT 1 FROM evidence_assessments a "
                "WHERE a.evidence_id = d.entity_id "
                "AND a.thesis_id = :thesis_id"
                f"{as_of_assess}"
                ")"
                ") OR ("
                "d.entity_type = 'report' AND EXISTS ("
                "SELECT 1 FROM research_reports r "
                "JOIN json_each(r.thesis_revision_ids_json) je "
                "JOIN thesis_revisions tr ON tr.revision_id = je.value "
                "WHERE r.report_id = d.entity_id "
                "AND tr.thesis_id = :thesis_id"
                f"{as_of_rev}"
                ")"
                ") OR ("
                "d.entity_type = 'decision' AND EXISTS ("
                "SELECT 1 FROM decision_records dec "
                "JOIN json_each(dec.thesis_revision_ids_json) je "
                "JOIN thesis_revisions tr ON tr.revision_id = je.value "
                "WHERE dec.decision_id = d.entity_id "
                "AND tr.thesis_id = :thesis_id"
                f"{as_of_rev}"
                ")"
                ")"
                ")"
            )

        if query.stances:
            stance_placeholders: list[str] = []
            for index, stance in enumerate(query.stances):
                key = f"stance_{index}"
                stance_placeholders.append(f":{key}")
                params[key] = _wire(stance)
            if query.thesis_id is not None:
                stance_scope = " AND a.thesis_id = :thesis_id"
            else:
                params["stance_case_id"] = query.case_id
                stance_scope = " AND a.case_id = :stance_case_id"
            as_of_clause = " AND a.assessed_at <= :as_of" if query.as_of else ""
            clauses.append(
                "EXISTS ("
                "SELECT 1 FROM evidence_assessments a "
                "WHERE a.evidence_id = d.entity_id "
                f"AND a.stance IN ({', '.join(stance_placeholders)})"
                f"{stance_scope}{as_of_clause}"
                ")"
            )

        if not query.include_superseded:
            clauses.append(self._supersession_clause(query=query, params=params))

        return " AND ".join(clauses), params

    def _supersession_clause(
        self,
        *,
        query: ResearchSearchQuery,
        params: dict[str, Any],
    ) -> str:
        if query.case_id is not None:
            if query.as_of is not None:
                params.setdefault("as_of", dt_to_db(query.as_of))
                evidence_case_hide = (
                    "EXISTS ("
                    "SELECT 1 FROM research_evidence succ "
                    "JOIN case_evidence_links scl "
                    "ON scl.evidence_id = succ.evidence_id "
                    "WHERE succ.supersedes_evidence_id = d.entity_id "
                    "AND scl.case_id = :case_id "
                    "AND scl.linked_at <= :as_of "
                    "AND succ.observed_at <= :as_of"
                    ")"
                )
            else:
                evidence_case_hide = (
                    "EXISTS ("
                    "SELECT 1 FROM research_evidence succ "
                    "JOIN case_evidence_links scl "
                    "ON scl.evidence_id = succ.evidence_id "
                    "WHERE succ.supersedes_evidence_id = d.entity_id "
                    "AND scl.case_id = :case_id"
                    ")"
                )
            if query.as_of is None:
                return (
                    "("
                    "(d.entity_type = 'evidence' AND NOT "
                    f"{evidence_case_hide}) OR "
                    "(d.entity_type != 'evidence' AND d.superseded_by_id IS NULL)"
                    ")"
                )
            return (
                "("
                "(d.entity_type = 'evidence' AND NOT "
                f"{evidence_case_hide}) OR "
                "(d.entity_type = 'report' AND NOT EXISTS ("
                "SELECT 1 FROM research_reports succ "
                "WHERE succ.supersedes_report_id = d.entity_id "
                "AND succ.created_at <= :as_of"
                ")) OR "
                "(d.entity_type = 'decision' AND NOT EXISTS ("
                "SELECT 1 FROM decision_records succ "
                "WHERE succ.supersedes_decision_id = d.entity_id "
                "AND succ.recorded_at <= :as_of"
                ")) OR "
                "(d.entity_type = 'journal' AND NOT EXISTS ("
                "SELECT 1 FROM journal_entries succ "
                "WHERE succ.supersedes_journal_id = d.entity_id "
                "AND succ.created_at <= :as_of"
                ")) OR "
                "(d.entity_type = 'event')"
                ")"
            )

        if query.as_of is None:
            return "d.superseded_by_id IS NULL"

        params.setdefault("as_of", dt_to_db(query.as_of))
        return (
            "("
            "(d.entity_type = 'evidence' AND NOT EXISTS ("
            "SELECT 1 FROM research_evidence succ "
            "WHERE succ.supersedes_evidence_id = d.entity_id "
            "AND succ.observed_at <= :as_of"
            ")) OR "
            "(d.entity_type = 'report' AND NOT EXISTS ("
            "SELECT 1 FROM research_reports succ "
            "WHERE succ.supersedes_report_id = d.entity_id "
            "AND succ.created_at <= :as_of"
            ")) OR "
            "(d.entity_type = 'decision' AND NOT EXISTS ("
            "SELECT 1 FROM decision_records succ "
            "WHERE succ.supersedes_decision_id = d.entity_id "
            "AND succ.recorded_at <= :as_of"
            ")) OR "
            "(d.entity_type = 'journal' AND NOT EXISTS ("
            "SELECT 1 FROM journal_entries succ "
            "WHERE succ.supersedes_journal_id = d.entity_id "
            "AND succ.created_at <= :as_of"
            ")) OR "
            "(d.entity_type = 'event')"
            ")"
        )

    def _hydrate_hit(
        self,
        *,
        entity_type: str,
        entity_id: str,
        score: Decimal | None,
        query: ResearchSearchQuery,
    ) -> ResearchSearchHitDTO:
        if entity_type == ResearchSearchEntityType.EVIDENCE.value:
            return self._hydrate_evidence(entity_id, score=score, query=query)
        if entity_type == ResearchSearchEntityType.REPORT.value:
            return self._hydrate_report(entity_id, score=score)
        if entity_type == ResearchSearchEntityType.EVENT.value:
            return self._hydrate_event(entity_id, score=score)
        if entity_type == ResearchSearchEntityType.DECISION.value:
            return self._hydrate_decision(entity_id, score=score)
        if entity_type == ResearchSearchEntityType.JOURNAL.value:
            return self._hydrate_journal(entity_id, score=score)
        raise ResearchMemoryNotFound(
            "unknown search hit entity type",
            details={"entity_type": entity_type, "entity_id": entity_id},
        )

    def _hydrate_evidence(
        self,
        entity_id: str,
        *,
        score: Decimal | None,
        query: ResearchSearchQuery,
    ) -> ResearchSearchHitDTO:
        row = self._session.get(ResearchEvidenceRow, entity_id)
        if row is None:
            raise ResearchMemoryNotFound(
                "evidence not found during hydrate",
                details={
                    "entity_type": ResearchSearchEntityType.EVIDENCE.value,
                    "entity_id": entity_id,
                },
            )
        evidence = evidence_to_domain(row)
        stances, assessment_ids = self._matched_assessment_metadata(
            evidence_id=entity_id, query=query
        )
        return ResearchSearchHitDTO(
            entity_type=ResearchSearchEntityType.EVIDENCE,
            entity_id=evidence.evidence_id,
            case_id=query.case_id,
            title=evidence.title,
            snippet=_truncate_snippet(evidence.summary),
            visible_at=evidence.observed_at,
            occurred_at=evidence_occurred_at(evidence),
            instrument_ids=evidence.instrument_ids,
            topic_tags=evidence.topic_tags,
            matched_stances=stances,
            matched_assessment_ids=assessment_ids,
            score=score,
            source_name=evidence.source_name,
        )

    def _hydrate_report(
        self, entity_id: str, *, score: Decimal | None
    ) -> ResearchSearchHitDTO:
        row = self._session.get(ResearchReportRow, entity_id)
        if row is None:
            raise ResearchMemoryNotFound(
                "report not found during hydrate",
                details={
                    "entity_type": ResearchSearchEntityType.REPORT.value,
                    "entity_id": entity_id,
                },
            )
        report = report_to_domain(row)
        return ResearchSearchHitDTO(
            entity_type=ResearchSearchEntityType.REPORT,
            entity_id=report.report_id,
            case_id=report.case_id,
            title=report.title,
            snippet=_truncate_snippet(report.summary),
            visible_at=report.created_at,
            occurred_at=report.as_of,
            instrument_ids=self._instruments_for_evidence_ids(report.evidence_ids),
            topic_tags=(),
            matched_stances=(),
            matched_assessment_ids=(),
            score=score,
            source_name=None,
        )

    def _hydrate_event(
        self, entity_id: str, *, score: Decimal | None
    ) -> ResearchSearchHitDTO:
        row = self._session.get(ResearchEventRow, entity_id)
        if row is None:
            raise ResearchMemoryNotFound(
                "event not found during hydrate",
                details={
                    "entity_type": ResearchSearchEntityType.EVENT.value,
                    "entity_id": entity_id,
                },
            )
        event = event_to_domain(row)
        return ResearchSearchHitDTO(
            entity_type=ResearchSearchEntityType.EVENT,
            entity_id=event.event_id,
            case_id=event.case_id,
            title=event.title,
            snippet=_truncate_snippet(event.summary),
            visible_at=event.recorded_at,
            occurred_at=event.occurred_at,
            instrument_ids=event.instrument_ids,
            topic_tags=(),
            matched_stances=(),
            matched_assessment_ids=(),
            score=score,
            source_name=event.source_name,
        )

    def _hydrate_decision(
        self, entity_id: str, *, score: Decimal | None
    ) -> ResearchSearchHitDTO:
        row = self._session.get(DecisionRecordRow, entity_id)
        if row is None:
            raise ResearchMemoryNotFound(
                "decision not found during hydrate",
                details={
                    "entity_type": ResearchSearchEntityType.DECISION.value,
                    "entity_id": entity_id,
                },
            )
        decision = decision_to_domain(row)
        primary = (
            (decision.primary_instrument_id,)
            if decision.primary_instrument_id is not None
            else ()
        )
        instruments = stable_instrument_union(
            primary,
            self._instruments_for_evidence_ids(decision.evidence_ids),
        )
        return ResearchSearchHitDTO(
            entity_type=ResearchSearchEntityType.DECISION,
            entity_id=decision.decision_id,
            case_id=decision.case_id,
            title=decision.title,
            snippet=_truncate_snippet(decision.rationale),
            visible_at=decision.recorded_at,
            occurred_at=decision.decided_at,
            instrument_ids=instruments,
            topic_tags=(),
            matched_stances=(),
            matched_assessment_ids=(),
            score=score,
            source_name=None,
        )

    def _hydrate_journal(
        self, entity_id: str, *, score: Decimal | None
    ) -> ResearchSearchHitDTO:
        row = self._session.get(JournalEntryRow, entity_id)
        if row is None:
            raise ResearchMemoryNotFound(
                "journal not found during hydrate",
                details={
                    "entity_type": ResearchSearchEntityType.JOURNAL.value,
                    "entity_id": entity_id,
                },
            )
        entry = journal_to_domain(row)
        return ResearchSearchHitDTO(
            entity_type=ResearchSearchEntityType.JOURNAL,
            entity_id=entry.journal_id,
            case_id=entry.case_id,
            title=entry.title,
            snippet=_truncate_snippet(entry.body_markdown),
            visible_at=entry.created_at,
            occurred_at=entry.created_at,
            instrument_ids=entry.instrument_ids,
            topic_tags=entry.topic_tags,
            matched_stances=(),
            matched_assessment_ids=(),
            score=score,
            source_name=None,
        )

    def _matched_assessment_metadata(
        self,
        *,
        evidence_id: str,
        query: ResearchSearchQuery,
    ) -> tuple[tuple[EvidenceStance, ...], tuple[str, ...]]:
        if not query.stances:
            return (), ()

        sql = (
            "SELECT assessment_id, stance, assessed_at "
            "FROM evidence_assessments "
            "WHERE evidence_id = :evidence_id"
        )
        params: dict[str, Any] = {"evidence_id": evidence_id}
        if query.thesis_id is not None:
            sql += " AND thesis_id = :thesis_id"
            params["thesis_id"] = query.thesis_id
        elif query.case_id is not None:
            sql += " AND case_id = :case_id"
            params["case_id"] = query.case_id
        stance_keys: list[str] = []
        for index, stance in enumerate(query.stances):
            key = f"ms_{index}"
            stance_keys.append(f":{key}")
            params[key] = _wire(stance)
        sql += f" AND stance IN ({', '.join(stance_keys)})"
        if query.as_of is not None:
            sql += " AND assessed_at <= :as_of"
            params["as_of"] = dt_to_db(query.as_of)
        sql += " ORDER BY assessed_at DESC, assessment_id ASC"

        rows = self._session.execute(text(sql), params).all()
        if not rows:
            return (), ()

        assessment_ids: list[str] = []
        seen_ids: set[str] = set()
        for assessment_id, _stance, _assessed_at in rows:
            aid = str(assessment_id)
            if aid in seen_ids:
                continue
            seen_ids.add(aid)
            assessment_ids.append(aid)

        latest_by_stance: dict[str, str] = {}
        for _assessment_id, stance, assessed_at in rows:
            wire = str(stance)
            if wire not in latest_by_stance:
                latest_by_stance[wire] = str(assessed_at)
        ordered = sorted(
            latest_by_stance.items(),
            key=lambda item: (-_iso_sort_key(item[1]), item[0]),
        )
        return (
            tuple(EvidenceStance(wire) for wire, _ in ordered),
            tuple(assessment_ids),
        )


def _iso_sort_key(value: str) -> float:
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0
