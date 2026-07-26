"""Limit-pool and sentiment Eastmoney endpoint implementation."""

# Mixin attributes are supplied by EastmoneyAShareAdapter.
# mypy: disable-error-code="attr-defined"

from __future__ import annotations

from infrastructure.providers.a_share.eastmoney.common import (
    _EM_M_TO_SUFFIX,
    _POOL_SORT_BY_TYPE,
    _POOL_URL_BY_TYPE,
    _STOCKRANK_ALL_CURRENT_URL,
    _STOCKRANK_CONCEPT_HEAT_URL,
    SHANGHAI,
    Any,
    AssetType,
    DataCategory,
    DataContractError,
    Decimal,
    HttpRequest,
    Instrument,
    LimitPoolEntry,
    LimitPoolType,
    LimitUpContext,
    LimitUpLadderRung,
    Mapping,
    Market,
    ProviderSuccess,
    ProviderUnavailableError,
    ReliabilityLevel,
    SentimentSignal,
    SentimentSourceType,
    VendorId,
    combine_shanghai_date_time,
    date,
    datetime,
    decimal_from_text,
    infer_session_basic,
    instrument_id_from_code,
    loads_json_decimal,
    require_a_share_instrument,
    require_aware_datetime,
    require_decimal,
    require_exact_date,
    require_int,
)


