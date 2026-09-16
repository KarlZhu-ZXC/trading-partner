"""Synthetic current-turn research evidence regression gate."""

import json

import pytest

from application.dto.agent_answer import AgentAnswerBlock, AgentAnswerEnvelope
from application.ports.agent_tool_gateway import AgentToolReceipt
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
    "text,missing",
    [
        ("缺少的证据：（1）单季收入；（2）可比基线；（3）多期序列。", 0),
        ("Evidence: (1) quarterly revenue; (2) comparable periods.", 0),
        ("价格：（100）美元。", 1),
        ("缺少的证据：（1）单季收入；（2）价格为999。", 1),
        ("证据：（2）单季收入；（4）可比基线。", 1),
    ],
)
def test_colon_introduced_list_labels_are_not_amounts(text, missing):
    result = guard_research_answer(
        AgentAnswerEnvelope(blocks=(AgentAnswerBlock(kind="GAP", text=text),)),
        receipts=[],
        tool_payloads=[],
    )
    assert result.summary["missing_count"] == missing
    if not missing:
        assert result.envelope.blocks[0].text == text


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


def test_reference_rendering_and_missing_value_explanations_survive() -> None:
    from application.ports.agent_tool_gateway import AgentToolReceipt
    from application.services.copilot_research_evidence import research_evidence_catalog

    receipt = AgentToolReceipt(
        capability="portfolio_get",
        operation="performance",
        request_id="req_fees",
        effect="READ_DURABLE",
    )
    payload = {
        "receipt": receipt.as_dict(),
        "result": {
            "currency": "USD",
            "basis": "gross_before_fees",
            "realized_pnl": "80",
            "fees": None,
            "net_trading_pnl": None,
        },
    }
    catalog = research_evidence_catalog([receipt], [payload])
    assert any(e["ref"].endswith("/fees") and ": null" in e["text"] for e in catalog["entries"])
    assert all("confirmed=null" not in e["text"] for e in catalog["entries"])
    refs = ("req_fees/result/realized_pnl", "req_fees/result/fees")
    guarded = guard_research_answer(
        AgentAnswerEnvelope(
            blocks=(
                AgentAnswerBlock(kind="FACT", text="@evidence", evidence_refs=(refs[0],)),
                AgentAnswerBlock(
                    kind="GAP", text="缺少手续费，不能把费前收益当作净收益。", evidence_refs=refs
                ),
                AgentAnswerBlock(kind="INFERENCE", text="不能认定为已确认突破；缺少成交明细。"),
            )
        ),
        receipts=[receipt],
        tool_payloads=[payload],
    )
    assert guarded.summary["missing_count"] == 0
    assert guarded.summary["verified_count"] == 1
    fact = guarded.envelope.blocks[0]
    assert "80" in fact.text and fact.basis == "gross_before_fees"
    assert guarded.envelope.blocks[1].text.startswith("缺少手续费")


@pytest.mark.parametrize(
    "text", ["not filled; sold", "不能认定为已确认突破；已买入", "价格为 999", "截至 2030-01-01"]
)
def test_interpretation_cannot_smuggle_numbers_or_execution(text: str) -> None:
    guarded = guard_research_answer(
        AgentAnswerEnvelope(blocks=(AgentAnswerBlock(kind="INFERENCE", text=text),)),
        receipts=[],
        tool_payloads=[],
    )
    assert guarded.summary["missing_count"] == 1


def test_qualitative_comparison_keeps_known_paths_but_rejects_unbound_amounts() -> None:
    from application.ports.agent_tool_gateway import AgentToolReceipt
    from application.services.copilot_research_evidence import research_evidence_catalog

    receipt = AgentToolReceipt(
        capability="market_data_get",
        operation="quotes",
        request_id="req_compare",
        effect="READ_PROVIDER",
    )
    payload = {
        "receipt": receipt.as_dict(),
        "result": {
            "quotes": [
                {"last": "100", "currency": "USD", "quote_at": "2026-09-10T14:00:00Z"},
                {"last": "100", "currency": "JPY", "quote_at": "2026-09-10T13:00:00Z"},
            ]
        },
    }
    refs = ("req_compare/result/quotes/0/last", "req_compare/result/quotes/1/last")
    assert any(
        e["ref"].endswith("/quote_at")
        for e in research_evidence_catalog([receipt], [payload])["entries"]
    )
    good = "（1）quotes/0 和 quotes/1 的币种不同；（2）需汇率与时点对齐，无法直接比较。"
    guarded = guard_research_answer(
        AgentAnswerEnvelope(
            blocks=(
                AgentAnswerBlock(kind="INFERENCE", text=good, evidence_refs=refs),
                AgentAnswerBlock(kind="INFERENCE", text=good + "价格为 999。", evidence_refs=refs),
                AgentAnswerBlock(
                    kind="INFERENCE", text="quotes/99 的价格未知。", evidence_refs=refs
                ),
            )
        ),
        receipts=[receipt],
        tool_payloads=[payload],
    )
    assert guarded.envelope.blocks[0].text == good
    assert guarded.summary["missing_count"] == 2
    assert guarded.summary["verified_count"] == 0


