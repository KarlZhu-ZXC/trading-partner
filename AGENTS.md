# Trading Partner — Agent Guide

## Product intent

Trading Partner is a long-horizon investment judgment companion. Codex (or another
agent host) talks to the user; Trading Partner MCP supplies facts, research state,
and structured tools. The current Phase 1–2 boundary covers A-share/US research,
accounts, Investment Cases, Watchlist Hub, Risk, Monitoring, and professional
daily/weekly technical analysis — **not** backtests, paper trading, or live order writes.

## Implemented boundary

The public MCP surface is exactly **52** tools. Related read operations are grouped
behind closed `operation` enums; the underlying application services remain separate.

**Phase 1A**

- `system_health` (includes `components.research_search` FTS probe when wired)

**Phase 1B research state**

- `investment_case_create`
- `investment_case_query` (`case_id` for one Case; otherwise filtered list)
- `investment_case_archive`
- `research_state_get`
- `research_state_update`
- `thesis_revision_propose`
- `thesis_revision_confirm`
- `thesis_history_get`

**Phase 1D**

- `instrument_resolve`

Instrument resolution is local-first, not local-only. A local miss may use the
configured US/A-share instrument directories; only one validated candidate is
atomically cached in the Instrument Master. The Master is a registry/cache, not
an allowlist. Directory failures remain typed provider errors.

**Phase 1C research memory**

- `research_search`
- `research_report_get`
- `research_timeline_get`
- `journal_append`
- `decision_record_append`

**Phase 1E A-share facts**

- `a_share_get_facts` (`snapshot`, `market_structure`, `capital`, `limit_up`,
  `sentiment`, or `etf_option`)
- `research_search_reports`

**Phase 1F US market facts**

- `us_get_market` (`quote` or `composite`)
- `market_get_bars`
- `market_get_context` (SPY/QQQ/IWM, best-effort Yahoo breadth/sector rotation,
  and optional Moomoo OpenD US community-attention Hot List)
- `technical_get_snapshot`

**Phase 3A commodity futures facts**

- The existing `instrument_resolve`, `us_get_market`, `market_get_bars`,
  `technical_get_snapshot`, and `technical_render_chart` tools support Yahoo
  continuous futures `GC=F`, `MGC=F`, `SI=F`, `HG=F`, `PL=F`, and `PA=F` through
  `future:US:*` IDs. Futures are unadjusted and always disclose non-spot and roll risk.

**Phase 1G US research facts**

- `us_get_fundamentals` (`snapshot` or `statements`)
- `us_get_company_research` (`filings`, `insider_activity`, `company_updates`,
  or `events`)

**Phase 1H US context facts**

- `market_get_live_news`
- `us_get_macro_context`
- `us_get_sentiment_snapshot`
- `us_get_prediction_market_context`

`us_get_sentiment_snapshot` keeps StockTwits user labels, Reddit inference, and
Moomoo public-feed inference source-separated. The Moomoo path is deterministic:
it performs exact-symbol relevance filtering, HTML cleanup, deduplication,
low-quality filtering, and versioned bilingual rule classification. It never
invokes a Skill or an LLM; Codex or another external host interprets the returned
samples and summaries. The feed is current-only and missing engagement remains
null rather than inferred.

**Phase 1I read-only accounts and portfolio**

- `account_get` (`positions`, explicit `refresh`, or `transactions`)
- `portfolio_analyze`
- `portfolio_simulate_addition`

**Scheduled operational CLI (not a public MCP tool)**

- `uv run trading-partner-post-market-sync` checks the XNYS calendar and runs ten
  minutes after the real session close. It refreshes all configured account
  providers before the exact active-source Watchlist sync, persists one terminal
  receipt per market session, and never executes an order.

**Phase 1J durable context**

- `research_context_build`

**Phase 1K Challenge Review**

- `challenge_review_start`
- `challenge_review_get`
- `challenge_review_resolve`

**Phase 1L workflows**

- `research_run_deep_dive`
- `research_run_catalyst_review`
- `a_share_run_market_review`
- `us_run_market_review`
- `portfolio_run_review`

**Phase 2 watchlist hub**

- `watchlist_get` (`groups` or `items`)
- `watchlist_add`
- `watchlist_remove`

**Phase 2B Portfolio Risk Engine v1**

- `risk_policy_get`
- `risk_policy_update`
- `risk_check`

**Phase 2C Monitoring v1**

- `monitor_create`
- `monitor_query` (`monitor_id` for one Monitor; otherwise filtered list)
- `monitor_update`
- `monitor_evaluate`
- `monitor_event_list`
- `monitor_event_resolve`

**Phase 2D Technical Engine v2**

- `technical_render_chart`

