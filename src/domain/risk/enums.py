"""Frozen risk-enum values for Phase 2B risk checks and policy execution."""

from __future__ import annotations

from enum import StrEnum


class RiskCheckStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    BREACH = "BREACH"
    NOT_EVALUATED = "NOT_EVALUATED"


class RiskOverallStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    BREACH = "BREACH"
    INCOMPLETE = "INCOMPLETE"


class RiskSeverity(StrEnum):
    INFO = "INFO"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RiskConfirmer(StrEnum):
    SYSTEM_DEFAULT = "system_default"
    USER = "user"
    EXTERNAL_AGENT = "external_agent"