class EastmoneySentimentMixin:
    async def get_limit_pools(
        self,
        *,
        trade_date: date,
        pools: tuple[LimitPoolType, ...],
        as_of: datetime,
    ) -> ProviderSuccess[LimitUpContext]:
        """Fetch requested push2ex limit pools and compose LimitUpContext.

        Live-verified endpoints (2026-07-17): getTopicZTPool, getTopicZBPool,
        getTopicDTPool, getYesterdayZTPool. Counts/ladder are derived only from
        observed pool rows — no prediction or invented promotion rates.
        """
        self._require_configured()
        trade_date = require_exact_date(trade_date, field="trade_date")
        now = self._require_live_current_clist_as_of(as_of, trade_date, operation="limit_pools")
        self._require_current_clist_trade_date(trade_date, operation="limit_pools", now=now)
        ordered_pools = self._require_limit_pools(pools)
        date_param = trade_date.strftime("%Y%m%d")

        entries: list[LimitPoolEntry] = []
        counts: dict[LimitPoolType, int] = {
            LimitPoolType.LIMIT_UP: 0,
            LimitPoolType.LIMIT_DOWN: 0,
            LimitPoolType.BROKEN_LIMIT: 0,
            LimitPoolType.PREVIOUS_LIMIT_UP: 0,
        }
        for pool_type in ordered_pools:
            url = _POOL_URL_BY_TYPE[pool_type]
            sort = _POOL_SORT_BY_TYPE[pool_type]
            response = await self._gated_send(
                HttpRequest(
                    method="GET",
                    url=url,
                    params=self._pool_params(date_param, sort=sort),
                    headers=self._headers(),
                    body=None,
                    timeout_seconds=self._timeout_seconds,
                )
            )
            self._raise_for_http_status(response.status_code, operation="limit_pools")
            self._require_json_content_type(response.headers, operation="limit_pools")
            pool_entries = self._parse_limit_pool_body(
                response.body,
                pool_type=pool_type,
                trade_date=trade_date,
            )
            counts[pool_type] = len(pool_entries)
            entries.extend(pool_entries)

        # Deterministic order: pool enum order already applied; within pool by
        # instrument_id ascending (stable unique key with pool_type).
        entries.sort(
            key=lambda e: (
                list(LimitPoolType).index(e.pool_type),
                e.instrument_id,
            )
        )
        limit_up_count = counts[LimitPoolType.LIMIT_UP]
        limit_down_count = counts[LimitPoolType.LIMIT_DOWN]
        broken_limit_count = counts[LimitPoolType.BROKEN_LIMIT]
        # Only compute rates from pools that were actually requested.
        broken_rate: Decimal | None = None
        if LimitPoolType.LIMIT_UP in ordered_pools and LimitPoolType.BROKEN_LIMIT in ordered_pools:
            denom = limit_up_count + broken_limit_count
            if denom > 0:
                broken_rate = (Decimal(broken_limit_count) / Decimal(denom)).quantize(
                    Decimal("0.0001")
                )

        ladder, max_consecutive = self._build_limit_ladder(
            tuple(e for e in entries if e.pool_type is LimitPoolType.LIMIT_UP)
        )
        # promotion_rate requires prior-day→today identity match; without a
        # verified joined contract leave None (do not invent).
        context = LimitUpContext(
            trade_date=trade_date,
            entries=tuple(entries),
            limit_up_count=limit_up_count if LimitPoolType.LIMIT_UP in ordered_pools else 0,
            limit_down_count=limit_down_count if LimitPoolType.LIMIT_DOWN in ordered_pools else 0,
            broken_limit_count=broken_limit_count
            if LimitPoolType.BROKEN_LIMIT in ordered_pools
            else 0,
            broken_rate=broken_rate,
            max_consecutive_count=max_consecutive,
            promotion_rate=None,
            ladder=ladder,
        )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=context,
            meta=self._meta(
                category=DataCategory.LIMIT_UP,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
                current_cross_section=True,
            ),
        )

    def _require_limit_pools(self, pools: tuple[LimitPoolType, ...]) -> tuple[LimitPoolType, ...]:
        if not isinstance(pools, tuple):
            raise DataContractError(
                "pools must be a tuple of LimitPoolType",
                details={"field": "pools", "rule": "type"},
            )
        if not pools:
            raise DataContractError(
                "pools must not be empty at the adapter boundary",
                details={"field": "pools", "rule": "non_empty"},
            )
        seen: set[LimitPoolType] = set()
        ordered: list[LimitPoolType] = []
        for pool in pools:
            if not isinstance(pool, LimitPoolType):
                raise DataContractError(
                    "pools elements must be LimitPoolType",
                    details={"field": "pools", "rule": "type"},
                )
            if pool in seen:
                raise DataContractError(
                    "pools must not contain duplicates",
                    details={"field": "pools", "rule": "unique"},
                )
            seen.add(pool)
            ordered.append(pool)
        # Preserve caller order (service already normalizes to enum order).
        return tuple(ordered)

    def _parse_limit_pool_body(
        self,
        body: bytes,
        *,
        pool_type: LimitPoolType,
        trade_date: date,
    ) -> list[LimitPoolEntry]:
        payload = loads_json_decimal(body)
        data = self._require_data_object(payload, operation="limit_pools")
        if data is None:
            return []
        pool = data.get("pool")
        if pool is None:
            pool_list: list[object] = []
        elif not isinstance(pool, list):
            raise DataContractError(
                "Eastmoney limit pool list failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "rule": "contract_drift",
                },
            )
        else:
            pool_list = pool
        # Envelope integrity: qdate must equal requested trade_date (no relabeling).
        # Live probe 2026-07-17: date=20260715 and date=20260716 both returned
        # qdate=20260717 — date param is ignored; mismatch must reject.
        self._require_limit_pool_envelope(data, trade_date=trade_date, pool_len=len(pool_list))
        out: list[LimitPoolEntry] = []
        seen_ids: set[str] = set()
        for idx, raw in enumerate(pool_list):
            if not isinstance(raw, dict):
                raise DataContractError(
                    "Eastmoney limit pool row failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "limit_pools",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            entry = self._limit_pool_entry_from_row(
                raw, pool_type=pool_type, trade_date=trade_date, index=idx
            )
            if entry.instrument_id in seen_ids:
                raise DataContractError(
                    "Eastmoney limit pool returned duplicate instrument",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "limit_pools",
                        "rule": "unique",
                        "index": idx,
                    },
                )
            seen_ids.add(entry.instrument_id)
            out.append(entry)
        return out

    def _require_limit_pool_envelope(
        self,
        data: Mapping[str, Any],
        *,
        trade_date: date,
        pool_len: int,
    ) -> None:
        """Validate data.qdate and data.tc before emitting any pool rows."""
        expected_qdate = int(trade_date.strftime("%Y%m%d"))
        if "qdate" not in data:
            raise DataContractError(
                "Eastmoney limit pool missing data.qdate",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "field": "qdate",
                    "rule": "qdate_required",
                },
            )
        qdate = require_int(data.get("qdate"), field="data.qdate")
        if qdate != expected_qdate:
            raise DataContractError(
                "Eastmoney limit pool qdate does not match requested trade_date; "
                "refusing to relabel current cross-section as historical",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "field": "qdate",
                    "rule": "qdate_trade_date",
                    "requested": trade_date.isoformat(),
                    "observed": str(qdate),
                },
            )
        if "tc" not in data:
            raise DataContractError(
                "Eastmoney limit pool missing data.tc",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "field": "tc",
                    "rule": "tc_required",
                },
            )
        tc = require_int(data.get("tc"), field="data.tc")
        if tc < 0:
            raise DataContractError(
                "Eastmoney limit pool tc must be nonnegative",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "field": "tc",
                    "rule": "nonnegative",
                },
            )
        if tc != pool_len:
            raise DataContractError(
                "Eastmoney limit pool tc does not match returned pool length; "
                "refusing silent truncation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "field": "tc",
                    "rule": "pool_completeness",
                    "declared": tc,
                    "returned": pool_len,
                },
            )

    def _limit_pool_entry_from_row(
        self,
        row: Mapping[str, Any],
        *,
        pool_type: LimitPoolType,
        trade_date: date,
        index: int,
    ) -> LimitPoolEntry:
        code_raw = row.get("c")
        if not isinstance(code_raw, str) or not code_raw.strip().isdigit():
            raise DataContractError(
                "Eastmoney limit pool row missing code",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "rule": "contract_drift",
                    "index": index,
                },
            )
        code6 = code_raw.strip().zfill(6)
        m_raw = row.get("m")
        m_int = require_int(m_raw, field=f"pool[{index}].m") if m_raw is not None else None
        if m_int is None or m_int not in _EM_M_TO_SUFFIX:
            # Fail closed rather than guess SH/SZ from code ranges.
            raise DataContractError(
                "Eastmoney limit pool row missing market m",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "rule": "contract_drift",
                    "index": index,
                },
            )
        suffix = _EM_M_TO_SUFFIX[m_int]
        instrument_id = instrument_id_from_code(code6, suffix, asset=AssetType.EQUITY)
        name_raw = row.get("n")
        if not isinstance(name_raw, str) or not name_raw.strip():
            raise DataContractError(
                "Eastmoney limit pool row missing name",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "rule": "contract_drift",
                    "index": index,
                },
            )
        # Live-observed: p is price * 100 (integer cents).
        price_raw = row.get("p")
        if price_raw is None:
            raise DataContractError(
                "Eastmoney limit pool row missing price p",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "limit_pools",
                    "rule": "contract_drift",
                    "index": index,
                },
            )
        last = require_decimal(price_raw, field=f"pool[{index}].p") / Decimal(100)
        change_percent = require_decimal(row.get("zdp"), field=f"pool[{index}].zdp")
        consecutive = None
        if "lbc" in row and row.get("lbc") is not None:
            consecutive = require_int(row.get("lbc"), field=f"pool[{index}].lbc")
            if consecutive < 0:
                raise DataContractError(
                    "consecutive_limit_count must be nonnegative",
                    details={"field": "lbc", "rule": "nonnegative", "index": index},
                )
        days_and_boards = None
        zttj = row.get("zttj")
        if isinstance(zttj, dict):
            days = zttj.get("days")
            ct = zttj.get("ct")
            if days is not None and ct is not None:
                d_i = require_int(days, field=f"pool[{index}].zttj.days")
                c_i = require_int(ct, field=f"pool[{index}].zttj.ct")
                days_and_boards = f"{d_i}天{c_i}板"
        first_seal = self._pool_time_field(
            row.get("fbt"), trade_date=trade_date, field=f"pool[{index}].fbt"
        )
        last_seal = self._pool_time_field(
            row.get("lbt"), trade_date=trade_date, field=f"pool[{index}].lbt"
        )
        seal_amount = decimal_from_text(row.get("fund"), field=f"pool[{index}].fund")
        broken_count = None
        if "zbc" in row and row.get("zbc") is not None:
            broken_count = require_int(row.get("zbc"), field=f"pool[{index}].zbc")
            if broken_count < 0:
                raise DataContractError(
                    "broken_count must be nonnegative",
                    details={"field": "zbc", "rule": "nonnegative", "index": index},
                )
        industry = None
        hybk = row.get("hybk")
        if isinstance(hybk, str) and hybk.strip():
            industry = hybk.strip()
        return LimitPoolEntry(
            pool_type=pool_type,
            trade_date=trade_date,
            instrument_id=instrument_id,
            name=name_raw.strip(),
            last=last,
            change_percent=change_percent,
            consecutive_limit_count=consecutive,
            days_and_boards=days_and_boards,
            first_seal_at=first_seal,
            last_seal_at=last_seal,
            seal_amount_cny=seal_amount,
            broken_count=broken_count,
            industry=industry,
            reason_tags=(),
            source_vendor=VendorId.EASTMONEY,
            reliability=ReliabilityLevel.MEDIUM,
        )

    def _pool_time_field(self, raw: object, *, trade_date: date, field: str) -> datetime | None:
        if raw is None:
            return None
        # Live-observed: integer HHMMSS without leading zero (92500 → 09:25:00).
        if isinstance(raw, Decimal):
            if raw != raw.to_integral_value():
                raise DataContractError(
                    f"{field} must be an integer time code",
                    details={"field": field, "rule": "time_format"},
                )
            value = int(raw)
        elif isinstance(raw, int) and not isinstance(raw, bool):
            value = raw
        elif isinstance(raw, str) and raw.strip().isdigit():
            value = int(raw.strip())
        else:
            raise DataContractError(
                f"{field} failed contract validation",
                details={"field": field, "rule": "contract_drift"},
            )
        if value < 0:
            raise DataContractError(
                f"{field} must be nonnegative",
                details={"field": field, "rule": "nonnegative"},
            )
        text = f"{value:06d}"
        return combine_shanghai_date_time(trade_date, text)

    def _build_limit_ladder(
        self, limit_up_entries: tuple[LimitPoolEntry, ...]
    ) -> tuple[tuple[LimitUpLadderRung, ...], int | None]:
        by_count: dict[int, list[str]] = {}
        for entry in limit_up_entries:
            if entry.consecutive_limit_count is None:
                continue
            by_count.setdefault(entry.consecutive_limit_count, []).append(entry.instrument_id)
        if not by_count:
            return (), None
        rungs: list[LimitUpLadderRung] = []
        for count in sorted(by_count):
            ids = tuple(sorted(set(by_count[count])))
            rungs.append(
                LimitUpLadderRung(
                    consecutive_limit_count=count,
                    instrument_count=len(ids),
                    instrument_ids=ids,
                )
            )
        return tuple(rungs), max(by_count)

    async def get_sentiment_signals(
        self,
        instrument: Instrument | None,
        *,
        trade_date: date,
        sources: tuple[SentimentSourceType, ...],
        as_of: datetime,
    ) -> ProviderSuccess[tuple[SentimentSignal, ...]]:
        """Eastmoney current stockrank facts.

        Live-verified 2026-07-17: POST emappdata.../stockrank/getAllCurrentList.
        Current-only: ranks are a live cross-section and must not be labeled as
        an arbitrary historical trade_date. ``concept_heat`` is separately
        fetched for one required instrument from getHotStockRankList and returns
        concept hit counts, not a global concept ranking.
        """
        self._require_configured()
        trade_date = require_exact_date(trade_date, field="trade_date")
        # Sample adapter clock once; reject stale/historical before network.
        now = self._require_current_only_as_of_and_trade_date(
            as_of, trade_date, operation="sentiment"
        )
        ordered = self._require_sentiment_sources(sources)
        if instrument is not None:
            require_a_share_instrument(instrument)

        signals: list[SentimentSignal] = []
        for source in ordered:
            if source is SentimentSourceType.EASTMONEY_HOT:
                signals.extend(
                    await self._fetch_eastmoney_hot(instrument=instrument, trade_date=trade_date)
                )
            elif source is SentimentSourceType.CONCEPT_HEAT:
                if instrument is None or instrument.asset_type is AssetType.OPTION:
                    raise DataContractError(
                        "concept heat requires non-option instrument",
                        details={"field": "instrument", "rule": "asset_support"},
                    )
                signals.extend(
                    await self._fetch_concept_heat(
                        instrument=instrument, trade_date=trade_date, sampled_now=now, as_of=as_of
                    )
                )
            else:
                # ths_hot / interactive_qa / news are other vendors/categories.
                raise DataContractError(
                    "Eastmoney does not implement this sentiment source",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "sentiment",
                        "source": source.value,
                        "rule": "unsupported",
                    },
                )

        # Deterministic order: rank asc then instrument_id.
        signals.sort(
            key=lambda s: (
                s.rank if s.rank is not None else 10**9,
                s.instrument_id or "",
            )
        )
        fetched_at = now
        require_aware_datetime(fetched_at, field_name="fetched_at")
        session = infer_session_basic(Market.A_SHARE, as_of, timezone="Asia/Shanghai")
        return ProviderSuccess(
            value=tuple(signals),
            meta=self._meta(
                category=DataCategory.SENTIMENT,
                as_of=as_of,
                fetched_at=fetched_at,
                session=session,
                current_cross_section=True,
                warnings=("LOW_RELIABILITY_MARKET_SIGNAL",),
            ),
        )

    async def _fetch_concept_heat(
        self, *, instrument: Instrument, trade_date: date, sampled_now: datetime, as_of: datetime
    ) -> list[SentimentSignal]:
        code6, suffix = require_a_share_instrument(instrument)
        source_code = f"{suffix}{code6}"
        body = ('{"srcSecurityCode":"' + source_code + '"}').encode("utf-8")
        response = await self._gated_send(
            HttpRequest(
                method="POST",
                url=_STOCKRANK_CONCEPT_HEAT_URL,
                params={},
                headers={**self._headers(), "Content-Type": "application/json"},
                body=body,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="sentiment")
        self._require_json_content_type(response.headers, operation="sentiment")
        payload = loads_json_decimal(response.body)
        if (
            not isinstance(payload, dict)
            or set(payload)
            != {"globalId", "message", "status", "code", "data", "stack"}
            or payload.get("globalId") is not None
            or type(payload.get("status")) is not int
            or type(payload.get("code")) is not int
            or payload.get("stack") is not None
        ):
            raise DataContractError("concept heat envelope mismatch", details={"rule": "envelope"})
        if payload["status"] != 0 or payload["code"] != 0:
            raise ProviderUnavailableError(
                "Eastmoney concept heat business status failure",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "sentiment",
                    "error_type": "business_status",
                },
            )
        if payload.get("message") != "OK":
            raise DataContractError("concept heat envelope mismatch", details={"rule": "envelope"})
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise DataContractError(
                "concept heat data must be list", details={"field": "data", "rule": "type"}
            )
        parsed: list[tuple[str, str, int, datetime]] = []
        names: set[str] = set()
        ids: set[str] = set()
        previous: int | None = None
        calc_time: datetime | None = None
        import re

        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise DataContractError(
                    "concept heat row must object", details={"index": index, "rule": "type"}
                )
            name, cid, security, time_raw, hit, flag = (
                row.get("conceptName"),
                row.get("conceptId"),
                row.get("srcSecurityCode"),
                row.get("calcTime"),
                row.get("hitCount"),
                row.get("flag"),
            )
            if (
                not isinstance(name, str)
                or not name.strip()
                or not isinstance(cid, str)
                or re.fullmatch(r"BK[0-9]+", cid) is None
                or security != source_code
                or type(hit) is not int
                or hit < 0
                or type(flag) is not int
                or flag != 0
                or not isinstance(time_raw, str)
            ):
                raise DataContractError(
                    "concept heat row mismatch", details={"index": index, "rule": "row"}
                )
            try:
                observed = datetime.strptime(time_raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SHANGHAI)
            except ValueError as exc:
                raise DataContractError(
                    "concept heat calcTime invalid", details={"index": index, "rule": "calc_time"}
                ) from exc
            if (
                observed.date() != trade_date
                or observed > as_of
                or observed > sampled_now
                or (sampled_now - observed).total_seconds() > self._max_delayed_seconds
                or (cid in ids)
                or (name in names)
                or (previous is not None and hit > previous)
            ):
                raise DataContractError(
                    "concept heat row invariant mismatch",
                    details={"index": index, "rule": "freshness_identity_order"},
                )
            if calc_time is not None and observed != calc_time:
                raise DataContractError(
                    "concept heat calcTime must be shared",
                    details={"index": index, "rule": "shared_time"},
                )
            calc_time = observed
            previous = hit
            ids.add(cid)
            names.add(name)
            parsed.append((cid, name, hit, observed))
        parsed.sort(key=lambda row: (-row[2], row[0]))
        return [
            SentimentSignal(
                source_type=SentimentSourceType.CONCEPT_HEAT,
                trade_date=trade_date,
                instrument_id=None,
                rank=index + 1,
                rank_change=None,
                heat_value=Decimal(hit),
                concept_tags=(name,),
                label=name,
                source_vendor=VendorId.EASTMONEY,
                reliability=ReliabilityLevel.LOW,
                is_authoritative=False,
                source_item_id=cid,
                observed_at=observed,
            )
            for index, (cid, name, hit, observed) in enumerate(parsed)
        ]

    def _require_sentiment_sources(
        self, sources: tuple[SentimentSourceType, ...]
    ) -> tuple[SentimentSourceType, ...]:
        if not isinstance(sources, tuple):
            raise DataContractError(
                "sources must be a tuple of SentimentSourceType",
                details={"field": "sources", "rule": "type"},
            )
        if not sources:
            raise DataContractError(
                "sources must not be empty at the adapter boundary",
                details={"field": "sources", "rule": "non_empty"},
            )
        seen: set[SentimentSourceType] = set()
        out: list[SentimentSourceType] = []
        for source in sources:
            if not isinstance(source, SentimentSourceType):
                raise DataContractError(
                    "sources elements must be SentimentSourceType",
                    details={"field": "sources", "rule": "type"},
                )
            if source in seen:
                raise DataContractError(
                    "sources must not contain duplicates",
                    details={"field": "sources", "rule": "unique"},
                )
            seen.add(source)
            out.append(source)
        return tuple(out)

    async def _fetch_eastmoney_hot(
        self,
        *,
        instrument: Instrument | None,
        trade_date: date,
    ) -> list[SentimentSignal]:
        # Empty JSON object is live-verified success body (2026-07-17).
        response = await self._gated_send(
            HttpRequest(
                method="POST",
                url=_STOCKRANK_ALL_CURRENT_URL,
                params={},
                headers={
                    **self._headers(),
                    "Content-Type": "application/json",
                },
                body=b"{}",
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation="sentiment")
        self._require_json_content_type(response.headers, operation="sentiment")
        payload = loads_json_decimal(response.body)
        if not isinstance(payload, dict):
            raise DataContractError(
                "Eastmoney stockrank payload failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "sentiment",
                    "rule": "contract_drift",
                },
            )
        # Live envelope: status/code 0 + data list of {sc, rk, rc, hisRc}.
        status = payload.get("status")
        code = payload.get("code")
        if status not in (0, "0", Decimal(0)) or code not in (0, "0", Decimal(0)):
            raise ProviderUnavailableError(
                "Eastmoney stockrank business status failure",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "sentiment",
                    "error_type": "business_status",
                    "status_class": "none",
                },
            )
        data = payload.get("data")
        if data is None:
            return []
        if not isinstance(data, list):
            raise DataContractError(
                "Eastmoney stockrank data failed contract validation",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "sentiment",
                    "rule": "contract_drift",
                },
            )
        wanted: str | None = None
        if instrument is not None:
            wanted = instrument.instrument_id
        signals: list[SentimentSignal] = []
        seen_ids: set[str] = set()
        for idx, row in enumerate(data):
            if not isinstance(row, dict):
                raise DataContractError(
                    "Eastmoney stockrank row failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "sentiment",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            sc = row.get("sc")
            if not isinstance(sc, str) or len(sc) < 8:
                raise DataContractError(
                    "Eastmoney stockrank row missing sc",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "sentiment",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            sc_u = sc.strip().upper()
            # Live shape: SH600664 / SZ002185
            if sc_u.startswith("SH") and sc_u[2:].isdigit():
                instrument_id = instrument_id_from_code(
                    sc_u[2:].zfill(6), "SH", asset=AssetType.EQUITY
                )
            elif sc_u.startswith("SZ") and sc_u[2:].isdigit():
                instrument_id = instrument_id_from_code(
                    sc_u[2:].zfill(6), "SZ", asset=AssetType.EQUITY
                )
            elif sc_u.startswith("BJ") and sc_u[2:].isdigit():
                instrument_id = instrument_id_from_code(
                    sc_u[2:].zfill(6), "BJ", asset=AssetType.EQUITY
                )
            else:
                raise DataContractError(
                    "Eastmoney stockrank sc failed contract validation",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "sentiment",
                        "rule": "contract_drift",
                        "index": idx,
                    },
                )
            if instrument_id in seen_ids:
                raise DataContractError(
                    "Eastmoney stockrank returned duplicate instrument",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "sentiment",
                        "rule": "unique",
                        "index": idx,
                    },
                )
            seen_ids.add(instrument_id)
            if wanted is not None and instrument_id != wanted:
                continue
            rank = require_int(row.get("rk"), field=f"stockrank[{idx}].rk")
            if rank < 0:
                raise DataContractError(
                    "rank must be nonnegative",
                    details={"field": "rk", "rule": "nonnegative", "index": idx},
                )
            rank_change = None
            if "rc" in row and row.get("rc") is not None:
                rank_change = require_int(row.get("rc"), field=f"stockrank[{idx}].rc")
            signals.append(
                SentimentSignal(
                    source_type=SentimentSourceType.EASTMONEY_HOT,
                    trade_date=trade_date,
                    instrument_id=instrument_id,
                    rank=rank,
                    rank_change=rank_change,
                    heat_value=None,
                    concept_tags=(),
                    label=None,
                    source_vendor=VendorId.EASTMONEY,
                    reliability=ReliabilityLevel.LOW,
                    is_authoritative=False,
                )
            )
        return signals
