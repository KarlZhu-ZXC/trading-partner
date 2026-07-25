# Trading Partner Phase 1

> Status: completed on 2026-07-18  
> Product surface: 54 public MCP tools  
> Migration head at closeout: `0008_phase1l_workflows`  
> Markets: A-share and US  
> Interaction surface: Codex conversation

## 1. Outcome

Phase 1 delivers a long-horizon investment judgment companion. Codex is the only
conversational interface; Trading Partner MCP supplies verified facts, durable
research state, account context, historical memory, and structured workflow fact
packages.

The product can:

- restore a research file (normally an instrument-centered Investment Case) and its evolving investment
  judgments (Theses) across Codex tasks;
- research A-share and US instruments with source/time/freshness metadata;
- read Moomoo, Schwab, or strict manual-CSV accounts without order permissions;
- analyze gross portfolio exposure and descriptive correlation/beta;
- preserve reports, events, decisions, journals, and challenge reviews;
- run deep-dive, catalyst, market, and portfolio research workflows.

Phase 1 cannot backtest, paper trade, submit/cancel orders, or run autonomous
monitoring. Those capabilities belong to later phases.

## 2. Phase map

| Slice | Delivered capability |
|---|---|
| 1A | Python/MCP skeleton, health, mock snapshots, architecture boundaries |
| 1B | Investment Case, Thesis revisions, assumptions, invalidations, questions, research WatchlistItem |
| 1C | Immutable research memory, timeline, journal, decisions, full-text search |
| 1D | Instrument Master, local-first dynamic resolution, provider router/cache/rate limits |
| 1E | A-share quote, structure, capital, limit-up, sentiment, reports, ETF options |
| 1F | US quote, bars, context, deterministic technical indicators |
| 1G | US fundamentals, statements, SEC filings, insiders, corporate events |
| 1H | News, FRED/ALFRED macro, Reddit/Moomoo sentiment, dormant StockTwits compatibility, Polymarket context |
| 1I | Read-only Moomoo/manual-CSV accounts and deterministic portfolio exposure |
| 1J | Durable cross-task research Context Builder |
| 1K | Persistent ten-dimension Challenge Review and explicit user resolution |
| 1L | Historical transactions and five provider-backed research workflows |
| 1M | Dialogue/longitudinal evaluation, backup/restore, delivery audit |

Schwab read-only balances, positions, and transactions were added after the main
closeout without changing the tool count or introducing order methods.

## 3. Public MCP boundary

The authoritative Phase 1 runtime inventory is exposed through the consolidated
public façade documented in `AGENTS.md`; the full Phase 1–2 surface is 52 tools.

### System and research state

```text
system_health
investment_case_create
investment_case_query
investment_case_archive
research_state_get
research_state_update
thesis_revision_propose
thesis_revision_confirm
thesis_history_get
instrument_resolve
research_search
research_report_get
research_timeline_get
journal_append
decision_record_append
```

### A-share facts

```text
a_share_get_facts
research_search_reports
```

### US facts and context

```text
us_get_market
market_get_bars
market_get_context
technical_get_snapshot
us_get_fundamentals
us_get_company_research
market_get_live_news
us_get_macro_context
us_get_sentiment_snapshot
us_get_prediction_market_context
```

`market_get_context` 在原有 SPY/QQQ/IWM 代理基础上，使用 yfinance Screener 的聚合
`total` 提供上涨、下跌和平盘家数，并使用 Yahoo 的 11 个美国板块指数生成 1/5/20 个交易日
收益及相对 SPY 的 20 日表现。它不会拉取或保存全市场日线。该口径是 Yahoo 美国上市证券池，
可能包含 ETF 与 ADR，不等同交易所官方普通股 breadth；请求按 15 分钟桶进入持久化 Provider
缓存。Yahoo 失败不影响指数代理，缺失字段分别附带 `US_BREADTH_UNAVAILABLE` 或
`US_SECTOR_ROTATION_UNAVAILABLE`。

同一工具还可选择性读取 Moomoo OpenD 美股 Hot List，保留交易、搜索、新闻和综合热度，
但明确将其定义为 community attention，而非方向性 sentiment。请求复用全项目 OpenD
限流器并按 15 分钟桶缓存；OpenD 低于 10.9 时返回
`MOOMOO_OPEND_VERSION_UNSUPPORTED`，其余市场上下文仍可用。

