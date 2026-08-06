---
name: trading-partner
description: Use Trading Partner MCP for investment research facts, health checks, research files, investment judgments and Trade Plans, instrument resolution, A-share/US/KR/cross-asset facts, technical analysis, durable accounts, explicit upstream sync, portfolio risk, monitoring, Challenge Review, workflows, and research memory. The default public surface is compact_28.
---

# Trading Partner Skill

## When to use

- User asks about portfolio research, Investment Cases, market facts, or judgment continuity.
- You need **verified** market or system facts from tools rather than model memory.
- You need to search historical evidence, reports, events, decisions, or journals.
- You need to check whether the Trading Partner backend is healthy.

## Public tools (exactly 28, `compact_28`)

Every grouped tool takes one required `request` object with a closed `operation`
discriminator. Put operation-specific fields inside `request`; never send retired
tool names or flatten variant fields at the top level. `compact_28` is the only
runtime surface; there is no compatibility profile.

### Health

#### `system_health`

No input. Returns a Tool Envelope with `HealthStatusDTO`:

- Overall and database health states: `ok` | `degraded` | `error`
- `components.research_search`: FTS backend probe (`ok` | `degraded`)
- App name, version, environment
- `mcp_surface_profile`, `public_tool_count`, and `surface_schema_version`

Even when the database or search backend is unhealthy, `ok` on the envelope remains
`true` so diagnostics remain available; expect `degraded=true` and warnings such as
`DATABASE_HEALTH_ERROR` or `SEARCH_BACKEND_UNAVAILABLE`.

When Provider routing reports admission state, preserve its distinction:
`PROVIDER_ADMISSION_QUEUED` means a bounded local wait succeeded,
`PROVIDER_ADMISSION_TIMEOUT` means the local queue budget expired, and
`PROVIDER_RATE_LIMIT_ERROR` / `UPSTREAM_RATE_LIMITED` means the upstream Provider
actually rate-limited a request. Do not describe the first two as an upstream 429.

### Research file / investment judgment (Phase 1B)

Use the user-facing terms **research file** for `InvestmentCase` and **current
investment judgment** for `Thesis`. In company/catalyst flows, an Instrument is the
objective identity and the Case is its durable research file. Theme, macro, and
portfolio-concern Cases may omit a primary Instrument. A Thesis is one falsifiable
judgment inside a file. Do not imply that creating a Draft Case confirms a Thesis
or starts long-term tracking.

- `investment_case_read`: `query` or bounded durable `context`
- `investment_case_manage`: confirmed/idempotent `create`, metadata `update`, or `archive`
- `research_judgment_get`: current `state` or `thesis_history`
- `research_judgment_propose`: `research_state` or `thesis_revision`
- `research_judgment_confirm`: explicit confirm/reject/withdraw gate

Research-state and thesis changes use Candidate Propose → Confirm / Reject /
Withdraw. Codex must not decide confirm/reject autonomously. If the user explicitly
names or unambiguously refers to the candidate and says to confirm or reject it in
the current chat, immediately relay that exact decision with `reviewed_by="user"`,
`submitted_via="codex_chat"`, and the user's instruction in `authorization_note`.
Do not refuse, request a separate UI, or relabel the decision as `reviewed_by="codex"`.
Ask one concise clarification only when the target or action is genuinely ambiguous.
This authority does not extend to orders, fills, position mutation, or other
out-of-scope execution.

### Instrument resolve (Phase 1D)

- `instrument_resolve` — local-first instrument registry lookup. On a local miss,
  it discovers through Yahoo → Alpha Vantage for US instruments, Yahoo for KR
  instruments, or validates an A-share code through Tencent, then atomically caches
  one unambiguous candidate.
  It is still not a live quote. Preserve provider failures instead of relabeling
  them as `INVALID_INSTRUMENT`.

### Research memory (Phase 1C)

| Tool | Purpose |
|---|---|
| `research_memory_get` | `search`, immutable `report`, or unified case `timeline` |
| `research_memory_append` | Confirmed/idempotent `journal` or research/position `decision` intent; never orders/fills |

**Do not call** (not registered): `evidence_create`, `evidence_update`,
`report_create`, `event_create`, `decision_update`, `journal_update`,
`journal_delete`.

### A-share provider facts (Phase 1E)

