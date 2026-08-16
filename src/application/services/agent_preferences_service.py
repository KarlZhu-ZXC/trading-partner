"""Explicit, versioned writes for Agent presentation preferences."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from application.ports.agent_preferences_repository import AgentPreferencesRepository
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from domain.agent.preferences import (
    DEFAULT_AGENT_PREFERENCES,
    AgentPreferenceLanguage,
    AgentPreferences,
    AgentPreferencesRevision,
    AgentResponseDensity,
    AgentRiskStyle,
)
from domain.common.errors import AgentPreferencesVersionConflict, DataContractError
from domain.common.ids import EntityIdPrefix

_FIELDS = frozenset(
    {
        "language",
        "response_density",
        "preferred_source_codes",
        "risk_style",
        "default_chart",
    }
)


class AgentPreferencesService:
    """Application service with no fact, portfolio, or execution fields."""

    def __init__(
        self,
        repository: AgentPreferencesRepository,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._id_generator = id_generator

    def get(self, owner_principal: str) -> AgentPreferences | None:
        _owner(owner_principal)
        return self._repository.get(owner_principal.strip())

    def update(
        self,
        owner_principal: str,
        patch: Mapping[str, object],
        *,
        expected_version: int,
        actor: str,
        idempotency_key: str,
        authorization_note: str,
    ) -> AgentPreferences:
        owner = _owner(owner_principal)
        expected = _version(expected_version, allow_zero=True)
        _actor(actor)
        _idempotency(idempotency_key)
        _note(authorization_note)
        if not isinstance(patch, Mapping) or not patch:
            raise DataContractError("preferences patch must be a non-empty object")
        unknown = sorted(str(key) for key in patch if key not in _FIELDS)
        if unknown:
            raise DataContractError("preferences contain unsupported fields")

        current = self._repository.get(owner)
        if current is None:
            if expected != 0:
                raise DataContractError("expected_version must be 0 for new preferences")
            now = self._clock.now()
            initial = _coerce_patch(patch)
            value = AgentPreferences(
                preferences_id=self._id_generator.new(EntityIdPrefix.AGENT_PREFERENCES),
                owner_principal=owner,
                language=initial.get(
                    "language", DEFAULT_AGENT_PREFERENCES["language"]
                ),
                response_density=initial.get(
                    "response_density",
                    DEFAULT_AGENT_PREFERENCES["response_density"],
                ),
                preferred_source_codes=initial.get(
                    "preferred_source_codes",
                    DEFAULT_AGENT_PREFERENCES["preferred_source_codes"],
                ),
                risk_style=initial.get(
                    "risk_style", DEFAULT_AGENT_PREFERENCES["risk_style"]
                ),
                default_chart=initial.get(
                    "default_chart", DEFAULT_AGENT_PREFERENCES["default_chart"]
                ),
                web_background=initial.get(
                    "web_background", DEFAULT_AGENT_PREFERENCES["web_background"]
                ),
                version=1,
                created_at=now,
                updated_at=now,
            )
            revision = self._revision(
                value,
                operation="CREATE",
                actor=actor,
                idempotency_key=idempotency_key,
                authorization_note=authorization_note,
            )
            return self._repository.create(value, revision)

        if current.version != expected:
            raise AgentPreferencesVersionConflict("Agent preferences version conflict")
        now = self._clock.now()
        changes = _coerce_patch(patch)
        value = replace(
            current,
            language=changes.get("language", current.language),
            response_density=changes.get(
                "response_density", current.response_density
            ),
            preferred_source_codes=changes.get(
                "preferred_source_codes", current.preferred_source_codes
            ),
            risk_style=changes.get("risk_style", current.risk_style),
            default_chart=changes.get("default_chart", current.default_chart),
            web_background=changes.get("web_background", current.web_background),
            version=current.version + 1,
            updated_at=now,
        )
        revision = self._revision(
            value,
            operation="UPDATE",
            actor=actor,
            idempotency_key=idempotency_key,
            authorization_note=authorization_note,
        )
        return self._repository.update(value, expected_version=expected, revision=revision)

    def reset(
        self,
        owner_principal: str,
        *,
        expected_version: int,
        actor: str,
        idempotency_key: str,
        authorization_note: str,
    ) -> AgentPreferences:
        owner = _owner(owner_principal)
        expected = _version(expected_version, allow_zero=True)
        _actor(actor)
        _idempotency(idempotency_key)
        _note(authorization_note)
        current = self._repository.get(owner)
        if current is None:
            if expected != 0:
                raise DataContractError("expected_version must be 0 for new preferences")
            now = self._clock.now()
            value = AgentPreferences(
                preferences_id=self._id_generator.new(EntityIdPrefix.AGENT_PREFERENCES),
                owner_principal=owner,
                language=DEFAULT_AGENT_PREFERENCES["language"],
                response_density=DEFAULT_AGENT_PREFERENCES["response_density"],
                preferred_source_codes=DEFAULT_AGENT_PREFERENCES[
                    "preferred_source_codes"
                ],
                risk_style=DEFAULT_AGENT_PREFERENCES["risk_style"],
                default_chart=DEFAULT_AGENT_PREFERENCES["default_chart"],
                web_background=DEFAULT_AGENT_PREFERENCES["web_background"],
                version=1,
                created_at=now,
                updated_at=now,
            )
            revision = self._revision(
                value,
                operation="RESET",
                actor=actor,
                idempotency_key=idempotency_key,
                authorization_note=authorization_note,
            )
            return self._repository.create(value, revision)
        if current.version != expected:
            raise AgentPreferencesVersionConflict("Agent preferences version conflict")
        now = self._clock.now()
        value = replace(
            current,
            language=DEFAULT_AGENT_PREFERENCES["language"],
            response_density=DEFAULT_AGENT_PREFERENCES["response_density"],
            preferred_source_codes=DEFAULT_AGENT_PREFERENCES[
                "preferred_source_codes"
            ],
            risk_style=DEFAULT_AGENT_PREFERENCES["risk_style"],
            default_chart=DEFAULT_AGENT_PREFERENCES["default_chart"],
            web_background=DEFAULT_AGENT_PREFERENCES["web_background"],
            version=current.version + 1,
            updated_at=now,
        )
        revision = self._revision(
            value,
            operation="RESET",
            actor=actor,
            idempotency_key=idempotency_key,
            authorization_note=authorization_note,
        )
        return self._repository.update(value, expected_version=expected, revision=revision)

    def history(
        self,
        owner_principal: str,
        *,
        limit: int = 100,
    ) -> tuple[AgentPreferencesRevision, ...]:
        owner = _owner(owner_principal)
        if type(limit) is not int or not 1 <= limit <= 500:
            raise DataContractError("limit must be between 1 and 500")
        return self._repository.list_history(owner, limit=limit)

    def _revision(
        self,
        value: AgentPreferences,
        *,
        operation: str,
        actor: str,
        idempotency_key: str,
        authorization_note: str,
    ) -> AgentPreferencesRevision:
        return AgentPreferencesRevision(
            revision_id=self._id_generator.new(EntityIdPrefix.AGENT_PREFERENCE_REVISION),
            preferences_id=value.preferences_id,
            owner_principal=value.owner_principal,
            operation=operation,
            actor=actor,
            idempotency_key=idempotency_key,
            authorization_note=authorization_note,
            preferences=value,
            created_at=self._clock.now(),
        )


def _owner(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 256:
        raise DataContractError("owner_principal must be bounded nonblank text")
    return value.strip()


def _actor(value: object) -> None:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 256:
        raise DataContractError("actor must be bounded nonblank text")


def _note(value: object) -> None:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 2_000:
        raise DataContractError("authorization_note must be bounded nonblank text")


def _idempotency(value: object) -> None:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 128:
        raise DataContractError("idempotency_key must be bounded nonblank text")


def _version(value: object, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if type(value) is not int or value < minimum:
        raise DataContractError("expected_version is invalid")
    return value


def _coerce_patch(patch: Mapping[str, object]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if "language" in patch:
        try:
            result["language"] = AgentPreferenceLanguage(str(patch["language"]))
        except ValueError as exc:
            raise DataContractError("language is invalid") from exc
    if "response_density" in patch:
        try:
            result["response_density"] = AgentResponseDensity(str(patch["response_density"]))
        except ValueError as exc:
            raise DataContractError("response_density is invalid") from exc
    if "preferred_source_codes" in patch:
        value = patch["preferred_source_codes"]
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise DataContractError("preferred_source_codes must be a sequence")
        # AgentPreferences performs the strict code and duplicate checks.
        result["preferred_source_codes"] = tuple(value)
    if "risk_style" in patch:
        try:
            result["risk_style"] = AgentRiskStyle(str(patch["risk_style"]))
        except ValueError as exc:
            raise DataContractError("risk_style is invalid") from exc
    for field_name in ("default_chart", "web_background"):
        if field_name in patch:
            value = patch[field_name]
            if type(value) is not bool:
                raise DataContractError(f"{field_name} must be a boolean")
            result[field_name] = value
    return result


__all__ = ["AgentPreferencesService"]
