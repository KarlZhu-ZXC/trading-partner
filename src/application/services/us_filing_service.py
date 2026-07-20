"""Router-backed SEC filings and insider-activity service."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from application.dto.provider_routing import ProviderSuccess, RouterExecutionResult
from application.ports.category_provider import CategoryProvider
from application.ports.clock import Clock
from application.ports.provider_cache_codec import ProviderCacheCodec
from application.ports.us_research_providers import (
    USFilingsProvider,
    USInsiderActivityProvider,
)
from application.services.provider_router import ProviderRouter
from application.services.us_market_data_service import build_us_fingerprint
from domain.common.enums import AssetType, DataCategory, Market
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument
from domain.us_research.enums import USFilingForm
from domain.us_research.models import USFiling, USInsiderTransaction

OP_FILINGS = "us.filings.v1"
OP_INSIDER = "us.insider_activity.v1"


class USFilingService:
    def __init__(
        self,
        router: ProviderRouter,
        clock: Clock,
        filings_codec: ProviderCacheCodec[tuple[USFiling, ...]],
        insider_codec: ProviderCacheCodec[tuple[USInsiderTransaction, ...]],
    ) -> None:
        if router is None or clock is None:
            raise DataContractError("router and clock are required")
        self._router = router
        self._clock = clock
        self._filings_codec = filings_codec
        self._insider_codec = insider_codec

    def _request(
        self,
        instrument: Instrument,
        *,
        start: date | None,
        end: date | None,
        limit: int,
        as_of: datetime,
    ) -> None:
        if (
            not isinstance(instrument, Instrument)
            or instrument.market is not Market.US
            or instrument.asset_type is not AssetType.EQUITY
        ):
            raise DataContractError("US filing research supports US equities only")
        require_aware_datetime(as_of, field_name="as_of")
        if as_of > self._clock.now():
            raise DataContractError("as_of must not be in the future")
        if start is not None and type(start) is not date:
            raise DataContractError("start must be a date")
        if end is not None and type(end) is not date:
            raise DataContractError("end must be a date")
        if start is not None and end is not None and start > end:
            raise DataContractError("start must be <= end")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise DataContractError("limit must be between 1 and 100")

    @staticmethod
    def _validate_rows(
        success: ProviderSuccess[tuple[object, ...]],
        *,
        row_type: type,
        category: DataCategory,
        instrument: Instrument,
        as_of: datetime,
    ) -> None:
        if success.meta.category is not category or success.meta.as_of != as_of:
            raise DataContractError("provider metadata does not match request")
        if not isinstance(success.value, tuple):
            raise DataContractError("provider rows must be a tuple")
        if any(
            not isinstance(row, row_type) or row.instrument_id != instrument.instrument_id  # type: ignore[attr-defined]
            for row in success.value
        ):
            raise DataContractError("provider row identity does not match request")

    async def get_filings(
        self,
        instrument: Instrument,
        *,
        forms: tuple[USFilingForm, ...],
        start: date | None,
        end: date | None,
        include_sections: bool,
        limit: int,
        as_of: datetime,
    ) -> RouterExecutionResult[tuple[USFiling, ...]]:
        self._request(instrument, start=start, end=end, limit=limit, as_of=as_of)
        if not isinstance(forms, tuple) or any(
            not isinstance(form, USFilingForm) for form in forms
        ):
            raise DataContractError("forms must be tuple[USFilingForm, ...]")
        if type(include_sections) is not bool:
            raise DataContractError("include_sections must be bool")

        async def call(adapter: CategoryProvider) -> ProviderSuccess[tuple[USFiling, ...]]:
            if not isinstance(adapter, USFilingsProvider):
                raise DataContractError("adapter does not implement USFilingsProvider")
            return await adapter.get_filings(
                instrument,
                forms=forms,
                start=start,
                end=end,
                include_sections=include_sections,
                limit=limit,
                as_of=as_of,
            )

        def validate(success: ProviderSuccess[tuple[USFiling, ...]]) -> None:
            self._validate_rows(
                success,
                row_type=USFiling,
                category=DataCategory.FILINGS,
                instrument=instrument,
                as_of=as_of,
            )
            if any(
                filing.accepted_at is not None and filing.accepted_at > as_of
                for filing in success.value
            ):
                raise DataContractError("filing is not visible at as_of")
            local_day = as_of.astimezone(ZoneInfo(instrument.timezone)).date()
            cutoff = min(end or local_day, local_day)
            if any(
                filing.filed_date > cutoff or (start is not None and filing.filed_date < start)
                for filing in success.value
            ):
                raise DataContractError("filing is outside request cutoff")

        params = {
            "forms": ",".join(form.value for form in forms),
            "start": start.isoformat() if start else "",
            "end": end.isoformat() if end else "",
            "include_sections": str(include_sections).lower(),
            "limit": str(limit),
        }
        return await self._router.execute(
            market=Market.US,
            category=DataCategory.FILINGS,
            call=call,
            operation_name=OP_FILINGS,
            request_fingerprint=build_us_fingerprint(
                OP_FILINGS, instrument.instrument_id, params, as_of
            ),
            instrument=instrument,
            as_of=as_of,
            cache_codec=self._filings_codec,
            result_validator=validate,
        )

    async def get_insider_activity(
        self,
        instrument: Instrument,
        *,
        start: date | None,
        end: date | None,
        limit: int,
        as_of: datetime,
    ) -> RouterExecutionResult[tuple[USInsiderTransaction, ...]]:
        self._request(instrument, start=start, end=end, limit=limit, as_of=as_of)

        async def call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[USInsiderTransaction, ...]]:
            if not isinstance(adapter, USInsiderActivityProvider):
                raise DataContractError("adapter does not implement USInsiderActivityProvider")
            return await adapter.get_insider_activity(
                instrument, start=start, end=end, limit=limit, as_of=as_of
            )

        def validate(success: ProviderSuccess[tuple[USInsiderTransaction, ...]]) -> None:
            self._validate_rows(
                success,
                row_type=USInsiderTransaction,
                category=DataCategory.INSIDER_ACTIVITY,
                instrument=instrument,
                as_of=as_of,
            )
            if any(
                row.accepted_at is not None and row.accepted_at > as_of for row in success.value
            ):
                raise DataContractError("insider filing is not visible at as_of")
            local_day = as_of.astimezone(ZoneInfo(instrument.timezone)).date()
            cutoff = min(end or local_day, local_day)
            if any(
                row.transaction_date is not None
                and (
                    row.transaction_date > cutoff
                    or (start is not None and row.transaction_date < start)
                )
                for row in success.value
            ):
                raise DataContractError("insider transaction is outside request cutoff")

        params = {
            "start": start.isoformat() if start else "",
            "end": end.isoformat() if end else "",
            "limit": str(limit),
        }
        return await self._router.execute(
            market=Market.US,
            category=DataCategory.INSIDER_ACTIVITY,
            call=call,
            operation_name=OP_INSIDER,
            request_fingerprint=build_us_fingerprint(
                OP_INSIDER, instrument.instrument_id, params, as_of
            ),
            instrument=instrument,
            as_of=as_of,
            cache_codec=self._insider_codec,
            result_validator=validate,
        )
