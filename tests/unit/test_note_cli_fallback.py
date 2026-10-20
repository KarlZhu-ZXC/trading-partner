from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from application.ports.agent_model_provider import ModelRequest, ModelResponse, ModelToolCall
from application.services.external_note_interpretation_service import (
    ExternalNoteInterpretationService,
)
from domain.common.errors import (
    DataContractError,
    ProviderAuthenticationError,
    ProviderNotConfigured,
    ProviderRateLimitError,
    ProviderRequestRejectedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from domain.external_note.enums import NoteCoverage
from domain.external_note.models import ExternalNoteRevision
from infrastructure.providers.llm.opencode_cli import OpenCodeCliNoteProvider
from infrastructure.system.clock import SystemClock
from infrastructure.system.id_generator import Uuid7IdGenerator


def _request() -> ModelRequest:
    return ModelRequest(
        messages=({"role": "user", "content": "synthetic private text"},),
        tools=(
            {
                "type": "function",
                "function": {
                    "name": "submit_note_interpretation",
                    "parameters": {"type": "object"},
                },
            },
        ),
    )


def _events(text: str = "{}", *, stop: bool = True) -> bytes:
    events = [{"type": "text", "part": {"text": text}}]
    if stop:
        events.append({"type": "step_finish", "part": {"reason": "stop"}})
    return "\n".join(json.dumps(x) for x in events).encode()


@pytest.mark.asyncio
async def test_cli_isolates_storage_credentials_and_sends_private_text_only_on_stdin(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "secret-test-key")
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "secret-test-password")
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", "unsafe inherited config")
    provider = OpenCodeCliNoteProvider(model="space-bunny-free")
    roots = []

    async def run(args, payload, root, env, timeout):
        roots.append(root)
        assert root.stat().st_mode & 0o077 == 0
        assert "OPENCODE_API_KEY" not in env
        assert "OPENCODE_SERVER_PASSWORD" not in env
        assert env["OPENCODE_TEST_HOME"] == str(root)
        assert all(
            env[f"XDG_{k}_HOME"].startswith(str(root)) for k in ("CONFIG", "DATA", "CACHE", "STATE")
        )
        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        assert config["permission"] == {"*": "ask"}
        assert config["mcp"] == {}
        assert config["share"] == "disabled"
        assert config["small_model"] == "opencode/space-bunny-free"
        assert "--auto" not in args
        assert "synthetic private text" not in " ".join(args)
        if args == ("--version",):
            return b"1.18.21\n"
        assert b"synthetic private text" in payload
        return _events()

    monkeypatch.setattr(provider, "_run", run)
    response = await provider.complete(_request())
    assert response.model == "space-bunny-free"
    assert response.tool_calls[0].name == "submit_note_interpretation"
    assert response.tool_calls[0].arguments == "{}"
    assert all(not p.exists() for p in roots)


@pytest.mark.asyncio
async def test_old_cli_never_receives_note_content(monkeypatch):
    provider = OpenCodeCliNoteProvider(model="space-bunny-free")
    calls = []

    async def run(args, payload, *rest):
        calls.append(payload)
        return b"1.17.9\n"

    monkeypatch.setattr(provider, "_run", run)
    with pytest.raises(ProviderNotConfigured):
        await provider.complete(_request())
    assert calls == [b""]


@pytest.mark.parametrize(
    "model", ["big-pickle", "muse-spark-1.3-contributor-free", "gpt-6-sol", "../bad"]
)
def test_cli_rejects_models_outside_private_note_allowlist(model):
    with pytest.raises(DataContractError):
        OpenCodeCliNoteProvider(model=model)


@pytest.mark.parametrize(
    "event,error",
    [
        (
            {"type": "error", "error": {"data": {"statusCode": 429, "message": "private text"}}},
            ProviderRateLimitError,
        ),
        (
            {"type": "error", "error": {"data": {"statusCode": 403, "message": "private text"}}},
            ProviderAuthenticationError,
        ),
        (
            {"type": "error", "error": {"data": {"message": "private text"}}},
            ProviderUnavailableError,
        ),
    ],
)
def test_cli_diagnostics_do_not_copy_provider_content(event, error):
    provider = OpenCodeCliNoteProvider(model="space-bunny-free")
    with pytest.raises(error) as caught:
        provider._response(json.dumps(event).encode())
    assert "private text" not in str(caught.value)
    details = caught.value.details
    assert details["stage"] == "model"
    assert details["failure_kind"] == "upstream_error"
    assert details["partial_output"] is False
    assert set(details) <= {
        "stage",
        "failure_kind",
        "partial_output",
        "status_code",
        "cli_error_type",
    }
    assert "private text" not in json.dumps(details)


