# Trading Partner Phase 2 — Watchlist, Risk, Monitoring, and Technical Engine

> Status: Watchlist completed 2026-07-18; Risk, Monitoring, and Technical Engine v2 completed 2026-07-20  
> Design version: Phase 2 v4  
> Public MCP surface: 28 compact tools; the former 52-tool rollback profile is removed.
> Phase 2 terminal migration: `0013_phase2c_monitoring`; repository head is
> `0028_provider_route_history`.
> Upstream source: exactly one of `MOOMOO` or `MANUAL_CSV`

## 1. Product outcome

Phase 2 makes the user's watchlist a durable research entry point inside Codex.
The user can list groups and members, add or remove one member through the active
source, restart Codex, and recover the latest persisted state from Trading
Partner's database.

Typical conversations:

```text
列出我的 Moomoo 半导体自选。
把 NVDA 加到 MAG。
从 Favorites 删除 FX.XAUUSD。
刷新 CSV 自选，然后告诉我哪些标的已经有 Investment Case。
```

Phase 2 does not run a hidden in-process alert daemon, backtest engine, or order
gateway. A local launchd integration may wake its deterministic due dispatcher hourly.
Its Portfolio Risk Engine is deterministic and read-only: it evaluates
facts and hypothetical additions but cannot place, modify, or cancel orders.

Scheduling remains an external host responsibility. For Codex Automation, cron,
or launchd, Phase 2 provides a read-only full-sync CLI:

```bash
cd <project-root>
uv run trading-partner-watchlist-sync
```

The command is designed for post-market automation. It refreshes every group and
membership from the one configured source, marks disappeared database rows inactive,
preserves history, and prints one JSON summary. Exit status is `0` only after a
fresh complete sync. It never calls the watchlist add/remove operations. Moomoo
reads are paced conservatively to remain within OpenD request-frequency limits, so a
large watchlist may take about one minute.

For the complete US post-market operation, use the due-checked orchestration CLI:

```bash
uv run trading-partner-post-market-sync
```

It refreshes every configured account provider first, then performs the same exact
Watchlist full sync. XNYS session rules determine whether the job is due ten minutes
after the real close, including holidays and early-close sessions. One terminal
receipt is stored per market session; successful sessions are skipped idempotently,
while partial or failed sessions remain retryable. Portfolio and Watchlist persist
independently, and neither command can place, modify, or cancel an order.

Scheduling remains an external-host responsibility; neither CLI turns the MCP
server into a scheduler.

## 2. Three separate concepts

```text
Active Watchlist Source
= Moomoo OpenD OR Manual CSV
= upstream group/member management

Watchlist Hub Store
= durable database groups, memberships, lifecycle, sync state, mutation receipts

Research WatchlistItem
= thesis hint, triggers, status, expiry, and Investment Case association
```

The existing Phase 1 `WatchlistItem` remains unchanged. It is research metadata,
not an external membership row. An external add does not fabricate a thesis hint.
An external remove never archives/deletes a Research WatchlistItem or Investment
Case.

## 3. Source selection

One setting chooses the only active upstream source:

```env
WATCHLIST_SOURCE=MOOMOO
```

or:

```env
WATCHLIST_SOURCE=MANUAL_CSV
MANUAL_WATCHLIST_CSV_PATH=data/watchlist.v1.csv
```

There is no `ALL`, cross-source merge, mirror comparison, reconcile, or automatic
migration. Switching the setting changes the upstream used by future refreshes;
it does not silently overwrite existing durable history.

Shared Moomoo settings remain:

```env
MOOMOO_HOST=127.0.0.1
MOOMOO_PORT=11111
```

Watchlist access uses Quote Context and never needs a trading account id, trade
password, or trade unlock.

## 4. Domain model

### 4.1 `WatchlistGroup`

```text
group_id                 watch_group_<uuid7>
source                   MOOMOO | MANUAL_CSV
source_group_key         exact upstream group identity
name                     exact Unicode display name
group_type               SYSTEM | CUSTOM | MANUAL
writable                 bool
active                   bool
first_seen_at            aware datetime
last_seen_at             aware datetime
removed_at               aware datetime | null
last_synced_at            aware datetime
```

