"""Evaluator privacy, resumability and denominator contracts; no model calls."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from application.ports.agent_model_provider import ModelRequest
from domain.common.errors import ProviderUnavailableError

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts/evaluate_note_models.py"
_SPEC = importlib.util.spec_from_file_location("note_model_evaluation", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
evaluation = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(evaluation)


def test_contributor_requires_explicit_training_permission():
    selected = "gemini-4-argon-contributor:high"
    with pytest.raises(ValueError, match="Contributor"):
        evaluation.variant(selected)
    assert evaluation.variant(selected, allow_contributor=True) == (
        "gemini-4-argon-contributor",
        "high",
    )


@pytest.mark.asyncio
async def test_selected_go_cost_gate_runs_before_settings_or_network(monkeypatch):
    def forbidden():
        pytest.fail("Settings/network must not be accessed before the cost gate")

    monkeypatch.setattr(evaluation, "load_settings", forbidden)
    args = SimpleNamespace(
        corpus=evaluation.DEFAULT_CORPUS,
        authorize_private=False,
        provider="opencode_go",
        variant=["deepseek-v4.1-flash:high"],
        documented_protocols=True,
        concurrency=1,
        max_retries=0,
        live=True,
    )
    with pytest.raises(ValueError, match="extra usage"):
        await evaluation.evaluate(args)


@pytest.mark.asyncio
async def test_selected_zen_uses_zen_config_and_preserves_model_access_stop(tmp_path, monkeypatch):
    calls = []
    zen_config = SimpleNamespace(api_key="fixture-key", base_url="https://example.invalid")

    class Directory:
        def __init__(self, config, **kwargs):
            assert config is zen_config

        async def list_models(self, **kwargs):
            return SimpleNamespace(
                models=[SimpleNamespace(id="deepseek-v4.1-flash-free", reasoning_efforts=[])]
            )

        async def aclose(self):
            pass

    class Provider:
        def __init__(self, **kwargs):
            assert kwargs["provider"] == "opencode_zen"
            assert kwargs["api_key"] == "fixture-key"

        async def aclose(self):
            pass

    class Service:
        def __init__(self, *args, **kwargs):
            pass

        async def analyze(self, revision, *args, **kwargs):
            calls.append(revision.note_id)
            return SimpleNamespace(
                status="FAILED",
                error_code="PROVIDER_REQUEST_REJECTED",
                payload_json=None,
                reasoning_effort="high",
            )

    monkeypatch.setattr(
        evaluation,
        "load_settings",
        lambda: SimpleNamespace(
            resolved_external_note_analysis_config=None,
            resolved_agent_llm_configs={"opencode_zen": zen_config},
            provider_proxy_url=None,
        ),
    )
    monkeypatch.setattr(evaluation, "OpenCodeZenModelProvider", Directory)
    monkeypatch.setattr(evaluation, "EvaluationProtocolProvider", Provider)
    monkeypatch.setattr(evaluation, "ExternalNoteInterpretationService", Service)
    args = SimpleNamespace(
        corpus=evaluation.DEFAULT_CORPUS,
        authorize_private=False,
        provider="opencode_zen",
        variant=["deepseek-v4.1-flash-free:high"],
        documented_protocols=True,
        concurrency=1,
        max_retries=0,
        cases="entry_question,adopted_zone",
        repeats=1,
        timeout=300,
        live=True,
        run_dir=tmp_path / "run",
        retry_failed=False,
    )
    await evaluation.evaluate(args)
    await evaluation.evaluate(args)
    assert len(calls) == 1
    assert len(list(args.run_dir.glob("result-*.json"))) == 1
    summary = evaluation.summarize(args.run_dir)["groups"]["deepseek-v4.1-flash-free:high"]
    assert summary["pending_jobs"] == 1


@pytest.mark.asyncio
async def test_observer_preserves_safe_failure_details_without_payloads():
    error = ProviderUnavailableError(
        "secret response must not be retained",
        details={
            "status_code": 503,
            "attempts": 2,
            "error_type": "ProxyError",
            "body": "private note",
            "url": "https://secret.invalid",
        },
    )

    class FailingProvider:
        async def complete(self, request):
            raise error

    observed = evaluation.ObservedProvider(FailingProvider())
    with pytest.raises(ProviderUnavailableError) as captured:
        await observed.complete(ModelRequest())
    assert captured.value is error
    assert observed.failures == [
        {
            "provider_call": 1,
            "retryable": True,
            "http_status": 503,
            "transport_attempts": 2,
            "transport_error": "ProxyError",
        }
    ]
    assert observed.calls == 1
    assert observed.usage == []


@pytest.mark.asyncio
async def test_observer_retains_reviewable_requests_and_responses_separately_from_diagnostics():
    from application.ports.agent_model_provider import ModelResponse

    class Provider:
        async def complete(self, request):
            return ModelResponse(text="reviewable response", model="model")

    observed = evaluation.ObservedProvider(Provider())
    await observed.complete(ModelRequest(messages=({"role": "user", "content": "case input"},)))
    assert observed.request_snapshots[0]["messages"][0]["content"] == "case input"
    assert observed.response_snapshots[0]["text"] == "reviewable response"
    assert observed.failures == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "selected", "direct", "message"),
    [
        ("opencode_cli", "deepseek-v4.1-flash-free:high", False, "provider_default"),
        ("opencode_cli", "unapproved:provider_default", False, "allowlisted"),
        ("opencode_go", "deepseek-flash:provider_default", False, "explicit"),
        ("opencode_cli", "deepseek-v4.1-flash-free:provider_default", True, "Go-only"),
    ],
)
async def test_provider_selection_rejects_unsupported_or_misleading_evaluation_conditions(
    provider, selected, direct, message
):
    args = SimpleNamespace(
        corpus=evaluation.DEFAULT_CORPUS,
        authorize_private=False,
        provider=provider,
        variant=[selected],
        direct=direct,
    )
    with pytest.raises(ValueError, match=message):
        await evaluation.evaluate(args)


@pytest.mark.asyncio
async def test_recovered_format_error_is_recorded_without_repair_text():
    from application.ports.agent_model_provider import ModelMessage, ModelResponse

    class Provider:
        async def complete(self, request):
            return ModelResponse()

    observed = evaluation.ObservedProvider(Provider())
    await observed.complete(ModelRequest(messages=({"role": "user", "content": "private"},)))
    await observed.complete(
        ModelRequest(
            messages=(
                ModelMessage(
                    role="user",
                    content="上一次提交未被接受（NOTE_INTERPRETATION_TOOL_CALL_MISSING）。"
                    "private response must not be recorded",
                ),
            )
        )
    )
    await observed.complete(
        ModelRequest(messages=({"role": "user", "content": "private NOTE_INTERPRETATION_BAD"},))
    )
    assert observed.repair_errors == [
        {"provider_call": 1, "error_code": "NOTE_INTERPRETATION_TOOL_CALL_MISSING"}
    ]
    assert "private" not in json.dumps(observed.repair_errors)


def test_failure_details_reject_unbounded_diagnostics():
    error = ProviderUnavailableError(
        "private",
        details={
            "status_code": True,
            "attempts": 999,
            "error_type": "secret arbitrary text",
            "request": "private",
        },
    )
    assert evaluation.safe_failure_details(error) == {
        "retryable": True,
        "transport_error": "OTHER",
    }


def test_synthetic_corpus_has_reviewable_cases():
    corpus = evaluation.load_corpus(evaluation.DEFAULT_CORPUS)
    assert len(corpus["cases"]) >= 16
    assert {"take_profit_plan", "adopted_zone", "negative_adoption", "malformed_prices"} <= {
        case["id"] for case in corpus["cases"]
    }


def test_private_read_needs_authorization_and_owner_permissions(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "corpus.json"
    corpus = evaluation.load_corpus(evaluation.DEFAULT_CORPUS)
    corpus["classification"] = "private"
    path.write_text(json.dumps(corpus))
    path.chmod(0o600)
    with pytest.raises(ValueError, match="authorize-private"):
        evaluation.load_corpus(path)
    assert evaluation.load_corpus(path, authorized=True)["classification"] == "private"
    path.chmod(0o644)
    with pytest.raises(ValueError, match="owner-only"):
        evaluation.load_corpus(path, authorized=True)


def test_exact_action_check_never_claims_semantic_accuracy():
    case = {"expected_actions": {"UPSIDE": ["REDUCE"]}}
    value = evaluation.mechanical_checks(
        {"user_scenarios": [{"scenario": "UPSIDE", "action": "REVIEW"}]}, case
    )
    assert value["failed_scenarios"] == ["UPSIDE"]
    value = evaluation.mechanical_checks(
        {"user_scenarios": [{"scenario": "UPSIDE", "action": "REDUCE"}]}, case
    )
    assert value["failed_scenarios"] == []
    assert value["semantic_fidelity"] == "REQUIRES_SOURCE_REVIEW"


def test_resume_refuses_changed_inputs_and_writes_owner_only(tmp_path):
    run = tmp_path / "run"
    manifest = {"corpus_hash": "original", "effort": "high"}
    evaluation.prepare_run(run, manifest)
    evaluation.prepare_run(run, manifest)
    assert (run / "manifest.json").stat().st_mode & 0o077 == 0
    with pytest.raises(ValueError, match="inputs changed"):
        evaluation.prepare_run(run, {**manifest, "effort": "max"})


def test_single_writer_lock_prevents_duplicate_live_runs(tmp_path):
    with (
        evaluation.run_lock(tmp_path / "run"),
        pytest.raises(BlockingIOError),
        evaluation.run_lock(tmp_path / "run"),
    ):
        pass


def test_failure_stays_in_denominator_and_retry_history(tmp_path):
    evaluation.write_json(
        tmp_path / "manifest.json",
        {"cases": ["a", "b", "c"], "repeats": 1, "variants": [["model", "high"]]},
    )
    base = {
        "model": "model",
        "effort": "high",
        "attempt": 1,
        "seconds": 300,
        "status": "FAILED",
        "error_code": "PROVIDER_TIMEOUT_ERROR",
    }
    evaluation.write_json(tmp_path / "result-a-1.json", {**base, "job_id": "a"})
    evaluation.write_json(tmp_path / "result-b-1.json", {**base, "job_id": "b"})
    evaluation.write_json(
        tmp_path / "result-b-2.json",
        {
            **base,
            "job_id": "b",
            "attempt": 2,
            "status": "SUCCEEDED",
            "mechanical": {"failed_scenarios": []},
        },
    )
    evaluation.write_json(tmp_path / "reviews.json", {"results": {"b": {"verdict": "PASS"}}})
    group = evaluation.summarize(tmp_path)["groups"]["model:high"]
    assert group["planned_jobs"] == 3
    assert group["incapable_jobs"] == 1
    assert group["pending_jobs"] == 1
    assert group["failed_attempts"] == 2
    assert group["recovered_jobs"] == 1
    assert group["end_to_end_pass_fraction"] == "1/3"
    assert group["assessment_complete"] is False


def test_error_ledger_keeps_recovered_error_without_raw_payload(tmp_path):
    evaluation.write_json(
        tmp_path / "result-a-001.json",
        {
            "job_id": "a",
            "status": "SUCCEEDED",
            "payload": {"text": "private note"},
            "repair_errors": [
                {"provider_call": 1, "error_code": "NOTE_INTERPRETATION_JSON_INVALID"}
            ],
        },
    )
    evaluation.write_json(
        tmp_path / "result-b-001.json",
        {
            "job_id": "b",
            "status": "FAILED",
            "error_code": "PROVIDER_RATE_LIMIT_ERROR",
            "failure_diagnostics": [
                {"provider_call": 1, "http_status": 429, "body": "private provider response"}
            ],
        },
    )
    evaluation.write_json(
        tmp_path / "reviews.json",
        {
            "results": {
                "a": {"verdict": "PASS", "auxiliary_findings": ["minor wording"]},
                "b": {"verdict": "FAIL", "findings": ["unsupported action"]},
            }
        },
    )
    ledger = evaluation.error_ledger(tmp_path)
    assert [e["kind"] for e in ledger["events"]] == [
        "OUTPUT_REJECTED_BEFORE_REPAIR",
        "ATTEMPT_FAILED",
        "PROVIDER_CALL_FAILED",
        "AUXILIARY_CONTENT_DEFECT",
        "CONTENT_REVIEW_FAILED",
    ]
    assert ledger["events"][0]["attempt_recovered"] is True
    assert "private note" not in json.dumps(ledger)
    assert "private provider response" not in json.dumps(ledger)


def test_request_hash_supports_repair_message_dataclasses():
    from application.ports.agent_model_provider import ModelMessage

    assert evaluation.digest({"messages": (ModelMessage(role="user", content="repair"),)})


def test_contributor_and_invalid_effort_are_not_silently_sent():
    with pytest.raises(ValueError):
        evaluation.variant("test-contributor:high")
    with pytest.raises(ValueError):
        evaluation.variant("model:extreme")


@pytest.mark.asyncio
@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize(
    ("failures", "code", "expected"),
    [
        (3, "PROVIDER_UNAVAILABLE_ERROR", 4),
        (8, "PROVIDER_TIMEOUT_ERROR", 4),
        (8, "NOTE_INTERPRETATION_INVALID_OUTPUT", 1),
        (8, "PROVIDER_AUTHENTICATION_ERROR", 1),
        (8, "PROVIDER_RATE_LIMIT_ERROR", 1),
    ],
)
async def test_bounded_retry_keeps_attempts_and_resume_cannot_reset_budget(
    tmp_path, monkeypatch, failures, code, expected, direct
):
    from dataclasses import dataclass
    from types import SimpleNamespace

    @dataclass
    class Config:
        base_url: str = "https://example.invalid"
        model: str = "model"
        timeout_seconds: float = 300
        reasoning_effort: str = "high"

    calls = 0
    delays = []
    routes = []

    class Provider:
        def __init__(self, *args, **kwargs):
            routes.append(kwargs)

        async def list_models(self, **kwargs):
            return SimpleNamespace(models=[SimpleNamespace(id="model", reasoning_efforts=["high"])])

        async def aclose(self):
            pass

    class Service:
        def __init__(self, *args, **kwargs):
            pass

        async def analyze(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            assert kwargs["interpretation_context"] == {"owner_clarifications": ["已成交"]}
            failed = calls <= failures
            return SimpleNamespace(
                status="FAILED" if failed else "SUCCEEDED",
                error_code=code if failed else None,
                payload_json='{"user_scenarios": []}',
                reasoning_effort="high",
            )

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(
        evaluation,
        "load_settings",
        lambda: SimpleNamespace(
            resolved_external_note_analysis_config=Config(),
            provider_proxy_url="http://proxy.invalid",
        ),
    )
    monkeypatch.setattr(evaluation, "OpenCodeGoModelProvider", Provider)
    monkeypatch.setattr(evaluation, "ExternalNoteInterpretationService", Service)
    monkeypatch.setattr(evaluation.asyncio, "sleep", sleep)
    corpus = evaluation.load_corpus(evaluation.DEFAULT_CORPUS)
    for case in corpus["cases"]:
        case["interpretation_context"] = {"owner_clarifications": ["已成交"]}
    corpus_path = tmp_path / "context-corpus.json"
    tmp_path.chmod(0o700)
    evaluation.write_json(corpus_path, corpus)
    args = SimpleNamespace(
        corpus=corpus_path,
        authorize_private=True,
        variant=["model:high"],
        cases=(
            "entry_question,adopted_zone"
            if code == "PROVIDER_RATE_LIMIT_ERROR"
            else "entry_question"
        ),
        repeats=1,
        concurrency=1,
        timeout=300,
        max_retries=3,
        direct=direct,
        live=True,
        run_dir=tmp_path / "run",
        retry_failed=False,
    )
    await evaluation.evaluate(args)
    assert calls == expected
    assert all(r["trust_env"] is not direct for r in routes)
    assert all(r["proxy_url"] == (None if direct else "http://proxy.invalid") for r in routes)
    assert len(list(args.run_dir.glob("result-*.json"))) == expected
    assert delays == [1, 2, 4][: expected - 1]
    if code == "PROVIDER_RATE_LIMIT_ERROR":
        assert json.loads((args.run_dir / "pause.json").read_text())["status"] == (
            "BLOCKED_PROVIDER_RATE_LIMIT"
        )
        summary = evaluation.summarize(args.run_dir)["groups"]["model:high"]
        assert summary["planned_jobs"] == 2
        assert summary["incapable_jobs"] == 1
        assert summary["pending_jobs"] == 1
        return
    args.retry_failed = True
    await evaluation.evaluate(args)
    assert calls == expected
    summary = evaluation.summarize(args.run_dir)["groups"]["model:high"]
    assert summary["failed_attempts"] == min(failures, expected)
    assert summary["recovered_jobs"] == (1 if failures < expected else 0)