def test_cli_rejects_partial_output_and_tool_operations():
    from domain.common.errors import ProviderRequestRejectedError

    provider = OpenCodeCliNoteProvider(model="space-bunny-free")
    with pytest.raises(ProviderUnavailableError):
        provider._response(_events(stop=False))
    with pytest.raises(ProviderRequestRejectedError) as caught:
        provider._response(b'{"type":"tool_use","part":{"tool":"bash","input":"private text"}}')
    assert caught.value.details == {
        "stage": "response",
        "failure_kind": "tool_operation",
        "partial_output": False,
    }
    assert caught.value.retryable is False
    assert "private text" not in json.dumps(caught.value.details)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_cli_timeout_and_cancellation_reap_the_child(tmp_path, cancel):
    import sys

    provider = OpenCodeCliNoteProvider(model="space-bunny-free", executable=sys.executable)
    pid_file = tmp_path / "child.pid"
    code = f'import os,time;open({str(pid_file)!r},"w").write(str(os.getpid()));time.sleep(30)'
    task = asyncio.create_task(
        provider._run(("-c", code), b"", tmp_path, dict(os.environ), 0.5 if not cancel else 10)
    )
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.01)
    assert pid_file.exists()
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else ProviderTimeoutError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


def _payload(wrong_author=False) -> dict:
    return {
        "change_relation": "NEW_THREAD",
        "material_change_summary": "首次解析",
        "viewpoints": [
            {
                "speaker_kind": "USER",
                "speaker_label": "WRONG" if wrong_author else "USER",
                "source_block_ordinals": [0],
                "summary": "只观察",
                "holding_horizon": "UNKNOWN",
                "direction": "UNKNOWN",
                "structure": "未给出",
            }
        ],
        "user_scenarios": [
            {
                "scenario": s,
                "action": "NO_ACTION",
                "condition": "原文未给出",
                "confirmation": "原文未给出",
                "loss_boundary": "原文未给出",
            }
            for s in ("UPSIDE", "SIDEWAYS", "PULLBACK", "INVALIDATION")
        ],
        "catalysts": [],
        "key_levels": [],
        "missing_evidence": [],
        "contradictions": [],
        "suggested_next_step": "NO_ACTION",
    }


def _revision():
    now = datetime.now(UTC)
    return ExternalNoteRevision(
        note_revision_id="external_note_revision_synthetic",
        note_id="external_note_synthetic",
        version=1,
        content_sha256="a" * 64,
        source_revision_key="synthetic",
        title="Synthetic",
        summary="",
        full_body="本人：四种情景只观察，不操作。",
        coverage=NoteCoverage.FULL,
        source_timestamp=now,
        observed_at=now,
        visibility="SELF",
        related_provider_stock_ids=(),
        related_provider_codes=(),
        blocks=(),
    )


class Provider:
    def __init__(self, error=None, wrong_author=False):
        self.error = error
        self.wrong_author = wrong_author
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return ModelResponse(
            tool_calls=(
                ModelToolCall(
                    id="synthetic_call",
                    name="submit_note_interpretation",
                    arguments=json.dumps(_payload(self.wrong_author)),
                ),
            )
        )


def _service(provider, name="opencode_go", fallback=None):
    return ExternalNoteInterpretationService(
        provider,
        provider_name=name,
        model="space-bunny-free" if name == "opencode_cli" else "deepseek-flash",
        reasoning_effort="provider_default" if name == "opencode_cli" else "max",
        clock=SystemClock(),
        id_generator=Uuid7IdGenerator(),
        fallback=fallback,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        ProviderRateLimitError("safe"),
        ProviderTimeoutError("safe"),
        ProviderUnavailableError("safe"),
        None,
    ],
)
async def test_fallback_preserves_context_validates_output_and_records_actual_source(error):
    primary = Provider(error=error, wrong_author=error is None)
    backup = Provider()
    service = _service(primary, fallback=_service(backup, "opencode_cli"))
    context = {"owner_clarifications": ["synthetic clarification"]}
    result = await service.analyze(_revision(), None, interpretation_context=context)
    assert result.status == "SUCCEEDED"
    assert result.provider == "opencode_cli"
    assert result.model == "space-bunny-free"
    assert result.reasoning_effort == "provider_default"
    assert len(backup.requests) == 1
    assert backup.requests[0].reasoning_effort is None
    assert backup.requests[0].session_id == primary.requests[0].session_id
    assert (
        json.loads(backup.requests[0].messages[1]["content"])["interpretation_context"] == context
    )
    assert len(primary.requests) == (2 if error is None else 1)


