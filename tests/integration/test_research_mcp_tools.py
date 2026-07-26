"""Real FastMCP stdio integration for Phase 1B research tools."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from interfaces.mcp.server import PUBLIC_TOOL_NAMES

EXPECTED_TOOLS = PUBLIC_TOOL_NAMES


def _alembic_config(project_root: Path) -> Config:
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "migrations"))
    return cfg


def _migrate(database_url: str, project_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Alembic env.py loads URL via AppSettings — set DATABASE_URL."""
    for key in list(os.environ):
        if key in __import__("conftest").APP_SETTINGS_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_NAME", "trading-partner-research-stdio-test")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MCP_SERVER_NAME", "trading-partner-research-stdio-test")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "5")
    command.upgrade(_alembic_config(project_root), "head")


def _stdio_env(database_path: Path, project_root: Path) -> dict[str, str]:
    # Editable install + hatch dev-mode-dirs put src/ on sys.path; do not inject PYTHONPATH.
    _ = project_root
    return {
        **os.environ,
        "APP_NAME": "trading-partner-research-stdio-test",
        "APP_ENV": "test",
        "LOG_LEVEL": "INFO",
        "DATABASE_URL": f"sqlite:///{database_path}",
        "MCP_SERVER_NAME": "trading-partner-research-stdio-test",
        "DEFAULT_TIMEZONE": "UTC",
        "PROVIDER_TIMEOUT_SECONDS": "5",
    }


def _parse_envelope(result: Any) -> dict[str, Any]:
    assert result.isError is False, getattr(result, "content", result)
    assert result.content, "tool result missing content"
    text = result.content[0].text
    payload = json.loads(text)
    assert isinstance(payload, dict)
    assert "ok" in payload
    assert "errors" in payload
    return payload


class _CompactSession:
    """Route lifecycle semantics through the sole compact public names."""

    _GROUPED = {
        "investment_case_create": ("investment_case_manage", "create"),
        "investment_case_query": ("investment_case_read", "query"),
        "investment_case_archive": ("investment_case_manage", "archive"),
        "research_state_get": ("research_judgment_get", "state"),
        "research_state_update": ("research_judgment_propose", "research_state"),
        "thesis_revision_propose": ("research_judgment_propose", "thesis_revision"),
        "thesis_history_get": ("research_judgment_get", "thesis_history"),
    }
    _RENAMED = {"thesis_revision_confirm": "research_judgment_confirm"}

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name in self._GROUPED:
            target, operation = self._GROUPED[name]
            return await self._session.call_tool(
                target,
                {"request": {"operation": operation, **arguments}},
            )
        return await self._session.call_tool(self._RENAMED.get(name, name), arguments)


