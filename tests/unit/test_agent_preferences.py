from __future__ import annotations

from datetime import UTC, datetime

import pytest

from application.services.agent_preferences_service import AgentPreferencesService
from domain.common.errors import AgentPreferencesVersionConflict, DataContractError


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


class _Ids:
    def __init__(self) -> None:
        self.n = 0

    def new(self, prefix: object) -> str:
        self.n += 1
        return f"{getattr(prefix, 'value', 'id')}_{self.n}"


class _Repo:
    def __init__(self) -> None:
        self.value = None
        self.revisions = []

    def get(self, owner: str):
        return (
            self.value if self.value is not None and self.value.owner_principal == owner else None
        )

    def create(self, value, revision):
        self.value = value
        self.revisions.append(revision)
        return value

    def update(self, value, *, expected_version: int, revision):
        assert self.value.version == expected_version
        self.value = value
        self.revisions.append(revision)
        return value

    def list_history(self, owner: str, *, limit: int = 100):
        return tuple(item for item in self.revisions if item.owner_principal == owner)[:limit]


def test_preferences_are_explicit_versioned_and_presentation_only() -> None:
    repo = _Repo()
    service = AgentPreferencesService(repo, _Clock(), _Ids())
    value = service.update(
        "local-console",
        {"language": "en", "default_chart": True},
        expected_version=0,
        actor="user:1",
        idempotency_key="pref-create",
        authorization_note="explicit user preference update",
    )
    assert value.version == 1
    assert value.as_dict()["language"] == "en"
    assert "price" not in value.as_dict()
    assert "position" not in value.as_dict()
    updated = service.update(
        "local-console",
        {"response_density": "compact", "preferred_source_codes": ["dukascopy"]},
        expected_version=1,
        actor="user:1",
        idempotency_key="pref-update",
        authorization_note="explicit user preference update",
    )
    assert updated.version == 2
    assert len(service.history("local-console")) == 2


def test_preferences_reject_unknown_fact_fields_and_stale_versions() -> None:
    repo = _Repo()
    service = AgentPreferencesService(repo, _Clock(), _Ids())
    with pytest.raises(DataContractError):
        service.update(
            "local-console",
            {"price": "4310"},
            expected_version=0,
            actor="user:1",
            idempotency_key="bad",
            authorization_note="explicit user preference update",
        )
    with pytest.raises(DataContractError):
        service.update(
            "local-console",
            {"web_background": False},
            expected_version=0,
            actor="user:1",
            idempotency_key="disable-web-search",
            authorization_note="attempt to disable default web search",
        )
    service.update(
        "local-console",
        {"risk_style": "cautious"},
        expected_version=0,
        actor="user:1",
        idempotency_key="pref-create",
        authorization_note="explicit user preference update",
    )
    with pytest.raises(AgentPreferencesVersionConflict):
        service.update(
            "local-console",
            {"risk_style": "direct"},
            expected_version=0,
            actor="user:1",
            idempotency_key="stale",
            authorization_note="explicit user preference update",
        )
