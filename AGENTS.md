# Trading Partner — Agent Guide

## Product intent

Trading Partner is a long-horizon investment judgment companion. Codex (or another
agent host) talks to the user; Trading Partner MCP supplies facts, research state,
and structured tools. The implemented Phase 1–3D boundary covers A-share/US research,
Korea Exchange quote/technical monitoring,
accounts, Research Subjects, Watchlist Hub, Risk v2, Monitoring v2, versioned Trade
Plans, deterministic Position Sizing, and professional daily/weekly technical
analysis, plus a manual QuantConnect Free code/result bridge and narrowly
confirmation-gated Schwab US stock/ETF order writes. The sole unattended order
exception is the dedicated installed SGOV cash-sweep scheduler described below —
this is **not** an automated backtest runner or general autonomous trading system.

Canonical product language is **Research Subject** in English and 标的、研究标的 or
研究档案 in Chinese, depending on whether the context emphasizes the object or its
durable file. `InvestmentCase`, `investment_case_*`, `case_id`, `case_type`,
`linked_case_ids`, and the opaque `case_` ID prefix are compatibility-boundary names,
not user-facing terminology. Equity means an actual stock Instrument only.

## Implemented boundary

The sole public MCP vNext Shadow surface is exactly **27** tools
(`mcp_vnext_shadow`). Grouped tools accept one required `request` object. Large
groups publish a flattened operation schema to reduce host context, then revalidate
the exact closed operation variant before dispatch, so fields from another operation
still fail without invoking a service. Application services remain separate; compact
routing belongs only to `interfaces/mcp/`.

**System and identity**

- `system_health` — health plus `mcp_surface_profile`, `public_tool_count`,
  `surface_schema_version`, the durable-only Data Quality Center, and a
  materialized-only `attention_summary`. Always follow with
  `investment_case_read/attention`; the summary cannot skip the inbox. The quality
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

- `investment_case_read` (`query`, `context`, `attention`)
- `investment_case_manage` (`create`, `update`, `archive`)
- `research_judgment_get` (`state`, `thesis_history`)
- `research_judgment_propose` (`research_state`, `thesis_revision`)
- `research_judgment_confirm`
- `research_memory_get` (`search`, `report`, `timeline`)
- `research_memory_append` (`journal`, `decision`)

The same grouped tools also expose Catalyst Agenda and Judgment Scorecard without
increasing the public tool surface: `research_memory_get/agenda`,
`research_memory_append/agenda_item`, `research_judgment_get/scorecard_history`, and
`research_workflow_run/judgment_scorecard`. Agenda reads are durable-only. Explicit
`trading-partner-catalyst-sync` routes free current Yahoo calendar dates and selected
FRED release IDs through the existing Provider Router (`CORPORATE_ACTIONS` for Yahoo,
`MACRO` for FRED) and stores an append-only sync
receipt; an empty or failed Provider result is never called “no catalyst.” User
create/revise/cancel/outcome-link writes retain actor, expected-version, idempotency,
and point-in-time visibility checks. Outcome links must remain within one Research
Subject/Instrument scope and may reference durable Event/Report/Evidence facts.
Outcome closure stores the actual occurrence time and a bounded human note; an
OCCURRED link correction appends another OCCURRED version. Console candidate choices
reuse durable timeline/search. A daily notification source ID is stable per date and
window, so later same-day data changes never enqueue a second summary.
Judgment Scorecard S1 locks one exact Thesis revision, adds deterministic Catalyst
outcome calibration to the existing eight discipline cards, preserves S0 runs, has
no aggregate score, and cannot mutate research state, a position, or an order.

Candidate Propose → Confirm / Reject / Withdraw remains mandatory. Codex must not
autonomously choose confirm or reject. When the user explicitly states the exact
decision in the current chat, the host must relay it as `reviewed_by="user"`,
`submitted_via="mcp_chat"` (`codex_chat` remains a compatibility alias), with the
user's bounded instruction in `authorization_note`; do not refuse or require a
separate UI. This explicit chat
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
Research Subject lifecycle is exactly `DRAFT`, `ACTIVE`, and `ARCHIVED`;
conviction state belongs to the Thesis.
Theme, macro, and portfolio-concern Research Subjects may intentionally have no
primary Instrument. The normal attachment flow is exactly Propose Instrument →
explicit Confirm/Reject/Withdraw. Confirming the create proposal attaches the
Instrument directly to the Research Subject; callers and Console UI must not require
a second Shortlist or Select step. Persisted Research WatchlistItem states
`WATCHING`, `SHORTLISTED`, `SELECTED`, and `REJECTED`, plus `update_status`, remain
readable for compatibility with older durable records and clients, but are not the
default user workflow. A later Trade Plan chooses its execution `instrument_id`
explicitly and does not require a prior `SELECTED` transition. Instrument attachment
never mutates the Research Subject identity, creates a position, or executes an order.