def _revision_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "kind": "thesis_revision",
        "title": "Primary demand thesis",
        "statement": "Demand is structural over multi-year horizon",
        "rationale": "Hyperscaler capex and CUDA ecosystem lock-in",
        "confidence_band": "high",
        "rating": "buy",
        "invalidation_check_note": "Monitor gross margin and export controls",
        "assumptions": [
            {
                "statement": "AI spend continues",
                "basis": "Hyperscaler guidance",
                "falsifiability": "Capex cuts for two quarters",
            }
        ],
        "invalidations": [
            {
                "description": "Gross margin collapse",
                "observable": "Gross margin below 50%",
                "severity": "hard",
            }
        ],
        "thesis_role": "primary",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_research_mcp_tools_stdio_full_lifecycle(
    tmp_path: Path, project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "research_stdio.db"
    database_url = f"sqlite:///{database_path}"
    _migrate(database_url, project_root, monkeypatch)

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "interfaces.mcp.server"],
        cwd=str(project_root),
        env=_stdio_env(database_path, project_root),
    )

    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        api = _CompactSession(session)

        # ---- exact tool list: 1A(2)+1B(9)+1C(6)+1D(1)+1E(7)=25 ----
        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        assert names == EXPECTED_TOOLS
        assert len(names) == 28
        # No old aliases or internal Evidence writes.
        assert "open_question_create" not in names
        assert "thesis_revision_reject" not in names
        assert "thesis_revision_withdraw" not in names
        assert "evidence_create" not in names
        assert "report_create" not in names

        # Phase 1A health tool remains wired.
        health = _parse_envelope(await api.call_tool("system_health", {}))
        assert health["ok"] is True

        # ---- investment_case create / query ----
        created = _parse_envelope(
            await api.call_tool(
                "investment_case_create",
                {
                    "case_type": "company",
                    "title": "NVDA long-horizon",
                    "summary": "Structural GPU demand thesis for multi-year hold",
                    "primary_instrument_id": "equity:US:NVDA",
                    "topic_tags": ["ai", "semiconductors"],
                    "linked_case_ids": [],
                    "confirmed_by": "user",
                    "idempotency_key": "case-create-1",
                },
            )
        )
        assert created["ok"] is True, created
        case_id = created["data"]["case_id"]
        assert case_id.startswith("case_")
        assert created["data"]["status"] == "draft"

        got = _parse_envelope(await api.call_tool("investment_case_query", {"case_id": case_id}))
        assert got["ok"] is True
        assert got["data"]["case_id"] == case_id
        assert got["data"]["title"] == "NVDA long-horizon"

        listed = _parse_envelope(
            await api.call_tool(
                "investment_case_query",
                {"include_archived": False, "limit": 50, "offset": 0},
            )
        )
        assert listed["ok"] is True
        assert listed["data"]["total"] >= 1
        assert any(item["case_id"] == case_id for item in listed["data"]["items"])

        # ---- thesis_revision propose + confirm ----
        proposed = _parse_envelope(
            await api.call_tool(
                "thesis_revision_propose",
                {
                    "case_id": case_id,
                    "thesis_id": None,
                    "payload": _revision_payload(),
                    "confirmation_mode": "strict_review",
                    "proposed_by": "codex",
                    "proposed_by_rationale": "Initial primary thesis from research discussion",
                    "idempotency_key": "thesis-propose-1",
                },
            )
        )
        assert proposed["ok"] is True, proposed
        candidate_id = proposed["data"]["candidate_id"]
        assert candidate_id.startswith("run_")
        assert proposed["data"]["status"] == "proposed"
        assert proposed["data"]["kind"] == "thesis_revision"

        # Idempotent re-propose returns same candidate + DUPLICATE warning
        reprop = _parse_envelope(
            await api.call_tool(
                "thesis_revision_propose",
                {
                    "case_id": case_id,
                    "thesis_id": None,
                    "payload": _revision_payload(),
                    "confirmation_mode": "strict_review",
                    "proposed_by": "codex",
                    "proposed_by_rationale": "Initial primary thesis from research discussion",
                    "idempotency_key": "thesis-propose-1",
                },
            )
        )
        assert reprop["ok"] is True, reprop
        assert reprop["degraded"] is True
        assert reprop["data"]["candidate_id"] == candidate_id
        assert any(w["code"] == "DUPLICATE_IDEMPOTENCY_KEY" for w in reprop["warnings"])

        # Pending candidates appear in research_state_get before confirm
        pending_state = _parse_envelope(
            await api.call_tool(
                "research_state_get",
                {
                    "case_id": case_id,
                    "include_archived_theses": False,
                    "include_watchlist": True,
                },
            )
        )
        assert pending_state["ok"] is True, pending_state
        pending_ids = {c["candidate_id"] for c in pending_state["data"]["pending_candidates"]}
        assert candidate_id in pending_ids

        # codex cannot confirm (schema → JSON-RPC/tool error, not envelope)
        codex_confirm = await api.call_tool(
            "thesis_revision_confirm",
            {
                "candidate_id": candidate_id,
                "action": "confirm",
                "reviewed_by": "codex",
            },
        )
        assert codex_confirm.isError is True

        confirmed = _parse_envelope(
            await api.call_tool(
                "thesis_revision_confirm",
                {
                    "candidate_id": candidate_id,
                    "action": "confirm",
                    "reviewed_by": "user",
                    "submitted_via": "codex_chat",
                    "authorization_note": "我确认这个候选",
                    "review_note": "User confirmed primary thesis",
                },
            )
        )
        assert confirmed["ok"] is True, confirmed
        assert confirmed["data"]["candidate"]["status"] == "confirmed"
        assert confirmed["data"]["research_state"] is not None
        # affected_entity_id is revision-oriented; thesis_id lives on the state snapshot.
        theses = confirmed["data"]["research_state"]["theses"]
        assert len(theses) >= 1
        thesis_id = theses[0]["thesis_id"]
        assert thesis_id.startswith("thesis_")

        # history after confirm
        history = _parse_envelope(
            await api.call_tool("thesis_history_get", {"thesis_id": thesis_id})
        )
        assert history["ok"] is True, history
        assert history["data"]["thesis"]["thesis_id"] == thesis_id
        assert len(history["data"]["revisions"]) >= 1

        # ---- reject path ----
        reject_prop = _parse_envelope(
            await api.call_tool(
                "thesis_revision_propose",
                {
                    "case_id": case_id,
                    "thesis_id": thesis_id,
                    "payload": _revision_payload(
                        title="Aggressive upside",
                        statement="Demand doubles again next year",
                        replaces_revision_no=1,
                    ),
                    "confirmation_mode": "normal",
                    "proposed_by": "codex",
                    "proposed_by_rationale": "Upside scenario candidate",
                    "idempotency_key": "thesis-propose-reject",
                },
            )
        )
        assert reject_prop["ok"] is True, reject_prop
        reject_cand = reject_prop["data"]["candidate_id"]

        rejected = _parse_envelope(
            await api.call_tool(
                "thesis_revision_confirm",
                {
                    "candidate_id": reject_cand,
                    "action": "reject",
                    "reviewed_by": "user",
                    "rejection_reason": "Too aggressive without evidence",
                },
            )
        )
        assert rejected["ok"] is True, rejected
        assert rejected["data"]["status"] == "rejected"

        # ---- withdraw path ----
        withdraw_prop = _parse_envelope(
            await api.call_tool(
                "thesis_revision_propose",
                {
                    "case_id": case_id,
                    "thesis_id": thesis_id,
                    "payload": _revision_payload(
                        title="Withdraw me",
                        statement="Temporary draft thesis",
                        replaces_revision_no=1,
                    ),
                    "confirmation_mode": "normal",
                    "proposed_by": "codex",
                    "proposed_by_rationale": "Will withdraw after rethink",
                    "idempotency_key": "thesis-propose-withdraw",
                },
            )
        )
        assert withdraw_prop["ok"] is True, withdraw_prop
        withdraw_cand = withdraw_prop["data"]["candidate_id"]

        withdrawn = _parse_envelope(
            await api.call_tool(
                "thesis_revision_confirm",
                {
                    "candidate_id": withdraw_cand,
                    "action": "withdraw",
                    "reviewed_by": "codex",
                    "review_note": "Withdrawing own NORMAL candidate",
                },
            )
        )
        assert withdrawn["ok"] is True, withdrawn
        assert withdrawn["data"]["status"] == "withdrawn"

        # research_state_update must not create cases (envelope business error)
        blocked_create = _parse_envelope(
            await api.call_tool(
                "research_state_update",
                {
                    "case_id": case_id,
                    "payload": {
                        "kind": "case_status_change",
                        "action": "create",
                        "case_type": "theme",
                        "title": "Should fail",
                        "summary": "Must use investment_case_create",
                    },
                    "confirmation_mode": "normal",
                    "proposed_by": "user",
                    "proposed_by_rationale": "attempt bypass",
                    "idempotency_key": "bad-case-create",
                },
            )
        )
        assert blocked_create["ok"] is False
        assert blocked_create["errors"]
        assert "investment_case_create" in blocked_create["errors"][0]["message"]

        # ---- research_state_update: open_question + watchlist ----
        oq = _parse_envelope(
            await api.call_tool(
                "research_state_update",
                {
                    "case_id": case_id,
                    "payload": {
                        "kind": "open_question",
                        "action": "create",
                        "text": "What is the next-gen GPU launch timing?",
                    },
                    "confirmation_mode": "normal",
                    "proposed_by": "codex",
                    "proposed_by_rationale": "Track launch cadence uncertainty",
                    "idempotency_key": "oq-create-1",
                },
            )
        )
        assert oq["ok"] is True, oq
        assert oq["data"]["kind"] == "open_question"
        assert oq["data"]["status"] == "proposed"

        oq_confirm = _parse_envelope(
            await api.call_tool(
                "thesis_revision_confirm",
                {
                    "candidate_id": oq["data"]["candidate_id"],
                    "action": "confirm",
                    "reviewed_by": "user",
                },
            )
        )
        assert oq_confirm["ok"] is True, oq_confirm

        wl = _parse_envelope(
            await api.call_tool(
                "research_state_update",
                {
                    "case_id": case_id,
                    "payload": {
                        "kind": "watchlist_item",
                        "action": "create",
                        "market": "US",
                        "symbol": "AVGO",
                        "display_name": "Broadcom",
                        "thesis_hint": "Custom ASIC peer for comparison",
                        "triggers": ["earnings miss", "export control"],
                        "case_id": case_id,
                    },
                    "confirmation_mode": "normal",
                    "proposed_by": "user",
                    "proposed_by_rationale": "Peer watchlist entry",
                    "idempotency_key": "wl-create-1",
                },
            )
        )
        assert wl["ok"] is True, wl
        assert wl["data"]["kind"] == "watchlist_item"

        wl_confirm = _parse_envelope(
            await api.call_tool(
                "thesis_revision_confirm",
                {
                    "candidate_id": wl["data"]["candidate_id"],
                    "action": "confirm",
                    "reviewed_by": "user",
                },
            )
        )
        assert wl_confirm["ok"] is True, wl_confirm

        # ---- research_state_get snapshot ----
        state = _parse_envelope(
            await api.call_tool(
                "research_state_get",
                {
                    "case_id": case_id,
                    "include_archived_theses": False,
                    "include_watchlist": True,
                },
            )
        )
        assert state["ok"] is True, state
        data = state["data"]
        assert data["case"]["case_id"] == case_id
        assert len(data["theses"]) >= 1
        assert len(data["latest_revisions"]) >= 1
        assert len(data["open_questions"]) >= 1
        assert len(data["watchlist_items"]) >= 1

        # ---- archive ----
        archived = _parse_envelope(
            await api.call_tool(
                "investment_case_archive",
                {
                    "case_id": case_id,
                    "archived_reason": "Thesis invalidated by policy shift",
                    "reviewed_by": "user",
                    "idempotency_key": "case-archive-1",
                },
            )
        )
        assert archived["ok"] is True, archived
        assert archived["data"]["status"] == "archived"
        assert archived["data"]["archived_reason"] is not None

        # business error still ToolEnvelope (not JSON-RPC)
        missing = _parse_envelope(
            await api.call_tool(
                "investment_case_query",
                {"case_id": "case_00000000-0000-7000-8000-000000000099"},
            )
        )
        assert missing["ok"] is False
        assert missing["errors"]
        assert missing["errors"][0]["code"]

        # schema validation failure is MCP tool error (not ToolEnvelope ok=false)
        bad_id = await api.call_tool(
            "investment_case_query",
            {"case_id": "case_not-a-uuid"},
        )
        assert bad_id.isError is True

        # create-case idempotency warning path
        create_again = _parse_envelope(
            await api.call_tool(
                "investment_case_create",
                {
                    "case_type": "company",
                    "title": "NVDA long-horizon",
                    "summary": "Structural GPU demand thesis for multi-year hold",
                    "primary_instrument_id": "equity:US:NVDA",
                    "topic_tags": ["ai", "semiconductors"],
                    "linked_case_ids": [],
                    "confirmed_by": "user",
                    "idempotency_key": "case-create-1",
                },
            )
        )
        assert create_again["ok"] is True
        assert create_again["degraded"] is True
        assert any(w["code"] == "DUPLICATE_IDEMPOTENCY_KEY" for w in create_again["warnings"])
        assert create_again["data"]["case_id"] == case_id
