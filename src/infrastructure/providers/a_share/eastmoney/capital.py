"""Capital and ownership Eastmoney endpoint implementation."""

# Mixin attributes are supplied by EastmoneyAShareAdapter.
# mypy: disable-error-code="attr-defined"

from __future__ import annotations

from infrastructure.providers.a_share.eastmoney.common import (
    _DAILY_FLOW_URL,
    _KLINE_URL,
    _MILLION_CNY,
    _NORTHBOUND_MUTUAL_TYPES,
    SHANGHAI,
    AdjustmentMethod,
    AssetType,
    BarInterval,
    BlockTradeRecord,
    ChipDistributionBin,
    ChipDistributionSnapshot,
    ChipInputBar,
    DataCategory,
    DataContractError,
    DragonTigerRecord,
    DragonTigerSeat,
    FundFlowPoint,
    HttpRequest,
    Instrument,
    MarginRecord,
    Market,
    NorthboundFlowPoint,
    ProviderSuccess,
    ReliabilityLevel,
    ShareholderCountRecord,
    VendorId,
    combine_shanghai_date_time,
    date,
    datetime,
    decimal_from_text,
    derive_tp_chip_v1,
    eastmoney_secid,
    infer_session_basic,
    instrument_id_from_code,
    int_from_text,
    loads_json_decimal,
    parse_shanghai_date,
    parse_shanghai_datetime,
    publication_cutoff_keep,
    require_a_share_instrument,
    require_aware_datetime,
    require_decimal,
    require_exact_date,
    require_int,
)


