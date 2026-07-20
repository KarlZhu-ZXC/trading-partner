"""Phase 1E E2: official 2024–2026 A-share trading calendar."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from domain.common.errors import CalendarOutOfRange, ConfigurationError
from infrastructure.config.settings import (
    PACKAGED_A_SHARE_TRADING_CALENDAR_PATH,
    PROJECT_ROOT,
)
from infrastructure.providers.a_share import trading_calendar as calendar_module
from infrastructure.providers.a_share.trading_calendar import (
    JsonAShareTradingCalendar,
    load_default_a_share_trading_calendar,
)

FIXTURE = PROJECT_ROOT / "config" / "a_share_trading_calendar.v1.json"
GENERATOR = PROJECT_ROOT / "scripts" / "generate_a_share_trading_calendar.py"

OFFICIAL_URLS = [
    "https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20231226_5733941.shtml",
    "https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20241223_10767110.shtml",
    "https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20251222_10802510.shtml",
]


def test_fixture_exists_with_official_metadata() -> None:
    assert FIXTURE.is_file()
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "a_share_trading_calendar.v1"
    assert payload["version"] == "sse-official-2024-2026.v1"
    assert payload["timezone"] == "Asia/Shanghai"
    assert payload["coverage_from"] == "2024-01-01"
    assert payload["coverage_to"] == "2026-12-31"
    assert len(payload["open_days"]) == 727
    assert payload["regular_sessions"]
    assert payload["content_sha256"]
    assert payload["source"]["kind"] == "official_exchange_schedule"
    assert payload["source"]["urls"] == OFFICIAL_URLS
    assert payload["generated_at"]


def test_content_sha256_matches_loader_canonicalization() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    check = {k: v for k, v in payload.items() if k != "content_sha256"}
    canonical = json.dumps(check, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert digest == payload["content_sha256"]


def test_generator_script_exists_and_is_deterministic() -> None:
    assert GENERATOR.is_file()
    # Import as path execution would; load by exec of helper surface via subprocess-free import.
    import importlib.util

    spec = importlib.util.spec_from_file_location("generate_a_share_trading_calendar", GENERATOR)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    a = mod.build_payload()
    b = mod.build_payload()
    assert a == b
    assert a["content_sha256"] == b["content_sha256"]
    failures = mod.verify_payload(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert failures == []


def test_key_holiday_boundaries_all_three_years() -> None:
    cal = JsonAShareTradingCalendar.load(FIXTURE)
    # 2024
    assert cal.is_trading_day(date(2024, 1, 1)) is False  # New Year
    assert cal.is_trading_day(date(2024, 1, 2)) is True
    assert cal.is_trading_day(date(2024, 2, 8)) is True  # pre-CNY
    assert cal.is_trading_day(date(2024, 2, 9)) is False  # CNY start
    assert cal.is_trading_day(date(2024, 2, 16)) is False
    assert cal.is_trading_day(date(2024, 2, 19)) is True  # first open after CNY
    assert cal.is_trading_day(date(2024, 4, 4)) is False
    assert cal.is_trading_day(date(2024, 5, 1)) is False
    assert cal.is_trading_day(date(2024, 10, 1)) is False
    assert cal.is_trading_day(date(2024, 10, 7)) is False
    assert cal.is_trading_day(date(2024, 10, 8)) is True
    # 2025
    assert cal.is_trading_day(date(2025, 1, 1)) is False
    assert cal.is_trading_day(date(2025, 1, 27)) is True
    assert cal.is_trading_day(date(2025, 1, 28)) is False
    assert cal.is_trading_day(date(2025, 2, 4)) is False
    assert cal.is_trading_day(date(2025, 2, 5)) is True
    assert cal.is_trading_day(date(2025, 5, 1)) is False
    assert cal.is_trading_day(date(2025, 10, 8)) is False
    # 2026
    assert cal.is_trading_day(date(2026, 1, 1)) is False
    assert cal.is_trading_day(date(2026, 1, 2)) is False
    assert cal.is_trading_day(date(2026, 2, 16)) is False
    assert cal.is_trading_day(date(2026, 2, 24)) is True
    assert cal.is_trading_day(date(2026, 7, 17)) is True  # product day covered/open
    assert cal.is_trading_day(date(2026, 10, 1)) is False
    assert cal.is_trading_day(date(2026, 12, 31)) is True


def test_weekend_makeup_days_remain_closed() -> None:
    cal = JsonAShareTradingCalendar.load(FIXTURE)
    # National makeup workdays that fall on weekends — exchanges do not open.
    for day in (
        date(2024, 2, 4),
        date(2024, 2, 18),
        date(2024, 9, 14),
        date(2024, 10, 12),
        date(2025, 1, 26),
        date(2025, 2, 8),
        date(2026, 2, 15),
        date(2026, 10, 10),
    ):
        assert day.weekday() >= 5
        assert cal.is_trading_day(day) is False


def test_coverage_endpoints_and_no_2027_guessing() -> None:
    cal = JsonAShareTradingCalendar.load(FIXTURE)
    assert cal.coverage_from == date(2024, 1, 1)
    assert cal.coverage_to == date(2026, 12, 31)
    assert cal.is_trading_day(date(2024, 1, 2)) is True
    assert cal.is_trading_day(date(2026, 12, 31)) is True
    with pytest.raises(CalendarOutOfRange):
        cal.is_trading_day(date(2023, 12, 31))
    with pytest.raises(CalendarOutOfRange) as after:
        cal.is_trading_day(date(2027, 1, 1))
    assert after.value.code == "CALENDAR_OUT_OF_RANGE"
    with pytest.raises(CalendarOutOfRange):
        cal.previous_trading_day(date(2027, 6, 15))
    with pytest.raises(CalendarOutOfRange):
        cal.sessions_for(date(2027, 1, 4))


def test_load_and_sessions() -> None:
    cal = JsonAShareTradingCalendar.load(FIXTURE)
    assert cal.version == "sse-official-2024-2026.v1"
    assert cal.source["kind"] == "official_exchange_schedule"
    sessions = cal.sessions_for(date(2024, 1, 2))
    assert len(sessions) == 2
    assert sessions[0].start_at.tzinfo is not None
    assert sessions[0].end_at > sessions[0].start_at
    assert cal.sessions_for(date(2024, 1, 1)) == ()


def test_previous_trading_day() -> None:
    cal = JsonAShareTradingCalendar.load(FIXTURE)
    assert cal.previous_trading_day(date(2024, 1, 3)) == date(2024, 1, 2)
    assert cal.previous_trading_day(date(2024, 1, 8)) == date(2024, 1, 5)
    # Across CNY holiday
    assert cal.previous_trading_day(date(2024, 2, 19)) == date(2024, 2, 8)
    with pytest.raises(CalendarOutOfRange):
        cal.previous_trading_day(date(2024, 1, 1))
    with pytest.raises(CalendarOutOfRange):
        cal.previous_trading_day(date(2023, 12, 31))


def test_sha256_mismatch_rejected(tmp_path: Path) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["content_sha256"] = "0" * 64
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="content_sha256|sha256"):
        JsonAShareTradingCalendar.load(bad)


def test_isolated_wheel_load_path_present() -> None:
    """Packaged path constant exists; content matches tracked source when present."""
    # In editable/src layout the packaged copy may only exist after wheel install.
    tracked = JsonAShareTradingCalendar.load(FIXTURE)
    if PACKAGED_A_SHARE_TRADING_CALENDAR_PATH.is_file():
        packaged = JsonAShareTradingCalendar.load(PACKAGED_A_SHARE_TRADING_CALENDAR_PATH)
        assert packaged.content_sha256 == tracked.content_sha256
        assert packaged.coverage_to == date(2026, 12, 31)
    assert tracked.is_trading_day(date(2026, 7, 17)) is True


def test_default_loader_prefers_project_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_copy = tmp_path / "project.json"
    packaged_copy = tmp_path / "packaged.json"
    project_copy.write_bytes(FIXTURE.read_bytes())
    packaged_copy.write_bytes(FIXTURE.read_bytes())
    monkeypatch.setattr(calendar_module, "DEFAULT_A_SHARE_TRADING_CALENDAR_PATH", project_copy)
    monkeypatch.setattr(calendar_module, "PACKAGED_A_SHARE_TRADING_CALENDAR_PATH", packaged_copy)

    loaded_paths: list[Path] = []
    original_load = JsonAShareTradingCalendar.load

    def tracking_load(
        cls: type[JsonAShareTradingCalendar], path: Path
    ) -> JsonAShareTradingCalendar:
        del cls
        loaded_paths.append(path)
        return original_load(path)

    monkeypatch.setattr(JsonAShareTradingCalendar, "load", classmethod(tracking_load))

    calendar = load_default_a_share_trading_calendar()

    assert loaded_paths == [project_copy]
    assert calendar.content_sha256 == JsonAShareTradingCalendar.load(FIXTURE).content_sha256


def test_default_loader_falls_back_to_packaged_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing_project = tmp_path / "missing-project.json"
    packaged_copy = tmp_path / "packaged.json"
    packaged_copy.write_bytes(FIXTURE.read_bytes())
    monkeypatch.setattr(calendar_module, "DEFAULT_A_SHARE_TRADING_CALENDAR_PATH", missing_project)
    monkeypatch.setattr(calendar_module, "PACKAGED_A_SHARE_TRADING_CALENDAR_PATH", packaged_copy)

    calendar = load_default_a_share_trading_calendar()

    assert calendar.content_sha256 == JsonAShareTradingCalendar.load(FIXTURE).content_sha256


def test_default_loader_rejects_missing_sources_without_path_disclosure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret_marker = "private-home-marker"
    monkeypatch.setattr(
        calendar_module,
        "DEFAULT_A_SHARE_TRADING_CALENDAR_PATH",
        tmp_path / secret_marker / "project.json",
    )
    monkeypatch.setattr(
        calendar_module,
        "PACKAGED_A_SHARE_TRADING_CALENDAR_PATH",
        tmp_path / secret_marker / "packaged.json",
    )

    with pytest.raises(ConfigurationError) as raised:
        load_default_a_share_trading_calendar()

    assert raised.value.code == "CONFIGURATION_ERROR"
    assert raised.value.details == {
        "reason": "calendar_missing",
        "attempted_locations": ["project_config", "packaged_resource"],
    }
    assert secret_marker not in str(raised.value)
    assert secret_marker not in repr(raised.value.details)


def test_default_loader_delegates_malformed_preferred_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    malformed_project = tmp_path / "malformed-project.json"
    malformed_project.write_text("{not-json", encoding="utf-8")
    packaged_copy = tmp_path / "packaged.json"
    packaged_copy.write_bytes(FIXTURE.read_bytes())
    monkeypatch.setattr(calendar_module, "DEFAULT_A_SHARE_TRADING_CALENDAR_PATH", malformed_project)
    monkeypatch.setattr(calendar_module, "PACKAGED_A_SHARE_TRADING_CALENDAR_PATH", packaged_copy)

    with pytest.raises(ConfigurationError) as raised:
        load_default_a_share_trading_calendar()

    assert raised.value.details["reason"] == "malformed_json"


def test_calendar_packaged_resource_identity_is_frozen() -> None:
    assert calendar_module.DEFAULT_A_SHARE_TRADING_CALENDAR_PATH == FIXTURE
    assert (
        calendar_module.PACKAGED_A_SHARE_TRADING_CALENDAR_PATH
        == PACKAGED_A_SHARE_TRADING_CALENDAR_PATH
    )
    assert PACKAGED_A_SHARE_TRADING_CALENDAR_PATH.name == FIXTURE.name
