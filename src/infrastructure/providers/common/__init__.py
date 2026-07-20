"""Provider resilience primitives, contract validation, and cache codecs.

Timeout / retry / circuit breaker / rate limiter / VerifiedMarketSnapshot
contract checks / D6b1 cache codec. No router wiring here.
"""

from infrastructure.providers.common.circuit_breaker import CircuitBreaker
from infrastructure.providers.common.contract_validation import (
    validate_verified_market_snapshot,
)
from infrastructure.providers.common.market_snapshot_category_adapter import (
    MarketSnapshotCategoryAdapter,
)
from infrastructure.providers.common.null_category_provider import NullCategoryProvider
from infrastructure.providers.common.rate_limiter import (
    DefaultRateLimitPolicy,
    ProviderRateLimiter,
    floor_window_start,
)
from infrastructure.providers.common.retry import (
    DEFAULT_RETRYABLE_ERROR_TYPES,
    RetryPolicy,
    delay_after_failure,
    run_with_retry,
)
from infrastructure.providers.common.timeout import run_with_timeout
from infrastructure.providers.common.unimplemented_vendor_adapter import (
    UnimplementedVendorAdapter,
)
from infrastructure.providers.common.verified_snapshot_cache_codec import (
    CODEC_ID as VERIFIED_MARKET_SNAPSHOT_CODEC_ID,
)
from infrastructure.providers.common.verified_snapshot_cache_codec import (
    VerifiedMarketSnapshotCacheCodec,
)

__all__ = [
    "DEFAULT_RETRYABLE_ERROR_TYPES",
    "VERIFIED_MARKET_SNAPSHOT_CODEC_ID",
    "CircuitBreaker",
    "DefaultRateLimitPolicy",
    "MarketSnapshotCategoryAdapter",
    "NullCategoryProvider",
    "ProviderRateLimiter",
    "RetryPolicy",
    "UnimplementedVendorAdapter",
    "VerifiedMarketSnapshotCacheCodec",
    "delay_after_failure",
    "floor_window_start",
    "run_with_retry",
    "run_with_timeout",
    "validate_verified_market_snapshot",
]
