"""Phase 1D D4: VendorChainConfig port and YamlVendorChainConfig loader tests."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import pytest

from application.ports import VendorChainConfig
from domain.common.enums import AppEnvironment, DataCategory, LogLevel, Market, VendorId
from domain.common.errors import ConfigurationError
from infrastructure.config import settings as settings_module
from infrastructure.config.settings import PROJECT_ROOT, AppSettings
from infrastructure.config.vendor_chain import YamlVendorChainConfig

DEFAULT_YAML = PROJECT_ROOT / "config" / "vendor_chains.default.yaml"

A_SHARE_EXPECTED: dict[DataCategory, tuple[VendorId, ...]] = {
    DataCategory.INSTRUMENT_MASTER: (VendorId.LOCAL_MASTER, VendorId.SEED_FIXTURE),
    DataCategory.MARKET_QUOTE: (VendorId.TENCENT, VendorId.EASTMONEY),
    DataCategory.MARKET_OHLCV: (VendorId.TENCENT, VendorId.EASTMONEY),
    DataCategory.MARKET_STRUCTURE: (VendorId.EASTMONEY,),
    DataCategory.FUNDAMENTALS: (VendorId.EASTMONEY,),
    DataCategory.FINANCIAL_STATEMENTS: (VendorId.SINA, VendorId.EASTMONEY),
    DataCategory.ANNOUNCEMENTS: (VendorId.CNINFO, VendorId.SZSE, VendorId.SSE),
    DataCategory.CORPORATE_ACTIONS: (VendorId.EASTMONEY,),
    DataCategory.RESEARCH_REPORTS: (
        VendorId.EASTMONEY,
        VendorId.THS,
        VendorId.IWENCAI,
    ),
    DataCategory.CAPITAL: (
        VendorId.EASTMONEY,
        VendorId.SINA,
        VendorId.HKEX,
        VendorId.SSE,
        VendorId.SZSE,
    ),
    DataCategory.LIMIT_UP: (VendorId.EASTMONEY, VendorId.THS),
    DataCategory.OPTIONS: (VendorId.SINA,),
    DataCategory.INTERACTIVE_QA: (VendorId.CNINFO,),
    DataCategory.NEWS: (VendorId.CLS, VendorId.EASTMONEY),
    DataCategory.SENTIMENT: (VendorId.THS, VendorId.EASTMONEY),
    DataCategory.INDUSTRY_CYCLE: (VendorId.NAHS,),
    DataCategory.COMPANY_OPERATING_METRICS: (VendorId.CNINFO,),
}

US_EXPECTED: dict[DataCategory, tuple[VendorId, ...]] = {
    DataCategory.INSTRUMENT_MASTER: (VendorId.LOCAL_MASTER, VendorId.SEED_FIXTURE),
    DataCategory.MARKET_QUOTE: (VendorId.YFINANCE, VendorId.ALPHA_VANTAGE),
    DataCategory.MARKET_OHLCV: (VendorId.YFINANCE, VendorId.ALPHA_VANTAGE),
    DataCategory.MARKET_BREADTH: (VendorId.YFINANCE,),
    DataCategory.COMMUNITY_HEAT: (VendorId.MOOMOO,),
    # Phase 1G research chains.
    DataCategory.FUNDAMENTALS: (
        VendorId.YFINANCE,
        VendorId.ALPHA_VANTAGE,
        VendorId.SEC_EDGAR,
    ),
    DataCategory.FINANCIAL_STATEMENTS: (
        VendorId.SEC_EDGAR,
        VendorId.YFINANCE,
        VendorId.ALPHA_VANTAGE,
    ),
    DataCategory.FILINGS: (VendorId.SEC_EDGAR,),
    DataCategory.CORPORATE_ACTIONS: (VendorId.YFINANCE, VendorId.SEC_EDGAR),
    DataCategory.INSIDER_ACTIVITY: (VendorId.SEC_EDGAR, VendorId.ALPHA_VANTAGE),
    DataCategory.NEWS: (VendorId.YFINANCE, VendorId.ALPHA_VANTAGE),
    DataCategory.MACRO: (VendorId.FRED,),
    DataCategory.SENTIMENT: (
        VendorId.MOOMOO_FEED,
        VendorId.REDDIT,
    ),
    DataCategory.PREDICTION_MARKET: (VendorId.POLYMARKET,),
    DataCategory.ACCOUNT: (
        VendorId.SCHWAB,
        VendorId.MOOMOO,
        VendorId.MANUAL_CSV,
    ),
}


def _write_yaml(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def _minimal_valid(*, a_share_extra: str = "", us_extra: str = "") -> str:
    return f"""