A Draft/non-tracking Research Subject may contain research artifacts and proposed candidates,
but it cannot receive an ACTIVE/STRENGTHENED/WEAKENED Thesis or an ACTIVE Trade
Plan. A live Thesis requires an ACTIVE Research Subject; an ACTIVE Trade
Plan additionally requires a live Thesis. A tracking Research Subject cannot leave tracking
while a live Thesis or ACTIVE/PAUSED Trade Plan remains. Violations return the
non-retryable `RESEARCH_STATE_CONFLICT`. Never auto-activate or cascade another
entity to hide the conflict; each lifecycle transition retains its own explicit
Candidate confirmation.
An existing Thesis revision preserves status unless `thesis_status` is explicitly
provided; a real status transition requires `STRICT_REVIEW`. To archive a tracked
Research Subject, explicitly archive its ACTIVE/PAUSED Trade Plan first, retire the live Thesis,
then archive the Research Subject.
Each Research Subject may hold several Thesis threads but at most one live PRIMARY
across ACTIVE/STRENGTHENED/WEAKENED. Multiple SUB Theses may share that PRIMARY;
COMPETITOR and BEAR represent alternatives and contrary judgments. SUB parent and
rival references must belong to the same Research Subject and are validated during
both proposal and confirmation. Confirmed revisions may update Thesis title, role,
parent, and rival metadata while preserving append-only candidate/revision history.
A live SUB requires a live PRIMARY parent. Retire live SUB children before retiring
their PRIMARY, and detach every SUB before changing that PRIMARY to another role.
Assumption, Invalidation, Open Question, Watchlist, parent/rival Thesis, and linked
Research Subject references must be validated against their owning Research Subject/Thesis before proposal
and again before confirmation. Existence alone is insufficient. Retiring a Research Subject or
Trade Plan never silently pauses or archives a bound Monitor. ACTIVE/PAUSED Monitors
require an ACTIVE Research Subject, and linked ACTIVE/PAUSED Monitors block Subject
or live-Plan retirement with `RESEARCH_STATE_CONFLICT`; callers must archive those
Monitor definitions explicitly.

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
- During the Dukascopy weekend closure, current XAUUSD price rules may use
  Binance PAXG/USDC spot and current LIGHT.CMD-USD/USOIL rules may use
  Hyperliquid XYZ CL/USDC. The former is tokenized gold; the latter is a HIP-3
  perpetual. Both retain USDC, venue/liquidity, and basis-risk warnings and must
  never be relabelled as the requested OTC identity, WTI spot, or NYMEX CL.
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
- `portfolio_analyze` (`exposure`, `coverage`, `performance_summary`, `simulate_addition`,
  `retro_history`)
- `research_judgment_get` (`challenge_review`) restores a Challenge Review
- `research_judgment_propose` (`challenge_review`) starts one; explicit resolution
  uses `research_judgment_confirm` (`challenge_review`)
- `broker_order_manage` — calculate the SGOV Shadow Preview, or preview/submit/read/
  cancel one exact Schwab US stock/ETF order through the expiring current-chat
  confirmation contract; no generic broker request or replacement operation
- `research_workflow_run` (`deep_dive`, `catalyst_review`,
  `a_share_market_review`, `us_market_review`, `portfolio_review`, `peer_comparison`,
  `historical_validation_prepare`, `historical_validation_import`, `trade_retro`)

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

