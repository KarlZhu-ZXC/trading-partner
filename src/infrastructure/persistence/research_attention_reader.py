"""One aggregate SELECT for the complete durable research attention summary."""

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from application.ports.research_attention_reader import ResearchAttentionSummary
from domain.common.enums import CandidateStatus, ResearchSubjectStatus
from infrastructure.persistence.orm.research import CandidateThesisRevisionRow, ResearchSubjectRow


class SqlAlchemyResearchAttentionReader:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def pending_by_subject(self) -> tuple[ResearchAttentionSummary, ...]:
        statement = (
            select(
                ResearchSubjectRow.subject_id,
                ResearchSubjectRow.title,
                func.count(CandidateThesisRevisionRow.candidate_id),
            )
            .join(
                CandidateThesisRevisionRow,
                CandidateThesisRevisionRow.subject_id == ResearchSubjectRow.subject_id,
            )
            .where(
                ResearchSubjectRow.status != ResearchSubjectStatus.ARCHIVED.value,
                CandidateThesisRevisionRow.status == CandidateStatus.PROPOSED.value,
            )
            .group_by(ResearchSubjectRow.subject_id, ResearchSubjectRow.title)
            .order_by(ResearchSubjectRow.subject_id)
        )
        with self._engine.connect() as connection:
            return tuple(
                ResearchAttentionSummary(subject_id=row[0], title=row[1], pending_count=row[2])
                for row in connection.execute(statement)
            )
