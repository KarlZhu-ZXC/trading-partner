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
    if value.generated_by == "fallback" and all(
        block.kind is AgentAnswerBlockKind.SUMMARY for block in value.blocks
    ):
        return "".join(block.text for block in value.blocks)
    sections: list[str] = []
    for block in value.blocks:
        lines = [answer_block_heading(block), "", block.text]
        metadata = answer_block_metadata_line(block)
        if metadata:
            lines.extend(("", metadata))
        source_lines = answer_block_source_lines(block)
        if source_lines:
            lines.extend(("", *source_lines))
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def answer_block_heading(block: AgentAnswerBlock) -> str:
    """Return the exact heading used by the canonical answer renderer."""

    return f"## {_HEADINGS[block.kind]}"


def answer_block_metadata_line(block: AgentAnswerBlock) -> str | None:
    """Return the exact metadata line used by the canonical answer renderer."""

    metadata: list[str] = []
    if block.as_of:
        metadata.append(f"as_of={block.as_of}")
    if block.basis:
        metadata.append(f"basis={block.basis}")
    if block.evidence_refs:
        metadata.append(f"evidence={', '.join(block.evidence_refs)}")
    return f"`{' · '.join(metadata)}`" if metadata else None


def answer_block_source_lines(block: AgentAnswerBlock) -> tuple[str, ...]:
    """Return the exact source URL lines used by the canonical renderer."""

    return tuple(f"- {url}" for url in block.source_urls)


def parse_research_answer(value: str) -> AgentAnswerEnvelope:
    """Preserve a model's bounded root note as unverified text, never as facts.

    The public DTO stays closed. Only this observed model-output deviation is
    normalized; all other keys, block validators and size bounds remain strict.
    The caller must run the research field checker over every resulting block.
    """
    raw = value.strip()
    if raw.startswith("```json") and raw.endswith("```"):
        raw = raw[7:-3].strip()
    try:
        decoded = json.loads(raw)
        if isinstance(decoded, dict) and "note" in decoded:
            note = decoded["note"]
            if not isinstance(note, str) or not note.strip() or len(note) > 4_000:
                raise ValueError("invalid research note")
            base = {key: item for key, item in decoded.items() if key != "note"}
            envelope = AgentAnswerEnvelope.model_validate(base)
            return AgentAnswerEnvelope.model_validate(
                {
                    **envelope.model_dump(),
                    "blocks": [
                        *envelope.blocks,
                        AgentAnswerBlock(kind=AgentAnswerBlockKind.INFERENCE, text=note),
                    ],
                }
            )
    except (TypeError, ValueError):
        pass
    return parse_agent_answer(value)


def agent_answer_envelope_json(value: AgentAnswerEnvelope) -> str:
    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = [
    "agent_answer_envelope_json",
    "answer_block_heading",
    "answer_block_metadata_line",
    "answer_block_source_lines",
    "parse_agent_answer",
    "parse_research_answer",
    "render_agent_answer",
]