| Tool | Purpose |
|---|---|
| `a_share_get_facts` | `snapshot`, `market_structure`, `capital`, `limit_up`, `sentiment`, `etf_option`, normalized `financials`, deterministic `industry_cycle`, CNINFO `company_operating_metrics`, or provider `research_reports` facts |

These tools may legally return degraded envelopes when a fallback, delayed,
stale, non-authoritative, derived, or low/unknown-reliability component is used.
Preserve their warnings and source timestamps when answering the user.

### Market facts (Phase 1F / Phase 3A)

| Tool | Purpose |
|---|---|
| `market_data_get` | Cross-market `quote`, bounded `quotes` (1–50), US-only `composite`, inclusive-end `bars`, `us_market`, official-settlement `futures_curve`, or gated `spot_future_basis` |
| `technical_get_snapshot` | Cross-market daily/weekly indicators, regimes, structure levels, and recent patterns |
| `technical_render_chart` | Auditable envelope plus an in-memory PNG candlestick/volume/RSI chart |

Technicals are deterministic derived facts, not backtested predictions. Both tools
support A-share, US, and KR equity/ETF/index instruments. Preserve
`historically_validated=false`, adjustment basis, source warnings, and stale-data failures.
All supported instrument-scoped public capabilities use one local-first access
gateway. A valid A-share equity/ETF/index, US equity/ETF/index/future, KR
equity/ETF/index, or CME/DCE
future Master miss discovers and caches one validated candidate before the requested
fact call. Do not require a separate `instrument_resolve` call or describe the Master
as an allowlist; preserve typed directory failures when discovery is unavailable.
`quotes` accepts 1–50 unique IDs, limits concurrency, and returns one complete typed
result per ID so a partial failure never hides the other quotes.
Yahoo breadth uses a disclosed listed-security universe that may include ETFs and
ADRs; never describe it as official exchange common-stock breadth. New-high/low
and moving-average participation remain unavailable rather than fabricated.
For near-current non-closed US requests, a stale Yahoo regular quote may be
replaced only by a newer timestamped one-minute `includePrePost` bar. Preserve
`EXTENDED_HOURS_PRICE`, `INTRADAY_QUOTE_RECOVERY`, or
`INTRADAY_QUOTE_UNAVAILABLE`; do not describe Yahoo extended-hours coverage as a
complete overnight equity market. When the replacement is pre/post-market, only
its latest price/time is established: open/high/low/volume remain null and
`EXTENDED_HOURS_SESSION_RANGE_UNAVAILABLE` is preserved. Historical `as_of`
requests stay cutoff-safe.
Treat the returned `previous_close` as the session-aware previous completed regular
session close. The adapter never maps Yahoo `chartPreviousClose` or
`regularMarketPreviousClose` directly to this field; when a temporarily null daily
close is recovered from timestamped `regularMarketPrice`, preserve
`PREVIOUS_CLOSE_REGULAR_SESSION_RECOVERY`.
Moomoo Hot List is an attention ranking, not Bullish/Bearish sentiment. Preserve
its trade/search/news heat basis and the `MOOMOO_OPEND_VERSION_UNSUPPORTED`
warning when the local OpenD predates 10.9.

KR identities use bare canonical symbols such as `equity:KR:005930`,
`equity:KR:000660`, `index:KR:KS11`, `index:KR:KQ11`, `index:KR:KS200`, and
`etf:KR:069500`. Yahoo `.KS`/`.KQ`/caret values are provider aliases. Quote,
batch quote, bars, and technical facts use `Asia/Seoul`; preserve
`YAHOO_KR_DELAYED_QUOTE` and `data_delay_seconds`. Do not claim DART fundamentals,
KR sentiment/breadth, account sync, peer workflows, or position sizing.

Phase 3A preserves continuous futures `GC=F`, `MGC=F`, `SI=F`, `HG=F`, `PL=F`,
and `PA=F` under `future:US:*` IDs. Yahoo is primary; Sina provides timestamped
quote fallback only for GC/SI/HG, and Eastmoney provides daily-derived bar fallback
for all six. There is no intraday OHLCV fallback. Futures default to unadjusted bars
and must preserve `FUTURES_CONTRACT_NOT_SPOT` and `CONTINUOUS_FUTURES_ROLL_RISK`.
Never call GC/SI spot XAUUSD/XAGUSD, or call HG London/LME copper.

