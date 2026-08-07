# Trading Partner — Agent Guide

## Product intent

Trading Partner is a long-horizon investment judgment companion. Codex (or another
agent host) talks to the user; Trading Partner MCP supplies facts, research state,
and structured tools. The implemented Phase 1–3D boundary covers A-share/US research,
Korea Exchange quote/technical monitoring,
accounts, Research Subjects, Watchlist Hub, Risk v2, Monitoring v2, versioned Trade
Plans, deterministic Position Sizing, and professional daily/weekly technical
analysis, plus a manual QuantConnect Free code/result bridge — **not** an automated
backtest runner or live order writes.

Canonical product language is **Research Subject** in English and 标的、研究标的 or
研究档案 in Chinese, depending on whether the context emphasizes the object or its
durable file. `InvestmentCase`, `investment_case_*`, `case_id`, `case_type`,
`linked_case_ids`, and the opaque `case_` ID prefix are compatibility-boundary names,
not user-facing terminology. Equity means an actual stock Instrument only.

## Implemented boundary

The sole public MCP surface is exactly **28** tools (`compact_28`). Grouped tools
accept one required `request` object
whose closed `operation` union rejects fields from other operations. Application
services remain separate; compact routing belongs only to `interfaces/mcp/`.

**System and identity**

- `system_health` — health plus `mcp_surface_profile`, `public_tool_count`,
  `surface_schema_version`, and the durable-only Data Quality Center. The quality
  view summarizes latest account snapshot valuation/timestamp coverage, account
  activity receipts, and active Monitor blind spots without contacting an upstream
  Provider. Provider checks retain their `live_probe` versus `configuration` label;
  configuration is never presented as reachability. Operational health and data
  quality keep separate statuses. Secret-safe Provider route receipts persist
  market/category, vendor-chain outcomes, cache/fallback selection, and typed error
  codes for 30 days (maximum 5,000 rows); they never persist fingerprints, payloads,
  or exception text. The quality center aggregates the most recent 24 hours.
- `instrument_resolve` — local-first lookup; a unique provider result may be cached.

Instrument resolution is local-first, not local-only. A local miss may use the
configured US/A-share/KR instrument directories; only one validated candidate is
atomically cached in the Instrument Master. The Master is a registry/cache, not
an allowlist. Directory failures remain typed provider errors.

All Router-managed Provider calls use the shared bounded cross-process admission
scheduler keyed by vendor and data category. It atomically reserves current or
near-future fixed-window capacity and waits asynchronously up to
`PROVIDER_RATE_LIMIT_MAX_WAIT_SECONDS`. A successful wait emits
`PROVIDER_ADMISSION_QUEUED`; local budget exhaustion is
`PROVIDER_ADMISSION_TIMEOUT`; an actual upstream quota response remains
`PROVIDER_RATE_LIMIT_ERROR` with `UPSTREAM_RATE_LIMITED`. Do not collapse these
states or reintroduce reject-only counters. Anonymous cancelled reservations expire
with their short window; this is not a strict FIFO job queue.

**Research files, judgment, and memory**

- `investment_case_read` (`query`, `context`)
- `investment_case_manage` (`create`, `update`, `archive`)
- `research_judgment_get` (`state`, `thesis_history`)
- `research_judgment_propose` (`research_state`, `thesis_revision`)
- `research_judgment_confirm`
- `research_memory_get` (`search`, `report`, `timeline`)
- `research_memory_append` (`journal`, `decision`)

Candidate Propose → Confirm / Reject / Withdraw remains mandatory. Codex must not
autonomously choose confirm or reject. When the user explicitly states the exact
decision in the current Codex chat, Codex must relay it as `reviewed_by="user"`,
`submitted_via="codex_chat"`, with the user's bounded instruction in
`authorization_note`; do not refuse or require a separate UI. This explicit chat
authorization is the highest authority inside the implemented, non-executing product
scope. Journal/Decision append and confirmed manage operations follow the same rule
and retain confirmer, idempotency, expected-version, and actor gates. Ambiguous target
or action references require clarification, and no confirmation authorizes orders or
other out-of-scope execution.