version: 1
markets:
  A_SHARE:
    market_snapshot:
      vendors: [mock_a_share]
{a_share_extra}
  US:
    market_snapshot:
      vendors: [mock_us]
{us_extra}
"""


def test_default_yaml_file_is_available_and_contract_export_is_consistent() -> None:
    assert DEFAULT_YAML.is_file()
    config: VendorChainConfig = YamlVendorChainConfig.load(DEFAULT_YAML)

    for market, expected in ((Market.A_SHARE, A_SHARE_EXPECTED), (Market.US, US_EXPECTED)):
        loaded = dict(config.all_categories(market))
        assert loaded == expected
        for category, chain in expected.items():
            assert config.chain_for(market, category) == chain

    assert config.chain_for(Market.A_SHARE, DataCategory.MARKET_SNAPSHOT) == ()
    assert config.chain_for(Market.US, DataCategory.MARKET_SNAPSHOT) == ()
    assert config.chain_for(Market.OTC, DataCategory.MARKET_QUOTE) == (
        VendorId.DUKASCOPY,
    )
    assert config.chain_for(Market.OTC, DataCategory.MARKET_OHLCV) == (
        VendorId.DUKASCOPY,
    )
    assert config.chain_for(Market.CME, DataCategory.MARKET_QUOTE) == (
        VendorId.YFINANCE,
    )
    assert config.chain_for(Market.CME, DataCategory.MARKET_OHLCV) == (
        VendorId.YFINANCE,
    )
    assert config.chain_for(Market.CME, DataCategory.FUTURES_REFERENCE) == (
        VendorId.CME_PUBLIC,
    )
    assert config.chain_for(Market.CME, DataCategory.FUTURES_STATISTICS) == (
        VendorId.CME_PUBLIC,
    )
    assert config.chain_for(Market.DCE, DataCategory.FUTURES_REFERENCE) == (
        VendorId.DCE_OFFICIAL,
    )
    assert config.chain_for(Market.DCE, DataCategory.FUTURES_STATISTICS) == (
        VendorId.DCE_OFFICIAL,
    )


def test_default_vendor_sets_respect_phase_boundaries() -> None:
    config = YamlVendorChainConfig.load(DEFAULT_YAML)

    for chain in config.all_categories(Market.A_SHARE).values():
        assert set(chain) <= set(VendorId)
        assert VendorId.A_SHARE_FALLBACK not in chain

    allowed_us = {
        VendorId.MOCK_US,
        VendorId.NULL,
        VendorId.LOCAL_MASTER,
        VendorId.SEED_FIXTURE,
        VendorId.YFINANCE,
        VendorId.ALPHA_VANTAGE,
        VendorId.SEC_EDGAR,
        VendorId.FRED,
        VendorId.MOOMOO_FEED,
        VendorId.REDDIT,
        VendorId.POLYMARKET,
        VendorId.SCHWAB,
        VendorId.MOOMOO,
        VendorId.MANUAL_CSV,
    }
    for chain in config.all_categories(Market.US).values():
        assert set(chain) <= allowed_us
        assert VendorId.EASTMONEY not in chain


def test_yaml_none_null_and_quoted_null_are_equivalent(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path / "nulls.yaml",
        """
version: 1
markets:
  A_SHARE:
    fundamentals:
      vendors: [null]
    news:
      vendors: ["null"]
  US:
    fundamentals:
      vendors: [null]
    filings:
      vendors:
        - "null"