Phase 2D also upgrades the existing `technical_get_snapshot` from the Phase 1F
US-only v1 calculation to one shared A-share/US daily-and-weekly engine.

Do **not** invent quotes or account balances. Phase 1E A-share tools are provider-backed and
must preserve envelope source/freshness/warning semantics. Phase 1F US tools are
provider-backed with Yahoo→Alpha Vantage routing. US breadth uses cached Yahoo
Screener totals over a disclosed listed-security universe that may include ETFs
and ADRs; sector rotation uses versioned Yahoo sector-index symbols. Neither is
presented as official exchange common-stock breadth, and unavailable high/low or
moving-average participation is never fabricated. Phase 1G combines current
Yahoo/Alpha facts with separately based SEC reported facts and preserves filing
visibility cutoffs. Phase 1H adds dated news,
vintage-safe FRED observations, source-separated social sentiment, and
current-only prediction-market probabilities. Phase 1I account ports read Schwab
through a project-owned `schwab-py` OAuth token, Moomoo OpenD, or a strict manual
CSV; persist account snapshots; and compute deterministic gross portfolio exposure
without implicit FX conversion. The Schwab adapter exposes only balances,
positions, and transactions — no order method or plugin CLI runtime dependency.
Moomoo Hot List is an optional `market_get_context` component, not directional
sentiment. It uses the shared cross-process OpenD limiter, is cached in 15-minute
buckets, and requires OpenD 10.9 or newer. Older versions remain a typed
`MOOMOO_OPEND_VERSION_UNSUPPORTED` degradation. Moomoo discussion-post retrieval
is a separate public-feed Provider under `us_get_sentiment_snapshot`; it never
uses OpenD, a Skill, or an LLM at runtime.
Ordinary holdings, portfolio, and risk questions read the latest durable account
snapshots. Broker refresh is explicit: only `account_get(operation="refresh")`, or a workflow
called with `refresh_accounts=true` after an explicit user request, may fetch and
persist new account facts. Snapshot staleness is disclosed, not an implicit trigger.
Phase 1J restores one current durable Investment Case context with contrary-first
evidence, explicit budget truncation, and optional latest portfolio positions.
Phase 1K bypasses ordinary discussion but persists material strict reviews with a
versioned ten-dimension checklist and explicit non-executing user resolution.
Phase 1L returns actual fact packages for five workflows while Codex remains the
synthesizer. Workflow receipts/reports and normalized historical transactions are
durable; workflow outputs never execute or directly mutate a Thesis. An
instrument-only `research_run_deep_dive` creates or reuses one non-archived Draft
Investment Case by default; `create_case=false` preserves ad-hoc mode. Draft case
creation is a research-folder write, not long-term tracking, Thesis confirmation,
or trading authority. Catalyst Review does not auto-create a Case.

Phase 2 selects exactly one active watchlist upstream (`MOOMOO` or `MANUAL_CSV`).
The database persists complete group/membership lifecycle history and mutation
receipts. Reads may refresh explicitly and fall back to stale durable state with a
typed warning. Adds/removes require an allowed confirmer and idempotency key;
external deletion never deletes Phase 1 Research WatchlistItems or Investment
Cases. Unsupported provider codes stay visible without fabricated instruments.

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
V1 supports A-share/US `PRICE_ABOVE`/`PRICE_BELOW` rules and a portfolio
`RISK_OVERALL_AT_LEAST` rule. Rule states are `QUIET`, `TRIGGERED`, or
`NOT_EVALUATED`; durable events are emitted only on state transitions, so repeated
unchanged facts do not create duplicate alerts. Provider failures and stale facts
remain `NOT_EVALUATED`. Event acknowledgement/resolution never mutates a Thesis,
position, Risk Policy, or order, and every run carries `execution_effect=false`.

Phase 2D derives standard indicators through the open-source TA-Lib backend and
project-owned structure analysis over provider-backed adjusted daily bars. Phase 3A
adds explicitly unadjusted Yahoo continuous-futures bars. It supports A-share and US
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
Withdraw. Codex may propose changes, but may not confirm or reject them.
Journal and Decision append require explicit `user` or `external_agent`
confirmation. Decision records are research/position **intent** only — never
orders, fills, or positions.

## Architecture rules

1. Domain never imports MCP, SQLAlchemy, Alembic, Pydantic Settings, or providers.
2. Application never imports infrastructure or interfaces.
3. Interfaces only adapt protocols / validate inputs / convert to DTOs.
4. Only `src/bootstrap.py` wires application + infrastructure.
5. Provider raw payloads never cross the infrastructure boundary.
6. Precise numbers come from tool snapshots with source, time, freshness, and basis.

## Source layout

```text
src/
├── bootstrap.py
├── application/
├── domain/
├── infrastructure/
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
strategies, backtest, paper, execution, orders, fills
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