Trade Retro is an immutable transaction-versus-plan discipline audit, not another
performance-attribution engine. `prepare` captures the current Trade Plan and
confirmed Decision Records before the requested period. `run` compares durable
broker transactions with the latest eligible pre-period snapshot and persists
coverage, missing-plan, inactive-plan, missing-invalidation, direction-record,
ambiguous-plan, round-trip, and same-day-reentry findings. It never treats a post-period plan as
evidence of prior discipline. Optional Bailian narration receives only bounded
deterministic facts, must answer in Chinese, and has no research/account/order write
port; deterministic results remain usable without it. `export` updates only the
owned marker block in the configured Obsidian weekly note and preserves handwritten
content. `retro_history` is durable-only and contacts no Provider.
`review` appends an explicitly confirmed human-review revision; it never overwrites
the generated Run or Finding. Each write requires an idempotency key,
`expected_version`, confirmer, and authorization note. It may record an overall
`OPEN`/`ACCEPTED`/`DISPUTED`/`RESOLVED` status, bounded correction note and action
items, plus `ACCEPTED`/`DISPUTED`/`RESOLVED` dispositions for exact deterministic
Finding keys. A disputed Finding requires a note. Stale writers remain
`TRADE_RETRO_REVIEW_VERSION_CONFLICT`; there is no hidden merge. `export` includes
the latest review and records that review version while retaining the original Run.

The Console-only durable Review Queue materializes Catalyst overdue items, open Trade
Retro reviews and action items, consecutive Judgment Scorecard gaps, and unresolved
Agent/Broker states without adding a public MCP tool. Each ReviewItem retains a stable
source key, first/last seen time, recurrence count, optional due time, status, and an
optional resolution reference. Human acknowledge/resolve transitions require the
Console session, expected version, idempotency key, actor, and authorization note;
resolution requires a bounded note. A successfully observed source disappearance may
auto-resolve an item. A failed or unavailable source read must never auto-resolve one.
If a closed source condition disappears and later recurs, the same item reopens with a
higher occurrence count. The Decision Workbench consumes this queue while the existing
Research, Monitor, Agenda, Retro, and Scorecard pages remain intact.
Each occurrence also retains its own opened/last-seen, first-acknowledged, and closure
timestamps plus MANUAL/AUTO resolution mode. Queue metrics must use occurrence history,
not lifetime first_seen timestamps or a paginated item list; zero-sample medians/rates
remain null. Decision Workbench may acknowledge, adjust a due time, or resolve an exact
ReviewItem through the same Console session/version/idempotency gate.

**Scheduled operational CLI (not a public MCP tool)**

- `uv run trading-partner-post-market-sync` checks the XNYS calendar and runs ten
  minutes after the real session close. It refreshes all configured account
  providers before the exact active-source Watchlist sync, persists one terminal
  receipt per market session, and never executes an order.
- `uv run trading-partner-sgov-plan preview` explicitly refreshes Schwab and prints
  an immediate all-account Shadow plan. The dedicated launchd scheduler runs once
  per phase at 15:45 and 15:55 America/New_York, or 15 and 5 minutes before an
  official early close, using a $2,000 hard cash floor plus $200 buffer per account.
  The first phase is preparation-only. The completion phase refreshes again and may
  submit at most one `SGOV` `BUY LIMIT` `DAY` `NORMAL` order per eligible Schwab
  account at the current ask. It rechecks zero margin, quote age/spread, existing BUY
  reserves, and the cash floor immediately before submission. Stable per-session,
  per-account preview/submit keys prevent duplicate Provider calls; `SUBMITTING` or
  `UNKNOWN` is never retried and requires reconciliation. Installation of this
  dedicated scheduler is the durable user authorization and uninstalling it revokes
  future automatic runs. It cannot sell, cancel, replace, use extended/overnight
  sessions, or submit another instrument. All other live actions retain the exact
  current-chat confirmation gate. Each phase emits a SYSTEM Outbox notification and
  never calls Codex or an LLM. Closed-day and non-due wakes make no Provider request.
  Retryable account-refresh and quote/sizing reads receive at most three bounded
  attempts; order submission is never retried after an unknown outcome. A blocked
  completion notice names the failed stage, attempt count, Provider, typed error,
  and safe HTTP status when available.
