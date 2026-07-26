"""Bucket planning contracts mirrored from the current dukascopy-node client."""

from datetime import UTC, datetime

from domain.cross_asset.enums import OfferSide
from domain.us_market.enums import USBarInterval
from infrastructure.providers.cross_asset.dukascopy_jetta import (
    JETTA_BATCH_PAUSE_SECONDS,
    JETTA_BATCH_SIZE,
    JETTA_RETRY_COUNT,
    generate_jetta_bucket_requests,
)


def test_bucket_granularity_and_active_from_query() -> None:
    now = datetime(2026, 7, 24, 12, tzinfo=UTC)
    minute = generate_jetta_bucket_requests(
        instrument_code="XAU-USD",
        interval=USBarInterval.ONE_MINUTE,
        offer_side=OfferSide.BID,
        start_at=datetime(2026, 7, 23, tzinfo=UTC),
        end_at=datetime(2026, 7, 25, tzinfo=UTC),
        now=now,
    )
    assert minute[0].url.endswith("/2026/7/23")
    assert minute[0].mutable is False
    assert minute[1].url.endswith("/candles/minute/XAU-USD/BID")
    assert minute[1].params == {
        "from": str(int(datetime(2026, 7, 24, tzinfo=UTC).timestamp() * 1000))
    }
    assert minute[1].mutable is True

    hourly = generate_jetta_bucket_requests(
        instrument_code="XAG-USD",
        interval=USBarInterval.SIXTY_MINUTES,
        offer_side=OfferSide.ASK,
        start_at=datetime(2026, 5, 1, tzinfo=UTC),
        end_at=datetime(2026, 7, 24, tzinfo=UTC),
        now=now,
    )
    assert [request.url.rsplit("/", 2)[-2:] for request in hourly[:2]] == [
        ["2026", "5"],
        ["2026", "6"],
    ]
    assert hourly[-1].url.endswith("/candles/hour/XAG-USD/ASK")


def test_upstream_client_pacing_defaults_are_frozen() -> None:
    assert JETTA_BATCH_SIZE == 10
    assert JETTA_BATCH_PAUSE_SECONDS == 1.0
    assert JETTA_RETRY_COUNT == 0
