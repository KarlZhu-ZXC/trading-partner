"""MCP-facing coordinator for Phase 1I account and portfolio tools."""

from __future__ import annotations

from datetime import datetime

from application.dto.error_mapper import to_error_info, to_error_info_from_exception
from application.dto.portfolio import (
    AccountGetPositionsInput,
    AccountGetSnapshotInput,
    AccountPositionDTO,
    AccountPositionsAccountDTO,
    AccountPositionsDTO,
    AccountSnapshotDTO,
    AccountSnapshotsDTO,
    PortfolioAnalyzeInput,
    PortfolioSimulateAdditionInput,
    PortfolioSimulationDTO,
    PortfolioSnapshotDTO,
)
from application.dto.tool_envelope import SourceReference, ToolEnvelope, WarningInfo
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services.account_service import AccountService
from application.services.portfolio_service import PortfolioService
from domain.common.enums import Freshness, SourceRole, VendorId
from domain.common.errors import TradingPartnerError
from domain.common.ids import EntityIdPrefix
from domain.portfolio.models import AccountSnapshot


class PortfolioToolCoordinator:
    def __init__(
        self,
        account_service: AccountService,
        portfolio_service: PortfolioService,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._accounts = account_service
        self._portfolio = portfolio_service
        self._clock = clock
        self._ids = id_generator
        self._redactor = secret_redactor

    async def get_account_snapshot(
        self, request: AccountGetSnapshotInput
    ) -> ToolEnvelope[AccountSnapshotsDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        as_of = request.as_of or self._clock.now()
        try:
            result = await self._accounts.refresh(providers=request.providers, as_of=as_of)
            data = AccountSnapshotsDTO(
                snapshots=tuple(AccountSnapshotDTO.from_domain(item) for item in result.snapshots)
            )
            codes = result.warning_codes + tuple(
                code for item in result.snapshots for code in item.warning_codes
            )
            return self._success(request_id, as_of, data, result.snapshots, codes)
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, as_of, exc)

    def get_account_positions(
        self, request: AccountGetPositionsInput
    ) -> ToolEnvelope[AccountPositionsDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        as_of = self._clock.now()
        try:
            ids = (request.snapshot_id,) if request.snapshot_id else ()
            snapshots = self._accounts.get_snapshots(ids)
            data = AccountPositionsDTO(
                accounts=tuple(
                    AccountPositionsAccountDTO(
                        snapshot_id=item.snapshot_id,
                        account_ref=item.account_ref,
                        provider=item.provider,
                        account_as_of=item.account_as_of,
                        positions=tuple(
                            AccountPositionDTO.model_validate(position)
                            for position in item.positions
                        ),
                    )
                    for item in snapshots
                )
            )
            codes = tuple(code for item in snapshots for code in item.warning_codes)
            return self._success(request_id, as_of, data, snapshots, codes)
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, as_of, exc)

    def analyze_portfolio(
        self, request: PortfolioAnalyzeInput
    ) -> ToolEnvelope[PortfolioSnapshotDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        as_of = self._clock.now()
        try:
            value = self._portfolio.analyze(
                account_snapshot_ids=request.account_snapshot_ids,
                base_currency=request.base_currency,
            )
            snapshots = self._accounts.get_snapshots(value.account_snapshot_ids)
            return self._success(
                request_id,
                value.as_of,
                PortfolioSnapshotDTO.from_domain(value),
                snapshots,
                value.warning_codes,
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, as_of, exc)

    def simulate_addition(
        self, request: PortfolioSimulateAdditionInput
    ) -> ToolEnvelope[PortfolioSimulationDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        as_of = self._clock.now()
        try:
            value = self._portfolio.simulate_addition(
                account_snapshot_ids=request.account_snapshot_ids,
                instrument_id=request.instrument_id,
                quantity=request.quantity,
                assumed_price=request.assumed_price,
                currency=request.currency,
                base_currency=request.base_currency,
            )
            snapshots = self._accounts.get_snapshots(value.before.account_snapshot_ids)
            codes = value.before.warning_codes + value.after.warning_codes
            return self._success(
                request_id,
                value.before.as_of,
                PortfolioSimulationDTO.from_domain(value),
                snapshots,
                codes,
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, as_of, exc)

    def _success[T](
        self,
        request_id: str,
        as_of: datetime,
        data: T,
        snapshots: tuple[AccountSnapshot, ...],
        codes: tuple[str, ...],
    ) -> ToolEnvelope[T]:
        fetched_at = max((item.fetched_at for item in snapshots), default=self._clock.now())
        warnings = tuple(
            WarningInfo(code=code, message="Account or portfolio data warning.", details={})
            for code in dict.fromkeys(codes)
        )
        roles = {
            VendorId.SCHWAB: SourceRole.PRIMARY,
            VendorId.MOOMOO: SourceRole.PRIMARY,
            VendorId.MANUAL_CSV: SourceRole.SUPPLEMENTAL,
        }
        sources = tuple(
            SourceReference(
                name=vendor.value,
                role=roles.get(vendor, SourceRole.SUPPLEMENTAL),
                retrieved_at=max(
                    item.fetched_at for item in snapshots if item.provider is vendor
                ),
            )
            for vendor in dict.fromkeys(item.provider for item in snapshots)
        )
        return ToolEnvelope.success(
            request_id=request_id,
            market=None,
            as_of=as_of,
            fetched_at=fetched_at,
            freshness=Freshness.UNKNOWN,
            sources=sources,
            data=data,
            degraded=bool(warnings),
            warnings=warnings,
        )

    def _failure[T](
        self, request_id: str, as_of: datetime, exc: BaseException
    ) -> ToolEnvelope[T]:
        mapped = (
            to_error_info(exc, self._redactor)
            if isinstance(exc, TradingPartnerError)
            else to_error_info_from_exception(exc, self._redactor)
        )
        return ToolEnvelope.failure(
            request_id=request_id,
            market=None,
            as_of=as_of,
            fetched_at=self._clock.now(),
            freshness=Freshness.UNKNOWN,
            sources=(),
            errors=(mapped,),
            degraded=True,
            data=None,
        )