- `uv run trading-partner-retro` prepares, runs, reads, or exports Trade Retro
  records. `prepare` must run before the period being audited; `run` never refreshes
  a broker and incomplete transaction coverage remains explicit. `weekly` audits the
  last completed Monday-to-Saturday UTC US trading week, optionally exports it, and
  snapshots the following Monday-to-Saturday window for the next run.

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
`TECHNICAL` fact rules support explicit daily/weekly (`1d`/`1w`) metrics from the
shared Technical Engine. Ordered numeric rules may carry a separate recovery
threshold for deterministic hysteresis. Legacy rules without a technical interval
remain daily. Hourly/4-hour indicators and compound Boolean rules are not supported.

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
Dukascopy XAUUSD/XAGUSD/light-oil INTERVAL schedules are venue-aware: the dispatcher skips
the published Friday-to-Sunday closure and daily maintenance break before Provider
access, reports `MARKET_CLOSED`, and resumes at the next observation window unless
an enabled keyless weekend proxy supports that exact rule set. PAXG/USDC is the
first XAUUSD weekend reference and XYZ CL/USDC is the light-oil reference. Optional
IG Weekend Gold is the final XAUUSD fallback. These observations keep the requested
Monitor identity but disclose their exact source and proxy basis; they never supply
bars, technicals, XAGUSD, or historical `as_of` facts.
Retryable weekend-reference calls use at most three bounded attempts. Failed
primary/fallback hops are persisted on the immutable observation as structured,
secret-safe diagnostics containing Provider, stage, typed error code, optional HTTP
status, attempt, and retryability. Console Run details may render those fields but
must never persist or display request URLs, proxy values, headers, response bodies,
or exception text. Runs created before migration `0036` remain readable with an
empty diagnostic list; never infer a missing historical cause.
Scheduled Monitor quote reads also receive at most three bounded attempts when the
safe Provider diagnostic explicitly marks the failure retryable. Contract/authentication
failures are not retried, and a successful retry is disclosed by
`MONITOR_PROVIDER_READ_RETRIED`.
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
their durable Monitor events. Monitor notifications use Telegram Bot API 10.1+
Rich Messages and a native two-column table for state/severity plus the combined
condition/meaning; generic/manual notifications remain regular HTML messages. Quiet
rules are collapsed behind a count while triggered and unavailable rules remain
visible. Machine rule codes, repeated prices, and repeated distances stay in the
durable Run rather than widening the phone table. The
sender places symbol/current price in the first line, followed by the transition
summary and compact rule table. Transition alerts and changed
post-market blocks include the prior observed price, price change, and the exact
Provider source from the run receipt. Price-change percentages are rounded half-up
and rendered with exactly two decimal places.
The prominent transition section must identify every changed rule by its exact
condition/threshold, bounded human meaning, severity, and event state; never reduce
the change to a bare `TRIGGERED`/`RECOVERED` label. Historical Outbox formats remain
readable. A single-transition headline includes its bounded condition; a
multi-transition headline stays a compact count and details each change below.
Green means that the prior alarm condition cleared; it is not a bullish signal and
must never be described as a price or market recovery.
Provider interruption is rendered as one compact operational card rather than one
change per affected rule. A later quiet re-evaluation emits a blue data-restored
card; data restoration and green alarm clearance are different states.
Prominent red/green Unicode alert bands distinguish a newly triggered or recovered
level because Telegram HTML cannot set text background colors. Common provenance
warnings are condensed to a human-readable basis line without hiding typed errors.
Weekend cards explicitly describe PAXG/USDC as tokenized-gold proxy, XYZ CL/USDC
as a HIP-3 perpetual proxy, or IG Weekend Gold as a separate CFD proxy. They do
not generate or upload an image.

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
moving-average participation is never fabricated. For near-current requests, a
stale Yahoo regular quote may be replaced only by a newer timestamped one-minute
`includePrePost` bar with an explicit recovery/extended-hours warning. This check
also applies after the US post-market window closes so a newer real post-market
print is not discarded in favor of the regular close; it never implies continuous
overnight trading. Exchange quote DTOs expose `display_price=last` and
`price_basis=last`. Equity/ETF/index `previous_close` follows the actual returned
`quote_at + session`, never the requested session when a fallback observation is older;
`previous_close_basis=previous_completed_regular_session_close` names that contract.
Futures instead use `previous_completed_daily_bar_close` and must not be called a
regular-session close or settlement. A host must say 前收（前一已完成常规交易时段收盘）,
not 昨收, for the equity-like basis and must never call either basis the prior arbitrary K-line;
pre/post-market recovery establishes only the latest price/time, so
open/high/low/volume stay null with `EXTENDED_HOURS_SESSION_RANGE_UNAVAILABLE`;
historical `as_of` remains cutoff-safe and Yahoo is not presented as complete
overnight equity coverage. If current pre-market has no same-day minute
observation, a prior-day post-market value may remain latest-known only with
`INTRADAY_QUOTE_UNAVAILABLE`; classify it by its own timestamp and derive
`previous_close` from that day's completed regular session rather than moving the
baseline back another day.
Near-current US equity/ETF requests during the Sunday-Thursday 20:00-04:00
America/New_York overnight window route the local Moomoo OpenD dedicated
`overnight_*` snapshot fields before Yahoo. The returned session is `OVERNIGHT`,
`display_price` is the exact instrument's `overnight_price`, and `previous_close`
remains `prev_close_price`; regular/pre/post fields and related proxies are never
substituted. OpenD exposes one snapshot `update_time`, not a separate overnight
trade timestamp, so preserve `MOOMOO_OVERNIGHT_OBSERVED_AT_SNAPSHOT_TIME`, venue/
liquidity warnings, and source freshness. The snapshot must belong to the same
overnight window and be at/before the request cutoff. Missing entitlement, OpenD,
timestamp, or overnight value falls back explicitly with
`OVERNIGHT_QUOTE_UNAVAILABLE`; never claim full overnight coverage from Yahoo.
Phase 1G combines current
Yahoo/Alpha facts with separately based SEC reported facts and preserves filing
visibility cutoffs. Phase 1H adds dated news,
vintage-safe FRED observations, source-separated social sentiment, and
current-only prediction-market probabilities. Phase 1I account ports read Schwab
through a project-owned `schwab-py` OAuth token, Moomoo OpenD, or a strict manual
CSV; persist account snapshots; and compute deterministic gross portfolio exposure
without implicit FX conversion. The Schwab account adapter exposes balances,
positions, supported active one-leg open orders, transactions, and a read-only quote
used by SGOV Shadow Preview. A separate closed adapter exposes named
place/status/cancel endpoints for configured REAL accounts and has no generic request
or plugin CLI runtime dependency. Every place consumes a 30–300 second durable
preview and exact current-chat user authorization, except for the closed SGOV-only
scheduler authorization above; unknown responses are persisted and never retried
automatically. LIMIT and STOP_LIMIT BUY/SELL plus protective
MARKET/STOP/TRAILING sell orders are supported; AM/PM/SEAMLESS are LIMIT-only and
SEAMLESS is not overnight. Margin, overselling, shorts, options/complex orders,
replacement, unattended execution outside that SGOV BUY exception, and unbounded BUY
market/stop/trailing orders are blocked.
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
all risk results carry `execution_effect=false` and cannot invoke the separate
confirmation-gated broker-order service.

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
Optional composite judgment policies may add a bounded Playbook, 1–12 reference
Instruments, relative-strength pairs, and user-confirmed execution state. The
runtime computes 1h/4h/1d/3d returns, rule state, provenance, and session alignment
before a server-side LLM call. Alibaba Cloud Model Studio `qwen3.8-max` is the
default; the retained DeepSeek Provider can be selected through `LLM_PROVIDER`.
Only the Bailian adapter may use bounded web search for current macro-event context; search usage and
up to ten source URLs are persisted, while prices, positions, levels, returns, and
quantity facts remain deterministic-only. Explanations are validated as Chinese.
The LLM has no mutation/order port;
evidence IDs and quantity ranges are validated, session-misaligned divergence
actions are downgraded to WAIT, unchanged qualitative signatures skip calls, and
only material judgment changes create events/notifications. After primary failover,
one malformed fallback payload may receive exactly one structure-only retry; a second
invalid payload remains an explicit failed judgment. Never infer a fill or
mutate confirmed state from an LLM result.

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
An explicit user decision in the current chat is user authorization, not a host
self-confirmation: relay the exact candidate/action with `reviewed_by="user"`,
`submitted_via="mcp_chat"` (`codex_chat` remains a compatibility alias), and a
bounded `authorization_note` containing the user's instruction. Do not claim authenticated identity—the local stdio boundary is
caller-asserted—but do preserve this provenance in the audit record.
Journal and Decision append require explicit `user` or `external_agent`
confirmation. Decision records are research/position **intent** only — never
orders, fills, or positions.