### Accounts, portfolio, durable context, and challenge

```text
account_get
portfolio_analyze
portfolio_simulate_addition
research_context_build
challenge_review_start
challenge_review_get
challenge_review_resolve
```

### Transactions and workflows

```text
research_run_deep_dive
research_run_catalyst_review
a_share_run_market_review
us_run_market_review
portfolio_run_review
```

Detailed input/output and degradation semantics live in the
[MCP capability guide](../guide/mcp-capability-boundary.md). Agent-facing hard
constraints live in [AGENTS.md](../../AGENTS.md).

## 4. Research model

The user-facing concept is a **research file**. For the primary company/catalyst
flow, `Instrument` is the objective security identity and `InvestmentCase` is the
durable, subjective research file opened around that Instrument; `Thesis` is one
current, falsifiable investment judgment inside the file. Theme, macro, and
portfolio-concern Cases may instead span instruments or have no primary Instrument.
A research file owns or links its current judgments,
append-only revisions, assumptions, invalidation conditions, open questions,
reports, events, decisions, journals, challenge reviews, and optional WatchlistItem
metadata.

```text
Instrument
└── Investment Case (instrument research file)
    ├── Thesis (current investment judgment)
    ├── Thesis revisions
    └── evidence, reports, events, decisions, journals, and reviews
```

For an instrument-centered Case, the three entities are deliberately not collapsed:
an Instrument can exist without a research file, a research file can exist before any judgment is confirmed, and
the judgment can change while the Instrument identity and research history remain
stable. One open file is reused by default for an instrument; an archived file does
not delete or archive the Instrument.

Research changes use Candidate Propose → Confirm/Reject/Withdraw. Codex may
propose changes but cannot impersonate the user to confirm or reject them.
Journal and decision appends require an explicit `user` or authorized
`external_agent` confirmer. Decisions express research or position intent only;
they never create orders, fills, or holdings.

An instrument-only Deep Dive creates or reuses a Draft instrument research file by
default. Draft means the research has a durable home, not automatic long-term
monitoring and not a confirmed investment judgment. `create_case=false` preserves
ad-hoc research.

## 5. Provider model

### A-share

- Tencent is preferred for validated quote and forward-adjusted daily bars.
- Eastmoney, Sina, CNINFO, THS, and exchange sources supply complementary facts.
- Provider-specific partial availability is represented as degraded data, not
  invented completeness.
- `a-stock-data` is a pinned reference only, never a runtime dependency.

### US

- Yahoo/yfinance is the primary current market/fundamental source where applicable.
- For a near-current request during a Yahoo regular, pre-market, or post-market
  session, a stale regular quote is compared with the latest available one-minute
  `includePrePost` bar. The newer timestamp wins and carries
  `EXTENDED_HOURS_PRICE` or `INTRADAY_QUOTE_RECOVERY`; a failed recovery remains
  explicit as `INTRADAY_QUOTE_UNAVAILABLE`. Historical `as_of` requests never use
  this current-only recovery path, and Yahoo does not provide a complete overnight
  equity market.
- Yahoo quote `previous_close` is derived from the latest completed daily bar before
  `quote_at`; for a post-market equity quote, that day's regular close is the
  previous close. The range-dependent `chartPreviousClose` metadata field is never
  used as a previous-session close; this prevents chart-window baselines from
  corrupting intraday percentage comparisons.
- Alpha Vantage is a configured fallback, not a broker quote substitute. Its optional
  comma-separated key pool is ordered and sticky, and advances only on explicit
  provider rate-limit responses; it is not a round-robin throughput mechanism.
- SEC is the authority for filings and separately based reported facts.
- FRED/ALFRED observations preserve requested vintage cutoffs.
- Social sources remain separated; current Polymarket odds are never presented as
  historical probabilities.
- TradingAgents is a pinned reference only, never a runtime dependency or second
  agent runtime.

### Instruments

Instrument Master is a registry/cache, not an allowlist. A local miss may use the
configured directory, validate one unambiguous candidate, and cache it atomically.
Portfolio research dynamically resolves legitimate broker positions before asking
market providers for facts.

## 6. Accounts and portfolio

