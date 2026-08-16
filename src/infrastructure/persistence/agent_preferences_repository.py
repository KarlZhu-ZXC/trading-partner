"""SQLAlchemy adapter for durable Agent presentation preferences."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult, Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from application.ports.agent_preferences_repository import AgentPreferencesRepository
from domain.agent.preferences import (
    AgentPreferenceLanguage,
    AgentPreferences,
    AgentPreferencesRevision,
    AgentResponseDensity,
    AgentRiskStyle,
)
from domain.common.errors import AgentPreferencesVersionConflict, IdempotencyConflict
from infrastructure.persistence.orm import AgentPreferencesRevisionRow, AgentPreferencesRow


def _domain(row: AgentPreferencesRow) -> AgentPreferences:
    return AgentPreferences(
        preferences_id=row.preferences_id,
        owner_principal=row.owner_principal,
        language=AgentPreferenceLanguage(row.language),
        response_density=AgentResponseDensity(row.response_density),
        preferred_source_codes=tuple(row.preferred_source_codes),
        risk_style=AgentRiskStyle(row.risk_style),
        default_chart=bool(row.default_chart),
        web_background=bool(row.web_background),
        version=row.version,
        created_at=datetime.fromisoformat(row.created_at),
        updated_at=datetime.fromisoformat(row.updated_at),
    )


def _revision(row: AgentPreferencesRevisionRow) -> AgentPreferencesRevision:
    snapshot = AgentPreferences(
        preferences_id=row.preferences_id,
        owner_principal=row.owner_principal,
        language=AgentPreferenceLanguage(row.language),
        response_density=AgentResponseDensity(row.response_density),
        preferred_source_codes=tuple(row.preferred_source_codes),
        risk_style=AgentRiskStyle(row.risk_style),
        default_chart=bool(row.default_chart),
        web_background=bool(row.web_background),
        version=row.version,
        created_at=datetime.fromisoformat(row.created_at),
        updated_at=datetime.fromisoformat(row.created_at),
    )
    return AgentPreferencesRevision(
        revision_id=row.revision_id,
        preferences_id=row.preferences_id,
        owner_principal=row.owner_principal,
        operation=row.operation,
        actor=row.actor,
        idempotency_key=row.idempotency_key,
        authorization_note=row.authorization_note,
        preferences=snapshot,
        created_at=datetime.fromisoformat(row.created_at),
    )


def _preference_row(value: AgentPreferences) -> AgentPreferencesRow:
    assert value.created_at is not None and value.updated_at is not None
    return AgentPreferencesRow(
        preferences_id=value.preferences_id,
        owner_principal=value.owner_principal,
        language=value.language.value,
        response_density=value.response_density.value,
        preferred_source_codes=value.preferred_source_codes,
        risk_style=value.risk_style.value,
        default_chart=value.default_chart,
        web_background=value.web_background,
        version=value.version,
        created_at=value.created_at.isoformat(),
        updated_at=value.updated_at.isoformat(),
    )


def _revision_row(value: AgentPreferencesRevision) -> AgentPreferencesRevisionRow:
    snapshot = value.preferences
    timestamp = value.created_at.isoformat()
    return AgentPreferencesRevisionRow(
        revision_id=value.revision_id,
        preferences_id=value.preferences_id,
        owner_principal=value.owner_principal,
        version=snapshot.version,
        operation=value.operation,
        actor=value.actor,
        idempotency_key=value.idempotency_key,
        authorization_note=value.authorization_note,
        language=snapshot.language.value,
        response_density=snapshot.response_density.value,
        preferred_source_codes=snapshot.preferred_source_codes,
        risk_style=snapshot.risk_style.value,
        default_chart=snapshot.default_chart,
        web_background=snapshot.web_background,
        created_at=timestamp,
    )


class SqlAlchemyAgentPreferencesRepository(AgentPreferencesRepository):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, owner_principal: str) -> AgentPreferences | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(AgentPreferencesRow).where(
                    AgentPreferencesRow.owner_principal == owner_principal
                )
            )
            return None if row is None else _domain(row)

    def create(
        self,
        value: AgentPreferences,
        revision: AgentPreferencesRevision,
    ) -> AgentPreferences:
        try:
            with Session(self._engine, expire_on_commit=False) as session, session.begin():
                existing_revision = session.scalar(
                    select(AgentPreferencesRevisionRow).where(
                        AgentPreferencesRevisionRow.owner_principal
                        == revision.owner_principal,
                        AgentPreferencesRevisionRow.idempotency_key == revision.idempotency_key,
                    )
                )
                if existing_revision is not None:
                    existing = session.get(AgentPreferencesRow, existing_revision.preferences_id)
                    if existing is not None:
                        return _domain(existing)
                    raise IdempotencyConflict("Agent preferences idempotency record is invalid")
                session.add(_preference_row(value))
                session.add(_revision_row(revision))
            return value
        except IntegrityError as exc:
            raise IdempotencyConflict("Agent preferences write was already used") from exc

    def update(
        self,
        value: AgentPreferences,
        *,
        expected_version: int,
        revision: AgentPreferencesRevision,
    ) -> AgentPreferences:
        with Session(self._engine, expire_on_commit=False) as session:
            try:
                with session.begin():
                    existing_revision = session.scalar(
                        select(AgentPreferencesRevisionRow).where(
                            AgentPreferencesRevisionRow.owner_principal
                            == revision.owner_principal,
                            AgentPreferencesRevisionRow.idempotency_key
                            == revision.idempotency_key,
                        )
                    )
                    if existing_revision is not None:
                        existing = session.get(
                            AgentPreferencesRow,
                            existing_revision.preferences_id,
                        )
                        if existing is not None:
                            return _domain(existing)
                        raise IdempotencyConflict("Agent preferences idempotency record is invalid")
                    result = cast(
                        CursorResult[Any],
                        session.execute(
                            update(AgentPreferencesRow)
                            .where(
                                AgentPreferencesRow.preferences_id
                                == value.preferences_id,
                                AgentPreferencesRow.owner_principal
                                == value.owner_principal,
                                AgentPreferencesRow.version == expected_version,
                            )
                            .values(
                                language=value.language.value,
                                response_density=value.response_density.value,
                                preferred_source_codes=value.preferred_source_codes,
                                risk_style=value.risk_style.value,
                                default_chart=value.default_chart,
                                web_background=value.web_background,
                                version=value.version,
                                updated_at=value.updated_at.isoformat()
                                if value.updated_at is not None
                                else None,
                            )
                        ),
                    )
                    if getattr(result, "rowcount", None) != 1:
                        raise AgentPreferencesVersionConflict(
                            "Agent preferences version conflict"
                        )
                    session.add(_revision_row(revision))
                return value
            except IntegrityError as exc:
                raise IdempotencyConflict("Agent preferences write was already used") from exc

    def list_history(
        self,
        owner_principal: str,
        *,
        limit: int = 100,
    ) -> tuple[AgentPreferencesRevision, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(AgentPreferencesRevisionRow)
                .where(AgentPreferencesRevisionRow.owner_principal == owner_principal)
                .order_by(
                    AgentPreferencesRevisionRow.version.desc(),
                    AgentPreferencesRevisionRow.created_at.desc(),
                )
                .limit(min(limit, 500))
            )
            return tuple(_revision(row) for row in rows)


__all__ = ["SqlAlchemyAgentPreferencesRepository"]
