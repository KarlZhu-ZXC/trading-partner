# Product capability contracts

Maintained agent-facing detail owned by the root `AGENTS.md`. Read the sections
relevant to the affected behavior; paths in this document are repository-relative.

## Implemented boundary

The public `mcp_vnext_shadow` v12 surface has nine entry tools over the unchanged
24-capability/106-operation Registry. `capability_discover` reveals a compact catalog,
operation names, or one exact original input schema plus `call_tool`. `capability_read`
routes only READ_DURABLE/READ_PROVIDER capabilities; `capability_write` routes only
non-reserved confirmation-gated capabilities and requires the original capability
name as confirmation. Neither gateway accepts the six dedicated tools:
`instrument_resolve`, `research_judgment_propose`, `research_judgment_confirm`,
`external_state_sync`, `broker_order_manage`, and `technical_render_chart`.
Those preserve their own annotations and handlers. Discovery never invokes business
services or grants authorization. Removed public names remain capability identities
for durable records, Console calls and next-read hints, never callable MCP aliases.
Full inputs retain original nesting under gateway `arguments`, closed validation,
defaults, actor/version gates and result compaction. Console/Agent retain the full
Registry. No dynamic per-connection registration or schema truncation. Instrument
resolution exposes its small complete schema to avoid an extra discovery round.

**System and identity**

- `system_health` — health plus `mcp_surface_profile`, `public_tool_count`,
  `surface_schema_version`, the durable-only Data Quality Center, and a
  materialized-only `attention_summary`. Always follow with
  `research_get/attention`; the summary cannot skip the inbox. The quality
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

**Technical platform runtime**

- Installed runtimes pin one owner-controlled `RUNTIME_ROOT`; every mutable token,
  lock, attachment, backup, Observation inbox, reconciliation artifact, and optional
  account-basis checkpoint derives from it. Wheel/site-packages directories contain
  code and static defaults only. Real Observation bodies and account-basis values are
  Git-ignored and must never be packaged, committed, or copied into tests/docs.
- Persistent trusted-LAN Console mode binds only the authenticated Next.js Web
  process to `0.0.0.0`; the data API remains on `127.0.0.1:8765`. The LaunchAgent
  stores only an owner-only password-file path, never the password, a URL secret,
  or a `NEXT_PUBLIC_*` value. `trading-partner-agent console install --lan`
  generates/reuses that file and subsequent ordinary Console restarts bring up API
  plus LAN Web together. This remains trusted-LAN HTTP, not public hosting.
- The loopback Console BFF reuses the exact public capability schema, confirmation
  policy, and application handler but must retain complete local read results.
  MCP's 15 KiB result compaction applies to MCP/Agent transport and the explicit
  Capability Workbench only; it must never determine which local Subjects,
  Candidates, Monitors, positions, Watchlist items, Agenda items, Retro runs,
  Scorecards, or ReviewItems exist. Owned Console reads invoked after a user action
  must explicitly request the same full-result mode. `_truncated` markers are
  transport metadata, never domain rows or actionable identities.
- Non-test startup must verify the actual migration head against the single release
  marker in `application.ports.database` before repository construction. Diagnostics
  expose actual/expected revisions; ordinary reads never migrate the database.
- SQLite production connections use WAL, `synchronous=NORMAL`, a 30-second busy
  timeout, and bounded autocheckpointing. Maintenance status exposes only safe WAL
  counters. Do not disable WAL to hide writer contention.
- Launchd-triggered Monitor due, Post-market, and SGOV auto-run paths retain their
  business idempotency and file locks and additionally use durable Operational Job
  claim/lease/heartbeat/attempt/terminal receipts. Lease expiry becomes
  `INTERRUPTED`; it never implies that an unknown order is safe to retry. A launched
  Python orchestrator dispatches project child modules through its current
  `sys.executable`; it must not rediscover `uv` through launchd's minimal `PATH`.
- Optional OpenTelemetry is disabled by default and accepts only allowlisted `tp.*`
  attributes. Prompts, payloads, URLs, credentials, headers, exception text, and stack
  traces must never enter spans.
- Optional semantic Research Search is local-only and disabled by default. FTS5 stays
  canonical lexical recall; vector projection is rebuildable, never a business source
  of truth, and any embedding failure falls back to lexical search without blocking a
  research write.
