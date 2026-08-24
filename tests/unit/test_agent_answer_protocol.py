from __future__ import annotations

import json

from application.dto.agent_answer import AgentAnswerBlockKind
from application.services.agent_answer_protocol import (
    agent_answer_envelope_json,
    parse_agent_answer,
    render_agent_answer,
)


def test_structured_answer_parses_and_renders_deterministically() -> None:
    value = parse_agent_answer(
        json.dumps(
            {
                "schema_version": 1,
                "generated_by": "model",
                "blocks": [
                    {
                        "kind": "FACT",
                        "text": "当前 durable snapshot 无持仓。",
                        "evidence_refs": ["req_positions"],
                        "as_of": "2026-08-20T04:00:00Z",
                    },
                    {
                        "kind": "NEXT_STEP",
                        "text": "无需执行动作。",
                    },
                ],
            }
        )
    )
    assert value.generated_by == "model"
    assert value.blocks[0].kind is AgentAnswerBlockKind.FACT
    rendered = render_agent_answer(value)
    assert "## 事实" in rendered
    assert "evidence=req_positions" in rendered
    assert "## 下一步" in rendered
    assert json.loads(agent_answer_envelope_json(value))["schema_version"] == 1


def test_plain_text_fallback_preserves_legacy_answer_exactly() -> None:
    raw = "保持原来的普通文本回答。"
    value = parse_agent_answer(raw)
    assert value.generated_by == "fallback"
    assert render_agent_answer(value) == raw


def test_long_plain_text_fallback_is_chunked_without_losing_content() -> None:
    raw = "x" * 4_001
    value = parse_agent_answer(raw)
    assert value.generated_by == "fallback"
    assert len(value.blocks) == 2
    assert render_agent_answer(value) == raw


def test_plain_text_fallback_has_a_total_safety_bound() -> None:
    raw = "x" * 64_001
    value = parse_agent_answer(raw)
    assert render_agent_answer(value) == raw[:64_000]


def test_invalid_structured_url_fails_closed_to_plain_text() -> None:
    raw = json.dumps(
        {
            "schema_version": 1,
            "generated_by": "model",
            "blocks": [
                {
                    "kind": "CITATION",
                    "text": "unsafe",
                    "source_urls": ["https://user:password@example.com/private"],
                }
            ],
        }
    )
    value = parse_agent_answer(raw)
    assert value.generated_by == "fallback"
    assert render_agent_answer(value) == raw
