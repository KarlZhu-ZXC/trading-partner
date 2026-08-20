"""Read-only Attention query vocabulary.

This is independent of the ReviewItem persistence ABI. Mapping from a
materialized ReviewItem onto an Attention item happens in Application.
"""

from domain.attention.enums import (
    ATTENTION_DEFAULT_LIMIT,
    ATTENTION_MAX_LIMIT,
    AttentionClosureCode,
    AttentionCoverageSource,
    AttentionCoverageState,
    AttentionScope,
    AttentionSeverity,
    AttentionSourceType,
    AttentionStatus,
    AttentionTrackingKind,
)

__all__ = [
    "ATTENTION_DEFAULT_LIMIT",
    "ATTENTION_MAX_LIMIT",
    "AttentionClosureCode",
    "AttentionCoverageSource",
    "AttentionCoverageState",
    "AttentionScope",
    "AttentionSeverity",
    "AttentionSourceType",
    "AttentionStatus",
    "AttentionTrackingKind",
]
