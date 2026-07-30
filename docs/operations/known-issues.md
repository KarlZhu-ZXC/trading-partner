# Trading Partner — Known Issues

> Updated: 2026-07-30
> Scope: reproducible defects and external product-boundary gaps only. Completed
> implementation narratives belong in phase specifications and release notes.

## Active queue

There are no confirmed open runtime defects at this revision.

| ID | Status | Boundary | Summary |
|---|---|---|---|
| `AUTH-001` | deferred | Confirmation identity | Local stdio confirmation fields are caller assertions. Authenticated principal binding must be supplied by a future authenticated MCP host/transport. |

`ActorContext` already distinguishes `CALLER_ASSERTED` from `AUTHENTICATED` and
rejects trusted-principal/`confirmed_by` mismatches. The local stdio server cannot
upgrade an assertion into authentication by itself.

## Accepted operational constraints

These are deliberate product boundaries, not open defects:

- Exact Moomoo Watchlist synchronization is `O(number of groups)`. OpenD permits
  10 `get_user_security` requests per 30 seconds, so a 24-group exact replay takes
  roughly one minute. Conversational reads use durable state; the scheduled CLI
  performs the exact refresh out of band.
- Reddit anonymous RSS remains best effort. A configured, spend-capped Apify
  fallback handles rate limiting or unavailability.
- CME/DCE/Dukascopy/Polymarket public endpoints may be unavailable through a
  particular network. `PROVIDER_PROXY_URL` is optional and failures stay typed.
- Local stdio MCP exposes no authenticated user identity, order write, fill,
  execution, runtime LLM, or automated backtest runner.

## Resolved index

Detailed contracts are now owned by the phase specifications and release notes.

| Area | Resolved behavior |
|---|---|
| Post-market sync | Calendar-aware due detection, bounded catch-up, session receipt, and idempotent skip |
| Moomoo Watchlist | Shared cross-process limiter, progress heartbeat, `IDX` normalization, and versioned SPG correction |
| Reddit | Durable cooldown, request coalescing, cached stale fallback, and bounded Apify recovery |
| Account persistence | Sanitized integrity classification, deterministic SQLite foreign keys, and parent-first snapshot persistence |
| Workflows | Request claim before provider access and durable terminal fact replay |
| Challenge Review | Payload-hashed idempotent start and resolution |
| Architecture | Sole 28-tool compact MCP surface and capability-split provider adapters |

See [Phase 1](../phases/phase1.md), [Phase 2](../phases/phase2.md),
[Phase 3](../phases/phase3.md), and [release notes](../releases/v0.2.0.md) for the
accepted implementation details.
