"""Deterministic evidence binding for Shared Agent final answers.

The model remains responsible for prose, but the host must not silently turn a
number or an execution verb into an unsupported fact.  This small application
service accepts bounded tool payloads/receipts, extracts exact scalar claims,
and returns a redaction-safe manifest that can be persisted with the assistant
message receipt.  It deliberately does not call an LLM or a Provider.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?P<prefix>[$€£¥]|USD|CNY|RMB|HKD|JPY)?\s*"
    r"(?P<number>[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"\s*(?P<percent>%)?",
    re.IGNORECASE,
)
_DATE_RE = re.compile(r"^\d{4}(?:[-/]\d{1,2}(?:[-/]\d{1,2})?)?$")
_DATE_CLAIM_RE = re.compile(
    r"\b(?P<year>\d{4})[-/](?P<month>\d{1,2})(?:[-/](?P<day>\d{1,2}))?(?![-/\d])"
)
_ISO_DATE_TIME_RE = re.compile(
    r"\b\d{4}[-/]\d{1,2}(?:[-/]\d{1,2}"
    r"(?:[T ]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
    r")?"
)
_ACTION_RE = re.compile(
    r"(?:买入|卖出|加仓|减仓|成交|已成交|下单|已下单|撤单|已撤单|执行|已执行|买进|卖出)",
)
_PENDING_ACTION_WORDS = frozenset({"买入", "卖出", "加仓", "减仓", "下单", "撤单", "执行"})
_QUALITY_DISCLOSURE_RE = re.compile(
    r"(?:降级|陈旧|过期|延迟|不可用|不完整|新鲜度|数据质量|stale|delayed|degraded|unavailable|freshness)",
    re.IGNORECASE,
)
_QUALITY_FRESHNESS_VALUES = frozenset(
    {"stale", "delayed", "degraded", "unavailable", "unknown", "incomplete"}
)
_QUALITY_WARNING_MARKERS = frozenset(
    {"STALE", "DEGRADED", "DELAY", "UNAVAILABLE", "INCOMPLETE", "FRESHNESS"}
)
_MAX_CLAIMS = 48
_MAX_FACTS = 96
_MAX_TEXT_CHARS = 64_000


@dataclass(frozen=True, slots=True)
class EvidenceFact:
    """One bounded source fact available to the final-answer guard."""

    capability: str
    operation: str
    request_id: str | None = None
    as_of: str | None = None
    freshness: str | None = None
    degraded: bool | None = None
    source_codes: tuple[str, ...] = ()
    warning_codes: tuple[str, ...] = ()
    error_codes: tuple[str, ...] = ()
    price_basis: str | None = None
    values: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "operation": self.operation,
            "request_id": self.request_id,
            "as_of": self.as_of,
            "freshness": self.freshness,
            "degraded": self.degraded,
            "source_codes": list(self.source_codes[:8]),
            "warning_codes": list(self.warning_codes[:16]),
            "error_codes": list(self.error_codes[:16]),
            "price_basis": self.price_basis,
            "values": list(self.values[:32]),
        }


@dataclass(frozen=True, slots=True)
class EvidenceClaim:
    text: str
    normalized: str
    kind: str = "number"
    verified: bool = False
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text[:160],
            "normalized": self.normalized,
            "kind": self.kind,
            "verified": self.verified,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class EvidenceGuardResult:
    text: str
    verified: bool
    claims: tuple[EvidenceClaim, ...] = ()
    unverified_claims: tuple[EvidenceClaim, ...] = ()
    manifest: Mapping[str, Any] = field(default_factory=dict)
    repair_request: Mapping[str, Any] | None = None

    @property
    def has_unverified_claims(self) -> bool:
        return bool(self.unverified_claims)


def _safe_code(value: object, *, limit: int = 160) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if len(value) > limit or any(ord(char) < 32 for char in value):
        return None
    return value


def _number_key(number: str, percent: bool) -> str | None:
    cleaned = number.replace(",", "").strip()
    try:
        value = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    rendered = format(value.normalize(), "f")
    if rendered == "-0":
        rendered = "0"
    return f"{rendered}%" if percent else rendered


def _date_key(value: str) -> str:
    parts = value.replace("/", "-").split("-")
    return "date:" + "-".join(parts)


def _is_date_fragment(text: str, start: int, end: int) -> bool:
    """Avoid treating ISO date/timestamp components as financial claims."""

    # Check containment rather than mere proximity: financial values often
    # sit next to ``quote_at`` and must still be checked, while seconds and
    # timezone offsets inside the timestamp must not become separate claims.
    window_start = max(0, start - 48)
    window_end = min(len(text), end + 48)
    window = text[window_start:window_end]
    return any(
        window_start + match.start() <= start
        and end <= window_start + match.end()
        for match in _ISO_DATE_TIME_RE.finditer(window)
    )


def _extract_numbers(value: object, *, depth: int = 0) -> tuple[str, ...]:
    if depth > 7:
        return ()
    if isinstance(value, Mapping):
        results: list[str] = []
        for key, item in value.items():
            # IDs, hashes, and timestamps are not numeric market claims.
            key_text = str(key).lower()
            if key_text.endswith(("_id", "_sha256", "request_id", "timestamp")):
                continue
            results.extend(_extract_numbers(item, depth=depth + 1))
            if len(results) >= _MAX_FACTS:
                break
        return tuple(results[:_MAX_FACTS])
    if isinstance(value, (list, tuple, set, frozenset)):
        results = []
        for item in list(value)[:128]:
            results.extend(_extract_numbers(item, depth=depth + 1))
            if len(results) >= _MAX_FACTS:
                break
        return tuple(results[:_MAX_FACTS])
    if isinstance(value, bool) or value is None:
        return ()
    if isinstance(value, (int, float, Decimal)):
        return (_number_key(str(value), False) or "",)
    if isinstance(value, str):
        results = [_date_key(match.group(0)) for match in _DATE_CLAIM_RE.finditer(value)]
        for match in _NUMBER_RE.finditer(value[:_MAX_TEXT_CHARS]):
            raw = match.group("number")
            if _DATE_RE.fullmatch(raw.replace(",", "")) or _is_date_fragment(
                value, match.start(), match.end()
            ):
                continue
            key = _number_key(raw, bool(match.group("percent")))
            if key is not None:
                results.append(key)
        return tuple(results[:_MAX_FACTS])
    return ()


def _bounded_codes(value: object, *, limit: int = 16) -> tuple[str, ...]:
    if isinstance(value, str):
        value = (value,)
    elif isinstance(value, Mapping):
        # Warning/error envelopes may be represented by one ``{"code": ...}``
        # object rather than a list.  Keep this shape bounded and normalize it
        # to the same code tuple as the list form.
        value = (value,)
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    values: list[str] = []
    for item in list(value)[:64]:
        raw = item.get("code") if isinstance(item, Mapping) else item
        code = _safe_code(raw, limit=128)
        if code is not None and code not in values:
            values.append(code)
        if len(values) >= limit:
            break
    return tuple(values)


def _quality_metadata(
    value: object,
    *,
    depth: int = 0,
) -> tuple[bool | None, str | None, str | None, tuple[str, ...], tuple[str, ...]]:
    """Extract envelope quality fields from recursively nested payloads.

    Agent tool payloads normally wrap the canonical result as ``result`` beside
    an application ``receipt``.  The receipt can carry ``degraded`` and warning
    codes while the result carries ``freshness``/``as_of`` (or vice versa), so
    looking only at the outer mapping would silently lose provenance.  This
    helper traverses bounded mapping/list nesting and merges the quality fields
    without retaining raw payload text.
    """

    if depth > 7:
        return None, None, None, (), ()
    degraded: bool | None = None
    freshness: str | None = None
    as_of: str | None = None
    warnings: list[str] = []
    errors: list[str] = []

    def merge_codes(target: list[str], values: Iterable[str]) -> None:
        for code in values:
            if code not in target:
                target.append(code)
            if len(target) >= 16:
                break

    if isinstance(value, Mapping):
        raw_degraded = value.get("degraded")
        if type(raw_degraded) is bool:
            degraded = raw_degraded
        raw_freshness = value.get("freshness")
        if isinstance(raw_freshness, str) and raw_freshness.strip():
            freshness = raw_freshness.strip()[:160]
        raw_as_of = value.get("as_of")
        if isinstance(raw_as_of, str) and raw_as_of.strip():
            as_of = raw_as_of.strip()[:160]
        merge_codes(warnings, _bounded_codes(value.get("warnings", ())))
        merge_codes(warnings, _bounded_codes(value.get("warning_codes", ())))
        merge_codes(errors, _bounded_codes(value.get("errors", ())))
        merge_codes(errors, _bounded_codes(value.get("error_codes", ())))
        merge_codes(errors, _bounded_codes(value.get("error_code", ())))
        children: Iterable[object] = list(value.values())[:128]
    elif isinstance(value, (list, tuple, set, frozenset)):
        children = list(value)[:128]
    else:
        children = ()

    for child in children:
        child_degraded, child_freshness, child_as_of, child_warnings, child_errors = (
            _quality_metadata(child, depth=depth + 1)
        )
        # Any nested degraded marker must be retained.  A nested ``False``
        # must not erase an already observed ``True``.
        if child_degraded is True:
            degraded = True
        elif degraded is None and child_degraded is False:
            degraded = False
        if child_freshness:
            current_quality = (freshness or "").casefold()
            nested_quality = child_freshness.casefold()
            if freshness is None or (
                current_quality == "fresh" and nested_quality != "fresh"
            ):
                freshness = child_freshness
        if as_of is None and child_as_of:
            as_of = child_as_of
        merge_codes(warnings, child_warnings)
        merge_codes(errors, child_errors)
        if len(warnings) >= 16 and len(errors) >= 16:
            break
    return degraded, freshness, as_of, tuple(warnings[:16]), tuple(errors[:16])


def _iter_receipts(receipts: Iterable[object]) -> Iterable[EvidenceFact]:
    for raw in list(receipts)[:_MAX_FACTS]:
        if isinstance(raw, Mapping):
            capability = _safe_code(raw.get("capability"))
            operation = _safe_code(raw.get("operation"))
            request_id = _safe_code(raw.get("request_id"))
            as_of = _safe_code(raw.get("as_of"))
            freshness = _safe_code(raw.get("freshness"))
            degraded = raw.get("degraded") if type(raw.get("degraded")) is bool else None
            price_basis = _safe_code(raw.get("price_basis"))
            raw_source_codes = raw.get("source_codes", ())
            if isinstance(raw_source_codes, str):
                raw_source_codes = (raw_source_codes,)
            source_codes = tuple(
                code
                for code in (_safe_code(item, limit=128) for item in raw_source_codes)
                if code is not None
            )[:8]
            warning_codes = _bounded_codes(raw.get("warnings", raw.get("warning_codes", ())))
            error_codes = _bounded_codes(raw.get("errors", raw.get("error_codes", ())))
            error_codes = tuple(
                dict.fromkeys((*error_codes, *_bounded_codes(raw.get("error_code", ()))))
            )[:16]
            values = _extract_numbers(raw)
        else:
            capability = _safe_code(getattr(raw, "capability", None))
            operation = _safe_code(getattr(raw, "operation", None))
            request_id = _safe_code(getattr(raw, "request_id", None))
            as_of = _safe_code(getattr(raw, "as_of", None))
            freshness = _safe_code(getattr(raw, "freshness", None))
            raw_degraded = getattr(raw, "degraded", None)
            degraded = raw_degraded if type(raw_degraded) is bool else None
            price_basis = _safe_code(getattr(raw, "price_basis", None))
            source_codes = tuple(
                code
                for code in (
                    _safe_code(item, limit=128)
                    for item in getattr(raw, "source_codes", ())
                )
                if code is not None
            )[:8]
            warning_codes = _bounded_codes(
                getattr(raw, "warnings", getattr(raw, "warning_codes", ()))
            )
            error_codes = _bounded_codes(
                getattr(raw, "errors", getattr(raw, "error_codes", ()))
            )
            error_codes = tuple(
                dict.fromkeys(
                    (
                        *error_codes,
                        *_bounded_codes(getattr(raw, "error_code", ())),
                    )
                )
            )[:16]
            values = _extract_numbers(raw)
        nested_degraded, nested_freshness, nested_as_of, nested_warnings, nested_errors = (
            _quality_metadata(raw)
        )
        if nested_degraded is True:
            degraded = True
        elif degraded is None:
            degraded = nested_degraded
        if freshness is None or (
            freshness.casefold() == "fresh"
            and nested_freshness is not None
            and nested_freshness.casefold() != "fresh"
        ):
            freshness = nested_freshness
        if as_of is None:
            as_of = nested_as_of
        warning_codes = tuple(dict.fromkeys((*warning_codes, *nested_warnings)))[:16]
        error_codes = tuple(dict.fromkeys((*error_codes, *nested_errors)))[:16]
        if capability is None or operation is None:
            continue
        yield EvidenceFact(
            capability=capability,
            operation=operation,
            request_id=request_id,
            as_of=as_of,
            freshness=freshness,
            degraded=degraded,
            source_codes=source_codes,
            warning_codes=warning_codes,
            error_codes=error_codes,
            price_basis=price_basis,
            values=tuple(value for value in values if value)[:32],
        )


def _collect_field_values(value: object, keys: frozenset[str], *, depth: int = 0) -> set[str]:
    if depth > 8:
        return set()
    if isinstance(value, Mapping):
        results: set[str] = set()
        for key, item in value.items():
            if str(key).lower() in keys and isinstance(item, str):
                results.add(item[:160])
            results.update(_collect_field_values(item, keys, depth=depth + 1))
        return results
    if isinstance(value, (list, tuple)):
        results = set()
        for item in value[:128]:
            results.update(_collect_field_values(item, keys, depth=depth + 1))
        return results
    return set()


def _claim_values(text: str) -> tuple[EvidenceClaim, ...]:
    values: list[EvidenceClaim] = []
    for match in _DATE_CLAIM_RE.finditer(text[:_MAX_TEXT_CHARS]):
        normalized = _date_key(match.group(0))
        values.append(
            EvidenceClaim(
                text=match.group(0),
                normalized=normalized,
                kind="date",
            )
        )
        if len(values) >= _MAX_CLAIMS:
            return tuple(values)
    for match in _NUMBER_RE.finditer(text[:_MAX_TEXT_CHARS]):
        raw_number = match.group("number")
        if _DATE_RE.fullmatch(raw_number.replace(",", "")) or _is_date_fragment(
            text, match.start(), match.end()
        ):
            continue
        number_normalized = _number_key(raw_number, bool(match.group("percent")))
        if number_normalized is None:
            continue
        raw = match.group(0).strip()
        if any(item.normalized == number_normalized for item in values):
            continue
        values.append(EvidenceClaim(text=raw, normalized=number_normalized))
        if len(values) >= _MAX_CLAIMS:
            break
    return tuple(values)


def _manifest(facts: Sequence[EvidenceFact], claims: Sequence[EvidenceClaim]) -> dict[str, Any]:
    return {
        "version": "agent_evidence_v1",
        "facts": [fact.as_dict() for fact in facts[:_MAX_FACTS]],
        "claims": [claim.as_dict() for claim in claims[:_MAX_CLAIMS]],
        "fact_count": len(facts),
        "claim_count": len(claims),
    }


def _quality_issues(facts: Sequence[EvidenceFact]) -> list[str]:
    """Return typed stale/degraded signals that require answer disclosure."""

    issues: list[str] = []
    for fact in facts:
        if fact.degraded is True and "DEGRADED" not in issues:
            issues.append("DEGRADED")
        freshness = fact.freshness.casefold() if fact.freshness else ""
        if freshness in _QUALITY_FRESHNESS_VALUES:
            code = f"FRESHNESS_{freshness.upper()}"
            if code not in issues:
                issues.append(code)
        for raw_code in (*fact.warning_codes, *fact.error_codes):
            upper = raw_code.upper()
            if (
                any(marker in upper for marker in _QUALITY_WARNING_MARKERS)
                and raw_code not in issues
            ):
                issues.append(raw_code[:128])
    return issues[:16]


def guard_agent_response(
    text: str,
    *,
    receipts: Iterable[object] = (),
    tool_payloads: Iterable[object] = (),
    max_output_chars: int = _MAX_TEXT_CHARS,
) -> EvidenceGuardResult:
    """Bind exact numeric/action claims to bounded current-turn evidence.

    Unverified numbers are retained in the returned text with a compact
    ``未验证`` marker.  A caller may perform one model repair request using
    :func:`build_repair_request`; this guard itself never calls a provider.
    """

    bounded_text = str(text)[:max(1, min(max_output_chars, _MAX_TEXT_CHARS))]
    receipt_facts = tuple(_iter_receipts(receipts))
    payload_receipts: list[Mapping[str, Any]] = []
    for payload in list(tool_payloads)[:_MAX_FACTS]:
        if not isinstance(payload, Mapping):
            continue
        nested_receipt = payload.get("receipt")
        if isinstance(nested_receipt, Mapping):
            merged = dict(nested_receipt)
            merged["_payload"] = payload.get("result")
            payload_receipts.append(merged)
        elif (
            _safe_code(payload.get("capability")) is not None
            and _safe_code(payload.get("operation")) is not None
        ):
            # Some lightweight hosts pass the canonical receipt/result envelope
            # directly instead of wrapping it under ``receipt``.  Keep this
            # path equivalent so quality metadata is not dropped.
            payload_receipts.append(payload)
    payload_facts = tuple(_iter_receipts(payload_receipts))
    facts_by_key: dict[tuple[str, str, str | None], EvidenceFact] = {}
    for fact in (*receipt_facts, *payload_facts):
        key = (fact.capability, fact.operation, fact.request_id)
        prior = facts_by_key.get(key)
        if prior is None or (not prior.values and fact.values):
            facts_by_key[key] = fact
    facts = tuple(facts_by_key.values())[:_MAX_FACTS]
    payload_values = tuple(
        value
        for payload in list(tool_payloads)[:_MAX_FACTS]
        for value in _extract_numbers(payload)
        if value
    )
    fact_values = {value for fact in facts for value in fact.values}
    available_values = fact_values | set(payload_values)
    claims = _claim_values(bounded_text)
    checked: list[EvidenceClaim] = []
    unverified: list[EvidenceClaim] = []
    for claim in claims:
        verified = claim.normalized in available_values
        item = EvidenceClaim(
            text=claim.text,
            normalized=claim.normalized,
            kind=(
                claim.kind
                if claim.kind != "number"
                else "percentage"
                if claim.normalized.endswith("%")
                else "number"
            ),
            verified=verified,
            reason=None if verified else "NO_CURRENT_TURN_EVIDENCE",
        )
        checked.append(item)
        if not verified:
            unverified.append(item)

    action_matches = tuple(_ACTION_RE.finditer(bounded_text))
    has_action_receipt = any(
        fact.capability == "broker_order_manage"
        and fact.operation in {"submit", "status", "cancel"}
        for fact in facts
    )
    if action_matches and not has_action_receipt:
        # Suggestions are fine; only claims phrased as completed actions are
        # marked.  This keeps ordinary “是否买入？” questions unaffected.
        completed_action = any(
            (
                match.group(0).startswith(("已", "成交"))
                or "已" in bounded_text[max(0, match.start() - 2) : match.start()]
            )
            and not re.search(
                r"(?:非|未|不是|并非|没有|无)\s*(?:实际)?$",
                bounded_text[max(0, match.start() - 6) : match.start()],
            )
            for match in action_matches
        )
        if completed_action:
            action_claim = EvidenceClaim(
                text=action_matches[0].group(0),
                normalized="action",
                kind="action",
                verified=False,
                reason="NO_CONFIRMATION_RECEIPT",
            )
            checked.append(action_claim)
            unverified.append(action_claim)

    basis_issues: list[str] = []
    basis_values = {fact.price_basis for fact in facts if fact.price_basis}
    payload_basis_values = {
        value
        for payload in list(tool_payloads)[:_MAX_FACTS]
        for value in _collect_field_values(
            payload,
            frozenset({"price_basis", "previous_close_basis"}),
        )
    }
    basis_values.update(payload_basis_values)
    actual_trade_claim = re.search(r"(?<!非)(?:实际)?成交价", bounded_text) is not None
    close_claim = "收盘价" in bounded_text
    previous_close_claim = "昨收" in bounded_text
    basis_mismatch = (
        (actual_trade_claim or close_claim)
        and not any("last" in basis or "close" in basis for basis in basis_values)
    ) or (
        previous_close_claim
        and any("previous_completed_regular_session_close" in basis for basis in basis_values)
    )
    basis_unavailable = previous_close_claim and not basis_values
    if basis_mismatch or basis_unavailable:
        basis_code = "PRICE_BASIS_MISMATCH" if basis_mismatch else "PRICE_BASIS_UNAVAILABLE"
        basis_issues.append(basis_code)
        unverified.append(
            EvidenceClaim(
                text="价格基线",
                normalized="price_basis",
                kind="basis",
                verified=False,
                reason=basis_code,
            )
        )

    quality_issues = _quality_issues(facts)
    if quality_issues and _QUALITY_DISCLOSURE_RE.search(bounded_text) is None:
        quality_claim = EvidenceClaim(
            text="数据质量状态",
            normalized="quality",
            kind="quality",
            verified=False,
            reason="QUALITY_DISCLOSURE_MISSING",
        )
        checked.append(quality_claim)
        unverified.append(quality_claim)

    manifest = _manifest(facts, checked)
    manifest["basis_issues"] = basis_issues
    manifest["quality_issues"] = quality_issues
    if unverified:
        manifest["unverified_claims"] = [item.as_dict() for item in unverified[:_MAX_CLAIMS]]
    safe_text = bounded_text
    if unverified:
        # Do not rewrite the model's prose or invent replacement values.  A
        # compact suffix makes the unsupported claims visible to the user.
        safe_text = f"{bounded_text}\n\n⚠ 未验证：本轮证据无法追溯上述精确数字/行动状态。"
    repair_request = build_repair_request(unverified, manifest) if unverified else None
    return EvidenceGuardResult(
        text=safe_text,
        verified=not unverified,
        claims=tuple(checked),
        unverified_claims=tuple(unverified),
        manifest=manifest,
        repair_request=repair_request,
    )


def build_repair_request(
    claims: Sequence[EvidenceClaim],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a bounded model-repair payload without raw tool output."""

    encoded_manifest = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded_manifest) <= 12_000:
        bounded_manifest: Mapping[str, Any] = json.loads(encoded_manifest)
    else:
        bounded_manifest = {
            "version": manifest.get("version", "agent_evidence_v1"),
            "truncated": True,
            "size_bytes": len(encoded_manifest),
            "sha256": hashlib.sha256(encoded_manifest).hexdigest(),
            "fact_count": manifest.get("fact_count"),
            "claim_count": manifest.get("claim_count"),
        }
    return {
        "instruction": (
            "Only repair or remove claims that cannot be traced to the supplied "
            "evidence manifest; do not invent values."
        ),
        "claims": [claim.as_dict() for claim in claims[:_MAX_CLAIMS]],
        "manifest": bounded_manifest,
    }


def evidence_manifest_json(result: EvidenceGuardResult, *, max_bytes: int = 16_384) -> str:
    """Serialize a manifest for AgentMessage.model_receipt_json safely."""

    encoded = json.dumps(
        result.manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) <= max_bytes:
        return encoded.decode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return json.dumps(
        {
            "version": "agent_evidence_v1",
            "truncated": True,
            "size_bytes": len(encoded),
            "sha256": digest,
            "unverified_count": len(result.unverified_claims),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = [
    "EvidenceClaim",
    "EvidenceFact",
    "EvidenceGuardResult",
    "build_repair_request",
    "evidence_manifest_json",
    "guard_agent_response",
]
