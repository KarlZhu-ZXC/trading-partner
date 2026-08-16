"""Byte-identical guard helpers shared by Provider adapters.

Only proven-identical, stateless guards belong here. Anything whose output
feeds Router fallback or retry decisions (HTTP status → typed error mapping,
retryability flags, diagnostic fields) stays adapter-local until a
per-Provider mapping table plus contract tests exist.
"""

from __future__ import annotations

from datetime import datetime

from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime


def require_as_of(*, as_of: datetime, clock_now: datetime) -> datetime:
    """Validate a cutoff-safe request timestamp and return the clock value.

    Byte-equivalent to the guard previously duplicated across fourteen
    adapters: rejects naive datetimes and future-relative cutoffs with the
    same typed error and details.
    """

    require_aware_datetime(as_of, field_name="as_of")
    require_aware_datetime(clock_now, field_name="clock.now")
    if as_of > clock_now:
        raise DataContractError(
            "as_of must not be in the future relative to clock",
            details={"field": "as_of", "rule": "not_future"},
        )
    return clock_now