@pytest.mark.asyncio
async def test_invalid_fallback_author_is_never_accepted():
    backup = Provider(wrong_author=True)
    result = await _service(
        Provider(error=ProviderRateLimitError("safe")), fallback=_service(backup, "opencode_cli")
    ).analyze(_revision(), None)
    assert result.status == "FAILED"
    assert result.provider == "opencode_cli"
    assert result.error_code == "NOTE_INTERPRETATION_ATTRIBUTION_LABEL_CHANGED"
    assert len(backup.requests) == 2


@pytest.mark.asyncio
async def test_primary_success_auth_failure_and_cancellation_do_not_fallback():
    for error, status in [(None, "SUCCEEDED"), (ProviderAuthenticationError("safe"), "FAILED")]:
        backup = Provider()
        result = await _service(
            Provider(error=error), fallback=_service(backup, "opencode_cli")
        ).analyze(_revision(), None)
        assert result.status == status
        assert not backup.requests
    backup = Provider()
    with pytest.raises(asyncio.CancelledError):
        await _service(
            Provider(error=asyncio.CancelledError()), fallback=_service(backup, "opencode_cli")
        ).analyze(_revision(), None)
    assert not backup.requests


@pytest.mark.parametrize(
    "model", ["space-bunny-free", "deepseek-v4.1-flash-free", "glm-5.3-flash-free"]
)
def test_composition_enables_only_explicit_note_fallback(model):
    from composition_root.external_notes import build_external_note_services
    from domain.common.enums import AppEnvironment, LogLevel
    from infrastructure.config.settings import AppSettings

    for enabled in (False, True):
        settings = AppSettings(
            _env_file=None,
            app_name="tp",
            app_env=AppEnvironment.TEST,
            log_level=LogLevel.INFO,
            mcp_server_name="tp",
            default_timezone="UTC",
            provider_timeout_seconds=1,
            database_url="sqlite:///:memory:",
            opencode_go_api_key="test-key",
            moomoo_notes_remote_enabled=False,
            external_note_cli_fallback_enabled=enabled,
            external_note_cli_fallback_model=model,
            provider_proxy_url="http://proxy.invalid:3128",
        )
        bundle = build_external_note_services(
            settings=settings,
            repository=Mock(),
            review_repository=Mock(),
            research_uow_factory=Mock(),
            account_snapshots=Mock(),
            monitors=Mock(),
            clock=SystemClock(),
            id_generator=Uuid7IdGenerator(),
            resilience=Mock(),
        )
        assert (bundle.service._interpretation._fallback is not None) == enabled
        if enabled:
            fallback = bundle.service._interpretation._fallback
            assert fallback._provider.model == model
            assert fallback._provider._proxy_url == "http://proxy.invalid:3128"
    with pytest.raises(ValueError):
        AppSettings(
            _env_file=None, external_note_cli_fallback_model="muse-spark-1.3-contributor-free"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        'import sys;print("partial synthetic response");sys.exit(1)',
        'import sys;sys.stdout.write("x" * 2100000);sys.stdout.flush()',
    ],
)
async def test_cli_rejects_failed_processes_and_bounded_output(tmp_path, code):
    import sys

    provider = OpenCodeCliNoteProvider(model="space-bunny-free", executable=sys.executable)
    with pytest.raises(ProviderUnavailableError):
        await provider._run(("-c", code), b"", tmp_path, dict(os.environ), 5)


def test_cli_uses_configured_provider_proxy_and_preserves_explicit_ca(monkeypatch):
    proxy = "http://test-user:test-password@proxy.invalid:3128"
    monkeypatch.setenv("HTTPS_PROXY", "http://ambient.invalid:7890")
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "opencode.ai")
    monkeypatch.setenv("NODE_EXTRA_CA_CERTS", "/owner/custom-ca.pem")
    monkeypatch.setenv("NODE_TLS_REJECT_UNAUTHORIZED", "0")
    provider = OpenCodeCliNoteProvider(model="deepseek-v4.1-flash-free", proxy_url=proxy)
    env = provider._environment()
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        assert env[key] == proxy
    assert env["NO_PROXY"] == "localhost,127.0.0.1,::1"
    assert env["no_proxy"] == "localhost,127.0.0.1,::1"
    assert env["NODE_EXTRA_CA_CERTS"] == "/owner/custom-ca.pem"
    assert "NODE_TLS_REJECT_UNAUTHORIZED" not in env


