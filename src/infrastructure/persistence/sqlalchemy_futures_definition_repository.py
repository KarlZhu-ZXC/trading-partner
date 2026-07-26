"""SQLAlchemy append-only futures definition repository (migration 0017).

Product/contract identity rows are created once. Definition versions and
continuous mappings are append-only: never update or delete prior versions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from application.ports.futures_definition_repository import (
    FuturesDefinitionBatch,
    FuturesDefinitionRepository,
)
from domain.common.enums import Market
from domain.common.errors import DataContractError, PersistenceError
from domain.common.time import require_aware_datetime
from domain.cross_asset.enums import (
    ContinuousAdjustment,
    ContractLifecycleStatus,
    RollRule,
    SettlementMethod,
)
from domain.cross_asset.futures_models import (
    ContinuousContractMapping,
    ContinuousSeriesDefinition,
    FuturesContractDefinition,
    FuturesContractStatistics,
    FuturesProductDefinition,
)
from infrastructure.persistence.models import (
    ContinuousContractMappingRow,
    ContinuousSeriesDefinitionRow,
    FuturesContractRow,
    FuturesContractStatisticsRow,
    FuturesContractVersionRow,
    FuturesProductRow,
    FuturesProductVersionRow,
)
from infrastructure.persistence.repositories._mapping import (
    date_from_db,
    date_to_db,
    decimal_from_db,
    decimal_to_db,
    dt_from_db,
    dt_opt_from_db,
    dt_opt_to_db,
    dt_to_db,
)


def _utc(value: datetime) -> datetime:
    return require_aware_datetime(value).astimezone(UTC)


def _utc_text(value: datetime) -> str:
    return dt_to_db(_utc(value))


def _require_decimal(value: str | None, *, field: str) -> Decimal:
    converted = decimal_from_db(value)
    if converted is None:
        raise PersistenceError(
            "stored futures definition has null decimal",
            details={"field": field},
            retryable=False,
        )
    return converted


class SqlAlchemyFuturesDefinitionRepository:
    """Append-only implementation of :class:`FuturesDefinitionRepository`."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_product(
        self,
        product_key: str,
        as_of: datetime,
    ) -> FuturesProductDefinition | None:
        as_of_text = _utc_text(as_of)
        with Session(self._engine) as session:
            product_row = session.scalar(
                select(FuturesProductRow).where(
                    FuturesProductRow.product_key == product_key
                )
            )
            if product_row is None:
                return None
            version_row = session.scalar(
                select(FuturesProductVersionRow)
                .where(
                    FuturesProductVersionRow.product_id == product_row.product_id,
                    FuturesProductVersionRow.valid_from <= as_of_text,
                    or_(
                        FuturesProductVersionRow.valid_to.is_(None),
                        FuturesProductVersionRow.valid_to > as_of_text,
                    ),
                )
                .order_by(
                    FuturesProductVersionRow.version.desc(),
                    FuturesProductVersionRow.valid_from.desc(),
                )
                .limit(1)
            )
            if version_row is None:
                return None
            return self._product_domain(product_row, version_row)

    def list_contracts(
        self,
        product_id: str,
        as_of: datetime,
    ) -> tuple[FuturesContractDefinition, ...]:
        as_of_text = _utc_text(as_of)
        with Session(self._engine) as session:
            contract_rows = tuple(
                session.scalars(
                    select(FuturesContractRow)
                    .where(FuturesContractRow.product_id == product_id)
                    .order_by(FuturesContractRow.contract_month)
                )
            )
            results: list[FuturesContractDefinition] = []
            for contract_row in contract_rows:
                version_row = session.scalar(
                    select(FuturesContractVersionRow)
                    .where(
                        FuturesContractVersionRow.instrument_id
                        == contract_row.instrument_id,
                        FuturesContractVersionRow.definition_as_of <= as_of_text,
                    )
                    .order_by(
                        FuturesContractVersionRow.version.desc(),
                        FuturesContractVersionRow.definition_as_of.desc(),
                    )
                    .limit(1)
                )
                if version_row is None:
                    continue
                results.append(self._contract_domain(contract_row, version_row))
            results.sort(
                key=lambda c: (
                    c.expiration_at.isoformat()
                    if c.expiration_at is not None
                    else c.contract_month,
                    c.instrument_id,
                )
            )
            return tuple(results)

    def get_continuous_series(
        self,
        instrument_id: str,
        as_of: datetime,
    ) -> ContinuousSeriesDefinition | None:
        as_of_text = _utc_text(as_of)
        with Session(self._engine) as session:
            row = session.get(ContinuousSeriesDefinitionRow, instrument_id)
            if row is None:
                return None
            if row.valid_from > as_of_text:
                return None
            if row.valid_to is not None and row.valid_to <= as_of_text:
                return None
            return self._series_domain(row)

    def list_continuous_mappings(
        self,
        continuous_instrument_id: str,
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[ContinuousContractMapping, ...]:
        start_text = _utc_text(start)
        end_text = _utc_text(end)
        if end_text < start_text:
            raise DataContractError(
                "end must be >= start",
                details={"field": "end", "rule": "range_order"},
            )
        with Session(self._engine) as session:
            rows = tuple(
                session.scalars(
                    select(ContinuousContractMappingRow)
                    .where(
                        ContinuousContractMappingRow.continuous_instrument_id
                        == continuous_instrument_id,
                        ContinuousContractMappingRow.effective_from <= end_text,
                        or_(
                            ContinuousContractMappingRow.effective_to.is_(None),
                            ContinuousContractMappingRow.effective_to > start_text,
                        ),
                    )
                    .order_by(ContinuousContractMappingRow.effective_from)
                )
            )
            return tuple(self._mapping_domain(row) for row in rows)

    def save_definition_batch(self, batch: FuturesDefinitionBatch) -> None:
        if not isinstance(batch, FuturesDefinitionBatch):
            raise DataContractError(
                "batch must be FuturesDefinitionBatch",
                details={"field": "batch"},
            )
        now = datetime.now(UTC)
        try:
            with Session(self._engine) as session, session.begin():
                for product in batch.products:
                    self._append_product(session, product, now=now)
                for contract in batch.contracts:
                    self._append_contract(session, contract, now=now)
                for series in batch.continuous_series:
                    self._append_series(session, series, now=now)
                for mapping in batch.mappings:
                    self._append_mapping(session, mapping)
        except IntegrityError as exc:
            raise PersistenceError(
                "futures definition persistence conflict",
                details={"cause": type(exc.orig).__name__},
                retryable=True,
            ) from exc

    def save_statistics(
        self,
        statistics: tuple[FuturesContractStatistics, ...],
    ) -> int:
        """Append explicit EOD observations; exact repeated syncs are idempotent."""
        now = datetime.now(UTC)
        inserted = 0
        try:
            with Session(self._engine) as session, session.begin():
                for item in statistics:
                    key = (
                        item.instrument_id,
                        date_to_db(item.trade_date),
                        _utc_text(item.published_at),
                        item.source,
                    )
                    existing = session.get(FuturesContractStatisticsRow, key)
                    if existing is not None:
                        if not self._statistics_matches(existing, item):
                            raise PersistenceError(
                                "futures statistics vintage conflicts with store",
                                details={
                                    "instrument_id": item.instrument_id,
                                    "trade_date": item.trade_date.isoformat(),
                                },
                                retryable=False,
                            )
                        continue
                    session.add(
                        FuturesContractStatisticsRow(
                            instrument_id=item.instrument_id,
                            trade_date=date_to_db(item.trade_date) or "",
                            published_at=_utc_text(item.published_at),
                            source=item.source,
                            settlement=decimal_to_db(item.settlement),
                            settlement_status=item.settlement_status.value,
                            session_volume=decimal_to_db(item.session_volume),
                            open_interest=decimal_to_db(item.open_interest),
                            recorded_at=_utc_text(now),
                        )
                    )
                    inserted += 1
        except IntegrityError as exc:
            raise PersistenceError(
                "futures statistics persistence conflict",
                details={"cause": type(exc.orig).__name__},
                retryable=True,
            ) from exc
        return inserted

    def _append_product(
        self,
        session: Session,
        product: FuturesProductDefinition,
        *,
        now: datetime,
    ) -> None:
        existing = session.get(FuturesProductRow, product.product_id)
        if existing is None:
            by_key = session.scalar(
                select(FuturesProductRow).where(
                    FuturesProductRow.product_key == product.product_key
                )
            )
            if by_key is not None and by_key.product_id != product.product_id:
                raise PersistenceError(
                    "product_key already bound to a different product_id",
                    details={
                        "product_key": product.product_key,
                        "existing_product_id": by_key.product_id,
                    },
                    retryable=False,
                )
            session.add(
                FuturesProductRow(
                    product_id=product.product_id,
                    product_key=product.product_key,
                    market=product.market.value,
                    root=product.root,
                    created_at=_utc_text(now),
                )
            )
        elif (
            existing.product_key != product.product_key
            or existing.root != product.root
            or existing.market != product.market.value
        ):
            raise PersistenceError(
                "futures product identity is immutable",
                details={"product_id": product.product_id},
                retryable=False,
            )

        # Append a new version only when version number is new.
        existing_version = session.scalar(
            select(FuturesProductVersionRow).where(
                FuturesProductVersionRow.product_id == product.product_id,
                FuturesProductVersionRow.version == product.version,
            )
        )
        if existing_version is not None:
            # Idempotent: identical content OK; drift is a conflict.
            if not self._product_version_matches(existing_version, product):
                raise PersistenceError(
                    "futures product version content conflicts with store",
                    details={
                        "product_id": product.product_id,
                        "version": product.version,
                    },
                    retryable=False,
                )
            return
        version_id = product.version_id
        if version_id is None:
            raise DataContractError(
                "product version_id is required for persistence",
                details={"product_id": product.product_id},
            )
        session.add(
            FuturesProductVersionRow(
                version_id=version_id,
                product_id=product.product_id,
                version=product.version,
                exchange=product.exchange,
                commodity=product.commodity,
                currency=product.currency,
                price_unit=product.price_unit,
                multiplier=decimal_to_db(product.multiplier) or "",
                tick_size=decimal_to_db(product.tick_size) or "",
                settlement_method=product.settlement_method.value,
                session_calendar_id=product.session_calendar_id,
                source=product.source,
                valid_from=_utc_text(product.valid_from),
                valid_to=dt_opt_to_db(
                    product.valid_to.astimezone(UTC) if product.valid_to else None
                ),
                definition_as_of=_utc_text(product.definition_as_of),
            )
        )

    def _append_contract(
        self,
        session: Session,
        contract: FuturesContractDefinition,
        *,
        now: datetime,
    ) -> None:
        existing = session.get(FuturesContractRow, contract.instrument_id)
        if existing is None:
            session.add(
                FuturesContractRow(
                    instrument_id=contract.instrument_id,
                    product_id=contract.product_id,
                    contract_month=contract.contract_month,
                    created_at=_utc_text(now),
                )
            )
        elif (
            existing.product_id != contract.product_id
            or existing.contract_month != contract.contract_month
        ):
            raise PersistenceError(
                "futures contract identity is immutable",
                details={"instrument_id": contract.instrument_id},
                retryable=False,
            )

        existing_version = session.scalar(
            select(FuturesContractVersionRow).where(
                FuturesContractVersionRow.instrument_id == contract.instrument_id,
                FuturesContractVersionRow.version == contract.version,
            )
        )
        if existing_version is not None:
            if not self._contract_version_matches(existing_version, contract):
                raise PersistenceError(
                    "futures contract version content conflicts with store",
                    details={
                        "instrument_id": contract.instrument_id,
                        "version": contract.version,
                    },
                    retryable=False,
                )
            return
        version_id = contract.version_id
        if version_id is None:
            # Auto-stable version id is not invented here — service must mint.
            raise DataContractError(
                "contract version_id is required for persistence",
                details={"instrument_id": contract.instrument_id},
            )
        session.add(
            FuturesContractVersionRow(
                version_id=version_id,
                instrument_id=contract.instrument_id,
                version=contract.version,
                listed_at=dt_opt_to_db(
                    contract.listed_at.astimezone(UTC) if contract.listed_at else None
                ),
                first_trade_at=dt_opt_to_db(
                    contract.first_trade_at.astimezone(UTC)
                    if contract.first_trade_at
                    else None
                ),
                last_trade_at=dt_opt_to_db(
                    contract.last_trade_at.astimezone(UTC)
                    if contract.last_trade_at
                    else None
                ),
                expiration_at=dt_opt_to_db(
                    contract.expiration_at.astimezone(UTC)
                    if contract.expiration_at
                    else None
                ),
                first_notice_at=dt_opt_to_db(
                    contract.first_notice_at.astimezone(UTC)
                    if contract.first_notice_at
                    else None
                ),
                delivery_start=date_to_db(contract.delivery_start),
                delivery_end=date_to_db(contract.delivery_end),
                settlement_at=dt_opt_to_db(
                    contract.settlement_at.astimezone(UTC)
                    if contract.settlement_at
                    else None
                ),
                status=contract.status.value,
                definition_as_of=_utc_text(contract.definition_as_of),
                source=contract.source,
            )
        )

    def _append_series(
        self,
        session: Session,
        series: ContinuousSeriesDefinition,
        *,
        now: datetime,
    ) -> None:
        existing = session.get(ContinuousSeriesDefinitionRow, series.instrument_id)
        if existing is not None:
            if (
                existing.product_id != series.product_id
                or existing.roll_rule != series.roll_rule.value
                or existing.rank != series.rank
                or existing.adjustment != series.adjustment.value
                or existing.provider_methodology_version
                != series.provider_methodology_version
            ):
                raise PersistenceError(
                    "continuous series identity is immutable",
                    details={"instrument_id": series.instrument_id},
                    retryable=False,
                )
            return
        session.add(
            ContinuousSeriesDefinitionRow(
                instrument_id=series.instrument_id,
                product_id=series.product_id,
                roll_rule=series.roll_rule.value,
                rank=series.rank,
                adjustment=series.adjustment.value,
                provider_methodology_version=series.provider_methodology_version,
                valid_from=_utc_text(series.valid_from),
                valid_to=dt_opt_to_db(
                    series.valid_to.astimezone(UTC) if series.valid_to else None
                ),
                created_at=_utc_text(now),
            )
        )

    def _append_mapping(
        self,
        session: Session,
        mapping: ContinuousContractMapping,
    ) -> None:
        existing = session.scalar(
            select(ContinuousContractMappingRow).where(
                ContinuousContractMappingRow.continuous_instrument_id
                == mapping.continuous_instrument_id,
                ContinuousContractMappingRow.effective_from
                == _utc_text(mapping.effective_from),
            )
        )
        if existing is not None:
            if (
                existing.contract_instrument_id != mapping.contract_instrument_id
                or existing.mapping_source != mapping.mapping_source
                or (existing.effective_to or None)
                != (
                    dt_opt_to_db(
                        mapping.effective_to.astimezone(UTC)
                        if mapping.effective_to
                        else None
                    )
                )
            ):
                raise PersistenceError(
                    "continuous mapping content conflicts with store",
                    details={
                        "continuous_instrument_id": mapping.continuous_instrument_id,
                        "effective_from": mapping.effective_from.isoformat(),
                    },
                    retryable=False,
                )
            return
        session.add(
            ContinuousContractMappingRow(
                continuous_instrument_id=mapping.continuous_instrument_id,
                contract_instrument_id=mapping.contract_instrument_id,
                effective_from=_utc_text(mapping.effective_from),
                effective_to=dt_opt_to_db(
                    mapping.effective_to.astimezone(UTC)
                    if mapping.effective_to
                    else None
                ),
                mapping_source=mapping.mapping_source,
            )
        )

    @staticmethod
    def _product_version_matches(
        row: FuturesProductVersionRow,
        product: FuturesProductDefinition,
    ) -> bool:
        return (
            row.exchange == product.exchange
            and row.commodity == product.commodity
            and row.currency == product.currency
            and row.price_unit == product.price_unit
            and row.multiplier == (decimal_to_db(product.multiplier) or "")
            and row.tick_size == (decimal_to_db(product.tick_size) or "")
            and row.settlement_method == product.settlement_method.value
            and row.session_calendar_id == product.session_calendar_id
            and row.source == product.source
        )

    @staticmethod
    def _contract_version_matches(
        row: FuturesContractVersionRow,
        contract: FuturesContractDefinition,
    ) -> bool:
        return (
            row.status == contract.status.value
            and row.source == contract.source
            and row.definition_as_of == _utc_text(contract.definition_as_of)
        )

    @staticmethod
    def _statistics_matches(
        row: FuturesContractStatisticsRow,
        item: FuturesContractStatistics,
    ) -> bool:
        return (
            row.settlement == decimal_to_db(item.settlement)
            and row.settlement_status == item.settlement_status.value
            and row.session_volume == decimal_to_db(item.session_volume)
            and row.open_interest == decimal_to_db(item.open_interest)
        )

    @staticmethod
    def _product_domain(
        product: FuturesProductRow,
        version: FuturesProductVersionRow,
    ) -> FuturesProductDefinition:
        return FuturesProductDefinition(
            product_id=product.product_id,
            product_key=product.product_key,
            root=product.root,
            market=Market(product.market),
            exchange=version.exchange,
            commodity=version.commodity,
            currency=version.currency,
            price_unit=version.price_unit,
            multiplier=_require_decimal(version.multiplier, field="multiplier"),
            tick_size=_require_decimal(version.tick_size, field="tick_size"),
            settlement_method=SettlementMethod(version.settlement_method),
            session_calendar_id=version.session_calendar_id,
            source=version.source,
            valid_from=dt_from_db(version.valid_from, field_name="valid_from"),
            definition_as_of=dt_from_db(
                version.definition_as_of, field_name="definition_as_of"
            ),
            version_id=version.version_id,
            version=version.version,
            valid_to=dt_opt_from_db(version.valid_to, field_name="valid_to"),
        )

    @staticmethod
    def _contract_domain(
        contract: FuturesContractRow,
        version: FuturesContractVersionRow,
    ) -> FuturesContractDefinition:
        return FuturesContractDefinition(
            instrument_id=contract.instrument_id,
            product_id=contract.product_id,
            contract_month=contract.contract_month,
            status=ContractLifecycleStatus(version.status),
            definition_as_of=dt_from_db(
                version.definition_as_of, field_name="definition_as_of"
            ),
            version_id=version.version_id,
            version=version.version,
            listed_at=dt_opt_from_db(version.listed_at, field_name="listed_at"),
            first_trade_at=dt_opt_from_db(
                version.first_trade_at, field_name="first_trade_at"
            ),
            last_trade_at=dt_opt_from_db(
                version.last_trade_at, field_name="last_trade_at"
            ),
            expiration_at=dt_opt_from_db(
                version.expiration_at, field_name="expiration_at"
            ),
            first_notice_at=dt_opt_from_db(
                version.first_notice_at, field_name="first_notice_at"
            ),
            delivery_start=date_from_db(version.delivery_start),
            delivery_end=date_from_db(version.delivery_end),
            settlement_at=dt_opt_from_db(
                version.settlement_at, field_name="settlement_at"
            ),
            source=version.source,
        )

    @staticmethod
    def _series_domain(row: ContinuousSeriesDefinitionRow) -> ContinuousSeriesDefinition:
        return ContinuousSeriesDefinition(
            instrument_id=row.instrument_id,
            product_id=row.product_id,
            roll_rule=RollRule(row.roll_rule),
            rank=row.rank,
            adjustment=ContinuousAdjustment(row.adjustment),
            provider_methodology_version=row.provider_methodology_version,
            valid_from=dt_from_db(row.valid_from, field_name="valid_from"),
            valid_to=dt_opt_from_db(row.valid_to, field_name="valid_to"),
        )

    @staticmethod
    def _mapping_domain(row: ContinuousContractMappingRow) -> ContinuousContractMapping:
        return ContinuousContractMapping(
            continuous_instrument_id=row.continuous_instrument_id,
            contract_instrument_id=row.contract_instrument_id,
            effective_from=dt_from_db(row.effective_from, field_name="effective_from"),
            mapping_source=row.mapping_source,
            effective_to=dt_opt_from_db(row.effective_to, field_name="effective_to"),
        )


# Structural check that the concrete class matches the port.
def _assert_protocol() -> None:
    _: type[FuturesDefinitionRepository] = SqlAlchemyFuturesDefinitionRepository


_assert_protocol()
