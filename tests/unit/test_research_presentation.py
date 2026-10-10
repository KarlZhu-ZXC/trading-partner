"""Host-owned research presentation projections."""

from __future__ import annotations

import pytest

from application.dto.agent_answer import AgentAnswerBlock, AgentAnswerEnvelope
from application.dto.research_presentation import (
    ResearchFieldSegment,
    ResearchMetadataSegment,
    ResearchTextSegment,
)
from application.ports.agent_tool_gateway import AgentToolReceipt
from application.services.agent_answer_protocol import render_agent_answer
from application.services.copilot_research_evidence import (
    guard_research_answer,
    research_evidence_catalog,
)


@pytest.fixture
def scoped_evidence() -> tuple[AgentToolReceipt, dict[str, object]]:
    receipt = AgentToolReceipt(
        capability="portfolio_get",
        operation="positions",
        request_id="req_presentation",
        effect="READ_DURABLE",
        source_codes=("SYNTHETIC",),
        warning_codes=("SCOPE_WARNING",),
        degraded=True,
    )
    payload: dict[str, object] = {
        "receipt": receipt.as_dict(),
        "result": {
            "data": {
                "positions": [
                    {
                        "instrument_id": "equity:US:SYNTH",
                        "account_ref": "synthetic_account",
                        "currency": "USD",
                        "quantity": "0",
                        "snapshot_at": "2026-09-15T12:00:00Z",
                        "basis": "broker_snapshot",
                    },
                    {
                        "instrument_id": "equity:US:OTHER",
                        "account_ref": "acct · key=value",
                        "currency": "JPY",
                        "quantity": None,
                        "snapshot_at": "2026-09-14T12:00:00Z",
                    },
                ],
                "source_url": "https://example.test/source",
            }
        },
    }
    return receipt, payload


def _entry(
    receipt: AgentToolReceipt,
    payload: dict[str, object],
    suffix: str,
) -> dict[str, object]:
    return next(
        entry
        for entry in research_evidence_catalog([receipt], [payload])["entries"]
        if entry["ref"].endswith(suffix)
    )


def test_projection_preserves_scoped_fields_and_ordered_context(scoped_evidence) -> None:
    receipt, payload = scoped_evidence
    entry = _entry(receipt, payload, "/positions/0/quantity")
    guarded = guard_research_answer(
        AgentAnswerEnvelope(
            blocks=(
                AgentAnswerBlock(
                    kind="FACT",
                    text=entry["text"],
                    evidence_refs=(entry["ref"],),
                    as_of=entry["as_of"],
                    basis=entry["basis"],
                ),
            )
        ),
        receipts=[receipt],
        tool_payloads=[payload],
    )

    assert guarded.presentation is not None
    field = next(
        segment
        for segment in guarded.presentation.segments
        if isinstance(segment, ResearchFieldSegment)
    )
    assert field.ref == entry["ref"]
    assert field.path == "result/data/positions/0/quantity"
    assert field.scalar == "0"
    assert field.is_null is False
    assert field.context == (
        ("instrument_id", "equity:US:SYNTH"),
        ("account_ref", "synthetic_account"),
        ("currency", "USD"),
        ("snapshot_at", "2026-09-15T12:00:00Z"),
        ("basis", "broker_snapshot"),
        ("sources", "SYNTHETIC"),
        ("warnings", "SCOPE_WARNING"),
        ("degraded", "true"),
    )


def test_projection_renders_inline_and_grouped_fields_from_canonical_values(
    scoped_evidence,
) -> None:
    receipt, payload = scoped_evidence
    zero = _entry(receipt, payload, "/positions/0/quantity")
    missing = _entry(receipt, payload, "/positions/1/quantity")
    inline = AgentAnswerBlock(
        kind="INFERENCE",
        text=f"数量为@evidence({zero['ref']})。",
        evidence_refs=(zero["ref"],),
    )
    grouped = AgentAnswerBlock(
        kind="FACT",
        text="@evidence",
        evidence_refs=(zero["ref"], missing["ref"]),
    )
    guarded = guard_research_answer(
        AgentAnswerEnvelope(blocks=(inline, grouped)),
        receipts=[receipt],
        tool_payloads=[payload],
    )

    assert guarded.presentation is not None
    fields = [
        segment
        for segment in guarded.presentation.segments
        if isinstance(segment, ResearchFieldSegment)
    ]
    assert [field.ref for field in fields] == [zero["ref"], zero["ref"], missing["ref"]]
    assert fields[0].inline is True
    assert fields[1].inline is False
    assert fields[2].scalar == "null"
    assert fields[2].is_null is True
    assert guarded.presentation.rendered_text() == render_agent_answer(guarded.envelope)