Invariants:

- `(source, source_group_key)` is unique;
- inactive iff `removed_at` is non-null;
- a system-derived Moomoo group is read-only except the explicit `Favorites`
  group supported by the upstream API;
- timestamps never move backward.

### 4.2 `WatchlistMembership`

```text
membership_id            watch_membership_<uuid7>
group_id                 WatchlistGroup foreign key
source                   MOOMOO | MANUAL_CSV
provider_code            exact upstream code (for example US.NVDA or FX.XAUUSD)
instrument_id            normalized Trading Partner id | null
display_name             upstream/user display name
provider_asset_type      source-provided type | null
research_supported       bool
active                   bool
first_seen_at            aware datetime
last_seen_at             aware datetime
removed_at               aware datetime | null
last_synced_at            aware datetime
```

Invariants:

- `(group_id, provider_code)` is unique;
- inactive iff `removed_at` is non-null;
- `research_supported=true` requires a valid A-share/US `instrument_id`;
- unsupported Moomoo codes remain visible with their exact provider code and
  `research_supported=false`; they are never discarded or mislabeled;
- refresh marks missing upstream members inactive instead of deleting rows.

### 4.3 `WatchlistMutation`

```text
mutation_id              watch_mutation_<uuid7>
idempotency_key          unique caller key
action                   ADD | REMOVE
source                   MOOMOO | MANUAL_CSV
group_name               exact target group
provider_code            exact target code
requested_by             user | external_agent
status                   PENDING | SUCCEEDED | PARTIAL | FAILED
requested_at             aware datetime
completed_at             aware datetime | null
error_code               typed code | null
```

The mutation receipt prevents repeat user/tool calls from producing ambiguous
external writes. Provider payloads, tokens, account identifiers, and credentials
are never stored.

## 5. Persistence

Migration `0009_phase2_watchlist_hub` adds:

```text
watchlist_groups
watchlist_memberships
watchlist_mutations
```

It does not modify or replace Phase 1's `watchlist_items` table. A dedicated
Watchlist Hub repository/UoW owns the new tables. Business rows and redacted audit
events commit together when the database is the only mutable boundary.

Database behavior:

- ordinary reads return the latest durable state;
- refresh atomically upserts seen groups/members and marks missing rows inactive;
- a source outage with durable rows returns stale/degraded data plus a typed
  warning;
- a source outage without durable rows returns a typed provider failure;
- successful upstream write followed by local persistence failure is a partial
  failure; retry/refresh must safely heal the database state;
- no physical deletion of membership history in public application paths.

## 6. Moomoo adapter

The project-owned adapter uses the same official APIs validated by the
`moomoo-trader` plugin:

```text
OpenQuoteContext.get_user_security_group
OpenQuoteContext.get_user_security
OpenQuoteContext.modify_user_security
```

Rules:

- OpenD host must remain loopback-only;
- production CLI and MCP processes share the same sliding-window OpenD request
  coordinator under `data/locks/`; Watchlist endpoint buckets are independent,
  while account endpoint buckets are additionally scoped by hashed account ID;
- verified OpenD static-metadata errors are corrected only through the strict,
  versioned `config/moomoo_security_corrections.yaml` registry; corrected
  normalized identity/display name never replaces the raw provider asset type;
- SDK console logging is disabled before context construction;
- every context closes in `finally`;
- pandas/SDK objects and raw payloads never cross infrastructure;
- group names and provider codes are passed exactly, including Unicode;
- `CUSTOM` groups and `Favorites` may be written; other system-derived groups
  are read-only;
- ADD/REMOVE is verified by re-reading the target group before reporting success;
- the adapter respects the audited 10 requests per 30 seconds limit for each
  Watchlist endpoint and does not fan out unbounded group reads.

Normalization:

- `US.NVDA` → supported US instrument when asset type can be validated;
- `SH.600519` / `SZ.000001` → supported A-share instrument;
- unsupported markets/types such as `FX.XAUUSD` remain visible but cannot be
  promoted into a fabricated Trading Partner instrument.

## 7. Manual CSV adapter

`watchlist.v1.csv` has one exact header:

```text
schema_version,group_name,instrument_id,display_name
```