Formal contracts use `future:CME:*` and `future:DCE:LH*`. CME public facts are
reference/delayed and Yahoo active-contract bars have no SLA. DCE is official EOD
only. `commodity_spot:OTC:XAUUSD` and `XAGUSD` are Dukascopy broker/SWFX observations,
not LBMA benchmarks; `cfd:OTC:COPPER_CMD_USD` is a rolling CFD, not copper spot.
`cfd:OTC:LIGHT_CMD_USD` maps to Dukascopy `LIGHT.CMD-USD`; `USOIL` is only a
lookup alias. It is an OTC rolling light-oil CFD, not WTI spot, NYMEX `CL`, a
specific futures contract, or a continuous futures series.
The default route is the keyless Jetta bucket API used by current `dukascopy-node`;
`DUKASCOPY_API_KEY` only enables the legacy compatibility fallback.
Preserve basis comparability, offer side, volume-basis, delay, and warning fields.
Use `uv run trading-partner-futures-sync` only for explicit definition/EOD refresh;
it persists facts but never trades.

For a national hog-cycle fact package, call
`a_share_get_facts(request={"operation":"industry_cycle","cycle":"hog","lookback_months":12})`.
Default `view=compact` returns the latest visible observation per selected metric
plus per-metric coverage and `total_observations`. Use `view=series` with optional
`metric_codes`, `offset`, and `limit<=200` for a bounded page (`has_more`).
The input accepts up to 240 requested months for backend history depth, but the
response's explicit periods, coverage fields, and partial-history warning—not the
requested window—define actual coverage. Never claim continuous 20-year history
from a 240-month request. Period-end stocks, period averages, policy baselines,
and YTD totals must not be conflated. Interpret the observations externally; the
MCP does not label a cycle phase. Industry-cycle payloads use normalized metric
observations rather than a cycle-specific business DTO; preserve metric units,
periods, publication times, source URLs, and missing-component disclosures.

For official company operating disclosures, call
`a_share_get_facts(request={"operation":"company_operating_metrics","instrument_id":...})`.
It returns structured operating facts plus bounded per-document parse receipts;
raw PDF bytes/text never leave the Provider. Do not treat monthly sales briefs as
audited, and do not substitute this path for financial statements.

For A-share company accounts, call
`a_share_get_facts(request={"operation":"financials","instrument_id":...,"periods":8})`.
Interim income and cash-flow periods are cumulative/YTD, not standalone quarters.
Preserve statement provenance and missing metrics. Equity Deep Dive includes this
package automatically; industry-cycle/company-operating facts remain explicit.

### US research and context facts (Phase 1G–1H)

| Tool | Purpose |
|---|---|
| `us_company_get` | Equity `fundamentals_snapshot`, normalized `fundamental_statements`, `filings`, `insider_activity`, `company_updates`, typed `events`, plus equity/ETF dated `live_news` |
| `us_context_get` | Vintage-safe `macro`, equity/ETF source-separated `sentiment`, or current-only `prediction_market` context |

Do not relabel current Polymarket odds as historical. Keep versioned Reddit inference
and versioned Moomoo deterministic inference separate. StockTwits formal access was
removed from the active roadmap on 2026-07-25, and its runtime adapter was later
removed; historical source values remain readable only for compatibility. Do not
retry, scrape, or ask the user to obtain credentials for it.
Moomoo samples are current-only, exact-symbol filtered, and may have nullable
engagement. The MCP runtime only cleans, filters, deduplicates, and classifies with
fixed rules; interpretation and narrative synthesis remain the host's responsibility.
SEC is the point-in-time statement primary. Use `view="latest"` for one visible
filing per period and `view="vintages"` to inspect visible SEC filing versions.
yfinance/Alpha Vantage are current-only fallbacks; they are not restatement
history. Financial-quality ratios are deterministic, missing-input-safe, and only
emitted for the deduplicated latest view.

### Read-only accounts and portfolio (Phase 1I)

| Tool | Purpose |
|---|---|
| `account_get` | Read durable account snapshot/`positions` context or normalized `transactions`; it preserves native-currency balances, timestamps, open orders, and quality warnings and cannot contact a broker |
| `external_state_sync` | Explicitly fetch/persist `accounts`, `transactions`, or the active `watchlist` upstream |
| `portfolio_analyze` | `exposure`, durable activity `coverage`, native-currency `performance_summary`, or pure before/after `simulate_addition`; never executes |