@pytest.mark.parametrize("trailing_blocks", [0, 31])
def test_grouped_selection_renders_separate_facts_with_bounded_envelope(
    trailing_blocks: int,
) -> None:
    from application.ports.agent_tool_gateway import AgentToolReceipt

    receipt = AgentToolReceipt(
        capability="market_data_get",
        operation="quotes",
        request_id="req_group",
        effect="READ_PROVIDER",
    )
    payload = {
        "receipt": receipt.as_dict(),
        "result": {
            "quotes": [
                {"last": "100", "currency": "USD"},
                {"last": "200", "currency": "JPY"},
            ]
        },
    }
    block = AgentAnswerBlock(
        kind="FACT",
        text="@evidence",
        evidence_refs=(
            "req_group/result/quotes/0/last",
            "req_group/result/quotes/1/last",
        ),
    )
    guarded = guard_research_answer(
        AgentAnswerEnvelope(
            blocks=(
                block,
                *(AgentAnswerBlock(kind="GAP", text="待研究") for _ in range(trailing_blocks)),
            )
        ),
        receipts=[receipt],
        tool_payloads=[payload],
    )
    AgentAnswerEnvelope.model_validate(guarded.envelope.model_dump())
    if trailing_blocks:
        assert len(guarded.envelope.blocks) == 32
        assert guarded.summary["missing_count"] == 1
    else:
        first, second = guarded.envelope.blocks
        assert "100" in first.text and "currency=USD" in first.text
        assert "200" in second.text and "currency=JPY" in second.text
        assert guarded.summary["verified_count"] == 2


@pytest.mark.parametrize("suffix", ["", "；已执行买入"])
def test_unconfirmed_structure_explanation_is_not_an_execution_claim(suffix: str) -> None:
    text = "不能当作已确认突破。本包未提供将结构转为已确认所需的确认条件。" + suffix
    guarded = guard_research_answer(
        AgentAnswerEnvelope(blocks=(AgentAnswerBlock(kind="GAP", text=text),)),
        receipts=[],
        tool_payloads=[],
    )
    assert guarded.summary["missing_count"] == bool(suffix)
    if not suffix:
        assert guarded.envelope.blocks[0].text == text


@pytest.fixture
def inline_evidence():
    receipt = AgentToolReceipt(
        capability="portfolio_get",
        operation="positions",
        request_id="req_inline",
        effect="READ_DURABLE",
        source_codes=("SYNTHETIC",),
    )
    payload = {
        "receipt": receipt.as_dict(),
        "result": {
            "positions": [
                {
                    "instrument_id": "equity:US:SYNTH",
                    "account_ref": "synthetic_account",
                    "currency": "USD",
                    "quantity": "100",
                    "snapshot_at": "2026-09-15T12:00:00Z",
                    "basis": "broker_snapshot",
                },
                {
                    "instrument_id": "equity:US:OTHER",
                    "currency": "JPY",
                    "quantity": "100",
                    "snapshot_at": "2026-09-14T12:00:00Z",
                },
            ],
            "fees": None,
        },
    }
    return receipt, payload