## Shared Agent Runtime (Agent-A–D)

The Shared Agent Runtime is disabled by default and provider-neutral. It persists durable
conversations, explicit channel bindings, append-only messages, bounded model/tool
receipts, Pending Actions, cursors, and one-time channel handoffs through migrations
`0044`–`0046`. Migration `0049` adds durable Agent Turn lifecycle records for refresh/
disconnect recovery, migration `0050` adds explicit owner-scoped Agent presentation
preferences, and migration `0051` discards legacy Moomoo `initial_margin` values that
were persisted as financing usage before `debtCash` became the semantics. Terminal
failures persist only bounded safe error codes. Console Chat
and the strictly allowlisted Telegram poller are implemented;
Agent broker orders remain unavailable and are not authorized by a research action.

The model sees only private `tp_capability_search`, `tp_read`, and confirmation-preparing
`tp_prepare_action`; these names are never registered as public MCP tools. Agent-A may
auto-run durable/provider reads, instrument discovery/cache, and explicitly non-executing
technical artifacts. All other writes remain denied unless Agent-D's explicit operation
allowlist creates a Pending Action that the same channel/principal confirms using the
exact hash, version, expiry, and single-use opaque token. Sync/evaluate, accounts, risk
policy, and every broker write remain denied. Exact grouped-operation DTO validation and
the 27-tool MCP inventory remain unchanged. Conversation memory is continuity context,
never a source of current prices, positions, fills, or research state.