Research Subject `update` changes only confirmed file metadata (`title`, `summary`,
`topic_tags`, and `linked_case_ids`) through the existing user/external-agent gate and
an idempotent audit candidate. It does not rewrite a Thesis, Trade Plan, evidence,
report, Monitor, position, or historical research record.
The Research Subject title must identify the durable research object or research question, and
the summary must define stable research scope. Entry/add/trim, take-profit,
stop-loss, sizing, and position plans belong to the Thesis or Trade Plan, never the
Research Subject title/summary. Research Subject type and primary Instrument are immutable after creation.
For new lifecycle decisions use `DRAFT`, `ACTIVE`, and `ARCHIVED`; legacy Research Subject
`STRENGTHENED`, `WEAKENED`, and `INVALIDATED` values remain readable only for
compatibility because conviction state belongs to the Thesis.

A Draft/non-tracking Research Subject may contain research artifacts and proposed candidates,
but it cannot receive an ACTIVE/STRENGTHENED/WEAKENED Thesis or an ACTIVE Trade
Plan. A live Thesis requires an ACTIVE/STRENGTHENED/WEAKENED Research Subject; an ACTIVE Trade
Plan additionally requires a live Thesis. A tracking Research Subject cannot leave tracking
while a live Thesis or ACTIVE/PAUSED Trade Plan remains. Violations return the
non-retryable `RESEARCH_STATE_CONFLICT`. Never auto-activate or cascade another
entity to hide the conflict; each lifecycle transition retains its own explicit
Candidate confirmation.
An existing Thesis revision preserves status unless `thesis_status` is explicitly
provided; a real status transition requires `STRICT_REVIEW`. To archive a tracked
Research Subject, explicitly archive its ACTIVE/PAUSED Trade Plan first, retire the live Thesis,
then archive the Research Subject.
Assumption, Invalidation, Open Question, Watchlist, parent/rival Thesis, and linked
Research Subject references must be validated against their owning Research Subject/Thesis before proposal
and again before confirmation. Existence alone is insufficient. Retiring a Research Subject or
Trade Plan never silently pauses or archives a bound Monitor; callers must inspect
and transition Monitor definitions explicitly.

**Provider facts and technicals**

- `a_share_get_facts` (`snapshot`, `market_structure`, `capital`, `limit_up`,
  `sentiment`, `etf_option`, `financials`, `industry_cycle`,
  `company_operating_metrics`, or `research_reports`)
- `market_data_get` (`quote`, bounded `quotes`, `composite`, `bars`, `us_market`, `futures_curve`,
  or `spot_future_basis`)
- `technical_get_snapshot`
- `technical_render_chart`
- `us_company_get` (`fundamentals_snapshot`, `fundamental_statements`, `filings`,
  `insider_activity`, `company_updates`, `events`, or `live_news`)
- `us_context_get` (`macro`, `sentiment`, `prediction_market`)

**Phase 3A commodity futures facts**

- The existing `instrument_resolve`, `market_data_get`, `technical_get_snapshot`,
  and `technical_render_chart` tools support Yahoo
  continuous futures `GC=F`, `MGC=F`, `SI=F`, `HG=F`, `PL=F`, and `PA=F` through
  `future:US:*` IDs. Futures are unadjusted and always disclose non-spot and roll risk.
- Futures routing is asset-aware: Yahoo is primary; timestamped Sina quotes are a
  best-effort fallback for GC/SI/HG; Eastmoney daily bars are a best-effort fallback
  for all six metals and may be aggregated to weekly/monthly. There is no intraday
  OHLCV fallback, and a price-only minute line must never be promoted to candles.
- Formal CME metal contracts use `future:CME:*` identities, CME public contract/
  settlement facts, and Yahoo active-contract quote/bars. DCE `future:DCE:LH*`
  supplies official EOD chain/settlement facts only. Dukascopy supplies free
  broker/SWFX `commodity_spot:OTC:XAUUSD`, `XAGUSD`, and separately labelled
  rolling copper/light-oil CFDs. None may be relabelled as a licensed benchmark,
  exchange future, or spot commodity.
- Dukascopy also supplies `cfd:OTC:LIGHT_CMD_USD` through the upstream
  `LIGHT.CMD-USD` Jetta code. `USOIL` is a lookup alias only. This identity is a
  Dukascopy OTC rolling light-oil CFD—not WTI spot, NYMEX `CL`, a specific futures
  contract, or a continuous futures series.
