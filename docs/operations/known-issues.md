# Trading Partner — Known Issues

> Updated: 2026-07-20  
> Purpose: one cross-phase queue for verified defects and operational gaps  
> Rule: resolve one item at a time; move an item to `resolved` only with focused evidence

## Status vocabulary

- `open`: reproducible and ready to design or implement.
- `blocked`: cannot complete without an external prerequisite.
- `deferred`: understood product-boundary gap, intentionally not scheduled now.
- `resolved`: implemented and verified; retain the resolution evidence here.

## Current priority order

| Order | ID | Status | Area | Summary |
|---:|---|---|---|---|
| 1 | `MOOMOO-WL-001` | resolved | Watchlist sync | The roughly 63-second exact synchronization time is an accepted upstream constraint; exact refresh runs out of band and latency-sensitive reads use durable state |
| 2 | `MOOMOO-WL-007` | resolved | Watchlist sync | Full watchlist sync runs as an externally scheduled, account-first precision replay |
| 3 | `MOOMOO-WL-002` | open | Watchlist sync | CLI emits no progress while it waits for OpenD quota windows |
| 4 | `MOOMOO-WL-003` | resolved | OpenD coordination | CLI and MCP share one cross-process sliding-window rate limiter for Moomoo Watchlist and account calls |
| 5 | `MOOMOO-WL-004` | resolved | Instrument normalization | Moomoo `IDX` is normalized to Trading Partner `INDEX` while preserving the provider type |
| 6 | `MOOMOO-WL-006` | resolved | Instrument normalization | A versioned Moomoo correction registry converts the erroneous SPG identity while retaining raw provider provenance |
| 7 | `REDDIT-001` | resolved | US sentiment | A bounded Apify account fallback supplies Reddit threads when anonymous RSS returns 429 |
| 8 | `REDDIT-002` | resolved | US sentiment | Reddit honors provider cooldown signals and falls back to batched Apify reads |
| 9 | `PERSIST-001` | resolved | Account persistence | Integrity failures now expose a sanitized conflict type and reason-specific retryability instead of one generic uniqueness message |
| 10 | `PERSIST-002` | resolved | Account persistence | SQLite foreign-key enforcement and account parent/position insertion are deterministic across pooled connections |

## Moomoo Watchlist

### `MOOMOO-WL-001` — full-sync latency

- **Status:** resolved by product decision and operational separation. The upstream
  latency remains real, but it is no longer treated as a defect in the exact-sync
  implementation.

- **Observed:** the 2026-07-19 real run completed successfully in `62.83` seconds.
- **Current upstream shape:** 24 active groups: 18 system and 6 custom; 12 groups are empty.
- **Call shape:** two group-list reads plus one `get_user_security(group_name)` read
  for each of 24 groups. The 143 membership relations are returned in those 24
  group calls; there is no per-symbol request.
- **Constraint:** Moomoo permits at most 10 `get_user_security` requests per 30
  seconds. A faithful 24-group refresh therefore spans three request windows.
- **Uncapped experiment (2026-07-19):** a process-local override removed Trading
  Partner's limiter without changing the production setting. Full sync failed in
  `4.52` seconds with `PROVIDER_UNAVAILABLE_ERROR`. After the quota window reset,
  a direct read probe completed exactly 10 group-member calls and OpenD rejected
  call 11 after `2.646` seconds with: `Maximum 10 times per 30 seconds.` No
  add/remove operation was used. This proves the roughly 63-second full-sync time
  is imposed by the upstream quota rather than only by local conservative pacing.
- **Official API audit (v10.9):** the supported Watchlist surface contains only
  group listing, one named group's members, and one named group's mutation. The
  Proto request has one required `groupName`; member rows contain no group/tag
  field. The group-list response has only name/type, with no member count,
  revision, or last-modified timestamp. There is no documented Watchlist change
  push. Exact externally edited group membership therefore has an unavoidable
  `O(number of groups)` polling cost.
- **Existing automation comparison:** the local `美股：盘后小结` Automation does
  not perform a group-preserving sync. Its `DailyStockAutomation/portfolio_sync.py`
  sets `GROUP_NAME=All`, makes one `get_user_security(All)` call, and writes a flat
  CSV where every row has `group_name=All`. An equivalent 2026-07-19 read through
  the bundled `moomoo-trader` script took `1.26` seconds. This is a valid fast
  universe snapshot, but it cannot answer custom-group/tag questions or preserve
  membership lifecycle history.