Capability discovery distinguishes automatic `read` from `prepare_action`; discovering a
write schema never invokes it. Independent read calls may execute with bounded parallelism,
while model-order messages/events/receipts remain deterministic. A refreshed Console may
list a durable `PRESENTED` action but must not persist its raw token or automatically recover
confirmation authority. Only an explicit user resume may rotate the token under exact
conversation/channel/principal/expiry/version CAS; the old token becomes invalid and the
arguments plus expiry remain unchanged. Navigation-only Console context is untrusted and
must never substitute for a capability read. The default Bailian Agent endpoint publishes
bounded native Web Search and extractor support; usage plus source URLs must remain explicit,
and webpages cannot override canonical Trading Partner price, position, level, return, or
quantity facts. Endpoints without native support remain search-disabled. Agent broker orders
remain closed unless a later explicit product decision changes that separate gate.

The Console Agent composer links Provider, model, and reasoning-effort selection. Selecting a
Provider asks the backend to fetch and briefly cache its standard model directory with
server-owned credentials; the browser receives only bounded text-model IDs and capability
metadata, never an API key or full endpoint. Catalog failure falls back visibly to the configured
default model. The runtime revalidates the selected Provider, catalog model, and reasoning effort
before persisting the user message, so a modified browser request cannot inject an arbitrary
model name.

## Console heading and control language

Console sections must use one consistent information hierarchy. A card header has **at most two
text levels** and uses exactly one of these modes: **kicker → title** for a functional section, or
**title → object subtitle** for an object overview such as `THEME` followed by the specific Theme
name. Never render kicker, title, subtitle, and description together in one header. The rendered
header keeps a divider between the heading and the card body.

- **Kicker** names the functional domain, workflow stage, or operating constraint. It is short,
  uppercase, and must not repeat the title with different capitalization. Examples:
  `EVENT COVERAGE`, `DECISION WORKFLOW`, `RESEARCH HEALTH`, `APPEND-ONLY EDITING`.
- **Title** names the stable object or section the user is viewing. Use concise Title Case for
  ordinary sections. For a Research Subject overview, the subject type is the title (for example,
  `THEME`) and the specific Research Subject title is the subtitle.
- **Object subtitle** is reserved for the specific identity beneath an object-type title. It must
  not be combined with a kicker.
- **Description** is an optional single, concise intro paragraph below the header divider. It may
  explain scope, behavior, provenance, or a safety boundary, but is never another header level.
  Do not use a metric list such as `Next 7D / upcoming / overdue` as a title; render metrics in the
  card body.

Avoid synonymous pairs such as `TODAY` / `Decision Inbox`, `CATALYST AGENDA` /
`Catalyst Agenda pulse`, or `RESEARCH SUBJECTS` / `All Research Subjects`. Prefer distinct
relationships such as `DECISION WORKFLOW` / `Today’s Inbox` and `EVENT COVERAGE` /
`Catalyst Pulse`; put any further explanation below the divider. A section should make sense from
its two header levels alone.

Passive labels and interactive controls must also read differently. Statuses use a passive
dot-plus-text treatment without border, fill, hover, or button-like padding. Tags describe nouns
or classification values and use the passive tag treatment. Buttons use an action verb, retain an
obvious border/fill plus hover/focus/disabled states, and must not be styled like tags. Description
Lists across pages use the shared component and top-rule treatment rather than page-specific
boxed variants. Primary, destructive, and secondary actions must be spatially and visually
distinct. When adding or renaming a prominent Console section, update the rendered-HTML regression
tests so the intended heading relationship cannot silently regress.

