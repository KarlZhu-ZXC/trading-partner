"""Phase 1E E4c Sina ETF option adapter fixture contracts (offline)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from a_share_fixture_transport import ScriptedHttpTransport
from application.ports.http_transport import HttpRequest, HttpResponse
from conftest import FixedClock
from domain.a_share.enums import OptionType
from domain.common.enums import AssetType, DataCategory, Market
from domain.common.errors import (
    DataContractError,
    NoMarketData,
    ProviderRateLimitError,
)
from domain.instruments.models import Instrument
from infrastructure.providers.a_share.sina import SinaAShareAdapter

AS_OF = datetime(2026, 7, 17, 7, 0, tzinfo=UTC)  # 15:00 Shanghai
_FIXTURE_ROOT = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "infrastructure"
    / "providers"
    / "a_share"
    / "fixtures"
    / "sina"
    / "options"
)
_JSON = {"content-type": "application/json; charset=utf-8"}
_JS = {"content-type": "application/javascript; charset=GB18030"}
_MISSING = object()


def _etf(symbol: str = "510050.SH") -> Instrument:
    return Instrument(
        instrument_id=f"etf:A_SHARE:{symbol}",
        symbol=symbol,
        name="50ETF",
        market=Market.A_SHARE,
        exchange="SSE" if symbol.endswith(".SH") else "SZSE",
        currency="CNY",
        timezone="Asia/Shanghai",
        asset_type=AssetType.ETF,
    )


def _body(name: str) -> bytes:
    path = _FIXTURE_ROOT / name
    return path.read_bytes()


class OptionFixtureTransport:
    """Route option endpoints to offline fixtures by path/params."""

    def __init__(
        self,
        *,
        remainder: str = "remainder_success.json",
        chain: str = "chain_success.txt",
        quotes: str = "quotes_success.txt",
        greeks: str = "greeks_selected_3p0.txt",
        stock_name: str = "stock_name_success.json",
        stock_name_status: int = 200,
        greeks_body: bytes | None = None,
        quotes_body: bytes | None = None,
    ) -> None:
        self.requests: list[HttpRequest] = []
        self._remainder = remainder
        self._chain = chain
        self._quotes = quotes
        self._greeks = greeks
        self._stock_name = stock_name
        self._stock_name_status = stock_name_status
        self._greeks_body = greeks_body
        self._quotes_body = quotes_body

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        path = urlsplit(request.url).path.lower()
        if path.endswith("stockoptionservice.getstockname"):
            return HttpResponse(
                status_code=self._stock_name_status,
                headers=dict(_JSON),
                body=_body(self._stock_name) if self._stock_name_status == 200 else b"",
            )
        if path.endswith("stockoptionservice.getremainderday"):
            body = _body(self._remainder)
            # Success remainder fixture is July-shaped; rewrite expireDay/cateId to
            # the requested month so multi-month nearest-month scans stay live-shaped.
            if self._remainder == "remainder_success.json":
                month = (request.params or {}).get("date", "2026-07")
                payload = json.loads(body.decode("utf-8"))
                data = payload["result"]["data"]
                y, m = month.split("-")
                # Third Wednesday-ish placeholder within the requested month.
                data["expireDay"] = f"{y}-{m}-22"
                data["cateId"] = f"510050C{y[2:]}{m}"
                if month != "2026-07":
                    # Keep July as the nearest nonexpired for expiry=None tests.
                    data["remainderDays"] = 40
                body = json.dumps(payload).encode("utf-8")
            return HttpResponse(status_code=200, headers=dict(_JSON), body=body)
        if path == "/list" or path.endswith("/list"):
            list_param = (request.params or {}).get("list", "")
            if "OP_UP_" in list_param or "OP_DOWN_" in list_param:
                return HttpResponse(status_code=200, headers=dict(_JS), body=_body(self._chain))
            if "CON_SO_" in list_param:
                body = self._greeks_body if self._greeks_body is not None else _body(self._greeks)
                return HttpResponse(status_code=200, headers=dict(_JS), body=body)
            body = self._quotes_body if self._quotes_body is not None else _body(self._quotes)
            return HttpResponse(status_code=200, headers=dict(_JS), body=body)
        raise FileNotFoundError(f"unmapped option request path {path!r}")


def _adapter(transport: object, *, current_window_seconds: int = 300) -> SinaAShareAdapter:
    return SinaAShareAdapter(
        transport,  # type: ignore[arg-type]
        clock=FixedClock(AS_OF),
        current_window_seconds=current_window_seconds,
        max_fresh_seconds=15,
        max_delayed_seconds=86_400,
    )


@pytest.mark.asyncio
async def test_option_snapshot_success_sorted_and_greeks_join() -> None:
    transport = OptionFixtureTransport()
    adapter = _adapter(transport)
    result = await adapter.get_option_snapshot(
        _etf(),
        expiry=date(2026, 7, 22),
        strike_center=Decimal("3.0"),
        strike_count_each_side=0,
        as_of=AS_OF,
    )
    assert result.meta.category is DataCategory.OPTIONS
    assert result.meta.vendor.value == "sina"
    snap = result.value
    assert snap.underlying_instrument_id == "etf:A_SHARE:510050.SH"
    assert snap.expiry == date(2026, 7, 22)
    assert len(snap.quotes) == 2
    assert len(snap.greeks) == 2
    assert snap.quotes[0].contract.option_type is OptionType.CALL
    assert snap.quotes[1].contract.option_type is OptionType.PUT
    assert snap.quotes[0].contract.strike == Decimal("3.000")
    assert snap.quotes[0].contract.instrument_id == "option:A_SHARE:10007601"
    assert snap.quotes[0].contract.multiplier is None
    assert snap.quotes[0].bid_prices == (Decimal("0.1200"),)
    assert snap.quotes[0].ask_prices == (Decimal("0.1300"),)
    assert all(g.source_provided is True for g in snap.greeks)
    assert [g.contract_instrument_id for g in snap.greeks] == [
        q.contract.instrument_id for q in snap.quotes
    ]
    assert all(
        (r.headers or {}).get("Referer") == "https://finance.sina.com.cn/"
        for r in transport.requests
    )


@pytest.mark.asyncio
async def test_option_snapshot_nearest_month_when_expiry_none() -> None:
    transport = OptionFixtureTransport()
    adapter = _adapter(transport)
    result = await adapter.get_option_snapshot(
        _etf(),
        expiry=None,
        strike_center=Decimal("3.0"),
        strike_count_each_side=0,
        as_of=AS_OF,
    )
    assert result.value.expiry == date(2026, 7, 22)


@pytest.mark.asyncio
async def test_option_snapshot_standard_m_and_adjusted_ab_trading_codes() -> None:
    for fixture in (
        "greeks_selected_3p0.txt",
        "greeks_adjusted_a.txt",
        "greeks_adjusted_b.txt",
    ):
        transport = OptionFixtureTransport(greeks=fixture)
        adapter = _adapter(transport)
        result = await adapter.get_option_snapshot(
            _etf(),
            expiry=date(2026, 7, 22),
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
        assert len(result.value.quotes) == 2
        assert all(g.source_provided is True for g in result.value.greeks)


@pytest.mark.asyncio
async def test_pre_network_rejections_without_network() -> None:
    unknown = Instrument(
        instrument_id="etf:A_SHARE:510880.SH",
        symbol="510880.SH",
        name="unknown",
        market=Market.A_SHARE,
        exchange="SSE",
        currency="CNY",
        timezone="Asia/Shanghai",
        asset_type=AssetType.ETF,
    )

    transport = OptionFixtureTransport()
    with pytest.raises(DataContractError) as exc:
        await _adapter(transport).get_option_snapshot(
            unknown,
            expiry=None,
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "unsupported_etf"
    assert transport.requests == []

    transport = OptionFixtureTransport()
    adapter = _adapter(transport, current_window_seconds=60)
    stale = AS_OF - timedelta(seconds=120)
    from domain.common.errors import StaleMarketData

    with pytest.raises(StaleMarketData) as exc:
        await adapter.get_option_snapshot(
            _etf(),
            expiry=date(2026, 7, 22),
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=stale,
        )
    assert exc.value.details.get("rule") == "current_window"
    assert transport.requests == []

    transport = OptionFixtureTransport()
    with pytest.raises(DataContractError) as exc:
        await _adapter(transport).get_option_snapshot(
            _etf(),
            expiry=date(2026, 7, 22),
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF + timedelta(hours=1),
        )
    assert exc.value.details.get("rule") == "not_future"
    assert transport.requests == []


@pytest.mark.asyncio
async def test_remainder_stock_id_mismatch_is_contract_drift() -> None:
    transport = OptionFixtureTransport(remainder="remainder_contract_drift.json")
    adapter = _adapter(transport)
    with pytest.raises(DataContractError) as exc:
        await adapter.get_option_snapshot(
            _etf(),
            expiry=date(2026, 7, 22),
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "stock_id_mismatch"


@pytest.mark.parametrize(
    ("field", "value", "rule"),
    [
        ("name", _MISSING, "other_name"),
        ("name", "   ", "other_name"),
        ("name", 50, "other_name"),
        ("url", _MISSING, "other_url"),
        ("url", "   ", "other_url"),
        ("url", 50, "other_url"),
    ],
    ids=[
        "name-missing",
        "name-blank",
        "name-nonstr",
        "url-missing",
        "url-blank",
        "url-nonstr",
    ],
)
@pytest.mark.asyncio
async def test_remainder_other_surface_drift_rejected(field: str, value: object, rule: str) -> None:
    class _T(OptionFixtureTransport):
        async def send(self, request: HttpRequest) -> HttpResponse:
            response = await super().send(request)
            if (
                not urlsplit(request.url)
                .path.lower()
                .endswith("stockoptionservice.getremainderday")
            ):
                return response
            payload = json.loads(response.body.decode("utf-8"))
            other = payload["result"]["data"]["other"]
            if value is _MISSING:
                other.pop(field)
            else:
                other[field] = value
            return HttpResponse(
                status_code=response.status_code,
                headers=response.headers,
                body=json.dumps(payload).encode("utf-8"),
            )

    adapter = _adapter(_T())
    with pytest.raises(DataContractError) as exc:
        await adapter.get_option_snapshot(
            _etf(),
            expiry=date(2026, 7, 22),
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == rule


@pytest.mark.asyncio
async def test_missing_counterpart_is_contract_drift() -> None:
    quotes = (_FIXTURE_ROOT / "quotes_success.txt").read_text(encoding="utf-8")
    lines = [
        ln
        for ln in quotes.splitlines()
        if "CON_OP_10007604" not in ln  # put @ 2.9 not in chain
    ]
    quotes_body = ("\n".join(lines) + "\n").encode("utf-8")

    class _T(OptionFixtureTransport):
        async def send(self, request: HttpRequest) -> HttpResponse:
            self.requests.append(request)
            path = urlsplit(request.url).path.lower()
            if path.endswith("stockoptionservice.getstockname"):
                return HttpResponse(
                    status_code=200,
                    headers=dict(_JSON),
                    body=_body("stock_name_success.json"),
                )
            if path.endswith("stockoptionservice.getremainderday"):
                return HttpResponse(
                    status_code=200,
                    headers=dict(_JSON),
                    body=_body("remainder_success.json"),
                )
            list_param = (request.params or {}).get("list", "")
            if "OP_UP_" in list_param:
                return HttpResponse(
                    status_code=200,
                    headers=dict(_JS),
                    body=_body("chain_missing_put.txt"),
                )
            if "CON_SO_" in list_param:
                return HttpResponse(status_code=200, headers=dict(_JS), body=b"")
            return HttpResponse(status_code=200, headers=dict(_JS), body=quotes_body)

    adapter = _adapter(_T())
    with pytest.raises(DataContractError) as exc:
        await adapter.get_option_snapshot(
            _etf(),
            expiry=date(2026, 7, 22),
            strike_center=Decimal("2.9"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "missing_counterpart"


@pytest.mark.asyncio
async def test_rate_limit_on_stock_name() -> None:
    transport = ScriptedHttpTransport(
        responses=[
            HttpResponse(status_code=429, headers=dict(_JSON), body=b""),
        ]
    )
    adapter = _adapter(transport)
    with pytest.raises(ProviderRateLimitError):
        await adapter.get_option_snapshot(
            _etf(),
            expiry=date(2026, 7, 22),
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )


@pytest.mark.asyncio
async def test_statements_still_supported() -> None:
    """OPTIONS support must not remove prior statements/capital categories."""
    adapter = _adapter(OptionFixtureTransport())
    assert adapter.supports(Market.A_SHARE, DataCategory.FINANCIAL_STATEMENTS)
    assert adapter.supports(Market.A_SHARE, DataCategory.CAPITAL)
    assert adapter.supports(Market.A_SHARE, DataCategory.OPTIONS)
    assert not adapter.supports(Market.US, DataCategory.OPTIONS)


@pytest.mark.asyncio
async def test_strike_window_validation() -> None:
    adapter = _adapter(OptionFixtureTransport())
    with pytest.raises(DataContractError) as exc:
        await adapter.get_option_snapshot(
            _etf(),
            expiry=date(2026, 7, 22),
            strike_center=Decimal("-1"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "positive_finite"
    with pytest.raises(DataContractError) as exc2:
        await adapter.get_option_snapshot(
            _etf(),
            expiry=date(2026, 7, 22),
            strike_center=Decimal("3.0"),
            strike_count_each_side=21,
            as_of=AS_OF,
        )
    assert exc2.value.details.get("rule") == "range"


@pytest.mark.asyncio
async def test_expiry_mismatch_rejects() -> None:
    transport = OptionFixtureTransport()
    adapter = _adapter(transport)
    with pytest.raises(DataContractError) as exc:
        await adapter.get_option_snapshot(
            _etf(),
            expiry=date(2026, 7, 21),  # month available but expireDay is 22
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "expiry_mismatch"


@pytest.mark.asyncio
async def test_no_data_when_month_unavailable() -> None:
    transport = OptionFixtureTransport()
    adapter = _adapter(transport)
    with pytest.raises(NoMarketData):
        await adapter.get_option_snapshot(
            _etf(),
            expiry=date(2027, 1, 20),
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )


@pytest.mark.asyncio
async def test_hq_rejects_unmatched_script_line() -> None:
    bad = (_FIXTURE_ROOT / "quotes_success.txt").read_text(encoding="utf-8") + "alert('xss');\n"
    transport = OptionFixtureTransport(quotes_body=bad.encode("utf-8"))
    adapter = _adapter(transport)
    with pytest.raises(DataContractError) as exc:
        await adapter.get_option_snapshot(
            _etf(),
            expiry=date(2026, 7, 22),
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "hq_line_grammar"


@pytest.mark.parametrize(
    "body",
    [
        'var hq_str_CON_OP_10007601="safe";alert(1);//";',
        'var hq_str_CON_OP_10007601="safe";var hq_str_EXTRA="evil";',
        'var hq_str_CON_OP_10007601="safe\\payload";',
        'var hq_str_CON_OP_10007601="safe\npayload";',
    ],
    ids=["embedded-script", "extra-assignment", "backslash", "embedded-newline"],
)
def test_hq_safe_assignment_grammar_rejects_adversarial_bodies(body: str) -> None:
    adapter = _adapter(OptionFixtureTransport())
    with pytest.raises(DataContractError) as exc:
        adapter._parse_hq_var_assignments(  # noqa: SLF001 — parser security contract
            body, operation="options_quotes"
        )
    assert exc.value.details.get("rule") == "hq_line_grammar"


def test_hq_safe_assignment_grammar_accepts_gb18030_chinese_body() -> None:
    raw = 'var hq_str_CON_OP_10007601="50ETF购7月3000,3.000,正常";'.encode("gb18030")
    text = raw.decode("gb18030")
    adapter = _adapter(OptionFixtureTransport())
    assert adapter._parse_hq_var_assignments(  # noqa: SLF001 — parser security contract
        text, operation="options_quotes"
    ) == {"CON_OP_10007601": "50ETF购7月3000,3.000,正常"}


@pytest.mark.asyncio
async def test_hq_rejects_duplicate_var_lines() -> None:
    base = (_FIXTURE_ROOT / "greeks_selected_3p0.txt").read_text(encoding="utf-8")
    first = base.splitlines()[0]
    transport = OptionFixtureTransport(greeks_body=(base + first + "\n").encode("utf-8"))
    adapter = _adapter(transport)
    with pytest.raises(DataContractError) as exc:
        await adapter.get_option_snapshot(
            _etf(),
            expiry=date(2026, 7, 22),
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "duplicate_var"


@pytest.mark.asyncio
async def test_mixed_local_quote_dates_rejected() -> None:
    quotes = (_FIXTURE_ROOT / "quotes_success.txt").read_text(encoding="utf-8")
    # Shift one contract's quote date across local midnight.
    mixed = quotes.replace(
        "var hq_str_CON_OP_10007602="
        '"10,0.1200,0.0800,0.1300,12,100,1.2,3.000,0.1100,0.1150,0.2000,0.0500,'
        "0,0,0,0,0,0,0,0,0.1300,12,0.1200,10,0,0,0,0,0,0,0,0,"
        "2026-07-17 14:55:00",
        "var hq_str_CON_OP_10007602="
        '"10,0.1200,0.0800,0.1300,12,100,1.2,3.000,0.1100,0.1150,0.2000,0.0500,'
        "0,0,0,0,0,0,0,0,0.1300,12,0.1200,10,0,0,0,0,0,0,0,0,"
        "2026-07-16 14:55:00",
        1,
    )
    transport = OptionFixtureTransport(quotes_body=mixed.encode("utf-8"))
    adapter = _adapter(transport)
    with pytest.raises(DataContractError) as exc:
        await adapter.get_option_snapshot(
            _etf(),
            expiry=date(2026, 7, 22),
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "mixed_quote_dates"


@pytest.mark.asyncio
async def test_envelope_rejects_nonzero_status_code() -> None:
    payload = (
        b'{"result":{"status":{"code":1},"data":{"cateList":["50ETF"],'
        b'"contractMonth":["2026-07"]}}}'
    )

    class _T(OptionFixtureTransport):
        async def send(self, request: HttpRequest) -> HttpResponse:
            self.requests.append(request)
            path = urlsplit(request.url).path.lower()
            if path.endswith("stockoptionservice.getstockname"):
                return HttpResponse(status_code=200, headers=dict(_JSON), body=payload)
            return await super().send(request)

    adapter = _adapter(_T())
    with pytest.raises(DataContractError) as exc:
        await adapter.get_option_snapshot(
            _etf(),
            expiry=date(2026, 7, 22),
            strike_center=Decimal("3.0"),
            strike_count_each_side=0,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "status_code"


@pytest.mark.asyncio
async def test_hq_rejects_live_percent_encoded_comma_symbol_shape() -> None:
    """Exact live failure when list commas were percent-encoded as %2C.

    Sina does not decode %2C and returns a single empty assignment whose
    variable name literally contains ``%2C``. Parser must fail closed
    (never eval) via the safe assignment grammar.
    """
    body = _body("hq_encoded_comma_failure.txt")
    assert b"%2C" in body
    assert body.strip() == b'var hq_str_OP_UP_5100502607%2COP_DOWN_5100502607="";'

    class _T:
        def __init__(self) -> None:
            self.requests: list[HttpRequest] = []

        async def send(self, request: HttpRequest) -> HttpResponse:
            self.requests.append(request)
            return HttpResponse(status_code=200, headers=dict(_JS), body=body)

    adapter = _adapter(_T())
    with pytest.raises(DataContractError) as exc:
        await adapter._fetch_hq_list(  # noqa: SLF001 — regression on private parse path
            ["OP_UP_5100502607", "OP_DOWN_5100502607"],
            operation="options_chain",
        )
    assert exc.value.details.get("rule") == "hq_line_grammar"
    # Never eval: error details must not echo the raw script body.
    blob = json.dumps(exc.value.details) + exc.value.message
    assert "eval" not in blob.lower()
    assert "%2C" not in blob


@pytest.mark.asyncio
async def test_hq_batches_when_symbols_exceed_80() -> None:
    """>80 HQ symbols → multiple requests; each batch ≤80; exact symbol validation."""
    symbols = [f"CON_OP_{10000000 + i}" for i in range(85)]
    assert len(symbols) > 80

    class _BatchTransport:
        def __init__(self) -> None:
            self.requests: list[HttpRequest] = []

        async def send(self, request: HttpRequest) -> HttpResponse:
            self.requests.append(request)
            list_param = (request.params or {}).get("list", "")
            batch = [s for s in list_param.split(",") if s]
            assert 1 <= len(batch) <= 80
            # Exact requested variables only — empty bodies still match grammar.
            lines = [f'var hq_str_{sym}="";' for sym in batch]
            body = ("\n".join(lines) + "\n").encode("utf-8")
            return HttpResponse(status_code=200, headers=dict(_JS), body=body)

    transport = _BatchTransport()
    adapter = _adapter(transport)
    result = await adapter._fetch_hq_list(  # noqa: SLF001 — unit contract on batching
        symbols, operation="options_quotes"
    )

    assert len(transport.requests) == 2  # 80 + 5
    batch_sizes = [len((r.params or {}).get("list", "").split(",")) for r in transport.requests]
    assert batch_sizes == [80, 5]
    assert all(size <= 80 for size in batch_sizes)
    # Comma-joined list param preserved as structured values (transport encodes later).
    first_list = (transport.requests[0].params or {})["list"]
    assert "," in first_list
    assert "%2C" not in first_list
    assert list(result.keys()) == symbols or set(result) == set(symbols)
    assert set(result) == set(symbols)
    assert len(result) == 85
    # Each batch independently required exact match; aggregate has no duplicates.
    assert len(result) == len(set(result))


@pytest.mark.asyncio
async def test_hq_batch_exact_symbols_fail_closed_on_missing() -> None:
    """A single incomplete batch fails exact_symbols; no silent partial aggregate."""
    symbols = [f"CON_OP_{10000000 + i}" for i in range(81)]

    class _T:
        def __init__(self) -> None:
            self.requests: list[HttpRequest] = []

        async def send(self, request: HttpRequest) -> HttpResponse:
            self.requests.append(request)
            list_param = (request.params or {}).get("list", "")
            batch = list_param.split(",")
            # Drop the last symbol from every batch → exact_symbols fails.
            incomplete = batch[:-1] if len(batch) > 1 else batch
            lines = [f'var hq_str_{sym}="";' for sym in incomplete]
            body = ("\n".join(lines) + "\n").encode("utf-8")
            return HttpResponse(status_code=200, headers=dict(_JS), body=body)

    transport = _T()
    adapter = _adapter(transport)
    with pytest.raises(DataContractError) as exc:
        await adapter._fetch_hq_list(  # noqa: SLF001
            symbols, operation="options_quotes"
        )
    assert exc.value.details.get("rule") == "exact_symbols"
    # First batch (80) fails before second request is issued.
    assert len(transport.requests) == 1
    assert len((transport.requests[0].params or {})["list"].split(",")) == 80


# ---------------------------------------------------------------------------
# Depth normalization: zero price is authoritative absent-level sentinel
# ---------------------------------------------------------------------------

# Live-smoked Sina CON_OP put 10011693 strike 2.9500 — ask5..ask1 price/qty pairs.
# ask2 has price 0.0000 with stale nonzero qty 124 (official map remains price/qty).
_LIVE_ZERO_PRICE_STALE_QTY_ASK5_TO_ASK1: tuple[tuple[str, str], ...] = (
    ("0.0000", "0"),
    ("0.0000", "0"),
    ("0.0000", "0"),
    ("0.0000", "124"),
    ("0.0399", "32"),
)


def _depth_fields(
    *,
    ask5_to_ask1: tuple[tuple[str, str], ...] | None = None,
    bid1_to_bid5: tuple[tuple[str, str], ...] | None = None,
) -> list[str]:
    """Build a 45-field CON_OP-shaped list with only depth slots filled."""
    fields = [""] * 45
    asks = ask5_to_ask1 or tuple(("0", "0") for _ in range(5))
    bids = bid1_to_bid5 or tuple(("0", "0") for _ in range(5))
    assert len(asks) == 5 and len(bids) == 5
    for i, (p, q) in enumerate(asks):
        fields[12 + i * 2] = p
        fields[12 + i * 2 + 1] = q
    for i, (p, q) in enumerate(bids):
        fields[22 + i * 2] = p
        fields[22 + i * 2 + 1] = q
    return fields


def test_depth_accepts_live_zero_price_stale_qty_ask_shape() -> None:
    """Exact live sentinel: zero price omits level even when qty residue is nonzero."""
    adapter = _adapter(OptionFixtureTransport())
    fields = _depth_fields(ask5_to_ask1=_LIVE_ZERO_PRICE_STALE_QTY_ASK5_TO_ASK1)
    prices, volumes = adapter._parse_depth_levels(  # noqa: SLF001
        fields, start=12, best_first=False, side="ask"
    )
    # Best-first after reverse: only ask1 present; ask2 zero-price omitted as absent.
    assert prices == (Decimal("0.0399"),)
    assert volumes == (32,)


def test_depth_frozen_fixture_con_op_10011693_zero_price_stale_qty() -> None:
    """Frozen CON_OP line for put 10011693 strike 2.9500 (live smoke shape)."""
    raw = _body("quotes_zero_price_stale_qty_ask.txt").decode("utf-8").strip()
    # Exact assignment grammar + frozen ask5..ask1 pairs.
    assert raw.startswith('var hq_str_CON_OP_10011693="')
    assert raw.endswith('";')
    body = raw[len('var hq_str_CON_OP_10011693="') : -2]
    fields = body.split(",")
    # ask5..ask1 at indices 12..21
    live_pairs = tuple((fields[12 + i * 2], fields[12 + i * 2 + 1]) for i in range(5))
    assert live_pairs == _LIVE_ZERO_PRICE_STALE_QTY_ASK5_TO_ASK1
    assert fields[7] == "2.9500"

    adapter = _adapter(OptionFixtureTransport())
    quote, strike = adapter._parse_con_op(  # noqa: SLF001
        body,
        contract_id="10011693",
        option_type=OptionType.PUT,
        underlying_instrument_id="etf:A_SHARE:510050.SH",
        expiry=date(2026, 7, 22),
        as_of=AS_OF,
    )
    assert strike == Decimal("2.9500")
    assert quote.ask_prices == (Decimal("0.0399"),)
    assert quote.ask_volumes == (32,)
    assert quote.bid_prices == (Decimal("0.0390"),)
    assert quote.bid_volumes == (20,)


def test_depth_rejects_positive_price_with_zero_quantity() -> None:
    adapter = _adapter(OptionFixtureTransport())
    fields = _depth_fields(
        ask5_to_ask1=(
            ("0", "0"),
            ("0", "0"),
            ("0", "0"),
            ("0", "0"),
            ("0.0399", "0"),  # ask1: positive price, zero qty
        )
    )
    with pytest.raises(DataContractError) as exc:
        adapter._parse_depth_levels(  # noqa: SLF001
            fields, start=12, best_first=False, side="ask"
        )
    assert exc.value.details.get("rule") == "depth_pair"


def test_depth_rejects_positive_price_with_missing_quantity() -> None:
    adapter = _adapter(OptionFixtureTransport())
    fields = _depth_fields(
        ask5_to_ask1=(
            ("0", "0"),
            ("0", "0"),
            ("0", "0"),
            ("0", "0"),
            ("0.0399", ""),  # ask1: positive price, blank qty
        )
    )
    with pytest.raises(DataContractError) as exc:
        adapter._parse_depth_levels(  # noqa: SLF001
            fields, start=12, best_first=False, side="ask"
        )
    assert exc.value.details.get("rule") == "depth_pair"


def test_depth_rejects_missing_price_with_nonzero_quantity() -> None:
    """Blank/missing price + nonzero qty is depth_pair (not the zero-price sentinel)."""
    adapter = _adapter(OptionFixtureTransport())
    fields = _depth_fields(
        ask5_to_ask1=(
            ("0", "0"),
            ("0", "0"),
            ("0", "0"),
            ("", "124"),  # ask2: missing price, nonzero qty
            ("0.0399", "32"),
        )
    )
    with pytest.raises(DataContractError) as exc:
        adapter._parse_depth_levels(  # noqa: SLF001
            fields, start=12, best_first=False, side="ask"
        )
    assert exc.value.details.get("rule") == "depth_pair"


def test_depth_rejects_negative_price() -> None:
    adapter = _adapter(OptionFixtureTransport())
    fields = _depth_fields(
        bid1_to_bid5=(
            ("-0.01", "10"),
            ("0", "0"),
            ("0", "0"),
            ("0", "0"),
            ("0", "0"),
        )
    )
    with pytest.raises(DataContractError) as exc:
        adapter._parse_depth_levels(  # noqa: SLF001
            fields, start=22, best_first=True, side="bid"
        )
    assert exc.value.details.get("rule") == "nonnegative"


def test_depth_rejects_negative_quantity() -> None:
    adapter = _adapter(OptionFixtureTransport())
    fields = _depth_fields(
        bid1_to_bid5=(
            ("0.11", "-1"),
            ("0", "0"),
            ("0", "0"),
            ("0", "0"),
            ("0", "0"),
        )
    )
    with pytest.raises(DataContractError) as exc:
        adapter._parse_depth_levels(  # noqa: SLF001
            fields, start=22, best_first=True, side="bid"
        )
    assert exc.value.details.get("rule") == "nonnegative"


def test_depth_rejects_negative_qty_on_zero_price_level() -> None:
    """Zero price does not launder a negative quantity residue."""
    adapter = _adapter(OptionFixtureTransport())
    fields = _depth_fields(
        ask5_to_ask1=(
            ("0", "0"),
            ("0", "0"),
            ("0", "0"),
            ("0.0000", "-5"),
            ("0.0399", "32"),
        )
    )
    with pytest.raises(DataContractError) as exc:
        adapter._parse_depth_levels(  # noqa: SLF001
            fields, start=12, best_first=False, side="ask"
        )
    assert exc.value.details.get("rule") == "nonnegative"


def test_depth_rejects_data_after_absent_level() -> None:
    """Valid deeper level after an omitted absent level is still a depth_gap."""
    adapter = _adapter(OptionFixtureTransport())
    # Best-first bid: bid1 present, bid2 absent (zero price), bid3 present → gap.
    fields = _depth_fields(
        bid1_to_bid5=(
            ("0.12", "10"),
            ("0.0000", "0"),
            ("0.10", "5"),
            ("0", "0"),
            ("0", "0"),
        )
    )
    with pytest.raises(DataContractError) as exc:
        adapter._parse_depth_levels(  # noqa: SLF001
            fields, start=22, best_first=True, side="bid"
        )
    assert exc.value.details.get("rule") == "depth_gap"


def test_depth_rejects_data_after_zero_price_stale_qty_absent() -> None:
    """Zero-price+stale-qty counts as absent for contiguous-depth enforcement."""
    adapter = _adapter(OptionFixtureTransport())
    # ask1 present, ask2 zero-price+stale qty (absent), ask3 present → gap after reverse.
    fields = _depth_fields(
        ask5_to_ask1=(
            ("0", "0"),
            ("0", "0"),
            ("0.05", "8"),  # ask3 present
            ("0.0000", "124"),  # ask2 absent via zero price
            ("0.0399", "32"),  # ask1 present
        )
    )
    with pytest.raises(DataContractError) as exc:
        adapter._parse_depth_levels(  # noqa: SLF001
            fields, start=12, best_first=False, side="ask"
        )
    assert exc.value.details.get("rule") == "depth_gap"
