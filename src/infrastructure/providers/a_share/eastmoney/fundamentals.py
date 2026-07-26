"""Fundamentals and research Eastmoney endpoint implementation."""

# Mixin attributes are supplied by EastmoneyAShareAdapter.
# mypy: disable-error-code="attr-defined"

from __future__ import annotations

from infrastructure.providers.a_share.eastmoney.common import (
    _F10_SECTION_REPORTS,
    _INTRADAY_FLOW_URL,
    _NEWS_LIST_URL,
    _REPORT_LIST_URL,
    _STATEMENT_REPORT_NAMES,
    AnalystReportItem,
    Any,
    BarInterval,
    ConsensusEstimate,
    DataCategory,
    DataContractError,
    Decimal,
    DividendRecord,
    F10Section,
    FinancialStatementLine,
    FinancialStatementType,
    FundamentalMetric,
    FundFlowPoint,
    HttpRequest,
    Instrument,
    Mapping,
    Market,
    NewsItem,
    NoMarketData,
    PartialDataError,
    ProviderSuccess,
    ReliabilityLevel,
    UnlockRecord,
    VendorId,
    date,
    datetime,
    decimal_from_text,
    eastmoney_secid,
    infer_session_basic,
    int_from_text,
    loads_json_decimal,
    parse_shanghai_date,
    parse_shanghai_datetime,
    publication_cutoff_keep,
    require_a_share_instrument,
    require_aware_datetime,
    sanitize_public_url,
)