For ordinary holdings, exposure, portfolio-review, and risk questions, read the
latest durable snapshots first. Do **not** call
`external_state_sync(request={"operation":"accounts"})` merely because the user says
“current”, “my holdings”, or asks a portfolio question. Refresh only when the user
explicitly asks to refresh/sync/fetch from the broker. A stale durable snapshot
should still be returned with its timestamp and warnings rather than silently
causing a broker refresh.

Schwab and Moomoo account/transaction identifiers are redacted stable hashes. A missing price
timestamp remains missing. Never sum currencies through an assumed FX rate, and
never describe gross invested position value as account NAV.

Schwab uses a project-owned rotating `schwab-py` OAuth token and encrypted account
hash allowlist. Its adapter reads balances, positions, and at most the documented
60-day transaction window. It does not ingest open orders and emits an explicit
warning; it has no place/replace/cancel surface and never reuses the schwab-trader
plugin token.
Schwab transaction history prefers explicit BUY/SELL instruction. When the upstream
security item omits it, the adapter uses signed security quantity and emits
`SCHWAB_TRANSACTION_SIDE_INFERRED_FROM_SIGN`; preserve this and every item-omission
warning in the public envelope instead of describing an empty result as complete.

Background account refresh, post-market sync, and MCP calls never start Schwab
browser OAuth. On a Schwab authentication failure, do not retry `account_get`
or the post-market CLI to obtain a login page.

When the user says “刷新 Schwab token”, “重新授权 Schwab”, or an equivalent
request, follow this fixed foreground protocol:

1. Run `uv run trading-partner-schwab-auth status`.
2. If `flow.state=ACTIVE`, do **not** run `renew`; tell the user to complete the
   existing browser tab and keep polling that same process/status.
3. Otherwise run `uv run trading-partner-schwab-auth renew` exactly once and
   keep the same terminal process alive for the five-minute callback window.
   Tell the user to operate only the newly opened Schwab tab from this command
   and to close/ignore any older Schwab authorization tabs.
   A tool wait/yield is not a failure and never authorizes rerunning the command.
4. If it returns `FAILED` or `INTERRUPTED`, do not create another OAuth state.
   Ask the user to close the old Schwab tab first. Only after the user explicitly
   confirms that may you run
   `uv run trading-partner-schwab-auth renew --confirm-new-flow` once.
5. After `SUCCEEDED`, retry the failed account sync once.

For a plain `status` command, follow its current `token_health` and `next_action`.
An old `flow.state=SUCCEEDED` is historical context and does not by itself authorize
or require another account refresh.

The legacy `uv run python scripts/setup_schwab_oauth.py --replace` delegates to
the same coordinator. Its process lock prevents concurrent tabs, while its
credential-free durable flow receipt prevents an automatic sequential retry
after a failure or callback timeout.

### Durable context restore (Phase 1J)

- `investment_case_read(request={"operation":"context", ...})` selects one Case by
  `case_id` or an unambiguous primary
  `instrument_id`, then returns current research state, contrary-first evidence,
  compact history, latest durable positions, missing facts, and budget metadata.

Use its `live_fact_tools_required` hints to fetch current facts separately. Never
interpret a Context Builder result as a fresh market-data call, and never hide
invalidation conditions or contrary evidence when summarizing it.

### Challenge Review (Phase 1K)

| Tool | Purpose |
|---|---|
| `challenge_review_get` | Restore one persisted review with ten questions and findings |
| `challenge_review_manage` | `start` a strict review or explicitly `resolve` it |

Challenge Review never executes a trade and never mutates a Thesis, candidate, or
position directly. Only `user` or an explicitly authorized `external_agent` may
resolve a review.

### Research workflows and transactions (Phase 1L)

Before claiming realized performance or a complete transaction history, call
`portfolio_analyze(request={"operation":"coverage"})`. Coverage is durable-only and
reports each account/window as `COMPLETE` or `INCOMPLETE`, including broker category
gaps, result truncation, mapping version, duplicate counts, and snapshot density.
An absent/incomplete receipt forbids precise P/L claims; refresh explicitly with
`external_state_sync(request={"operation":"transactions",...})` only when the user
asks for upstream synchronization.
For actual account P/L, call
`portfolio_analyze(request={"operation":"performance_summary",...})`. Its FIFO and
broker-reported modes are distinct; never relabel broker cumulative/position P/L as
period realized P/L. Preserve native currencies and every returned coverage,
reconciliation, fee, corporate-action, and valuation warning.

