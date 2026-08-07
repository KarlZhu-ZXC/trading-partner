"""Deterministic policy for Research Subject title and summary metadata.

Research Subject metadata identifies the durable research object and its scope.  It is not a
Thesis or Trade Plan, so action-plan language belongs in those separate records.
This module intentionally uses only bounded string matching; it does not infer
intent with an NLP model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from domain.common.errors import InputValidationError, SubjectMetadataPolicyViolation

CASE_METADATA_ACTION_PLAN_POLICY = "ACTION_PLAN_LANGUAGE"
MAX_SUBJECT_TITLE_LENGTH = 200
MAX_SUBJECT_SUMMARY_LENGTH = 4000

_CN_ACTION_PLAN_TERMS = (
    "加仓",
    "减仓",
    "建仓",
    "清仓",
    "补仓",
    "抄底",
    "止盈",
    "止损",
    "分批买入",
    "分批卖出",
    "买入计划",
    "卖出计划",
    "仓位计划",
    "仓位管理",
    "敞口管理",
    "交易计划",
    "不追价",
)

_EN_ACTION_PLAN_TERMS = (
    "buy plan",
    "sell plan",
    "add position",
    "trim position",
    "position plan",
    "position sizing",
    "trade plan",
    "take profit",
    "stop loss",
    "scale in",
    "scale out",
    "entry plan",
    "exit plan",
)

# Keep matching word/phrase based.  In particular, ``trade`` or ``plan`` alone
# is not forbidden, and ``capital expenditure plan`` remains valid research
# context.  ASCII boundaries avoid matching a phrase embedded in a larger word.
_EN_ACTION_PLAN_PATTERNS = tuple(
    re.compile(
        rf"(?<![a-z0-9]){re.escape(term).replace(r'\ ', r'\s+')}(?![a-z0-9])",
        flags=re.IGNORECASE,
    )
    for term in _EN_ACTION_PLAN_TERMS
)


@dataclass(frozen=True, slots=True)
class SubjectMetadata:
    """Normalized, policy-validated Research Subject metadata."""

    title: str
    summary: str


def validate_subject_metadata(*, title: str, summary: str) -> SubjectMetadata:
    """Normalize and validate a Research Subject title and summary as one effective pair.

    Length/blank checks use the same bounds as the public schema.  Forbidden
    action-plan language raises a typed non-retryable error whose details contain
    only the field and stable policy code; the rejected input is never echoed.
    """

    title_n = title.strip()
    summary_n = summary.strip()
    _validate_length(title_n, field="title", maximum=MAX_SUBJECT_TITLE_LENGTH)
    _validate_length(summary_n, field="summary", maximum=MAX_SUBJECT_SUMMARY_LENGTH)

    _validate_policy(field="title", value=title_n, title=True)
    _validate_policy(field="summary", value=summary_n, title=False)
    return SubjectMetadata(title=title_n, summary=summary_n)


def normalize_subject_metadata(*, title: str, summary: str) -> SubjectMetadata:
    """Backward-friendly alias for :func:`validate_subject_metadata`."""

    return validate_subject_metadata(title=title, summary=summary)


def _validate_length(value: str, *, field: str, maximum: int) -> None:
    if not value or len(value) > maximum:
        raise InputValidationError(
            f"{field} must be 1..{maximum} characters",
            details={"field": field, "maximum": maximum},
        )


def _validate_policy(*, field: str, value: str, title: bool) -> None:
    if title and value.endswith(("计划", "方案")):
        _raise_policy_violation(field)

    if any(term in value for term in _CN_ACTION_PLAN_TERMS):
        _raise_policy_violation(field)

    folded = value.casefold()
    if any(pattern.search(folded) is not None for pattern in _EN_ACTION_PLAN_PATTERNS):
        _raise_policy_violation(field)


def _raise_policy_violation(field: str) -> None:
    raise SubjectMetadataPolicyViolation(
        "Research Subject title and summary must describe a research object "
        "and scope, not an action plan",
        details={"field": field, "policy_code": CASE_METADATA_ACTION_PLAN_POLICY},
    )


__all__ = [
    "CASE_METADATA_ACTION_PLAN_POLICY",
    "MAX_SUBJECT_SUMMARY_LENGTH",
    "MAX_SUBJECT_TITLE_LENGTH",
    "SubjectMetadata",
    "normalize_subject_metadata",
    "validate_subject_metadata",
]
