# Trading Partner — Known Issues

> Updated: 2026-09-08
> Scope: active defects and accepted operational constraints only. Resolved work
> belongs in the current product specification and release notes.

## Active issues

No tracked code defects remain in this list. Resolved fixes are recorded in
[Unreleased](../releases/unreleased.md).

## Accepted operational constraints

- Trusted-LAN Console mode is authenticated HTTP, not public hosting. The data API
  remains loopback-only; use a private TLS/device-identity network across untrusted
  links.
- Exact Moomoo Watchlist synchronization is bounded by OpenD group request limits;
  scheduled sync performs the slow exact replay while ordinary reads stay durable-only.
- Moomoo private-note enrichment uses an unofficial read-only internal Web surface and
  may degrade to local cache when authentication, throttling, or page shape changes.
  Summary-only text never reaches model analysis or Decision adoption.
- Reddit RSS and free public CME/DCE/Dukascopy/Polymarket routes are best effort.
  Unavailability remains typed and may use only explicitly configured bounded fallbacks.
- Portfolio performance remains native-currency and fail-closed. Cross-currency
  aggregation, missing corporate-action lots, unavailable fees, and transferred cost
  basis are not estimated.
- Migrations `0069` and `0070` are intentional forward-only data repairs. Restore uses
  a verified database backup, not an empty Alembic downgrade.
- Local/automated backtests, general autonomous trading, order replacement, complex
  orders, short selling, and unattended execution remain unavailable except for the
  closed installed SGOV cash-sweep scheduler.

See the [current product specification](../product.md) and
[Unreleased](../releases/unreleased.md) for implemented behavior.
