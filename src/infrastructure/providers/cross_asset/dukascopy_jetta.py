"""Deterministic Dukascopy Jetta bucket planning.

This mirrors the request strategy used by the current ``dukascopy-node``
client without adding a Node.js runtime dependency.  Minute candles are stored
in UTC-day buckets, hourly candles in UTC-month buckets, and daily candles in
UTC-year buckets.  The active bucket uses a structured ``from`` query and must
never be treated as immutable cache data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.cross_asset.enums import OfferSide
from domain.us_market.enums import USBarInterval

JETTA_ROOT = "https://jetta.dukascopy.com/v1"
JETTA_BATCH_SIZE = 10
JETTA_BATCH_PAUSE_SECONDS = 1.0
JETTA_RETRY_COUNT = 0


@dataclass(frozen=True, slots=True)
class DukascopyBucketRequest:
    url: str
    params: dict[str, str]
    mutable: bool


def _bucket_start(value: datetime, interval: USBarInterval) -> datetime:
    value = value.astimezone(UTC)
    if interval is USBarInterval.ONE_MINUTE:
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    if interval is USBarInterval.SIXTY_MINUTES:
        return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if interval is USBarInterval.ONE_DAY:
        return value.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    raise DataContractError(
        "bar interval is not supported by Dukascopy Jetta",
        details={"field": "interval", "rule": "jetta_native_bucket"},
    )


def _next_bucket(value: datetime, interval: USBarInterval) -> datetime:
    if interval is USBarInterval.ONE_MINUTE:
        from datetime import timedelta

        return value + timedelta(days=1)
    if interval is USBarInterval.SIXTY_MINUTES:
        if value.month == 12:
            return value.replace(year=value.year + 1, month=1)
        return value.replace(month=value.month + 1)
    if interval is USBarInterval.ONE_DAY:
        return value.replace(year=value.year + 1)
    raise DataContractError(
        "bar interval is not supported by Dukascopy Jetta",
        details={"field": "interval", "rule": "jetta_native_bucket"},
    )


def _source(interval: USBarInterval) -> str:
    if interval is USBarInterval.ONE_MINUTE:
        return "minute"
    if interval is USBarInterval.SIXTY_MINUTES:
        return "hour"
    if interval is USBarInterval.ONE_DAY:
        return "day"
    raise DataContractError(
        "bar interval is not supported by Dukascopy Jetta",
        details={"field": "interval", "rule": "jetta_native_bucket"},
    )


def generate_jetta_bucket_requests(
    *,
    instrument_code: str,
    interval: USBarInterval,
    offer_side: OfferSide,
    start_at: datetime,
    end_at: datetime,
    now: datetime,
) -> tuple[DukascopyBucketRequest, ...]:
    """Return ordered bucket requests for ``[start_at, end_at)``.

    URL query data stays in ``params`` so the bounded transport can validate the
    host/path independently and never receives an adapter-built raw query string.
    """

    require_aware_datetime(start_at, field_name="start_at")
    require_aware_datetime(end_at, field_name="end_at")
    require_aware_datetime(now, field_name="now")
    if end_at <= start_at:
        return ()
    if offer_side not in {OfferSide.BID, OfferSide.ASK}:
        raise DataContractError(
            "offer_side must be B or A",
            details={"field": "offer_side", "rule": "enum"},
        )
    if not instrument_code or "/" in instrument_code or "?" in instrument_code:
        raise DataContractError(
            "Dukascopy Jetta instrument code is invalid",
            details={"field": "instrument_code", "rule": "fixed_mapping"},
        )

    start_at = start_at.astimezone(UTC)
    date_limit = min(end_at.astimezone(UTC), now.astimezone(UTC))
    if start_at >= date_limit:
        return ()

    source = _source(interval)
    side = "BID" if offer_side is OfferSide.BID else "ASK"
    bucket = _bucket_start(start_at, interval)
    out: list[DukascopyBucketRequest] = []
    while bucket < date_limit:
        bucket_end = _next_bucket(bucket, interval)
        active = bucket <= now.astimezone(UTC) < bucket_end
        base = f"{JETTA_ROOT}/candles/{source}/{instrument_code}/{side}"
        if active:
            out.append(
                DukascopyBucketRequest(
                    url=base,
                    params={"from": str(int(bucket.timestamp() * 1000))},
                    mutable=True,
                )
            )
        elif interval is USBarInterval.ONE_MINUTE:
            out.append(
                DukascopyBucketRequest(
                    url=f"{base}/{bucket.year}/{bucket.month}/{bucket.day}",
                    params={},
                    mutable=False,
                )
            )
        elif interval is USBarInterval.SIXTY_MINUTES:
            out.append(
                DukascopyBucketRequest(
                    url=f"{base}/{bucket.year}/{bucket.month}",
                    params={},
                    mutable=False,
                )
            )
        else:
            out.append(
                DukascopyBucketRequest(
                    url=f"{base}/{bucket.year}",
                    params={},
                    mutable=False,
                )
            )
        bucket = bucket_end
    return tuple(out)


__all__ = [
    "DukascopyBucketRequest",
    "JETTA_BATCH_PAUSE_SECONDS",
    "JETTA_BATCH_SIZE",
    "JETTA_RETRY_COUNT",
    "JETTA_ROOT",
    "generate_jetta_bucket_requests",
]
