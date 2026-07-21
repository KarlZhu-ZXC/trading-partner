---
name: trading-partner
description: Use Trading Partner MCP for investment research facts, health checks, Investment Cases, thesis candidates, instrument resolve, A-share and US provider facts, professional technical analysis, accounts, portfolio exposure, risk and monitoring, durable context restore, Challenge Review, research workflows, and research memory. The public surface is exactly 52 tools.
---

# Trading Partner Skill

## When to use

- User asks about portfolio research, Investment Cases, market facts, or judgment continuity.
- You need **verified** market or system facts from tools rather than model memory.
- You need to search historical evidence, reports, events, decisions, or journals.
- You need to check whether the Trading Partner backend is healthy.

## Public tools (exactly 52)

### Health

#### `system_health`

No input. Returns a Tool Envelope with `HealthStatusDTO`:

- Overall and database health states: `ok` | `degraded` | `error`
- `components.research_search`: FTS backend probe (`ok` | `degraded`)
- App name, version, environment

Even when the database or search backend is unhealthy, `ok` on the envelope remains
`true` so diagnostics remain available; expect `degraded=true` and warnings such as
`DATABASE_HEALTH_ERROR` or `SEARCH_BACKEND_UNAVAILABLE`.

### Investment Case / thesis (Phase 1B)

- `investment_case_create` / `investment_case_query` / `investment_case_archive`
- `research_state_get` / `research_state_update`
- `thesis_revision_propose` / `thesis_revision_confirm` / `thesis_history_get`

Research-state and thesis changes use Candidate Propose → Confirm / Reject /
Withdraw. Codex may propose, but must not confirm or reject.

### Instrument resolve (Phase 1D)

- `instrument_resolve` — local-first instrument registry lookup. On a local miss,
  it discovers through Yahoo → Alpha Vantage for US instruments or validates an
  A-share code through Tencent, then atomically caches one unambiguous candidate.
  It is still not a live quote. Preserve provider failures instead of relabeling
  them as `INVALID_INSTRUMENT`.

### Research memory (Phase 1C)

| Tool | Purpose |
|---|---|
| `research_search` | Full-text + structured search over evidence/report/event/decision/journal |
| `research_report_get` | Read one immutable report by `report_<uuid7>` |
| `research_timeline_get` | Unified case timeline |
| `journal_append` | Append journal only after explicit user confirmation |
| `decision_record_append` | Record research/position **intent**; never orders/fills |

**Do not call** (not registered): `evidence_create`, `evidence_update`,
`report_create`, `event_create`, `decision_update`, `journal_update`,
`journal_delete`.

### A-share provider facts (Phase 1E)

| Tool | Purpose |
|---|---|
| `a_share_get_facts` | `snapshot`, `market_structure`, `capital`, `limit_up`, `sentiment`, or `etf_option` facts |
| `research_search_reports` | Provider report/consensus search; does not archive reports |

These tools may legally return degraded envelopes when a fallback, delayed,
stale, non-authoritative, derived, or low/unknown-reliability component is used.
Preserve their warnings and source timestamps when answering the user.

### US market facts (Phase 1F)

| Tool | Purpose |
|---|---|
| `us_get_market` | Provider-backed US `quote` or `composite` snapshot |
| `market_get_bars` | Inclusive-end US equity/index and commodity-futures OHLCV with asset-aware adjustment |
| `market_get_context` | SPY/QQQ/IWM, best-effort Yahoo breadth/11-sector rotation, and optional Moomoo OpenD US community-attention Hot List; unavailable components stay explicit |
| `technical_get_snapshot` | Cross-market daily/weekly indicators, regimes, structure levels, and recent patterns |
| `technical_render_chart` | Auditable envelope plus an in-memory PNG candlestick/volume/RSI chart |

Technicals are deterministic derived facts, not backtested predictions. Both tools
support A-share and US equity/ETF/index instruments. Preserve
`historically_validated=false`, adjustment basis, source warnings, and stale-data failures.
Yahoo breadth uses a disclosed listed-security universe that may include ETFs and
ADRs; never describe it as official exchange common-stock breadth. New-high/low
and moving-average participation remain unavailable rather than fabricated.
Moomoo Hot List is an attention ranking, not Bullish/Bearish sentiment. Preserve
its trade/search/news heat basis and the `MOOMOO_OPEND_VERSION_UNSUPPORTED`
warning when the local OpenD predates 10.9.

