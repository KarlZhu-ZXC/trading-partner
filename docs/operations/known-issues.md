# Trading Partner — Known Issues

> Updated: 2026-08-03
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
- Risk v2 evaluates durable positions but does not consume broker open orders.
  Moomoo snapshots retain open orders for read-only display; Schwab open orders are
  not ingested. Pending-order exposure and duplicate-order prevention therefore
  remain unavailable rather than being treated as a pass.
- The scheduled account plus Watchlist synchronization is keyed to the XNYS close.
  QMT, A-share account synchronization, and FX aggregation are intentionally
  deferred for at least two months; A-share monitoring remains fact-only and does
  not imply an A-share broker snapshot.
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
| Release identity | Python package metadata, health, and Console API share one application version source |
| KR Console Monitor | Monitor builder resolves KR instruments and exposes `KR_POST_MARKET` without changing backend scope |
| Instrument first use | One shared local-first discovery gateway across market, technical, research, context, workflow, and Monitor facts |
| Watchlist reads/sync | Omitted Moomoo scope selects durable `All`; public sync refreshes every group and membership; pagination is explicit |
| Monitor read scope | Monitor-filtered runs contain only that Monitor's observations; Dashboard uses compact run summaries |
| ETF research | US ETF workflow uses ETF quote/technical/news/sentiment/macro facts without equity-only company calls |
| OTC Monitor sessions | Dukascopy XAUUSD/XAGUSD intervals skip known closures; optional IG Weekend Gold browser fallback is current-only, XAUUSD-only, and explicitly CFD/not-spot |
| Margin-account risk checks | A negative cash balance is retained as a signed cash ratio and evaluated as a policy breach instead of failing the complete risk result with `DATA_CONTRACT_ERROR` |
| Yahoo extended-hours quotes | A recovered pre/post-market last price clears unsupported regular-session range/volume fields and emits `EXTENDED_HOURS_SESSION_RANGE_UNAVAILABLE` |
| Technical interval input | MCP schemas advertise `1d`/`1w`; common daily/weekly aliases normalize at the DTO boundary instead of failing conversational calls |
| Schwab OAuth status | `next_action` follows current token health; only the successful `renew` command asks for one account-sync retry |

See [Phase 1](../phases/phase1.md), [Phase 2](../phases/phase2.md),
[Phase 3](../phases/phase3.md), and [release notes](../releases/v0.2.0.md) for the
accepted implementation details.
