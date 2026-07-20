"""SEC EDGAR filings + insider adapter (Phase 1G G2a).

Direct HttpTransport only. Fixed endpoints:

- ``https://www.sec.gov/files/company_tickers.json`` (exact GET)
- ``https://data.sec.gov/submissions/`` (prefix GET)
- ``https://www.sec.gov/Archives/edgar/data/`` (prefix GET)

Implements CategoryProvider + USFilingsProvider + USInsiderActivityProvider.
Does **not** implement companyfacts / fundamentals / statements (later slices).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Final
from xml.etree import ElementTree as ET

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.ports.http_transport import HttpRequest, HttpResponse, HttpTransport
from domain.common.enums import (
    AssetType,
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
from domain.instruments.models import Instrument
from domain.market.session import infer_session_basic
from domain.us_research.enums import USFilingForm, USInsiderAcquiredDisposed
from domain.us_research.models import USFiling, USFilingSection, USInsiderTransaction
from infrastructure.providers.us.sec_common import (
    ARCHIVES_PREFIX as _ARCHIVES_PREFIX,
)
from infrastructure.providers.us.sec_common import (
    JSON_CONTENT_TYPES as _JSON_CONTENT,
)
from infrastructure.providers.us.sec_common import (
    SUBMISSIONS_PREFIX as _SUBMISSIONS_PREFIX,
)
from infrastructure.providers.us.sec_common import (
    content_type_ok as _content_type_ok,
)
from infrastructure.providers.us.sec_common import (
    filed_visibility_utc,
    raise_for_sec_http_status,
    sec_contract,
)
from infrastructure.providers.us.sec_common import (
    loads_sec_json_strict as _loads_json_strict,
)
from infrastructure.providers.us.sec_identity import SECIdentityResolver
from infrastructure.system.clock import SystemClock

_HTML_CONTENT: Final[tuple[str, ...]] = (
    "text/html",
    "application/xhtml",
    "text/plain",
    "*/*",
)
_XML_CONTENT: Final[tuple[str, ...]] = (
    "application/xml",
    "text/xml",
    "application/xhtml",
    "text/plain",
    "*/*",
)

_SUPPORTED_CATEGORIES: Final[frozenset[DataCategory]] = frozenset(
    {DataCategory.FILINGS, DataCategory.INSIDER_ACTIVITY}
)

_FORM_BASE: Final[Mapping[str, USFilingForm]] = {
    "10-K": USFilingForm.FORM_10K,
    "10-Q": USFilingForm.FORM_10Q,
    "8-K": USFilingForm.FORM_8K,
    "DEF 14A": USFilingForm.DEF_14A,
    "4": USFilingForm.FORM_4,
    "S-1": USFilingForm.S_1,
    "SC 13D": USFilingForm.SC_13D,
    "SC 13G": USFilingForm.SC_13G,
}

_SECTION_FORMS: Final[frozenset[USFilingForm]] = frozenset(
    {USFilingForm.FORM_10K, USFilingForm.FORM_10Q, USFilingForm.FORM_8K}
)
_SECTION_ALGORITHM: Final[str] = "sec_sections_v1"
_SECTION_TEXT_MAX: Final[int] = 4_000
_SECTION_COUNT_MAX: Final[int] = 20
_DESC_DOMAIN_MAX: Final[int] = 8_000

_SAFE_DOC_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._\-]+$")
_ACCESSION_RE: Final[re.Pattern[str]] = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_ITEM_HEADING_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)^\s*item\s+(\d+[a-z]?|\d+\.\d+)\s*[.\-:]?\s*(.*)$"
)
_WS_RE: Final[re.Pattern[str]] = re.compile(r"[ \t\f\v]+")
_BLANK_LINES_RE: Final[re.Pattern[str]] = re.compile(r"\n{3,}")
_RULE_10B5_RE: Final[re.Pattern[str]] = re.compile(r"10b5-1", re.IGNORECASE)

# Required aligned-array columns (must be present lists). Optional columns may be
# absent (padded) or contain null/blank values.
_REQUIRED_LIST_KEYS: Final[tuple[str, ...]] = (
    "accessionNumber",
    "filingDate",
    "form",
    "primaryDocument",
)
_OPTIONAL_LIST_KEYS: Final[tuple[str, ...]] = (
    "reportDate",
    "acceptanceDateTime",
    "items",
)
# SEC historical shard filenames: CIK##########-submissions-###.json only.
_SHARD_NAME_RE: Final[re.Pattern[str]] = re.compile(
    r"^CIK(\d{10})-submissions-(\d{3})\.json$"
)
_MAX_SHARD_DESCRIPTORS: Final[int] = 20
# Bounded Form 4 over-fetch so multi-tx filings + transaction_date filters can fill limit.
_MAX_FORM4_OVERFETCH: Final[int] = 100

_WARN_SECTIONS: Final[str] = "SECTIONS_UNAVAILABLE"
_WARN_FORM4: Final[str] = "FORM4_DOCUMENT_DEGRADED"


def _contract(
    message: str,
    *,
    operation: str,
    rule: str,
    **extra: object,
) -> DataContractError:
    return sec_contract(message, operation=operation, rule=rule, **extra)


def _normalize_form(raw: str) -> tuple[USFilingForm, bool] | None:
    text = raw.strip().upper()
    if not text:
        return None
    is_amendment = text.endswith("/A")
    base = text[:-2] if is_amendment else text
    # DEF 14A keeps internal space; SEC uses "DEF 14A".
    if base == "DEF 14A" or base == "DEF14A":
        return USFilingForm.DEF_14A, is_amendment
    if base in {"SC13D", "SC 13D"}:
        return USFilingForm.SC_13D, is_amendment
    if base in {"SC13G", "SC 13G"}:
        return USFilingForm.SC_13G, is_amendment
    mapped = _FORM_BASE.get(base)
    if mapped is None:
        # Try original casing map keys upper-normalized.
        for key, form in _FORM_BASE.items():
            if key.upper() == base:
                return form, is_amendment
        return None
    return mapped, is_amendment


def _parse_iso_date(value: object, *, field: str, operation: str) -> date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise _contract(
            f"{field} must be an ISO date string",
            operation=operation,
            rule="date_type",
        )
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        raise _contract(
            f"{field} is not a valid ISO date",
            operation=operation,
            rule="date_parse",
        ) from None


def _parse_acceptance(value: object, *, operation: str) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise _contract(
            "acceptanceDateTime must be a string",
            operation=operation,
            rule="datetime_type",
        )
    text = value.strip()
    if not text:
        return None
    # SEC uses "YYYY-MM-DDTHH:MM:SS.sss" without zone; treat as UTC.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise _contract(
            "acceptanceDateTime is not a valid datetime",
            operation=operation,
            rule="datetime_parse",
        ) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _visible_at(filed_date: date, accepted_at: datetime | None) -> datetime:
    if accepted_at is not None:
        return accepted_at
    # Conservative: first visible at next UTC midnight after filed_date.
    return filed_visibility_utc(filed_date)


def _safe_archives_url(cik10: str, accession: str, primary_document: str) -> str | None:
    if not _ACCESSION_RE.fullmatch(accession):
        return None
    if not _SAFE_DOC_RE.fullmatch(primary_document):
        return None
    if ".." in primary_document or "/" in primary_document or "\\" in primary_document:
        return None
    try:
        cik_num = str(int(cik10))
    except (TypeError, ValueError):
        return None
    if not cik_num.isdigit():
        return None
    acc_nodash = accession.replace("-", "")
    if not acc_nodash.isdigit() or len(acc_nodash) != 18:
        return None
    return f"{_ARCHIVES_PREFIX}{cik_num}/{acc_nodash}/{primary_document}"


def _parse_items(raw: object) -> tuple[str, ...]:
    if raw is None or raw == "":
        return ()
    if not isinstance(raw, str):
        return ()
    parts = [p.strip() for p in raw.split(",")]
    return tuple(p for p in parts if p)


class _SafeHtmlTextExtractor(HTMLParser):
    """Collect visible text only; never follow links or execute markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style", "noscript"} and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data:
            self._chunks.append(data)

    def text(self) -> str:
        joined = "".join(self._chunks)
        joined = _WS_RE.sub(" ", joined)
        joined = joined.replace("\r\n", "\n").replace("\r", "\n")
        return _BLANK_LINES_RE.sub("\n\n", joined).strip()