- Failed Agent model turns persist only closed diagnostics: machine error code,
  Provider/model identity, safe HTTP status, retryability, and bounded attempt count.
  Console renders these as a dedicated durable notification and restores it after
  refresh/reconnect. The notification stays above the scrollable conversation and
  may be dismissed as presentation-only local state; dismissal never deletes or
  resolves the durable failed turn. Never persist or display an endpoint URL, response body,
  exception message, header, request payload, or credential.

**Research files, judgment, and memory**

Apply the user's default `strategy_v1` discipline only when the user is making or
reviewing an actionable investment judgment for a specific stock or ETF: equity
Thesis conviction, entry, add, hold, reduce, exit, or a concrete trade setup. It
must not activate from software development, UI/schema/test/docs work, generic
financial facts, domain identifiers such as Thesis/Trade Plan/position/strategy, or
non-equity assets. When activated, cover `UPSIDE`, `SIDEWAYS`, `PULLBACK`, and
`INVALIDATION`, with an explicit action or `NO_ACTION` for each. If the user selects
another primary Strategy, keep BossMo only as a risk/process check. This discipline
is model interpretation, not a Provider fact, confirmation, position mutation, or
order authorization.

- `view_get` (`inbox`, `review`, `current`) — bounded durable View intake reads. Inbox
  lists pending/deferred Observation reviews, review compares one exact revision with
  confirmed Thesis/Plan/Decision, Position, Monitor, and coverage context, and current
  derives the latest formal view from an exact adopted review plus Decision. These
  operations never return private full note bodies or contact a Provider.
- `research_workflow_run/evaluate_view` — explicitly confirmation-gated Provider
  evaluation that appends a non-authoritative configured escalated-review draft; it
  never confirms or mutates judgment. Contributor models additionally require the
  explicit training opt-in.

- `research_get` (`query`, `context`, `attention`, `state`, `thesis_history`,
  `scorecard_history`, `challenge_review`, `search`, `report`, `timeline`, `agenda`)
- `investment_case_manage` (`create`, `update`, `archive`)
- `research_judgment_propose` (`research_state`, `thesis_revision`)
- `research_judgment_confirm`
- `research_memory_append` (`journal`, `decision`)

Phase 4A extends the existing Decision Record rather than creating another
decision module. A Decision may carry optional `strategy_code`,
`strategy_version`, one structured `scenario` (`UPSIDE`, `SIDEWAYS`, `PULLBACK`,
or `INVALIDATION`), an exact `trade_plan_id` + `trade_plan_version` pair, and an
aware `review_due_at`. The exact Trade Plan version must exist and belong to the
same Research Subject. These fields remain intent/review metadata only and never
authorize or create an order. Historical Decisions without them remain readable.
An elapsed `review_due_at` is materialized through the existing ReviewItem and
Attention path as `DECISION_REVIEW_DUE`. The source stays active until a later
Decision explicitly names the exact prior Decision through
`supersedes_decision_id`, or the user manually resolves the ReviewItem. Failed or
bounded source reads must never auto-resolve it.