- Dukascopy follows the current keyless `dukascopy-node` Jetta strategy: minute/
  hour/day data use UTC day/month/year buckets, up to 10 requests run per batch,
  and batches pause for one second. Completed buckets are cached; active `from`
  buckets are not. `DUKASCOPY_API_KEY` is legacy-fallback-only.
- Dukascopy OTC quote DTOs expose `display_price` plus `price_basis`; bid/ask
  observations normally use their midpoint while `last` stays null. Never
  relabel a quote midpoint as a traded price.
- `uv run trading-partner-futures-sync` explicitly refreshes contract definitions
  and persists EOD statistics vintages. It is idempotent and has no order effect.

**Korea Exchange market facts**

- `Market.KR` uses canonical bare-code identities such as `equity:KR:005930`,
  `equity:KR:000660`, `index:KR:KS11`, `index:KR:KQ11`, `index:KR:KS200`, and
  `etf:KR:069500`; Yahoo `.KS`/`.KQ`/caret symbols remain Provider aliases.
- `instrument_resolve`, `market_data_get` quote/bounded quotes/bars, and both
  technical tools support KR equity/ETF/index instruments through Yahoo with
  `Asia/Seoul` dates. Preserve `YAHOO_KR_DELAYED_QUOTE`, `data_delay_seconds`,
  and upstream intraday-history limits.
- Manual CSV Watchlist and durable price/technical Monitoring support KR. Moomoo
  Watchlist writes do not. `KR_POST_MARKET` uses XKRX sessions in the unified
  hourly dispatcher and Telegram run summaries.
- DART fundamentals/filings, KR news/sentiment/breadth, account sync, peer
  workflows, and KR Position Sizing are not implemented. Do not route them through
  US services or infer them from Yahoo quote data.

**Phase 3B company financial/operating facts and optional industry datasets**

- `a_share_get_facts(request={"operation":"financials",...})` returns normalized A-share income,
  balance-sheet, and cash-flow facts for up to 20 reported periods. It labels
  interim statements as cumulative/YTD, preserves publication cutoffs and
  provenance, and derives only ratios whose inputs are present. Sina is primary;
  Eastmoney is a narrower fallback. Equity Deep Dive includes this fact package.
- `a_share_get_facts(request={"operation":"industry_cycle","cycle":"hog",...})` returns official
  national monthly hog/pork/feed prices and pig-grain ratios plus the latest visible
  periodic capacity observation. Default `view=compact` returns the latest visible
  observation per selected metric with per-metric coverage; `view=series` pages a
  filtered history (`offset`, `limit<=200`, `has_more`). Optional `metric_codes`
  are lower_snake_case filters. It applies the publication-time `as_of` cutoff,
  makes no cycle-phase verdict, and discloses missing company operating data and
  live-hog futures curves. Explicit historical synchronization persists publication
  vintages and reports gaps; a 240-month request never implies continuous coverage.
- `a_share_get_facts(request={"operation":"company_operating_metrics","instrument_id":...})`
  downloads publication-cutoff-safe official CNINFO finalpage PDFs and returns a
  bounded, generic company operating series plus per-document parse receipts. It
  extracts explicit sales volume/price/revenue, slaughter/output, breeding-sow,
  and full-cost disclosures; financial statements remain owned by the existing
  fundamentals/statements path. Raw PDFs and extracted text never leave the Provider.

`us_context_get(request={"operation":"sentiment",...})` keeps Reddit inference and Moomoo
public-feed inference
source-separated. The Moomoo path is deterministic:
it performs exact-symbol relevance filtering, HTML cleanup, deduplication,
low-quality filtering, and versioned bilingual rule classification. It never
invokes a Skill or an LLM; Codex or another external host interprets the returned
samples and summaries. The feed is current-only and missing engagement remains
null rather than inferred.
US normalized statements route SEC → yfinance → Alpha Vantage. SEC is the
point-in-time primary and exposes filing/accession metadata; `latest` deduplicates
period ends while `vintages` keeps visible filing versions. yfinance and Alpha
Vantage are current-only fallbacks and must not be described as historical filing
vintages. Derived financial-quality metrics are emitted only for the deduplicated
latest view.
StockTwits formal access is no longer an active roadmap deliverable. The runtime
adapter, setting, and network allowlist were removed; historical enum/database
values remain readable for compatibility. Agents must not retry, scrape, or request
credentials for it.