class EastmoneyCapitalMixin:
    async def get_daily_flow(
        self,
        instrument: Instrument,
        *,
        start: date | None,
        end: date | None,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[FundFlowPoint, ...]]:
        self._require_configured()
        self._require_as_of(as_of)
        if start is not None and end is not None and end < start:
            raise DataContractError(
                "end must be >= start",
                details={"field": "end", "rule": "range_order"},
            )
        code6, suffix = require_a_share_instrument(instrument)
        secid = eastmoney_secid(code6, suffix)
        response = await self._gated_send(
            HttpRequest(
                method="GET",
                url=_DAILY_FLOW_URL,
                params={
                    "lmt": "0",
                    "klt": "101",
                    "secid": secid,
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                },
                headers=self._headers(),
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="daily_flow")
        self._require_json_content_type(response.headers, operation="daily_flow")
        payload = loads_json_decimal(response.body)
        points = self._parse_fflow_klines(
            payload,
            operation="daily_flow",
            interval=BarInterval.ONE_DAY,
            as_of=as_of,
            start=start,
            end=end,
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

    def _parse_fflow_klines(
        self,
        payload: object,
        *,
        operation: str,
        interval: BarInterval,
        as_of: datetime,
        start: date | None,
        end: date | None,
    ) -> list[FundFlowPoint]:
        if not isinstance(payload, dict):
            raise DataContractError(
                "Eastmoney fund-flow payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "contract_drift",
                },
            )
        data = payload.get("data")
        if data is None:
            return []
        if not isinstance(data, dict):
            raise DataContractError(
                "Eastmoney fund-flow data failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "contract_drift",
                },
            )
        klines = data.get("klines")
        if klines is None:
            return []
        if not isinstance(klines, list):
            raise DataContractError(
                "Eastmoney fund-flow klines must be a list",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "contract_drift",
                },
            )
        points: list[FundFlowPoint] = []
        for idx, raw in enumerate(klines):
            if not isinstance(raw, str) or not raw.strip():
                raise DataContractError(
                    "Eastmoney fund-flow kline row failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": operation,
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            parts = raw.split(",")
            if len(parts) < 6:
                raise DataContractError(
                    "Eastmoney fund-flow kline row missing fields",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": operation,
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            ts_raw = parts[0].strip()
            if interval is BarInterval.ONE_DAY:
                day = parse_shanghai_date(ts_raw)
                if start is not None and day < start:
                    continue
                if end is not None and day > end:
                    continue
                occurred_at: datetime = combine_shanghai_date_time(day, "15:00:00")
            else:
                parsed_at = parse_shanghai_datetime(ts_raw, field="occurred_at")
                if parsed_at is None:
                    raise DataContractError(
                        "Eastmoney fund-flow timestamp missing",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": operation,
                            "rule": "contract_drift",
                            "index": idx,
                        },
                    )
                occurred_at = parsed_at
            if occurred_at > as_of:
                continue
            points.append(
                FundFlowPoint(
                    occurred_at=occurred_at,
                    interval=interval,
                    main_net_cny=decimal_from_text(parts[1], field="main_net_cny"),
                    super_large_net_cny=decimal_from_text(parts[2], field="super_large_net_cny"),
                    large_net_cny=decimal_from_text(parts[3], field="large_net_cny"),
                    medium_net_cny=decimal_from_text(parts[4], field="medium_net_cny"),
                    small_net_cny=decimal_from_text(parts[5], field="small_net_cny"),
                    source_vendor=VendorId.EASTMONEY,
                    reliability=ReliabilityLevel.MEDIUM,
                    is_authoritative=False,
                )
            )
        points.sort(key=lambda p: p.occurred_at)
        return points

    async def get_northbound(
        self, *, start: date | None, end: date | None, as_of: datetime
    ) -> ProviderSuccess[tuple[NorthboundFlowPoint, ...]]:
        self._require_configured()
        now = self._require_as_of(as_of)
        if start is not None and end is not None and end < start:
            raise DataContractError(
                "end must be >= start",
                details={"field": "end", "rule": "range_order"},
            )
        filter_expr = ""
        if start is not None and end is not None:
            filter_expr = f"(TRADE_DATE>='{start.isoformat()}')(TRADE_DATE<='{end.isoformat()}')"
        elif start is not None:
            filter_expr = f"(TRADE_DATE>='{start.isoformat()}')"
        elif end is not None:
            filter_expr = f"(TRADE_DATE<='{end.isoformat()}')"
        payload = await self._datacenter_get(
            report_name="RPT_MUTUAL_DEAL_HISTORY",
            filter_expr=filter_expr,
            columns=("TRADE_DATE,MUTUAL_TYPE,BUY_AMT,SELL_AMT,NET_DEAL_AMT,FUND_INFLOW"),
            page_size=100,
            operation="northbound",
            sort_columns="TRADE_DATE",
            sort_types="-1",
        )
        points: list[NorthboundFlowPoint] = []
        incomplete = False
        for row in self._require_datacenter_rows(payload, operation="northbound"):
            mutual = row.get("MUTUAL_TYPE")
            if not isinstance(mutual, str):
                mutual = str(mutual) if mutual is not None else ""
            channel = _NORTHBOUND_MUTUAL_TYPES.get(mutual.strip())
            if channel is None:
                continue
            trade_raw = row.get("TRADE_DATE")
            if not isinstance(trade_raw, str) or not trade_raw.strip():
                raise DataContractError(
                    "Eastmoney northbound missing TRADE_DATE",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "northbound",
                        "rule": "contract_drift",
                    },
                )
            trade_date = parse_shanghai_date(trade_raw)
            if start is not None and trade_date < start:
                continue
            if end is not None and trade_date > end:
                continue
            as_of_day = as_of.astimezone(SHANGHAI).date()
            if trade_date > as_of_day:
                continue
            buy = decimal_from_text(row.get("BUY_AMT"), field="buy_cny")
            sell = decimal_from_text(row.get("SELL_AMT"), field="sell_cny")
            net = decimal_from_text(row.get("NET_DEAL_AMT"), field="net_buy_cny")
            if buy is None and sell is None and net is None:
                incomplete = True
            # Vendor unit is 百万元 → CNY.
            buy_cny = buy * _MILLION_CNY if buy is not None else None
            sell_cny = sell * _MILLION_CNY if sell is not None else None
            net_cny = net * _MILLION_CNY if net is not None else None
            points.append(
                NorthboundFlowPoint(
                    trade_date=trade_date,
                    channel=channel,
                    net_buy_cny=net_cny,
                    buy_cny=buy_cny,
                    sell_cny=sell_cny,
                    disclosure_note=("Eastmoney mutual deal history; not HKEX official daily"),
                    source_vendor=VendorId.EASTMONEY,
                    reliability=ReliabilityLevel.LOW,
                    is_authoritative=False,
                )
            )
        points.sort(key=lambda p: (p.trade_date, p.channel))
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        warn_codes: list[str] = [
            "NORTHBOUND_DISCLOSURE_INCOMPLETE",
            "LOW_RELIABILITY_MARKET_SIGNAL",
        ]
        if incomplete:
            pass  # already flagged via disclosure_note + warnings
        meta = self._meta(
            category=DataCategory.CAPITAL,
            as_of=as_of,
            fetched_at=fetched_at,
            session=session,
        )
        from dataclasses import replace as _replace

        meta = _replace(meta, warnings=tuple(warn_codes))
        _ = now
        return ProviderSuccess(value=tuple(points), meta=meta)

    async def get_dragon_tiger(
        self,
        instrument: Instrument | None,
        *,
        trade_date: date,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[DragonTigerRecord, ...]]:
        self._require_configured()
        self._require_as_of(as_of)
        trade_date = require_exact_date(trade_date, field="trade_date")
        as_of_day = as_of.astimezone(SHANGHAI).date()
        if trade_date > as_of_day:
            raise DataContractError(
                "trade_date must not be later than as_of local day",
                details={"field": "trade_date", "rule": "as_of_cutoff"},
            )
        day_s = trade_date.isoformat()
        filter_expr = f"(TRADE_DATE='{day_s}')"
        code6: str | None = None
        if instrument is not None:
            code6, _suffix = require_a_share_instrument(instrument)
            filter_expr = f"(TRADE_DATE='{day_s}')(SECURITY_CODE=\"{code6}\")"
        summary_payload = await self._datacenter_get(
            report_name="RPT_DAILYBILLBOARD_DETAILSNEW",
            filter_expr=filter_expr,
            columns=(
                "TRADE_DATE,SECUCODE,SECURITY_CODE,EXPLANATION,EXPLAIN,"
                "BILLBOARD_BUY_AMT,BILLBOARD_SELL_AMT,BILLBOARD_NET_AMT,"
                "SUM_BUY_AMT,SUM_SELL_AMT,NET_BS_AMT"
            ),
            page_size=50,
            operation="dragon_tiger",
            sort_columns="TRADE_DATE",
            sort_types="-1",
        )
        rows = self._require_datacenter_rows(summary_payload, operation="dragon_tiger")
        records: list[DragonTigerRecord] = []
        for row in rows:
            sec = row.get("SECUCODE") or row.get("SECURITY_CODE")
            if not isinstance(sec, str) or not sec.strip():
                raise DataContractError(
                    "Eastmoney dragon tiger missing SECUCODE",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "dragon_tiger",
                        "rule": "contract_drift",
                    },
                )
            sec_u = sec.strip().upper()
            if "." not in sec_u:
                # SECURITY_CODE alone — require instrument for identity.
                if instrument is None:
                    continue
                inst_id = instrument.instrument_id
            else:
                code_part, suffix_part = sec_u.rsplit(".", 1)
                inst_id = instrument_id_from_code(
                    code_part.zfill(6), suffix_part, asset=AssetType.EQUITY
                )
            if instrument is not None and inst_id != instrument.instrument_id:
                continue
            reason = row.get("EXPLANATION") or row.get("EXPLAIN") or "dragon_tiger"
            if not isinstance(reason, str) or not reason.strip():
                reason = "dragon_tiger"
            buy = require_decimal(
                row.get("BILLBOARD_BUY_AMT")
                if row.get("BILLBOARD_BUY_AMT") is not None
                else row.get("SUM_BUY_AMT"),
                field="buy_total_cny",
            )
            sell = require_decimal(
                row.get("BILLBOARD_SELL_AMT")
                if row.get("BILLBOARD_SELL_AMT") is not None
                else row.get("SUM_SELL_AMT"),
                field="sell_total_cny",
            )
            net = decimal_from_text(
                row.get("BILLBOARD_NET_AMT")
                if row.get("BILLBOARD_NET_AMT") is not None
                else row.get("NET_BS_AMT"),
                field="net_buy_cny",
            )
            if net is None:
                net = buy - sell
            seats = await self._fetch_dragon_seats(
                trade_date=trade_date,
                security_code=code6 or sec_u.split(".")[0].zfill(6),
            )
            records.append(
                DragonTigerRecord(
                    trade_date=trade_date,
                    instrument_id=inst_id,
                    reason=reason.strip()[:500],
                    buy_total_cny=buy,
                    sell_total_cny=sell,
                    net_buy_cny=net,
                    seats=tuple(seats),
                    source_vendor=VendorId.EASTMONEY,
                    reliability=ReliabilityLevel.MEDIUM,
                    is_authoritative=False,
                )
            )
        records.sort(key=lambda r: (r.instrument_id, r.reason))
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=tuple(records),
            meta=self._meta(
                category=DataCategory.CAPITAL,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
            ),
        )

    async def _fetch_dragon_seats(
        self, *, trade_date: date, security_code: str
    ) -> list[DragonTigerSeat]:
        day_s = trade_date.isoformat()
        payload = await self._datacenter_get(
            report_name="RPT_OPERATEDEPT_TRADE",
            filter_expr=(f"(TRADE_DATE='{day_s}')(SECURITY_CODE=\"{security_code}\")"),
            columns=("RANK,TRADE_DIRECTION,OPERATEDEPT_NAME,BUY_AMT_REAL,SELL_AMT_REAL,NET"),
            page_size=40,
            operation="dragon_tiger",
            sort_columns="RANK",
            sort_types="1",
        )
        seats: list[DragonTigerSeat] = []
        for row in self._require_datacenter_rows(payload, operation="dragon_tiger"):
            rank = require_int(row.get("RANK"), field="rank")
            direction = row.get("TRADE_DIRECTION")
            direction_s = str(direction).strip() if direction is not None else "0"
            # 0 = buy ranking, 1 = sell ranking (Eastmoney convention).
            side = "sell" if direction_s in {"1", "卖", "sell"} else "buy"
            branch = row.get("OPERATEDEPT_NAME")
            if not isinstance(branch, str) or not branch.strip():
                continue
            if side == "buy":
                amount = require_decimal(row.get("BUY_AMT_REAL"), field="amount_cny")
            else:
                amount = require_decimal(row.get("SELL_AMT_REAL"), field="amount_cny")
            name_l = branch.strip()
            is_inst = "机构" in name_l or "专用" in name_l
            seats.append(
                DragonTigerSeat(
                    rank=rank,
                    side=side,
                    branch_name=name_l[:200],
                    amount_cny=amount,
                    is_institution=is_inst if is_inst else None,
                )
            )
        return seats

    async def get_margin(
        self, instrument: Instrument, *, limit: int, as_of: datetime
    ) -> ProviderSuccess[tuple[MarginRecord, ...]]:
        self._require_configured()
        self._require_as_of(as_of)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise DataContractError(
                "limit must be a positive int",
                details={"field": "limit", "rule": "positive"},
            )
        code6, _suffix = require_a_share_instrument(instrument)
        payload = await self._datacenter_get(
            report_name="RPTA_WEB_RZRQ_GGMX",
            filter_expr=f'(SCODE="{code6}")',
            columns="DATE,SCODE,RZYE,RZMRE,RZCHE,RQYE,RQYL,RQCHL",
            page_size=min(limit, 100),
            operation="margin",
            sort_columns="DATE",
            sort_types="-1",
        )
        as_of_day = as_of.astimezone(SHANGHAI).date()
        records: list[MarginRecord] = []
        for row in self._require_datacenter_rows(payload, operation="margin"):
            day_raw = row.get("DATE")
            if not isinstance(day_raw, str) or not day_raw.strip():
                raise DataContractError(
                    "Eastmoney margin missing DATE",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "margin",
                        "rule": "contract_drift",
                    },
                )
            trade_date = parse_shanghai_date(day_raw)
            if trade_date > as_of_day:
                continue
            records.append(
                MarginRecord(
                    trade_date=trade_date,
                    financing_balance_cny=require_decimal(
                        row.get("RZYE"), field="financing_balance_cny"
                    ),
                    financing_buy_cny=require_decimal(row.get("RZMRE"), field="financing_buy_cny"),
                    financing_repayment_cny=require_decimal(
                        row.get("RZCHE"), field="financing_repayment_cny"
                    ),
                    securities_lending_balance_cny=decimal_from_text(
                        row.get("RQYE"), field="securities_lending_balance_cny"
                    ),
                    securities_lending_sell_shares=int_from_text(
                        row.get("RQYL"), field="securities_lending_sell_shares"
                    ),
                    source_vendor=VendorId.EASTMONEY,
                    reliability=ReliabilityLevel.MEDIUM,
                    is_authoritative=False,
                )
            )
            if len(records) >= limit:
                break
        records.sort(key=lambda r: r.trade_date, reverse=True)
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=tuple(records),
            meta=self._meta(
                category=DataCategory.CAPITAL,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
            ),
        )

    async def get_block_trades(
        self, instrument: Instrument, *, limit: int, as_of: datetime
    ) -> ProviderSuccess[tuple[BlockTradeRecord, ...]]:
        self._require_configured()
        self._require_as_of(as_of)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise DataContractError(
                "limit must be a positive int",
                details={"field": "limit", "rule": "positive"},
            )
        code6, _suffix = require_a_share_instrument(instrument)
        payload = await self._datacenter_get(
            report_name="RPT_DATA_BLOCKTRADE",
            filter_expr=f'(SECURITY_CODE="{code6}")',
            columns=(
                "TRADE_DATE,DEAL_PRICE,DEAL_VOLUME,DEAL_AMT,PREMIUM_RATIO,BUYER_NAME,SELLER_NAME"
            ),
            page_size=min(limit, 100),
            operation="block_trades",
            sort_columns="TRADE_DATE",
            sort_types="-1",
        )
        as_of_day = as_of.astimezone(SHANGHAI).date()
        records: list[BlockTradeRecord] = []
        for row in self._require_datacenter_rows(payload, operation="block_trades"):
            day_raw = row.get("TRADE_DATE")
            if not isinstance(day_raw, str) or not day_raw.strip():
                raise DataContractError(
                    "Eastmoney block trade missing TRADE_DATE",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "block_trades",
                        "rule": "contract_drift",
                    },
                )
            trade_date = parse_shanghai_date(day_raw)
            if trade_date > as_of_day:
                continue
            buyer = row.get("BUYER_NAME")
            seller = row.get("SELLER_NAME")
            records.append(
                BlockTradeRecord(
                    trade_date=trade_date,
                    price=require_decimal(row.get("DEAL_PRICE"), field="price"),
                    volume_shares=require_int(row.get("DEAL_VOLUME"), field="volume_shares"),
                    amount_cny=require_decimal(row.get("DEAL_AMT"), field="amount_cny"),
                    premium_percent=decimal_from_text(
                        row.get("PREMIUM_RATIO"), field="premium_percent"
                    ),
                    buyer_branch=(
                        buyer.strip()[:200] if isinstance(buyer, str) and buyer.strip() else None
                    ),
                    seller_branch=(
                        seller.strip()[:200] if isinstance(seller, str) and seller.strip() else None
                    ),
                    source_vendor=VendorId.EASTMONEY,
                    reliability=ReliabilityLevel.MEDIUM,
                    is_authoritative=False,
                )
            )
            if len(records) >= limit:
                break
        records.sort(
            key=lambda r: (
                -r.trade_date.toordinal(),
                r.price,
                r.volume_shares,
                r.amount_cny,
                r.buyer_branch or "",
                r.seller_branch or "",
            )
        )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=tuple(records),
            meta=self._meta(
                category=DataCategory.CAPITAL,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
            ),
        )

    async def get_shareholder_counts(
        self, instrument: Instrument, *, limit: int, as_of: datetime
    ) -> ProviderSuccess[tuple[ShareholderCountRecord, ...]]:
        self._require_configured()
        now = self._require_as_of(as_of)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise DataContractError(
                "limit must be a positive int",
                details={"field": "limit", "rule": "positive"},
            )
        code6, suffix = require_a_share_instrument(instrument)
        secucode = f"{code6}.{suffix}"
        payload = await self._datacenter_get(
            report_name="RPT_F10_EH_HOLDERNUM",
            filter_expr=f'(SECUCODE="{secucode}")',
            columns=("END_DATE,NOTICE_DATE,HOLDER_TOTAL_NUM,TOTAL_NUM_RATIO,AVG_FREE_SHARES"),
            page_size=min(limit, 100),
            operation="shareholder_counts",
            sort_columns="END_DATE",
            sort_types="-1",
        )
        records: list[ShareholderCountRecord] = []
        unknown_excluded = False
        for row in self._require_datacenter_rows(payload, operation="shareholder_counts"):
            end_raw = row.get("END_DATE")
            if not isinstance(end_raw, str) or not end_raw.strip():
                raise DataContractError(
                    "Eastmoney shareholder missing END_DATE",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "shareholder_counts",
                        "rule": "contract_drift",
                    },
                )
            period_end = parse_shanghai_date(end_raw)
            published_at = parse_shanghai_datetime(row.get("NOTICE_DATE"), field="published_at")
            keep, excluded = publication_cutoff_keep(published_at, as_of=as_of, now=now)
            if excluded:
                unknown_excluded = True
            if not keep:
                continue
            records.append(
                ShareholderCountRecord(
                    period_end=period_end,
                    published_at=published_at,
                    shareholder_count=require_int(
                        row.get("HOLDER_TOTAL_NUM"), field="shareholder_count"
                    ),
                    change_percent=decimal_from_text(
                        row.get("TOTAL_NUM_RATIO"), field="change_percent"
                    ),
                    average_holding_shares=decimal_from_text(
                        row.get("AVG_FREE_SHARES"), field="average_holding_shares"
                    ),
                    source_vendor=VendorId.EASTMONEY,
                    reliability=ReliabilityLevel.MEDIUM,
                    is_authoritative=False,
                )
            )
            if len(records) >= limit:
                break
        records.sort(
            key=lambda r: (
                -r.period_end.toordinal(),
                0 if r.published_at is not None else 1,
                -(r.published_at.timestamp()) if r.published_at is not None else 0.0,
            )
        )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        meta = self._meta(
            category=DataCategory.CAPITAL,
            as_of=as_of,
            fetched_at=fetched_at,
            session=session,
        )
        if unknown_excluded:
            from dataclasses import replace as _replace

            meta = _replace(meta, warnings=("PUBLICATION_TIME_UNKNOWN_EXCLUDED",))
        return ProviderSuccess(value=tuple(records), meta=meta)

    async def get_chip_distribution(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[ChipDistributionSnapshot]:
        self._require_configured()
        sampled_now = self._require_as_of(as_of)
        code6, suffix = require_a_share_instrument(instrument)
        if instrument.asset_type is not AssetType.EQUITY:
            raise DataContractError(
                "chip distribution supports equity only",
                details={"field": "instrument", "rule": "asset_support"},
            )
        local_day = as_of.astimezone(SHANGHAI).date()
        sessions = self._calendar.sessions_for(local_day)
        end_day = (
            local_day
            if sessions and as_of >= sessions[-1].end_at
            else self._calendar.previous_trading_day(local_day)
        )
        reverse_days: list[date] = []
        day = end_day
        while len(reverse_days) < 120:
            reverse_days.append(day)
            if len(reverse_days) < 120:
                day = self._calendar.previous_trading_day(day)
        expected_days = tuple(reversed(reverse_days))
        response = await self._gated_send(
            HttpRequest(
                method="GET",
                url=_KLINE_URL,
                params={
                    "secid": eastmoney_secid(code6, suffix),
                    "klt": "101",
                    "fqt": "1",
                    "beg": expected_days[0].strftime("%Y%m%d"),
                    "end": end_day.strftime("%Y%m%d"),
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                },
                headers=self._headers(),
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="chip_distribution")
        self._require_json_content_type(response.headers, operation="chip_distribution")
        data = self._require_data_object(
            loads_json_decimal(response.body), operation="chip_distribution"
        )
        if data is None:
            raise DataContractError(
                "chip input is missing required sessions",
                details={"field": "data", "rule": "missing_session"},
            )
        response_code = data.get("code")
        response_market = data.get("market")
        expected_market = 1 if suffix == "SH" else 0
        if (
            response_code != code6
            or type(response_market) is not int
            or response_market != expected_market
        ):
            raise DataContractError(
                "chip input identity does not match requested instrument",
                details={"field": "data", "rule": "identity_mismatch"},
            )
        rows = data.get("klines")
        if not isinstance(rows, list) or len(rows) != 120:
            raise DataContractError(
                "chip rows must contain 120 sessions",
                details={"field": "klines", "rule": "exact_120"},
            )
        dates: list[date] = []
        inputs: list[ChipInputBar] = []
        for index, raw in enumerate(rows):
            if not isinstance(raw, str):
                raise DataContractError(
                    "chip kline row must be string",
                    details={"field": "klines", "index": index, "rule": "type"},
                )
            parts = raw.split(",")
            if len(parts) != 11:
                raise DataContractError(
                    "chip kline row width mismatch",
                    details={"field": "klines", "index": index, "rule": "width"},
                )
            date_text = parts[0].strip()
            try:
                parsed_date = date.fromisoformat(date_text)
            except ValueError as exc:
                raise DataContractError(
                    "chip kline date invalid",
                    details={"field": "klines", "index": index, "rule": "date"},
                ) from exc
            if parsed_date.isoformat() != date_text:
                raise DataContractError(
                    "chip kline date invalid",
                    details={"field": "klines", "index": index, "rule": "date"},
                )
            dates.append(parsed_date)
            open_price = require_decimal(parts[1], field=f"chip[{index}].open")
            close_price = require_decimal(parts[2], field=f"chip[{index}].close")
            high_price = require_decimal(parts[3], field=f"chip[{index}].high")
            low_price = require_decimal(parts[4], field=f"chip[{index}].low")
            volume = require_decimal(parts[5], field=f"chip[{index}].volume")
            amount = require_decimal(parts[6], field=f"chip[{index}].amount")
            require_decimal(parts[7], field=f"chip[{index}].amplitude")
            require_decimal(parts[8], field=f"chip[{index}].pct_change")
            require_decimal(parts[9], field=f"chip[{index}].change")
            turnover_percent = require_decimal(
                parts[10], field=f"chip[{index}].turnover_percent"
            )
            if (
                min(open_price, close_price, high_price, low_price) <= 0
                or high_price < max(open_price, close_price, low_price)
                or low_price > min(open_price, close_price, high_price)
                or volume < 0
                or amount < 0
                or turnover_percent < 0
            ):
                raise DataContractError(
                    "chip kline numeric range mismatch",
                    details={"field": "klines", "index": index, "rule": "range"},
                )
            inputs.append(
                ChipInputBar(
                    low=low_price,
                    high=high_price,
                    close=close_price,
                    turnover_percent=turnover_percent,
                )
            )
        if tuple(dates) != expected_days:
            raise DataContractError(
                "chip sessions mismatch", details={"field": "klines", "rule": "session_sequence"}
            )
        result = derive_tp_chip_v1(inputs)
        snapshot_at = self._calendar.sessions_for(end_day)[-1].end_at
        fetched_at = sampled_now
        from dataclasses import replace

        meta = self._meta(
            category=DataCategory.CAPITAL,
            as_of=as_of,
            fetched_at=fetched_at,
            session=infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai"),
            data_timestamp=snapshot_at,
            adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
        )
        meta = replace(meta, warnings=("DERIVED_CHIP_DISTRIBUTION",))
        return ProviderSuccess(
            value=ChipDistributionSnapshot(
                as_of=snapshot_at,
                bins=tuple(
                    ChipDistributionBin(
                        price_low=result.edges[i],
                        price_high=result.edges[i + 1],
                        holding_ratio=result.weights[i],
                    )
                    for i in range(len(result.weights))
                ),
                profit_ratio=result.profit_ratio,
                average_cost=result.average_cost,
                concentration_90=result.concentration_90,
                concentration_70=result.concentration_70,
                source_vendor=VendorId.EASTMONEY,
                reliability=ReliabilityLevel.LOW,
                is_authoritative=False,
                calculation_method="turnover_decay_uniform_range",
                algorithm_version="tp_chip_v1",
                lookback_sessions=120,
                input_adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
                bar_trade_date=end_day,
            ),
            meta=meta,
        )

    # --- E4b limit pools + sentiment -----------------------------------------