Every editable Console field that is required for the current action must show a red leading
asterisk in its visible label and expose matching native `required` or `aria-required="true"`
semantics. Conditional requirements show the asterisk only while the condition applies; an
either/or requirement marks the field group rather than incorrectly marking every member.
Placeholders, helper text, validation errors, and a `(Required)` suffix never replace the
asterisk. Optional fields receive no asterisk and do not need an `(Optional)` suffix. Immutable
disabled metadata is not marked required in edit mode. New or changed forms must extend the
Console UI-convention regression test so this contract cannot silently regress.

## Architecture rules

1. Domain never imports MCP, SQLAlchemy, Alembic, Pydantic Settings, or providers.
2. Application never imports infrastructure or interfaces.
3. Interfaces only adapt protocols / validate inputs / convert to DTOs.
4. Only `src/bootstrap.py` and the sanctioned `src/composition_root/` package
   connect application services to infrastructure. `bootstrap.py` stays the
   public façade (`ApplicationContainer`, `build_application`); bounded graph
   builders under `composition_root/` may import both layers and are enforced
   by the architecture boundary tests.
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
├── bootstrap.py          # composition-root façade
├── composition_root/     # bounded app+infra graph builders (with bootstrap.py)
├── application/
├── domain/
├── infrastructure/       # composition/, persistence/orm/, providers/, config/
└── interfaces/
```

Imports are top-level (`application.*`, `domain.*`, `infrastructure.*`,
`interfaces.*`, `bootstrap`). There is no `trading_partner` package layer.

Docs: `docs/README.md` indexes the roadmap, consolidated phase specifications,
user guides, and historical archives.

## Documentation placement

The root `README.md` is the project's simplified product manual, not a design
record. It stays limited to the product tour, capability summary, safety
boundary, quick start, operational command cheat sheet, and documentation
links. Do not grow it with design or implementation narrative.

Design and implementation content — architecture internals such as composition
bundles and ORM grouping, provider pacing/fallback rules, runtime semantics,
confirmation/idempotency contract detail, and capability contracts — belongs
in this file when it defines an agent-facing boundary, or in a dedicated page
under `docs/` (typically `docs/operations/` or `docs/guide/`) for
operator/user-facing detail. When the detail already exists here or under
`docs/`, link to it from the README instead of restating it. Every new `docs/`
page must be added to the `docs/README.md` index.

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
local/automated backtest engines, autonomous/unattended order execution except the
closed SGOV BUY cash-sweep scheduler
automated evidence ingestion, general-purpose/autonomous runtime LLM synthesis
outside enabled composite Monitor judgment and optional Trade Retro narration
order replacement, options/complex orders, short selling, Schwab API overnight orders
```

## Upstream

TradingAgents and a-stock-data are **reference only** (see `references/`). They are
not runtime dependencies. Do not add MiniMax or Grok as project runtime deps.

## Verification

Use progressive verification. Test effort must be proportional to the current
change; do not run every check after every edit.

- Inner loop: run only the exact affected test node/module and lint only changed
  Python files. Prefer commands such as
  `uv run pytest tests/unit/test_x.py::test_y -q` and
  `uv run ruff check path/to/changed.py tests/path/to/test_changed.py`.
- Feature checkpoint: run the directly affected test directories/modules. Do not
  add coverage, wheel builds, dependency audits, or the entire suite.
- Subagents/workers must not run repository-wide pytest, mypy, coverage, frontend
  builds, wheel smoke, or audits unless the parent explicitly delegates that one
  check. They report their focused commands and results to the main agent.
- The main agent owns broad verification and runs it at most once after the last
  relevant code change. Do not repeat an already successful command when its
  covered files have not changed.
- Full coverage, isolated-wheel smoke, dependency audits, SBOM, and secret scans
  belong to CI/release verification unless the change directly affects that area
  or the user explicitly requests them.
- Documentation-only changes require formatting/diff checks, not pytest.

The final local backend checkpoint, when warranted, is:

```bash
uv run ruff check .
uv run mypy
uv run pytest -q
```

Run `uv run alembic upgrade head` only for migration/persistence changes. CI remains
the authority for the full coverage floor and packaging/security matrix.