@pytest.mark.parametrize("kind", ["INFERENCE", "SUMMARY", "GAP", "NEXT_STEP"])
def test_inline_fields_keep_explanation_and_each_fields_full_scope(inline_evidence, kind):
    receipt, payload = inline_evidence
    refs = (
        "req_inline/result/positions/0/quantity",
        "req_inline/result/positions/1/quantity",
        "req_inline/result/positions/0/snapshot_at",
        "req_inline/result/fees",
    )
    text = (
        f"观察到@evidence({refs[0]})与@evidence({refs[1]})；"
        f"快照日期为@evidence({refs[2]})，费用字段为@evidence({refs[3]})。"
        "币种与时点不同，不能合并判断。"
    )
    result = guard_research_answer(
        AgentAnswerEnvelope(blocks=(AgentAnswerBlock(kind=kind, text=text, evidence_refs=refs),)),
        receipts=[receipt],
        tool_payloads=[payload],
    )
    block = result.envelope.blocks[0]
    assert "币种与时点不同，不能合并判断。" in block.text
    assert "@evidence" not in block.text
    for expected in (
        "quantity: 100",
        "equity:US:SYNTH",
        "equity:US:OTHER",
        "currency=USD",
        "currency=JPY",
        "2026-09-15T12:00:00Z",
        "2026-09-14T12:00:00Z",
        "account_ref=synthetic_account",
        "basis=broker_snapshot",
        "fees: null",
    ):
        assert expected in block.text
    assert block.kind == ("INFERENCE" if kind == "SUMMARY" else kind)
    assert result.summary["verified_count"] == 0  # Interpretation is not a verified fact.
    assert result.summary["missing_count"] == 0
    AgentAnswerEnvelope.model_validate(result.envelope.model_dump())


@pytest.mark.parametrize(
    "text",
    [
        "数量为 100，@evidence(req_inline/result/positions/0/quantity)",
        "数量为100，@evidence(req_inline/result/positions/0/quantity)",
        "数量为@evidence(req_inline/result/positions/0/quantity)999",
        "截至 2030-01-01，@evidence(req_inline/result/positions/0/quantity)",
        "已买入@evidence(req_inline/result/positions/0/quantity)",
        "@evidence(req_inline/result/positions/99/quantity)",
        "@evidence(req_inline/result/positions/1/quantity)",  # Not selected by this block.
        "@evidence(req_old/result/positions/0/quantity)",
        "@evidence(req_inline/result/positions/0/quantity",  # Malformed.
        "@evidence()",
        "@evidence(req_inline/result/positions/0/quantity) https://example.test/forged",
    ],
)
def test_inline_fields_do_not_authorize_unbound_claims(inline_evidence, text):
    receipt, payload = inline_evidence
    result = guard_research_answer(
        AgentAnswerEnvelope(
            blocks=(
                AgentAnswerBlock(
                    kind="INFERENCE",
                    text=text,
                    evidence_refs=("req_inline/result/positions/0/quantity",),
                ),
            )
        ),
        receipts=[receipt],
        tool_payloads=[payload],
    )
    assert result.summary["missing_count"] == 1
    assert result.envelope.blocks[0].kind == "GAP"
    assert "@evidence" not in result.envelope.blocks[0].text


@pytest.mark.parametrize("total_limit", [False, True])
def test_inline_expansion_respects_block_and_envelope_bounds(inline_evidence, total_limit):
    receipt, payload = inline_evidence
    ref = "req_inline/result/positions/0/quantity"
    text = f"@evidence({ref})" + "解释" * (20 if total_limit else 1950)
    blocks = [AgentAnswerBlock(kind="INFERENCE", text=text, evidence_refs=(ref,))]
    if total_limit:
        blocks.extend(AgentAnswerBlock(kind="INFERENCE", text="解释" * 2000) for _ in range(15))
        blocks.append(
            AgentAnswerBlock(kind="INFERENCE", text="解释" * ((64_000 - 60_000 - len(text)) // 2))
        )
    result = guard_research_answer(
        AgentAnswerEnvelope(blocks=tuple(blocks)),
        receipts=[receipt],
        tool_payloads=[payload],
    )
    assert result.summary["missing_count"] == 1
    AgentAnswerEnvelope.model_validate(result.envelope.model_dump())


@pytest.mark.parametrize("request_id,missing", [("req_inline", 0), ("req_old", 1)])
def test_inline_path_is_an_exact_selection_without_redundant_ref_array(
    inline_evidence, request_id, missing
):
    receipt, payload = inline_evidence
    ref = f"{request_id}/result/positions/0/quantity"
    result = guard_research_answer(
        AgentAnswerEnvelope(
            blocks=(
                AgentAnswerBlock(
                    kind="INFERENCE",
                    text=f"持仓字段为@evidence({ref})，需结合风险判断。",
                ),
            )
        ),
        receipts=[receipt],
        tool_payloads=[payload],
    )
    assert result.summary["missing_count"] == missing
    if not missing:
        block = result.envelope.blocks[0]
        assert block.evidence_refs == (ref,)
        assert "quantity: 100" in block.text and "currency=USD" in block.text
        assert block.kind == "INFERENCE"
