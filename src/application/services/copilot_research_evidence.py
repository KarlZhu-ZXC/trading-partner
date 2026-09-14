"""Closed, current-turn evidence binding for Copilot research answers.

Fact prose is deliberately host-rendered: a model selects a field reference and
copies its catalog text. Matching a bag of numbers cannot establish which asset,
field or time a claim describes. The catalog is model context, never diagnostics.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TypedDict

from application.dto.agent_answer import (
    AgentAnswerBlock,
    AgentAnswerBlockKind,
    AgentAnswerEnvelope,
)
from application.ports.agent_tool_gateway import AgentToolReceipt

_SAFE_PATH = re.compile(r"^[A-Za-z0-9_.:-]+$")
_NUMERIC = re.compile(r"(?<!\w)[+-]?\d+(?:[.,]\d+)*(?:%)?")
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:T[\d:.+Z-]+)?\b")
_EXECUTED = re.compile(
    r"已(?:成交|下单|执行|确认|买入|卖出|撤单)|"
    r"\b(?:executed|submitted|filled|purchased|sold|confirmed breakout)\b|"
    r"(?:突破|结构)(?:已经|已)?确认",
    re.IGNORECASE,
)
_CONTEXT_KEYS = (
    "instrument_id",
    "account_ref",
    "metric_code",
    "unit",
    "units",
    "scale",
    "period_end",
    "period_type",
    "adjustment",
    "currency",
    "as_of",
    "observed_at",
    "timestamp",
    "published_at",
    "quote_at",
    "snapshot_at",
    "source_as_of",
    "price_basis",
    "basis",
    "freshness",
    "source",
    "confirmed",
    "confirmed_at",
    "status",
    "direction",
)
# Only closed structured fact fields: no note bodies, prompts, descriptions or
# arbitrary string IDs become factual evidence merely because a tool returned them.
_FACT_FIELDS = frozenset(
    {
        "last",
        "display_price",
        "bid",
        "ask",
        "open",
        "high",
        "low",
        "close",
        "previous_close",
        "volume",
        "change",
        "change_pct",
        "quantity",
        "price",
        "market_value",
        "cost_basis",
        "average_cost",
        "current_average_cost",
        "unrealized_pnl",
        "realized_pnl",
        "net_trading_pnl",
        "dividend_income",
        "total_pnl",
        "revenue",
        "net_income",
        "operating_income",
        "free_cash_flow",
        "total_assets",
        "total_liabilities",
        "equity",
        "eps",
        "pe_ratio",
        "rsi",
        "atr",
        "ema",
        "sma",
        "value",
        "status",
        "confirmed",
        "confirmed_at",
        "confirmation_time",
        "occurred_at",
        "direction",
        "level",
        "upper",
        "lower",
        "strength",
        "freshness",
        "price_basis",
        "currency",
        "instrument_id",
        "as_of",
        "observed_at",
        "published_at",
        "timestamp",
        "basis",
        "source",
        "url",
        "source_url",
    }
)
_READ_EFFECTS = frozenset({"READ_DURABLE", "READ_PROVIDER"})
_MAX_ENTRIES = 96
_MAX_CATALOG_BYTES = 24 * 1024


class EvidenceEntry(TypedDict):
    ref: str
    text: str
    as_of: str | None
    basis: str | None
    source_urls: list[str]


class EvidenceCatalog(TypedDict):
    schema_version: int
    entries: list[EvidenceEntry]
    truncated: bool
    omitted_count: int


@dataclass(frozen=True, slots=True)
class ResearchEvidenceResult:
    envelope: AgentAnswerEnvelope
    summary: dict[str, object]


def _scalar(value: object) -> str | None:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, Decimal)):
        return str(value)[:100]
    if isinstance(value, str) and 0 < len(value) <= 160:
        return value
    return None


def _aware_time(value: str) -> datetime | None:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
    except ValueError:
        return None


def _catalog_entries(
    receipts: Sequence[AgentToolReceipt],
    tool_payloads: Sequence[object],
) -> tuple[list[EvidenceEntry], int, bool]:
    current = {
        (r.request_id, r.capability, r.operation): r
        for r in receipts[:32]
        if r.request_id
        and not r.error_code
        and not r.result_truncated
        and (r.effect in _READ_EFFECTS or r.capability == "agent_web_search")
    }
    entries: dict[str, EvidenceEntry] = {}
    omitted_count = 0
    traversal_truncated = len(receipts) > 32 or len(tool_payloads) > 32
    duplicate_requests: set[str] = set()
    seen_payloads: dict[str, str] = {}
    for payload in tool_payloads[:32]:
        if not isinstance(payload, Mapping) or payload.get("ok") is False:
            continue
        meta = payload.get("receipt")
        if not isinstance(meta, Mapping):
            continue
        key = (meta.get("request_id"), meta.get("capability"), meta.get("operation"))
        if not all(isinstance(part, (str, type(None))) for part in key):
            continue
        request, capability, operation = key
        if not isinstance(request, str) or not isinstance(capability, str):
            continue
        if operation is not None and not isinstance(operation, str):
            continue
        receipt = current.get((request, capability, operation))
        if receipt is None or meta.get("error_code") or meta.get("result_truncated"):
            continue
        if any(meta.get(name) != value for name, value in receipt.as_dict().items()):
            continue
        request_id = receipt.request_id
        assert request_id is not None
        if not _SAFE_PATH.fullmatch(request_id):
            continue
        result = payload.get("result")
        if isinstance(result, Mapping) and (
            result.get("ok") is False or result.get("error") or result.get("errors")
        ):
            continue
        fingerprint = json.dumps(result, sort_keys=True, default=str)
        if request_id in seen_payloads and seen_payloads[request_id] != fingerprint:
            duplicate_requests.add(request_id)
            continue
        seen_payloads[request_id] = fingerprint
        stack: list[tuple[object, str, dict[str, str], int, datetime | None]] = [
            (result, "result", {}, 0, None)
        ]
        visited = 0
        while stack and visited < 2048:
            value, path, context, depth, cutoff = stack.pop()
            visited += 1
            if depth > 10:
                traversal_truncated = True
                continue
            if isinstance(value, Mapping):
                if value.get("_truncated") or value.get("error") or value.get("ok") is False:
                    continue
                declared = value.get("as_of")
                declared_time = _aware_time(declared) if isinstance(declared, str) else None
                if declared_time is not None:
                    if cutoff is not None and declared_time > cutoff:
                        continue
                    cutoff = min(cutoff, declared_time) if cutoff else declared_time
                # Enforce the inherited query cutoff before children can override
                # their local quote/source timestamps with another nested context.
                source_times = [
                    _aware_time(value[name])
                    for name in (
                        "published_at",
                        "observed_at",
                        "timestamp",
                        "quote_at",
                        "snapshot_at",
                        "source_as_of",
                    )
                    if isinstance(value.get(name), str)
                ]
                if cutoff is not None and any(t is not None and t > cutoff for t in source_times):
                    continue
                traversal_truncated |= len(value) > 128
                next_context = dict(context)
                for name in _CONTEXT_KEYS:
                    scalar = _scalar(value.get(name))
                    if scalar is not None:
                        next_context[name] = scalar
                for name, child in reversed(list(value.items())[:128]):
                    if isinstance(name, str) and _SAFE_PATH.fullmatch(name):
                        if name in {"body", "text", "prompt", "description", "content", "summary"}:
                            continue
                        stack.append((child, f"{path}/{name}", next_context, depth + 1, cutoff))
                continue
            if isinstance(value, (list, tuple)):
                traversal_truncated |= len(value) > 96
                for index, child in reversed(list(enumerate(value[:96]))):
                    stack.append((child, f"{path}/{index}", context, depth + 1, cutoff))
                continue
            leaf = path.rsplit("/", 1)[-1]
            if leaf.isdigit() and path.rsplit("/", 2)[-2] == "source_urls":
                leaf = "source_url"
            scalar = _scalar(value)
            ref = f"{request_id}/{path}"
            if scalar is None or leaf not in _FACT_FIELDS or len(ref) > 160:
                continue
            # Web pages are background sources, never canonical price/position facts.
            is_web = receipt.capability == "agent_web_search"
            if is_web and leaf not in {"url", "source_url", "published_at"}:
                continue
            if leaf not in _CONTEXT_KEYS and leaf not in {
                "status",
                "confirmed",
                "confirmed_at",
                "confirmation_time",
                "occurred_at",
                "direction",
                "url",
                "source_url",
            }:
                try:
                    number = Decimal(scalar.rstrip("%"))
                    if not number.is_finite():
                        continue
                except InvalidOperation:
                    continue
            as_of = (
                context.get("quote_at")
                or context.get("snapshot_at")
                or context.get("source_as_of")
                or context.get("observed_at")
                or context.get("timestamp")
                or context.get("as_of")
            )
            times = [
                _aware_time(context[k])
                for k in ("published_at", "observed_at", "timestamp", "quote_at", "snapshot_at")
                if k in context
            ]
            claim_time = _aware_time(as_of) if as_of else None
            limits = [item for item in (cutoff, claim_time) if item is not None]
            if limits and any(t is not None and t > min(limits) for t in times):
                continue
            basis = context.get("price_basis") or context.get("basis")
            metadata = dict(context)
            metadata["sources"] = ",".join(receipt.source_codes)
            metadata["warnings"] = ",".join(receipt.warning_codes)
            metadata["degraded"] = str(receipt.degraded).lower()
            text = f"{path}: {scalar}"
            if metadata:
                text += " · " + " · ".join(f"{k}={v}" for k, v in metadata.items() if v)
            if len(text) > 4000:
                continue
            urls = (
                [scalar]
                if leaf in {"url", "source_url"} and scalar.startswith(("https://", "http://"))
                else []
            )
            if ref not in entries and len(entries) >= _MAX_ENTRIES:
                omitted_count += 1
                continue
            entries[ref] = {
                "ref": ref,
                "text": text,
                "as_of": as_of,
                "basis": basis,
                "source_urls": urls,
            }
        traversal_truncated |= bool(stack)
    return (
        [entry for ref, entry in entries.items() if ref.split("/", 1)[0] not in duplicate_requests],
        omitted_count,
        traversal_truncated,
    )


def research_evidence_catalog(
    receipts: Sequence[AgentToolReceipt],
    tool_payloads: Sequence[object],
) -> EvidenceCatalog:
    """Bounded field catalog for prompt context; must not be persisted as diagnostics."""
    entries, omitted, traversal_truncated = _catalog_entries(receipts, tool_payloads)
    catalog: EvidenceCatalog = {
        "schema_version": 1,
        "entries": entries,
        "truncated": traversal_truncated or omitted > 0,
        "omitted_count": omitted,
    }
    # Ensure the bound for the default escaped JSON serialization as well as the
    # UTF-8 literal prompt serializer. Removed entries cannot pass the guard.
    while len(json.dumps(catalog, ensure_ascii=True).encode("utf-8")) > _MAX_CATALOG_BYTES:
        catalog["entries"].pop()
        catalog["omitted_count"] += 1
        catalog["truncated"] = True
    return catalog


def guard_research_answer(
    envelope: AgentAnswerEnvelope,
    *,
    receipts: Sequence[AgentToolReceipt],
    tool_payloads: Sequence[object],
) -> ResearchEvidenceResult:
    """Release exact sourced facts; replace unsupported assertions with safe gaps."""
    entries = {e["ref"]: e for e in research_evidence_catalog(receipts, tool_payloads)["entries"]}
    blocks: list[AgentAnswerBlock] = []
    codes: set[str] = set()
    verified = 0
    missing = 0
    for block in envelope.blocks:
        code: str | None = None
        selected = [entries[ref] for ref in block.evidence_refs if ref in entries]
        required = block.kind in {AgentAnswerBlockKind.FACT, AgentAnswerBlockKind.CITATION}
        prose = re.sub(r"(?m)^\s*(?:#{1,6}\s*)?\d+[.)]\s+", "", block.text)
        numeric = bool(_NUMERIC.search(_DATE.sub("", prose)))
        inline_urls = re.findall(r"https?://[^\s<>]+", block.text)
        allowed_urls = [url for entry in selected for url in entry["source_urls"]]
        if any(url not in allowed_urls for url in inline_urls):
            code = "RESEARCH_SOURCE_URL_UNVERIFIED"
        elif _EXECUTED.search(block.text):
            code = "RESEARCH_EXECUTION_CLAIM_DENIED"
        elif len(selected) != len(block.evidence_refs):
            code = "RESEARCH_EVIDENCE_REF_MISSING"
        elif required or numeric or selected or block.source_urls:
            if len(selected) != 1:
                code = "RESEARCH_FIELD_EVIDENCE_REQUIRED"
            else:
                evidence = selected[0]
                if block.text != evidence["text"]:
                    code = "RESEARCH_CLAIM_NOT_EXACT"
                elif block.as_of != evidence["as_of"] or block.basis != evidence["basis"]:
                    code = "RESEARCH_CONTEXT_MISMATCH"
                elif any(url not in evidence["source_urls"] for url in block.source_urls):
                    code = "RESEARCH_SOURCE_URL_UNVERIFIED"
                else:
                    verified += 1
        if code:
            missing += 1
            codes.add(code)
            blocks.append(
                AgentAnswerBlock(
                    kind=AgentAnswerBlockKind.GAP,
                    text="本项陈述缺少可核对的本轮字段证据，已隐藏；请补充对应来源后复查。",
                )
            )
        else:
            # Qualitative synthesis is explicitly interpretation, never host-verified fact.
            blocks.append(
                block.model_copy(update={"kind": AgentAnswerBlockKind.INFERENCE})
                if block.kind == AgentAnswerBlockKind.SUMMARY and not selected
                else block
            )
    return ResearchEvidenceResult(
        envelope=envelope.model_copy(update={"blocks": tuple(blocks)}),
        summary={
            "schema_version": 1,
            "verified_count": verified,
            "missing_count": missing,
            "codes": sorted(codes),
            "status": "GAPS" if missing else ("VERIFIED" if verified else "NOT_CHECKED"),
        },
    )