Phase 3A also supports Yahoo continuous futures `GC=F`, `MGC=F`, `SI=F`, `HG=F`,
`PL=F`, and `PA=F` under `future:US:*` IDs. Futures default to unadjusted bars and
must preserve `FUTURES_CONTRACT_NOT_SPOT` and `CONTINUOUS_FUTURES_ROLL_RISK`.
Never call GC/SI spot XAUUSD/XAGUSD, or call HG London/LME copper.

### US research and context facts (Phase 1G–1H)

| Tool | Purpose |
|---|---|
| `us_get_fundamentals` | Current snapshot or normalized statements |
| `us_get_company_research` | Filings, insider activity, company updates, or typed events |
| `market_get_live_news` | Dated company/global news with publication cutoff |
| `us_get_macro_context` | FRED observations with requested ALFRED vintage cutoff |
| `us_get_sentiment_snapshot` | Source-separated StockTwits labels, Reddit inference, and deterministic Moomoo feed mining |
| `us_get_prediction_market_context` | Current-only open Polymarket probabilities |

Do not relabel current Polymarket odds as historical. Keep StockTwits user labels,
versioned Reddit inference, and versioned Moomoo deterministic inference separate.
Moomoo samples are current-only, exact-symbol filtered, and may have nullable
engagement. The MCP runtime only cleans, filters, deduplicates, and classifies with
fixed rules; interpretation and narrative synthesis remain the host's responsibility.

### Read-only accounts and portfolio (Phase 1I)

| Tool | Purpose |
|---|---|
| `account_get` | Read durable positions, explicitly refresh, or fetch historical transactions |
| `portfolio_analyze` | Compute native-currency gross market/currency/instrument exposure |
| `portfolio_simulate_addition` | Pure before/after hypothetical addition; never executes |

For ordinary holdings, exposure, portfolio-review, and risk questions, read the
latest durable snapshots first. Do **not** call `account_get(operation="refresh")` and do not
set `refresh_accounts=true` merely because the user says “current”, “my holdings”,
or asks a portfolio question. Refresh brokers only when the user explicitly asks
to refresh/sync/fetch from the broker, or when no durable snapshot exists; disclose
the refresh before doing it. A stale durable snapshot should still be returned with
its timestamp and warnings rather than silently causing a broker refresh.

Schwab and Moomoo account/transaction identifiers are redacted stable hashes. A missing price
timestamp remains missing. Never sum currencies through an assumed FX rate, and
never describe gross invested position value as account NAV.

Schwab uses a project-owned rotating `schwab-py` OAuth token and encrypted account
hash allowlist. Its adapter reads balances, positions, and at most the documented
60-day transaction window. It does not ingest open orders and emits an explicit
warning; it has no place/replace/cancel surface and never reuses the schwab-trader
plugin token.

### Durable context restore (Phase 1J)

- `research_context_build` selects one Case by `case_id` or an unambiguous primary
  `instrument_id`, then returns current research state, contrary-first evidence,
  compact history, latest durable positions, missing facts, and budget metadata.

Use its `live_fact_tools_required` hints to fetch current facts separately. Never
interpret a Context Builder result as a fresh market-data call, and never hide
invalidation conditions or contrary evidence when summarizing it.

### Challenge Review (Phase 1K)

| Tool | Purpose |
|---|---|
| `challenge_review_start` | Bypass ordinary discussion or persist a material strict review |
| `challenge_review_get` | Restore one persisted review with ten questions and findings |
| `challenge_review_resolve` | Record an explicit accept/revise/reject/defer resolution |

Challenge Review never executes a trade and never mutates a Thesis, candidate, or
position directly. Only `user` or an explicitly authorized `external_agent` may
resolve a review.

### Research workflows and transactions (Phase 1L)