**Accounts, sync, portfolio, workflows, and Challenge Review**

- `account_get` (`positions`, `transactions`) — durable only; positions preserve the
  full native-currency snapshot context, timestamps, open orders, and quality
  warnings; it never contacts brokers
- `external_state_sync` (`accounts`, `transactions`, `watchlist`) — the only public
  upstream refresh entry
- `portfolio_analyze` (`exposure`, `coverage`, `performance_summary`, `simulate_addition`)
- `challenge_review_get`
- `challenge_review_manage` (`start`, `resolve`)
- `research_workflow_run` (`deep_dive`, `catalyst_review`,
  `a_share_market_review`, `us_market_review`, `portfolio_review`, `peer_comparison`,
  `historical_validation_prepare`, `historical_validation_import`)

The two historical-validation operations parse but never execute LEAN Python,
write owner-only gitignored artifacts, and import only a user-downloaded
QuantConnect Results JSON. The user operates the free web UI. Remote code matching
and dataset version remain explicitly unverified; the bridge never confirms a
Thesis, mutates a Trade Plan, or creates a broker order.

Compact workflows never accept hidden Research Subject creation or account refresh. Create a
Research Subject first with `investment_case_manage(request={"operation":"create",...})`;
refresh accounts first with `external_state_sync(request={"operation":"accounts"})`.
Peer Comparison accepts one primary and 1–5 caller-specified same-market A-share/US
equity peers. It aligns normalized statements and optional current valuation facts,
does not discover/rank peers, and never mutates a Research Subject, Thesis, Trade Plan, or account.

**Scheduled operational CLI (not a public MCP tool)**

- `uv run trading-partner-post-market-sync` checks the XNYS calendar and runs ten
  minutes after the real session close. It refreshes all configured account
  providers before the exact active-source Watchlist sync, persists one terminal
  receipt per market session, and never executes an order.

**Watchlist, Risk v2, and Monitoring v2**

- `watchlist_get` (`groups`, `items`) — durable only
- `watchlist_manage` (`add`, `remove`)
- `portfolio_risk_get` (`policy`, `check`)
- `risk_policy_update`
- `monitor_read` (`definitions`, `dashboard`, `runs`, `events`)
- `monitor_manage` (`create`, `update`, `resolve_event`)
- `monitor_evaluate`

Every explicitly supplied Monitor rule requires a bounded human-readable
`description` on create/update. The stable `rule_code` remains a machine identity;
direction, threshold, severity, and meaning are separate persisted fields. Legacy
versions without a description remain readable but must be completed before an edit
can create a new version.

