# Trading Partner — Agent Guide

## Product intent

Trading Partner is a long-horizon investment judgment companion. Codex (or another
agent host) talks to the user; Trading Partner MCP supplies facts, research state,
and structured tools. The implemented Phase 1–3D boundary covers A-share/US research,
Korea Exchange quote/technical monitoring,
accounts, Investment Cases, Watchlist Hub, Risk v2, Monitoring v2, versioned Trade
Plans, deterministic Position Sizing, and professional daily/weekly technical
analysis, plus a manual QuantConnect Free code/result bridge — **not** an automated
backtest runner or live order writes.

## Implemented boundary

The sole public MCP surface is exactly **28** tools (`compact_28`). Grouped tools
accept one required `request` object
whose closed `operation` union rejects fields from other operations. Application
services remain separate; compact routing belongs only to `interfaces/mcp/`.

**System and identity**

- `system_health` — health plus `mcp_surface_profile`, `public_tool_count`, and
  `surface_schema_version`.
- `instrument_resolve` — local-first lookup; a unique provider result may be cached.

Instrument resolution is local-first, not local-only. A local miss may use the
configured US/A-share/KR instrument directories; only one validated candidate is
atomically cached in the Instrument Master. The Master is a registry/cache, not
an allowlist. Directory failures remain typed provider errors.

**Research files, judgment, and memory**

- `investment_case_read` (`query`, `context`)
- `investment_case_manage` (`create`, `archive`)
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
  broker/SWFX `commodity_spot:OTC:XAUUSD`, `XAGUSD`, and the separately labelled
  rolling copper CFD. None may be relabelled as LBMA/LME benchmark data.
- Dukascopy follows the current keyless `dukascopy-node` Jetta strategy: minute/
  hour/day data use UTC day/month/year buckets, up to 10 requests run per batch,
  and batches pause for one second. Completed buckets are cached; active `from`
  buckets are not. `DUKASCOPY_API_KEY` is legacy-fallback-only.
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

- `account_get` (`positions`, `transactions`) — durable only; never contacts brokers
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

Compact workflows never accept hidden Case creation or account refresh. Create a
Case first with `investment_case_manage(request={"operation":"create",...})`;
refresh accounts first with `external_state_sync(request={"operation":"accounts"})`.
Peer Comparison accepts one primary and 1–5 caller-specified same-market A-share/US
equity peers. It aligns normalized statements and optional current valuation facts,
does not discover/rank peers, and never mutates a Case, Thesis, Trade Plan, or account.

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
Optional Telegram delivery uses a durable Outbox linked to either an event or a
market-close run. Event alerts remain transition-only, while every evaluated
A-share/US/KR post-market group emits one consolidated run summary even when no state
changes; INTERVAL runs remain transition-only. Source and Outbox are committed
atomically, retry is bounded, and expired messages are not delivered late.
`trading-partner-monitor-notifications` provides
secret-safe `status`, `test`, and `flush` operations without adding an MCP tool.
Messages reuse the same run observations to include current price/time and every
rule's condition, value, distance, severity, and state. Multiple same-Monitor
transitions in one run are delivered as one Telegram message without collapsing
their durable Monitor events. Telegram does not support responsive tables, so the
sender places symbol/current price in the first line, followed by the transition
summary and mobile-first vertical rule cards. It does not generate or upload an
image.

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
historical `as_of` remains cutoff-safe and Yahoo is not presented as complete
overnight equity coverage. Phase 1G combines current
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
Phase 1J restores one current durable research file (`InvestmentCase`)
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
Draft case creation is a research-folder write, not long-term tracking, Thesis confirmation,
or trading authority. Catalyst Review does not auto-create a Case.
For A-share Deep Dive, `industry_cycle="hog"` explicitly adds the compact national
hog-cycle package and, for equities, the company operating-metrics package. The
workflow never infers an industry cycle from an instrument or company name.

Phase 2 selects exactly one active watchlist upstream (`MOOMOO` or `MANUAL_CSV`).
The database persists complete group/membership lifecycle history and mutation
receipts. Reads are durable-first by default, may refresh only when explicitly
requested, and fall back to stale durable state with a
typed warning. Adds/removes require an allowed confirmer and idempotency key;
external deletion never deletes Phase 1 Research WatchlistItems or Investment
Cases. Unsupported provider codes stay visible without fabricated instruments.
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