The Console-only Moomoo living-note intake is an observation source, not another
Research or decision module. It reads the local private-note cache without writing
to Moomoo and stores content changes as immutable external-note revisions. Attribution
uses a deterministic explicit-speaker section state: each dated section starts as
`USER`. A line-leading `@speaker` marker (with optional colon/body) is canonical and
may introduce any bounded new speaker. For legacy text without `@`, only `boss墨`,
`宝总`, and `姜汁汽水` are recognized as named speakers. Following unlabeled paragraphs
inherit that speaker until the next date, explicit line-leading marker, or explicit
USER label. Every other bare `heading:` prefix—including 财报看法、整体观点、风险、结论—
inherits the current speaker and must never create a person. Mid-sentence `@` mentions
do not change attribution.
With the user's explicit private-content authorization, new
revisions may be interpreted in the background by OpenCode Go
`deepseek-flash` (DeepSeek V4.1 Flash) at `max` effort under a strict schema and
120-second timeout. Escalated review drafts also default to `deepseek-flash` at
`max`. Optional OpenCode Go `muse-spark-1.3-contributor` at `high` requires the owner to have explicitly
accepted Contributor prompt/completion training through
`EXTERNAL_NOTE_CONTRIBUTOR_TRAINING_OPT_IN=true`. The configured protocol router must send Muse Spark 1.3 and Grok 4.6 through
Responses rather than Chat Completions. Every OpenCode Go HTTP request carries an
`x-opencode-session` derived as an opaque hash of one stable conversation or workflow
identity. Tool rounds, retries, and schema repair must reuse it; raw Conversation,
Note, Monitor, and idempotency IDs must never leave the process in that header. The
model draft compares the prior successful revision and covers USER
`UPSIDE`, `SIDEWAYS`, `PULLBACK`, and `INVALIDATION`. It cannot confirm a Thesis,
Decision, Plan, Monitor, position, or order. Console `Review as Decision` only
prefills the existing Decision confirmation dialog with the exact note revision.
The confirmed Decision stores that exact revision through optional
`external_note_revision_id`; the revision must exist, belong to a Note whose primary
Instrument matches the Research Subject, and have been observed no later than the
Decision time. Historical Decisions without the field remain readable.
`SUMMARY_ONLY` list text is change-detection evidence only: it must not be sent to
the model or adopted, and eviction of a prior FULL editor cache must not create a
false downgrade revision when the visible summary is unchanged.
Console Observation-to-Research navigation pins one exact revision and reads only
already-persisted, attribution-validated interpretation/review payloads. Prefer a
successful escalated draft for that revision, otherwise its first pass. Private
source/model text must not enter URLs or browser storage. USER statements may seed
editable DRAFT Theses; aggregate key-level text is a labelled reference, never an
automatically inferred numeric stop or entry. USER scenarios seed MANUAL/REVIEW Plan
conditions. Preserve existing conditions, numeric settings, and unsaved edits. Subject
metadata still defines stable scope; creation/prefill never confirms a Thesis/Plan,
adopts an Observation review, or invokes a model. All formal writes keep their gates.

External observations use one provider-neutral adapter contract. Source capabilities
declare full-text, incremental-sync, interactive-session, and content-mode support;
the same sync may aggregate several sources while identity remains `(source,
external_id)`. The owner-controlled Local Observation Bridge accepts only the closed
`observation-source-v1` full-text JSON contract. Moomoo, future TradingView capture,
and other adapters must converge there or emit the same canonical snapshot; they must
not create source-specific Journal, interpretation, or Decision modules.
Observation idempotency is keyed by a stable source revision key, not content hash.
Replaying the same source revision creates no row; a newly observed reversion to old
content remains a new revision; an unseen observation older than the latest source
time is ignored with an explicit out-of-order warning. A Moomoo list text may be
promoted from `SUMMARY_ONLY` only when a prior editor body proves line-by-line that
the list text is its strict ordered superset. Promotion preserves prior paragraph
boundaries and may add only a proven prefix and/or suffix; middle insertion or rewrite
fails closed. Date section order is detected independently for every revision as
`NEWEST_TO_OLDEST`, `OLDEST_TO_NEWEST`, `MIXED`, or `UNKNOWN`; the model receives that
closed result and must not infer chronology from position for mixed/unknown notes.
The same speaker inheritance rules apply so a named viewpoint neither stops after one
line nor leaks across the next date.
Console, CLI, and future adapter captures share a bounded cross-process observation
sync lock. In-process concurrent captures serialize; cross-process contention waits
briefly and then returns retryable `OBSERVATION_SYNC_BUSY` rather than racing identity
or revision writes.
Moomoo note ingestion must never control or automate the desktop UI. An optional
read-only HTTP enrichment layer may use an explicitly configured owner-only Cookie
file to refresh the internal note-list response and editor HTML. The Moomoo desktop
CEF Cookie database contains only presentation state such as locale; authentication
is injected by its native bridge and must not be misrepresented as a reusable Cookie.
The user prohibits Computer Use or UI automation against Moomoo, not read-only
process/network diagnostics. With explicit user authorization, secret-safe analysis
may inspect process metadata, local IPC, or network protocol shape, but must not alter
the app, system proxy/certificate state, account state, or trading state, and must
never display or persist recovered credentials. A remote Cookie may alternatively
come from an explicitly authenticated Web session and be provided over stdin. Requests are serial,
bounded, and sleep for a newly sampled delay inside the configured min/max window.
The Cookie, query identity, response body, and endpoint details never enter logs,
receipts, database rows, or Console errors. Missing/expired authentication, throttling,
or internal-page drift falls back to the local cache; list summaries remain
`SUMMARY_ONLY` and never reach the model or Decision adoption.

