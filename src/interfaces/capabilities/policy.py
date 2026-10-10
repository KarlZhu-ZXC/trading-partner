"""Transport-neutral capability effects, confirmation, and safety hints."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CapabilityEffect(StrEnum):
    """The durable effect class of one capability."""

    READ_DURABLE = "READ_DURABLE"
    READ_PROVIDER = "READ_PROVIDER"
    CACHE_DISCOVERY = "CACHE_DISCOVERY"
    APPEND = "APPEND"
    APPEND_OPEN_WORLD = "APPEND_OPEN_WORLD"
    MANAGE = "MANAGE"
    MANAGE_OPEN_WORLD = "MANAGE_OPEN_WORLD"
    SYNC = "SYNC"
    EVALUATE = "EVALUATE"
    LOCAL_ARTIFACT = "LOCAL_ARTIFACT"


class ConfirmationPolicy(StrEnum):
    """How a capability caller must authorize a write-like operation."""

    NONE = "NONE"
    MATCH_CAPABILITY_NAME = "MATCH_CAPABILITY_NAME"


@dataclass(frozen=True, slots=True)
class CapabilityAnnotations:
    """Protocol-neutral safety hints with a stable serialization projection.

    The field names intentionally match the existing public annotation payload so
    old catalog and contract consumers can continue to call ``model_dump``.  The
    class has no dependency on a transport SDK; adapters map it to their native
    annotation type at the boundary.
    """

    title: str | None = None
    readOnlyHint: bool | None = None
    destructiveHint: bool | None = None
    idempotentHint: bool | None = None
    openWorldHint: bool | None = None

    def model_dump(self, *, mode: str = "python") -> dict[str, object]:
        """Return a Pydantic-compatible mapping used by existing callers."""

        del mode
        return {
            "title": self.title,
            "readOnlyHint": self.readOnlyHint,
            "destructiveHint": self.destructiveHint,
            "idempotentHint": self.idempotentHint,
            "openWorldHint": self.openWorldHint,
        }


@dataclass(frozen=True, slots=True)
class CapabilityPolicy:
    """Effect and confirmation contract shared by every interface adapter."""

    effect: CapabilityEffect
    confirmation: ConfirmationPolicy
    read_only: bool
    destructive: bool
    idempotent: bool
    open_world: bool

    @property
    def confirmation_required(self) -> bool:
        return self.confirmation is not ConfirmationPolicy.NONE

    @property
    def annotations(self) -> CapabilityAnnotations:
        """Compatibility projection for existing catalog/contract consumers."""

        return CapabilityAnnotations(
            readOnlyHint=self.read_only,
            destructiveHint=self.destructive,
            idempotentHint=self.idempotent,
            openWorldHint=self.open_world,
        )


def _policy(
    effect: CapabilityEffect,
    *,
    confirmation: ConfirmationPolicy,
    read_only: bool,
    destructive: bool,
    idempotent: bool,
    open_world: bool,
) -> CapabilityPolicy:
    return CapabilityPolicy(
        effect=effect,
        confirmation=confirmation,
        read_only=read_only,
        destructive=destructive,
        idempotent=idempotent,
        open_world=open_world,
    )


READ_DURABLE = _policy(
    CapabilityEffect.READ_DURABLE,
    confirmation=ConfirmationPolicy.NONE,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
READ_PROVIDER = _policy(
    CapabilityEffect.READ_PROVIDER,
    confirmation=ConfirmationPolicy.NONE,
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=True,
)
CACHE_DISCOVERY = _policy(
    CapabilityEffect.CACHE_DISCOVERY,
    confirmation=ConfirmationPolicy.NONE,
    read_only=False,
    destructive=False,
    idempotent=True,
    open_world=True,
)
MANAGE = _policy(
    CapabilityEffect.MANAGE,
    confirmation=ConfirmationPolicy.MATCH_CAPABILITY_NAME,
    read_only=False,
    destructive=True,
    idempotent=True,
    open_world=False,
)
MANAGE_OPEN_WORLD = _policy(
    CapabilityEffect.MANAGE_OPEN_WORLD,
    confirmation=ConfirmationPolicy.MATCH_CAPABILITY_NAME,
    read_only=False,
    destructive=True,
    idempotent=True,
    open_world=True,
)
APPEND = _policy(
    CapabilityEffect.APPEND,
    confirmation=ConfirmationPolicy.MATCH_CAPABILITY_NAME,
    read_only=False,
    destructive=False,
    idempotent=True,
    open_world=False,
)
APPEND_OPEN_WORLD = _policy(
    CapabilityEffect.APPEND_OPEN_WORLD,
    confirmation=ConfirmationPolicy.MATCH_CAPABILITY_NAME,
    read_only=False,
    destructive=False,
    idempotent=True,
    open_world=True,
)
SYNC = _policy(
    CapabilityEffect.SYNC,
    confirmation=ConfirmationPolicy.MATCH_CAPABILITY_NAME,
    read_only=False,
    destructive=False,
    idempotent=False,
    open_world=True,
)
EVALUATE = _policy(
    CapabilityEffect.EVALUATE,
    confirmation=ConfirmationPolicy.MATCH_CAPABILITY_NAME,
    read_only=False,
    destructive=False,
    idempotent=True,
    open_world=True,
)
LOCAL_ARTIFACT = _policy(
    CapabilityEffect.LOCAL_ARTIFACT,
    confirmation=ConfirmationPolicy.MATCH_CAPABILITY_NAME,
    read_only=False,
    destructive=False,
    idempotent=True,
    open_world=True,
)
