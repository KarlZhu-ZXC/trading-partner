"""Router-backed US fundamentals, statements, and corporate actions."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from application.dto.provider_routing import ProviderSuccess, RouterExecutionResult
from application.ports.category_provider import CategoryProvider
from application.ports.clock import Clock
from application.ports.provider_cache_codec import ProviderCacheCodec
from application.ports.us_research_providers import (
    USCorporateActionsProvider,
    USFinancialStatementsProvider,
    USFundamentalProvider,
)
from application.services.provider_router import ProviderRouter
from application.services.us_market_data_service import build_us_fingerprint
from application.services.us_research_tool_policies import OFFICIAL_FUNDAMENTALS_POLICY
from domain.common.enums import AssetType, DataCategory, Market
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument
from domain.us_research.enums import USStatementFrequency
from domain.us_research.models import (
    USCorporateAction,
    USFinancialStatements,
    USFundamentalSnapshot,
)

OP_FUNDAMENTAL = "us.fundamental_snapshot.v1"
OP_OFFICIAL_FUNDAMENTAL = "us.official_fundamental_snapshot.v1"
OP_STATEMENTS = "us.financial_statements.v1"
OP_ACTIONS = "us.corporate_actions.v1"


class USFundamentalService:
    def __init__(
        self,
        router: ProviderRouter,
        clock: Clock,
        fundamental_codec: ProviderCacheCodec[USFundamentalSnapshot],
        statements_codec: ProviderCacheCodec[USFinancialStatements],
        actions_codec: ProviderCacheCodec[tuple[USCorporateAction, ...]],
    ) -> None:
        if router is None or clock is None:
            raise DataContractError("router and clock are required")
        self._router = router
        self._clock = clock
        self._fundamental_codec = fundamental_codec
        self._statements_codec = statements_codec
        self._actions_codec = actions_codec

    def _request(self, instrument: Instrument, as_of: datetime) -> None:
        if not isinstance(instrument, Instrument):
            raise DataContractError("instrument must be Instrument")
        if instrument.market is not Market.US or instrument.asset_type is not AssetType.EQUITY:
            raise DataContractError("US research supports US equities only")
        require_aware_datetime(as_of, field_name="as_of")
        if as_of > self._clock.now():
            raise DataContractError("as_of must not be in the future")

    @staticmethod
    def _validate(
        success: ProviderSuccess[object],
        *,
        expected_type: type,
        category: DataCategory,
        instrument: Instrument,
        as_of: datetime,
    ) -> None:
        if not isinstance(success, ProviderSuccess):
            raise DataContractError("provider must return ProviderSuccess")
        if success.meta.category is not category or success.meta.as_of != as_of:
            raise DataContractError("provider metadata does not match request")
        if not isinstance(success.value, expected_type):
            raise DataContractError("provider value has invalid type")
        if success.value.instrument_id != instrument.instrument_id:  # type: ignore[attr-defined]
            raise DataContractError("provider instrument_id does not match request")

    async def get_snapshot(
        self, instrument: Instrument, as_of: datetime
    ) -> RouterExecutionResult[USFundamentalSnapshot]:
        self._request(instrument, as_of)

        async def call(adapter: CategoryProvider) -> ProviderSuccess[USFundamentalSnapshot]:
            if not isinstance(adapter, USFundamentalProvider):
                raise DataContractError("adapter does not implement USFundamentalProvider")
            return await adapter.get_fundamental_snapshot(instrument, as_of)

        def validate(success: ProviderSuccess[USFundamentalSnapshot]) -> None:
            self._validate(
                success,
                expected_type=USFundamentalSnapshot,
                category=DataCategory.FUNDAMENTALS,
                instrument=instrument,
                as_of=as_of,
            )

        return await self._router.execute(
            market=Market.US,
            category=DataCategory.FUNDAMENTALS,
            call=call,
            operation_name=OP_FUNDAMENTAL,
            request_fingerprint=build_us_fingerprint(
                OP_FUNDAMENTAL, instrument.instrument_id, {}, as_of
            ),
            instrument=instrument,
            as_of=as_of,
            cache_codec=self._fundamental_codec,
            result_validator=validate,
        )

    async def get_official_snapshot(
        self, instrument: Instrument, as_of: datetime
    ) -> RouterExecutionResult[USFundamentalSnapshot]:
        """Fetch SEC-only reported metrics for explicit authority composition."""
        self._request(instrument, as_of)

        async def call(adapter: CategoryProvider) -> ProviderSuccess[USFundamentalSnapshot]:
            if not isinstance(adapter, USFundamentalProvider):
                raise DataContractError("adapter does not implement USFundamentalProvider")
            return await adapter.get_fundamental_snapshot(instrument, as_of)

        def validate(success: ProviderSuccess[USFundamentalSnapshot]) -> None:
            self._validate(
                success,
                expected_type=USFundamentalSnapshot,
                category=DataCategory.FUNDAMENTALS,
                instrument=instrument,
                as_of=as_of,
            )

        return await self._router.execute(
            market=Market.US,
            category=DataCategory.FUNDAMENTALS,
            call=call,
            operation_name=OP_OFFICIAL_FUNDAMENTAL,
            request_fingerprint=build_us_fingerprint(
                OP_OFFICIAL_FUNDAMENTAL, instrument.instrument_id, {}, as_of
            ),
            instrument=instrument,
            as_of=as_of,
            tool_policy=OFFICIAL_FUNDAMENTALS_POLICY,
            cache_codec=self._fundamental_codec,
            result_validator=validate,
        )

    async def get_statements(
        self,
        instrument: Instrument,
        *,
        frequency: USStatementFrequency,
        limit: int,
        as_of: datetime,
    ) -> RouterExecutionResult[USFinancialStatements]:
        self._request(instrument, as_of)
        if not isinstance(frequency, USStatementFrequency):
            raise DataContractError("frequency must be USStatementFrequency")
        cap = 8 if frequency is USStatementFrequency.QUARTERLY else 5
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= cap:
            raise DataContractError(f"limit must be between 1 and {cap}")

        async def call(adapter: CategoryProvider) -> ProviderSuccess[USFinancialStatements]:
            if not isinstance(adapter, USFinancialStatementsProvider):
                raise DataContractError("adapter does not implement USFinancialStatementsProvider")
            return await adapter.get_financial_statements(
                instrument, frequency=frequency, limit=limit, as_of=as_of
            )

        def validate(success: ProviderSuccess[USFinancialStatements]) -> None:
            self._validate(
                success,
                expected_type=USFinancialStatements,
                category=DataCategory.FINANCIAL_STATEMENTS,
                instrument=instrument,
                as_of=as_of,
            )
            if success.value.frequency is not frequency:
                raise DataContractError("statement frequency does not match request")

        params = {"frequency": frequency.value, "limit": str(limit)}
        return await self._router.execute(
            market=Market.US,
            category=DataCategory.FINANCIAL_STATEMENTS,
            call=call,
            operation_name=OP_STATEMENTS,
            request_fingerprint=build_us_fingerprint(
                OP_STATEMENTS, instrument.instrument_id, params, as_of
            ),
            instrument=instrument,
            as_of=as_of,
            cache_codec=self._statements_codec,
            result_validator=validate,
        )

    async def get_corporate_actions(
        self,
        instrument: Instrument,
        *,
        start: date | None,
        end: date | None,
        as_of: datetime,
    ) -> RouterExecutionResult[tuple[USCorporateAction, ...]]:
        self._request(instrument, as_of)
        if start is not None and type(start) is not date:
            raise DataContractError("start must be a date")
        if end is not None and type(end) is not date:
            raise DataContractError("end must be a date")
        if start is not None and end is not None and start > end:
            raise DataContractError("start must be <= end")

        async def call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[USCorporateAction, ...]]:
            if not isinstance(adapter, USCorporateActionsProvider):
                raise DataContractError("adapter does not implement USCorporateActionsProvider")
            return await adapter.get_corporate_actions(
                instrument, start=start, end=end, as_of=as_of
            )

        def validate(success: ProviderSuccess[tuple[USCorporateAction, ...]]) -> None:
            if success.meta.category is not DataCategory.CORPORATE_ACTIONS:
                raise DataContractError("corporate actions category mismatch")
            if success.meta.as_of != as_of or not isinstance(success.value, tuple):
                raise DataContractError("corporate actions result is invalid")
            for action in success.value:
                if (
                    not isinstance(action, USCorporateAction)
                    or action.instrument_id != instrument.instrument_id
                ):
                    raise DataContractError("corporate action identity mismatch")
                if action.effective_date is not None:
                    local_day = as_of.astimezone(ZoneInfo(instrument.timezone)).date()
                    cutoff = min(end or local_day, local_day)
                    if action.effective_date > cutoff or (
                        start is not None and action.effective_date < start
                    ):
                        raise DataContractError("corporate action is outside request cutoff")

        params = {
            "start": start.isoformat() if start else "",
            "end": end.isoformat() if end else "",
        }
        return await self._router.execute(
            market=Market.US,
            category=DataCategory.CORPORATE_ACTIONS,
            call=call,
            operation_name=OP_ACTIONS,
            request_fingerprint=build_us_fingerprint(
                OP_ACTIONS, instrument.instrument_id, params, as_of
            ),
            instrument=instrument,
            as_of=as_of,
            cache_codec=self._actions_codec,
            result_validator=validate,
        )