- **Product decision (2026-07-19):** retain the exact, group-preserving full sync
  as the scheduled/default synchronization contract. Do not replace it with a
  rolling or partial refresh merely to reduce elapsed time.
- **Rejected as a complete solution:** a `quick` mode that always reads every
  custom group is fast only while the custom-group count stays below the
  10-requests-per-window limit; it also weakens the exact-full-sync contract.
- **Optimization boundary:** 24 membership reads require three upstream quota
  windows, so an exact sync has a hard wall-clock floor of roughly 60 seconds.
  Local work accounts for only about three additional seconds. Safe tuning may
  save a few seconds, but cannot make the provider refresh materially shorter
  without omitting groups.
- **Resolution:** retain the exact provider job and its compliant pacing. Run that
  refresh through the external post-market synchronization CLI; latency-sensitive
  conversational reads use the last successful durable snapshot with
  `refresh=false` and expose `last_synced_at`. Do not add a partial/rolling quick
  mode that loses custom-group membership semantics. Progress visibility and
  cross-process MCP/CLI coordination remain separate follow-up items under
  `MOOMOO-WL-002/003` and do not reopen this accepted latency constraint.
- **Acceptance evidence:** the real exact refresh completed successfully while
  preserving every active group and membership; the uncapped probe proved that
  OpenD itself rejects request 11 inside 30 seconds; the scheduled account-first
  CLI persists the result for later durable reads.

### `MOOMOO-WL-007` — post-market full sync is external and account-sequenced

- **Observed:** users expect an exact group-preserving watchlist state immediately
  market close without inflating MCP read latency.
- **Product decision:** the exact sync runs as a separate post-market job via
  `trading-partner-post-market-sync`; MCP conversational paths must read only the
  durable snapshot and never execute a full refresh inline.
- **Required sequence:** run all configured account snapshot operations first,
  then run the watchlist sync, so fresh positions and account context exist before
  any downstream review or post-market reporting.
- **Calendar constraint:** the sync should align with XNYS trading-calendar boundaries
  (including early-close and non-trading days). On regular close days it runs once
  when the external scheduler confirms the close window is complete; skip on
  incomplete windows.
- **Execution semantics:** sync is an exact membership replay, not incremental quick mode;
  it should complete as a full pass if the exchange of groups changes.
- **Resolution:** the CLI enforces XNYS close plus ten minutes, refreshes
  configured account sources before Watchlist, stores a per-session terminal
  receipt, skips completed sessions, retries imperfect sessions, and uses a
  project-owned non-blocking process lock. The active `美股：盘后小结` Automation
  now calls this CLI instead of `trigger_portfolio_sync.py`; its two due-checked
  local-time candidates cover regular-close daylight-saving changes without
  duplicating successful synchronization.

### `MOOMOO-WL-002` — missing progress output

- **Observed:** the CLI produces no output during its roughly one-minute wait.
- **Risk:** a human or Automation host may mistake a healthy quota wait for a hang.
- **Candidate resolution:** write bounded progress events to stderr while keeping
  exactly one machine-readable result JSON on stdout.

### `MOOMOO-WL-003` — cross-process coordination

- **Observed:** the CLI and MCP server have independent in-memory limiters. A
  scheduled full sync can overlap a conversational `refresh=true` call, and two
  Automation invocations can overlap each other.
- **Risk:** combined processes may exceed the upstream limit even though each
  process is locally compliant; simultaneous database refreshes are unnecessary.
- **Resolution:** every production `MoomooAccountAdapter` and
  `MoomooWatchlistAdapter` receives the same OpenD request coordinator from the
  composition root. Independent CLI and MCP processes persist reservations in
  `data/locks/moomoo_opend_rate_limit.log` under an exclusive file lock, enforcing
  a rolling 10-request/30-second window rather than a boundary-bursting fixed
  window.
- **Quota model:** group listing, group membership, Watchlist mutation, funds,
  positions, orders, and historical deals use separate upstream-aligned buckets.
  Account buckets are further scoped by the existing hashed account reference;
  raw account IDs are never written to the coordination file. `get_acc_list` is
  not locally capped because the audited OpenD documentation does not specify a
  request-frequency limit for it.
