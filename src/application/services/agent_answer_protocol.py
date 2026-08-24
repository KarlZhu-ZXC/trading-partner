"""Parse and render typed Agent answers with a legacy text fallback."""

from __future__ import annotations

import json

from application.dto.agent_answer import (
    AgentAnswerBlock,
    AgentAnswerBlockKind,
    AgentAnswerEnvelope,
)

_HEADINGS = {
    AgentAnswerBlockKind.SUMMARY: "结论",
    AgentAnswerBlockKind.FACT: "事实",
    AgentAnswerBlockKind.INFERENCE: "推断",
    AgentAnswerBlockKind.GAP: "缺口",
    AgentAnswerBlockKind.NEXT_STEP: "下一步",
    AgentAnswerBlockKind.CITATION: "引用",
}
_FALLBACK_BLOCK_CHARS = 4_000
_FALLBACK_TOTAL_CHARS = 64_000


def parse_agent_answer(value: str) -> AgentAnswerEnvelope:
    raw = value.strip()
    if raw.startswith("```json") and raw.endswith("```"):
        raw = raw[7:-3].strip()
    try:
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise ValueError("answer envelope must be an object")
        return AgentAnswerEnvelope.model_validate(decoded)
    except (TypeError, ValueError):
        fallback_text = value.strip() or "模型未返回可显示内容。"
        fallback_text = fallback_text[:_FALLBACK_TOTAL_CHARS]
        return AgentAnswerEnvelope(
            generated_by="fallback",
            blocks=tuple(
                AgentAnswerBlock(
                    kind=AgentAnswerBlockKind.SUMMARY,
                    text=fallback_text[offset : offset + _FALLBACK_BLOCK_CHARS],
                )
                for offset in range(0, len(fallback_text), _FALLBACK_BLOCK_CHARS)
            ),
        )


def render_agent_answer(value: AgentAnswerEnvelope) -> str:
    if (
        value.generated_by == "fallback"
        and all(block.kind is AgentAnswerBlockKind.SUMMARY for block in value.blocks)
    ):
        return "".join(block.text for block in value.blocks)
    sections: list[str] = []
    for block in value.blocks:
        lines = [f"## {_HEADINGS[block.kind]}", "", block.text]
        metadata: list[str] = []
        if block.as_of:
            metadata.append(f"as_of={block.as_of}")
        if block.basis:
            metadata.append(f"basis={block.basis}")
        if block.evidence_refs:
            metadata.append(f"evidence={', '.join(block.evidence_refs)}")
        if metadata:
            lines.extend(("", f"`{' · '.join(metadata)}`"))
        if block.source_urls:
            lines.extend(("", *[f"- {url}" for url in block.source_urls]))
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def agent_answer_envelope_json(value: AgentAnswerEnvelope) -> str:
    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = ["agent_answer_envelope_json", "parse_agent_answer", "render_agent_answer"]
