"""Daily fund-flow Sina endpoint implementation."""

# Mixin attributes are supplied by SinaAShareAdapter.
# mypy: disable-error-code="attr-defined"

from __future__ import annotations

from infrastructure.providers.a_share.sina.common import (
    _DAILY_FLOW_URL,
    BarInterval,
    DataCategory,
    DataContractError,
    FundFlowPoint,
    HttpRequest,
    Instrument,
    ProviderSuccess,
    ReliabilityLevel,
    VendorId,
    combine_shanghai_date_time,
    date,
    datetime,
    decimal_from_text,
    loads_json_decimal_declared,
    parse_shanghai_date,
    require_a_share_instrument,
    require_aware_datetime,
)


class SinaDailyFlowMixin:
    async def get_daily_flow(
        self,
        instrument: Instrument,
        *,
        start: date | None,
        end: date | None,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[FundFlowPoint, ...]]:
        """Daily fund-flow fallback only (not intraday/northbound/etc.)."""
        self._require_configured()
        self._require_as_of(as_of)
        if start is not None and end is not None and end < start:
            raise DataContractError(
                "end must be >= start",
                details={"field": "end", "rule": "range_order"},
            )
        code6, suffix = require_a_share_instrument(instrument)
        daima = self._paper_code(code6, suffix)
        response = await self._client.send(
            HttpRequest(
                method="GET",
                url=_DAILY_FLOW_URL,
                params={
                    "page": "1",
                    "num": "60",
                    "sort": "opendate",
                    "asc": "0",
                    "daima": daima,
                },
                headers=self._client.json_headers(
                    referer="https://finance.sina.com.cn/"
                ),
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="daily_flow")
        self._require_json_content(response.headers, operation="daily_flow")
        try:
            payload = loads_json_decimal_declared(response.body, response.headers)
        except DataContractError as exc:
            self._ensure_no_body_leak(exc)
            raise
        if not isinstance(payload, list):
            # Error envelopes (e.g. code=11 / __ERROR Service not valid) and
            # any non-list shape are contract drift — never invent rows.
            raise DataContractError(
                "Sina daily flow payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "daily_flow",
                    "rule": "contract_drift",
                },
            )
        points: list[FundFlowPoint] = []
        for idx, row in enumerate(payload):
            if not isinstance(row, dict):
                raise DataContractError(
                    "Sina daily flow row failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "daily_flow",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            day_raw = row.get("opendate")
            if not isinstance(day_raw, str) or not day_raw.strip():
                raise DataContractError(
                    "Sina daily flow missing opendate",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "daily_flow",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            day = parse_shanghai_date(day_raw)
            if start is not None and day < start:
                continue
            if end is not None and day > end:
                continue
            occurred_at = combine_shanghai_date_time(day, "15:00:00")
            if occurred_at > as_of:
                continue
            # Live shape: netamount + r0_net required when present; r1/r2/r3
            # nets may be absent → keep None (never coerce missing to zero).
            points.append(
                FundFlowPoint(
                    occurred_at=occurred_at,
                    interval=BarInterval.ONE_DAY,
                    main_net_cny=decimal_from_text(
                        row.get("netamount"), field="main_net_cny"
                    ),
                    super_large_net_cny=decimal_from_text(
                        row.get("r0_net"), field="super_large_net_cny"
                    ),
                    large_net_cny=decimal_from_text(
                        row.get("r1_net"), field="large_net_cny"
                    ),
                    medium_net_cny=decimal_from_text(
                        row.get("r2_net"), field="medium_net_cny"
                    ),
                    small_net_cny=decimal_from_text(
                        row.get("r3_net"), field="small_net_cny"
                    ),
                    source_vendor=VendorId.SINA,
                    reliability=ReliabilityLevel.LOW,
                    is_authoritative=False,
                )
            )
        points.sort(key=lambda p: p.occurred_at)
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        return ProviderSuccess(
            value=tuple(points),
            meta=self._meta(
                category=DataCategory.CAPITAL,
                as_of=as_of,
                fetched_at=fetched_at,
                warnings=("LOW_RELIABILITY_MARKET_SIGNAL",),
            ),
        )