Monitoring also supports `monitor_read` operations `dashboard` and `runs` without
adding public tools. Dashboard embeds a compact per-Monitor latest-run summary;
`runs` filtered by `monitor_id` contains only that Monitor's observations, while
`run_id` returns the full immutable batch. `INTERVAL` definitions use a whole-hour `interval_minutes`
(minimum 60). `trading-partner-monitor-run due` performs deterministic due selection
before provider access for INTERVAL plus A-share/US/KR post-market groups;
`trading-partner-monitor-scheduler install` installs one hourly macOS launchd wake
and never invokes Codex or an LLM. A market group runs at most once per exchange
session after close plus the configured delay. Every evaluated rule is stored as an
immutable run observation, while events remain state-transition-only. Codex
market-review Automations must not duplicate Monitor evaluation or alerts.
Successful whole-hour INTERVAL schedules are anchored to the run-start hour so
Provider latency cannot turn a two-hour definition into a three-hour effective
cycle. Due dispatch uses live evaluation time, not a pre-fetch historical cutoff.
Dukascopy XAUUSD/XAGUSD INTERVAL schedules are venue-aware: the dispatcher skips
the published Friday-to-Sunday closure and daily maintenance break before Provider
access, reports `MARKET_CLOSED`, and resumes at the next observation window. When
explicitly enabled, current XAUUSD price rules may use a bounded Apify browser
fallback during the published IG Weekend Gold window. The observation keeps the
requested Monitor identity but must disclose `ig_weekend_cfd`, scrape time, and
proxy/not-spot warnings; it never supplies bars, technicals, XAGUSD, or historical
`as_of` facts and must never be presented as XAUUSD spot or LBMA gold.
Optional Telegram delivery uses a durable Outbox linked to either an event or a
market-close run. INTERVAL alerts remain transition-only. A-share/US/KR post-market
groups persist their ordinary transition events but enqueue no separate event-linked
Telegram cards: each evaluated group emits exactly one consolidated run summary,
including an explicit zero-change heartbeat and every changed-point detail. Source
and Outbox are committed
atomically, retry is bounded, and expired messages are not delivered late.
`trading-partner-notifications` provides secret-safe `status`, `test`, `flush`,
and explicitly authorized `enqueue` operations without adding an MCP tool.
`trading-partner-monitor-notifications` remains an alias. `enqueue` reads a
plain-text body from stdin and requires `--title`, `--idempotency-key`,
`--confirmed-by user|external_agent`, and a bounded `--authorization-note`;
the JSON receipt never echoes the body or authorization note. Internal
deterministic producers use the closed `SYSTEM` source; explicitly authorized
`MANUAL` writes retain their caller authorization and have no order effect.
Messages reuse the same run observations to include current price/time/source once,
then every rule's state, condition, and bounded human meaning. Repeated values and
distances remain available in the durable Run instead of being repeated on a phone
screen; one shared unavailable-fact cause is rendered once. Multiple same-Monitor
transitions in one run are delivered as one Telegram message without collapsing
their durable Monitor events. Telegram does not support responsive tables, so the
sender places symbol/current price in the first line, followed by the transition
summary and mobile-first vertical rule lines. Transition alerts and changed
post-market blocks include the prior observed price, price change, and the exact
Provider source from the run receipt. Price-change percentages are rounded half-up
and rendered with exactly two decimal places.
The prominent transition section must identify every changed rule by its exact
condition/threshold, bounded human meaning, severity, and event state; never reduce
the change to a bare `TRIGGERED`/`RECOVERED` label. Historical Outbox formats remain
readable. A single-transition headline includes its bounded condition; a
multi-transition headline stays a compact count and details each change below.
Prominent red/green Unicode alert bands distinguish a newly triggered or recovered
level because Telegram HTML cannot set text background colors. Common provenance
warnings are condensed to a human-readable basis line without hiding typed errors.
IG Weekend Gold cards
explicitly describe the value as an XAUUSD weekend-volatility proxy rather than
spot/LBMA. It does not generate or upload an image.

**Phase 3D judgment-to-plan controls**

- `research_judgment_propose(request={"operation":"research_state","kind":"trade_plan",...})`
  proposes a versioned Trade Plan; `research_judgment_confirm` remains the explicit
  user/external-agent confirmation gate.
- `research_judgment_get(request={"operation":"state",...})` returns the current Trade Plan and history.
- `portfolio_risk_get(request={"operation":"check","trade_plan_id":...})` returns deterministic
  A-share/US Position Sizing plus
  Risk Engine v2 checks; missing NAV, cash, FX, stop, freshness, or optional facts remain
  `NOT_EVALUATED`/`INCOMPLETE`.
- `monitor_manage` operations `create` and `update` can bind one exact confirmed Trade Plan version and
  compile its `MONITORABLE` conditions. `MANUAL` conditions remain human review items.
- Trade Plan `instrument_id` is the execution/position instrument consumed by
  Position Sizing and portfolio risk. Each monitorable condition may name a
  different fact/reference instrument, and a bound Monitor may display that
  reference instrument. This supports relationships such as UCO execution with
  `cfd:OTC:LIGHT_CMD_USD` observation without treating their prices, returns,
  multipliers, or currencies as interchangeable.
- Monitoring v2 fact comparisons cover price, volume, technical, fundamental, company
  event, macro, sentiment, Thesis state, and portfolio risk with typed unavailability.

Phase 2D also upgrades the existing `technical_get_snapshot` from the Phase 1F
US-only v1 calculation to one shared A-share/US daily-and-weekly engine.

