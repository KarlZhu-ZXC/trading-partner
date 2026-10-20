"""Default note MCP stays independent of numerical SDK initialization."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from domain.common.errors import NoMarketData, ProviderRateLimitError, ProviderUnavailableError
from infrastructure.calendars.kr_market_session_calendar import XkrxMarketSessionCalendar
from infrastructure.calendars.us_market_session_calendar import XnysMarketSessionCalendar
from infrastructure.providers.us.yfinance_breadth_client import YFinanceBreadthClient
from infrastructure.providers.us.yfinance_calendar_client import YFinanceCalendarClient
from infrastructure.providers.us.yfinance_fundamentals_client import YFinanceFundamentalsClient
from infrastructure.providers.us.yfinance_statements_client import YFinanceStatementsClient

ROOT = Path(__file__).resolve().parents[2]


def test_default_mcp_composes_and_discovers_when_numerical_imports_are_unavailable(
    tmp_path, migrated_sqlite_url
):
    script = f"""
import importlib.abc, json, runpy, sys
blocked = {{'numpy', 'pandas', 'yfinance', 'exchange_calendars', 'talib', 'matplotlib',
           'pypdf', 'fastembed'}}
class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        optional_trace = fullname.startswith(('opentelemetry.sdk', 'opentelemetry.exporter'))
        if fullname.split('.')[0] in blocked or optional_trace:
            raise ModuleNotFoundError('optional dependency blocked: ' + fullname)
sys.meta_path.insert(0, Blocker())
runner = runpy.run_path({str(ROOT / "scripts/benchmark_mcp_startup.py")!r})
print(json.dumps(runner['sample']()))

import asyncio
from pathlib import Path
from sqlalchemy import event
from bootstrap import build_application
from infrastructure.config.settings import AppSettings
from interfaces.mcp.server import create_capability_registry
settings = AppSettings(_env_file=None, app_name='synthetic-read', app_env='test',
    log_level='INFO', database_url={migrated_sqlite_url!r}, mcp_server_name='synthetic-read',
    default_timezone='UTC', provider_timeout_seconds=5, runtime_root=Path({str(tmp_path)!r}))
container = build_application(settings)
from datetime import UTC, datetime
from domain.common.enums import VendorId
from domain.portfolio.enums import AccountEnvironment
from domain.portfolio.models import AccountSnapshot
from infrastructure.persistence.account_snapshot_repository import (
    SqlAlchemyAccountSnapshotRepository,
)
at = datetime(2026, 7, 16, tzinfo=UTC)
SqlAlchemyAccountSnapshotRepository(container.resources.database.engine).append_account(
    AccountSnapshot('synthetic_snapshot', 'synthetic_account', VendorId.SCHWAB,
        AccountEnvironment.REAL, 'USD', at, at, None, None, None, None, (), (), False, ())
)
writes = []
def record(connection, cursor, statement, parameters, context, executemany):
    if statement.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')):
        writes.append(statement.split()[0])
event.listen(container.resources.database.engine, 'before_cursor_execute', record)
async def read():
    try:
        registry = create_capability_registry(container)
        for name, operation in [('view_get', 'notes'), ('portfolio_get', 'positions')]:
            result = await registry.invoke(name, {{'request': {{'operation': operation}}}})
            assert result['ok'], (name, result)
        assert not writes, writes
        assert not blocked.intersection(sys.modules)
    finally:
        await container.aclose()
asyncio.run(read())
"""
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
    env.update(PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert len(receipt["tool_names"]) == 9
    assert "capability_read" in receipt["tool_names"]
    assert receipt["loaded_numerical_modules"] == []


@pytest.mark.parametrize("factory", [XnysMarketSessionCalendar, XkrxMarketSessionCalendar])
def test_calendar_loading_failure_is_not_cached_and_never_invents_a_session(factory, monkeypatch):
    import exchange_calendars

    real = exchange_calendars.get_calendar
    calls = []

    def load(name, **kwargs):
        calls.append(name)
        if len(calls) == 1:
            raise ValueError("synthetic schedule unavailable")
        return real(name, **kwargs)

    monkeypatch.setattr(exchange_calendars, "get_calendar", load)
    calendar = factory()
    assert not calls
    moment = datetime(2026, 7, 16, 12, tzinfo=UTC)
    with pytest.raises(ValueError, match="schedule unavailable"):
        calendar.session_at(moment)
    assert calendar.session_at(moment) is not None
    calendar.session_at(moment)
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["fundamentals", "statements", "breadth", "calendar"])
@pytest.mark.parametrize("failure", ["rate_limit", "missing", "unexpected"])
async def test_deferred_yahoo_clients_preserve_error_mapping(kind, failure, monkeypatch):
    from yfinance.exceptions import YFRateLimitError, YFTickerMissingError

    error = {
        "rate_limit": YFRateLimitError(),
        "missing": YFTickerMissingError("SYNTHETIC", "synthetic missing ticker"),
        "unexpected": RuntimeError("sensitive upstream details must not escape"),
    }[failure]

    def fail(*args):
        raise error

    expected = ProviderUnavailableError
    if failure == "rate_limit":
        expected = ProviderRateLimitError
    elif failure == "missing" and kind in {"fundamentals", "statements"}:
        expected = NoMarketData
    if kind == "fundamentals":
        client = YFinanceFundamentalsClient()
        monkeypatch.setattr(client, "_get_info_sync", fail)
        call = client.get_info("SYNTHETIC", timeout_seconds=1)
    elif kind == "statements":
        client = YFinanceStatementsClient()
        monkeypatch.setattr(client, "_get_tables_sync", fail)
        call = client.get_tables("SYNTHETIC", frequency="yearly", timeout_seconds=1)
    elif kind == "breadth":
        client = YFinanceBreadthClient()
        monkeypatch.setattr(client, "_get_current_sync", fail)
        call = client.get_current(timeout_seconds=1)
    else:
        call = YFinanceCalendarClient._run(fail, timeout_seconds=1)
    with pytest.raises(expected) as caught:
        await call
    assert caught.value.details["vendor"] == "yfinance"
    assert "sensitive upstream" not in str(caught.value)