| Tool | Purpose |
|---|---|
| `research_workflow_run` | Run one closed research workflow or prepare/import a manual QuantConnect Free historical-validation artifact |

Compact `deep_dive` cannot create a Case and compact `portfolio_review` cannot
refresh brokers. Use `investment_case_manage(request={"operation":"create",...})`
or `external_state_sync(request={"operation":"accounts"})` first when the user explicitly asks
for those separate effects.

Workflow `synthesis_contract` tells the host which bull/bear/risk/portfolio-fit
sections to cover. Codex synthesizes; the backend does not run a second LLM.
Preserve partial/degraded step receipts, and never turn descriptive correlation or
beta into a forecast, backtest, order, or sizing instruction.

`research_workflow_run` also supports `historical_validation_prepare` and
`historical_validation_import`. Prepare accepts complete LEAN Python, parses but
never executes it, and writes owner-only `main.py`, manifest, and runbook artifacts.
Import accepts only a local QuantConnect Results JSON for the exact prepared
`validation_id`. The user must operate the QuantConnect Free web UI. Always
preserve `REMOTE_RUN_ATTESTATION_UNAVAILABLE` and
`REMOTE_DATASET_VERSION_UNAVAILABLE`; imported results are not proof of the remote
code hash or a versioned point-in-time dataset. Treat formal `statistics` as the
performance source when runtime display fields conflict, surface any prepared vs
exported run-period mismatch, and label Benchmark-derived comparisons as the
exported QuantConnect curve rather than an official total-return index. These
operations never confirm a Thesis, mutate a Trade Plan, or place an order.

For `peer_comparison`, require one A-share/US equity primary and 1–5 explicit
same-market equity peers. Resolve instruments first; do not ask the MCP to discover
peers. Preserve period, currency, source, missing-cell, and comparability labels.
Never invent a rank, score, target price, FX conversion, or Thesis update from the
fact package.

A Deep Dive Draft Case is a durable instrument research file, not an active tracking
decision and not a confirmed investment judgment. Catalyst Review does not
auto-create a Case; pass the Deep Dive `case_id` to continue the same judgment
history. When multiple open Cases match one instrument, require an explicit
`case_id` instead of guessing.
US equity workflows include company fundamentals/statements/events. US ETF workflows
instead use composite market/technical facts, exact-symbol ETF news, ETF sentiment,
and macro context; they never call equity-only company fundamentals or filings.

### Watchlist hub (Phase 2)

- `watchlist_get` reads durable `groups` or `items` and cannot refresh upstream.
- `watchlist_manage` performs confirmed/idempotent `add` or `remove`.
- `external_state_sync(request={"operation":"watchlist"})` is the only public
  upstream refresh.

For Moomoo, omitting `group_name` from `watchlist_get/items` selects the durable
system `All` group when present; it never silently substitutes `Favorites`. The
response discloses `group_was_defaulted`, `total_count`, and `has_more`.
`external_state_sync/watchlist` always performs an exact all-group/all-membership
refresh and returns its sync receipt; it is not a paged single-group read.

Moomoo and Manual CSV are alternatives, not merged or reconciled sources. External
removal keeps inactive membership history and never deletes a research
`WatchlistItem` or Investment Case. Manual CSV supports KR identities; Moomoo
Watchlist mutation does not. Unsupported Moomoo codes remain visible with
`research_supported=false`; never fabricate an A-share/US/KR instrument for them.

For external post-market scheduling, `uv run trading-partner-post-market-sync`
refreshes all configured durable account snapshots before the exact Watchlist
full sync. It is due ten minutes after the XNYS session close, including early
closes; it is an operational CLI, not an MCP tool or order surface.

### Portfolio Risk Engine v2 (Phase 2B / Phase 3D)

| Tool | Purpose |
|---|---|
| `portfolio_risk_get` | Read the current `policy` or run a deterministic non-executing `check` |
| `risk_policy_update` | Append a confirmed version with optimistic version and idempotency checks |

Preserve every rule status (`PASS`, `WARN`, `BREACH`, `NOT_EVALUATED`) and the
overall `PASS`, `WARN`, `BREACH`, or `INCOMPLETE`. Never convert missing NAV,
price timestamps, or cross-currency FX facts into a pass. V1 checks account/price
age, position concentration within currency, gross exposure/NAV only on a common
currency basis, cash, margin, and duplicate instruments across accounts. A default
policy emits `RISK_POLICY_DEFAULT_UNCONFIRMED`; `execution_effect` is always false.