Do **not** invent quotes or account balances. Phase 1E A-share tools are provider-backed and
must preserve envelope source/freshness/warning semantics. Phase 1F US tools are
provider-backed with Yahoo→Alpha Vantage routing. US breadth uses cached Yahoo
Screener totals over a disclosed listed-security universe that may include ETFs
and ADRs; sector rotation uses versioned Yahoo sector-index symbols. Neither is
presented as official exchange common-stock breadth, and unavailable high/low or
moving-average participation is never fabricated. For near-current non-closed
requests, a stale Yahoo regular quote may be replaced only by a newer timestamped
one-minute `includePrePost` bar with an explicit recovery/extended-hours warning;
pre/post-market recovery establishes only the latest price/time, so
open/high/low/volume stay null with `EXTENDED_HOURS_SESSION_RANGE_UNAVAILABLE`;
historical `as_of` remains cutoff-safe and Yahoo is not presented as complete
overnight equity coverage. If current pre-market has no same-day minute
observation, a prior-day post-market value may remain latest-known only with
`INTRADAY_QUOTE_UNAVAILABLE`; classify it by its own timestamp and derive
`previous_close` from that day's completed regular session rather than moving the
baseline back another day. Phase 1G combines current
Yahoo/Alpha facts with separately based SEC reported facts and preserves filing
visibility cutoffs. Phase 1H adds dated news,
vintage-safe FRED observations, source-separated social sentiment, and
current-only prediction-market probabilities. Phase 1I account ports read Schwab
through a project-owned `schwab-py` OAuth token, Moomoo OpenD, or a strict manual
CSV; persist account snapshots; and compute deterministic gross portfolio exposure
without implicit FX conversion. The Schwab adapter exposes only balances,
positions, and transactions — no order method or plugin CLI runtime dependency.
Moomoo Hot List is an optional `market_data_get(request={"operation":"us_market",...})` component,
not directional
sentiment. It uses the shared cross-process OpenD limiter, is cached in 15-minute
buckets, and requires OpenD 10.9 or newer. Older versions remain a typed
`MOOMOO_OPEND_VERSION_UNSUPPORTED` degradation. Moomoo discussion-post retrieval
is a separate public-feed Provider under `us_context_get(request={"operation":"sentiment",...})`;
it never
uses OpenD, a Skill, or an LLM at runtime.
Ordinary holdings, portfolio, and risk questions read the latest durable account
snapshots. Broker refresh is explicit: only
`external_state_sync(request={"operation":"accounts"})` may fetch and persist new account facts.
Snapshot staleness is disclosed, not an implicit trigger.
Transaction sync persists canonical native-currency account activities. Security
trades, dividends, interest, fees, transfers, corporate actions, and other cash
events share stable Provider event IDs; cash-only activities may omit
`instrument_id`, and unavailable fees remain null rather than zero. Schwab long
requests are accumulated through bounded 60-day windows. Moomoo history deals are
trade-only and explicitly mark fees and other activity categories unavailable.
Each sync stores an append-only coverage receipt with event deduplication counts,
effective window, snapshot density, mapping version, missing categories, and a
machine-readable `COMPLETE`/`INCOMPLETE` status. `portfolio_analyze/coverage` is a
durable-only read; it never refreshes a broker or computes P/L.
`portfolio_analyze/performance_summary` deterministically reconstructs native-currency
FIFO lots or reports broker snapshot cost basis. It separates realized/unrealized
P/L, dividends, interest, known fees, and external cash flow; every instrument can
be traced to durable activity IDs and an ending snapshot. It never performs FX
aggregation and remains `INCOMPLETE` when inception history, fees, corporate-action
lot effects, ending reconciliation, or timestamped valuation is not proven.
The owner-only `trading-partner-performance-reconciliation` CLI can inspect a strict
Schwab Realized Gain/Loss CSV and compare one redacted statement account/month with
the durable FIFO ledger. It writes only an immutable redacted draft, never contacts
Schwab, adds no MCP tool, and never constitutes A1 sign-off; account and symbol-level
residuals still require explicit human review.
Phase 1J restores one current durable Research Subject
context with contrary-first
evidence, explicit budget truncation, and optional latest portfolio positions.
Phase 1K bypasses ordinary discussion but persists material strict reviews with a
versioned ten-dimension checklist and explicit non-executing user resolution.
The workflow surface returns actual fact packages for six workflows while Codex remains the
synthesizer. Workflow receipts/reports and normalized historical transactions are
durable; workflow outputs never execute or directly mutate a current investment
judgment (`Thesis`). An
instrument-only `research_workflow_run(request={"operation":"deep_dive",...})` reuses one
non-archived Draft instrument
research file by default. Creating a new Draft requires explicit confirmer and
idempotency key; `create_case=false` preserves ad-hoc mode.
Draft Research Subject creation is a research-folder write, not long-term tracking, Thesis confirmation,
or trading authority. Catalyst Review does not auto-create a Research Subject.
For A-share Deep Dive, `industry_cycle="hog"` explicitly adds the compact national
hog-cycle package and, for equities, the company operating-metrics package. The
workflow never infers an industry cycle from an instrument or company name.

