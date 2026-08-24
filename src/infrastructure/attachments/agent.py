"""Owner-only file storage for validated Agent image attachments."""

from __future__ import annotations

import hashlib
import os
import re
import struct
import tempfile
from pathlib import Path

from domain.agent.attachments import (
    AGENT_IMAGE_MAX_BYTES,
    AGENT_IMAGE_MEDIA_TYPES,
    AgentImageAttachment,
)
from domain.common.errors import DataContractError

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SOF_MARKERS = frozenset(
    {
        *range(0xC0, 0xC4),
        *range(0xC5, 0xC8),
        *range(0xC9, 0xCC),
        *range(0xCD, 0xD0),
    }
)
_SAFE_ATTACHMENT_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,159}$")


def _png_dimensions(content: bytes) -> tuple[int, int]:
    if len(content) < 24 or not content.startswith(_PNG_SIGNATURE):
        raise DataContractError("Agent image is not a valid PNG")
    if content[12:16] != b"IHDR":
        raise DataContractError("Agent PNG is missing its IHDR header")
    width, height = struct.unpack(">II", content[16:24])
    return width, height


def _jpeg_dimensions(content: bytes) -> tuple[int, int]:
    if len(content) < 4 or content[:2] != b"\xff\xd8":
        raise DataContractError("Agent image is not a valid JPEG")
    index = 2
    while index + 3 < len(content):
        if content[index] != 0xFF:
            index += 1
            continue
        while index < len(content) and content[index] == 0xFF:
            index += 1
        if index >= len(content):
            break
        marker = content[index]
        index += 1
        if marker in {0xD8, 0xD9}:
            continue
        if marker == 0xDA:
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > len(content):
            break
        segment_length = int.from_bytes(content[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(content):
            break
        if marker in _JPEG_SOF_MARKERS:
            if segment_length < 7:
                break
            height = int.from_bytes(content[index + 3 : index + 5], "big")
            width = int.from_bytes(content[index + 5 : index + 7], "big")
            return width, height
        index += segment_length
    raise DataContractError("Agent JPEG is missing its frame dimensions")


def _dimensions(media_type: str, content: bytes) -> tuple[int, int]:
    if media_type == "image/png":
        return _png_dimensions(content)
    if media_type == "image/jpeg":
        return _jpeg_dimensions(content)
    raise DataContractError("Agent image media type is unsupported")


def _safe_original_name(value: str | None) -> str | None:
    if value is None:
        return None
    name = Path(value).name.strip()
    if not name or name in {".", ".."} or any(ord(char) < 32 for char in name):
        return None
    return name[:255]


class FileAgentAttachmentStore:
    """Store image bytes below a private, non-browsable project directory."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        os.chmod(self._root, 0o700)

    def _path(self, attachment_id: str) -> Path:
        if _SAFE_ATTACHMENT_ID.fullmatch(attachment_id) is None:
            raise DataContractError("Agent attachment ID is invalid")
        path = (self._root / f"{attachment_id}.img").resolve()
        if path.parent != self._root:
            raise DataContractError("Agent attachment path escaped its private root")
        return path

    def save(
        self,
        *,
        attachment_id: str,
        content: bytes,
        media_type: str,
        original_name: str | None,
    ) -> AgentImageAttachment:
        if media_type not in AGENT_IMAGE_MEDIA_TYPES:
            raise DataContractError("Agent image media type is unsupported")
        if not isinstance(content, bytes) or not 1 <= len(content) <= AGENT_IMAGE_MAX_BYTES:
            raise DataContractError("Agent image bytes are out of bounds")
        width, height = _dimensions(media_type, content)
        attachment = AgentImageAttachment(
            attachment_id=attachment_id,
            media_type=media_type,
            byte_size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            width=width,
            height=height,
            original_name=_safe_original_name(original_name),
        )
        target = self._path(attachment_id)
        if target.exists():
            existing = self.read(attachment)
            if existing == content:
                return attachment
            raise DataContractError("Agent attachment ID was already used")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{attachment_id}.",
            suffix=".tmp",
            dir=self._root,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
            os.chmod(target, 0o600)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return attachment

    def read(self, attachment: AgentImageAttachment) -> bytes:
        path = self._path(attachment.attachment_id)
        try:
            content = path.read_bytes()
        except FileNotFoundError as error:
            raise DataContractError("Agent image attachment is unavailable") from error
        if (
            len(content) != attachment.byte_size
            or hashlib.sha256(content).hexdigest() != attachment.sha256
        ):
            raise DataContractError("Agent image attachment failed integrity verification")
        return content

    def delete(self, attachment: AgentImageAttachment) -> None:
        self._path(attachment.attachment_id).unlink(missing_ok=True)


__all__ = ["FileAgentAttachmentStore"]
