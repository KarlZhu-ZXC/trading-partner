"""SQLAlchemy append-only transaction annotation repository."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from application.ports.activity_annotation_repository import ActivityAnnotationRepository
from domain.common.enums import VendorId
from domain.common.errors import (
    ActivityAnnotationVersionConflict,
    IdempotencyConflict,
    PersistenceError,
)
from domain.portfolio.enums import ActivityAnnotationStatus, TradeCycleClassification
from domain.portfolio.models import ActivityAnnotation
from infrastructure.persistence.orm import TransactionDecisionLinkRow
from infrastructure.persistence.repositories._mapping import dt_from_db, dt_to_db
from infrastructure.persistence.repositories.append_only import register_append_only_listeners

register_append_only_listeners()


def _domain(row: TransactionDecisionLinkRow) -> ActivityAnnotation:
    return ActivityAnnotation(
        annotation_id=row.annotation_id,
        provider=VendorId(row.provider),
        account_ref=row.account_ref,
        provider_transaction_id=row.provider_transaction_id,
        version=row.version,
        status=ActivityAnnotationStatus(row.status),
        classification=(
            TradeCycleClassification(row.classification) if row.classification else None
        ),
        order_intent_id=row.order_intent_id,
        decision_id=row.decision_id,
        trade_plan_id=row.trade_plan_id,
        trade_plan_version=row.trade_plan_version,
        subject_id=row.subject_id,
        note=row.note,
        actor=row.actor,
        authorization_note=row.authorization_note,
        idempotency_key=row.idempotency_key,
        created_at=dt_from_db(row.created_at, field_name="created_at"),
    )


def _same_payload(left: ActivityAnnotation, right: ActivityAnnotation) -> bool:
    """Compare the caller-owned idempotent payload, excluding generated identity."""

    return (
        left.provider == right.provider
        and left.account_ref == right.account_ref
        and left.provider_transaction_id == right.provider_transaction_id
        and left.status == right.status
        and left.classification == right.classification
        and left.order_intent_id == right.order_intent_id
        and left.decision_id == right.decision_id
        and left.trade_plan_id == right.trade_plan_id
        and left.trade_plan_version == right.trade_plan_version
        and left.subject_id == right.subject_id
        and left.note == right.note
        and left.actor == right.actor
        and left.authorization_note == right.authorization_note
    )


class SqlAlchemyActivityAnnotationRepository(ActivityAnnotationRepository):
    """Append-only persistence for one annotation revision per exact activity."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @staticmethod
    def _key_filter(
        statement: Select[tuple[TransactionDecisionLinkRow]],
        *,
        provider: VendorId,
        account_ref: str,
        provider_transaction_id: str,
    ) -> Select[tuple[TransactionDecisionLinkRow]]:
        # Kept as a tiny helper so every read uses all three natural-key parts.
        return statement.where(
            TransactionDecisionLinkRow.provider == provider.value,
            TransactionDecisionLinkRow.account_ref == account_ref,
            TransactionDecisionLinkRow.provider_transaction_id == provider_transaction_id,
        )

    def append(
        self,
        annotation: ActivityAnnotation,
        *,
        expected_version: int | None = None,
    ) -> ActivityAnnotation:
        with Session(self._engine) as session, session.begin():
            duplicate = session.scalar(
                select(TransactionDecisionLinkRow).where(
                    TransactionDecisionLinkRow.idempotency_key == annotation.idempotency_key
                )
            )
            if duplicate is not None:
                existing = _domain(duplicate)
                if not _same_payload(existing, annotation):
                    raise IdempotencyConflict("Activity annotation idempotency key was reused")
                return existing

            current = (
                session.scalar(
                    select(func.max(TransactionDecisionLinkRow.version)).where(
                        TransactionDecisionLinkRow.provider == annotation.provider.value,
                        TransactionDecisionLinkRow.account_ref == annotation.account_ref,
                        TransactionDecisionLinkRow.provider_transaction_id
                        == annotation.provider_transaction_id,
                    )
                )
                or 0
            )
            if expected_version is not None and expected_version != current:
                raise ActivityAnnotationVersionConflict(
                    "Activity annotation expected version does not match current version",
                    details={
                        "current_version": current,
                        "expected_version": expected_version,
                    },
                )
            if annotation.version != current + 1:
                raise ActivityAnnotationVersionConflict(
                    "Activity annotation version must append the current revision",
                    details={
                        "current_version": current,
                        "requested_version": annotation.version,
                    },
                )
            row = TransactionDecisionLinkRow(
                annotation_id=annotation.annotation_id,
                provider=annotation.provider.value,
                account_ref=annotation.account_ref,
                provider_transaction_id=annotation.provider_transaction_id,
                version=annotation.version,
                status=annotation.status.value,
                classification=(
                    annotation.classification.value if annotation.classification else None
                ),
                order_intent_id=annotation.order_intent_id,
                decision_id=annotation.decision_id,
                trade_plan_id=annotation.trade_plan_id,
                trade_plan_version=annotation.trade_plan_version,
                subject_id=annotation.subject_id,
                note=annotation.note,
                actor=annotation.actor,
                authorization_note=annotation.authorization_note,
                idempotency_key=annotation.idempotency_key,
                created_at=dt_to_db(annotation.created_at),
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError as exc:
                raise PersistenceError("Activity annotation append conflict") from exc
            return annotation

    # Explicit name for callers that use the revision vocabulary.
    append_revision = append

    def get_latest(
        self,
        *,
        provider: VendorId,
        account_ref: str,
        provider_transaction_id: str,
    ) -> ActivityAnnotation | None:
        with Session(self._engine) as session:
            statement = select(TransactionDecisionLinkRow)
            statement = self._key_filter(
                statement,
                provider=provider,
                account_ref=account_ref,
                provider_transaction_id=provider_transaction_id,
            )
            statement = statement.order_by(TransactionDecisionLinkRow.version.desc()).limit(1)
            row = session.scalar(statement)
            return _domain(row) if row is not None else None

    def get_by_idempotency_key(self, idempotency_key: str) -> ActivityAnnotation | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(TransactionDecisionLinkRow).where(
                    TransactionDecisionLinkRow.idempotency_key == idempotency_key
                )
            )
            return _domain(row) if row is not None else None

    def list_latest(
        self,
        *,
        providers: tuple[VendorId, ...] = (),
        account_refs: tuple[str, ...] = (),
        limit: int | None = None,
    ) -> tuple[ActivityAnnotation, ...]:
        with Session(self._engine) as session:
            statement = select(TransactionDecisionLinkRow)
            if providers:
                statement = statement.where(
                    TransactionDecisionLinkRow.provider.in_(item.value for item in providers)
                )
            if account_refs:
                statement = statement.where(
                    TransactionDecisionLinkRow.account_ref.in_(account_refs)
                )
            rows = tuple(session.scalars(statement))
        latest: dict[tuple[str, str, str], TransactionDecisionLinkRow] = {}
        for row in rows:
            key = (row.provider, row.account_ref, row.provider_transaction_id)
            old = latest.get(key)
            if old is None or row.version > old.version:
                latest[key] = row
        values = sorted(
            (_domain(row) for row in latest.values()),
            key=lambda item: (
                item.created_at,
                item.provider.value,
                item.account_ref,
                item.provider_transaction_id,
            ),
            reverse=True,
        )
        return tuple(values if limit is None else values[:limit])

    def list_revisions(
        self,
        *,
        provider: VendorId,
        account_ref: str,
        provider_transaction_id: str,
    ) -> tuple[ActivityAnnotation, ...]:
        with Session(self._engine) as session:
            statement = select(TransactionDecisionLinkRow)
            statement = self._key_filter(
                statement,
                provider=provider,
                account_ref=account_ref,
                provider_transaction_id=provider_transaction_id,
            )
            statement = statement.order_by(TransactionDecisionLinkRow.version)
            return tuple(_domain(row) for row in session.scalars(statement))

    def list(
        self,
        *,
        providers: tuple[VendorId, ...] = (),
        account_refs: tuple[str, ...] = (),
        limit: int | None = None,
    ) -> tuple[ActivityAnnotation, ...]:
        return self.list_latest(
            providers=providers,
            account_refs=account_refs,
            limit=limit,
        )
