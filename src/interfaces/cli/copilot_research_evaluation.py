"""Synthetic field-binding gate; no market, model, broker or filesystem writes."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from application.dto.agent_answer import AgentAnswerBlock, AgentAnswerEnvelope
from application.ports.agent_tool_gateway import AgentToolReceipt
from application.services.copilot_research_evidence import (
    guard_research_answer,
    research_evidence_catalog,
)

CATALOG_PATH = Path(__file__).resolve().parents[3] / "evals/copilot-research.v1.json"


def evaluate_research_evidence_case(case: dict[str, Any]) -> dict[str, object]:
    receipt = AgentToolReceipt(
        capability="market_data_get",
        operation="quote",
        request_id="req_synthetic",
        effect="READ_PROVIDER",
        source_codes=("SYNTHETIC",),
    )
    result: dict[str, object] = {
        "instrument_id": "equity:US:SYNTH",
        "currency": "USD",
        "as_of": "2026-08-13T12:00:00+00:00",
        "price_basis": "last",
        "last": "123.45",
        "previous_close": "120.00",
        "quantity": "7",
        "confirmed": False,
        "freshness": "fresh",
        "source_url": "https://example.test/source",
    }
    if case.get("fixture") == "stale":
        result["freshness"] = "stale"
        receipt = replace(receipt, degraded=True, warning_codes=("QUOTE_STALE",))
    payload: dict[str, Any] = {"receipt": receipt.as_dict(), "result": result}
    entries = research_evidence_catalog([receipt], [payload])["entries"]
    assert isinstance(entries, list)
    field = case.get("field", "last")
    entry = next(e for e in entries if e["ref"].endswith("/" + field))
    block_data: dict[str, Any] = {
        "kind": case.get("kind", "FACT"),
        "text": entry["text"],
        "evidence_refs": [entry["ref"]],
        "as_of": entry["as_of"],
        "basis": entry["basis"],
    }
    receipts = [receipt]
    payloads: list[object] = [payload]
    mutation = case.get("mutation", "none")
    if mutation == "text":
        block_data["text"] = case["text"]
    elif mutation == "replace_text":
        block_data["text"] = block_data["text"].replace(case["old"], case["new"])
    elif mutation == "ref":
        block_data["evidence_refs"] = [case["ref"]]
    elif mutation == "no_ref":
        block_data["evidence_refs"] = []
    elif mutation == "no_receipt":
        receipts = []
    elif mutation == "no_payload":
        payloads = []
    elif mutation == "receipt":
        receipts = [replace(receipt, **case["update"])]
    elif mutation == "payload_receipt":
        payload["receipt"].update(case["update"])
    elif mutation == "result":
        result.update(case["update"])
    elif mutation == "metadata":
        block_data.update(case["update"])
    elif mutation == "duplicate_payload":
        payloads.append(payload)
    elif mutation == "conflicting_payload":
        payloads.append({"receipt": receipt.as_dict(), "result": {**result, "last": "999"}})
    elif mutation == "multiple_ref":
        block_data["evidence_refs"].append("req_synthetic/result/quantity")
    else:
        if mutation != "none":
            raise ValueError("unknown research evaluation mutation")
    block = AgentAnswerBlock.model_validate(block_data)
    guarded = guard_research_answer(
        AgentAnswerEnvelope(blocks=(block,)),
        receipts=receipts,
        tool_payloads=payloads,
    )
    expected_gap = case["expected"] == "gap"
    actual_gap = guarded.summary["missing_count"] == 1
    failures = []
    if actual_gap != expected_gap:
        failures.append("EVIDENCE_GATE_OUTCOME_MISMATCH")
    if expected_gap and guarded.envelope.blocks[0].text == block.text:
        failures.append("UNSUPPORTED_PROSE_RELEASED")
    return {
        "case_id": case["id"],
        "category": case["category"],
        "passed": not failures,
        "failures": failures,
    }


def run_research_evidence_evaluations() -> list[dict[str, object]]:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"]
    if len(cases) != 40 or len({c["id"] for c in cases}) != 40:
        raise ValueError("research evidence catalog requires 40 unique synthetic cases")
    return [evaluate_research_evidence_case(case) for case in cases]
