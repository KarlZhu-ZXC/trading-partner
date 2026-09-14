from types import SimpleNamespace as NS
from unittest.mock import Mock

import httpx
import pytest

import interfaces.console.api as api


@pytest.mark.asyncio
async def test_digest_and_recovery_routes_only_read_and_disable_caching(monkeypatch):
    today = Mock(return_value=NS(model_dump=lambda **k: {"groups": []}))
    weekly = Mock(return_value=NS(model_dump=lambda **k: {"confirmed_views": []}))
    status = Mock(return_value={"status": "NOT_FOUND"})
    submit = Mock()
    services = NS(
        today_review=NS(get=today),
        weekly_review=NS(get=weekly),
        quick_review=NS(submission_status=status, submit=submit),
    )
    monkeypatch.setattr(api, "_container", lambda r: NS(services=services))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://127.0.0.1:8765"
    ) as client:
        for path in (
            "/api/review-digest",
            "/api/weekly-review",
            "/api/research/s1/quick-review/submissions/request-one",
        ):
            result = await client.get(path)
            assert result.status_code == 200
            assert result.headers["cache-control"] == "no-store"
        status.assert_called_once_with("s1", "request-one")
        today.assert_called_once()
        weekly.assert_called_once()
        submit.assert_not_called()