Rules:

- every row uses `schema_version=1`;
- key `(group_name, instrument_id)` is unique;
- group name, instrument id, and display name are non-blank;
- `instrument_id` must be a valid A-share/US Trading Partner instrument;
- formula prefixes and malformed/extra/missing columns fail closed;
- UTF-8 with optional BOM is accepted;
- MCP add/remove takes an exclusive file lock, writes a temporary file in the
  same directory, flushes/fsyncs, then atomically replaces the original;
- parsing or write failure leaves the previous file intact;
- successful file mutation is re-read before the database is updated.

CSV is a manual alternative to Moomoo, not an export/mirror of it.

## 8. Application flows

### 8.1 Read groups

```text
request
→ optional source refresh
→ persist group snapshot
→ read durable active/inactive groups
→ ToolEnvelope with source, freshness, warnings
```

### 8.2 Read members

```text
request(group or configured default)
→ optional target-group refresh
→ persist membership snapshot and inactive history
→ attach matching Research WatchlistItem/Case references by normalized identity
→ ToolEnvelope
```

### 8.3 Add/remove

```text
explicit confirmer + idempotency key
→ validate writable group and code/instrument
→ persist PENDING mutation
→ write active source
→ verify by source read-back
→ persist membership state + mutation outcome + redacted audit
→ ToolEnvelope
```

No add/remove call uses the Thesis Candidate confirmation state machine. The user
instruction authorizes only the named membership mutation, never a Thesis change
or trade.

## 9. Public MCP tools

The Watchlist Hub exposes exactly three tools.

### `watchlist_get`

Inputs:

```text
refresh: bool = false
include_inactive: bool = false
```

With `operation="groups"`, returns durable groups, source, writability, active
state, and sync timestamps. With `operation="items"`, accepts:

Inputs:

```text
group_name: string | null = configured default group
refresh: bool = false
include_inactive: bool = false
limit: 1..500 = 100
offset: >=0 = 0
```

Returns memberships plus normalized instrument/research support and optional
Research WatchlistItem/Case references.

### `watchlist_manage` (`add`)

Inputs:

```text
group_name: string | null = configured default group
instrument_id: string
display_name: string | null
confirmed_by: user | external_agent
idempotency_key: string
```

CSV accepts normalized A-share/US instruments. Moomoo converts supported
instrument ids to exact provider codes. Unsupported raw-code creation is deferred;
unsupported existing Moomoo members remain readable/removable by membership id.

### `watchlist_manage` (`remove`)

Inputs:

```text
membership_id: string
confirmed_by: user | external_agent
idempotency_key: string
```

Removal targets one persisted membership, preventing ambiguity between groups or
provider-code aliases. It never deletes research metadata.

## 10. Configuration

New safe settings:

```text
WATCHLIST_SOURCE=MOOMOO
WATCHLIST_DEFAULT_GROUP=Favorites
MANUAL_WATCHLIST_CSV_PATH=
```

All settings are typed in `AppSettings`, documented in `.env.example`, and added
with safe defaults to the local `.env` without reading or overwriting existing
values. No new secret is required.

## 11. Implementation slices

| Slice | Deliverable |
|---|---|
| 2A | Domain, DTO, ports, `0009`, ORM, repository/UoW |
| 2B | Strict Manual CSV read/write adapter |
| 2C | Moomoo Quote Context read/write adapter and normalization |
| 2D | Application service, stale fallback, idempotency, audit, research links |
| 2E | Bootstrap, settings, four MCP tools, docs/skill/inventory updates |
| 2F | Focused tests, migration gate, CSV smoke, real Moomoo read smoke and non-mutating verification |
| 2G | Full-sync operational CLI for external schedulers and conservative Moomoo read pacing |

Real Moomoo ADD/REMOVE smoke requires a user-named harmless target mutation. Phase
2 code completion does not authorize changing a real watchlist merely for testing;
read-only live smoke plus mocked mutation/read-back contract is sufficient until
the user explicitly requests a real add/remove.

## 12. Acceptance

Phase 2 is complete only when all of the following are proven:

