"""Synthetic current-turn research evidence regression gate."""

import json

import pytest

from application.dto.agent_answer import AgentAnswerBlock, AgentAnswerEnvelope
from application.services.copilot_research_evidence import guard_research_answer
from interfaces.cli.copilot_research_evaluation import (
    CATALOG_PATH,
    evaluate_research_evidence_case,
    run_research_evidence_evaluations,
)

CASES = json.loads(CATALOG_PATH.read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_research_evidence_gate(case: dict[str, object]) -> None:
    result = evaluate_research_evidence_case(case)
    assert result["passed"], result


def test_all_forty_evaluation_cases_pass() -> None:
    results = run_research_evidence_evaluations()
    assert len(results) == 40
    assert all(result["passed"] for result in results)


def test_no_facts_does_not_claim_verified() -> None:
    guarded = guard_research_answer(
        AgentAnswerEnvelope(blocks=(AgentAnswerBlock(kind="SUMMARY", text="仍需研究。"),)),
        receipts=[],
        tool_payloads=[],
    )
    assert guarded.summary["status"] == "NOT_CHECKED"
    assert guarded.summary["verified_count"] == 0


@pytest.mark.parametrize(
    ("quote_time", "nested_as_of", "allowed"),
    [
        ("2026-08-14T12:00:00+00:00", None, False),
        ("2026-08-14T12:00:00+00:00", "2026-08-14T12:00:00+00:00", False),
        ("2026-08-13T21:00:00+08:00", None, False),
        ("2026-08-13T20:00:00+08:00", None, True),
        ("2026-08-13T19:00:00+08:00", None, True),
        ("2026-08-13T11:00:00+00:00", "2026-08-13T10:00:00+00:00", False),
    ],
)
def test_outer_cutoff_survives_nested_quote_context(
    quote_time: str,
    nested_as_of: str | None,
    allowed: bool,
) -> None:
    from application.ports.agent_tool_gateway import AgentToolReceipt
    from application.services.copilot_research_evidence import research_evidence_catalog

    receipt = AgentToolReceipt(
        capability="market_data_get",
        operation="quote",
        request_id="req_cutoff",
        effect="READ_PROVIDER",
    )
    quote = {"quote_at": quote_time, "last": "123.45"}
    if nested_as_of:
        quote["as_of"] = nested_as_of
    payload = {
        "receipt": receipt.as_dict(),
        "result": {"as_of": "2026-08-13T12:00:00+00:00", "data": quote},
    }
    catalog = research_evidence_catalog([receipt], [payload])
    refs = {entry["ref"] for entry in catalog["entries"]}
    assert ("req_cutoff/result/data/last" in refs) is allowed


def test_catalog_byte_bound_and_guard_use_identical_clipped_refs() -> None:
    from application.ports.agent_tool_gateway import AgentToolReceipt
    from application.services.copilot_research_evidence import research_evidence_catalog

    receipt = AgentToolReceipt(
        capability="market_data_get",
        operation="quotes",
        request_id="req_large",
        effect="READ_PROVIDER",
    )
    rows = [
        {
            "instrument_id": f"equity:US:SYNTH{index}",
            "source": "合成来源" * 35,
            "last": "123.45",
            "currency": "USD",
        }
        for index in range(40)
    ]
    payload = {"receipt": receipt.as_dict(), "result": rows}
    catalog = research_evidence_catalog([receipt], [payload])
    assert len(json.dumps(catalog).encode()) <= 24 * 1024
    assert len(json.dumps(catalog, ensure_ascii=False).encode()) <= 24 * 1024
    assert catalog["truncated"] is True
    assert catalog["omitted_count"] > 0
    missing = next(
        f"req_large/result/{index}/last"
        for index in range(40)
        if f"req_large/result/{index}/last" not in {e["ref"] for e in catalog["entries"]}
    )
    guarded = guard_research_answer(
        AgentAnswerEnvelope(
            blocks=(
                AgentAnswerBlock(
                    kind="FACT",
                    text="123.45",
                    evidence_refs=(missing,),
                ),
            )
        ),
        receipts=[receipt],
        tool_payloads=[payload],
    )
    assert guarded.summary["missing_count"] == 1
    assert guarded.summary["codes"] == ["RESEARCH_EVIDENCE_REF_MISSING"]


def test_financial_scale_period_and_account_context_remain_bound() -> None:
    from application.ports.agent_tool_gateway import AgentToolReceipt
    from application.services.copilot_research_evidence import research_evidence_catalog

    receipt = AgentToolReceipt(
        capability="portfolio_get",
        operation="positions",
        request_id="req_scope",
        effect="READ_DURABLE",
    )
    payload = {
        "receipt": receipt.as_dict(),
        "result": {
            "data": {
                "account_ref": "synthetic_ira",
                "currency": "USD",
                "unit": "millions",
                "metric_code": "revenue",
                "period_end": "2026-06-30",
                "value": "100",
            }
        },
    }
    entry = next(
        item
        for item in research_evidence_catalog([receipt], [payload])["entries"]
        if item["ref"].endswith("/value")
    )
    assert "unit=millions" in entry["text"]
    assert "account_ref=synthetic_ira" in entry["text"]
    assert "period_end=2026-06-30" in entry["text"]
    guarded = guard_research_answer(
        AgentAnswerEnvelope(
            blocks=(
                AgentAnswerBlock(
                    kind="FACT",
                    text=entry["text"].replace("unit=millions", "unit=USD"),
                    evidence_refs=(entry["ref"],),
                    as_of=entry["as_of"],
                    basis=entry["basis"],
                ),
            )
        ),
        receipts=[receipt],
        tool_payloads=[payload],
    )
    assert guarded.summary["missing_count"] == 1
