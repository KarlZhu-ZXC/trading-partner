"""Official National Animal Husbandry Service hog-cycle adapter.

The upstream exposes publication HTML rather than a versioned API. Parsing is
strict and fail-closed: only named national table rows and explicit capacity
sentences are accepted. No cycle phase, profit, or company-level fact is inferred.
"""

from __future__ import annotations

import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.ports.http_transport import HttpRequest, HttpTransport
from domain.a_share.enums import (
    IndustryCycleType,
    IndustryMeasurementBasis,
    IndustryMetricFrequency,
)
from domain.a_share.models import (
    IndustryCycleSnapshot,
    IndustryMetricObservation,
)
from domain.common.enums import (
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import (
    DataContractError,
    NoMarketData,
    ProviderNotConfigured,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from domain.common.time import require_aware_datetime

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_PRICE_LIST = "https://www.nahs.org.cn/jcyj/scxs/"
_PRICE_ARCHIVE_LIST = "https://www.nahs.org.cn/jcyj/jghq/"
_CAPACITY_LIST = "https://www.nahs.org.cn/jcyj/jcgz/"
_PRICE_TITLE = re.compile(
    r"(?P<year>20\d{2})年(?P<month>\d{1,2})月(?:份)?全国畜产品和饲料价格情况"
)
_NUMBER = r"([0-9]+(?:\.[0-9]+)?)"
_PRICE_ROWS = {
    "仔猪": "piglet_cny_per_kg",
    "生猪": "live_hog_cny_per_kg",
    "猪肉": "pork_cny_per_kg",
    "玉米": "corn_cny_per_kg",
    "豆粕": "soybean_meal_cny_per_kg",
    "育肥猪配合饲料": "fattening_feed_cny_per_kg",
}
_CAPACITY_TITLE_TERMS = ("一季度畜牧生产", "上半年畜牧生产", "前三季度畜牧生产", "全国畜牧业生产")


@dataclass(frozen=True, slots=True)
class _PricePage:
    period: date
    published_at: datetime
    values: dict[str, Decimal]
    source_url: str


@dataclass(frozen=True, slots=True)
class _CapacityPage:
    period_start: date
    period_end: date
    published_at: datetime
    values: dict[str, Decimal]
    source_url: str


class _DocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.tables: list[list[list[str]]] = []
        self.publishdate: str | None = None
        self._href: str | None = None
        self._anchor_text: list[str] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        meta_name = attr.get("name") or ""
        if tag == "meta" and meta_name.lower() == "publishdate":
            self.publishdate = attr.get("content")
        elif tag == "a":
            self._href = attr.get("href")
            self._anchor_text = []
        elif tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)
        if self._href is not None:
            self._anchor_text.append(data)
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((self._href, _clean_text("".join(self._anchor_text))))
            self._href = None
        elif tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(_clean_text("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    @property
    def text(self) -> str:
        return _clean_text(" ".join(self.text_parts))


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def _decimal(value: str) -> Decimal:
    compact = re.sub(r"\s+", "", value).replace(",", "")
    try:
        result = Decimal(compact)
    except InvalidOperation:
        raise DataContractError("NAHS numeric cell changed format") from None
    if not result.is_finite() or result < 0:
        raise DataContractError("NAHS numeric cell must be finite and nonnegative")
    return result


def _publication_datetime(parser: _DocumentParser) -> datetime:
    if parser.publishdate is None:
        raise DataContractError("NAHS article missing publishdate metadata")
    try:
        published = date.fromisoformat(parser.publishdate)
    except ValueError:
        raise DataContractError("NAHS publishdate changed format") from None
    return datetime(published.year, published.month, published.day, tzinfo=_SHANGHAI)


def _page_number_url(base: str, page: int) -> str:
    return base if page == 0 else urljoin(base, f"index_{page}.htm")


def _is_safe_article_url(url: str, *, family: str) -> bool:
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and parts.hostname == "www.nahs.org.cn"
        and parts.port is None
        and parts.path.startswith(f"/jcyj/{family}/")
        and parts.path.endswith(".htm")
        and not parts.query
        and not parts.fragment
    )


class NahsHogCycleAdapter:
    """Read deterministic national hog-cycle inputs from official publications."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        clock: Clock,
        enabled: bool = True,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._transport = transport
        self._clock = clock
        self._enabled = bool(enabled)
        self._timeout_seconds = float(timeout_seconds)

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.NAHS

    @property
    def provider_name(self) -> str:
        return self.vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.A_SHARE and category is DataCategory.INDUSTRY_CYCLE

    def is_configured(self) -> bool:
        return self._enabled

    async def _get(self, url: str) -> _DocumentParser:
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=url,
                params={},
                headers={"User-Agent": "TradingPartner/1.0"},
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        if response.status_code == 429:
            raise ProviderRateLimitError("NAHS rate limited", details={"vendor": "nahs"})
        if response.status_code < 200 or response.status_code >= 300:
            raise ProviderUnavailableError(
                "NAHS HTTP failure",
                details={"vendor": "nahs", "status_class": f"{response.status_code // 100}xx"},
            )
        try:
            html = response.body.decode("utf-8")
        except UnicodeDecodeError:
            raise DataContractError("NAHS response is not UTF-8 HTML") from None
        parser = _DocumentParser()
        parser.feed(html)
        return parser

    async def _discover(
        self,
        base: str,
        *,
        family: str,
        title_match: re.Pattern[str] | None = None,
        title_terms: tuple[str, ...] = (),
        max_pages: int = 5,
        minimum_results: int | None = None,
        published_on_or_before: date | None = None,
    ) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        seen: set[str] = set()
        for page in range(max_pages):
            parser = await self._get(_page_number_url(base, page))
            for href, title in parser.links:
                if not title or not (
                    (title_match is not None and title_match.search(title))
                    or (title_terms and any(term in title for term in title_terms))
                ):
                    continue
                url = urljoin(base, href)
                path_date = re.search(r"/t(20\d{6})_", urlsplit(url).path)
                if published_on_or_before is not None and path_date is not None:
                    candidate_date = datetime.strptime(path_date.group(1), "%Y%m%d").date()
                    if candidate_date > published_on_or_before:
                        continue
                if url not in seen and _is_safe_article_url(url, family=family):
                    seen.add(url)
                    found.append((url, title))
            if minimum_results is not None and len(found) >= minimum_results:
                break
        return found

    async def _price_observation(
        self, url: str, title: str, *, as_of: datetime
    ) -> _PricePage | None:
        match = _PRICE_TITLE.search(title)
        if match is None:
            return None
        period = date(int(match.group("year")), int(match.group("month")), 1)
        parser = await self._get(url)
        published_at = _publication_datetime(parser)
        if published_at > as_of:
            return None
        values: dict[str, Decimal] = {}
        for table in parser.tables:
            for row in table:
                if len(row) < 2:
                    continue
                key = _PRICE_ROWS.get(_clean_text(row[0]))
                if key is not None:
                    values[key] = _decimal(row[1])
        compact_text = re.sub(r"\s+", "", parser.text)
        prose_patterns = {
            "piglet_cny_per_kg": r"全国仔猪平均价格" + _NUMBER + r"元/公斤",
            "live_hog_cny_per_kg": r"全国(?:生猪|活猪)平均价格" + _NUMBER + r"元/公斤",
            "pork_cny_per_kg": r"全国猪肉平均价格" + _NUMBER + r"元/公斤",
            "corn_cny_per_kg": r"全国玉米平均价格" + _NUMBER + r"元/公斤",
            "soybean_meal_cny_per_kg": r"全国豆粕平均价格" + _NUMBER + r"元/公斤",
            "fattening_feed_cny_per_kg": (
                r"全国育肥猪配合饲料(?:平均)?价格(?:为)?" + _NUMBER + r"元/公斤"
            ),
        }
        for key, pattern in prose_patterns.items():
            if key not in values and (prose_match := re.search(pattern, compact_text)):
                values[key] = _decimal(prose_match.group(1))
        required = {
            "piglet_cny_per_kg",
            "live_hog_cny_per_kg",
            "pork_cny_per_kg",
            "corn_cny_per_kg",
        }
        if not required.issubset(values):
            raise DataContractError("NAHS national price table missing required hog rows")
        ratio_match = re.search(r"猪粮比价为([0-9]+(?:\.[0-9]+)?)[:：]1", compact_text)
        if ratio_match:
            values["pig_grain_ratio"] = _decimal(ratio_match.group(1))
        return _PricePage(
            period=period,
            published_at=published_at,
            values=values,
            source_url=url,
        )

    async def _capacity_observation(
        self, url: str, title: str, *, as_of: datetime
    ) -> _CapacityPage | None:
        parser = await self._get(url)
        published_at = _publication_datetime(parser)
        if published_at > as_of:
            return None
        year_match = re.search(r"(20\d{2})年", title)
        year = published_at.year if year_match is None else int(year_match.group(1))
        if "一季度" in title:
            period_end = date(year, 3, 31)
        elif "上半年" in title:
            period_end = date(year, 6, 30)
        elif "前三季度" in title:
            period_end = date(year, 9, 30)
        else:
            period_end = date(year - 1 if published_at.month <= 2 else year, 12, 31)
        text = re.sub(r"\s+", "", parser.text)

        def find(pattern: str) -> Decimal | None:
            match = re.search(pattern, text)
            return _decimal(match.group(1)) if match else None

        values = {
            key: value
            for key, value in {
                "breeding_sow_inventory_10k_head": find(r"能繁母猪存栏" + _NUMBER + r"万头"),
                "pig_inventory_10k_head": find(r"全国生猪存栏" + _NUMBER + r"万头"),
                "pig_slaughter_ytd_10k_head": find(r"全国生猪出栏" + _NUMBER + r"万头"),
                "pork_output_ytd_10k_tonnes": find(r"猪肉产量" + _NUMBER + r"万吨"),
                "normal_breeding_sow_inventory_10k_head": find(r"正常保有量" + _NUMBER + r"万头"),
                "breeding_sow_percent_of_normal": find(r"正常保有量[0-9.]+万头的" + _NUMBER + r"%"),
            }.items()
            if value is not None
        }
        if "breeding_sow_inventory_10k_head" not in values:
            raise DataContractError("NAHS capacity article missing breeding sow inventory")
        return _CapacityPage(
            period_start=date(period_end.year, 1, 1),
            period_end=period_end,
            published_at=published_at,
            values=values,
            source_url=url,
        )

    async def get_hog_cycle(
        self, *, lookback_months: int, as_of: datetime
    ) -> ProviderSuccess[IndustryCycleSnapshot]:
        if not self._enabled:
            raise ProviderNotConfigured("NAHS hog-cycle adapter is disabled")
        require_aware_datetime(as_of, field_name="as_of")
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if as_of > now:
            raise DataContractError("as_of must not be in the future")
        if type(lookback_months) is not int or not 3 <= lookback_months <= 240:
            raise DataContractError("lookback_months must be an int in 3..240")

        candidates = await self._discover(
            _PRICE_LIST,
            family="scxs",
            title_match=_PRICE_TITLE,
            max_pages=16,
            minimum_results=lookback_months,
            published_on_or_before=as_of.astimezone(_SHANGHAI).date(),
        )
        if len(candidates) < lookback_months:
            archive_candidates = await self._discover(
                _PRICE_ARCHIVE_LIST,
                family="jghq",
                title_match=_PRICE_TITLE,
                max_pages=27,
                published_on_or_before=as_of.astimezone(_SHANGHAI).date(),
            )
            candidates.extend(
                item for item in archive_candidates if item[0] not in {url for url, _ in candidates}
            )
        prices: list[_PricePage] = []
        parse_failures = 0
        for url, title in candidates:
            try:
                observation = await self._price_observation(url, title, as_of=as_of)
            except DataContractError:
                parse_failures += 1
                continue
            if observation is not None and observation.period <= as_of.astimezone(_SHANGHAI).date():
                prices.append(observation)
            if len(prices) >= lookback_months:
                break
        if not prices:
            raise NoMarketData("No NAHS hog price publications were visible at as_of")
        prices = sorted(prices, key=lambda item: item.period)[-lookback_months:]

        capacity: _CapacityPage | None = None
        capacity_candidates = await self._discover(
            _CAPACITY_LIST,
            family="jcgz",
            title_terms=_CAPACITY_TITLE_TERMS,
            max_pages=3,
            minimum_results=1,
            published_on_or_before=as_of.astimezone(_SHANGHAI).date(),
        )
        for url, title in capacity_candidates:
            candidate = await self._capacity_observation(url, title, as_of=as_of)
            if candidate is not None:
                capacity = candidate
                break

        fetched_at = self._clock.now()
        meta = ProviderResultMeta(
            vendor=VendorId.NAHS,
            category=DataCategory.INDUSTRY_CYCLE,
            role=SourceRole.PRIMARY,
            as_of=as_of,
            fetched_at=fetched_at,
            freshness=Freshness.DELAYED,
            session=TradingSession.UNKNOWN,
            latency_ms=None,
            cache_disposition=CacheDisposition.MISS,
            adjustment=None,
            data_delay_seconds=None,
            warnings=(
                "HOG_CYCLE_MONTHLY_PRICE_FREQUENCY",
                "HOG_CYCLE_CAPACITY_PERIODIC",
                "HOG_CYCLE_NOT_COMPANY_COST",
                "HOG_CYCLE_FUTURES_CURVE_UNAVAILABLE",
                *(
                    ("HOG_CYCLE_HISTORY_PARTIAL",)
                    if len(prices) < lookback_months or parse_failures
                    else ()
                ),
            ),
        )
        observations: list[IndustryMetricObservation] = []
        price_units = {
            "piglet_cny_per_kg": "CNY/kg",
            "live_hog_cny_per_kg": "CNY/kg",
            "pork_cny_per_kg": "CNY/kg",
            "corn_cny_per_kg": "CNY/kg",
            "soybean_meal_cny_per_kg": "CNY/kg",
            "fattening_feed_cny_per_kg": "CNY/kg",
            "pig_grain_ratio": "ratio",
        }
        for page in prices:
            period_end = date(
                page.period.year,
                page.period.month,
                monthrange(page.period.year, page.period.month)[1],
            )
            observations.extend(
                IndustryMetricObservation(
                    metric_code=metric_code,
                    value=value,
                    unit=price_units[metric_code],
                    period_start=page.period,
                    period_end=period_end,
                    frequency=IndustryMetricFrequency.MONTHLY,
                    published_at=page.published_at,
                    source_url=page.source_url,
                )
                for metric_code, value in page.values.items()
            )
        if capacity is not None:
            capacity_units = {
                "breeding_sow_inventory_10k_head": "10k_head",
                "pig_inventory_10k_head": "10k_head",
                "pig_slaughter_ytd_10k_head": "10k_head_ytd",
                "pork_output_ytd_10k_tonnes": "10k_tonnes_ytd",
                "normal_breeding_sow_inventory_10k_head": "10k_head",
                "breeding_sow_percent_of_normal": "percent",
            }
            period_frequency = (
                IndustryMetricFrequency.QUARTERLY
                if capacity.period_end.month in {3, 9}
                else IndustryMetricFrequency.HALF_YEAR
                if capacity.period_end.month == 6
                else IndustryMetricFrequency.ANNUAL
            )
            observations.extend(
                IndustryMetricObservation(
                    metric_code=metric_code,
                    value=value,
                    unit=capacity_units[metric_code],
                    period_start=capacity.period_start,
                    period_end=capacity.period_end,
                    frequency=period_frequency,
                    published_at=capacity.published_at,
                    source_url=capacity.source_url,
                    measurement_basis=(
                        IndustryMeasurementBasis.YTD_TOTAL
                        if metric_code
                        in {"pig_slaughter_ytd_10k_head", "pork_output_ytd_10k_tonnes"}
                        else IndustryMeasurementBasis.POLICY_BASELINE
                        if metric_code == "normal_breeding_sow_inventory_10k_head"
                        else IndustryMeasurementBasis.PERIOD_END
                    ),
                )
                for metric_code, value in capacity.values.items()
            )
        observations.sort(key=lambda item: (item.period_end, item.metric_code))
        missing_components = ["company_operating_data", "live_hog_futures_curve"]
        if capacity is None:
            missing_components.append("capacity")
        if len(prices) < lookback_months or parse_failures:
            missing_components.append("requested_history_not_fully_available")
        return ProviderSuccess(
            value=IndustryCycleSnapshot(
                cycle=IndustryCycleType.HOG,
                dataset_code="nahs_national_hog_cycle",
                as_of=as_of,
                observations=tuple(observations),
                missing_components=tuple(missing_components),
            ),
            meta=meta,
        )
