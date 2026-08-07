"""SQLAlchemy Research Unit of Work — one Session for research rows + audit."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from domain.common.errors import PersistenceError
from infrastructure.persistence.audit_log_writer import SqlAlchemySessionAuditLogWriter
from infrastructure.persistence.repositories import (
    SqlAlchemyAssumptionRepository,
    SqlAlchemyCandidateThesisRevisionRepository,
    SqlAlchemyDecisionRecordRepository,
    SqlAlchemyEvidenceAssessmentRepository,
    SqlAlchemyEvidenceRepository,
    SqlAlchemyInvalidationConditionRepository,
    SqlAlchemyJournalRepository,
    SqlAlchemyOpenQuestionRepository,
    SqlAlchemyResearchEventRepository,
    SqlAlchemyResearchReportRepository,
    SqlAlchemyResearchSearchIndex,
    SqlAlchemyResearchSubjectRepository,
    SqlAlchemySubjectEvidenceLinkRepository,
    SqlAlchemyThesisRepository,
    SqlAlchemyThesisRevisionRepository,
    SqlAlchemyWatchlistRepository,
    register_append_only_listeners,
)
from infrastructure.persistence.trade_plan_repository import SqlAlchemyTradePlanRepository


class SqlAlchemyResearchUnitOfWork:
    """Context-managed UoW binding all research repositories to one Session."""

    def __init__(
        self,
        engine: Engine,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._id_generator = id_generator
        self._secret_redactor = secret_redactor
        self._session: Session | None = None
        self._subjects: SqlAlchemyResearchSubjectRepository | None = None
        self._theses: SqlAlchemyThesisRepository | None = None
        self._revisions: SqlAlchemyThesisRevisionRepository | None = None
        self._assumptions: SqlAlchemyAssumptionRepository | None = None
        self._invalidations: SqlAlchemyInvalidationConditionRepository | None = None
        self._questions: SqlAlchemyOpenQuestionRepository | None = None
        self._watchlist: SqlAlchemyWatchlistRepository | None = None
        self._candidates: SqlAlchemyCandidateThesisRevisionRepository | None = None
        self._evidence: SqlAlchemyEvidenceRepository | None = None
        self._subject_evidence_links: SqlAlchemySubjectEvidenceLinkRepository | None = None
        self._evidence_assessments: SqlAlchemyEvidenceAssessmentRepository | None = None
        self._reports: SqlAlchemyResearchReportRepository | None = None
        self._events: SqlAlchemyResearchEventRepository | None = None
        self._decisions: SqlAlchemyDecisionRecordRepository | None = None
        self._journal: SqlAlchemyJournalRepository | None = None
        self._search_index: SqlAlchemyResearchSearchIndex | None = None
        self._audit: SqlAlchemySessionAuditLogWriter | None = None
        self._trade_plans: SqlAlchemyTradePlanRepository | None = None
        register_append_only_listeners()

    def __enter__(self) -> SqlAlchemyResearchUnitOfWork:
        if self._session is not None:
            raise PersistenceError("ResearchUnitOfWork is already entered")
        session = Session(self._engine)
        # Ensure SQLite FK checks are on for this connection.
        if self._engine.dialect.name == "sqlite":
            from sqlalchemy import text

            session.execute(text("PRAGMA foreign_keys=ON"))
        self._session = session
        self._subjects = SqlAlchemyResearchSubjectRepository(session, self._clock)
        self._theses = SqlAlchemyThesisRepository(session, self._clock)
        self._revisions = SqlAlchemyThesisRevisionRepository(session)
        self._assumptions = SqlAlchemyAssumptionRepository(session)
        self._invalidations = SqlAlchemyInvalidationConditionRepository(session)
        self._questions = SqlAlchemyOpenQuestionRepository(session)
        self._watchlist = SqlAlchemyWatchlistRepository(session, self._clock)
        self._candidates = SqlAlchemyCandidateThesisRevisionRepository(session)
        self._evidence = SqlAlchemyEvidenceRepository(session)
        self._subject_evidence_links = SqlAlchemySubjectEvidenceLinkRepository(session)
        self._evidence_assessments = SqlAlchemyEvidenceAssessmentRepository(session)
        self._reports = SqlAlchemyResearchReportRepository(session)
        self._events = SqlAlchemyResearchEventRepository(session)
        self._decisions = SqlAlchemyDecisionRecordRepository(session)
        self._journal = SqlAlchemyJournalRepository(session)
        self._search_index = SqlAlchemyResearchSearchIndex(session)
        self._audit = SqlAlchemySessionAuditLogWriter(
            session,
            self._clock,
            self._id_generator,
            self._secret_redactor,
        )
        self._trade_plans = SqlAlchemyTradePlanRepository(session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if self._session is not None:
                if exc_type is not None:
                    self._session.rollback()
                self._session.close()
        finally:
            self._session = None
            self._subjects = None
            self._theses = None
            self._revisions = None
            self._assumptions = None
            self._invalidations = None
            self._questions = None
            self._watchlist = None
            self._candidates = None
            self._evidence = None
            self._subject_evidence_links = None
            self._evidence_assessments = None
            self._reports = None
            self._events = None
            self._decisions = None
            self._journal = None
            self._search_index = None
            self._audit = None
            self._trade_plans = None

    def _require_session(self) -> Session:
        if self._session is None:
            raise PersistenceError("ResearchUnitOfWork is not active; use as context manager")
        return self._session

    @property
    def subjects(self) -> SqlAlchemyResearchSubjectRepository:
        self._require_session()
        assert self._subjects is not None
        return self._subjects

    @property
    def theses(self) -> SqlAlchemyThesisRepository:
        self._require_session()
        assert self._theses is not None
        return self._theses

    @property
    def revisions(self) -> SqlAlchemyThesisRevisionRepository:
        self._require_session()
        assert self._revisions is not None
        return self._revisions

    @property
    def assumptions(self) -> SqlAlchemyAssumptionRepository:
        self._require_session()
        assert self._assumptions is not None
        return self._assumptions

    @property
    def invalidations(self) -> SqlAlchemyInvalidationConditionRepository:
        self._require_session()
        assert self._invalidations is not None
        return self._invalidations

    @property
    def questions(self) -> SqlAlchemyOpenQuestionRepository:
        self._require_session()
        assert self._questions is not None
        return self._questions

    @property
    def watchlist(self) -> SqlAlchemyWatchlistRepository:
        self._require_session()
        assert self._watchlist is not None
        return self._watchlist

    @property
    def candidates(self) -> SqlAlchemyCandidateThesisRevisionRepository:
        self._require_session()
        assert self._candidates is not None
        return self._candidates

    @property
    def evidence(self) -> SqlAlchemyEvidenceRepository:
        self._require_session()
        assert self._evidence is not None
        return self._evidence

    @property
    def subject_evidence_links(self) -> SqlAlchemySubjectEvidenceLinkRepository:
        self._require_session()
        assert self._subject_evidence_links is not None
        return self._subject_evidence_links

    @property
    def evidence_assessments(self) -> SqlAlchemyEvidenceAssessmentRepository:
        self._require_session()
        assert self._evidence_assessments is not None
        return self._evidence_assessments

    @property
    def reports(self) -> SqlAlchemyResearchReportRepository:
        self._require_session()
        assert self._reports is not None
        return self._reports

    @property
    def events(self) -> SqlAlchemyResearchEventRepository:
        self._require_session()
        assert self._events is not None
        return self._events

    @property
    def decisions(self) -> SqlAlchemyDecisionRecordRepository:
        self._require_session()
        assert self._decisions is not None
        return self._decisions

    @property
    def journal(self) -> SqlAlchemyJournalRepository:
        self._require_session()
        assert self._journal is not None
        return self._journal

    @property
    def search_index(self) -> SqlAlchemyResearchSearchIndex:
        self._require_session()
        assert self._search_index is not None
        return self._search_index

    @property
    def trade_plans(self) -> SqlAlchemyTradePlanRepository:
        self._require_session()
        assert self._trade_plans is not None
        return self._trade_plans

    @property
    def audit(self) -> SqlAlchemySessionAuditLogWriter:
        self._require_session()
        assert self._audit is not None
        return self._audit

    def commit(self) -> None:
        session = self._require_session()
        try:
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            raise PersistenceError(
                f"ResearchUnitOfWork commit failed: {type(exc).__name__}",
                details={"error_type": type(exc).__name__},
            ) from exc

    def rollback(self) -> None:
        session = self._require_session()
        session.rollback()
