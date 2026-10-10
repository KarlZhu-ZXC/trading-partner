"""Closed, current-turn evidence binding for Copilot research answers.

Fact prose is deliberately host-rendered: a model selects a field reference and
uses the @evidence marker (or copies its catalog text). Matching numbers cannot
establish which asset,
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
from application.dto.research_presentation import (
    ResearchAnswerPresentation,
    ResearchFieldSegment,
    ResearchMetadataSegment,
    ResearchTextSegment,
)
from application.ports.agent_tool_gateway import AgentToolReceipt
from application.services.agent_answer_protocol import (
    answer_block_heading,
    answer_block_metadata_line,
    answer_block_source_lines,
    render_agent_answer,
)

_SAFE_PATH = re.compile(r"^[A-Za-z0-9_.:-]+$")
# Chinese prose does not require whitespace before an amount. ASCII identifier
# suffixes remain distinct from amounts (for example a model's version name).
_NUMERIC = re.compile(r"(?<![A-Za-z0-9_])[+-]?\d+(?:[.,]\d+)*(?:%)?")
_INLINE_EVIDENCE = re.compile(r"@evidence\(([A-Za-z0-9][A-Za-z0-9_.:/-]{0,159})\)")
_PAREN_LIST = re.compile(r"(?:^|[\n；;。—:：])\s*[（(](\d+)[）)]")
# A missing-value definition compares representations, not a financial amount.
# Recognize only literal zero in an explicit negative definition; other numbers
# and actual fee/price assertions in the same block still need field evidence.
_MISSING_ZERO_LITERAL = re.compile(
    r"(?:(?<![A-Za-z_])null(?![A-Za-z_])|缺失(?:值|字段|数据|费用|手续费|数值)?)"
    r"(?:\s*表示(?:字段|数据|数值)?缺失[，,]?)?\s*"
    r"(?:不等于|不是|并非|而非|不代表|不应视为|!=|≠|is not|does not mean)\s*"
    r"(?:数值|数字)?\s*0(?![A-Za-z0-9_%％]|[.,]\d)|"
    r"(?:不能|不得|不应|不要|不可)(?:把|将)"
    r"(?:缺失(?:的)?(?:费用|手续费|数值|数据|字段|值)?|null)"
    r"(?:当作|视为|按|计为)\s*0(?![A-Za-z0-9_%％]|[.,]\d)(?:处理)?",
    re.IGNORECASE,
)
_EXECUTED = re.compile(
    r"已(?:成交|下单|执行|确认|买入|卖出|撤单)|"
    r"(?:订单|委托|候选|观点|判断|计划|仓位)(?:已经|已)确认|"
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
        "fees",
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
        "quote_at",
        "snapshot_at",
        "source_as_of",
        "period_end",
        "period_type",
        "unit",
        "units",
        "scale",
        "adjustment",
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
class _CatalogField:
    entry: EvidenceEntry
    path: str
    scalar: str
    is_null: bool
    context: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _ResearchBlockPresentation:
    block: AgentAnswerBlock
    body: tuple[ResearchTextSegment | ResearchFieldSegment, ...]


@dataclass(frozen=True, slots=True)
class ResearchEvidenceResult:
    envelope: AgentAnswerEnvelope
    summary: dict[str, object]
    presentation: ResearchAnswerPresentation | None = None


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
) -> tuple[list[_CatalogField], int, bool]:
    current = {
        (r.request_id, r.capability, r.operation): r
        for r in receipts[:32]
        if r.request_id
        and not r.error_code
        and not r.result_truncated
        and (r.effect in _READ_EFFECTS or r.capability == "agent_web_search")
    }
    entries: dict[str, _CatalogField] = {}
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
            scalar = "null" if value is None else _scalar(value)
            ref = f"{request_id}/{path}"
            if scalar is None or leaf not in _FACT_FIELDS or len(ref) > 160:
                continue
            # Web pages are background sources, never canonical price/position facts.
            is_web = receipt.capability == "agent_web_search"
            if is_web and leaf not in {"url", "source_url", "published_at"}:
                continue
            if (
                value is not None
                and leaf not in _CONTEXT_KEYS
                and leaf
                not in {
                    "status",
                    "confirmed",
                    "confirmed_at",
                    "confirmation_time",
                    "occurred_at",
                    "direction",
                    "url",
                    "source_url",
                }
            ):
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
            context_pairs = tuple((key, value) for key, value in metadata.items() if value)
            if metadata:
                text += " · " + " · ".join(f"{k}={v}" for k, v in context_pairs)
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
            entry: EvidenceEntry = {
                "ref": ref,
                "text": text,
                "as_of": as_of,
                "basis": basis,
                "source_urls": urls,
            }
            entries[ref] = _CatalogField(
                entry=entry,
                path=path,
                scalar=scalar,
                is_null=value is None,
                context=context_pairs,
            )
        traversal_truncated |= bool(stack)
    return (
        [field for ref, field in entries.items() if ref.split("/", 1)[0] not in duplicate_requests],
        omitted_count,
        traversal_truncated,
    )


def research_evidence_catalog(
    receipts: Sequence[AgentToolReceipt],
    tool_payloads: Sequence[object],
    *,
    evidence_cutoff: datetime | None = None,
) -> EvidenceCatalog:
    """Bounded field catalog for prompt context; must not be persisted as diagnostics."""
    return _bounded_catalog_with_fields(receipts, tool_payloads, evidence_cutoff=evidence_cutoff)[0]


def _bounded_catalog_with_fields(
    receipts: Sequence[AgentToolReceipt],
    tool_payloads: Sequence[object],
    *,
    evidence_cutoff: datetime | None = None,
) -> tuple[EvidenceCatalog, dict[str, _CatalogField]]:
    catalog_fields, omitted, traversal_truncated = _catalog_entries(receipts, tool_payloads)
    if evidence_cutoff is not None:
        retained = [
            field
            for field in catalog_fields
            if field.entry["as_of"] is not None
            and (fact_time := _aware_time(field.entry["as_of"])) is not None
            and fact_time <= evidence_cutoff
        ]
        omitted += len(catalog_fields) - len(retained)
        catalog_fields = retained
    fields = {field.entry["ref"]: field for field in catalog_fields}
    entries = [field.entry for field in catalog_fields]
    catalog: EvidenceCatalog = {
        "schema_version": 1,
        "entries": entries,
        "truncated": traversal_truncated or omitted > 0,
        "omitted_count": omitted,
    }
    # Ensure the bound for the default escaped JSON serialization as well as the
    # UTF-8 literal prompt serializer. Removed entries cannot pass the guard.
    while len(json.dumps(catalog, ensure_ascii=True).encode("utf-8")) > _MAX_CATALOG_BYTES:
        removed = catalog["entries"].pop()
        fields.pop(removed["ref"], None)
        catalog["omitted_count"] += 1
        catalog["truncated"] = True
    retained_refs = {entry["ref"] for entry in catalog["entries"]}
    return catalog, {ref: field for ref, field in fields.items() if ref in retained_refs}


def _text_segment(text: str) -> ResearchTextSegment | None:
    if not text:
        return None
    try:
        return ResearchTextSegment(text=text)
    except (TypeError, ValueError):
        return None


def _field_segment(
    field: _CatalogField,
    *,
    inline: bool,
) -> ResearchFieldSegment | None:
    try:
        return ResearchFieldSegment(
            ref=field.entry["ref"],
            path=field.path,
            scalar=field.scalar,
            is_null=field.is_null,
            context=field.context,
            inline=inline,
        )
    except (TypeError, ValueError):
        return None


def _inline_body_segments(
    text: str,
    fields: Mapping[str, _CatalogField],
) -> tuple[ResearchTextSegment | ResearchFieldSegment, ...] | None:
    segments: list[ResearchTextSegment | ResearchFieldSegment] = []
    cursor = 0
    for match in _INLINE_EVIDENCE.finditer(text):
        prefix = _text_segment(text[cursor : match.start()])
        if prefix is not None:
            segments.append(prefix)
        field = fields.get(match.group(1))
        if field is None:
            return None
        field_segment = _field_segment(field, inline=True)
        if field_segment is None:
            return None
        segments.append(field_segment)
        cursor = match.end()
    suffix = _text_segment(text[cursor:])
    if suffix is not None:
        segments.append(suffix)
    return tuple(segments)


def _body_segments(
    block: AgentAnswerBlock,
    *,
    original_text: str,
    selected: Sequence[EvidenceEntry],
    fields: Mapping[str, _CatalogField],
    inline_refs: Sequence[str],
) -> tuple[ResearchTextSegment | ResearchFieldSegment, ...] | None:
    if inline_refs:
        return _inline_body_segments(original_text, fields)
    if len(selected) == 1 and original_text == selected[0]["text"]:
        field = fields.get(selected[0]["ref"])
        if field is None:
            return None
        field_segment = _field_segment(field, inline=False)
        return (field_segment,) if field_segment is not None else None
    text = _text_segment(block.text)
    return (text,) if text is not None else None


def _build_research_presentation(
    envelope: AgentAnswerEnvelope,
    block_presentations: Sequence[_ResearchBlockPresentation],
) -> ResearchAnswerPresentation | None:
    if len(envelope.blocks) != len(block_presentations):
        return None
    try:
        segments: list[ResearchTextSegment | ResearchMetadataSegment | ResearchFieldSegment] = []
        if envelope.generated_by == "fallback" and all(
            block.block.kind is AgentAnswerBlockKind.SUMMARY for block in block_presentations
        ):
            for block_presentation in block_presentations:
                text = _text_segment(block_presentation.block.text)
                if text is None:
                    return None
                segments.append(text)
        else:
            for index, block_presentation in enumerate(block_presentations):
                answer_block = block_presentation.block
                segments.append(
                    ResearchTextSegment(text=answer_block_heading(answer_block) + "\n\n")
                )
                segments.extend(block_presentation.body)
                metadata = answer_block_metadata_line(answer_block)
                if metadata is not None:
                    segments.append(ResearchTextSegment(text="\n\n"))
                    segments.append(ResearchMetadataSegment(text=metadata))
                source_lines = answer_block_source_lines(answer_block)
                if source_lines:
                    segments.append(ResearchTextSegment(text="\n\n"))
                    segments.append(ResearchTextSegment(text="\n".join(source_lines)))
                if index < len(block_presentations) - 1:
                    segments.append(ResearchTextSegment(text="\n\n"))
        presentation = ResearchAnswerPresentation(segments=tuple(segments))
    except (TypeError, ValueError):
        return None
    return presentation if presentation.rendered_text() == render_agent_answer(envelope) else None


def guard_research_answer(
    envelope: AgentAnswerEnvelope,
    *,
    receipts: Sequence[AgentToolReceipt],
    tool_payloads: Sequence[object],
    evidence_cutoff: datetime | None = None,
) -> ResearchEvidenceResult:
    """Release exact sourced facts; replace unsupported assertions with safe gaps."""
    catalog, fields = _bounded_catalog_with_fields(
        receipts, tool_payloads, evidence_cutoff=evidence_cutoff
    )
    entries = {entry["ref"]: entry for entry in catalog["entries"]}
    blocks: list[AgentAnswerBlock] = []
    block_presentations: list[_ResearchBlockPresentation] = []
    presentation_possible = True
    codes: set[str] = set()
    verified = 0
    missing = 0
    for index, block in enumerate(envelope.blocks):
        original_text = block.text
        code: str | None = None
        required = block.kind in {AgentAnswerBlockKind.FACT, AgentAnswerBlockKind.CITATION}
        inline_refs = _INLINE_EVIDENCE.findall(block.text) if not required else []
        if not block.evidence_refs and inline_refs and len(set(inline_refs)) <= 20:
            # The literal current-turn path is itself an exact selection. Model
            # output need not repeat the same selection in a parallel array.
            # Explicit conflicting arrays and unknown paths still fail closed.
            block = block.model_copy(update={"evidence_refs": tuple(dict.fromkeys(inline_refs))})
        selected = [entries[ref] for ref in block.evidence_refs if ref in entries]
        explanation = _INLINE_EVIDENCE.sub("", block.text) if not required else block.text
        prose = re.sub(r"(?m)^\s*(?:#{1,6}\s*)?\d+[.)]\s+", "", explanation)
        # Known field/node paths and list labels are structure, not amounts.
        # Unknown paths and arbitrary numbers remain subject to exact binding.
        prose = re.sub(
            r"(?<![\w/])(?:[A-Za-z_][\w.-]*/)+(?:\d+|[A-Za-z_][\w.-]*)(?![\w/])",
            lambda match: (
                "" if any(f"/{match.group()}/" in f"/{ref}/" for ref in entries) else match.group()
            ),
            prose,
        )
        # A colon can introduce a numbered list inside a paragraph. Only strip
        # such labels when the complete sequence is unambiguously 1..N; a lone
        # parenthesized amount after a colon is still a numerical assertion.
        labels = list(_PAREN_LIST.finditer(prose))
        if 2 <= len(labels) <= 32 and [m.group(1) for m in labels] == [
            str(n) for n in range(1, len(labels) + 1)
        ]:
            prose = _PAREN_LIST.sub("", prose)
        else:
            prose = re.sub(r"(?:^|[\n；;。—])\s*[（(]\d+[）)]", "", prose)
        numeric = bool(_NUMERIC.search(_MISSING_ZERO_LITERAL.sub("", prose)))
        # Only explicitly negated mentions are exempt; a later affirmative
        # assertion in the same block must still be rejected.
        execution_prose = re.sub(
            r"(?:不能|不可|无法|尚未|未能)(?:认定|视为|当作|判定|证明|证实)?(?:为)?[“\"']?"
            r"(?:已确认突破|已(?:成交|下单|执行|确认|买入|卖出|撤单))|"
            r"(?:未提供|缺少)(?:将)?(?:该)?(?:结构|突破)(?:转为|达到)已确认"
            r"(?:所需的)?(?:确认)?(?:条件|证据)|"
            r"\b(?:not|never) (?:executed|submitted|filled|purchased|sold|confirmed breakout)\b",
            "",
            explanation,
            flags=re.IGNORECASE,
        )
        # A narrowly identified field-certainty qualifier is not confirmation
        # of a judgment/order. Do not exempt bare confirmations or a later
        # affirmative execution claim, and never promote this prose to FACT.
        execution_prose = re.sub(r"(?<=字段[为是])已确认的(?=已知值)", "", execution_prose)
        inline_urls = re.findall(r"https?://[^\s<>]+", block.text)
        allowed_urls = [url for entry in selected for url in entry["source_urls"]]
        if not required and (
            "@evidence" in explanation
            or any(ref not in block.evidence_refs or ref not in entries for ref in inline_refs)
        ):
            code = "RESEARCH_EVIDENCE_REF_MISSING"
        elif any(url not in allowed_urls for url in inline_urls):
            code = "RESEARCH_SOURCE_URL_UNVERIFIED"
        elif _EXECUTED.search(execution_prose):
            code = "RESEARCH_EXECUTION_CLAIM_DENIED"
        elif len(selected) != len(block.evidence_refs):
            code = "RESEARCH_EVIDENCE_REF_MISSING"
        elif required and block.text == "@evidence":
            if not selected:
                code = "RESEARCH_FIELD_EVIDENCE_REQUIRED"
            elif block.as_of is not None or block.basis is not None or block.source_urls:
                code = "RESEARCH_CONTEXT_MISMATCH"
            elif (
                len(blocks) + len(selected) + len(envelope.blocks) - index - 1 > 32
                or sum(len(item.text) for item in blocks)
                + sum(len(entry["text"]) for entry in selected)
                + sum(len(item.text) for item in envelope.blocks[index + 1 :])
                > 64_000
            ):
                code = "RESEARCH_FIELD_EVIDENCE_REQUIRED"
            else:
                # A grouped selection renders independent facts, never merges
                # currencies, subjects or timestamps into a model-created claim.
                grouped_blocks = [
                    AgentAnswerBlock(
                        kind=block.kind,
                        text=entry["text"],
                        evidence_refs=(entry["ref"],),
                        as_of=entry["as_of"],
                        basis=entry["basis"],
                        source_urls=tuple(entry["source_urls"]),
                    )
                    for entry in selected
                ]
                blocks.extend(grouped_blocks)
                for output_block, entry in zip(grouped_blocks, selected, strict=True):
                    field = fields.get(entry["ref"])
                    field_segment = _field_segment(field, inline=False) if field else None
                    if field_segment is None:
                        presentation_possible = False
                    else:
                        block_presentations.append(
                            _ResearchBlockPresentation(output_block, (field_segment,))
                        )
                verified += len(selected)
                continue
        elif required or numeric:
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
        elif any(url not in allowed_urls for url in block.source_urls):
            code = "RESEARCH_SOURCE_URL_UNVERIFIED"
        elif (block.as_of is not None or block.basis is not None) and (
            not selected
            or any(
                (block.as_of is not None and block.as_of != entry["as_of"])
                or (block.basis is not None and block.basis != entry["basis"])
                for entry in selected
            )
        ):
            code = "RESEARCH_CONTEXT_MISMATCH"
        if code is None and inline_refs:
            # Insert complete host-owned field context, never a bare matching
            # number. The surrounding explanation remains unverified inference.
            rendered = _INLINE_EVIDENCE.sub(
                lambda match: f"〔{entries[match.group(1)]['text']}〕", block.text
            )
            if (
                len(rendered) > 4_000
                or sum(len(item.text) for item in blocks)
                + len(rendered)
                + sum(len(item.text) for item in envelope.blocks[index + 1 :])
                > 64_000
            ):
                code = "RESEARCH_FIELD_EVIDENCE_REQUIRED"
            else:
                block = block.model_copy(update={"text": rendered})
        if code:
            missing += 1
            codes.add(code)
            gap_block = AgentAnswerBlock(
                kind=AgentAnswerBlockKind.GAP,
                text="本项陈述缺少可核对的本轮字段证据，已隐藏；请补充对应来源后复查。",
            )
            blocks.append(gap_block)
            gap_text = _text_segment(gap_block.text)
            if gap_text is None:
                presentation_possible = False
            else:
                block_presentations.append(_ResearchBlockPresentation(gap_block, (gap_text,)))
        else:
            # Qualitative synthesis is explicitly interpretation, never host-verified fact.
            checked_block = (
                block.model_copy(update={"kind": AgentAnswerBlockKind.INFERENCE})
                if block.kind == AgentAnswerBlockKind.SUMMARY
                else block
            )
            blocks.append(checked_block)
            body = _body_segments(
                checked_block,
                original_text=original_text,
                selected=selected,
                fields=fields,
                inline_refs=inline_refs,
            )
            if body is None:
                presentation_possible = False
            else:
                block_presentations.append(_ResearchBlockPresentation(checked_block, body))
    checked_envelope = envelope.model_copy(update={"blocks": tuple(blocks)})
    presentation = (
        _build_research_presentation(checked_envelope, block_presentations)
        if presentation_possible
        else None
    )
    return ResearchEvidenceResult(
        envelope=checked_envelope,
        summary={
            "schema_version": 1,
            "verified_count": verified,
            "missing_count": missing,
            "codes": sorted(codes),
            "status": "GAPS" if missing else ("VERIFIED" if verified else "NOT_CHECKED"),
        },
        presentation=presentation,
    )