""",
    )
    config = YamlVendorChainConfig.load(path)

    assert config.chain_for(Market.A_SHARE, DataCategory.FUNDAMENTALS) == (VendorId.NULL,)
    assert config.chain_for(Market.A_SHARE, DataCategory.NEWS) == (VendorId.NULL,)
    assert config.chain_for(Market.US, DataCategory.FUNDAMENTALS) == (VendorId.NULL,)
    assert config.chain_for(Market.US, DataCategory.FILINGS) == (VendorId.NULL,)


def test_empty_vendors_and_missing_category_are_handled_as_empty_plus_immutability(
    tmp_path: Path,
) -> None:
    config = YamlVendorChainConfig.load(
        _write_yaml(
            tmp_path / "empty.yaml",
            _minimal_valid(a_share_extra="    sentiment:\n      vendors: []\n"),
        ),
    )

    assert config.chain_for(Market.A_SHARE, DataCategory.SENTIMENT) == ()
    assert config.all_categories(Market.A_SHARE)[DataCategory.SENTIMENT] == ()
    assert config.chain_for(Market.US, DataCategory.ACCOUNT) == ()
    assert config.chain_for(Market.A_SHARE, DataCategory.LIMIT_UP) == ()

    # HEAD: undeclared legal categories return empty chain (default file).
    default = YamlVendorChainConfig.load(DEFAULT_YAML)
    assert default.chain_for(Market.A_SHARE, DataCategory.ACCOUNT) == ()
    assert default.chain_for(Market.A_SHARE, DataCategory.FILINGS) == ()
    assert default.chain_for(Market.A_SHARE, DataCategory.MACRO) == ()
    assert default.chain_for(Market.US, DataCategory.CAPITAL) == ()
    assert default.chain_for(Market.US, DataCategory.LIMIT_UP) == ()
    assert default.chain_for(Market.US, DataCategory.OPTIONS) == ()
    assert default.chain_for(Market.US, DataCategory.ANNOUNCEMENTS) == ()
    assert default.chain_for(Market.US, DataCategory.MARKET_STRUCTURE) == ()
    assert default.chain_for(Market.US, DataCategory.RESEARCH_REPORTS) == ()
    assert default.chain_for(Market.A_SHARE, DataCategory.INSIDER_ACTIVITY) == ()

    view = default.all_categories(Market.A_SHARE)
    assert isinstance(view, Mapping)
    assert isinstance(view, MappingProxyType)
    with pytest.raises(TypeError):
        view[DataCategory.NEWS] = (VendorId.NULL,)  # type: ignore[index]
    with pytest.raises(TypeError):
        del view[DataCategory.NEWS]  # type: ignore[attr-defined]
    # Only explicitly configured categories; not the full DataCategory enum.
    assert DataCategory.ACCOUNT not in view
    assert DataCategory.FILINGS not in view
    assert set(view) == set(A_SHARE_EXPECTED)


def test_invalid_vendor_chain_payloads_are_rejected(tmp_path: Path) -> None:
    test_cases: tuple[tuple[str, str, str, dict[str, object]], ...] = (
        (
            "unknown_root_key",
            """
version: 1
markets:
  A_SHARE:
    market_snapshot:
      vendors: [mock_a_share]
  US:
    market_snapshot:
      vendors: [mock_us]
extra: true
""",
            "invalid_root_keys",
            {"unknown_key_count": 1},
        ),
        (
            "unknown_market",
            """
version: 1
markets:
  A_SHARE:
    market_snapshot:
      vendors: [mock_a_share]
  US:
    market_snapshot:
      vendors: [mock_us]
  HK:
    market_snapshot:
      vendors: [mock_us]
""",
            "unknown_market",
            {},
        ),
        (
            "missing_required_markets",
            """
version: 1
markets:
  A_SHARE:
    market_snapshot:
      vendors: [mock_a_share]
""",
            "missing_required_markets",
            {},
        ),
        (
            "unknown_category",
            _minimal_valid(a_share_extra="    not_a_category:\n      vendors: [null]\n"),
            "unknown_category",
            {"market": Market.A_SHARE.value},
        ),
        (
            "vendors_not_list",
            """
version: 1
markets:
  A_SHARE:
    market_snapshot:
      vendors: not_a_list
  US:
    market_snapshot:
      vendors: [mock_us]
""",
            "vendors_not_list",
            {},
        ),
        (
            "invalid_version_type",
            """
version: "1"
markets:
  A_SHARE:
    market_snapshot:
      vendors: [mock_a_share]
  US:
    market_snapshot:
      vendors: [mock_us]
""",
            "invalid_version_type",
            {},
        ),
        (
            "root_not_mapping",
            "- just\n- a\n- list\n",
            "root_not_mapping",
            {},
        ),
        (
            "invalid_vendor_type",
            """