def test_projection_preserves_delimiter_containing_context_without_text_parsing(
    scoped_evidence,
) -> None:
    receipt, payload = scoped_evidence
    entry = _entry(receipt, payload, "/positions/1/quantity")
    guarded = guard_research_answer(
        AgentAnswerEnvelope(
            blocks=(
                AgentAnswerBlock(
                    kind="FACT",
                    text=entry["text"],
                    evidence_refs=(entry["ref"],),
                    as_of=entry["as_of"],
                    basis=entry["basis"],
                ),
            )
        ),
        receipts=[receipt],
        tool_payloads=[payload],
    )

    assert guarded.presentation is not None
    field = next(
        segment
        for segment in guarded.presentation.segments
        if isinstance(segment, ResearchFieldSegment)
    )
    assert ("account_ref", "acct · key=value") in field.context
    assert field.raw_text() == entry["text"]


def test_projection_contains_exact_frame_metadata_and_source_text(scoped_evidence) -> None:
    receipt, payload = scoped_evidence
    entry = _entry(receipt, payload, "/source_url")
    guarded = guard_research_answer(
        AgentAnswerEnvelope(
            blocks=(
                AgentAnswerBlock(
                    kind="FACT",
                    text="@evidence",
                    evidence_refs=(entry["ref"],),
                ),
            )
        ),
        receipts=[receipt],
        tool_payloads=[payload],
    )

    assert guarded.presentation is not None
    assert guarded.presentation.rendered_text() == render_agent_answer(guarded.envelope)
    assert any(
        isinstance(segment, ResearchMetadataSegment)
        and segment.text == "`evidence=req_presentation/result/data/source_url`"
        for segment in guarded.presentation.segments
    )
    assert any(
        isinstance(segment, ResearchTextSegment)
        and segment.text == "- https://example.test/source"
        for segment in guarded.presentation.segments
    )


@pytest.mark.parametrize("variant", ["rejected", "stale", "trimmed"])
def test_rejected_stale_or_trimmed_refs_have_no_projected_field(
    variant: str,
    scoped_evidence,
) -> None:
    receipt, payload = scoped_evidence
    if variant == "rejected":
        entry = _entry(receipt, payload, "/positions/0/quantity")
        block = AgentAnswerBlock(
            kind="FACT",
            text=entry["text"].replace("quantity: 0", "quantity: 999"),
            evidence_refs=(entry["ref"],),
        )
        receipts = [receipt]
        payloads = [payload]
    elif variant == "stale":
        receipt = AgentToolReceipt(
            capability="market_data_get",
            operation="quote",
            request_id="req_stale_projection",
            effect="READ_PROVIDER",
        )
        payloads = [
            {
                "receipt": receipt.as_dict(),
                "result": {
                    "as_of": "2026-09-15T12:00:00Z",
                    "data": {"quote_at": "2026-09-16T12:00:00Z", "last": "123"},
                },
            }
        ]
        block = AgentAnswerBlock(
            kind="FACT",
            text="result/data/last: 123",
            evidence_refs=("req_stale_projection/result/data/last",),
        )
        receipts = [receipt]
    else:
        receipt = AgentToolReceipt(
            capability="market_data_get",
            operation="quotes",
            request_id="req_trimmed_projection",
            effect="READ_PROVIDER",
        )
        rows = [
            {"instrument_id": f"equity:US:SYNTH{index}", "source": "x" * 500, "last": "123"}
            for index in range(40)
        ]
        payloads = [{"receipt": receipt.as_dict(), "result": rows}]
        block = AgentAnswerBlock(
            kind="FACT",
            text="123",
            evidence_refs=("req_trimmed_projection/result/39/last",),
        )
        receipts = [receipt]

    guarded = guard_research_answer(
        AgentAnswerEnvelope(blocks=(block,)),
        receipts=receipts,
        tool_payloads=payloads,
    )
    assert guarded.presentation is not None
    assert not any(
        isinstance(segment, ResearchFieldSegment) for segment in guarded.presentation.segments
    )
    assert guarded.presentation.rendered_text() == render_agent_answer(guarded.envelope)


def test_model_cannot_supply_host_projection(scoped_evidence) -> None:
    receipt, payload = scoped_evidence
    entry = _entry(receipt, payload, "/positions/0/quantity")
    guarded = guard_research_answer(
        AgentAnswerEnvelope(
            blocks=(
                AgentAnswerBlock(
                    kind="INFERENCE",
                    text="数量是999。",
                    evidence_refs=(entry["ref"],),
                ),
            )
        ),
        receipts=[receipt],
        tool_payloads=[payload],
    )

    assert guarded.presentation is not None
    assert not any(
        isinstance(segment, ResearchFieldSegment) for segment in guarded.presentation.segments
    )
    assert "999" not in guarded.presentation.rendered_text()
