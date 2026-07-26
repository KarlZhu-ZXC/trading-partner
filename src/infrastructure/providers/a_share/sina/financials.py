"""Financial statements Sina endpoint implementation."""

# Mixin attributes are supplied by SinaAShareAdapter.
# mypy: disable-error-code="attr-defined"

from __future__ import annotations

from infrastructure.providers.a_share.sina.common import (
    _FINANCIAL_ITEM_CODES,
    _SOURCE_BY_TYPE,
    _STATEMENTS_URL,
    DataCategory,
    DataContractError,
    FinancialStatementLine,
    FinancialStatementType,
    HttpRequest,
    Instrument,
    NoMarketData,
    ProviderSuccess,
    datetime,
    decimal_from_text,
    loads_json_decimal,
    parse_shanghai_date,
    parse_shanghai_datetime,
    publication_cutoff_keep,
    re,
    require_a_share_instrument,
    require_aware_datetime,
)


class SinaFinancialsMixin:
    async def get_financial_statements(
        self,
        instrument: Instrument,
        *,
        statement_types: tuple[FinancialStatementType, ...],
        periods: int,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[FinancialStatementLine, ...]]:
        self._require_configured()
        now = self._require_as_of(as_of)
        if (
            not isinstance(periods, int)
            or isinstance(periods, bool)
            or periods < 1
            or periods > 40
        ):
            raise DataContractError(
                "periods must be an int in 1..40",
                details={"field": "periods", "rule": "range"},
            )
        if not isinstance(statement_types, tuple) or not statement_types:
            raise DataContractError(
                "statement_types must be a non-empty tuple",
                details={"field": "statement_types", "rule": "non_empty"},
            )
        seen: set[FinancialStatementType] = set()
        for st in statement_types:
            if not isinstance(st, FinancialStatementType):
                raise DataContractError(
                    "statement_types elements must be FinancialStatementType",
                    details={"field": "statement_types", "rule": "type"},
                )
            if st in seen:
                raise DataContractError(
                    "statement_types must not contain duplicates",
                    details={"field": "statement_types", "rule": "unique"},
                )
            seen.add(st)

        code6, suffix = require_a_share_instrument(instrument)
        paper = self._paper_code(code6, suffix)
        lines: list[FinancialStatementLine] = []
        unknown_excluded = False
        operation = "statements"

        for stype in statement_types:
            source = _SOURCE_BY_TYPE[stype]
            response = await self._client.send(
                HttpRequest(
                    method="GET",
                    url=_STATEMENTS_URL,
                    params={
                        "paperCode": paper,
                        "source": source,
                        "type": "0",
                        "page": "1",
                        "num": str(periods),
                    },
                    headers=self._client.json_headers(
                        referer="https://finance.sina.com.cn/"
                    ),
                    body=None,
                    timeout_seconds=self._timeout_seconds,
                )
            )
            self._raise_for_http_status(response.status_code, operation=operation)
            self._require_json_content(response.headers, operation=operation)
            try:
                payload = loads_json_decimal(response.body)
                type_lines, excluded = self._parse_statement_payload(
                    payload,
                    statement_type=stype,
                    periods=periods,
                    as_of=as_of,
                    now=now,
                )
            except DataContractError as exc:
                self._ensure_no_body_leak(exc)
                raise
            lines.extend(type_lines)
            unknown_excluded = unknown_excluded or excluded

        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        if not lines:
            raise NoMarketData(
                "provider returned no market data",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                },
            )
        warnings: tuple[str, ...] = ()
        if unknown_excluded:
            warnings = ("PUBLICATION_TIME_UNKNOWN_EXCLUDED",)
        return ProviderSuccess(
            value=tuple(lines),
            meta=self._meta(
                category=DataCategory.FINANCIAL_STATEMENTS,
                as_of=as_of,
                fetched_at=fetched_at,
                warnings=warnings,
            ),
        )

    def _parse_statement_payload(
        self,
        payload: object,
        *,
        statement_type: FinancialStatementType,
        periods: int,
        as_of: datetime,
        now: datetime,
    ) -> tuple[list[FinancialStatementLine], bool]:
        if not isinstance(payload, dict):
            raise DataContractError(
                "Sina statements payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "statements",
                    "rule": "contract_drift",
                },
            )
        # Current upstream contract: report_list is keyed by YYYYMMDD.
        result = payload.get("result")
        if result is None:
            return [], False
        if not isinstance(result, dict):
            raise DataContractError(
                "Sina statements payload missing result object",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "statements",
                    "rule": "contract_drift",
                },
            )
        data = result.get("data")
        if data is None:
            return [], False
        if not isinstance(data, dict):
            raise DataContractError(
                "Sina statements data failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "statements",
                    "rule": "contract_drift",
                },
            )
        reports = data.get("report_list")
        if reports is None:
            return [], False
        if not isinstance(reports, dict):
            raise DataContractError(
                "Sina statements report_list must be an object",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "statements",
                    "rule": "contract_drift",
                },
            )
        if not reports:
            return [], False

        lines: list[FinancialStatementLine] = []
        unknown_excluded = False
        period_keys = sorted(reports, reverse=True)[:periods]
        for idx, period_key in enumerate(period_keys):
            if not isinstance(period_key, str) or re.fullmatch(r"\d{8}", period_key) is None:
                raise DataContractError(
                    "Sina statement period key must be YYYYMMDD",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "statements",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            report = reports.get(period_key)
            if not isinstance(report, dict):
                raise DataContractError(
                    "Sina statement period failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "statements",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            period_end = parse_shanghai_date(
                f"{period_key[:4]}-{period_key[4:6]}-{period_key[6:8]}"
            )
            pub_raw = report.get("publish_date")
            if isinstance(pub_raw, str) and re.fullmatch(r"\d{8}", pub_raw):
                pub_raw = f"{pub_raw[:4]}-{pub_raw[4:6]}-{pub_raw[6:8]}"
            published_at = parse_shanghai_datetime(pub_raw, field="published_at")
            keep, excluded = publication_cutoff_keep(
                published_at,
                as_of=as_of,
                now=now,
                current_window_seconds=self._current_window_seconds,
            )
            if excluded:
                unknown_excluded = True
            if not keep:
                continue
            items = report.get("data")
            if not isinstance(items, list):
                raise DataContractError(
                    "Sina statement items failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "statements",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            for jdx, item in enumerate(items):
                if not isinstance(item, dict):
                    raise DataContractError(
                        "Sina statement line failed contract validation",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": "statements",
                            "rule": "contract_drift",
                            "index": jdx,
                        },
                    )
                raw_code = item.get("item_field")
                name = item.get("item_title")
                if not isinstance(raw_code, str):
                    raise DataContractError(
                        "Sina statement line item_field must be a string",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": "statements",
                            "rule": "contract_drift",
                        },
                    )
                code = _FINANCIAL_ITEM_CODES[statement_type].get(raw_code.strip().upper())
                if code is None:
                    continue
                if not isinstance(name, str) or not name.strip():
                    name = code
                value = decimal_from_text(
                    item.get("item_value"),
                    field="value",
                )
                if code == "capital_expenditure" and value is not None:
                    value = abs(value)
                unit = report.get("rCurrency")
                if unit is None or (isinstance(unit, str) and not unit.strip()):
                    unit = "CNY"
                if not isinstance(unit, str):
                    raise DataContractError(
                        "Sina statement unit must be string",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": "statements",
                            "rule": "contract_drift",
                        },
                    )
                lines.append(
                    FinancialStatementLine(
                        statement_type=statement_type,
                        period_end=period_end,
                        published_at=published_at,
                        item_code=code,
                        item_name=name.strip()[:200],
                        value=value,
                        unit=unit.strip()[:50],
                    )
                )
        return lines, unknown_excluded
