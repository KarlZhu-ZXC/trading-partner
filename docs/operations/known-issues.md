# Trading Partner — Known Issues

> Updated: 2026-08-11
> Scope: reproducible defects and external product-boundary gaps only. Completed
> implementation narratives belong in phase specifications and release notes.

## Active queue

The 2026-08-07 durable-data audit found no existing cross-subject child references or
illegal Research Subject → Thesis → Trade Plan combinations. The remaining item is an
explicit transport boundary, not a claim that stored data is already corrupt.

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
- The scheduled account plus Watchlist synchronization is keyed to the XNYS close.
  QMT, A-share account synchronization, and FX aggregation are intentionally
  deferred for at least two months; A-share monitoring remains fact-only and does
  not imply an A-share broker snapshot.
- Local stdio MCP exposes no authenticated user identity, order write, fill,
  execution, or automated backtest runner. Runtime LLM use is limited to an enabled
  composite Monitor judgment or optional Trade Retro narration. Both receive only
  bounded deterministic facts and have no mutation/order port; Trade Retro remains
  valid with its deterministic Chinese fallback when no model is configured.

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
| Architecture | Sole 27-tool MCP vNext Shadow surface and capability-split provider adapters |
| Release identity | Python package metadata, health, and Console API share one application version source |
| KR Console Monitor | Monitor builder resolves KR instruments and exposes `KR_POST_MARKET` without changing backend scope |
| Instrument first use | One shared local-first discovery gateway across market, technical, research, context, workflow, and Monitor facts |
| Watchlist reads/sync | Omitted Moomoo scope selects durable `All`; public sync refreshes every group and membership; pagination is explicit |
| Monitor read scope | Monitor-filtered runs contain only that Monitor's observations; Dashboard uses compact run summaries |
| ETF research | US ETF workflow uses ETF quote/technical/news/sentiment/macro facts without equity-only company calls |
| OTC Monitor sessions | Dukascopy XAUUSD/XAGUSD/light-oil intervals skip known closures; weekend XAUUSD uses Binance PAXG/USDC, light oil uses Hyperliquid XYZ CL/USDC, and optional IG Weekend Gold is a final gold fallback; all are current-only labelled proxies |
| Weekend Provider diagnosis | Retryable weekend-reference calls use three bounded attempts; failed proxy/primary hops are persisted as secret-safe structured diagnostics and rendered in the Console Run drill-down |
| Margin-account risk checks | A negative cash balance is retained as a signed cash ratio and evaluated as a policy breach instead of failing the complete risk result with `DATA_CONTRACT_ERROR` |
| Moomoo margin usage | Securities-account financing usage maps OpenD `debtCash` (SDK `interest_charged_amount`), never the `initial_margin` collateral requirement; legacy snapshots expose unavailable rather than replaying a false breach |
| Yahoo extended-hours quotes | A recovered pre/post-market last price clears unsupported regular-session range/volume fields and emits `EXTENDED_HOURS_SESSION_RANGE_UNAVAILABLE` |
| Technical interval input | MCP schemas advertise `1d`/`1w`; common daily/weekly aliases normalize at the DTO boundary instead of failing conversational calls |
| Schwab OAuth status | `next_action` follows current token health; only the successful `renew` command asks for one account-sync retry |
| Telegram changed-point identity | Transition banners list each changed condition/threshold, bounded meaning, severity, and state instead of a bare `TRIGGERED`/`RECOVERED` label |
| Telegram post-market duplication | A market-close run persists transition events but sends exactly one run-linked digest; no duplicate per-symbol event cards, and price-change percentages keep two decimals |
| Provider burst admission | Router-managed Provider calls atomically reserve bounded current/future slots and wait asynchronously; local queue expiry is `PROVIDER_ADMISSION_TIMEOUT`, distinct from an upstream `PROVIDER_RATE_LIMIT_ERROR` |
| Research/Monitor lifecycle (`RESEARCH-STATE-004`) | ACTIVE/PAUSED Monitors require an ACTIVE Research Subject; a Research Subject or live Trade Plan cannot retire while a linked Monitor remains ACTIVE/PAUSED. Callers must archive the Monitor explicitly; no hidden cascade occurs. |
| Candidate child scope (`RESEARCH-STATE-002`) | Assumption, Invalidation, relaxed-Invalidation, and Open Question references are checked against their owning Subject/Thesis/revision at proposal and again at confirmation; cross-scope or tampered candidates fail atomically. |
| Research Subject links (`RESEARCH-STATE-003`) | JSON-held `linked_subject_ids` reject missing targets, self-links, and duplicates on direct writes and Candidate proposal, then revalidate before confirmation. |
| Pending-order risk | Risk v2 includes safely valued remaining BUY open-order notional in prospective exposure and explicitly reports SELL orders, unknown status, and incomplete valuation instead of treating pending exposure as absent. |

See [Phase 1](../phases/phase1.md), [Phase 2](../phases/phase2.md),
[Phase 3](../phases/phase3.md), and [release notes](../releases/v0.2.0.md) for the
accepted implementation details.
