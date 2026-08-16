"""SQLAlchemy persistence for single-use broker order intents."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from domain.common.errors import (
    BrokerOrderNotFound,
    BrokerOrderPreviewExpired,
    BrokerOrderStateConflict,
    IdempotencyConflict,
    PersistenceError,
)
from domain.execution.models import (
    BrokerOrderDuration,
    BrokerOrderInstruction,
    BrokerOrderIntent,
    BrokerOrderIntentStatus,
    BrokerOrderSession,
    BrokerOrderType,
    BrokerTrailType,
)
from infrastructure.persistence.orm import BrokerOrderIntentRow


def _decimal(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def _intent(row: BrokerOrderIntentRow) -> BrokerOrderIntent:
    payload = json.loads(row.order_payload_json)
    if not isinstance(payload, dict):
        raise PersistenceError("Broker order payload is not an object")
    return BrokerOrderIntent(
        order_intent_id=row.order_intent_id,
        account_ref=row.account_ref,
        instrument_id=row.instrument_id,
        symbol=row.symbol,
        instruction=BrokerOrderInstruction(row.instruction),
        quantity=row.quantity,
        order_type=BrokerOrderType(row.order_type),
        session=BrokerOrderSession(row.session),
        duration=BrokerOrderDuration(row.duration),
        limit_price=_decimal(row.limit_price),
        stop_price=_decimal(row.stop_price),
        trail_offset=_decimal(row.trail_offset),
        trail_type=BrokerTrailType(row.trail_type) if row.trail_type else None,
        limit_offset=_decimal(row.limit_offset),
        payload_sha256=row.payload_sha256,
        order_payload=cast(dict[str, object], payload),
        preview_idempotency_key=row.preview_idempotency_key,
        created_at=datetime.fromisoformat(row.created_at),
        expires_at=datetime.fromisoformat(row.expires_at),
        account_observed_at=datetime.fromisoformat(row.account_observed_at),
        cash_balance=_decimal(row.cash_balance),
        margin_balance=_decimal(row.margin_balance),
        open_buy_order_reserve=_decimal(row.open_buy_order_reserve),
        position_quantity=Decimal(row.position_quantity),
        quote_at=datetime.fromisoformat(row.quote_at) if row.quote_at else None,
        quote_source=row.quote_source,
        quote_price=_decimal(row.quote_price),
        estimated_notional=_decimal(row.estimated_notional),
        status=BrokerOrderIntentStatus(row.status),
        submit_idempotency_key=row.submit_idempotency_key,
        confirmed_by=row.confirmed_by,
        submitted_via=row.submitted_via,
        authorization_note=row.authorization_note,
        broker_order_id=row.broker_order_id,
        submitted_at=datetime.fromisoformat(row.submitted_at) if row.submitted_at else None,
        provider_status=row.provider_status,
        rejection_code=row.rejection_code,
        updated_at=datetime.fromisoformat(row.updated_at),
    )


class SqlAlchemyBrokerOrderRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create_preview(self, value: BrokerOrderIntent) -> BrokerOrderIntent:
        existing = self.get_by_preview_idempotency_key(value.preview_idempotency_key)
        if existing is not None:
            if existing.payload_sha256 == value.payload_sha256:
                return existing
            raise IdempotencyConflict("Broker preview idempotency key was reused")
        row = BrokerOrderIntentRow(
            order_intent_id=value.order_intent_id,
            account_ref=value.account_ref,
            instrument_id=value.instrument_id,
            symbol=value.symbol,
            instruction=value.instruction.value,
            quantity=value.quantity,
            order_type=value.order_type.value,
            session=value.session.value,
            duration=value.duration.value,
            limit_price=str(value.limit_price) if value.limit_price is not None else None,
            stop_price=str(value.stop_price) if value.stop_price is not None else None,
            trail_offset=str(value.trail_offset) if value.trail_offset is not None else None,
            trail_type=value.trail_type.value if value.trail_type else None,
            limit_offset=str(value.limit_offset) if value.limit_offset is not None else None,
            payload_sha256=value.payload_sha256,
            order_payload_json=json.dumps(
                value.order_payload, sort_keys=True, separators=(",", ":"), default=str
            ),
            preview_idempotency_key=value.preview_idempotency_key,
            created_at=value.created_at.isoformat(),
            expires_at=value.expires_at.isoformat(),
            account_observed_at=value.account_observed_at.isoformat(),
            cash_balance=(str(value.cash_balance) if value.cash_balance is not None else None),
            margin_balance=(
                str(value.margin_balance) if value.margin_balance is not None else None
            ),
            open_buy_order_reserve=(
                str(value.open_buy_order_reserve)
                if value.open_buy_order_reserve is not None
                else None
            ),
            position_quantity=str(value.position_quantity),
            quote_at=value.quote_at.isoformat() if value.quote_at else None,
            quote_source=value.quote_source,
            quote_price=str(value.quote_price) if value.quote_price is not None else None,
            estimated_notional=(
                str(value.estimated_notional) if value.estimated_notional is not None else None
            ),
            status=value.status.value,
            updated_at=(value.updated_at or value.created_at).isoformat(),
        )
        try:
            with Session(self._engine) as session:
                session.add(row)
                session.commit()
                session.refresh(row)
                return _intent(row)
        except IntegrityError as exc:
            replay = self.get_by_preview_idempotency_key(value.preview_idempotency_key)
            if replay is not None and replay.payload_sha256 == value.payload_sha256:
                return replay
            raise IdempotencyConflict("Broker preview idempotency key was reused") from exc

    def get(self, order_intent_id: str) -> BrokerOrderIntent | None:
        with Session(self._engine) as session:
            row = session.get(BrokerOrderIntentRow, order_intent_id)
            return _intent(row) if row is not None else None

    def get_by_preview_idempotency_key(self, key: str) -> BrokerOrderIntent | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(BrokerOrderIntentRow).where(
                    BrokerOrderIntentRow.preview_idempotency_key == key
                )
            )
            return _intent(row) if row is not None else None

    def claim_submit(
        self,
        *,
        order_intent_id: str,
        now: datetime,
        submit_idempotency_key: str,
        confirmed_by: str,
        submitted_via: str,
        authorization_note: str,
    ) -> tuple[BrokerOrderIntent, bool]:
        current = self.get(order_intent_id)
        if current is None:
            raise BrokerOrderNotFound("Broker order intent was not found")
        if current.submit_idempotency_key == submit_idempotency_key:
            return current, False
        if current.status is not BrokerOrderIntentStatus.PREVIEWED:
            raise BrokerOrderStateConflict(
                "Broker order preview is no longer submit-capable",
                details={"status": current.status.value},
            )
        if now > current.expires_at:
            raise BrokerOrderPreviewExpired("Broker order preview has expired")
        try:
            with Session(self._engine) as session:
                result = session.execute(
                    update(BrokerOrderIntentRow)
                    .where(
                        BrokerOrderIntentRow.order_intent_id == order_intent_id,
                        BrokerOrderIntentRow.status == BrokerOrderIntentStatus.PREVIEWED.value,
                        BrokerOrderIntentRow.submit_idempotency_key.is_(None),
                    )
                    .values(
                        status=BrokerOrderIntentStatus.SUBMITTING.value,
                        submit_idempotency_key=submit_idempotency_key,
                        confirmed_by=confirmed_by,
                        submitted_via=submitted_via,
                        authorization_note=authorization_note,
                        updated_at=now.isoformat(),
                    )
                )
                if result.rowcount != 1:  # type: ignore[attr-defined]
                    session.rollback()
                    replay = self.get(order_intent_id)
                    if (
                        replay is not None
                        and replay.submit_idempotency_key == submit_idempotency_key
                    ):
                        return replay, False
                    raise BrokerOrderStateConflict("Broker order submit was claimed elsewhere")
                session.commit()
        except IntegrityError as exc:
            raise IdempotencyConflict("Broker submit idempotency key was reused") from exc
        claimed = self.get(order_intent_id)
        assert claimed is not None
        return claimed, True

    def _mark(
        self,
        *,
        order_intent_id: str,
        now: datetime,
        status: BrokerOrderIntentStatus | None,
        **values: object,
    ) -> BrokerOrderIntent:
        update_values: dict[str, object] = {"updated_at": now.isoformat(), **values}
        if status is not None:
            update_values["status"] = status.value
        with Session(self._engine) as session:
            result = session.execute(
                update(BrokerOrderIntentRow)
                .where(BrokerOrderIntentRow.order_intent_id == order_intent_id)
                .values(**update_values)
            )
            if result.rowcount != 1:  # type: ignore[attr-defined]
                raise BrokerOrderNotFound("Broker order intent was not found")
            session.commit()
        value = self.get(order_intent_id)
        assert value is not None
        return value

    def mark_submitted(
        self,
        *,
        order_intent_id: str,
        broker_order_id: str,
        submitted_at: datetime,
        provider_status: str,
    ) -> BrokerOrderIntent:
        return self._mark(
            order_intent_id=order_intent_id,
            now=submitted_at,
            status=BrokerOrderIntentStatus.SUBMITTED,
            broker_order_id=broker_order_id,
            submitted_at=submitted_at.isoformat(),
            provider_status=provider_status,
        )

    def mark_rejected(self, *, order_intent_id: str, code: str, now: datetime) -> BrokerOrderIntent:
        return self._mark(
            order_intent_id=order_intent_id,
            now=now,
            status=BrokerOrderIntentStatus.REJECTED,
            rejection_code=code,
        )

    def mark_unknown(self, *, order_intent_id: str, code: str, now: datetime) -> BrokerOrderIntent:
        return self._mark(
            order_intent_id=order_intent_id,
            now=now,
            status=BrokerOrderIntentStatus.UNKNOWN,
            rejection_code=code,
        )

    def mark_cancelled(self, *, order_intent_id: str, now: datetime) -> BrokerOrderIntent:
        """Persist acceptance of a cancel request, not completion of cancellation.

        The provider cancel endpoint is asynchronous from the order-intent
        perspective.  A successful request therefore remains
        ``CANCEL_REQUESTED`` until a later provider status observation confirms
        ``CANCELED``/``CANCELLED``.
        """
        return self.mark_cancel_requested(order_intent_id=order_intent_id, now=now)

    def mark_cancel_requested(
        self, *, order_intent_id: str, now: datetime
    ) -> BrokerOrderIntent:
        return self._mark(
            order_intent_id=order_intent_id,
            now=now,
            status=BrokerOrderIntentStatus.CANCEL_REQUESTED,
            provider_status="CANCEL_REQUEST_ACCEPTED",
        )

    def record_provider_observation(
        self,
        *,
        order_intent_id: str,
        provider_status: str,
        now: datetime,
        status: BrokerOrderIntentStatus | None = None,
    ) -> BrokerOrderIntent:
        """Persist a bounded provider status observation without raw payloads.

        Unless a caller explicitly supplies a domain status transition (for
        example, a provider ``CANCELED`` observation), the durable intent
        status is preserved.  This keeps provider terminal statuses such as
        ``FILLED`` or ``REJECTED`` from being mistaken for an intent lifecycle
        transition.
        """
        values: dict[str, object] = {"provider_status": provider_status}
        return self._mark(
            order_intent_id=order_intent_id,
            now=now,
            # A plain observation updates the provider receipt atomically and
            # preserves whatever intent status a concurrent cancel/submit
            # operation committed.  An explicit status is only used for a
            # provider lifecycle transition such as cancellation confirmation.
            status=status,
            **values,
        )

    def list_unresolved(self, limit: int = 100) -> tuple[BrokerOrderIntent, ...]:
        """Return unresolved durable intents, newest updates first.

        ``UNKNOWN`` submit outcomes and accepted-but-unconfirmed cancellation
        requests require operator attention.  The query is read-only and
        deliberately does not contact a provider or retry either state.
        """
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer in [1,500]")
        with Session(self._engine) as session:
            rows = session.scalars(
                select(BrokerOrderIntentRow)
                .where(
                    BrokerOrderIntentRow.status.in_(
                        (
                            BrokerOrderIntentStatus.SUBMITTING.value,
                            BrokerOrderIntentStatus.UNKNOWN.value,
                            BrokerOrderIntentStatus.CANCEL_REQUESTED.value,
                        )
                    )
                )
                .order_by(
                    BrokerOrderIntentRow.updated_at.desc(),
                    BrokerOrderIntentRow.created_at.desc(),
                    BrokerOrderIntentRow.order_intent_id.desc(),
                )
                .limit(limit)
            ).all()
            return tuple(_intent(row) for row in rows)
