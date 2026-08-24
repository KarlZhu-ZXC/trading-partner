"""Decode and validate image data URLs at the Console boundary."""

from __future__ import annotations

import base64
import binascii
import re

from domain.agent.attachments import AGENT_IMAGE_MAX_BYTES, AGENT_IMAGE_MEDIA_TYPES
from domain.common.errors import DataContractError

_DATA_URL = re.compile(
    r"^data:(?P<media_type>image/(?:png|jpeg));base64,(?P<data>[A-Za-z0-9+/=]+)$"
)


def decode_image_data_url(value: str) -> tuple[str, bytes]:
    """Decode one bounded base64 data URL without accepting remote URLs."""

    if not isinstance(value, str):
        raise DataContractError("Agent image data must be a data URL")
    match = _DATA_URL.fullmatch(value)
    if match is None:
        raise DataContractError("Agent image data URL is invalid")
    media_type = match.group("media_type")
    if media_type not in AGENT_IMAGE_MEDIA_TYPES:
        raise DataContractError("Agent image media type is unsupported")
    try:
        content = base64.b64decode(match.group("data"), validate=True)
    except (ValueError, binascii.Error) as error:
        raise DataContractError("Agent image data URL is not valid base64") from error
    if not 1 <= len(content) <= AGENT_IMAGE_MAX_BYTES:
        raise DataContractError("Agent image exceeds the size limit")
    return media_type, content


__all__ = ["decode_image_data_url"]
