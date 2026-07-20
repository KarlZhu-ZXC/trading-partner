"""Phase 1D D5a: CacheEntry / health / rate-limit DTOs + cache-key grammar."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from application.dto.provider_state import (
    CacheEntry,
    ProviderHealthSnapshot,
    ProviderRateLimitSnapshot,
)
from application.services.provider_cache_support import (
    build_cache_key,
    parse_cache_key,
    require_cache_key_matches_entry,
    require_valid_fingerprint,
)
from domain.common.enums import (
    AppEnvironment,
    CircuitState,
    DataCategory,
    Freshness,
    HealthState,
    LogLevel,
    Market,
    VendorId,
)
from domain.common.errors import DataContractError
from infrastructure.config.settings import AppSettings

AS_OF = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
FETCHED = datetime(2026, 7, 16, 12, 0, 1, tzinfo=UTC)
EXPIRES = datetime(2026, 7, 16, 12, 5, 1, tzinfo=UTC)
GOOD_FP = "get_quote|a1b2c3d4e5f67890"
GOOD_KEY = "v1|US|market_quote|equity:US:NVDA|2026-07-16T12:00:00+00:00|get_quote|a1b2c3d4e5f67890"


def _cache_entry(**overrides: object) -> CacheEntry:
    base: dict[str, object] = {
        "key": GOOD_KEY,
        "category": DataCategory.MARKET_QUOTE,
        "market": Market.US,
        "instrument_id": "equity:US:NVDA",
        "vendor": VendorId.MOCK_US,
        "payload_json": '{"price":"100.00"}',
        "as_of": AS_OF,
        "fetched_at": FETCHED,
        "expires_at": EXPIRES,
        "freshness": Freshness.FRESH,
    }
    base.update(overrides)
    return CacheEntry(**base)  # type: ignore[arg-type]


def _mismatched_entry(
    *,
    key: str,
    category: DataCategory = DataCategory.MARKET_QUOTE,
    market: Market = Market.US,
    instrument_id: str | None = "equity:US:NVDA",
    as_of: datetime = AS_OF,
) -> CacheEntry:
    return CacheEntry(
        key=key,
        category=category,
        market=market,
        instrument_id=instrument_id,
        vendor=VendorId.MOCK_US,
        payload_json='{"price":"1"}',
        as_of=as_of,
        fetched_at=FETCHED,
        expires_at=EXPIRES,
        freshness=Freshness.FRESH,
    )


def _settings(**overrides: object) -> AppSettings:
    base: dict[str, object] = {
        "app_name": "tp",
        "app_env": AppEnvironment.TEST,
        "log_level": LogLevel.INFO,
        "database_url": "sqlite:////tmp/d5a.db",
        "mcp_server_name": "tp",
        "default_timezone": "UTC",
        "provider_timeout_seconds": 1.0,
    }
    base.update(overrides)
    return AppSettings(_env_file=None, **base)  # type: ignore[call-arg]


def test_dto_exports() -> None:
    from application.dto import CacheEntry as CE
    from application.dto import ProviderHealthSnapshot as HS
    from application.dto import ProviderRateLimitSnapshot as RS

    assert CE is CacheEntry
    assert HS is ProviderHealthSnapshot
    assert RS is ProviderRateLimitSnapshot


def _assert_no_secret_in_error(exc: BaseException, secret: str) -> None:
    for node in _exception_chain_nodes(exc):
        assert secret not in str(node)
        assert secret not in repr(node)
        if isinstance(node, DataContractError):
            assert secret not in node.message
            assert secret not in str(node.details)
            assert secret not in repr(node.details)
            for value in node.details.values():
                assert secret not in repr(value)
                assert secret not in str(value)


def _exception_chain_nodes(exc: BaseException) -> list[BaseException]:
    nodes: list[BaseException] = []
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        nodes.append(current)
        if current.__cause__ is not None:
            stack.append(current.__cause__)
        if current.__context__ is not None and not current.__suppress_context__:
            stack.append(current.__context__)
    return nodes


def test_cache_entry_matrix() -> None:
    naive = datetime(2026, 7, 16, 12, 0, 0)
    cases: tuple[tuple[str, dict[str, object], str | None, str | None], ...] = (
        ("valid", {}, None, None),
        (
            "naive_as_of",
            {"as_of": naive},
            "timezone-aware ISO 8601 datetime",
            "as_of",
        ),
        (
            "naive_fetched_at",
            {"fetched_at": naive},
            "timezone-aware ISO 8601 datetime",
            "fetched_at",
        ),
        (
            "naive_expires_at",
            {"expires_at": naive},
            "timezone-aware ISO 8601 datetime",
            "expires_at",
        ),
        (
            "expires_before_fetched",
            {"expires_at": FETCHED - timedelta(seconds=1)},
            "expires_at must be >= fetched_at",
            "expires_at",
        ),
        ("empty_key", {"key": "   "}, "key must be a non-empty string", "key"),
        ("allow_null_instrument", {"instrument_id": None}, None, None),
        (
            "invalid_payload_json",
            {"payload_json": 1},
            "payload_json must be a string",
            "payload_json",
        ),
    )

    for _label, overrides, message_hint, field in cases:
        if message_hint is None:
            entry = _cache_entry(**overrides)
            if "instrument_id" in overrides:
                assert entry.instrument_id is overrides.get("instrument_id")
            else:
                assert entry.instrument_id == "equity:US:NVDA"
            assert entry.expires_at >= entry.fetched_at
        else:
            with pytest.raises(DataContractError) as exc_info:
                _cache_entry(**overrides)
            exc = exc_info.value
            assert message_hint is not None and message_hint in exc.message
            if field:
                assert exc.details.get("field") == field


def test_cache_entry_rejects_invalid_instrument_id_without_echo() -> None:
    cases = (
        ("foo|bar",),
        ("equity:US:NVDA?token=secret",),
        ("api_key=test-secret-alphabet",),
        ("not-an-id",),
        ("equity:A_SHARE:600519.SH",),
    )
    for (bad_id,) in cases:
        market = Market.US
        with pytest.raises(DataContractError) as exc_info:
            _cache_entry(instrument_id=bad_id, market=market)
        exc = exc_info.value
        assert exc.details.get("field") == "instrument_id"
        if bad_id:
            assert bad_id not in exc.message
            assert bad_id not in repr(exc)
            assert bad_id not in str(exc.details)
        if bad_id.startswith("equity:A_SHARE"):
            assert exc.details.get("parsed_market") == "A_SHARE"
            assert exc.details.get("market") == "US"


def test_health_snapshot_matrix() -> None:
    valid = ProviderHealthSnapshot(
        vendor=VendorId.MOCK_US,
        category=DataCategory.MARKET_SNAPSHOT,
        state=HealthState.OK,
        success_count=0,
        failure_count=0,
        last_success_at=None,
        last_failure_at=None,
        last_error_code=None,
        circuit_state=CircuitState.CLOSED,
    )
    assert valid.success_count == 0

    cases: tuple[tuple[str, dict[str, object], str], ...] = (
        (
            "negative_success",
            {"success_count": -1, "state": HealthState.ERROR},
            "success_count",
        ),
        (
            "negative_failure",
            {"failure_count": -1, "state": HealthState.ERROR},
            "failure_count",
        ),
        (
            "naive_last_success",
            {"last_success_at": datetime(2026, 7, 16, 12, 0, 0)},
            "last_success_at",
        ),
        (
            "naive_last_failure",
            {"last_failure_at": datetime(2026, 7, 16, 12, 0, 0)},
            "last_failure_at",
        ),
    )

    for _label, overrides, field in cases:
        with pytest.raises(DataContractError) as exc_info:
            base_kwargs = {
                "vendor": VendorId.MOCK_US,
                "category": DataCategory.MARKET_SNAPSHOT,
                "state": HealthState.ERROR,
                "success_count": 1,
                "failure_count": 1,
                "last_success_at": None,
                "last_failure_at": None,
                "last_error_code": "X",
                "circuit_state": CircuitState.CLOSED,
            }
            base_kwargs.update(overrides)
            ProviderHealthSnapshot(**base_kwargs)
        assert exc_info.value.details.get("field") == field


def test_health_snapshot_error_code_matrix() -> None:
    valid_codes: tuple[str, ...] = ("X", "E", "PROVIDER_TIMEOUT_ERROR", "A" + "B" * 127)
    for code in valid_codes:
        snap = ProviderHealthSnapshot(
            vendor=VendorId.MOCK_US,
            category=DataCategory.MARKET_SNAPSHOT,
            state=HealthState.ERROR,
            success_count=0,
            failure_count=1,
            last_success_at=None,
            last_failure_at=AS_OF,
            last_error_code=code,
            circuit_state=CircuitState.CLOSED,
        )
        assert snap.last_error_code == code

    bad_codes = (
        "",
        "lower_case",
        "Has-Dash",
        "api_key=test-secret-value",
        "1STARTS_WITH_DIGIT",
        "A" * 129,
    )
    for bad_code in bad_codes:
        with pytest.raises(DataContractError) as exc_info:
            ProviderHealthSnapshot(
                vendor=VendorId.MOCK_US,
                category=DataCategory.MARKET_SNAPSHOT,
                state=HealthState.ERROR,
                success_count=0,
                failure_count=1,
                last_success_at=None,
                last_failure_at=AS_OF,
                last_error_code=bad_code,
                circuit_state=CircuitState.CLOSED,
            )
        err = exc_info.value
        assert err.details.get("field") == "error_code"
        if bad_code:
            assert bad_code not in err.message
            assert bad_code not in str(err.details)
            assert bad_code not in repr(err)
        assert "test-secret-value" not in err.message
        assert "api_key=" not in err.message


def test_rate_limit_snapshot_matrix() -> None:
    ProviderRateLimitSnapshot(
        vendor=VendorId.MOCK_US,
        category=DataCategory.MARKET_QUOTE,
        window_start=AS_OF,
        window_seconds=1,
        request_count=0,
        limit_count=1,
        updated_at=FETCHED,
    )
    with pytest.raises(DataContractError) as exc_info:
        ProviderRateLimitSnapshot(
            vendor=VendorId.MOCK_US,
            category=DataCategory.MARKET_QUOTE,
            window_start=AS_OF,
            window_seconds=0,
            request_count=0,
            limit_count=1,
            updated_at=FETCHED,
        )
    assert exc_info.value.details.get("field") == "window_seconds"

    with pytest.raises(DataContractError) as exc_info:
        ProviderRateLimitSnapshot(
            vendor=VendorId.MOCK_US,
            category=DataCategory.MARKET_QUOTE,
            window_start=AS_OF,
            window_seconds=1,
            request_count=0,
            limit_count=0,
            updated_at=FETCHED,
        )
    assert exc_info.value.details.get("field") == "limit_count"

    with pytest.raises(DataContractError) as exc_info:
        ProviderRateLimitSnapshot(
            vendor=VendorId.MOCK_US,
            category=DataCategory.MARKET_QUOTE,
            window_start=AS_OF,
            window_seconds=1,
            request_count=-1,
            limit_count=10,
            updated_at=FETCHED,
        )
    assert exc_info.value.details.get("field") == "request_count"


def test_build_cache_key_matrix_and_roundtrip() -> None:
    key = build_cache_key(
        Market.US,
        DataCategory.MARKET_QUOTE,
        "equity:US:NVDA",
        AS_OF,
        GOOD_FP,
    )
    assert key == GOOD_KEY

    null_key = build_cache_key(
        Market.A_SHARE,
        DataCategory.INSTRUMENT_MASTER,
        None,
        AS_OF,
        "resolve|deadbeefcafebabe",
    )
    assert "|-|" in null_key

    with pytest.raises(DataContractError) as exc_info:
        build_cache_key(
            Market.US,
            DataCategory.MARKET_QUOTE,
            "equity:US:NVDA",
            datetime(2026, 7, 16, 12, 0, 0),
            GOOD_FP,
        )
    assert "timezone-aware" in exc_info.value.message

    for _label, fp in (
        ("base", "get_quote|a1b2c3d4e5f67890"),
        ("v2", "op.name:v2|ffffffffffffffff"),
        ("underscore_hash", "x" + ("_" * 127) + "|0123456789abcdef"),
    ):
        normalized = build_cache_key(
            Market.US,
            DataCategory.MARKET_QUOTE,
            "equity:US:NVDA",
            AS_OF,
            fp,
        )
        assert normalized.endswith(f"|{fp}")
        assert require_valid_fingerprint(fp) == fp
        parsed = parse_cache_key(normalized)
        assert parsed.fingerprint == fp

    for _label, fp in (
        ("space", "has space"),
        ("query", "query?token=secret"),
        ("empty", ""),
        ("too_long", "x" * 257),
        ("secret", "api_key=test-secret-alphabet"),
        ("free_text", "free_text_no_pipe"),
        ("upper_hash", "get_quote|A1B2C3D4E5F67890"),
        ("short_hash", "get_quote|a1b2c3d4e5f6789"),
        ("missing_hash", "get_quote|"),
        ("extra_pipe", "get_quote|a1b2c3d4e5f67890|extra"),
        ("op_starts_digit", "1starts_digit|a1b2c3d4e5f67890"),
        ("op_space", "has space|a1b2c3d4e5f67890"),
    ):
        with pytest.raises(DataContractError) as exc_info:
            build_cache_key(Market.US, DataCategory.MARKET_QUOTE, "equity:US:NVDA", AS_OF, fp)
        err = exc_info.value
        assert err.details.get("field") == "fingerprint"
        if fp:
            assert fp not in err.message
            assert fp not in str(err.details)
            assert fp not in repr(err)
        assert "test-secret-alphabet" not in err.message
        assert "api_key=" not in err.message
        assert "token=secret" not in err.message

    with pytest.raises(DataContractError) as exc_info:
        build_cache_key(
            Market.US,
            DataCategory.MARKET_QUOTE,
            "equity:US:NVDA?token=sk-secret",
            AS_OF,
            GOOD_FP,
        )
    err = exc_info.value
    assert err.details.get("field") == "instrument_id"
    assert "sk-secret" not in err.message

    with pytest.raises(DataContractError) as exc_info:
        build_cache_key(
            Market.US,
            DataCategory.MARKET_QUOTE,
            "equity:A_SHARE:600519.SH",
            AS_OF,
            GOOD_FP,
        )
    assert exc_info.value.details.get("field") == "instrument_id"


def test_parse_cache_key_matrix() -> None:
    key = build_cache_key(Market.US, DataCategory.MARKET_QUOTE, "equity:US:NVDA", AS_OF, GOOD_FP)
    parsed = parse_cache_key(key)
    assert parsed.market is Market.US
    assert parsed.category is DataCategory.MARKET_QUOTE
    assert parsed.instrument_id == "equity:US:NVDA"
    assert parsed.as_of == AS_OF
    assert parsed.fingerprint == GOOD_FP

    parsed_null = parse_cache_key(
        build_cache_key(
            Market.A_SHARE,
            DataCategory.INSTRUMENT_MASTER,
            None,
            AS_OF,
            GOOD_FP,
        )
    )
    assert parsed_null.instrument_id is None

    for _label, key in (
        ("junk", "arbitrary-not-a-key"),
        (
            "bad_version",
            "v2|US|market_quote|equity:US:NVDA|2026-07-16T12:00:00+00:00|get_quote|a1b2c3d4e5f67890",
        ),
        (
            "bad_fp_case",
            "v1|US|market_quote|equity:US:NVDA|2026-07-16T12:00:00+00:00|get_quote|A1B2C3D4E5F67890",
        ),
        (
            "bad_secret",
            "v1|US|market_quote|equity:US:NVDA|2026-07-16T12:00:00+00:00|get_quote|sk-SECRET",
        ),
        (
            "bad_market",
            "v1|UNKNOWN|market_quote|equity:US:NVDA|2026-07-16T12:00:00+00:00|get_quote|a1b2c3d4e5f67890",
        ),
    ):
        with pytest.raises(DataContractError) as exc_info:
            parse_cache_key(key)
        err = exc_info.value
        assert err.details.get("field") == "key"
        if key:
            assert key not in err.message
            assert key not in str(err.details)
            assert key not in repr(err)
        assert "api_key=test-secret-alphabet" not in err.message

    for secret in (
        "v1|test-secret-market|market_quote|-|2026-07-16T12:00:00+00:00|get_quote|a1b2c3d4e5f67890",
        "v1|US|market_quote|equity:US:NVDA?token=test-secret|2026-07-16T12:00:00+00:00|get_quote|a1b2c3d4e5f67890",
        "v1|US|test-secret-category|equity:US:NVDA|2026-07-16T12:00:00+00:00|get_quote|a1b2c3d4e5f67890",
        "v1|US|market_quote|equity:US:NVDA|2026-13-99T99:99:99+00:00|get_quote|a1b2c3d4e5f67890",
        "v1|US|market_quote|equity:US:NVDA|2026-07-16T12:00:00+00:00|get_quote|test-secret-fp",
    ):
        with pytest.raises(DataContractError) as exc_info:
            parse_cache_key(secret)
        err = exc_info.value
        assert err.message == "cache key must be a valid v1 key"
        assert "test-secret" not in str(err)
        assert "test-secret" not in repr(err)
        assert "test-secret" not in str(err.details)
        assert err.__cause__ is None
        assert err.__context__ is None or err.__suppress_context__
        _assert_no_secret_in_error(err, "test-secret")


def test_require_cache_key_matches_entry_matrix() -> None:
    entry = _cache_entry()
    parsed = require_cache_key_matches_entry(entry.key, entry)
    assert parsed.market is entry.market
    assert parsed.as_of == entry.as_of

    mismatch_cases: tuple[tuple[str, CacheEntry], ...] = (
        (
            "key_mismatch",
            _cache_entry(
                key=build_cache_key(
                    Market.US,
                    DataCategory.MARKET_QUOTE,
                    entry.instrument_id,
                    AS_OF + timedelta(seconds=1),
                    GOOD_FP,
                )
            ),
        ),
        (
            "field_category",
            _mismatched_entry(
                key=entry.key,
                category=DataCategory.MARKET_OHLCV,
            ),
        ),
        (
            "field_instrument",
            _mismatched_entry(
                key=entry.key,
                instrument_id="equity:US:AAPL",
            ),
        ),
        (
            "field_as_of",
            _mismatched_entry(
                key=entry.key,
                as_of=AS_OF + timedelta(seconds=1),
            ),
        ),
        (
            "field_market",
            _mismatched_entry(
                key=entry.key,
                market=Market.A_SHARE,
                instrument_id="equity:A_SHARE:600519.SH",
            ),
        ),
    )

    for _label, mismatch_entry in mismatch_cases:
        validation_target = entry if _label == "key_mismatch" else mismatch_entry
        with pytest.raises(DataContractError) as exc_info:
            require_cache_key_matches_entry(mismatch_entry.key, validation_target)
        if mismatch_entry.key != validation_target.key:
            assert "key must match entry.key" in exc_info.value.message
        else:
            assert "cache key fields must match" in exc_info.value.message
        assert mismatch_entry.key not in exc_info.value.message


def test_cache_entry_and_parsed_key_are_immutable() -> None:
    entry = _cache_entry()
    parsed = parse_cache_key(entry.key)

    with pytest.raises(FrozenInstanceError):
        entry.key = "x"
    with pytest.raises(FrozenInstanceError):
        parsed.market = Market.A_SHARE


def test_cache_ttl_for_frozen_categories() -> None:
    settings = _settings()
    special = {
        DataCategory.INSTRUMENT_MASTER: 86400,
        DataCategory.MARKET_QUOTE: 5,
        DataCategory.MARKET_SNAPSHOT: 30,
        DataCategory.MARKET_STRUCTURE: 5,
        DataCategory.MARKET_BREADTH: 900,
        DataCategory.CAPITAL: 30,
        DataCategory.LIMIT_UP: 30,
        DataCategory.OPTIONS: 15,
        DataCategory.SENTIMENT: 30,
        DataCategory.RESEARCH_REPORTS: 3600,
        DataCategory.CORPORATE_ACTIONS: 21600,
        DataCategory.ANNOUNCEMENTS: 300,
        DataCategory.FUNDAMENTALS: 21600,
        DataCategory.FINANCIAL_STATEMENTS: 21600,
        DataCategory.FILINGS: 3600,
        DataCategory.INSIDER_ACTIVITY: 3600,
    }
    for category in DataCategory:
        expected = special.get(category, 300)
        assert expected > 0
        assert settings.cache_ttl_for(category) == expected


def test_cache_ttl_for_respects_overrides() -> None:
    settings = _settings(
        cache_ttl_market_quote_seconds=7,
        cache_ttl_news_seconds=42,
        cache_ttl_default_seconds=99,
    )
    assert settings.cache_ttl_for(DataCategory.MARKET_QUOTE) == 7
    assert settings.cache_ttl_for(DataCategory.NEWS) == 42
    assert settings.cache_ttl_for(DataCategory.MACRO) == 99


def test_enable_provider_cache_default_true() -> None:
    settings = _settings()
    assert settings.enable_provider_cache is True
