# Trading Partner — Known Issues

> Updated: 2026-09-03
> Scope: active defects and accepted operational constraints only. Resolved work
> belongs in the Phase specifications and release notes.

## Active issue

| ID | Status | Boundary | Summary |
|---|---|---|---|
| `AUTH-001` | deferred | Confirmation identity | Local stdio confirmation fields are caller assertions. Authenticated principal binding must be supplied by an authenticated MCP host/transport. |

`ActorContext` distinguishes `CALLER_ASSERTED` from `AUTHENTICATED` and rejects
trusted-principal/`confirmed_by` mismatches. A local stdio process cannot upgrade an
assertion into authenticated identity by itself.

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
- KR support covers quote, bars, technicals, manual Watchlist, and Monitoring. DART
  fundamentals, KR news/sentiment/breadth, account sync, and KR Position Sizing remain
  unsupported.
- Portfolio performance remains native-currency and fail-closed. Cross-currency
  aggregation, missing corporate-action lots, unavailable fees, and transferred cost
  basis are not estimated.
- Migrations `0069` and `0070` are intentional forward-only data repairs. Restore uses
  a verified database backup, not an empty Alembic downgrade.
- Local/automated backtests, general autonomous trading, order replacement, complex
  orders, short selling, and unattended execution remain unavailable except for the
  closed installed SGOV cash-sweep scheduler.

See [Phase 1](../phases/phase1.md), [Phase 2](../phases/phase2.md),
[Phase 3](../phases/phase3.md), [Phase 4](../phases/phase4.md), and
[Unreleased](../releases/unreleased.md) for implemented behavior.
