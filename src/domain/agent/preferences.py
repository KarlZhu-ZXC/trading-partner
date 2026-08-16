"""Durable, non-factual preferences for the shared Agent channels.

Preferences deliberately describe presentation only.  They must never carry
prices, positions, balances, trade intent, or research state; those facts are
owned by their respective durable domains and are read through the normal
capability boundary.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TypedDict

from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime

_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")


class AgentPreferenceLanguage(StrEnum):
    ZH_CN = "zh-CN"
    EN = "en"


class AgentResponseDensity(StrEnum):
    COMPACT = "compact"
    STANDARD = "standard"
    DETAILED = "detailed"


class AgentRiskStyle(StrEnum):
    BALANCED = "balanced"
    CAUTIOUS = "cautious"
    DIRECT = "direct"


def _text(value: object, field_name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise DataContractError(f"{field_name} must be bounded nonblank text")
    return value.strip()


def _codes(value: Sequence[str], field_name: str = "preferred_source_codes") -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise DataContractError(f"{field_name} must be a sequence of source codes")
    if len(value) > 32:
        raise DataContractError(f"{field_name} contains too many source codes")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or _CODE.fullmatch(item.strip()) is None:
            raise DataContractError(f"{field_name} contains an invalid source code")
        normalized = item.strip()
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class AgentPreferences:
    """One owner-scoped presentation preference snapshot."""

    preferences_id: str
    owner_principal: str
    language: AgentPreferenceLanguage = AgentPreferenceLanguage.ZH_CN
    response_density: AgentResponseDensity = AgentResponseDensity.STANDARD
    preferred_source_codes: tuple[str, ...] = ()
    risk_style: AgentRiskStyle = AgentRiskStyle.BALANCED
    default_chart: bool = False
    web_background: bool = True
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        _text(self.preferences_id, "preferences_id", 160)
        _text(self.owner_principal, "owner_principal", 256)
        if not isinstance(self.language, AgentPreferenceLanguage):
            raise DataContractError("language is invalid")
        if not isinstance(self.response_density, AgentResponseDensity):
            raise DataContractError("response_density is invalid")
        if not isinstance(self.risk_style, AgentRiskStyle):
            raise DataContractError("risk_style is invalid")
        _codes(self.preferred_source_codes)
        if type(self.default_chart) is not bool:
            raise DataContractError("default_chart must be a boolean")
        if type(self.web_background) is not bool:
            raise DataContractError("web_background must be a boolean")
        if type(self.version) is not int or self.version < 1:
            raise DataContractError("version must be a positive integer")
        for field_name, value in (("created_at", self.created_at), ("updated_at", self.updated_at)):
            if value is not None:
                require_aware_datetime(value, field_name=field_name)
        if (
            self.created_at is not None
            and self.updated_at is not None
            and self.updated_at < self.created_at
        ):
            raise DataContractError("updated_at must not precede created_at")

    def as_dict(self) -> dict[str, object]:
        """Return only presentation fields; no fact or execution state leaks."""

        return {
            "language": self.language.value,
            "response_density": self.response_density.value,
            "preferred_source_codes": list(self.preferred_source_codes),
            "risk_style": self.risk_style.value,
            "default_chart": self.default_chart,
            "web_background": self.web_background,
            "version": self.version,
            "updated_at": self.updated_at.isoformat() if self.updated_at is not None else None,
        }


@dataclass(frozen=True, slots=True)
class AgentPreferencesRevision:
    """Append-only audit row for a preferences snapshot."""

    revision_id: str
    preferences_id: str
    owner_principal: str
    operation: str
    actor: str
    idempotency_key: str
    authorization_note: str
    preferences: AgentPreferences
    created_at: datetime

    def __post_init__(self) -> None:
        _text(self.revision_id, "revision_id", 160)
        _text(self.preferences_id, "preferences_id", 160)
        _text(self.owner_principal, "owner_principal", 256)
        if self.operation not in {"CREATE", "UPDATE", "RESET"}:
            raise DataContractError("operation is invalid")
        _text(self.actor, "actor", 256)
        if not isinstance(self.idempotency_key, str) or _IDEMPOTENCY.fullmatch(
            self.idempotency_key.strip()
        ) is None:
            raise DataContractError("idempotency_key is invalid")
        _text(self.authorization_note, "authorization_note", 2_000)
        if self.preferences.preferences_id != self.preferences_id:
            raise DataContractError("revision preferences identity mismatch")
        if self.preferences.owner_principal != self.owner_principal:
            raise DataContractError("revision owner mismatch")
        require_aware_datetime(self.created_at, field_name="created_at")


class _AgentPreferencesDefaults(TypedDict):
    language: AgentPreferenceLanguage
    response_density: AgentResponseDensity
    preferred_source_codes: tuple[str, ...]
    risk_style: AgentRiskStyle
    default_chart: bool
    web_background: bool


DEFAULT_AGENT_PREFERENCES: _AgentPreferencesDefaults = {
    "language": AgentPreferenceLanguage.ZH_CN,
    "response_density": AgentResponseDensity.STANDARD,
    "preferred_source_codes": (),
    "risk_style": AgentRiskStyle.BALANCED,
    "default_chart": False,
    "web_background": True,
}


__all__ = [
    "AgentPreferenceLanguage",
    "AgentPreferences",
    "AgentPreferencesRevision",
    "AgentResponseDensity",
    "AgentRiskStyle",
    "DEFAULT_AGENT_PREFERENCES",
]