- exact Watchlist Hub delivery surface was 58 tools before Phase 2B;
- Moomoo and Manual CSV satisfy the same source contract;
- database survives restart and preserves inactive membership history;
- refresh is atomic and stale fallback is explicit;
- add/remove require an allowed confirmer and are idempotent;
- upstream-success/local-failure produces a recoverable partial result;
- unsupported Moomoo members remain visible without fake instruments;
- external deletion cannot cascade into Research WatchlistItem or Investment Case;
- `.env.example`, local `.env` safe defaults, README, AGENTS, skill, capability
  guide, roadmap, and docs index agree;
- Ruff, mypy, focused tests, full pytest, Alembic heads, package build, and a fresh
  installed-wheel smoke pass;
- real OpenD group/member read succeeds without raw SDK logs or secret output.

Historical Watchlist Hub closeout evidence (before the three Phase 2B tools):

- exact runtime registration: 58 tools, including all four `watchlist_*` tools;
- Ruff passed across the repository; mypy passed across 320 source files;
- full test suite: 1,750 tests passed in 29.44 seconds after the 2026-07-19
  test-asset slimming passes;
  duplicate DTO/settings matrices, Provider Router and Vendor Chain cases,
  provider-cache/cache-codec validation matrices, Research Memory model/repository
  matrices, historical frozen-path assertions, and repeated MCP tool-list checks
  were consolidated without removing representative stdio, migration, FTS,
  append-only, routing, or secret-redaction behavior; the Moomoo mutation unit
  test keeps its post-write verification while using a test-only rate-limit window;
- Phase 2A Watchlist closeout used `0010_post_market_sync_runs`, after
  `0011_reddit_rss_resilience` and `0009_phase2_watchlist_hub`; clean-database
  migration and migration round-trip tests passed;
- sdist and wheel built successfully; a fresh Python 3.13 environment installed
  the wheel and registered all 58 tools (`wheel_smoke_ok:58`);
- real OpenD read returned 24 groups, found `Favorites`, and persisted its member
  without executing a mutation; the unsupported member remained visible with no
  fabricated Instrument;
- injected persistence failure after a successful upstream write produced a
  durable `PARTIAL` mutation with `PERSISTENCE_ERROR`;
- integration tests proved restart recovery, stable membership IDs, inactive
  history, research metadata linkage, and no deletion of the separate Research
  WatchlistItem/Investment Case rows.

## 13. Phase 2B — Portfolio Risk Engine v1

Phase 2B pulls the deterministic account/portfolio risk gate forward so Codex can
challenge a proposed addition before the later strategy, backtest, or monitoring
layers exist. It adds exactly three public MCP tools:

```text
portfolio_risk_get (policy)
risk_policy_update
portfolio_risk_get (check)
```

`portfolio_risk_get` (`policy`) returns the current append-only policy version.
`risk_policy_update` requires `user` or authorized `external_agent` confirmation,
an expected current version, and an idempotency key. Migration `0012` seeds a
visible system-default policy (20% single position, 120% gross exposure/NAV, 5%
minimum cash, 25% maximum margin usage, 3600-second account age, 900-second price
age). Until explicitly confirmed, checks carry
`RISK_POLICY_DEFAULT_UNCONFIRMED` and cannot silently report a clean pass.

`portfolio_risk_get` (`check`) uses specified durable account snapshot ids, the latest durable
snapshot per account, or an explicit read-only provider refresh. It can optionally
evaluate one hypothetical addition using caller-supplied quantity, assumed price,
and currency. It returns independent checks with `PASS`, `WARN`, `BREACH`, or
`NOT_EVALUATED`; overall status is `PASS`, `WARN`, `BREACH`, or `INCOMPLETE`.

V1 evaluates:

- account and valued-position price age;
- single-position concentration within each native currency;
- gross exposure to NAV only when every account/position shares one currency and
  NAV is available;
- minimum cash and maximum margin use per account;
- the same instrument held across multiple accounts as a warning.

Missing NAV, prices, timestamps, or FX facts produce typed data-quality warnings
and `NOT_EVALUATED`; they never become a pass and currencies are never converted
through an assumed rate. `execution_effect` is always false.

