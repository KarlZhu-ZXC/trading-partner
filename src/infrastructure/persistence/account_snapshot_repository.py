"""SQLAlchemy append-only account and portfolio snapshot repository."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from domain.common.enums import VendorId
from domain.common.errors import PersistenceError
from domain.portfolio.enums import (
    AccountEnvironment,
    AccountOpenOrderSide,
    AccountOpenOrderStatus,
    AccountPositionSide,
)
from domain.portfolio.models import (
    AccountOpenOrder,
    AccountPosition,
    AccountSnapshot,
    PortfolioExposure,
    PortfolioSnapshot,
)
from infrastructure.persistence.models import (
    AccountPositionRow,
    AccountSnapshotRow,
    PortfolioSnapshotRow,
)


def _number(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _orders(value: tuple[AccountOpenOrder, ...]) -> str:
    return json.dumps(
        [
            {
                "provider_order_id": item.provider_order_id,
                "instrument_id": item.instrument_id,
                "side": item.side.value,
                "status": item.status.value,
                "quantity": str(item.quantity),
                "filled_quantity": str(item.filled_quantity),
                "limit_price": _number(item.limit_price),
                "submitted_at": item.submitted_at.isoformat() if item.submitted_at else None,
            }
            for item in value
        ],
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _integrity_diagnostic(
    exc: IntegrityError,
    *,
    entity: str,
) -> tuple[str, str, bool]:
    """Classify only known constraint names; never expose raw SQL or values."""
    raw = str(exc.orig).lower()
    if entity == "account_snapshot":
        if "account_snapshots.snapshot_id" in raw:
            return "account snapshot id conflict", "snapshot_id", True
        if "account_snapshots.fingerprint" in raw:
            return (
                "account snapshot fingerprint conflict is not yet visible",
                "fingerprint_concurrent_insert",
                True,
            )
        if "account_positions.snapshot_id, account_positions.instrument_id" in raw:
            return "account position identity conflict", "position_identity", False
        if "ck_account_snapshots_degraded" in raw:
            return "account snapshot validation constraint failed", "check_constraint", False
        if "foreign key constraint failed" in raw:
            return "account snapshot relational integrity failed", "foreign_key", False
        return "account snapshot integrity constraint failed", "unknown_integrity", False
    if "portfolio_snapshots.portfolio_snapshot_id" in raw:
        return "portfolio snapshot id conflict", "snapshot_id", True
    if "portfolio_snapshots.fingerprint" in raw:
        return (
            "portfolio snapshot fingerprint conflict is not yet visible",
            "fingerprint_concurrent_insert",
            True,
        )
    if "ck_portfolio_snapshots_degraded" in raw:
        return "portfolio snapshot validation constraint failed", "check_constraint", False
    if "foreign key constraint failed" in raw:
        return "portfolio snapshot relational integrity failed", "foreign_key", False
    return "portfolio snapshot integrity constraint failed", "unknown_integrity", False


def _persistence_integrity_error(
    exc: IntegrityError,
    *,
    entity: str,
) -> PersistenceError:
    message, conflict_type, retryable = _integrity_diagnostic(exc, entity=entity)
    return PersistenceError(
        message,
        details={"entity": entity, "conflict_type": conflict_type},
        retryable=retryable,
    )


class SqlAlchemyAccountSnapshotRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append_account(self, snapshot: AccountSnapshot) -> AccountSnapshot:
        fingerprint = self._account_fingerprint(snapshot)
        try:
            with Session(self._engine) as session, session.begin():
                session.add(
                    AccountSnapshotRow(
                        snapshot_id=snapshot.snapshot_id,
                        fingerprint=fingerprint,
                        account_ref=snapshot.account_ref,
                        provider=snapshot.provider.value,
                        environment=snapshot.environment.value,
                        base_currency=snapshot.base_currency,
                        account_as_of=snapshot.account_as_of.isoformat(),
                        fetched_at=snapshot.fetched_at.isoformat(),
                        cash=_number(snapshot.cash),
                        buying_power=_number(snapshot.buying_power),
                        net_assets=_number(snapshot.net_assets),
                        margin_used=_number(snapshot.margin_used),
                        open_orders_json=_orders(snapshot.open_orders),
                        degraded=int(snapshot.degraded),
                        warning_codes_json=json.dumps(snapshot.warning_codes),
                    )
                )
                # These append-only rows deliberately have no ORM relationship.
                # Flush the parent so SQLite cannot insert positions before it.
                session.flush()
                session.add_all(
                    [
                        AccountPositionRow(
                            snapshot_id=snapshot.snapshot_id,
                            instrument_id=item.instrument_id,
                            side=item.side.value,
                            quantity=str(item.quantity),
                            sellable_quantity=_number(item.sellable_quantity),
                            average_cost=_number(item.average_cost),
                            diluted_cost=_number(item.diluted_cost),
                            market_price=_number(item.market_price),
                            market_price_at=item.market_price_at.isoformat()
                            if item.market_price_at
                            else None,
                            market_value=_number(item.market_value),
                            unrealized_pnl=_number(item.unrealized_pnl),
                            realized_pnl=_number(item.realized_pnl),
                            currency=item.currency,
                        )
                        for item in snapshot.positions
                    ]
                )
            return snapshot
        except IntegrityError as exc:
            with Session(self._engine) as session:
                row = session.scalar(
                    select(AccountSnapshotRow).where(AccountSnapshotRow.fingerprint == fingerprint)
                )
                if row is not None:
                    return self._account(session, row)
            raise _persistence_integrity_error(
                exc,
                entity="account_snapshot",
            ) from None

    def get_account(self, snapshot_id: str) -> AccountSnapshot | None:
        with Session(self._engine) as session:
            row = session.get(AccountSnapshotRow, snapshot_id)
            return None if row is None else self._account(session, row)

    def latest_accounts(self) -> tuple[AccountSnapshot, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(AccountSnapshotRow).order_by(
                    AccountSnapshotRow.account_ref,
                    AccountSnapshotRow.account_as_of.desc(),
                )
            )
            latest: dict[str, AccountSnapshot] = {}
            for row in rows:
                if row.account_ref not in latest:
                    latest[row.account_ref] = self._account(session, row)
            return tuple(latest.values())

    def append_portfolio(self, snapshot: PortfolioSnapshot) -> PortfolioSnapshot:
        payload = self._portfolio_payload(snapshot)
        fingerprint = _fingerprint(payload)
        try:
            with Session(self._engine) as session, session.begin():
                session.add(
                    PortfolioSnapshotRow(
                        portfolio_snapshot_id=snapshot.portfolio_snapshot_id,
                        fingerprint=fingerprint,
                        account_snapshot_ids_json=json.dumps(snapshot.account_snapshot_ids),
                        as_of=snapshot.as_of.isoformat(),
                        base_currency=snapshot.base_currency,
                        total_value=_number(snapshot.total_value),
                        exposures_json=json.dumps(payload["exposures"]),
                        missing_instrument_ids_json=json.dumps(snapshot.missing_instrument_ids),
                        degraded=int(snapshot.degraded),
                        warning_codes_json=json.dumps(snapshot.warning_codes),
                    )
                )
            return snapshot
        except IntegrityError as exc:
            with Session(self._engine) as session:
                row = session.scalar(
                    select(PortfolioSnapshotRow).where(
                        PortfolioSnapshotRow.fingerprint == fingerprint
                    )
                )
                if row is not None:
                    return self._portfolio(row)
            raise _persistence_integrity_error(
                exc,
                entity="portfolio_snapshot",
            ) from None

    def get_portfolio(self, snapshot_id: str) -> PortfolioSnapshot | None:
        with Session(self._engine) as session:
            row = session.get(PortfolioSnapshotRow, snapshot_id)
            return None if row is None else self._portfolio(row)

    @staticmethod
    def _account_fingerprint(snapshot: AccountSnapshot) -> str:
        return _fingerprint(
            {
                "account_ref": snapshot.account_ref,
                "provider": snapshot.provider.value,
                "account_as_of": snapshot.account_as_of.isoformat(),
                "positions": [
                    [item.instrument_id, str(item.quantity), _number(item.market_value)]
                    for item in snapshot.positions
                ],
            }
        )

    @staticmethod
    def _account(session: Session, row: AccountSnapshotRow) -> AccountSnapshot:
        positions = session.scalars(
            select(AccountPositionRow)
            .where(AccountPositionRow.snapshot_id == row.snapshot_id)
            .order_by(AccountPositionRow.instrument_id)
        )
        raw_orders = json.loads(row.open_orders_json)
        return AccountSnapshot(
            snapshot_id=row.snapshot_id,
            account_ref=row.account_ref,
            provider=VendorId(row.provider),
            environment=AccountEnvironment(row.environment),
            base_currency=row.base_currency,
            account_as_of=datetime.fromisoformat(row.account_as_of),
            fetched_at=datetime.fromisoformat(row.fetched_at),
            cash=_decimal(row.cash),
            buying_power=_decimal(row.buying_power),
            net_assets=_decimal(row.net_assets),
            margin_used=_decimal(row.margin_used),
            positions=tuple(
                AccountPosition(
                    item.instrument_id,
                    AccountPositionSide(item.side),
                    Decimal(item.quantity),
                    _decimal(item.sellable_quantity),
                    _decimal(item.average_cost),
                    _decimal(item.diluted_cost),
                    _decimal(item.market_price),
                    datetime.fromisoformat(item.market_price_at) if item.market_price_at else None,
                    _decimal(item.market_value),
                    _decimal(item.unrealized_pnl),
                    _decimal(item.realized_pnl),
                    item.currency,
                )
                for item in positions
            ),
            open_orders=tuple(
                AccountOpenOrder(
                    item["provider_order_id"],
                    item["instrument_id"],
                    AccountOpenOrderSide(item["side"]),
                    AccountOpenOrderStatus(item["status"]),
                    Decimal(item["quantity"]),
                    Decimal(item["filled_quantity"]),
                    _decimal(item["limit_price"]),
                    datetime.fromisoformat(item["submitted_at"]) if item["submitted_at"] else None,
                )
                for item in raw_orders
            ),
            degraded=bool(row.degraded),
            warning_codes=tuple(json.loads(row.warning_codes_json)),
        )

    @staticmethod
    def _portfolio_payload(snapshot: PortfolioSnapshot) -> dict[str, object]:
        return {
            "account_snapshot_ids": snapshot.account_snapshot_ids,
            "as_of": snapshot.as_of.isoformat(),
            "base_currency": snapshot.base_currency,
            "total_value": _number(snapshot.total_value),
            "exposures": [
                [item.dimension, item.key, str(item.value), _number(item.weight)]
                for item in snapshot.exposures
            ],
            "missing": snapshot.missing_instrument_ids,
        }

    @staticmethod
    def _portfolio(row: PortfolioSnapshotRow) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            portfolio_snapshot_id=row.portfolio_snapshot_id,
            account_snapshot_ids=tuple(json.loads(row.account_snapshot_ids_json)),
            as_of=datetime.fromisoformat(row.as_of),
            base_currency=row.base_currency,
            total_value=_decimal(row.total_value),
            exposures=tuple(
                PortfolioExposure(item[0], item[1], Decimal(item[2]), _decimal(item[3]))
                for item in json.loads(row.exposures_json)
            ),
            missing_instrument_ids=tuple(json.loads(row.missing_instrument_ids_json)),
            degraded=bool(row.degraded),
            warning_codes=tuple(json.loads(row.warning_codes_json)),
        )