- **Concurrency semantics:** the limiter reserves and fsyncs one request slot
  before the SDK call, then releases the file lock immediately. A process waiting
  for one exhausted bucket does not block unrelated endpoint/account buckets.
  The existing post-market process lock still rejects duplicate full-sync jobs;
  the shared limiter covers MCP-versus-CLI and account-versus-Watchlist overlap.
- **Acceptance evidence:** focused tests use two limiter instances against one
  file to prove cross-instance waiting, independent operation/account buckets,
  malformed-tail tolerance, owner-only file mode, and adapter-to-bucket routing.

### `MOOMOO-WL-004` — `IDX` normalization

- **Observed:** `US..NDX` and `US..SPX` are stored with
  `research_supported=false` because Moomoo emits `stock_type=IDX` while the
  adapter recognizes only `INDEX`.
- **Resolution:** the Moomoo provider codec maps both `IDX` and `INDEX` to the
  existing domain `AssetType.INDEX`, while `provider_asset_type` retains the raw
  `IDX` provenance value.
- **Acceptance evidence:** focused adapter tests prove `US..NDX` and `US..SPX`
  resolve to `index:US:.NDX` and `index:US:.SPX` with
  `research_supported=true`.

### `MOOMOO-WL-006` — SPG upstream type conflicts with Instrument Master

- **Observed:** repeated direct 2026-07-19 OpenD reads of the `All` group returned
  `US.SPG`, `Simon Property`, and `stock_type=ETF`. It also returned the sentinel
  `listing_date=1970-01-01`.
- **Raw probe:** the read-only request was
  `OpenQuoteContext.get_user_security("All")` (Proto C2S:
  `groupName="All"`) and returned code `0`. The exact SPG response fields were
  `code="US.SPG"`, `name="Simon Property"`, `lot_size=1`,
  `stock_type="ETF"`, `stock_child_type="N/A"`, `stock_owner=""`,
  `option_type="N/A"`, `strike_time=""`, `strike_price="N/A"`,
  `suspension="N/A"`, `listing_date="1970-01-01"`, `stock_id=201707`,
  `delisting=false`, `main_contract=false`, and `last_trade_time=""`. The API
  returns the whole group and has no symbol filter; only the SPG row was emitted
  to diagnostics to avoid exposing the rest of the user's Watchlist.
- **Contrary fact:** the durable Instrument Master already contains
  `equity:US:SPG`, `Simon Property Group, Inc.`. SPG is an exchange-listed REIT
  equity, not an ETF.
- **Cause:** the Watchlist adapter currently converts Moomoo `stock_type`
  directly into an Instrument ID, so a bad upstream classification creates
  `etf:US:SPG` even when a validated local identity exists.
- **Resolution:** `config/moomoo_security_corrections.yaml` is the single tracked,
  versioned manual correction registry. Its audited SPG entry replaces the
  normalized asset type with `EQUITY` and display name with
  `Simon Property Group, Inc.`. The adapter still exposes raw
  `provider_asset_type=ETF`, so the correction does not rewrite provider
  provenance.
- **Safety contract:** every entry requires an exact provider code, supported
  domain asset type, corrected display name, reason, and verification date.
  Unknown fields, duplicates, invalid types, or malformed YAML fail closed at
  application startup. There is no symbol-specific branch in adapter code.
- **Acceptance evidence:** focused loader and adapter tests prove SPG becomes
  `equity:US:SPG` with the corrected name while raw `ETF` remains visible. The
  next exact Watchlist refresh updates durable membership state without changing
  membership lifecycle semantics.

## Reddit sentiment

### Existing mitigations already implemented

- `REDDIT_SUBREDDITS` configures an ordered, unique list of at
  most ten communities.
- Communities are requested serially with a configurable minimum interval,
  currently defaulting to six seconds.
- The adapter stops after the first 429, preserves earlier successful samples as
  partial data.
- Successful RSS samples are cached durably for 60 minutes per instrument and
  subreddit configuration; concurrent refreshes for the same instrument are
  coalesced within one application process.
- `Retry-After` and `X-Ratelimit-Reset` establish a bounded provider-wide
  cooldown shared through SQLite. During cooldown no Reddit request is made;
  expired durable samples are returned as `STALE` with `REDDIT_RATE_LIMITED`.

Anonymous RSS remains best effort; configured Apify fallback provides the
production recovery path when RSS is rate-limited or unavailable.

### `REDDIT-001` — anonymous RSS 429 recovery — resolved

- **Observed:** anonymous `www.reddit.com/r/<subreddit>/search.rss` calls can return
  429 even when requested serially.