The same grouped tools also expose Catalyst Agenda and Judgment Scorecard without
increasing the public tool surface: `research_get/agenda`,
`research_memory_append/agenda_item`, `research_get/scorecard_history`, and
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

**Console Changes Since Review**

`GET /api/research/{subject_id}/changes` is a durable-only application projection.
An omitted baseline chooses the latest user Decision by `recorded_at`; an exact
Decision ID pins that reviewed version, and `none` pins no completed review.
Never substitute current Thesis/Plan values for those exact references. Observation
queries must be scoped before pagination, Monitor old/new values come from immutable
run observations, and Agenda comparisons retain version visibility. Source failure
is independently UNAVAILABLE and legacy Monitor history is PARTIAL. These reads never
refresh Providers, invoke models, mutate reviews or send notifications. Same-Instrument
matching is not an assumption/invalidation link. Preserve source/occurrence/recording
metadata, including unavailable provenance; never reconstruct it from current state.
The selected change can relocate its page by stable ID. Console URLs may keep only
opaque context IDs and numeric offset, never source/model text. Journal's exact
revision read validates the Subject Instrument and returns no-store; the existing
review confirmation workflow owns all writes.

**Console Quick Review**

`GET /api/research/{subject_id}/quick-review` composes durable sources only. A
20-minute server token pins exact baseline references, source changes, latest USER
thinking and formal versions. POST requires `confirmed=true`, rationale and an
idempotency key; maintain appends NO_ACTION, defer appends RESEARCH_MORE with a future
review_due_at through DecisionRecordService's NORMAL-mode, user-actor and unique-key
gates. Retry retrieves the matching completed intent; no reload-triggered write.
Recheck source fingerprint before append; never silently adopt a new baseline.
General review does not adopt or close Observation reviews or authorize orders.

Latest thinking is exact-Instrument MOOMOO_NOTE only, newest revision per note,
FULL USER-attributed sections only. Existing model summaries require same-revision
success plus valid ordinals pointing to the selected USER blocks. Never fall back to
old success. Quote attribution, unavailable interpretation, missing/future dates,
inferred years and bounded excerpts remain explicit. Use configured local timezone
for note-date visibility. Page loads never sync notes or call models; the existing
explicit note-refresh action owns capture and authorized analysis.

**Console valuation and judgment calibration**

`GET /api/research/{subject_id}/calibration` is durable-only and pins the exact
user Decision plus Thesis/Plan versions. Conditions reuse the Changes Since Review
exact-link check; source failures and bounded histories remain explicit. Scorecard,
Agenda and Retro are evidence, never a combined outcome score or causal attribution.

Console valuation uses `normalized_diluted_eps_pe_v1`: annual SEC USD diluted EPS
multiplied by explicit user normalization and P/E assumptions, with deterministic
nine-cell sensitivity. No EV, aggregate equity value or split adjustment is inferred.
The explicit `/valuation/source` POST reads financials and returns a bounded,
process-owned one-hour token. `/valuation/calculate` accepts only the token and user
assumptions, never caller-supplied facts. `/valuation/versions` requires explicit
confirmation, authorization note and idempotency; it appends a Journal NOTE tagged
`valuation_v1` using existing audit/search/unique-key gates. Version identity is the
journal_id, with optional exact same-Subject supersedes link and branching allowed.
History is durable-only. It never confirms Thesis, Decision or orders. Business-model
eligibility is user attested, not an inferred Provider fact. No production migration
or model call is part of these operations.

**Provider facts and technicals**

- `a_share_get_facts` (`snapshot`, `market_structure`, `capital`, `limit_up`,
  `sentiment`, `etf_option`, `financials`, `industry_cycle`,
  `company_operating_metrics`, or `research_reports`)
