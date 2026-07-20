"""Durable state DTOs for the anonymous Reddit RSS provider."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
from domain.us_context.enums import USSentimentSource
from domain.us_context.models import USSentimentSample

_CONFIG_KEY_RE = re.compile(r"^[0-9a-f]{16}$")


@dataclass(frozen=True, slots=True)
class RedditSampleCacheEntry:
    """One validated, provider-specific successful RSS sample set."""

    instrument_id: str
    config_key: str
    samples: tuple[USSentimentSample, ...]
    fetched_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        parse_instrument_id(self.instrument_id)
        if (
            not isinstance(self.config_key, str)
            or _CONFIG_KEY_RE.fullmatch(self.config_key) is None
        ):
            raise DataContractError(
                "config_key must be 16 lowercase hexadecimal characters",
                details={"field": "config_key"},
            )
        if not isinstance(self.samples, tuple) or any(
            not isinstance(sample, USSentimentSample)
            or sample.instrument_id != self.instrument_id
            or sample.source is not USSentimentSource.REDDIT
            for sample in self.samples
        ):
            raise DataContractError(
                "samples must be a coherent USSentimentSample tuple",
                details={"field": "samples"},
            )
        require_aware_datetime(self.fetched_at, field_name="fetched_at")
        require_aware_datetime(self.expires_at, field_name="expires_at")
        if self.expires_at < self.fetched_at:
            raise DataContractError(
                "expires_at must not precede fetched_at",
                details={"field": "expires_at"},
            )
