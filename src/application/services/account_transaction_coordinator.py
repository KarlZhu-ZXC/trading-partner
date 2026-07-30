"""Read-only historical account transaction refresh and query boundary."""

from __future__ import annotations

from collections.abc import Mapping

from application.dto.account_transactions import (
    AccountGetTransactionsInput,
    AccountTransactionDTO,
    AccountTransactionsDTO,
)
from application.dto.error_mapper import to_error_info, to_error_info_from_exception
from application.dto.tool_envelope import SourceReference, ToolEnvelope, WarningInfo
from application.ports.account_transaction_provider import AccountTransactionProvider
from application.ports.account_transaction_repository import AccountTransactionRepository
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from domain.common.enums import Freshness, SourceRole, VendorId
from domain.common.errors import TradingPartnerError
from domain.common.ids import EntityIdPrefix

_PROVIDER_WARNING_MESSAGES = {
    "SCHWAB_TRANSACTION_SIDE_INFERRED_FROM_SIGN": (
        "Schwab omitted explicit trade instruction; side was derived from the signed "
        "security quantity."
    ),
    "SCHWAB_TRANSACTION_ITEM_OMITTED": (
        "One or more Schwab transaction items could not be normalized."
    ),
    "SCHWAB_NON_SECURITY_TRANSACTION_ITEM_SKIPPED": (
        "A Schwab cash journal item was outside the security-transaction model."
    ),
    "SCHWAB_TRANSACTION_WINDOW_DEFAULTED": (
        "Schwab transaction history used its supported default lookback window."
    ),
    "SCHWAB_TRANSACTION_WINDOW_CLAMPED": (
        "The requested Schwab transaction window was clamped to 60 days."
    ),
}


class AccountTransactionCoordinator:
    def __init__(
        self,
        providers: Mapping[VendorId, AccountTransactionProvider],
        repository: AccountTransactionRepository,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._providers = dict(providers)
        self._repository = repository
        self._clock = clock
        self._ids = id_generator
        self._redactor = secret_redactor

    async def get_transactions(
        self, request: AccountGetTransactionsInput
    ) -> ToolEnvelope[AccountTransactionsDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        as_of = request.end or self._clock.now()
        try:
            selected = request.providers or tuple(self._providers)
            unavailable: list[VendorId] = []
            sources: list[SourceReference] = []
            provider_warnings: dict[str, VendorId] = {}
            for vendor in selected:
                provider = self._providers.get(vendor)
                if provider is None or not provider.is_configured():
                    unavailable.append(vendor)
                    continue
                result = await provider.get_account_transactions(
                    start=request.start, end=request.end, limit=request.limit
                )
                self._repository.append_many(result.value)
                for code in result.meta.warnings:
                    provider_warnings.setdefault(code, vendor)
                sources.append(
                    SourceReference(
                        name=vendor.value,
                        role=SourceRole.PRIMARY,
                        retrieved_at=result.meta.fetched_at,
                    )
                )
            values = self._repository.list(
                providers=request.providers,
                start=request.start,
                end=request.end,
                limit=request.limit,
            )
            unavailable_warnings = tuple(
                WarningInfo(
                    code=f"{vendor.value.upper()}_TRANSACTIONS_UNAVAILABLE",
                    message="Historical transactions are unavailable from this provider.",
                    details={},
                )
                for vendor in unavailable
            )
            upstream_warnings = tuple(
                WarningInfo(
                    code=code,
                    message=_PROVIDER_WARNING_MESSAGES.get(
                        code, "Account transaction provider warning."
                    ),
                    details={"provider": vendor.value},
                )
                for code, vendor in provider_warnings.items()
            )
            warnings = unavailable_warnings + upstream_warnings
            return ToolEnvelope.success(
                request_id=request_id,
                market=None,
                as_of=as_of,
                fetched_at=self._clock.now(),
                freshness=Freshness.UNKNOWN,
                sources=tuple(sources),
                data=AccountTransactionsDTO(
                    transactions=tuple(AccountTransactionDTO.from_domain(item) for item in values),
                    unavailable_providers=tuple(unavailable),
                ),
                degraded=bool(warnings),
                warnings=warnings,
            )
        except Exception as exc:  # noqa: BLE001
            error = (
                to_error_info(exc, self._redactor)
                if isinstance(exc, TradingPartnerError)
                else to_error_info_from_exception(exc, self._redactor)
            )
            return ToolEnvelope.failure(
                request_id=request_id,
                market=None,
                as_of=as_of,
                fetched_at=self._clock.now(),
                errors=(error,),
            )

    def list_durable_transactions(
        self, request: AccountGetTransactionsInput
    ) -> ToolEnvelope[AccountTransactionsDTO]:
        """Read normalized durable transactions without contacting a broker."""
        request_id = self._ids.new(EntityIdPrefix.REQ)
        as_of = request.end or self._clock.now()
        try:
            values = self._repository.list(
                providers=request.providers,
                start=request.start,
                end=request.end,
                limit=request.limit,
            )
            return ToolEnvelope.success(
                request_id=request_id,
                market=None,
                as_of=as_of,
                fetched_at=self._clock.now(),
                freshness=Freshness.UNKNOWN,
                sources=(),
                data=AccountTransactionsDTO(
                    transactions=tuple(AccountTransactionDTO.from_domain(item) for item in values),
                    unavailable_providers=(),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            error = (
                to_error_info(exc, self._redactor)
                if isinstance(exc, TradingPartnerError)
                else to_error_info_from_exception(exc, self._redactor)
            )
            return ToolEnvelope.failure(
                request_id=request_id,
                market=None,
                as_of=as_of,
                fetched_at=self._clock.now(),
                errors=(error,),
            )