version: 1
markets:
  A_SHARE:
    market_snapshot:
      vendors: [1]
  US:
    market_snapshot:
      vendors: [mock_us]
""",
            "invalid_vendor_type",
            {},
        ),
        (
            "duplicate_vendor",
            """
version: 1
markets:
  A_SHARE:
    market_snapshot:
      vendors: [mock_a_share, mock_a_share]
  US:
    market_snapshot:
      vendors: [mock_us]
""",
            "duplicate_vendor",
            {"vendor": VendorId.MOCK_A_SHARE.value},
        ),
        (
            "invalid_category_object_keys",
            """
version: 1
markets:
  A_SHARE:
    market_snapshot:
      vendors: [mock_a_share]
      timeout: 1
  US:
    market_snapshot:
      vendors: [mock_us]
""",
            "invalid_category_object_keys",
            {"unknown_key_count": 1},
        ),
        (
            "unsupported_version",
            """
version: 2
markets:
  A_SHARE:
    market_snapshot:
      vendors: [mock_a_share]
  US:
    market_snapshot:
      vendors: [mock_us]
""",
            "unsupported_version",
            {"expected_version": 1, "version_type": "int"},
        ),
        (
            "unknown_vendor",
            """
version: 1
markets:
  A_SHARE:
    market_snapshot:
      vendors: [not_a_real_vendor]
  US:
    market_snapshot:
      vendors: [mock_us]
""",
            "unknown_vendor",
            {
                "index": 0,
                "market": Market.A_SHARE.value,
                "category": DataCategory.MARKET_SNAPSHOT.value,
            },
        ),
    )

    for label, body, reason, expected in test_cases:
        with pytest.raises(ConfigurationError) as exc_info:
            YamlVendorChainConfig.load(_write_yaml(tmp_path / f"{label}.yaml", body))
        details = exc_info.value.details
        assert details["reason"] == reason, label
        for key, expected_value in expected.items():
            assert details[key] == expected_value, label

        # Unknown free-form vendor strings must not surface (known enum context only).
        if label == "unknown_vendor":
            err = exc_info.value
            assert "vendor" not in details
            assert "not_a_real_vendor" not in err.message
            assert "not_a_real_vendor" not in repr(details)
            assert "not_a_real_vendor" not in str(details)
            assert "not_a_real_vendor" not in repr(err)


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


def _assert_secret_absent(exc: BaseException, secret: str) -> None:
    for node in _exception_chain_nodes(exc):
        assert secret not in str(node)
        assert secret not in repr(node)
        if isinstance(node, ConfigurationError):
            assert secret not in node.message
            assert secret not in repr(node.details)
            assert secret not in str(node.details)
            for value in node.details.values():
                assert secret not in repr(value)
                assert secret not in str(value)


def test_configuration_errors_do_not_leak_secret_like_values(tmp_path: Path) -> None:
    test_cases: tuple[tuple[str, str, str], ...] = (
        (
            "SUPER_SECRET_API_KEY_root",
            """
version: 1
markets:
  A_SHARE:
    market_snapshot:
      vendors: [mock_a_share]
  US:
    market_snapshot:
      vendors: [mock_us]
SUPER_SECRET_API_KEY_root: true
""",
            "invalid_root_keys",
        ),
        (
            "SUPER_SECRET_API_KEY_market",
            """
version: 1
markets:
  A_SHARE:
    market_snapshot:
      vendors: [mock_a_share]
  US:
    market_snapshot:
      vendors: [mock_us]
  SUPER_SECRET_API_KEY_market:
    market_snapshot:
      vendors: [mock_us]
""",
            "unknown_market",
        ),
        (
            "SUPER_SECRET_API_KEY_category",
            _minimal_valid(
                a_share_extra="    SUPER_SECRET_API_KEY_category:\n      vendors: [null]\n",
            ),
            "unknown_category",
        ),
        (
            "SUPER_SECRET_API_KEY_vendor",
            """
version: 1
markets:
  A_SHARE:
    market_snapshot:
      vendors: [SUPER_SECRET_API_KEY_vendor]
  US:
    market_snapshot:
      vendors: [mock_us]
""",
            "unknown_vendor",
        ),
        (
            "SUPER_SECRET_API_KEY_objkey",
            """