The following remain Phase 3 extensions: themes, drawdown, liquidity, event/earnings
risk, A-share T+1 and price limits, duplicate orders, historically calibrated
thresholds, Trade Plans, position sizing, and monitoring.

Phase 2B acceptance is intentionally focused: domain/policy persistence and
idempotency, representative pass/breach/incomplete evaluation, MCP inventory and
schemas, clean migration, Ruff, and mypy. It does not recreate the old exhaustive
provider/routing matrix.

Completion evidence on 2026-07-20:

- runtime inventory contains exactly 61 tools, including all three `risk_*` tools;
- clean SQLite migration and upgrade/downgrade/upgrade coverage reaches
  `0012_phase2b_risk_engine` and seeds the disclosed default policy;
- repository-wide Ruff and mypy passed across 333 source files;
- 54 focused MCP, bootstrap, migration, delivery-audit, and risk tests passed in
  3.89 seconds; the four new risk tests cover policy version/idempotency,
  breach, incomplete missing-price-time behavior, and MCP delegation;
- the project database was migrated without rebuilding account or Watchlist data;
  a live durable-snapshot smoke returned a typed `BREACH`, 38 rule results,
  `execution_effect=false`, source/data-quality warnings, and no tool error.

## 14. Phase 2C — Monitoring Hub

Monitoring converts a small set of user-confirmed conditions into durable,
repeatable checks. It is deliberately not a background LLM research loop. A run
fetches only facts required by active rules, persists the latest rule state, and
creates an event only when that state changes.

Public tools:

```text
monitor_manage (create)
monitor_read (definitions, dashboard, runs)
monitor_manage (update)
monitor_evaluate
monitor_read (events)
monitor_manage (resolve_event)
```

Definitions are append-only versions with optimistic concurrency, explicit
`user`/`external_agent` confirmation, and idempotency. A current version can be
`ACTIVE`, `PAUSED`, or `ARCHIVED`; cadence is `ON_DEMAND`, `INTERVAL`,
`A_SHARE_POST_MARKET`, or `US_POST_MARKET`. A price rule in a scheduled Monitor
must match that market, preventing an A-share rule from being evaluated after the
US close (or vice versa) against an inevitably stale quote. V1 rules are:

Each version may also have an aware `valid_until` timestamp. The timestamp is an
inclusive alarm lifetime, not a market-data freshness limit: once `as_of` is later
than it, the evaluator skips the Monitor before any provider request, state write,
or event creation and reports `MONITOR_EXPIRED`. Existing history is retained.
`max_fact_age_seconds` remains the separate per-rule guard against stale facts.

Public monitoring enum inputs are case-insensitive and whitespace-tolerant at the
DTO boundary (`active`, ` Active `, and `ACTIVE` normalize to `ACTIVE`). MCP schemas,
responses, domain objects, and persisted values always use the canonical uppercase
wire vocabulary.

- `PRICE_ABOVE` for one resolved A-share/US instrument;
- `PRICE_BELOW` for one resolved A-share/US instrument;
- `RISK_OVERALL_AT_LEAST` with `WARN` or `BREACH` threshold, using the latest
  durable accounts and current Risk Policy without refreshing brokers.

Every rule has a stable `rule_code`, severity, and maximum fact age. Its latest
state is `QUIET`, `TRIGGERED`, or `NOT_EVALUATED`. A first trigger produces a
`TRIGGERED` event; repeated unchanged triggers produce no event; a later quiet
state produces `RECOVERED`. Missing or stale facts produce `NOT_EVALUATED`, never
a quiet result. Events can be explicitly acknowledged or resolved, but neither
action changes a Thesis, Investment Case, Risk Policy, position, or order.

Phase 3A continuous futures reuse the same US price-rule path. Futures quote and
bars routing is asset-aware. Alpha Vantage is never sent a future. `GC=F`, `SI=F`,
and `HG=F` quotes may fall back from Yahoo to the timestamped Sina external-futures
feed; daily/weekly/monthly bars for all six seeded metals may fall back to
Eastmoney. Sina's unverified price-only minute line is not treated as OHLCV.
Fallback results preserve their source, unknown SLA/session status, observed delay,
non-spot warning, and contract-roll warning.