Phase 2 selects exactly one active watchlist upstream (`MOOMOO` or `MANUAL_CSV`).
The database persists complete group/membership lifecycle history and mutation
receipts. Reads are durable-first by default, may refresh only when explicitly
requested, and fall back to stale durable state with a
typed warning. Adds/removes require an allowed confirmer and idempotency key;
external deletion never deletes Phase 1 Research WatchlistItems or Research Subjects.
Unsupported provider codes stay visible without fabricated instruments.
For Moomoo durable item reads, omitted `group_name` selects the system `All` group
when present and returns explicit total/continuation metadata. Public Watchlist sync
always refreshes all groups and memberships.

Phase 2B stores append-only, explicitly confirmed risk-policy versions and performs
deterministic read-only checks over durable or explicitly refreshed account facts.
V1 covers account/price age, native-currency single-position concentration,
same-currency gross exposure/NAV, per-account cash and margin ratios, and duplicate
instruments across accounts. Missing NAV, price time, or FX facts produce
`NOT_EVALUATED`/`INCOMPLETE`, never an implicit pass. The system-default policy is
always disclosed until confirmed. A hypothetical addition is calculation-only;
all risk results carry `execution_effect=false` and no order surface exists.

Phase 2C stores explicitly confirmed, append-only Monitor versions and evaluates
active rules on demand or through the external `trading-partner-monitor-run` CLI.
V1 supports A-share/US/KR `PRICE_ABOVE`/`PRICE_BELOW` rules and a portfolio
`RISK_OVERALL_AT_LEAST` rule. Rule states are `QUIET`, `TRIGGERED`, or
`NOT_EVALUATED`; durable events are emitted only on state transitions, so repeated
unchanged facts do not create duplicate alerts. Provider failures and stale facts
remain `NOT_EVALUATED`. A versioned optional `valid_until` is an inclusive alarm
lifetime; expired Monitors are skipped before provider access, state mutation, or
event creation and report `MONITOR_EXPIRED`. It is separate from rule fact age.
Event acknowledgement/resolution never mutates a Thesis,
position, Risk Policy, or order, and every run carries `execution_effect=false`.

Phase 2D derives standard indicators through the open-source TA-Lib backend and
project-owned structure analysis over provider-backed adjusted daily bars. Phase 3A
adds explicitly unadjusted continuous-futures bars with Yahoo primary and a scoped
Eastmoney daily fallback. The shared technical engine supports A-share, US, and KR
equity/ETF/index instruments plus the seeded commodity-futures proxies, emitting daily and weekly
timeframes, regime states, disclosed metrics, clustered support/resistance, and
recent candlestick patterns. `technical_render_chart` returns an auditable
envelope, a permission-restricted local artifact reference, and an in-memory PNG
candlestick/volume/RSI chart. Hosts that do not promote MCP image blocks must embed
the returned `chart_artifact.display_markdown` verbatim. Technical outputs
remain `historically_validated=false`: they are derived facts, not forecasts,
strategies, trade signals, or execution authority.

**Not public MCP tools:** `evidence_create`, `evidence_update`, `report_create`,
`event_create`, `decision_update`, `journal_update`, `journal_delete`. Evidence /
Report / Event writes are internal services only.