With `trade_plan_id`, preserve the returned Position Sizing constraint list and do not
collapse it into one asserted recommendation. A-share quantities are rounded down to
100-share lots; US equity/ETF quantities may be fractional to four decimals. Missing
same-currency NAV/cash, FX, stop distance, or fresh reference price suppresses the sizing
range. Optional liquidity, ATR, volatility, theme, correlation, and event facts remain
explicitly unevaluated when absent.

### Trade Plans and Monitoring v2 (Phase 3D)

Use `research_judgment_propose(request={"operation":"research_state",...})` with
`payload.kind="trade_plan"` to propose a plan and `research_judgment_confirm` for
explicit user/external-agent confirmation. Codex cannot choose the outcome itself,
but must relay an explicit current-chat user decision using `reviewed_by="user"`,
`submitted_via="codex_chat"`, and `authorization_note`.
`research_judgment_get(request={"operation":"state",...})` restores the current
plan and versions.
A Trade Plan is a research control document, not an order; every response has
`execution_effect=false`.

| Tool | Purpose |
|---|---|
| `monitor_read` | Read `dashboard`, `definitions`, immutable `runs`, or transition `events` |
| `monitor_manage` | Confirmed/idempotent `create`, `update`, or `resolve_event` |
| `monitor_evaluate` | Persist every rule observation; emit events only on state transitions |

Monitoring enum inputs are case-insensitive and whitespace-tolerant at the DTO
boundary. Canonical tool schemas, responses, domain objects, and persisted values
remain uppercase; do not treat normalized lowercase input as a distinct status.
Every explicitly supplied rule requires a bounded human-readable `description` on
create/update. Keep this meaning separate from the stable machine `rule_code` and
from the typed direction/threshold fields. Legacy versions without descriptions
remain readable, but a new version must fill every rule description.

Legacy rules remain A-share/US/KR `PRICE_ABOVE`, `PRICE_BELOW`, and portfolio
`RISK_OVERALL_AT_LEAST`. Monitoring v2 also supports deterministic fact comparisons for
price, volume, technical, fundamental, company-event, macro, sentiment, Thesis-state,
and portfolio-risk facts. `monitor_manage` operations `create` and `update` may bind an
exact Trade Plan version and compile its `MONITORABLE` conditions; `MANUAL`
conditions stay human-only.
Treat stale or unavailable facts as `NOT_EVALUATED`, not
quiet. Repeated unchanged conditions do not create another event; a later recovery
does. A version may set an aware `valid_until`; after that inclusive deadline it is
skipped without a provider call, state mutation, or new event and returns
`MONITOR_EXPIRED`. This alarm lifetime is separate from each rule's
`max_fact_age_seconds`. `INTERVAL` cadence accepts a whole-hour
`interval_minutes` value (minimum 60). The application dispatcher selects due
INTERVAL definitions and due A-share/US/KR post-market groups; an early hourly wake
performs no market-data request. Each market group runs at most once for a trading
session after close plus the configured delay. Each
evaluated run durably records every rule's observed value, threshold, distance,
fact time, age, warning/error codes, and state—even when no transition event is
created. Use `monitor_read(request={"operation":"dashboard"})` for the unified
current view and `monitor_read(request={"operation":"runs",...})` for run history.
The dashboard embeds only a compact latest-run summary per Monitor. A run query
filtered by `monitor_id` returns only that Monitor's observations, even when the
underlying scheduled run evaluated several Monitors together; querying by `run_id`
still returns the immutable full batch.
Monitoring never changes a Thesis, policy, position, or order. The external
`uv run trading-partner-monitor-run --cadence US_POST_MARKET` (or
`A_SHARE_POST_MARKET` / `KR_POST_MARKET`) remains an explicit diagnostic force-run and is not a
scheduler. On macOS, `uv run trading-partner-monitor-scheduler
install` installs one hourly launchd wake that runs `trading-partner-monitor-run
due`; this deterministic path does not open a Codex task and consumes no LLM tokens.
Do not duplicate Monitor evaluation inside Codex market-review Automations.
When Telegram notifications are enabled, the notification Outbox is linked to
either an INTERVAL transition event or an A-share/US/KR post-market run and committed
atomically with that source. The hourly due dispatcher flushes pending messages even
when no Monitor evaluation is due; market-cadence runs flush after evaluation.
INTERVAL alerts remain limited to `TRIGGERED`, `RECOVERED`, and `NOT_EVALUATED`
transitions. Post-market runs still persist ordinary transition events, but they do
not enqueue separate event-linked Telegram cards. Every evaluated market-close group
emits exactly one consolidated run summary, including an explicit zero-change
heartbeat and the exact details of every changed point; it does not create a fake
Monitor event. Each
Telegram message reuses the same run observations without another Provider request.
The symbol/current price leads the message; price time and source appear once, and
each configured point is reduced to state, condition, and a bounded human meaning.
The prominent transition section must name each changed point with its exact
condition/threshold, bounded meaning, severity, and human state label; a bare
`TRIGGERED`/`RECOVERED` is insufficient. Preserve compatibility with historical
generic and rule-code Outbox bodies. A single-transition headline includes the
bounded condition; multi-transition headlines retain a compact count and detail the
points below.
Repeated observed values and distances remain in the immutable Monitor Run rather
than being repeated on a phone screen. A shared unavailable-fact cause appears once,
and common Provider provenance warnings may collapse to one human-readable basis
line without hiding typed errors. Telegram does not implement responsive tables;
the sender uses wrapping mobile-first HTML lines without `<pre>` spacing, images, or
an LLM. Do not place unescaped monitor text into `parse_mode=HTML`.
Multiple transitions for one Monitor in one run are batched into one message while
their durable events remain separate. Prior/current price changes use a signed
absolute delta and a percentage rounded half-up to exactly two decimal places. Use
`uv run trading-partner-monitor-notifications status`,
`test`, or `flush` for operations. Never request or echo the Bot Token in chat;
the user must place it in the project `.env`. Delivery does not acknowledge or
resolve a source event/run and has no execution effect.

