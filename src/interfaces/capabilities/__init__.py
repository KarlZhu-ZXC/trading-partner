"""Transport-neutral capability contracts and registry."""

from typing import Any

from .policy import (
    APPEND,
    APPEND_OPEN_WORLD,
    CACHE_DISCOVERY,
    EVALUATE,
    LOCAL_ARTIFACT,
    MANAGE,
    MANAGE_OPEN_WORLD,
    READ_DURABLE,
    READ_PROVIDER,
    SYNC,
    CapabilityAnnotations,
    CapabilityEffect,
    CapabilityPolicy,
    ConfirmationPolicy,
)
from .registry import (
    CapabilityConfirmationRequiredError,
    CapabilityInvocationError,
    CapabilityNotFoundError,
    CapabilityRegistrar,
    CompactCapabilityRegistry,
    CompactOperationDescriptor,
    RegisteredCapability,
    RegisteredTool,
)


def create_capability_registry(*args: Any, **kwargs: Any) -> CompactCapabilityRegistry:
    """Lazily compose the graph so importing core contracts stays SDK-free."""

    from .factory import create_capability_registry as _create_capability_registry

    return _create_capability_registry(*args, **kwargs)

__all__ = [
    "APPEND",
    "APPEND_OPEN_WORLD",
    "CACHE_DISCOVERY",
    "EVALUATE",
    "LOCAL_ARTIFACT",
    "MANAGE",
    "MANAGE_OPEN_WORLD",
    "READ_DURABLE",
    "READ_PROVIDER",
    "SYNC",
    "CapabilityAnnotations",
    "CapabilityConfirmationRequiredError",
    "CapabilityEffect",
    "CapabilityInvocationError",
    "CapabilityNotFoundError",
    "CapabilityPolicy",
    "CapabilityRegistrar",
    "CompactCapabilityRegistry",
    "CompactOperationDescriptor",
    "ConfirmationPolicy",
    "RegisteredCapability",
    "RegisteredTool",
    "create_capability_registry",
]