def _html_to_text(body: bytes) -> str:
    try:
        raw = body.decode("utf-8", errors="replace")
    except Exception:
        return ""
    parser = _SafeHtmlTextExtractor()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        # Malformed HTML: best-effort already buffered text.
        pass
    return parser.text()


def _extract_sections(
    *,
    form: USFilingForm,
    document_url: str | None,
    body: bytes,
) -> tuple[USFilingSection, ...]:
    text = _html_to_text(body)
    if not text:
        return ()
    lines = text.split("\n")
    # Locate item headings with line offsets.
    heads: list[tuple[int, str, str]] = []
    for idx, line in enumerate(lines):
        match = _ITEM_HEADING_RE.match(line.strip())
        if match is None:
            continue
        item_id = match.group(1).strip()
        rest = (match.group(2) or "").strip()
        name = f"Item {item_id}" + (f" {rest}" if rest else "")
        name = name[:128]
        heads.append((idx, name, item_id))
    if not heads:
        # Single body section when no item markers (still deterministic).
        snippet = text[: min(_SECTION_TEXT_MAX, _DESC_DOMAIN_MAX)]
        return (
            USFilingSection(
                section_name=f"{form.value} body",
                document_url=document_url,
                text=snippet or None,
                algorithm_version=_SECTION_ALGORITHM,
            ),
        )
    out: list[USFilingSection] = []
    for i, (start, name, _item_id) in enumerate(heads[:_SECTION_COUNT_MAX]):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(lines)
        body_text = "\n".join(lines[start:end]).strip()
        body_text = body_text[: min(_SECTION_TEXT_MAX, _DESC_DOMAIN_MAX)]
        out.append(
            USFilingSection(
                section_name=name,
                document_url=document_url,
                text=body_text or None,
                algorithm_version=_SECTION_ALGORITHM,
            )
        )
    return tuple(out)