| Tool | Purpose |
|---|---|
| `research_run_deep_dive` | Gather a cross-market Deep Equity Research fact package; an instrument-only call creates/reuses one Draft Case by default (`create_case=false` keeps ad-hoc mode) |
| `research_run_catalyst_review` | Gather dated catalyst, reaction, and expectation facts |
| `a_share_run_market_review` | Gather A-share board, industry, limit, capital, and heat facts |
| `us_run_market_review` | Gather US index, macro, news, and portfolio-impact facts |
| `portfolio_run_review` | Gather positions, transactions, exposure, industry/theme, correlation, and beta |

Workflow `synthesis_contract` tells the host which bull/bear/risk/portfolio-fit
sections to cover. Codex synthesizes; the backend does not run a second LLM.
Preserve partial/degraded step receipts, and never turn descriptive correlation or
beta into a forecast, backtest, order, or sizing instruction.

A Deep Dive Draft Case is a durable research folder, not an active tracking decision
and not a confirmed Thesis. Catalyst Review does not auto-create a Case; pass the
Deep Dive `case_id` to continue the same judgment history. When multiple open Cases
match one instrument, require an explicit `case_id` instead of guessing.

### Watchlist hub (Phase 2)

- `watchlist_get(operation="groups"|"items")` reads the durable database and may
  explicitly refresh the single configured Moomoo or Manual CSV upstream.
- `watchlist_add` / `watchlist_remove` require `user` or authorized
  `external_agent` confirmation plus an idempotency key.

Moomoo and Manual CSV are alternatives, not merged or reconciled sources. External
removal keeps inactive membership history and never deletes a research
`WatchlistItem` or Investment Case. Unsupported Moomoo codes remain visible with
`research_supported=false`; never fabricate an A-share/US instrument for them.

For external post-market scheduling, `uv run trading-partner-post-market-sync`
refreshes all configured durable account snapshots before the exact Watchlist
full sync. It is due ten minutes after the XNYS session close, including early
closes; it is an operational CLI, not an MCP tool or order surface.

### Portfolio Risk Engine (Phase 2B)

| Tool | Purpose |
|---|---|
| `risk_policy_get` | Read the current append-only policy version and disclose an unconfirmed system default |
| `risk_policy_update` | Append a confirmed version with optimistic version and idempotency checks |
| `risk_check` | Evaluate durable or explicitly refreshed accounts and an optional hypothetical addition without execution |

Preserve every rule status (`PASS`, `WARN`, `BREACH`, `NOT_EVALUATED`) and the
overall `PASS`, `WARN`, `BREACH`, or `INCOMPLETE`. Never convert missing NAV,
price timestamps, or cross-currency FX facts into a pass. V1 checks account/price
age, position concentration within currency, gross exposure/NAV only on a common
currency basis, cash, margin, and duplicate instruments across accounts. A default
policy emits `RISK_POLICY_DEFAULT_UNCONFIRMED`; `execution_effect` is always false.

### Monitoring (Phase 2C)

| Tool | Purpose |
|---|---|
| `monitor_create` | Create one confirmed versioned monitor with closed rule types |
| `monitor_query` | Restore one definition or list current definitions and latest rule states |
| `monitor_update` | Append a confirmed version, including pause/archive changes |
| `monitor_evaluate` | Evaluate active rules and persist only state transitions |
| `monitor_event_list` | Read durable trigger/recovery/not-evaluated events |
| `monitor_event_resolve` | Acknowledge or resolve an event with confirmation/idempotency |

V1 rule types are A-share/US `PRICE_ABOVE`, `PRICE_BELOW`, and portfolio
`RISK_OVERALL_AT_LEAST`. Treat stale or unavailable facts as `NOT_EVALUATED`, not
quiet. Repeated unchanged conditions do not create another event; a later recovery
does. Monitoring never changes a Thesis, policy, position, or order. The external
`uv run trading-partner-monitor-run --cadence US_POST_MARKET` (or
`A_SHARE_POST_MARKET`) evaluates only active monitors for that market cadence and
is not a scheduler itself.

### Technical Engine v2 (Phase 2D)

- `technical_get_snapshot` returns shared A-share/US `1d` and `1w` facts using
  provider-backed adjusted equity bars or unadjusted futures bars, TA-Lib standard
  indicators, project-owned
  structure clustering, and recent candlestick recognition.
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

Spot-metals providers, additional brokers, automated evidence ingestion, runtime
LLM synthesis, backtest, paper trading, and order execution remain out
of scope. Do not call tools that are not registered.