- `market_data_get` (`quote`, bounded `quotes`, `composite`, `bars`, `us_market`, `futures_curve`,
  or `spot_future_basis`)
- `technical_get_snapshot`
- `technical_render_chart`
- `us_get_facts` (`fundamentals_snapshot`, `fundamental_statements`, `filings`,
  `insider_activity`, `company_updates`, `events`, or `live_news`)
- `us_get_facts` (`macro`, `sentiment`, `prediction_market`)

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

KR expansion is not current work. Retain the implemented slice below without
adding DART, KR broker accounts, or other Korean-market integrations.

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

`us_get_facts(request={"operation":"sentiment",...})` keeps Reddit inference and Moomoo
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

- `portfolio_get` (`positions`, `transactions`) — durable only; positions preserve the
  full native-currency snapshot context, timestamps, open orders, and quality
  warnings; it never contacts brokers
- `external_state_sync` (`accounts`, `transactions`, `watchlist`) — the only public
  upstream refresh entry
- Moomoo historical-deal synchronization enriches exact order fees through bounded
  `order_fee_query` batches (maximum 20 orders, 10 requests per 30 seconds per
  account). One order fee is allocated exactly once across its partial fills. A
  failed, partial, or invalid fee read never drops the trades: Net P/L remains
  unavailable and Console may show explicitly labelled Gross P/L instead.
- Instrument performance separates `net_trading_pnl`, exact `dividend_income`, and
  `total_pnl`. Schwab dividend identity uses an explicit security Instrument first;
  cash-only rows may use a full-symbol token from the bounded description only when it
  uniquely matches a same-account equity/ETF candidate from durable activity or the
  current snapshot. Ambiguous/unmatched cash stays unattributed. An existing NULL
  transaction identity may be enriched only when every other normalized fact is equal.
  Corporate-action lot effects and missing transferred cost basis remain fail-closed
  unless a strict owner-verified Broker Statement/position-import basis checkpoint
  replaces the open lots at an exact timestamp. A checkpoint carries quantity, total
  native-currency cost, source reference, and optional document hash; it creates no
  trade or cash flow. A replaced zero-cash position-import activity is excluded from
  Trade Cycle counts. Later trades, DRIPs, dividends, and corporate actions continue
  normally, and any new mismatch fails closed again.
Behavior summary v3 retains monetary `payoff_ratio` and adds `return_payoff_ratio`,
`avg_win_return`, and `avg_loss_return`. Per-cycle return is net trading P/L divided
by maximum simultaneously deployed purchase-cost capital (fees excluded from that
capital denominator); averages weight eligible closed active Cycles equally.
`return_basis=NET_PNL_OVER_MAXIMUM_DEPLOYED_CAPITAL` is explicit. Wire returns are
fractions, while both payoff ratios are dimensionless. There is no minimum-sample
policy or sufficiency label: any nonempty valid sample may produce its descriptive
value. Payoff ratios require at least one valid winner and loser and a nonzero loss
average. Keep actual counts and exclusions visible. Missing/nonpositive capital is excluded,
never estimated. Native-currency normalization permits mixed-currency percentage
samples without FX; monetary averages/ratio still require one currency. Existing
historical summaries and Review records are not rewritten.

- `portfolio_get` (`exposure`, `coverage`, `performance_summary`, `performance_series`,
  `daily_equity`, `trade_cycles`, `trade_cycle_override_preview`, `journal_timeline`,
  `behavior_summary`, `behavior_review_history`, `unlinked_activity`, `simulate_addition`,
  `retro_history`)
- `research_get` (`challenge_review`) restores a Challenge Review
- `research_judgment_propose` (`challenge_review`) starts one; explicit resolution
  uses `research_judgment_confirm` (`challenge_review`)
- `broker_order_manage` — calculate the SGOV Shadow Preview, or preview/submit/read/
  cancel one exact Schwab US stock/ETF order through the expiring current-chat
  confirmation contract; no generic broker request or replacement operation
`portfolio_get/trade_cycle_override_preview` and
`research_memory_append/trade_cycle_override` use `override_operation` for the
Cycle action and reserve `operation` for MCP dispatch. The adapter maps the field
back to the existing domain input. Writes still require explicit confirmation,
idempotency, and the existing expected-version checks; Agent action permissions
are unchanged.