def _local_tag(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _xml_find_text(node: ET.Element, *path: str) -> str | None:
    current: ET.Element | None = node
    for part in path:
        if current is None:
            return None
        nxt: ET.Element | None = None
        for child in current:
            if _local_tag(child.tag) == part:
                nxt = child
                break
        current = nxt
    if current is None:
        return None
    # Prefer nested <value>.
    for child in current:
        if _local_tag(child.tag) == "value" and child.text:
            text = child.text.strip()
            return text or None
    if current.text and current.text.strip():
        return current.text.strip()
    return None


def _xml_boolish(raw: str | None) -> bool | None:
    if raw is None:
        return None
    text = raw.strip().casefold()
    if text in {"1", "true", "y", "yes"}:
        return True
    if text in {"0", "false", "n", "no"}:
        return False
    return None


def _to_decimal(raw: str | None) -> Decimal | None:
    if raw is None or not raw.strip():
        return None
    text = raw.strip().replace(",", "")
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite() or value < 0:
        return None
    return value


def _relationship_label(owner: ET.Element) -> str | None:
    rel = None
    for child in owner:
        if _local_tag(child.tag) == "reportingOwnerRelationship":
            rel = child
            break
    if rel is None:
        return None
    title = _xml_find_text(rel, "officerTitle")
    if title:
        return title[:256]
    flags: list[str] = []
    if _xml_boolish(_xml_find_text(rel, "isDirector")):
        flags.append("Director")
    if _xml_boolish(_xml_find_text(rel, "isOfficer")):
        flags.append("Officer")
    if _xml_boolish(_xml_find_text(rel, "isTenPercentOwner")):
        flags.append("10% Owner")
    if _xml_boolish(_xml_find_text(rel, "isOther")):
        other = _xml_find_text(rel, "otherText")
        flags.append(other or "Other")
    if not flags:
        return None
    return ", ".join(flags)[:256]


def _parse_form4_transactions(
    body: bytes,
    *,
    instrument_id: str,
    filed_date: date,
    accepted_at: datetime | None,
) -> tuple[USInsiderTransaction, ...] | None:
    """Return transactions, or None when the document is malformed."""
    if b"<!DOCTYPE" in body[:512] or b"<!doctype" in body[:512]:
        return None
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = body.decode("latin-1")
        except Exception:
            return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    rule_flag: bool | None = True if _RULE_10B5_RE.search(text) else None

    owners: list[ET.Element] = []
    transactions: list[ET.Element] = []

    def walk(node: ET.Element) -> None:
        tag = _local_tag(node.tag)
        if tag == "reportingOwner":
            owners.append(node)
        elif tag == "nonDerivativeTransaction":
            transactions.append(node)
        for child in node:
            walk(child)

    walk(root)
    if not owners:
        # Valid Form 4 with no reporting owner is treated as empty success set.
        return ()

    owner = owners[0]
    owner_name = _xml_find_text(owner, "reportingOwnerId", "rptOwnerName")
    if not owner_name:
        owner_name = _xml_find_text(owner, "rptOwnerName")
    if not owner_name:
        return None
    relationship = _relationship_label(owner)
    filed_at = (
        accepted_at
        if accepted_at is not None
        else datetime(filed_date.year, filed_date.month, filed_date.day, tzinfo=UTC)
    )

    out: list[USInsiderTransaction] = []
    for tx in transactions:
        tx_date_raw = _xml_find_text(tx, "transactionDate")
        tx_date = None
        if tx_date_raw:
            try:
                tx_date = date.fromisoformat(tx_date_raw[:10])
            except ValueError:
                tx_date = None
        code = _xml_find_text(tx, "transactionCoding", "transactionCode")
        ad_raw = _xml_find_text(
            tx, "transactionAmounts", "transactionAcquiredDisposedCode"
        )
        acquired: USInsiderAcquiredDisposed | None = None
        if ad_raw is not None:
            ad = ad_raw.strip().upper()
            if ad == "A":
                acquired = USInsiderAcquiredDisposed.ACQUIRED
            elif ad == "D":
                acquired = USInsiderAcquiredDisposed.DISPOSED
        shares = _to_decimal(
            _xml_find_text(tx, "transactionAmounts", "transactionShares")
        )
        price = _to_decimal(
            _xml_find_text(tx, "transactionAmounts", "transactionPricePerShare")
        )
        post = _to_decimal(
            _xml_find_text(
                tx, "postTransactionAmounts", "sharesOwnedFollowingTransaction"
            )
        )
        di_raw = _xml_find_text(tx, "ownershipNature", "directOrIndirectOwnership")
        is_direct: bool | None = None
        if di_raw is not None:
            di = di_raw.strip().upper()
            if di == "D":
                is_direct = True
            elif di == "I":
                is_direct = False
        out.append(
            USInsiderTransaction(
                instrument_id=instrument_id,
                owner_name=owner_name[:256],
                relationship=relationship,
                transaction_date=tx_date,
                filed_at=filed_at,
                accepted_at=accepted_at,
                transaction_code=code[:32] if code else None,
                acquired_disposed=acquired,
                shares=shares,
                price=price,
                post_transaction_shares=post,
                is_direct=is_direct,
                rule_10b5_1=rule_flag,
            )
        )
    return tuple(out)


class _RecentFiling:
    __slots__ = (
        "accession",
        "form",
        "is_amendment",
        "filed_date",
        "accepted_at",
        "period_of_report",
        "primary_document",
        "items",
        "url",
        "visible_at",
    )

    def __init__(
        self,
        *,
        accession: str,
        form: USFilingForm,
        is_amendment: bool,
        filed_date: date,
        accepted_at: datetime | None,
        period_of_report: date | None,
        primary_document: str | None,
        items: tuple[str, ...],
        url: str | None,
        visible_at: datetime,
    ) -> None:
        self.accession = accession
        self.form = form
        self.is_amendment = is_amendment
        self.filed_date = filed_date
        self.accepted_at = accepted_at
        self.period_of_report = period_of_report
        self.primary_document = primary_document
        self.items = items
        self.url = url
        self.visible_at = visible_at


class _ShardDescriptor:
    __slots__ = ("name", "filing_from", "filing_to", "filing_count")

    def __init__(
        self,
        *,
        name: str,
        filing_from: date | None,
        filing_to: date | None,
        filing_count: int | None,
    ) -> None:
        self.name = name
        self.filing_from = filing_from
        self.filing_to = filing_to
        self.filing_count = filing_count


class SECEdgarAdapter:
    """CategoryProvider for US filings + insider activity via SEC EDGAR."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        clock: Clock | None = None,
        enabled: bool = True,
        sec_user_agent: str | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        if transport is None:
            raise DataContractError(
                "transport is required",
                details={"field": "transport", "rule": "required"},
            )
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise DataContractError(
                "timeout_seconds must be a positive number",
                details={"field": "timeout_seconds", "rule": "positive"},
            )
        self._transport = transport
        self._clock = clock if clock is not None else SystemClock()
        self._enabled = bool(enabled)
        ua = sec_user_agent.strip() if isinstance(sec_user_agent, str) else ""
        self._sec_user_agent = ua or None
        self._timeout_seconds = float(timeout_seconds)
        self._identity: SECIdentityResolver | None = (
            SECIdentityResolver(
                transport,
                user_agent=self._sec_user_agent,
                timeout_seconds=self._timeout_seconds,
            )
            if self._sec_user_agent is not None
            else None
        )

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.SEC_EDGAR

    @property
    def provider_name(self) -> str:
        return VendorId.SEC_EDGAR.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.US and category in _SUPPORTED_CATEGORIES

    def is_configured(self) -> bool:
        return self._enabled and self._sec_user_agent is not None

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise ProviderNotConfigured(
                "SEC EDGAR adapter is not configured",
                details={"vendor": self.vendor_id.value},
            )

    def _require_as_of(self, as_of: datetime) -> datetime:
        require_aware_datetime(as_of, field_name="as_of")
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if as_of > now:
            raise DataContractError(
                "as_of must not be in the future relative to clock",
                details={"field": "as_of", "rule": "not_future"},
            )
        return now

    def _require_us_equity(self, instrument: Instrument) -> str:
        if not isinstance(instrument, Instrument):
            raise DataContractError(
                "instrument must be Instrument",
                details={"field": "instrument", "rule": "type"},
            )
        if instrument.market is not Market.US:
            raise DataContractError(
                "instrument market must be US",
                details={"field": "instrument", "rule": "market"},
            )
        if instrument.asset_type is not AssetType.EQUITY:
            raise DataContractError(
                "SEC EDGAR supports US equity only",
                details={
                    "field": "instrument",
                    "rule": "asset_type",
                    "asset_type": instrument.asset_type.value,
                },
            )
        symbol = instrument.symbol.strip().upper()
        if not symbol:
            raise DataContractError(
                "instrument symbol must be non-blank",
                details={"field": "symbol", "rule": "non_blank"},
            )
        return symbol

    def _require_limit(self, limit: int, *, operation: str) -> None:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 100:
            raise _contract(
                "limit must be an int in 1..100",
                operation=operation,
                rule="range",
            )

    def _headers(self, *, accept: str) -> dict[str, str]:
        assert self._sec_user_agent is not None
        return {
            "Accept": accept,
            "User-Agent": self._sec_user_agent,
        }

    def _raise_for_http_status(self, status_code: int, *, operation: str) -> None:
        raise_for_sec_http_status(status_code, operation=operation)

    def _meta(
        self,
        *,
        category: DataCategory,
        as_of: datetime,
        fetched_at: datetime,
        warnings: tuple[str, ...] = (),
    ) -> ProviderResultMeta:
        try:
            session = infer_session_basic(
                Market.US, as_of, timezone="America/New_York"
            )
        except DataContractError:
            session = TradingSession.UNKNOWN
        if not isinstance(session, TradingSession):
            session = TradingSession.UNKNOWN
        return ProviderResultMeta(
            vendor=self.vendor_id,
            category=category,
            role=SourceRole.PRIMARY,
            as_of=as_of,
            fetched_at=fetched_at,
            freshness=Freshness.FRESH,
            session=session,
            latency_ms=None,
            cache_disposition=CacheDisposition.MISS,
            adjustment=None,
            data_delay_seconds=None,
            warnings=warnings,
        )

    async def _get(
        self,
        url: str,
        *,
        operation: str,
        accept: str,
        allowed_content: Sequence[str],
        require_content_type: bool = True,
    ) -> HttpResponse:
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=url,
                params={},
                headers=self._headers(accept=accept),
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        )
        self._raise_for_http_status(response.status_code, operation=operation)
        if require_content_type and not _content_type_ok(
            response.headers, allowed_content
        ):
            raise _contract(
                "SEC EDGAR response Content-Type is not acceptable",
                operation=operation,
                rule="content_type",
            )
        return response

    async def _resolve_cik(self, symbol: str) -> str:
        if self._identity is None:
            raise ProviderNotConfigured(
                "SEC EDGAR adapter is not configured",
                details={"vendor": self.vendor_id.value},
            )
        return await self._identity.resolve_cik(symbol)

    async def _fetch_submissions_json(
        self, url: str, *, operation: str
    ) -> dict[str, object]:
        resp = await self._get(
            url,
            operation=operation,
            accept="application/json,text/plain,*/*",
            allowed_content=_JSON_CONTENT,
        )
        payload = _loads_json_strict(resp.body)
        if not isinstance(payload, dict):
            raise _contract(
                "submissions payload must be an object",
                operation=operation,
                rule="contract_drift",
            )
        return payload

    async def _fetch_main_submissions(self, cik10: str) -> dict[str, object]:
        return await self._fetch_submissions_json(
            f"{_SUBMISSIONS_PREFIX}CIK{cik10}.json",
            operation="submissions",
        )

    async def _fetch_shard(
        self, name: str, *, operation: str
    ) -> dict[str, object]:
        # name already validated as safe basename; fixed prefix only.
        return await self._fetch_submissions_json(
            f"{_SUBMISSIONS_PREFIX}{name}",
            operation=operation,
        )

    def _parse_aligned_rows(
        self,
        arrays_src: Mapping[str, object],
        *,
        cik10: str,
        operation: str,
    ) -> list[_RecentFiling]:
        """Parse SEC aligned list columns (recent or historical shard)."""
        arrays: dict[str, list[object]] = {}
        for key in _REQUIRED_LIST_KEYS:
            raw = arrays_src.get(key)
            if raw is None:
                raise _contract(
                    f"submissions aligned column {key} is required",
                    operation=operation,
                    rule="missing_column",
                    field=key,
                )
            if not isinstance(raw, list):
                raise _contract(
                    f"submissions aligned column {key} must be a list",
                    operation=operation,
                    rule="aligned_lists",
                    field=key,
                )
            arrays[key] = raw
        primary_len = len(arrays["accessionNumber"])
        for key in _REQUIRED_LIST_KEYS:
            if len(arrays[key]) != primary_len:
                raise _contract(
                    "submissions aligned arrays have unequal lengths",
                    operation=operation,
                    rule="aligned_lists",
                    field=key,
                )
        for key in _OPTIONAL_LIST_KEYS:
            raw = arrays_src.get(key)
            if raw is None:
                arrays[key] = [None] * primary_len
                continue
            if not isinstance(raw, list):
                raise _contract(
                    f"submissions aligned column {key} must be a list",
                    operation=operation,
                    rule="aligned_lists",
                    field=key,
                )
            if len(raw) != primary_len:
                raise _contract(
                    "submissions aligned arrays have unequal lengths",
                    operation=operation,
                    rule="aligned_lists",
                    field=key,
                )
            arrays[key] = raw

        if primary_len == 0:
            return []

        out: list[_RecentFiling] = []
        for i in range(primary_len):
            acc_raw = arrays["accessionNumber"][i]
            form_raw = arrays["form"][i]
            filed_raw = arrays["filingDate"][i]
            if not isinstance(acc_raw, str) or not acc_raw.strip():
                continue
            if not isinstance(form_raw, str):
                continue
            norm = _normalize_form(form_raw)
            if norm is None:
                continue
            form, is_amendment = norm
            filed_date = _parse_iso_date(
                filed_raw, field="filingDate", operation=operation
            )
            if filed_date is None:
                continue
            accession = acc_raw.strip()
            accepted_at = _parse_acceptance(
                arrays["acceptanceDateTime"][i], operation=operation
            )
            period = _parse_iso_date(
                arrays["reportDate"][i], field="reportDate", operation=operation
            )
            primary_doc = arrays["primaryDocument"][i]
            primary_document: str | None
            if isinstance(primary_doc, str) and primary_doc.strip():
                primary_document = primary_doc.strip()
            else:
                primary_document = None
            items = _parse_items(arrays["items"][i])
            url: str | None = None
            if primary_document is not None:
                url = _safe_archives_url(cik10, accession, primary_document)
            visible = _visible_at(filed_date, accepted_at)
            out.append(
                _RecentFiling(
                    accession=accession,
                    form=form,
                    is_amendment=is_amendment,
                    filed_date=filed_date,
                    accepted_at=accepted_at,
                    period_of_report=period,
                    primary_document=primary_document,
                    items=items,
                    url=url,
                    visible_at=visible,
                )
            )
        return out

    def _parse_recent_block(
        self,
        payload: Mapping[str, object],
        *,
        cik10: str,
        operation: str,
    ) -> tuple[list[_RecentFiling], list[_ShardDescriptor]]:
        filings = payload.get("filings")
        if not isinstance(filings, dict):
            raise _contract(
                "submissions.filings must be an object",
                operation=operation,
                rule="contract_drift",
            )
        recent = filings.get("recent")
        if not isinstance(recent, dict):
            raise _contract(
                "submissions.filings.recent must be an object",
                operation=operation,
                rule="contract_drift",
            )
        rows = self._parse_aligned_rows(recent, cik10=cik10, operation=operation)
        descriptors = self._parse_shard_descriptors(
            filings.get("files"), cik10=cik10, operation=operation
        )
        return rows, descriptors

    def _parse_shard_descriptors(
        self,
        files_raw: object,
        *,
        cik10: str,
        operation: str,
    ) -> list[_ShardDescriptor]:
        if files_raw is None:
            return []
        if not isinstance(files_raw, list):
            raise _contract(
                "submissions.filings.files must be a list",
                operation=operation,
                rule="contract_drift",
            )
        out: list[_ShardDescriptor] = []
        for idx, entry in enumerate(files_raw):
            if idx >= _MAX_SHARD_DESCRIPTORS:
                break
            if not isinstance(entry, dict):
                raise _contract(
                    "submissions shard descriptor must be an object",
                    operation=operation,
                    rule="contract_drift",
                    index=idx,
                )
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                raise _contract(
                    "submissions shard name is required",
                    operation=operation,
                    rule="unsafe_descriptor",
                    index=idx,
                )
            name = name.strip()
            if "/" in name or "\\" in name or ".." in name or name.startswith("."):
                raise _contract(
                    "submissions shard name is not a safe basename",
                    operation=operation,
                    rule="unsafe_descriptor",
                    index=idx,
                )
            match = _SHARD_NAME_RE.fullmatch(name)
            if match is None or match.group(1) != cik10:
                raise _contract(
                    "submissions shard name failed CIK##########-submissions-###.json rule",
                    operation=operation,
                    rule="unsafe_descriptor",
                    index=idx,
                )
            filing_from = _parse_iso_date(
                entry.get("filingFrom"), field="filingFrom", operation=operation
            )
            filing_to = _parse_iso_date(
                entry.get("filingTo"), field="filingTo", operation=operation
            )
            count_raw = entry.get("filingCount")
            filing_count: int | None
            if count_raw is None:
                filing_count = None
            elif type(count_raw) is int and not isinstance(count_raw, bool) and count_raw >= 0:
                filing_count = count_raw
            else:
                raise _contract(
                    "submissions shard filingCount must be a nonnegative int",
                    operation=operation,
                    rule="contract_drift",
                    index=idx,
                )
            out.append(
                _ShardDescriptor(
                    name=name,
                    filing_from=filing_from,
                    filing_to=filing_to,
                    filing_count=filing_count,
                )
            )
        # Newest historical window first (filingTo desc, then name desc).
        out.sort(
            key=lambda d: (
                d.filing_to or date.min,
                d.name,
            ),
            reverse=True,
        )
        return out

    def _filter_rows(
        self,
        rows: Sequence[_RecentFiling],
        *,
        forms: frozenset[USFilingForm] | None,
        start: date | None,
        end: date | None,
        as_of: datetime,
        apply_filed_date_range: bool,
    ) -> list[_RecentFiling]:
        selected: list[_RecentFiling] = []
        for row in rows:
            if forms is not None and row.form not in forms:
                continue
            if apply_filed_date_range:
                if start is not None and row.filed_date < start:
                    continue
                if end is not None and row.filed_date > end:
                    continue
            if row.visible_at > as_of:
                continue
            selected.append(row)
        selected.sort(
            key=lambda r: (r.visible_at, r.accession),
            reverse=True,
        )
        return selected

    def _merge_dedupe(
        self, existing: Sequence[_RecentFiling], extra: Sequence[_RecentFiling]
    ) -> list[_RecentFiling]:
        by_acc: dict[str, _RecentFiling] = {r.accession: r for r in existing}
        for row in extra:
            # First-seen wins (recent precedes shards).
            if row.accession not in by_acc:
                by_acc[row.accession] = row
        merged = list(by_acc.values())
        merged.sort(key=lambda r: (r.visible_at, r.accession), reverse=True)
        return merged

    def _shard_may_contribute(
        self,
        desc: _ShardDescriptor,
        *,
        start: date | None,
        end: date | None,
        apply_filed_date_range: bool,
    ) -> bool:
        if not apply_filed_date_range:
            return True
        too_old = (
            start is not None
            and desc.filing_to is not None
            and desc.filing_to < start
        )
        too_new = (
            end is not None
            and desc.filing_from is not None
            and desc.filing_from > end
        )
        return not too_old and not too_new

    async def _load_filtered_rows(
        self,
        *,
        cik10: str,
        forms: frozenset[USFilingForm] | None,
        start: date | None,
        end: date | None,
        as_of: datetime,
        limit: int,
        operation: str,
        apply_filed_date_range: bool,
    ) -> list[_RecentFiling]:
        payload = await self._fetch_main_submissions(cik10)
        recent_rows, descriptors = self._parse_recent_block(
            payload, cik10=cik10, operation=operation
        )
        selected = self._filter_rows(
            recent_rows,
            forms=forms,
            start=start,
            end=end,
            as_of=as_of,
            apply_filed_date_range=apply_filed_date_range,
        )
        # No start and recent already fills limit → do not fetch historical shards.
        if start is None and len(selected) >= limit:
            return selected[:limit]
        if len(selected) >= limit:
            return selected[:limit]

        for desc in descriptors:
            if len(selected) >= limit:
                break
            if not self._shard_may_contribute(
                desc,
                start=start,
                end=end,
                apply_filed_date_range=apply_filed_date_range,
            ):
                continue
            shard = await self._fetch_shard(desc.name, operation=f"{operation}_shard")
            # Historical shards use direct aligned arrays (not filings.recent).
            shard_rows = self._parse_aligned_rows(
                shard, cik10=cik10, operation=f"{operation}_shard"
            )
            filtered_shard = self._filter_rows(
                shard_rows,
                forms=forms,
                start=start,
                end=end,
                as_of=as_of,
                apply_filed_date_range=apply_filed_date_range,
            )
            selected = self._filter_rows(
                self._merge_dedupe(selected, filtered_shard),
                forms=forms,
                start=start,
                end=end,
                as_of=as_of,
                apply_filed_date_range=apply_filed_date_range,
            )
        return selected[:limit]

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
    ) -> ProviderSuccess[tuple[USFiling, ...]]:
        self._require_configured()
        self._require_as_of(as_of)
        self._require_limit(limit, operation="filings")
        if start is not None and end is not None and end < start:
            raise _contract(
                "end must be >= start",
                operation="filings",
                rule="date_range",
            )
        if not isinstance(include_sections, bool):
            raise _contract(
                "include_sections must be a bool",
                operation="filings",
                rule="type",
            )
        if forms is None or not isinstance(forms, tuple):
            raise _contract(
                "forms must be a tuple",
                operation="filings",
                rule="type",
            )
        for idx, form in enumerate(forms):
            if not isinstance(form, USFilingForm):
                raise _contract(
                    "forms elements must be USFilingForm",
                    operation="filings",
                    rule="type",
                    index=idx,
                )
        symbol = self._require_us_equity(instrument)
        cik10 = await self._resolve_cik(symbol)
        form_filter = frozenset(forms) if forms else None
        selected = await self._load_filtered_rows(
            cik10=cik10,
            forms=form_filter,
            start=start,
            end=end,
            as_of=as_of,
            limit=limit,
            operation="filings",
            apply_filed_date_range=True,
        )
        warnings: list[str] = []
        results: list[USFiling] = []
        for row in selected:
            sections: tuple[USFilingSection, ...] = ()
            if (
                include_sections
                and row.form in _SECTION_FORMS
                and row.url is not None
            ):
                try:
                    doc = await self._get(
                        row.url,
                        operation="filing_document",
                        accept="text/html,application/xhtml+xml,text/plain,*/*",
                        allowed_content=_HTML_CONTENT,
                        require_content_type=True,
                    )
                    sections = _extract_sections(
                        form=row.form, document_url=row.url, body=doc.body
                    )
                    if not sections and _WARN_SECTIONS not in warnings:
                        warnings.append(_WARN_SECTIONS)
                except (
                    DataContractError,
                    ProviderUnavailableError,
                    ProviderRateLimitError,
                    NoMarketData,
                ):
                    if _WARN_SECTIONS not in warnings:
                        warnings.append(_WARN_SECTIONS)
                    sections = ()
            results.append(
                USFiling(
                    instrument_id=instrument.instrument_id,
                    accession=row.accession,
                    form=row.form,
                    is_amendment=row.is_amendment,
                    filed_date=row.filed_date,
                    accepted_at=row.accepted_at,
                    period_of_report=row.period_of_report,
                    primary_document=row.primary_document,
                    url=row.url,
                    items=row.items,
                    sections=sections,
                )
            )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        return ProviderSuccess(
            value=tuple(results),
            meta=self._meta(
                category=DataCategory.FILINGS,
                as_of=as_of,
                fetched_at=fetched_at,
                warnings=tuple(warnings),
            ),
        )

    async def get_insider_activity(
        self,
        instrument: Instrument,
        *,
        start: date | None,
        end: date | None,
        limit: int,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[USInsiderTransaction, ...]]:
        self._require_configured()
        self._require_as_of(as_of)
        self._require_limit(limit, operation="insider_activity")
        if start is not None and end is not None and end < start:
            raise _contract(
                "end must be >= start",
                operation="insider_activity",
                rule="date_range",
            )
        symbol = self._require_us_equity(instrument)
        cik10 = await self._resolve_cik(symbol)
        # Do not filter Form 4 by filing_date start/end; over-fetch visible Form 4s.
        overfetch = min(_MAX_FORM4_OVERFETCH, max(limit * 5, 20))
        form4_rows = await self._load_filtered_rows(
            cik10=cik10,
            forms=frozenset({USFilingForm.FORM_4}),
            start=None,
            end=None,
            as_of=as_of,
            limit=overfetch,
            operation="insider_activity",
            apply_filed_date_range=False,
        )
        warnings: list[str] = []
        transactions: list[USInsiderTransaction] = []
        date_filter = start is not None or end is not None
        for row in form4_rows:
            if row.url is None:
                if _WARN_FORM4 not in warnings:
                    warnings.append(_WARN_FORM4)
                continue
            try:
                doc = await self._get(
                    row.url,
                    operation="form4_document",
                    accept="application/xml,text/xml,text/plain,*/*",
                    allowed_content=_XML_CONTENT,
                    require_content_type=True,
                )
            except (
                DataContractError,
                ProviderUnavailableError,
                ProviderRateLimitError,
                NoMarketData,
            ):
                if _WARN_FORM4 not in warnings:
                    warnings.append(_WARN_FORM4)
                continue
            parsed = _parse_form4_transactions(
                doc.body,
                instrument_id=instrument.instrument_id,
                filed_date=row.filed_date,
                accepted_at=row.accepted_at,
            )
            if parsed is None:
                if _WARN_FORM4 not in warnings:
                    warnings.append(_WARN_FORM4)
                continue
            for tx in parsed:
                if date_filter and tx.transaction_date is None:
                    continue
                if start is not None and (
                    tx.transaction_date is None or tx.transaction_date < start
                ):
                    continue
                if end is not None and (
                    tx.transaction_date is None or tx.transaction_date > end
                ):
                    continue
                transactions.append(tx)
        # Stable newest-first: transaction_date, then visible time, then owner.
        transactions.sort(
            key=lambda t: (
                t.transaction_date or date.min,
                t.accepted_at
                or t.filed_at
                or datetime.min.replace(tzinfo=UTC),
                t.owner_name,
            ),
            reverse=True,
        )
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        return ProviderSuccess(
            value=tuple(transactions[:limit]),
            meta=self._meta(
                category=DataCategory.INSIDER_ACTIVITY,
                as_of=as_of,
                fetched_at=fetched_at,
                warnings=tuple(warnings),
            ),
        )