def test_cli_keeps_ambient_proxy_when_no_provider_override(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://ambient.invalid:7890")
    monkeypatch.setenv("NO_PROXY", "localhost")
    env = OpenCodeCliNoteProvider(model="cat-jam-free")._environment()
    assert env["HTTPS_PROXY"] == "http://ambient.invalid:7890"
    assert env["NO_PROXY"] == "localhost"


@pytest.mark.parametrize("proxy", ["socks5://proxy.invalid:1234", "not-a-url"])
def test_cli_rejects_invalid_proxy_without_echoing_it(proxy):
    with pytest.raises(DataContractError) as caught:
        OpenCodeCliNoteProvider(model="cat-jam-free", proxy_url=proxy)
    assert proxy not in str(caught.value)


def test_cli_marks_tls_failures_without_blind_retries_or_raw_error_content(caplog):
    provider = OpenCodeCliNoteProvider(model="deepseek-v4.1-flash-free")
    event = {
        "type": "error",
        "error": {
            "name": "UnknownError",
            "data": {
                "message": "unknown certificate verification error",
                "body": "synthetic private text",
                "url": "https://user:password@private.invalid",
            },
        },
    }
    with pytest.raises(ProviderUnavailableError) as caught:
        provider._response(json.dumps(event).encode())
    assert caught.value.details == {
        "stage": "model",
        "failure_kind": "tls_verification",
        "partial_output": False,
        "cli_error_type": "UnknownError",
    }
    assert caught.value.retryable is False
    assert "synthetic private text" not in caplog.text
    assert "password" not in caplog.text


@pytest.mark.parametrize(
    "status,error",
    [
        (400, ProviderRequestRejectedError),
        (404, ProviderRequestRejectedError),
        (408, ProviderTimeoutError),
        (504, ProviderTimeoutError),
        (503, ProviderUnavailableError),
    ],
)
def test_cli_preserves_safe_http_status_and_rejection_type(status, error):
    provider = OpenCodeCliNoteProvider(model="cat-jam-free")
    event = {"type": "error", "error": {"name": "APIError", "data": {"statusCode": status}}}
    with pytest.raises(error) as caught:
        provider._response(json.dumps(event).encode())
    assert caught.value.details["status_code"] == status
    assert caught.value.retryable is (status >= 500 or status == 408)


def test_cli_distinguishes_incomplete_output_from_upstream_errors():
    provider = OpenCodeCliNoteProvider(model="cat-jam-free")
    with pytest.raises(ProviderUnavailableError) as caught:
        provider._response(_events(stop=False))
    assert caught.value.details == {
        "stage": "response",
        "failure_kind": "incomplete_output",
        "partial_output": True,
    }
    assert caught.value.retryable is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,budget",
    [
        ("deepseek-v4.1-flash-free", 32000),
        ("glm-5.3-flash-free", 32000),
        ("longcat-2.5-preview-free", 32000),
        ("cat-jam-free", 32000),
        ("space-bunny-free", 8000),
        ("step-5-preview-free", 8000),
    ],
)
async def test_cli_reserves_room_for_reasoning_without_changing_other_models(
    monkeypatch, model, budget
):
    provider = OpenCodeCliNoteProvider(model=model)

    async def run(args, payload, root, env, timeout):
        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        assert config["provider"]["opencode"]["models"][model]["limit"]["output"] == budget
        assert timeout <= 300
        return b"1.18.21\n" if args == ("--version",) else _events()

    monkeypatch.setattr(provider, "_run", run)
    await provider.complete(_request())


def test_cli_records_token_exhaustion_without_accepting_partial_json():
    provider = OpenCodeCliNoteProvider(model="deepseek-v4.1-flash-free")
    events = [
        {"type": "text", "part": {"text": '{"change_relation":'}},
        {"type": "step_finish", "part": {"reason": "length"}},
    ]
    with pytest.raises(ProviderUnavailableError) as caught:
        provider._response("\n".join(json.dumps(e) for e in events).encode())
    assert caught.value.details == {
        "stage": "response",
        "failure_kind": "token_limit",
        "partial_output": True,
        "finish_reason": "length",
    }
    assert caught.value.retryable is False
