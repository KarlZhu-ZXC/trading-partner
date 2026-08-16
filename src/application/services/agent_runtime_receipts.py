"""Pure Agent runtime result bounding, usage aggregation, and receipt rendering."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from application.ports.agent_model_provider import ModelResponse, ModelUsage


def bounded_tool_text(value: object, *, maximum_bytes: int) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    if len(encoded.encode("utf-8")) <= maximum_bytes:
        return encoded
    return json.dumps(
        {
            "ok": False,
            "error": {
                "code": "AGENT_TOOL_RESULT_TOO_LARGE",
                "message": "工具结果超过 Agent 上下文上限，请缩小查询范围。",
            },
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def tool_error(
    code: str,
    *,
    missing: list[str] | None = None,
    invalid: list[str] | None = None,
) -> dict[str, object]:
    error: dict[str, object] = {"code": code, "message": "工具调用未执行。"}
    if missing:
        error["missing"] = sorted(set(missing))[:32]
    if invalid:
        error["invalid"] = sorted(set(invalid))[:32]
    return {"ok": False, "error": error}


def aggregate_usage(responses: list[ModelResponse]) -> ModelUsage | None:
    usages = [item.usage for item in responses if item.usage is not None]
    if not usages:
        return None

    def total(field: str) -> int | None:
        values = [getattr(item, field) for item in usages]
        present = [item for item in values if item is not None]
        return sum(present) if present else None

    return ModelUsage(
        input_tokens=total("input_tokens"),
        output_tokens=total("output_tokens"),
        total_tokens=total("total_tokens"),
        web_search_calls=total("web_search_calls"),
        web_extractor_calls=total("web_extractor_calls"),
    )


def aggregate_latency(responses: list[ModelResponse]) -> int | None:
    values = [item.latency_ms for item in responses if item.latency_ms is not None]
    return sum(values) if values else None


def model_receipt_json(
    response: ModelResponse,
    responses: list[ModelResponse],
    tool_rounds: int,
    tool_trace: list[str],
    *,
    artifact_urls: list[str] | None = None,
    selected_provider_id: str | None = None,
    selected_model: str | None = None,
    route_reason: str | None = None,
    fallback_from: str | None = None,
    fallback_code: str | None = None,
    api_style: str | None = None,
    capability_search_audits: list[dict[str, object]] | None = None,
    evidence_manifest: str | None = None,
) -> str:
    usage = aggregate_usage(responses)
    latency_ms = aggregate_latency(responses)
    value: dict[str, Any] = {
        "model": response.model,
        "finish_reason": response.finish_reason,
        "model_calls": len(responses),
        "tool_rounds": tool_rounds,
        "usage": asdict(usage) if usage is not None else None,
        "web_search_used": any(item.web_search_used for item in responses),
        "web_extractor_used": any(item.web_extractor_used for item in responses),
        "web_source_urls": list(
            dict.fromkeys(url for item in responses for url in item.web_source_urls)
        )[:20],
        "request_id": response.request_id,
        "latency_ms": latency_ms,
        "model_attempts": [
            {
                "model": item.model,
                "finish_reason": item.finish_reason,
                "request_id": item.request_id,
                "latency_ms": item.latency_ms,
                "usage": asdict(item.usage) if item.usage is not None else None,
            }
            for item in responses[:8]
        ],
        "tool_trace": tool_trace[:32],
        "artifact_urls": list((artifact_urls or [])[:20]),
        "selected_provider_id": selected_provider_id,
        "selected_model": selected_model,
        "route_reason": route_reason,
        "fallback_from": fallback_from,
        "fallback_code": fallback_code,
        "api_style": api_style,
        "capability_search_audits": (capability_search_audits or [])[:16],
        "evidence_manifest": (
            json.loads(evidence_manifest) if isinstance(evidence_manifest, str) else None
        ),
    }
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) <= 16_384:
        return encoded

    manifest = value.get("evidence_manifest")
    if manifest is not None:
        raw_manifest = json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        value["evidence_manifest"] = {
            "version": "agent_evidence_v1",
            "truncated": True,
            "size_bytes": len(raw_manifest),
            "sha256": hashlib.sha256(raw_manifest).hexdigest(),
        }

    def bounded_list(key: str, limit: int) -> list[Any]:
        raw = value.get(key)
        return list(raw[:limit]) if isinstance(raw, list) else []

    value["capability_search_audits"] = bounded_list("capability_search_audits", 4)
    value["model_attempts"] = bounded_list("model_attempts", 2)
    value["tool_trace"] = bounded_list("tool_trace", 8)
    value["web_source_urls"] = bounded_list("web_source_urls", 4)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) <= 16_384:
        return encoded

    compact = {
        key: value.get(key)
        for key in (
            "model",
            "finish_reason",
            "model_calls",
            "tool_rounds",
            "usage",
            "web_search_used",
            "web_extractor_used",
            "request_id",
            "latency_ms",
            "selected_provider_id",
            "selected_model",
            "route_reason",
            "fallback_from",
            "fallback_code",
            "api_style",
            "artifact_urls",
            "evidence_manifest",
        )
    }
    return json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