version: 1
markets:
  A_SHARE:
    market_snapshot:
      vendors: [mock_a_share]
      SUPER_SECRET_API_KEY_objkey: 1
  US:
    market_snapshot:
      vendors: [mock_us]
""",
            "invalid_category_object_keys",
        ),
        (
            "SUPER_SECRET_API_KEY_yaml_parse",
            """
version: 1
markets: [
api_key: SUPER_SECRET_API_KEY_yaml_parse
  broken: [not: valid: yaml: [[[
""",
            "malformed_yaml",
        ),
        (
            "SUPER_SECRET_API_KEY_yaml_comment",
            """
version: 1
markets:
  A_SHARE:
    market_snapshot:
      vendors: [unknown_vendor_trigger]
  US:
    market_snapshot:
      vendors: [mock_us]
# comment containing SUPER_SECRET_API_KEY_yaml_comment
""",
            "unknown_vendor",
        ),
        (
            "991234567890123456789012345678901234567890",
            """
version: 991234567890123456789012345678901234567890
markets:
  A_SHARE:
    market_snapshot:
      vendors: [mock_a_share]
  US:
    market_snapshot:
      vendors: [mock_us]
""",
            "unsupported_version",
        ),
    )

    for secret, body, expected_reason in test_cases:
        with pytest.raises(ConfigurationError) as exc_info:
            YamlVendorChainConfig.load(_write_yaml(tmp_path / "secret.yaml", body))
        assert exc_info.value.details["reason"] == expected_reason, secret
        # message / details / exception chain must not leak the secret token
        _assert_secret_absent(exc_info.value, secret)

        if expected_reason == "malformed_yaml":
            assert exc_info.value.__cause__ is None
            assert exc_info.value.__context__ is None or exc_info.value.__suppress_context__

        if secret == "SUPER_SECRET_API_KEY_yaml_comment":
            # Raw YAML comment text must not appear either.
            assert "comment containing" not in exc_info.value.message
            assert "comment containing" not in repr(exc_info.value.details)
            _assert_secret_absent(exc_info.value, "SUPER_SECRET")


def test_malformed_yaml_reports_configuration_error_without_parser_leak(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        YamlVendorChainConfig.load(
            _write_yaml(
                tmp_path / "bad.yaml",
                "version: 1\nmarkets: [\n  this is : not: valid: yaml: [[[\n",
            )
        )
    assert exc_info.value.details["reason"] == "malformed_yaml"
    assert "this is" not in exc_info.value.message
    assert "[[[" not in exc_info.value.message
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None or exc_info.value.__suppress_context__


def test_missing_file_reports_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        YamlVendorChainConfig.load(tmp_path / "does_not_exist.yaml")
    assert exc_info.value.details["reason"] == "file_not_found"


def _settings(**overrides: object) -> AppSettings:
    base: dict[str, object] = {
        "app_name": "tp",
        "app_env": AppEnvironment.TEST,
        "log_level": LogLevel.INFO,
        "database_url": "sqlite:////tmp/tp-vendor-chain-test.db",
        "mcp_server_name": "tp",
        "default_timezone": "UTC",
        "provider_timeout_seconds": 1.0,
    }
    base.update(overrides)
    return AppSettings(_env_file=None, **base)  # type: ignore[call-arg]


def test_settings_path_resolution_and_env_loading_redaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(database_url=f"sqlite:///{tmp_path / 'x.db'}")
    expected_default = (PROJECT_ROOT / "config" / "vendor_chains.default.yaml").resolve()
    assert settings.vendor_chain_path == expected_default
    assert settings.vendor_chain_path.is_file()
    assert settings.vendor_chain_path.is_absolute()
    redacted_path = settings.redacted_dict()["vendor_chain_path"]
    assert redacted_path == str(settings.vendor_chain_path)
    # Non-secret path values stay visible; must not be masked as credentials.
    assert "***REDACTED***" not in str(redacted_path)
    assert settings.app_env is AppEnvironment.TEST
    assert settings.log_level is LogLevel.INFO

    # Explicit relative Path resolves against PROJECT_ROOT (not CWD).
    relative_settings = _settings(
        database_url=f"sqlite:///{tmp_path / 'x.db'}",
        vendor_chain_path=Path("config/vendor_chains.default.yaml"),
    )
    assert relative_settings.vendor_chain_path == expected_default
    assert relative_settings.vendor_chain_path.is_absolute()
    assert relative_settings.vendor_chain_path.is_file()

    # Constructor-supplied absolute path remains absolute (no re-rooting).
    absolute = (tmp_path / "custom_chains.yaml").resolve()
    absolute.write_text(_minimal_valid(), encoding="utf-8")
    absolute_settings = _settings(
        database_url=f"sqlite:///{tmp_path / 'x.db'}",
        vendor_chain_path=absolute,
    )
    assert absolute_settings.vendor_chain_path == absolute
    assert absolute_settings.vendor_chain_path.is_absolute()
    assert absolute_settings.redacted_dict()["vendor_chain_path"] == str(absolute)
    assert "***REDACTED***" not in str(absolute_settings.redacted_dict()["vendor_chain_path"])

    env_path = tmp_path / "test.env"
    env_path.write_text(
        "\n".join(
            [
                "APP_NAME=from-file",
                "APP_ENV=test",
                "LOG_LEVEL=INFO",
                f"DATABASE_URL=sqlite:///{tmp_path / 'db.sqlite'}",
                "MCP_SERVER_NAME=mcp-test",
                "DEFAULT_TIMEZONE=UTC",
                "PROVIDER_TIMEOUT_SECONDS=9",
                f"VENDOR_CHAIN_PATH={absolute}",
            ]
        ),
        encoding="utf-8",
    )
    for key in list(__import__("os").environ):
        if key in __import__("conftest").APP_SETTINGS_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)

    loaded_settings = AppSettings.load(env_file=env_path)
    assert loaded_settings.vendor_chain_path == absolute
    assert loaded_settings.vendor_chain_path.is_absolute()
    assert loaded_settings.app_env is AppEnvironment.TEST
    assert loaded_settings.log_level is LogLevel.INFO
    assert loaded_settings.redacted_dict()["vendor_chain_path"] == str(absolute)
    assert "***REDACTED***" not in str(loaded_settings.redacted_dict()["vendor_chain_path"])
    loaded_chain = YamlVendorChainConfig.load(loaded_settings.vendor_chain_path)
    assert loaded_chain.chain_for(Market.US, DataCategory.MARKET_SNAPSHOT) == (VendorId.MOCK_US,)


def test_settings_default_relative_falls_back_to_packaged_when_project_root_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_root = tmp_path / "installed_root"
    fake_root.mkdir()
    packaged = tmp_path / "packaged" / "vendor_chains.default.yaml"
    packaged.parent.mkdir()
    packaged.write_text(DEFAULT_YAML.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(settings_module, "PROJECT_ROOT", fake_root)
    monkeypatch.setattr(settings_module, "PACKAGED_VENDOR_CHAIN_PATH", packaged)

    settings = _settings(database_url=f"sqlite:///{tmp_path / 'x.db'}")
    assert settings.vendor_chain_path == packaged.resolve()

    settings_custom = _settings(
        database_url=f"sqlite:///{tmp_path / 'x.db'}",
        vendor_chain_path=Path("config/custom_chains.yaml"),
    )
    assert settings_custom.vendor_chain_path == (fake_root / "config/custom_chains.yaml").resolve()
    assert not settings_custom.vendor_chain_path.is_file()


def test_load_does_not_scan_directories(tmp_path: Path) -> None:
    good = tmp_path / "only.yaml"
    sibling = tmp_path / "vendor_chains.default.yaml"
    sibling.write_text("version: 99\nmarkets: {}\n", encoding="utf-8")
    good.write_text(_minimal_valid(), encoding="utf-8")
    config = YamlVendorChainConfig.load(good)
    assert config.chain_for(Market.US, DataCategory.MARKET_SNAPSHOT) == (VendorId.MOCK_US,)


def test_no_env_based_vendor_injection(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = YamlVendorChainConfig.load(DEFAULT_YAML)
    monkeypatch.setenv("VENDORS", "yfinance,broker")
    monkeypatch.setenv("VENDOR_CHAIN_OVERRIDE", "yfinance")
    config = YamlVendorChainConfig.load(DEFAULT_YAML)
    assert config.all_categories(Market.A_SHARE) == expected.all_categories(Market.A_SHARE)
    assert config.all_categories(Market.US) == expected.all_categories(Market.US)