class EastmoneyFundamentalsMixin:
    async def get_fundamentals(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[tuple[FundamentalMetric, ...]]:
        self._require_configured()
        now = self._require_as_of(as_of)
        code6, suffix = require_a_share_instrument(instrument)
        secucode = f"{code6}.{suffix}"
        payload = await self._datacenter_get(
            report_name="RPT_F10_FINANCE_MAINFINADATA",
            filter_expr=f'(SECUCODE="{secucode}")',
            columns=(
                "SECUCODE,SECURITY_CODE,REPORT_DATE,NOTICE_DATE,"
                "EPSJB,BPS,ROE_WEIGHT,MGJYXJJE,XSMLL,TOTALOPERATEREVE,"
                "PARENTNETPROFIT,KCFJCXSYJLR"
            ),
            page_size=4,
            operation="fundamentals",
            sort_columns="REPORT_DATE",
            sort_types="-1",
        )
        rows = self._require_datacenter_rows(payload, operation="fundamentals")
        metrics: list[FundamentalMetric] = []
        unknown_excluded = False
        field_map = (
            ("EPSJB", "eps", "CNY"),
            ("BPS", "bps", "CNY"),
            ("ROE_WEIGHT", "roe", "percent"),
            ("MGJYXJJE", "ocf_ps", "CNY"),
            ("XSMLL", "gross_margin", "percent"),
            ("TOTALOPERATEREVE", "operating_revenue", "CNY"),
            ("PARENTNETPROFIT", "net_profit_parent", "CNY"),
            ("KCFJCXSYJLR", "deducted_net_profit", "CNY"),
        )
        for row in rows:
            period_raw = row.get("REPORT_DATE")
            period_end = None
            if isinstance(period_raw, str) and period_raw.strip():
                period_end = parse_shanghai_date(period_raw)
            published_at = parse_shanghai_datetime(row.get("NOTICE_DATE"), field="published_at")
            keep, excluded = publication_cutoff_keep(published_at, as_of=as_of, now=now)
            if excluded:
                unknown_excluded = True
            if not keep:
                continue
            for field, name, unit in field_map:
                if field not in row:
                    continue
                raw_val = row.get(field)
                if raw_val is None or raw_val == "-":
                    continue
                if isinstance(raw_val, str) and not raw_val.strip():
                    continue
                # Prefer Decimal; allow int/str labels only when non-numeric.
                value: Decimal | str | int | None
                try:
                    value = decimal_from_text(raw_val, field=name)
                except DataContractError:
                    if isinstance(raw_val, str):
                        value = raw_val.strip()[:200]
                    elif isinstance(raw_val, int) and not isinstance(raw_val, bool):
                        value = raw_val
                    else:
                        raise
                metrics.append(
                    FundamentalMetric(
                        name=name,
                        value=value,
                        unit=unit,
                        period_end=period_end,
                        published_at=published_at,
                    )
                )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        if not metrics:
            raise NoMarketData(
                "provider returned no market data",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "fundamentals",
                },
            )
        warnings: tuple[str, ...] = ()
        if unknown_excluded:
            warnings = ("PUBLICATION_TIME_UNKNOWN_EXCLUDED",)
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        meta = self._meta(
            category=DataCategory.FUNDAMENTALS,
            as_of=as_of,
            fetched_at=fetched_at,
            session=session,
        )
        if warnings:
            from dataclasses import replace as _replace

            meta = _replace(meta, warnings=warnings)
        return ProviderSuccess(value=tuple(metrics), meta=meta)

    async def get_f10_sections(
        self,
        instrument: Instrument,
        *,
        sections: tuple[str, ...],
        as_of: datetime,
    ) -> ProviderSuccess[tuple[F10Section, ...]]:
        self._require_configured()
        self._require_as_of(as_of)
        if not isinstance(sections, tuple) or not sections:
            raise DataContractError(
                "sections must be a non-empty tuple",
                details={"field": "sections", "rule": "non_empty"},
            )
        code6, suffix = require_a_share_instrument(instrument)
        secucode = f"{code6}.{suffix}"
        out: list[F10Section] = []
        for section in sections:
            if not isinstance(section, str) or not section.strip():
                raise DataContractError(
                    "section name must be non-blank",
                    details={"field": "sections", "rule": "non_blank"},
                )
            key = section.strip().lower()
            report = _F10_SECTION_REPORTS.get(key)
            if report is None:
                # Unknown section key is contract error (closed allowlist).
                raise DataContractError(
                    "unsupported F10 section",
                    details={
                        "field": "sections",
                        "rule": "unknown_section",
                        "section": key,
                    },
                )
            payload = await self._datacenter_get(
                report_name=report,
                filter_expr=f'(SECUCODE="{secucode}")',
                columns="SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,ORG_NAME,MAIN_BUSINESS,ORG_PROFILE,HOLDER_NUM",
                page_size=1,
                operation="f10",
            )
            rows = self._require_datacenter_rows(payload, operation="f10")
            if not rows:
                continue
            row = rows[0]
            title = str(row.get("SECURITY_NAME_ABBR") or row.get("ORG_NAME") or key)[:500]
            # Build body from remaining text fields — untrusted data, never instructions.
            parts: list[str] = []
            for field in (
                "ORG_NAME",
                "ORG_PROFILE",
                "MAIN_BUSINESS",
                "HOLDER_NUM",
            ):
                val = row.get(field)
                if val is None:
                    continue
                text = str(val).strip()
                if text:
                    parts.append(f"{field}: {text}")
            body = "\n".join(parts)[:20_000]
            out.append(
                F10Section(
                    section=key[:100],
                    title=title,
                    body=body,
                    as_of=as_of,
                )
            )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        # Legitimate empty sections tuple is success for optional F10.
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=tuple(out),
            meta=self._meta(
                category=DataCategory.FUNDAMENTALS,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
            ),
        )

    async def get_financial_statements(
        self,
        instrument: Instrument,
        *,
        statement_types: tuple[FinancialStatementType, ...],
        periods: int,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[FinancialStatementLine, ...]]:
        """Eastmoney datacenter fallback for financial statements."""
        self._require_configured()
        now = self._require_as_of(as_of)
        if not isinstance(periods, int) or isinstance(periods, bool) or periods < 1 or periods > 40:
            raise DataContractError(
                "periods must be an int in 1..40",
                details={"field": "periods", "rule": "range"},
            )
        if not isinstance(statement_types, tuple) or not statement_types:
            raise DataContractError(
                "statement_types must be a non-empty tuple",
                details={"field": "statement_types", "rule": "non_empty"},
            )
        code6, suffix = require_a_share_instrument(instrument)
        secucode = f"{code6}.{suffix}"
        lines: list[FinancialStatementLine] = []
        unknown_excluded = False
        # Frozen item columns per statement type (subset locked by fixtures).
        item_columns: Mapping[
            FinancialStatementType, tuple[tuple[str, str, str], ...]
        ] = {
            FinancialStatementType.BALANCE_SHEET: (
                ("MONETARYFUNDS", "cash_and_equivalents", "货币资金"),
                ("ACCOUNTS_RECE", "accounts_receivable", "应收账款"),
                ("INVENTORY", "inventory", "存货"),
                ("TOTAL_ASSETS", "total_assets", "总资产"),
                ("TOTAL_LIABILITIES", "total_liabilities", "总负债"),
                ("TOTAL_EQUITY", "stockholders_equity", "股东权益合计"),
            ),
            FinancialStatementType.INCOME_STATEMENT: (
                ("TOTAL_OPERATE_INCOME", "total_revenue", "营业总收入"),
                ("TOTAL_OPERATE_COST", "cost_of_revenue", "营业总成本"),
                ("OPERATE_PROFIT", "operating_income", "营业利润"),
                (
                    "PARENT_NETPROFIT",
                    "net_income_attributable_parent",
                    "归属于母公司股东的净利润",
                ),
            ),
            FinancialStatementType.CASH_FLOW: (
                ("NETCASH_OPERATE", "operating_cash_flow", "经营活动现金流净额"),
                ("CONSTRUCT_LONG_ASSET", "capital_expenditure", "资本性支出"),
                ("NETCASH_INVEST", "investing_cash_flow", "投资活动现金流净额"),
                ("NETCASH_FINANCE", "financing_cash_flow", "筹资活动现金流净额"),
                ("CCE_ADD", "cash_change", "现金及现金等价物净增加额"),
            ),
        }
        for stype in statement_types:
            if not isinstance(stype, FinancialStatementType):
                raise DataContractError(
                    "statement_types elements must be FinancialStatementType",
                    details={"field": "statement_types", "rule": "type"},
                )
            report = _STATEMENT_REPORT_NAMES[stype]
            cols = ",".join(
                ["SECUCODE", "SECURITY_CODE", "REPORT_DATE", "NOTICE_DATE"]
                + [raw_code for raw_code, _, _ in item_columns[stype]]
            )
            payload = await self._datacenter_get(
                report_name=report,
                filter_expr=f'(SECUCODE="{secucode}")',
                columns=cols,
                page_size=periods,
                operation="statements",
                sort_columns="REPORT_DATE",
                sort_types="-1",
            )
            rows = self._require_datacenter_rows(payload, operation="statements")
            for row in rows[:periods]:
                period_raw = row.get("REPORT_DATE")
                if not isinstance(period_raw, str) or not period_raw.strip():
                    raise DataContractError(
                        "Eastmoney statement missing REPORT_DATE",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": "statements",
                            "rule": "contract_drift",
                        },
                    )
                period_end = parse_shanghai_date(period_raw)
                published_at = parse_shanghai_datetime(row.get("NOTICE_DATE"), field="published_at")
                keep, excluded = publication_cutoff_keep(published_at, as_of=as_of, now=now)
                if excluded:
                    unknown_excluded = True
                if not keep:
                    continue
                for raw_code, item_code, name in item_columns[stype]:
                    if raw_code not in row:
                        continue
                    value = decimal_from_text(row.get(raw_code), field=raw_code)
                    lines.append(
                        FinancialStatementLine(
                            statement_type=stype,
                            period_end=period_end,
                            published_at=published_at,
                            item_code=item_code,
                            item_name=name[:200],
                            value=value,
                            unit="CNY",
                        )
                    )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        if not lines:
            raise NoMarketData(
                "provider returned no market data",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "statements",
                },
            )
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        meta = self._meta(
            category=DataCategory.FINANCIAL_STATEMENTS,
            as_of=as_of,
            fetched_at=fetched_at,
            session=session,
        )
        if unknown_excluded:
            from dataclasses import replace as _replace

            meta = _replace(meta, warnings=("PUBLICATION_TIME_UNKNOWN_EXCLUDED",))
        return ProviderSuccess(value=tuple(lines), meta=meta)

    async def get_corporate_actions(
        self,
        instrument: Instrument,
        *,
        start: date | None,
        end: date | None,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[UnlockRecord | DividendRecord, ...]]:
        self._require_configured()
        now = self._require_as_of(as_of)
        if start is not None and end is not None and end < start:
            raise DataContractError(
                "end must be >= start",
                details={"field": "end", "rule": "range_order"},
            )
        code6, suffix = require_a_share_instrument(instrument)
        secucode = f"{code6}.{suffix}"
        records: list[UnlockRecord | DividendRecord] = []
        unknown_excluded = False

        # Unlocks
        unlock_payload = await self._datacenter_get(
            report_name="RPT_SHAREFROZEN_DETAIL",
            filter_expr=f'(SECUCODE="{secucode}")',
            columns=(
                "SECUCODE,SECURITY_CODE,FREE_DATE,NOTICE_DATE,FREE_SHARES_TYPE,"
                "FREE_SHARES,LIFT_MARKET_CAP,ACTUAL_FREE_SHARES"
            ),
            page_size=50,
            operation="corporate_actions",
            sort_columns="FREE_DATE",
            sort_types="-1",
        )
        for row in self._require_datacenter_rows(unlock_payload, operation="corporate_actions"):
            unlock_raw = row.get("FREE_DATE")
            if not isinstance(unlock_raw, str) or not unlock_raw.strip():
                raise DataContractError(
                    "Eastmoney unlock missing FREE_DATE",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "corporate_actions",
                        "rule": "contract_drift",
                    },
                )
            unlock_date = parse_shanghai_date(unlock_raw)
            if start is not None and unlock_date < start:
                continue
            if end is not None and unlock_date > end:
                continue
            published_at = parse_shanghai_datetime(row.get("NOTICE_DATE"), field="published_at")
            keep, excluded = publication_cutoff_keep(published_at, as_of=as_of, now=now)
            if excluded:
                unknown_excluded = True
            if not keep:
                continue
            unlock_type = row.get("FREE_SHARES_TYPE")
            if unlock_type is not None and not isinstance(unlock_type, str):
                unlock_type = None
            unlock_shares = int_from_text(row.get("FREE_SHARES"), field="unlock_shares")
            tradable = int_from_text(row.get("ACTUAL_FREE_SHARES"), field="tradable_shares")
            mkt = decimal_from_text(row.get("LIFT_MARKET_CAP"), field="market_value_cny")
            records.append(
                UnlockRecord(
                    unlock_date=unlock_date,
                    published_at=published_at,
                    unlock_type=unlock_type.strip()[:64] if unlock_type else None,
                    unlock_shares=unlock_shares,
                    tradable_shares=tradable,
                    market_value_cny=mkt,
                    source_vendor=VendorId.EASTMONEY,
                    reliability=ReliabilityLevel.MEDIUM,
                    is_authoritative=False,
                )
            )

        # Dividends
        div_payload = await self._datacenter_get(
            report_name="RPT_SHAREBONUS_DET",
            filter_expr=f'(SECUCODE="{secucode}")',
            columns=(
                "SECUCODE,SECURITY_CODE,REPORT_DATE,NOTICE_DATE,PLAN_NOTICE_DATE,"
                "IMPL_PLAN_PROFILE,ASSIGN_PROGRESS,EX_DIVIDEND_DATE,"
                "CASH_DIVIDEND_RATIO,BONUS_SHARE_RATIO,TRANSFER_RATIO"
            ),
            page_size=50,
            operation="corporate_actions",
            sort_columns="NOTICE_DATE",
            sort_types="-1",
        )
        for row in self._require_datacenter_rows(div_payload, operation="corporate_actions"):
            report_raw = row.get("REPORT_DATE") or row.get("NOTICE_DATE")
            if not isinstance(report_raw, str) or not report_raw.strip():
                raise DataContractError(
                    "Eastmoney dividend missing REPORT_DATE",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "corporate_actions",
                        "rule": "contract_drift",
                    },
                )
            report_day = parse_shanghai_date(report_raw)
            fiscal_year = report_day.year
            published_at = parse_shanghai_datetime(
                row.get("NOTICE_DATE") or row.get("PLAN_NOTICE_DATE"),
                field="published_at",
            )
            keep, excluded = publication_cutoff_keep(published_at, as_of=as_of, now=now)
            if excluded:
                unknown_excluded = True
            if not keep:
                continue
            ex_raw = row.get("EX_DIVIDEND_DATE")
            ex_date = None
            if isinstance(ex_raw, str) and ex_raw.strip() and ex_raw.strip() != "-":
                ex_date = parse_shanghai_date(ex_raw)
            if start is not None and ex_date is not None and ex_date < start:
                continue
            if end is not None and ex_date is not None and ex_date > end:
                continue
            plan = row.get("ASSIGN_PROGRESS") or row.get("IMPL_PLAN_PROFILE") or "unknown"
            if not isinstance(plan, str) or not plan.strip():
                plan = "unknown"
            records.append(
                DividendRecord(
                    fiscal_year=fiscal_year,
                    plan_status=plan.strip()[:64],
                    ex_date=ex_date,
                    cash_per_share=decimal_from_text(
                        row.get("CASH_DIVIDEND_RATIO"), field="cash_per_share"
                    ),
                    bonus_shares_per_share=decimal_from_text(
                        row.get("BONUS_SHARE_RATIO"), field="bonus_shares_per_share"
                    ),
                    transfer_shares_per_share=decimal_from_text(
                        row.get("TRANSFER_RATIO"), field="transfer_shares_per_share"
                    ),
                    published_at=published_at,
                    source_vendor=VendorId.EASTMONEY,
                    reliability=ReliabilityLevel.MEDIUM,
                    is_authoritative=False,
                )
            )

        def _actions_sort_key(
            item: UnlockRecord | DividendRecord,
        ) -> tuple[object, ...]:
            # Matches AShareSnapshotService corporate-actions order contract:
            # published_at desc (None last), Unlock before Dividend, then
            # stable identity fields.
            pub = (1, 0.0) if item.published_at is None else (0, -item.published_at.timestamp())
            if isinstance(item, UnlockRecord):
                return (
                    *pub,
                    0,
                    -item.unlock_date.toordinal(),
                    item.unlock_type or "",
                    item.unlock_shares if item.unlock_shares is not None else -1,
                )
            return (
                *pub,
                1,
                -item.fiscal_year,
                item.plan_status,
                item.ex_date.toordinal() if item.ex_date is not None else -1,
            )

        records.sort(key=_actions_sort_key)
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        # Legitimate empty is success for corporate actions.
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        meta = self._meta(
            category=DataCategory.CORPORATE_ACTIONS,
            as_of=as_of,
            fetched_at=fetched_at,
            session=session,
        )
        if unknown_excluded:
            from dataclasses import replace as _replace

            meta = _replace(meta, warnings=("PUBLICATION_TIME_UNKNOWN_EXCLUDED",))
        return ProviderSuccess(value=tuple(records), meta=meta)

    # Bounded Eastmoney report page size and page-budget for exact offset + cutoff.
    _REPORT_PAGE_SIZE_MAX = 50
    _REPORT_MAX_PAGES = 8

    async def search_reports(
        self,
        *,
        text: str | None,
        instrument: Instrument | None,
        industry_code: str | None,
        published_from: date | None,
        published_to: date | None,
        limit: int,
        offset: int,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[AnalystReportItem, ...]]:
        """Exact arbitrary-offset report search via adjacent page fetches.

        Offset semantics
        ----------------
        ``offset`` / ``limit`` apply to the **provider stream after publication
        cutoff** (and date-window filters), not to raw vendor rows. Rows with
        ``published_at > as_of`` or unknown publication time (historical) are
        dropped **before** the offset counter advances. Cross-page duplicate
        ``report_key`` values use first-seen deterministic dedupe (live pages
        can shift); the product service still rejects duplicate keys in the
        final adapter/cache payload.

        Page budget (fail-closed)
        -------------------------
        At most ``_REPORT_MAX_PAGES`` pages of size
        ``min(limit, _REPORT_PAGE_SIZE_MAX)`` are fetched. Before network, a
        raw window ``offset + limit`` that cannot fit that bounded capacity is
        rejected with ``DataContractError`` / ``page_budget_exceeded``. During
        fetch, if the page budget is exhausted while the last page was full
        and post-cutoff ``offset + limit`` was not satisfied, raise
        ``PartialDataError`` / ``page_budget_exhausted`` — never return a
        silently incomplete page.
        """
        self._require_configured()
        now = self._require_as_of(as_of)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 100:
            raise DataContractError(
                "limit must be an int in 1..100",
                details={"field": "limit", "rule": "range"},
            )
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise DataContractError(
                "offset must be a nonnegative int",
                details={"field": "offset", "rule": "nonnegative"},
            )
        if (
            published_from is not None
            and published_to is not None
            and published_to < published_from
        ):
            raise DataContractError(
                "published_to must be >= published_from",
                details={"field": "published_to", "rule": "range_order"},
            )

        page_size = min(max(limit, 1), self._REPORT_PAGE_SIZE_MAX)
        # Pages needed for offset+limit post-cutoff rows (raw capacity bound).
        needed = offset + limit
        max_capacity = self._REPORT_MAX_PAGES * page_size
        if needed > max_capacity:
            raise DataContractError(
                "requested offset+limit exceeds bounded report page capacity",
                details={
                    "field": "offset",
                    "rule": "page_budget_exceeded",
                    "offset": offset,
                    "limit": limit,
                    "max_capacity": max_capacity,
                    "max_pages": self._REPORT_MAX_PAGES,
                    "page_size": page_size,
                },
            )
        # Budget includes a small buffer for cutoff/dedupe losses, capped at max.
        page_budget = min(
            self._REPORT_MAX_PAGES,
            max(1, (needed + page_size - 1) // page_size + 2),
        )
        base_params: dict[str, str] = {
            "pageSize": str(page_size),
            "fields": "",
            "qType": "0",
            "orgCode": "",
            "industryCode": (
                industry_code.strip()
                if isinstance(industry_code, str) and industry_code.strip()
                else ""
            ),
        }
        if instrument is not None:
            code6, _ = require_a_share_instrument(instrument)
            base_params["code"] = code6
        if isinstance(text, str) and text.strip():
            # Wire query may include q; never put raw free text into fingerprints.
            base_params["q"] = text.strip()[:200]
        if published_from is not None:
            base_params["beginTime"] = published_from.isoformat()
        if published_to is not None:
            base_params["endTime"] = published_to.isoformat()

        # Stream provider pages (gate-serial). Cutoff/dedupe first, then exact
        # offset skip on the kept stream, then take limit. Final page is sorted
        # deterministically for stable output.
        kept: list[AnalystReportItem] = []
        seen_keys: set[str] = set()
        unknown_excluded = False
        skipped = 0
        page_no = 1
        pages_fetched = 0
        last_page_full = False
        budget_exhausted = False
        while pages_fetched < page_budget and len(kept) < limit:
            params = dict(base_params)
            params["pageNo"] = str(page_no)
            response = await self._gated_send(
                HttpRequest(
                    method="GET",
                    url=_REPORT_LIST_URL,
                    params=params,
                    headers=self._headers(),
                    body=None,
                    timeout_seconds=self._timeout_seconds,
                )
            )
            self._raise_for_http_status(response.status_code, operation="reports")
            self._require_json_content_type(response.headers, operation="reports")
            payload = loads_json_decimal(response.body)
            if not isinstance(payload, dict):
                raise DataContractError(
                    "Eastmoney reports payload failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "reports",
                        "rule": "contract_drift",
                    },
                )
            rows = payload.get("data")
            if rows is None:
                rows = []
            if not isinstance(rows, list):
                raise DataContractError(
                    "Eastmoney reports data must be a list",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "reports",
                        "rule": "contract_drift",
                    },
                )
            pages_fetched += 1
            if not rows:
                last_page_full = False
                break  # short/empty page — do not invent rows

            # Fail closed if envelope claims more rows than page_size.
            if len(rows) > page_size:
                raise DataContractError(
                    "Eastmoney reports page exceeds declared pageSize",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "reports",
                        "rule": "envelope_drift",
                    },
                )

            for idx, row in enumerate(rows):
                if not isinstance(row, dict):
                    raise DataContractError(
                        "Eastmoney report row failed contract validation",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": "reports",
                            "rule": "contract_drift",
                            "index": idx,
                        },
                    )
                key = row.get("infoCode") or row.get("id") or row.get("encodeUrl")
                title = row.get("title") or row.get("titleNew")
                if key is None or (isinstance(key, str) and not str(key).strip()):
                    raise DataContractError(
                        "Eastmoney report missing key",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": "reports",
                            "rule": "contract_drift",
                        },
                    )
                if not isinstance(title, str) or not title.strip():
                    raise DataContractError(
                        "Eastmoney report missing title",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": "reports",
                            "rule": "contract_drift",
                        },
                    )
                report_key = str(key).strip()[:200]
                # Cross-page duplicate: first-seen deterministic dedupe (live
                # pages can shift). Never emit duplicate keys in the final
                # tuple; product service also rejects duplicates from cache.
                if report_key in seen_keys:
                    continue
                seen_keys.add(report_key)

                pub_raw = row.get("publishDate") or row.get("datetime") or row.get("pubDate")
                published_at = parse_shanghai_datetime(pub_raw, field="published_at")
                keep, excluded = publication_cutoff_keep(published_at, as_of=as_of, now=now)
                if excluded:
                    unknown_excluded = True
                if not keep or published_at is None:
                    if published_at is None:
                        unknown_excluded = True
                    continue
                pub_day = published_at.date()
                if published_from is not None and pub_day < published_from:
                    continue
                if published_to is not None and pub_day > published_to:
                    continue

                # Exact offset on post-cutoff kept stream (handles offset % page_size).
                if skipped < offset:
                    skipped += 1
                    continue
                if len(kept) >= limit:
                    break

                institution = row.get("orgSName") or row.get("orgName")
                if institution is not None and not isinstance(institution, str):
                    institution = None
                analysts_raw = row.get("researcher") or row.get("author")
                analysts: list[str] = []
                if isinstance(analysts_raw, str) and analysts_raw.strip():
                    analysts = [a.strip()[:200] for a in analysts_raw.split(",") if a.strip()]
                rating = row.get("emRatingName") or row.get("rating")
                if rating is not None and not isinstance(rating, str):
                    rating = None
                target = decimal_from_text(row.get("aimPriceT"), field="target_price")
                encode = row.get("encodeUrl") or row.get("infoCode")
                source_url = None
                if isinstance(encode, str) and encode.strip():
                    source_url = sanitize_public_url(
                        f"https://data.eastmoney.com/report/info/{encode.strip()}.html",
                        field="source_url",
                    )
                kept.append(
                    AnalystReportItem(
                        report_key=report_key,
                        title=title.strip()[:500],
                        institution=institution.strip()[:200] if institution else None,
                        analyst_names=tuple(analysts),
                        published_at=published_at,
                        rating=rating.strip()[:64] if rating else None,
                        target_price=target,
                        eps_forecasts=(),
                        source_url=source_url,
                        pdf_url=None,
                    )
                )

            # Short page ⇒ no further pages (legitimate end of stream).
            if len(rows) < page_size:
                last_page_full = False
                break
            last_page_full = True
            page_no += 1
            if pages_fetched >= page_budget and len(kept) < limit:
                budget_exhausted = True
                break

        # Fail closed: budget exhausted on a full final page without filling
        # the requested post-cutoff page. Do not silently return incomplete.
        if (budget_exhausted or (pages_fetched >= page_budget and last_page_full)) and len(
            kept
        ) < limit:
            raise PartialDataError(
                "report page budget exhausted before offset+limit satisfied",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "reports",
                    "rule": "page_budget_exhausted",
                    "pages_fetched": pages_fetched,
                    "page_budget": page_budget,
                    "kept": len(kept),
                    "limit": limit,
                    "offset": offset,
                    "skipped": skipped,
                },
            )

        # Deterministic output order (never invent rows).
        kept.sort(key=lambda r: (-r.published_at.timestamp(), r.report_key))
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        meta = self._meta(
            category=DataCategory.RESEARCH_REPORTS,
            as_of=as_of,
            fetched_at=fetched_at,
            session=session,
        )
        if unknown_excluded:
            from dataclasses import replace as _replace

            meta = _replace(meta, warnings=("PUBLICATION_TIME_UNKNOWN_EXCLUDED",))
        return ProviderSuccess(value=tuple(kept), meta=meta)

    async def get_consensus(
        self, instrument: Instrument, *, as_of: datetime
    ) -> ProviderSuccess[tuple[ConsensusEstimate, ...]]:
        """Consensus via reportapi ranking fields when present (primary).

        At most one explicit year/EPS pair per row; malformed rows ignored
        deterministically; a single institution/report is never double-counted.
        """
        self._require_configured()
        self._require_as_of(as_of)
        code6, _ = require_a_share_instrument(instrument)
        response = await self._gated_send(
            HttpRequest(
                method="GET",
                url=_REPORT_LIST_URL,
                params={
                    "pageNo": "1",
                    "pageSize": "20",
                    "code": code6,
                    "qType": "0",
                },
                headers=self._headers(),
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="consensus")
        self._require_json_content_type(response.headers, operation="consensus")
        payload = loads_json_decimal(response.body)
        if not isinstance(payload, dict):
            raise DataContractError(
                "Eastmoney consensus payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "consensus",
                    "rule": "contract_drift",
                },
            )
        rows = payload.get("data")
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise DataContractError(
                "Eastmoney consensus data must be a list",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "consensus",
                    "rule": "contract_drift",
                },
            )
        # year -> list of (dedupe_key, eps)
        by_year: dict[int, list[tuple[str, Decimal]]] = {}
        seen_pair: set[tuple[int, str]] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue  # ignore malformed rows deterministically
            year: int | None = None
            eps: Decimal | None = None
            # At most one explicit year/EPS pair per row (first match wins).
            for ykey, ekey in (
                ("predictYear", "predictThisYearEps"),
                ("year", "eps"),
                ("fiscal_year", "mean"),
            ):
                y = row.get(ykey)
                e = row.get(ekey)
                if y is None or e is None:
                    continue
                try:
                    year = int(y) if not isinstance(y, int) or isinstance(y, bool) else y
                except (TypeError, ValueError):
                    year = None
                    continue
                if year < 1990 or year > 2100:
                    year = None
                    continue
                eps = decimal_from_text(e, field="eps")
                if eps is None:
                    year = None
                    continue
                break  # one pair only
            if year is None or eps is None:
                continue
            # Dedup key: prefer report/info id, else institution name, else row hash-ish.
            inst = row.get("orgSName") or row.get("orgName") or row.get("orgCode")
            rkey = row.get("infoCode") or row.get("id") or row.get("encodeUrl")
            if isinstance(rkey, str) and rkey.strip():
                dedupe = f"r:{rkey.strip()}"
            elif isinstance(inst, str) and inst.strip():
                dedupe = f"i:{inst.strip()}"
            else:
                dedupe = f"e:{year}:{eps}"
            pair_key = (year, dedupe)
            if pair_key in seen_pair:
                continue
            seen_pair.add(pair_key)
            by_year.setdefault(year, []).append((dedupe, eps))
        estimates: list[ConsensusEstimate] = []
        for year in sorted(by_year):
            vals = [eps for _, eps in by_year[year]]
            mean = sum(vals) / Decimal(len(vals))
            estimates.append(
                ConsensusEstimate(
                    fiscal_year=year,
                    metric="eps",
                    mean=mean,
                    high=max(vals),
                    low=min(vals),
                    institution_count=len(vals),
                )
            )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        if not estimates:
            raise NoMarketData(
                "provider returned no market data",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "consensus",
                },
            )
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=tuple(estimates),
            meta=self._meta(
                category=DataCategory.RESEARCH_REPORTS,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
            ),
        )

    async def get_news(
        self,
        instrument: Instrument | None,
        *,
        start: datetime,
        end: datetime,
        limit: int,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[NewsItem, ...]]:
        self._require_configured()
        self._require_as_of(as_of)
        require_aware_datetime(start, field_name="start")
        require_aware_datetime(end, field_name="end")
        if end < start:
            raise DataContractError(
                "end must be >= start",
                details={"field": "end", "rule": "range_order"},
            )
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 100:
            raise DataContractError(
                "limit must be an int in 1..100",
                details={"field": "limit", "rule": "range"},
            )
        params: dict[str, str] = {
            "client": "web",
            "biz": "web_news_col",
            "fastColumn": "102",
            "pageSize": str(limit),
            "pageNo": "1",
        }
        if instrument is not None:
            code6, suffix = require_a_share_instrument(instrument)
            params["code"] = f"{code6}.{suffix}"
        response = await self._gated_send(
            HttpRequest(
                method="GET",
                url=_NEWS_LIST_URL,
                params=params,
                headers=self._headers(),
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="news")
        self._require_json_content_type(response.headers, operation="news")
        payload = loads_json_decimal(response.body)
        if not isinstance(payload, dict):
            raise DataContractError(
                "Eastmoney news payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "news",
                    "rule": "contract_drift",
                },
            )
        data = payload.get("data")
        rows: list[Any]
        if data is None:
            rows = []
        elif isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            inner = data.get("list") or data.get("items") or data.get("news")
            if inner is None:
                rows = []
            elif isinstance(inner, list):
                rows = inner
            else:
                raise DataContractError(
                    "Eastmoney news list failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "news",
                        "rule": "contract_drift",
                    },
                )
        else:
            raise DataContractError(
                "Eastmoney news data failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "news",
                    "rule": "contract_drift",
                },
            )
        items: list[NewsItem] = []
        for idx, row in enumerate(rows):
            if len(items) >= limit:
                break
            if not isinstance(row, dict):
                raise DataContractError(
                    "Eastmoney news row failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "news",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            key = row.get("code") or row.get("id") or row.get("newsid") or row.get("art_code")
            title = row.get("title") or row.get("showTitle")
            if key is None or (isinstance(key, str) and not str(key).strip()):
                raise DataContractError(
                    "Eastmoney news missing key",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "news",
                        "rule": "contract_drift",
                    },
                )
            if not isinstance(title, str) or not title.strip():
                raise DataContractError(
                    "Eastmoney news missing title",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "news",
                        "rule": "contract_drift",
                    },
                )
            pub_raw = (
                row.get("showTime") or row.get("publishTime") or row.get("time") or row.get("date")
            )
            published_at = parse_shanghai_datetime(pub_raw, field="published_at")
            if published_at is None:
                continue
            if published_at < start or published_at > end or published_at > as_of:
                continue
            summary = row.get("summary") or row.get("digest") or row.get("content")
            if summary is not None and not isinstance(summary, str):
                summary = None
            if isinstance(summary, str):
                summary = summary[:4000]
            url_raw = row.get("url") or row.get("source_url")
            source_url = None
            if isinstance(url_raw, str) and url_raw.strip():
                source_url = sanitize_public_url(url_raw, field="source_url")
            else:
                source_url = sanitize_public_url(
                    f"https://finance.eastmoney.com/a/{str(key).strip()}.html",
                    field="source_url",
                )
            source_name = row.get("mediaName") or row.get("source") or "东方财富"
            if not isinstance(source_name, str) or not source_name.strip():
                source_name = "东方财富"
            items.append(
                NewsItem(
                    news_key=str(key).strip()[:200],
                    title=title.strip()[:500],
                    summary=summary,
                    published_at=published_at,
                    source_name=source_name.strip()[:200],
                    source_url=source_url,
                )
            )
        items.sort(key=lambda n: (-n.published_at.timestamp(), n.news_key))
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=tuple(items),
            meta=self._meta(
                category=DataCategory.NEWS,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
            ),
        )

    async def get_announcements(
        self, instrument: Instrument, *, limit: int, as_of: datetime
    ) -> ProviderSuccess[tuple[Any, ...]]:
        # Announcements chain is cninfo → exchanges; Eastmoney does not own it.
        raise DataContractError(
            "eastmoney does not implement announcements in Phase 1E E3",
            details={
                "vendor": self.vendor_id.value,
                "operation": "announcements",
                "rule": "unsupported",
                "category": DataCategory.ANNOUNCEMENTS.value,
            },
        )

    # --- E4a capital / chips ---------------------------------------------------

    async def get_intraday_flow(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[tuple[FundFlowPoint, ...]]:
        self._require_configured()
        self._require_as_of(as_of)
        code6, suffix = require_a_share_instrument(instrument)
        secid = eastmoney_secid(code6, suffix)
        response = await self._gated_send(
            HttpRequest(
                method="GET",
                url=_INTRADAY_FLOW_URL,
                params={
                    "lmt": "0",
                    "klt": "1",
                    "secid": secid,
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                },
                headers=self._headers(),
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="intraday_flow")
        self._require_json_content_type(response.headers, operation="intraday_flow")
        payload = loads_json_decimal(response.body)
        points = self._parse_fflow_klines(
            payload,
            operation="intraday_flow",
            interval=BarInterval.ONE_MINUTE,
            as_of=as_of,
            start=None,
            end=None,
        )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=tuple(points),
            meta=self._meta(
                category=DataCategory.CAPITAL,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
            ),
        )