- `research_workflow_run` (`deep_dive`, `catalyst_review`,
  `a_share_market_review`, `us_market_review`, `portfolio_review`, `peer_comparison`,
  `trade_retro`)

QuantConnect/LEAN code generation, backtesting, and result import are removed.
The public schema is `mcp-vnext-shadow-v12`; `research_workflow_run` rejects the
retired `historical_validation_prepare` and `historical_validation_import` operations.
Existing private artifacts are historical files only, not a runtime capability.

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

Phase 4B `portfolio_get/trade_cycles` is a rebuildable, durable-only,
long-only projection over normalized Account Transactions. It groups by exact
account, Instrument, and native currency; zero-to-buy opens, later buys add,
sells reduce, zero closes, and a later buy starts a new Cycle with a re-entry
reference. It never contacts a broker, creates an order, infers short exposure,
or treats transfers/corporate actions as trades. Missing prices/fees,
sell-without-open, oversells, incomplete coverage, and bounded results remain
explicit. Fill count is not a trade-win denominator; a complete CLOSED Cycle is
the unit. SGOV Cycles are deterministically `CASH_MANAGEMENT`; other Cycles remain
`UNCLASSIFIED` until a later explicit Strategy/Plan annotation exists.
`portfolio_get/performance_series` derives native-currency TWR, actual-timestamp
MWR/XIRR, and maximum drawdown only from durable Broker net-assets snapshots and
external-flow activities. Missing valuation boundaries, mixed currencies, or a
non-unique XIRR remain unavailable. `portfolio_get/behavior_summary` has no
aggregate score and retains numerator, denominator, exclusions, and exact refs.
`portfolio_get/unlinked_activity` lists unmatched Broker trades. An explicit
`research_memory_append/activity_annotation` revision may link one exact activity
to an existing same-Subject Decision/Plan or classify it truthfully; it never edits
the Broker fact or authorizes an order.
`research_memory_append/trade_cycle_override` appends a user-confirmed split/merge/
relink revision only after `portfolio_get/trade_cycle_override_preview`; the
algorithm projection remains retained and affected metrics fail closed until they
can be recomputed. `research_memory_append/behavior_review` records one exact
weekly/monthly/quarterly cohort and derives NEW/PERSISTENT/RESOLVED/RECURRED only
from complete durable action-source reads. `portfolio_get/journal_timeline`
merges durable Decisions, linked order intents/results, and Broker activities.

The Console-only durable Review Queue materializes Catalyst overdue items, open Trade
Retro reviews and action items, consecutive Judgment Scorecard gaps, and unresolved
Agent/Broker states without adding a public MCP tool. Each ReviewItem retains a stable
source key, first/last seen time, recurrence count, optional due time, status, and an
optional resolution reference. Human acknowledge/resolve transitions require the
Console session, expected version, idempotency key, actor, and authorization note;
resolution requires a bounded note. A successfully observed source disappearance may
auto-resolve an item. A failed or unavailable source read must never auto-resolve one.
If a closed source condition disappears and later recurs, the same item reopens with a
higher occurrence count. The Journal Reviews workflow consumes this queue while the existing
Research, Monitor, Agenda, Retro, and Scorecard pages remain intact.
Each occurrence also retains its own opened/last-seen, first-acknowledged, and closure
timestamps plus MANUAL/AUTO resolution mode. Queue metrics must use occurrence history,
not lifetime first_seen timestamps or a paginated item list; zero-sample medians/rates
remain null. Journal may acknowledge, adjust a due time, or resolve an exact
ReviewItem through the same Console session/version/idempotency gate.

**Scheduled operational CLI (not a public MCP tool)**