Regression receipts on 2026-07-23: the original live `GC=F` Yahoo evaluation
produced observations for all five configured rules with no run error codes.
After fallback implementation, direct live Sina checks returned timestamped GC,
SI, and HG quotes. Provider attempts and warnings remain visible, so fallback use
does not masquerade as Yahoo or as OTC spot.

External schedulers use:

```bash
uv run trading-partner-monitor-run --cadence US_POST_MARKET
uv run trading-partner-monitor-run --cadence A_SHARE_POST_MARKET

# Evaluate currently due INTERVAL and A-share/US/KR post-market groups
uv run trading-partner-monitor-run due

# Install/status/remove the token-free macOS hourly wake
uv run trading-partner-monitor-scheduler install
uv run trading-partner-monitor-scheduler status
uv run trading-partner-monitor-scheduler uninstall
```

The explicit market-cadence CLI force-runs active monitors once under a process
lock and is retained for diagnostics. The normal `due` dispatcher compares the
database schedule and latest run for INTERVAL plus A-share/US/KR post-market groups:
an early wake does not fetch facts, each market group runs at most once after that
exchange session's close plus the configured delay, and no Codex Automation needs
to evaluate Monitor rules. Partial/failed interval runs retry on the next hourly
wake; successful interval runs wait for their configured whole-hour interval,
anchored to the run-start hour so Provider latency cannot cause an extra-hour drift.
Due dispatch uses live evaluation time rather than treating the pre-fetch selection
time as an explicit historical cutoff. The
launchd job wakes at minute 5 of each hour and invokes only this local deterministic
path, so it consumes no Codex/LLM tokens. Monitor definitions, complete per-rule run observations, latest
states, transition events, and resolutions are durable; all runs have
`execution_effect=false`.

An optional Telegram Bot channel consumes durable transition events and scheduled
market-close run summaries. The source event/run and its notification Outbox row
are committed in the same database transaction. Repeated unchanged INTERVAL
observations produce no duplicate alert, while each evaluated A-share/US/KR post-market
group emits one consolidated zero-change-or-change heartbeat without fabricating a
Monitor event. Delivery is deterministic and local—no
Codex task or LLM is involved. Failed deliveries use bounded retry, Telegram `429`
respects `retry_after`, and events older than the configured TTL expire instead of
arriving as misleading late alerts.
Each message reuses the exact same run observations and never performs a second
quote request for presentation. Price, fact time, and Provider source are shown
once at the top. Every configured point remains visible as a compact state,
condition, and bounded human meaning; repeated observed values and distances stay
in the durable Run rather than being repeated on a phone screen. A shared
`NOT_EVALUATED` cause is shown once. Multiple transitions for one Monitor in one
run are batched into one Telegram message; the underlying events remain separate
and auditable.
Telegram does not support responsive tables, so the sender uses a price-first
headline and mobile-first vertical rule lines. Transition alerts include the prior
observed price, price change, and exact Provider source from the run receipt. They
show a prominent red/green Unicode alert band when a monitored level newly triggers
or recovers because Telegram HTML cannot set a text background color. Common source
provenance warnings are condensed into a human-readable basis line; true errors are
not hidden. IG Weekend Gold fallback messages explicitly say
that the value is an XAUUSD weekend-volatility proxy rather than spot/LBMA. It does
not generate or upload an image and does not invoke an LLM.

```bash
uv run trading-partner-monitor-notifications status
uv run trading-partner-monitor-notifications test
uv run trading-partner-monitor-notifications flush
```

The hourly due dispatcher flushes pending notifications even when no Monitor is
due. Market-cadence groups flush after evaluation. Delivery does not
acknowledge or resolve the source event/run and never changes a Thesis, Trade Plan,
position, or order.

`monitor_read(request={"operation":"dashboard"})` is the unified current control
plane. `monitor_read(request={"operation":"runs",...})` restores exact historical
observations including observed/threshold/distance values, fact age, and typed
warnings/errors. Events remain sparse notifications created only on state changes.
Runs created before migration `0023` remain readable with
`observation_history_complete=false`; missing historical observations are not
backfilled or inferred.

Deferred Monitoring extensions include announcement/filing deltas, earnings
windows, technical-cross rules, capital-flow rules, snooze/cooldown policy, and
market-specific due calendars.