- **Resolution:** when RSS returns 429, the adapter uses the configured Apify
  account to fetch bounded subreddit thread batches. Results remain identified as
  Reddit sentiment and carry `REDDIT_RATE_LIMITED` plus
  `REDDIT_APIFY_FALLBACK`; the Apify token is redacted and spending is capped.

### `REDDIT-002` — shared cooldown and header-aware backoff — resolved

- **Observed:** the adapter recognizes status 429 and stops the current subreddit
  loop, but does not consume rate-limit/Retry-After headers or persist a cooldown
  shared by overlapping workflows/processes.
- **Resolution:** migration `0011_reddit_rss_resilience` persists the provider-wide
  cooldown and last successful samples. The adapter honors both upstream timing
  headers, clamps cooldown to configured bounds, coalesces same-instrument refreshes,
  and serves typed stale data during rate limiting.

## Account persistence

### `PERSIST-001` — over-broad account uniqueness diagnosis — resolved

- **Observed:** every SQLAlchemy `IntegrityError` raised while appending an account
  snapshot was reported as `account snapshot uniqueness conflict` and inherited
  `retryable=true`, even when a child position, check, foreign-key, or unknown
  integrity constraint could not be fixed by retrying.
- **Resolution:** the public code remains the compatible `PERSISTENCE_ERROR`, while
  sanitized details now identify `entity` and one of `snapshot_id`,
  `fingerprint_concurrent_insert`, `position_identity`, `check_constraint`,
  `foreign_key`, or `unknown_integrity`. Messages are reason-specific. Structural
  constraints are non-retryable; snapshot-ID and not-yet-visible fingerprint
  races remain retryable.
- **Security:** classification compares only known constraint identifiers and
  never returns the raw database exception, SQL statement, parameters, account
  reference, positions, or balances.
- **Acceptance evidence:** repository tests cover fingerprint idempotency and
  snapshot-ID collision with redacted details. Domain validation rejects duplicate
  position identities before persistence.

### `PERSIST-002` — intermittent account position foreign-key failure — resolved

- **Observed:** a combined Schwab/Moomoo refresh could fail with sanitized
  `conflict_type=foreign_key`, although the same repository path passed isolated
  tests.
- **Root cause:** SQLite foreign keys were enabled by only some Unit of Work
  connections. Those pooled connections later made account persistence enforce
  the parent/child constraint, while SQLAlchemy had no ORM relationship requiring
  the snapshot parent to flush before its position rows. The result depended on
  which pooled connection was reused.
- **Resolution:** every SQLite connection created by the shared engine factory now
  enables foreign keys. Account persistence explicitly flushes the parent snapshot
  before appending positions.
- **Acceptance evidence:** 42 focused account, portfolio, workflow, Risk, Schwab,
  post-market, and migration tests pass; the local database reports foreign keys
  enabled and zero violations. A real combined refresh persisted three Schwab and
  Moomoo snapshots without errors.

## Resolution log

- `MOOMOO-WL-001` resolved on 2026-07-19 by accepting the verified OpenD quota
  floor, retaining exact group-preserving synchronization, scheduling it out of
  band, and using durable `refresh=false` reads for latency-sensitive conversations.
- `MOOMOO-WL-003` resolved on 2026-07-19 by a shared cross-process sliding-window
  coordinator for production CLI/MCP Watchlist and account OpenD calls.
- `MOOMOO-WL-004` resolved on 2026-07-19 by normalizing provider `IDX` to the
  existing domain `INDEX` identity without discarding raw provider provenance.
- `MOOMOO-WL-006` resolved on 2026-07-19 by the strict, versioned Moomoo security
  correction registry; SPG normalizes as an equity while raw OpenD `ETF` remains
  available for provenance.
- `REDDIT-001` resolved on 2026-07-19 by bounded Apify thread fallback when the
  anonymous RSS path returns 429 or is unavailable.
- `REDDIT-002` resolved on 2026-07-19 by durable cooldown, request coalescing,
  60-minute provider cache, and batched Apify recovery.
- `PERSIST-001` resolved on 2026-07-19 by sanitized constraint classification and
  reason-specific retryability for account and portfolio snapshot persistence.
- `PERSIST-002` resolved on 2026-07-20 by deterministic SQLite foreign-key
  enforcement and explicit account-snapshot parent flushing; a real combined
  Schwab/Moomoo refresh completed without persistence errors.