Thesis/research-state changes follow Candidate Propose → Confirm / Reject /
Withdraw. Codex may propose changes but must not choose the review outcome itself.
An explicit user decision in the current chat is user authorization, not a Codex
self-confirmation: relay the exact candidate/action with `reviewed_by="user"`,
`submitted_via="codex_chat"`, and a bounded `authorization_note` containing the
user's instruction. Do not claim authenticated identity—the local stdio boundary is
caller-asserted—but do preserve this provenance in the audit record.
Journal and Decision append require explicit `user` or `external_agent`
confirmation. Decision records are research/position **intent** only — never
orders, fills, or positions.

## Architecture rules

1. Domain never imports MCP, SQLAlchemy, Alembic, Pydantic Settings, or providers.
2. Application never imports infrastructure or interfaces.
3. Interfaces only adapt protocols / validate inputs / convert to DTOs.
4. Only `src/bootstrap.py` connects application services to infrastructure.
   `infrastructure/composition/` may build infrastructure-only bundles but must
   never import `application.services`.
   Application-only service/context bundles live in `application/runtime.py`;
   infrastructure resource ownership and deterministic composition overrides live
   in `infrastructure/composition/runtime.py`. Neither is a second composition root.
5. Provider raw payloads never cross the infrastructure boundary.
6. Precise numbers come from tool snapshots with source, time, freshness, and basis.
7. The sole FastMCP server directly composes compact capability adapters; do not
   reintroduce legacy tool registrars, handler-name lookup, or a second argument-model registry.
8. Public schema minimization must preserve resolvable local `$ref` targets and closed
   discriminated unions; repeated schema may be shared only within one tool schema.

## Source layout

```text
src/
├── bootstrap.py
├── application/
├── domain/
├── infrastructure/       # composition/, persistence/orm/, providers/, config/
└── interfaces/
```

Imports are top-level (`application.*`, `domain.*`, `infrastructure.*`,
`interfaces.*`, `bootstrap`). There is no `trading_partner` package layer.

Docs: `docs/README.md` indexes the roadmap, consolidated phase specifications,
user guides, and historical archives.

## Secrets and configuration

- Static secrets live only in project-root `.env` (gitignored).
- Provider-managed rotating OAuth tokens may live only under project-root
  `data/secrets/` (gitignored, owner-only). Only the provider SDK may create or
  update them; never copy tokens between applications.
- Never read, print, or paste real `.env` contents into chat, logs, tests, or commits.
- Use `.env.example` for key names and safe defaults.
- When adding an `AppSettings` environment key, update `.env.example` and also
  add its safe default to the existing local `.env` without overwriting values;
  Secret keys must be added empty for the user to fill.
- Redact API keys, tokens, and credentials in every output path.

## Coding conventions

- Python 3.13, `uv`, hatchling, `src/` layout.
- Codex owns architecture, naming, boundaries, and acceptance; delegate code
  implementation to grok Build when external implementation help is useful.
- The project-scoped `deterministic_coder` custom subagent (`gpt-5.6-luna`,
  `max`) is the preferred local executor for bounded deterministic code changes
  after Codex has fixed architecture, file/module ownership, public contracts,
  naming, and acceptance criteria. Assign it concrete files/responsibilities and
  remind it that other agents and user edits share the worktree. It must not make
  product or architecture decisions, broaden scope, commit, push, or perform
  external side effects unless that exact action is explicitly delegated. Codex
  reviews its diff and owns final verification and acceptance.
- Do not assign code implementation to Claude Code / MiniMax.
- Typed settings via `AppSettings`; project `.env` keys have no global prefix.
- Entity IDs: `<prefix>_<uuid7>` via `uuid6.uuid7()`.
- Instrument IDs: `<asset_type>:<market>:<symbol>`.
- Money and market values use `Decimal`, not binary floats.
- All datetimes are timezone-aware ISO 8601.

## Out of scope until later phases

```text
local/automated backtest engines, execution, orders, fills
automated evidence ingestion, runtime LLM synthesis
order writes
```

## Upstream

TradingAgents and a-stock-data are **reference only** (see `references/`). They are
not runtime dependencies. Do not add MiniMax or Grok as project runtime deps.

## Verification

```bash
uv sync
uv run ruff check .
uv run mypy src
uv run pytest
uv run alembic upgrade head
```