Dukascopy precious-metal INTERVAL schedules are the first venue-specific closed
window exception. XAUUSD/XAGUSD skip the New-York-aligned weekend closure and daily
17:00–18:00 ET maintenance break before Provider access. The dashboard reports
`MARKET_CLOSED` with the next observation window; it does not persist a false
`NOT_EVALUATED` transition merely because the venue is closed. An optional,
current-only IG Weekend Gold browser fallback can evaluate XAUUSD price rules inside
IG's own weekend window, but the observation remains explicitly labelled as a
separate CFD proxy and never supplies spot bars, technicals, or historical facts.

Current acceptance evidence (2026-07-29):

- the public surface remains exactly 28 tools; `monitor_read` has four closed
  operations and the current surface schema is `compact-v12`;
- migrations `0023_monitoring_hub_v3`, `0024_monitor_notification_outbox`, and
  `0028_provider_route_history` pass
  clean upgrade/downgrade/upgrade checks;
- focused tests cover transition deduplication, immutable observations, whole-hour
  schedule validation, due/skip behavior, compact schema, and launchd arguments;
- a live GC=F 4-hour interval smoke persisted five observations, and an immediate
  second dispatcher call returned `NO_DUE_MONITORS` without another quote request;
- repository-wide Ruff and mypy pass.

## 15. Phase 2D — Technical Engine v2

Phase 2D combines professional indicator calculation and chart rendering into one
bounded capability. The existing `technical_get_snapshot` remains the structured
fact entry point and is upgraded from US-only v1 to a shared A-share/US engine.
One public tool is added:

```text
technical_render_chart
```

Both tools accept resolved equity, ETF, or index instrument ids. The fetch layer
uses split-and-dividend-adjusted US daily bars and forward-adjusted A-share daily
bars; the calculation layer is market-neutral. It can derive `1d` and ISO-week
`1w` timeframes from one daily fetch, avoiding duplicate provider requests.

The versioned `tp_technical_v2` result contains:

- EMA 10/20, SMA 50/200, RSI 14, MACD, ATR 14, Bollinger Bands;
- ADX/+DI/-DI, Stochastic, ROC 20, MFI, OBV, and 20-period relative volume;
- explicit trend, momentum, volatility, and volume states;
- project-owned five-bar swing clustering within 0.75 ATR for nearby support and
  resistance, with touch count and method basis;
- recent TA-Lib engulfing, hammer, shooting-star, and doji recognition;
- exact adjustment basis, provider source, fetch time, freshness, warnings,
  algorithm/backend versions, and `historically_validated=false`.

For existing Phase 1F consumers, the top-level daily `bar_as_of`, `indicators`,
`support`, and `resistance` fields remain as a compatibility view; new consumers
should use the complete `timeframes` collection.

`technical_render_chart` returns a compact JSON Tool Envelope, a local artifact
reference with ready-to-embed Markdown, and an in-memory PNG. The chart contains
candlesticks, EMA20, SMA50, derived structure levels, volume, and RSI14. The local
file is permission-restricted under gitignored `data/artifacts/technical/`; no
chart blob or base64 payload is persisted to the database.

TA-Lib and Matplotlib are local open-source runtime libraries, not market-data
providers and not paid services. Provider data still follows the existing Router
and Tool Envelope policies. Minute bars, benchmark-relative strength, automated
signals, strategy scoring, parameter optimization, backtests, and orders remain
outside Phase 2D.

Completion evidence on 2026-07-20:

- default runtime inventory exposes exactly 28 tools;
- Ruff and mypy pass across the repository source tree;
- compact acceptance covers standard indicators, disclosed structure output,
  PNG rendering, MCP registration, bootstrap wiring, and public inventory;
- no setting, secret, migration, database table, scheduler, or order surface was
  added.

## 16. Outside the Phase 2 boundary

Some successor capabilities below are now implemented in Phase 3A/3D; they remain
outside the Phase 2 contract documented here.

```text
push alerts
historical data lake
backtests and experiments
crypto, Forex/metals, and futures research coverage
Trade Plans and position sizing
order writes
```