Phase 1 account access is read-only:

- Moomoo OpenD: balances, positions, open orders, and historical deals supported
  by the adapter boundary;
- Schwab: balances, positions, and the documented transaction window through a
  project-owned `schwab-py` OAuth token; open orders are explicitly not ingested;
- Manual CSV: strict versioned holdings import.

Provider account/order/transaction identifiers are stored as stable hashes. A
missing price timestamp stays missing. Portfolio totals use native-currency gross
position value and never assume an FX rate or relabel gross exposure as NAV.

Portfolio Review adds provider-backed industry/theme classification and
descriptive correlation/beta. It prioritizes instruments by absolute position
value, uses paced bounded fan-out, and reports incomplete classification or
instrument limits explicitly.

## 7. Workflows

Five workflows gather facts while Codex remains the synthesizer:

```text
Deep Dive
Catalyst Review
A-share Market Review
US Market Review
Portfolio Review
```

Each workflow persists a compact run receipt and optional Case-bound report.
Required and optional steps determine `complete`, `partial`, or `failed`. Provider
content is untrusted external data and cannot become instructions, permissions,
or trade authorization.

## 8. Architecture

```text
src/
├── bootstrap.py
├── application/
├── domain/
├── infrastructure/
└── interfaces/
```

- Domain imports no MCP, SQLAlchemy, Alembic, settings, or providers.
- Application imports no infrastructure or interfaces.
- Interfaces validate and adapt protocols; they do not own business policy.
- Only `bootstrap.py` wires application and infrastructure.
- Provider payloads never cross the infrastructure boundary.
- Money and precise market values use `Decimal`; datetimes are timezone-aware.

## 9. Persistence and security

SQLite migrations persist research state, search indexes, accounts, portfolio
snapshots, challenge reviews, workflow receipts, and transactions. Backup/restore
uses SQLite online backup plus integrity and schema checks.

Static secrets live only in the gitignored root `.env`. Rotating OAuth tokens may
live only under gitignored `data/secrets/`. Dependency loggers that can render
query credentials, OAuth token paths, or raw SDK connection identifiers are
suppressed at provider/transport boundaries. Secrets must never appear in MCP
envelopes, logs, tests, documentation, or commits.

## 10. Acceptance

Phase 1 closeout passed:

- Ruff;
- mypy across 280 source files at closeout;
- 2,044 tests;
- Alembic upgrade to `0008_phase1l_workflows`;
- sdist/wheel build and fresh Python 3.13 isolated-wheel smoke;
- 80 declarative dialogue scenarios;
- three longitudinal Investment Case fixtures;
- exact tool inventory, forbidden dependency/table/tool audit;
- SQLite backup/restore integrity verification.

Later provider hardening and Schwab integration added focused regression tests
without rebuilding the full Phase 1 test matrix.

## 11. Known operational boundaries

- Closed-session US quotes may be stale but represent the last known session.
- Reddit anonymous RSS is rate-limited and not a reliable production identity;
  approved OAuth remains the proper future solution.
- StockTwits formal access has left the active roadmap; its compatibility adapter
  remains default-disabled and must not trigger scraping, retries, or credential requests.
- Moomoo sentiment uses the current public feed with exact-symbol filtering,
  HTML cleanup, deduplication, low-quality filtering, and versioned deterministic
  bilingual rules. It does not call a Skill or an LLM; engagement may be unknown,
  and the feed must not be presented as a historical archive.
- Polymarket can require a separately configured proxy.
- Broker position market-price timestamps may be unavailable.
- Phase 1 has no scheduler, automatic evidence ingestion, runtime LLM synthesis,
  backtest, paper broker, or order-write code.

## 12. Successor phases

Phase 2 is the Watchlist Hub: one active upstream source (Moomoo or strict Manual
CSV), database-persisted groups/memberships/history, research metadata, and
conversation-authorized add/remove. Phase 3 contains historical data, backtests,
experiments, automatic monitoring, Trade Plans, deterministic trade risk, and
Paper Trading. The [global roadmap](../roadmap/global-roadmap-cn-us.md) is the
authority for later-phase sequencing.

## 13. Public documentation policy

Detailed implementation-stage notes are intentionally excluded from the public
tree. This consolidated specification is the current source of truth.
