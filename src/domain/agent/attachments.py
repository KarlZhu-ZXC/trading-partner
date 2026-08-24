"""Bounded image attachment records for Console Agent messages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from domain.common.errors import DataContractError

AGENT_IMAGE_MEDIA_TYPES = frozenset({"image/png", "image/jpeg"})
AGENT_IMAGE_MAX_BYTES = 2_000_000
AGENT_IMAGE_MAX_COUNT = 4
AGENT_IMAGE_MAX_TOTAL_BYTES = 4_000_000
AGENT_IMAGE_MAX_DIMENSION = 10_000
AGENT_IMAGE_MAX_PIXELS = 40_000_000

_ATTACHMENT_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,159}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class AgentImageAttachment:
    """Durable metadata for one private image file.

    The image bytes deliberately stay outside the database and are addressed
    only by ``attachment_id``.  The API layer derives an owner-checked URL
    when it serializes this record for the Console.
    """

    attachment_id: str
    media_type: str
    byte_size: int
    sha256: str
    width: int
    height: int
    original_name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.attachment_id, str) or _ATTACHMENT_ID.fullmatch(
            self.attachment_id
        ) is None:
            raise DataContractError("attachment_id is invalid")
        if self.media_type not in AGENT_IMAGE_MEDIA_TYPES:
            raise DataContractError("image media_type is unsupported")
        if (
            isinstance(self.byte_size, bool)
            or not isinstance(self.byte_size, int)
            or not 1 <= self.byte_size <= AGENT_IMAGE_MAX_BYTES
        ):
            raise DataContractError("image byte_size is out of bounds")
        if not isinstance(self.sha256, str) or _SHA256.fullmatch(self.sha256) is None:
            raise DataContractError("image sha256 is invalid")
        for field_name, value in (("width", self.width), ("height", self.height)):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= AGENT_IMAGE_MAX_DIMENSION
            ):
                raise DataContractError(f"image {field_name} is out of bounds")
        if self.width * self.height > AGENT_IMAGE_MAX_PIXELS:
            raise DataContractError("image pixel count is out of bounds")
        if self.original_name is not None and (
            not isinstance(self.original_name, str)
            or not self.original_name.strip()
            or len(self.original_name) > 255
            or any(ord(char) < 32 for char in self.original_name)
        ):
            raise DataContractError("image original_name is invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "attachment_id": self.attachment_id,
            "media_type": self.media_type,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
            "width": self.width,
            "height": self.height,
            "original_name": self.original_name,
        }

    @classmethod
    def from_dict(cls, value: object) -> AgentImageAttachment:
        if not isinstance(value, dict):
            raise DataContractError("image attachment metadata is invalid")
        try:
            return cls(
                attachment_id=value["attachment_id"],
                media_type=value["media_type"],
                byte_size=value["byte_size"],
                sha256=value["sha256"],
                width=value["width"],
                height=value["height"],
                original_name=value.get("original_name"),
            )
        except KeyError as error:
            raise DataContractError("image attachment metadata is incomplete") from error
        except TypeError as error:
            raise DataContractError("image attachment metadata has invalid fields") from error


def attachment_wire(value: AgentImageAttachment) -> dict[str, Any]:
    """Return only metadata safe for transport to the Console."""

    return value.as_dict()


__all__ = [
    "AGENT_IMAGE_MEDIA_TYPES",
    "AGENT_IMAGE_MAX_BYTES",
    "AGENT_IMAGE_MAX_COUNT",
    "AGENT_IMAGE_MAX_DIMENSION",
    "AGENT_IMAGE_MAX_PIXELS",
    "AGENT_IMAGE_MAX_TOTAL_BYTES",
    "AgentImageAttachment",
    "attachment_wire",
]