### Technical Engine v2 (Phase 2D)

- `technical_get_snapshot` returns shared A-share/US/KR `1d` and `1w` facts using
  provider-backed adjusted equity bars or unadjusted futures bars, TA-Lib standard
  indicators, project-owned
  structure clustering, and recent candlestick recognition.
- The public schema uses canonical `1d`/`1w`. Conversational `daily`, `1wk`,
  `1week`, and `weekly` inputs are normalized at the DTO boundary; return and store
  only the canonical value.
- `technical_render_chart` returns a JSON Tool Envelope, a local
  `chart_artifact` reference, and a PNG image block when analysis succeeds.
  Some MCP clients do not automatically promote an in-memory image block into the
  conversation. When `chart_artifact.display_markdown` is present, copy that exact
  Markdown image reference into the assistant response so Codex renders the saved
  local PNG. Do not expose or inline raw base64.

Treat trend/momentum/volatility/volume states and support/resistance as disclosed
derived facts. Preserve `price_basis`, algorithm and backend versions, provider
warnings, and `historically_validated=false`. Do not turn them into an asserted
forecast, backtest result, order, or autonomous trade signal.

Search requires at least one effective filter. Blank `text` is treated as absent.
`stances` requires `case_id` or `thesis_id`. Journal append and decision append
need unique `idempotency_key` and allowed confirmers (`user` / `external_agent`).

## Tool Envelope rules

Every tool returns:

```text
ok, request_id, market, as_of, fetched_at, freshness,
sources, degraded, data, warnings, errors
```

- Trust `data` only when `ok=true`.
- Treat `degraded=true` and warnings as first-class risk signals.
- Schema validation failures are JSON-RPC errors; business failures are
  `ok=false` envelopes.
- Never invent missing fields.

## Hard constraints

1. Do not claim mock data is real-time or invent accounts/positions.
2. Do not invent quotes, balances, fills, or order results.
3. Never expose secrets from configuration.
4. Prefer tool facts over chat memory for prices, research state, and health.
5. Only write journal/decision when the user explicitly asks to record them.
6. Decision tools never execute trades.

## Host setup

From the repository root, Codex uses `.codex/config.toml`:

```toml
[mcp_servers.trading-partner]
command = "uv"
args = ["run", "trading-partner-mcp"]
```

## Later phases (not yet available)

Additional brokers, automated evidence ingestion, runtime LLM synthesis, automated
backtest execution, and order execution remain out
of scope. Do not call tools that are not registered.