- `uv run trading-partner-post-market-sync` checks the XNYS calendar and runs ten
  minutes after the real session close. It refreshes all configured account
  providers, synchronizes normalized transactions and source-referenced Daily Equity,
  and then performs the exact
  active-source Watchlist sync followed by one `MOOMOO_NOTE` Observation sync with
  `analyze=true` for newly eligible FULL revisions. Deterministic USER-block comparison
  materializes review only when normalized USER text changed; duplicate, summary-only,
  external-speaker-only, and whitespace-only changes do not enqueue work. A model
  `NO_MATERIAL_CHANGE` label cannot suppress a real USER text change. Observation
  counts/status share the same durable receipt; Cookie,
  Provider, or note failures remain visible without blocking completed account,
  transaction, Watchlist, or notification work. It persists one terminal
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
  current-chat confirmation gate. The automatic preparation phase writes only durable
  state; the completion phase emits one concise SYSTEM Outbox result notification and
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
Every emitted Monitor transition notification also ends with a read-only model
analysis. Multiple transitions for one Monitor in one run share one analysis; an
existing successful composite judgment is reused instead of making a second call.
Otherwise the configured Monitor model receives only bounded event/rule facts,
uses `max` effort, and returns at most 160 Chinese characters. The call is capped
at 80 seconds. Failure appends an explicit unavailable sentence and never blocks,
changes, or suppresses the deterministic event. Post-market digests collect the
bounded analyses in one final section and make no model call when nothing changed.
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
- `research_get(request={"operation":"state",...})` returns the current Trade Plan and history.
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
is a separate public-feed Provider under `us_get_facts(request={"operation":"sentiment",...})`;
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
machine-readable `COMPLETE`/`INCOMPLETE` status. `portfolio_get/coverage` is a
durable-only read; it never refreshes a broker or computes P/L.
`portfolio_get/performance_summary` deterministically reconstructs native-currency
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
before a server-side LLM call. The owner-configured primary Monitor, event analysis,
Trade Retro, and Console Agent default use OpenCode Go `deepseek-flash` at `max`.
The independently configured Monitor failure fallback remains in place. Legacy
Bailian configuration retains `deepseek-v4-flash-0731` through Chat Completions
and its separately configured fallback; Agent model defaults remain separate.
DeepSeek and OpenCode Go subscription endpoints can also be selected through
`LLM_PROVIDER=deepseek|opencode_go`. OpenCode Go routes models through the documented
Responses, Chat Completions, or Messages protocol and never reads OpenCode's local
`auth.json`; its API key stays in the Trading Partner secret configuration. The Go
adapter sends the required opaque stable `x-opencode-session` on model calls, streams,
and directory requests; the independent Zen adapter does not inherit that Go-specific
header. Go Chat Completions and Responses requests omit client output-token limits,
including per-service budgets, so reasoning cannot exhaust a small local cap before
producing tool arguments. This covers complete, stream, retry and structure repair.
Provider limits, request timeouts and strict output/domain validation still apply.
Messages retains that protocol's required max_tokens; Zen and other Providers retain
their independent output budgets.
Only the Bailian adapter may use bounded web search for current macro-event context; search usage and
up to ten source URLs are persisted, while prices, positions, levels, returns, and
quantity facts remain deterministic-only. Explanations are validated as Chinese.
OpenCode Go has no native Web Search capability in this integration and must not be
described as having searched the web.
The LLM has no mutation/order port. Event analysis is notification-only and is not
a judgment, durable rule fact, confirmation, or execution authorization;
evidence IDs and quantity ranges are validated, session-misaligned divergence
actions are downgraded to WAIT, unchanged qualitative signatures skip calls, and
only material judgment changes create events/notifications. HOLD/WATCH/WAIT changes
with the same phase, WATCH urgency, no divergence, and zero quantity are one neutral
state and do not notify repeatedly. Consecutive model failures remain one interruption
even when their typed error codes differ. Weekend-proxy judgments keep the requested
XAUUSD Monitor identity but the title and market state must name PAXG/USDC or IG
Weekend Gold explicitly and must not call the proxy a fresh OTC/LBMA quote. After primary failover,
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
`tp_technical_v3` additionally embeds independent deterministic `tp_smc_v1` output:
confirmed internal/swing HH/HL/LH/LL, close-through BOS/CHoCH, Order Blocks, FVGs,
EQH/EQL liquidity, and premium/equilibrium/discount zones. Preserve occurrence and
confirmation times and zone status. The compatibility profile uses LuxAlgo's
published default lengths and thresholds; describe it as core calculation
compatibility on identical OHLC, not TradingView pixel parity or cross-provider bar
equality. Do not claim institutional order knowledge, historical validation, Breaker
Blocks, displacement, inducement, killzones, or automated signals.

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
