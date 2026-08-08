"""Secret-safe structured diagnostics shared by Provider and Monitor receipts."""

from __future__ import annotations

import re
from dataclasses import dataclass

from domain.common.errors import DataContractError

_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_STATUS_CLASS = re.compile(r"^(?:[1-5]xx|none)$")


@dataclass(frozen=True, slots=True)
class ProviderFailureDiagnostic:
    """A bounded failure receipt that cannot contain URLs, payloads, or secrets."""

    provider: str
    stage: str
    error_code: str
    retryable: bool
    attempt_count: int
    error_type: str | None = None
    status_class: str | None = None
    status_code: int | None = None

    def __post_init__(self) -> None:
        if not _TOKEN.fullmatch(self.provider):
            raise DataContractError("diagnostic provider is invalid")
        if not _TOKEN.fullmatch(self.stage):
            raise DataContractError("diagnostic stage is invalid")
        if not _CODE.fullmatch(self.error_code):
            raise DataContractError("diagnostic error_code is invalid")
        if type(self.retryable) is not bool:
            raise DataContractError("diagnostic retryable must be bool")
        if type(self.attempt_count) is not int or not 1 <= self.attempt_count <= 10:
            raise DataContractError("diagnostic attempt_count must be 1..10")
        if self.error_type is not None and not _TOKEN.fullmatch(self.error_type):
            raise DataContractError("diagnostic error_type is invalid")
        if self.status_class is not None and not _STATUS_CLASS.fullmatch(self.status_class):
            raise DataContractError("diagnostic status_class is invalid")
        if self.status_code is not None and (
            type(self.status_code) is not int or not 100 <= self.status_code <= 599
        ):
            raise DataContractError("diagnostic status_code is invalid")


__all__ = ["ProviderFailureDiagnostic"]
