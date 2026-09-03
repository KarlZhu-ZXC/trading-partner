# Phase 3 — Cross-Asset Facts, Manual Validation, and Plan Controls

Phase 3 grows Trading Partner beyond A-share/US equity research while preserving
the same provenance, read-only, and no-fabrication rules. The current product scope
is implemented. Phase 3C prepares a QuantConnect Free package and imports a
user-downloaded result, but Trading Partner does not own historical datasets, run a
backtest engine, automate QuantConnect, or execute an order. The manual prepare →
web backtest → import path has been exercised with a user-exported result.

To keep ownership and dependencies clear, Phase 3 is consolidated into four tracks:

| Track | Capability domain | Status |
|---|---|---|
| Phase 3A | Formal futures and cross-asset market facts | Free CME/DCE/Dukascopy integration implemented; LME discovery deferred |
| Phase 3B | Company financial/operating facts and optional industry datasets | Implemented, including caller-specified peer comparison |
| Phase 3C | Manual historical-validation bridge | QuantConnect Free prepare/import implemented; heavy historical platform deferred outside the current phase scope |
| Phase 3D | Judgment-to-plan controls | Implemented: versioned Trade Plans, Position Sizing, Risk v2, and Monitoring v2 |

An adjacent Korea Exchange market slice is also implemented without adding a fifth
Phase 3 track or another public tool. It formalizes `Market.KR` identities and Yahoo
quote/bars, shared technical analysis, Manual CSV Watchlist membership, price
Monitoring, and XKRX post-market dispatch. It does not add DART/company research,
KR sentiment/breadth, broker accounts, Moomoo Watchlist writes, or Position Sizing.

Two post-3D judgment-continuity slices are also implemented without expanding the
30-tool MCP vNext Shadow surface: Catalyst Agenda C0–C3 separates future known events from
observed facts and syncs free current Yahoo/FRED dates explicitly; Judgment Scorecard
S1 persists nine deterministic calibration cards for one exact Thesis revision,
including Agenda outcome calibration. Neither produces a total score, order, or
automatic Thesis/Trade Plan mutation.

## Phase 3A — Formal futures and cross-asset market facts

> Status: free continuous proxies, formal CME metal contracts, DCE live-hog EOD
> facts, Dukascopy OTC gold/silver and rolling copper/light-oil CFDs implemented.
> LME remains a non-blocking licensing/discovery item.

The existing public tools support six continuous metal-futures proxies without
adding another public tool. The current compact
inventory is 28. Yahoo remains primary. Timestamped
Sina quotes and Eastmoney daily-derived bars provide narrowly scoped fallbacks.

| Instrument ID | Yahoo symbol | Basis |
|---|---|---|
| `future:US:GC=F` | `GC=F` | COMEX front-month continuous gold future |
| `future:US:MGC=F` | `MGC=F` | COMEX front-month continuous micro gold future |
| `future:US:SI=F` | `SI=F` | COMEX front-month continuous silver future |
| `future:US:HG=F` | `HG=F` | COMEX front-month continuous copper future |
| `future:US:PL=F` | `PL=F` | NYMEX front-month continuous platinum future |
| `future:US:PA=F` | `PA=F` | NYMEX front-month continuous palladium future |

Supported paths:

- `instrument_resolve` recognizes `asset_type="future"` and Yahoo `ROOT=F` symbols;
- `market_data_get(request={"operation":"quote",...})` returns a futures quote;
- `market_data_get(request={"operation":"bars",...})` supports `1m`, `5m`, `15m`,
  `30m`, `60m`, `1d`, `1wk`, and
  `1mo`; an omitted adjustment becomes `none` for futures;
- `technical_get_snapshot` and `technical_render_chart` use unadjusted daily
  futures bars and disclose the continuous-futures basis.

Asset-aware Provider routing is deliberately narrower than the public interval
schema:

| Capability | Primary | Fallback | Covered symbols |
|---|---|---|---|
| current quote | Yahoo | Sina external-futures quote | `GC=F`, `SI=F`, `HG=F` |
| `1d` / `1wk` / `1mo` bars | Yahoo | Eastmoney global-futures daily bars, with deterministic weekly/monthly aggregation | all six seeded symbols |
| `1m`–`60m` OHLCV | Yahoo | none | no price-only line feed is promoted to OHLCV |

The Sina timestamp is parsed as Asia/Shanghai publication time. Because the
public feed has no delay SLA or verified futures-session calendar, fallback
successes preserve `freshness=unknown`, an observed `data_delay_seconds`,
`BEST_EFFORT_PUBLIC_FEED_NO_SLA`, and `FUTURES_SESSION_UNKNOWN`. Its previous
settlement is not mislabeled as previous close. Sina has no exact MGC/PL/PA
mapping, so those quotes remain unavailable when Yahoo fails.

Eastmoney symbols are fixed as `GC00Y`, `MGC00Y`, `SI00Y`, `HG00Y`, `PL00Y`, and
`PA00Y`. The adapter consumes only real daily OHLCV rows and may aggregate them
to weekly/monthly bars. Eastmoney supplies a trade date but no bar timestamp, so
the adapter anchors each row to the 17:00 America/New_York futures trade-date
boundary and excludes an in-progress row before that boundary. This derived time
is disclosed by `EASTMONEY_DAILY_DERIVED_BARS`. Sina's current-day minute endpoint
was explicitly rejected as an OHLCV fallback because it exposes line prices rather
than verified minute open/high/low/close bars.

Every successful futures response carries `FUTURES_CONTRACT_NOT_SPOT` and
`CONTINUOUS_FUTURES_ROLL_RISK`. `GC=F` must not be called XAUUSD, `SI=F` must not
be called XAGUSD, and `HG=F` must not be called London/LME copper. Exact futures
support/resistance levels must not be reused as OTC spot levels without a separately
observed basis.

Yahoo, Sina, and Eastmoney are best-effort personal-research sources without an
SLA. Intraday history remains limited by Yahoo/yfinance to approximately the latest
60 days. Contract rolls and differing vendor construction may introduce basis
changes or artificial discontinuities; Phase 3A does not splice providers into a
back-adjusted research-grade continuous series.

### Formal free integration

- CME GC/MGC/SI/HG/PL/PA share versioned product, contract, settlement/OI,
  curve, and continuous-mapping models. CME public facts are reference-only;
  Yahoo specific-contract quotes/bars never fall back to a continuous proxy.
- DCE LH uses the same model but exposes official EOD facts only. Intraday DCE
  bars and quotes remain unavailable rather than inferred from third-party main contracts.
- Dukascopy supplies XAUUSD/XAGUSD bid/ask broker-feed quotes and bars. Its copper
  and light-oil instruments are separately identified as rolling CFDs. The latter
  uses `cfd:OTC:LIGHT_CMD_USD`; `USOIL` is an alias and never implies WTI spot or
  a NYMEX `CL` contract.
- `market_data_get` now supports `futures_curve` and gated `spot_future_basis`;
  non-comparable units/times/delivery bases stay explicit.
- Monitor and Technical Engine accept CME and OTC instruments. DCE rules remain
  `NOT_EVALUATED` until a valid EOD settlement evaluation path exists.
- `trading-partner-futures-sync` explicitly refreshes definitions and persists
  append-only EOD statistics vintages; repeated identical syncs are idempotent.

Still out: LBMA benchmark licensing, LME Cash/3M until a zero-fee use licence is
verified, DCE minute data, expired-CME complete history, and back-adjusted datasets.

### Phase 3A closeout decision

There is no active Phase 3A closeout backlog. Futures facts remain request-driven:
ordinary queries contact the configured Provider and return typed degradation when
it is unavailable. The product does not require continuous futures ingestion,
durable settlement read-through, or DCE live-hog monitoring.

LME/LBMA licensing, DCE intraday data, expired-contract history, and back-adjusted
continuous series are accepted boundaries. They may be reconsidered only in a
future historical-data program and do not block Phase 3 closeout.

## Phase 3B — Company financial/operating facts and optional industry datasets

> Status: cross-market normalized financial statements and quality metrics,
> generic A-share operating disclosures, the optional hog dataset, and durable hog
> history ingestion are implemented. Caller-specified peer comparison is also
> implemented; further hog-cycle expansion is not currently prioritized.

Company fundamentals are the reusable core of this track; industry-cycle datasets
are optional extensions for sectors where a cycle model is genuinely useful.

### Company financial statements and quality facts

No new public tool was added. A-share equities use
`a_share_get_facts(operation="financials")`; US equities use
`us_company_get(request={"operation":"fundamental_statements",...})`.

| Market | Primary | Fallbacks | Point-in-time boundary |
|---|---|---|---|
| A-share | Sina public financial-report API | Eastmoney summary statements | publication cutoff retained; interim income/cash flow labeled cumulative/YTD |
| US | SEC Company Facts | yfinance, then Alpha Vantage | SEC latest/vintages are cutoff-safe; fallbacks are current-only |

Both markets normalize core income, balance-sheet, and cash-flow fields. The
shared deterministic layer derives free cash flow, operating-cash-flow/net-income,
FCF margin, capex/revenue, current ratio, and net debt only when every required
input is present. It neither substitutes zero for missing facts nor interprets a
ratio as a forecast. A-share responses accept 1–20 periods and bounded metric
filters. US `view=latest` returns one visible filing per period; `view=vintages`
keeps SEC accession/form/filing metadata and intentionally omits cross-vintage
derived ratios. Equity Deep Dive automatically includes the normalized statement
package; industry-specific company disclosures are still opt-in.

Alpaca is not used for statements: its useful project role is market/broker data,
while SEC provides the authoritative US filing-time axis and yfinance supplies a
practical current-only fallback.

### Optional hog-cycle dataset

The existing `a_share_get_facts` tool adds the closed operation
`industry_cycle`; these capabilities now route through the grouped vNext surface.

```json
{
  "operation": "industry_cycle",
  "cycle": "hog",
  "lookback_months": 12,
  "view": "compact",
  "metric_codes": [],
  "offset": 0,
  "limit": 50
}
```

`view` defaults to `compact` and returns the latest visible observation for each
selected metric plus deterministic per-metric coverage (`count`, `first_period`,
`last_period`) and `total_observations`. `view=series` returns a filtered page of
observations with the same coverage plus `offset`, `limit` (1..200), and
`has_more`. Optional `metric_codes` are validated lower_snake_case filters.
Provider/repository history may still use the full requested lookback; only the
MCP payload is bounded. A 240-month request is a target window, never a claim of
continuous 20-year coverage.

The `hog` cycle uses publication-time-safe official pages from 全国畜牧总站. It
returns national monthly piglet/live-hog/pork prices, corn and soybean-meal prices,
fattening-hog compound-feed prices, pig-grain ratios, and the latest visible
periodic capacity observation. The backend parses named facts deterministically
and does not invoke a Skill or LLM or assign a cycle phase.

The wire contract is intentionally industry-generic: every fact is an observation
with `metric_code`, decimal `value`, `unit`, `period_start`, `period_end`,
`frequency`, `measurement_basis`, `published_at`, `source_url`, estimation status,
and methodology metadata. This prevents a period-end sow stock, a year-to-date
slaughter total, and a monthly average price from being treated as equivalent.
New industry datasets extend the
metric registry and Provider layer rather than adding cycle-specific DTO families
or public MCP tools.

Published vintages are stored in `industry_metric_observations`. Run
`uv run trading-partner-industry-sync --months 240` for an explicit backfill or
monthly refresh; the JSON receipt reports the actual first/last month and missing
months. The official pages currently discoverable by the adapter do **not** form a
continuous 2006-present series. Missing months remain missing—there is no
interpolation, synthetic reconstruction, or silent third-party substitution. A
20-year request is therefore a target window, not a claim of 20-year coverage.

The standalone national dataset intentionally does not embed company facts.
`a_share_get_facts(operation="company_operating_metrics")` supplies the separate
company-disclosure package, and an explicit hog Deep Dive composes both packages.
National averages must not be treated as a listed producer's cost or margin.
DCE live-hog contracts and term structure belong to the shared formal-futures
integration in Phase 3A.

### 牧原股份验证记录（2026-07-23）

用牧原股份（`equity:A_SHARE:002714.SZ`）贯穿“标的解析 → 个股深度研究 →
猪周期事实 → 外部综合”后，确认并处理了以下流程问题：

- A 股中文全名可通过腾讯名称目录发现候选，并须再经腾讯报价接口校验后才写入
  Instrument Master；本地 Master 仍是缓存/注册表，不是允许名单。
- `create_case=false` 的 ad-hoc Deep Dive 不再调用仅适用于研究档案的
  `investment_case_read` 的 `context` operation，因此不会制造预期内的
  `INPUT_VALIDATION_ERROR` 或把
  成功研究错误标成 Partial。
- A 股研究标的工作流按步骤串行进入 Provider Router，避免同一研究流程把多个
  Eastmoney 家族请求同时压入共享队列；Provider 内部的缓存、限流、熔断和
  fallback 语义保持不变。

本轮没有把牧原股份特例写进公共 MCP。公告解析、公司经营序列、显式 Deep Dive
组合及 `industry_cycle` 有界输出已经完成。通用同行比较尾项也已完成：

- **3B-T02 — 调用方指定同行的可比事实包（已完成）。** 复用现有财报、行情和可选 A 股经营指标，
  对明确给出的同行标的生成同口径比较事实；MCP 不自动选择同行、排名或给出估值结论。
  请求和输出契约以本节及 capability guide 为准。

猪周期 Deep Dive 组合 DCE 期限结构、DCE 生猪监控和官方猪周期长期补源均不在当前优先
队列。既有猪周期查询能力保留，但暂不扩展。

用于后续验收的公司官方披露样本包括
[2026 年 6 月销售简报](https://static.cninfo.com.cn/finalpage/2026-07-07/1225411958.PDF)、
[2026 年半年度业绩预告](https://static.cninfo.com.cn/finalpage/2026-07-11/1225419543.PDF)、
[2026 年一季报](https://static.cninfo.com.cn/finalpage/2026-04-22/1225136604.PDF)和
[2025 年年度报告](https://static.cninfo.com.cn/finalpage/2026-03-28/1225042507.PDF)。

2026-07-25 live smoke 使用 `lookback_months=18`、`document_limit=20`：巨潮有界
关键词检索得到 20 份去重文档和 86 条经营观测，实际观测期间为 2025-03 至
2026-06；包含月度销售简报、业绩预告、一/三季报、半年报和年报解析回执。
2026 年 6 月结构化值与原文一致：商品猪销量 622.7 万头、均价 9.69 元/公斤、
销售收入 75.00 亿元、累计销量 3861.5 万头、期末能繁母猪 311.3 万头。
这是一轮可重复的 live smoke，不承诺每家公司都披露相同字段或具有相同历史深度。

#### P0 整改状态

以下问题来自 2026-07-23 牧原股份验收。`HOG-P0-001` 至 `004` 已完成并通过
2026-07-25 live smoke；当前没有阻塞猪周期研究流程的未解决 P0。所有实现均为
通用数据/研究能力，没有引入牧原股份专用工具或硬编码数据。

| ID | 原始问题 | 状态 | 完成日期 / 当前结论 |
|---|---|---|---|
| `HOG-P0-001` | 公告路径只有元数据，无法稳定得到正文事实 | **已解决** | 2026-07-25：巨潮公告按 `as_of` 和请求时间窗有界检索；仅下载官方 finalpage PDF；销售简报、业绩预告和定期报告均保留解析回执、公告时间、原文/PDF 链接与解析版本 |
| `HOG-P0-002` | 缺少上市公司经营指标时间序列 | **已解决** | 2026-07-25：通用 schema 返回明确披露的销量、售价、销售收入、出栏/屠宰量、能繁母猪与完全成本，区分月度/累计及公司口径；不重复财报 statements |
| `HOG-P0-003` | Deep Dive 未显式组合行业周期事实 | **已解决** | 2026-07-25：仅当 `research_workflow_run` 的 `deep_dive` operation 显式选择 `industry_cycle="hog"` 时，才组合公司经营披露与全国猪周期 compact 包；不按公司名称推断行业 |
| `HOG-P0-004` | 长周期 `industry_cycle` 事实包过大 | **已解决** | 2026-07-25：支持 `view=compact|series`（默认 compact）、`metric_codes` 过滤及 `offset`/`limit<=200` 有界分页，并返回 coverage / `has_more` |
| `HOG-P0-005` | 官方月度核心序列的长期覆盖有限 | **持续改进（不阻塞）** | 不再要求固定 20 年或连续覆盖；尽可能同步最长的可验证官方历史，持续显式披露真实覆盖与缺口，不插值、不伪造连续序列 |

## Phase 3C — QuantConnect Free manual validation bridge

> Status: the current Phase 3C scope was implemented on 2026-07-30. The MCP
> prepares hashed LEAN packages and imports user-exported QuantConnect result JSON.
> The user-operated end-to-end path has been exercised. See the
> [QuantConnect Free guide](../guide/quantconnect-free-bridge.md).

The existing `research_workflow_run` tool now has
`historical_validation_prepare` and `historical_validation_import` operations.
They write owner-only, gitignored artifacts and do not add public tools.
QuantConnect login, web compilation and the Backtest click remain user-operated.
Imported metrics are degraded because the free export cannot attest the exact
remote code hash or immutable dataset version.

The current boundary is intentionally narrow:

- Codex authors complete LEAN Python;
- Trading Partner validates without executing, hashes, and writes the package;
- the user copies the code to QuantConnect Free and runs it manually;
- Trading Partner imports the downloaded result and reports available metrics plus
  explicit reproducibility gaps.

Historical storage, DuckDB/Parquet, dataset/version registries, a local runner,
paid QuantConnect automation, Strategy Registry, experiment orchestration,
walk-forward/OOS/event studies, automated bias checks, and Trading Partner-owned
A-share/US market-rule simulation are deferred future options. QuantConnect/LEAN
and the submitted strategy code own market, fee, slippage, liquidity, and corporate
action simulation. Trading Partner records declared settings but does not attest
that the remote run used them. No imported result confirms a Thesis or authorizes
live execution.

## Phase 3D — Judgment-to-plan controls

> Status: implemented on 2026-07-26 and retained in the grouped public surface.

Monitoring extensions, Trade Plan, Position Sizing, and Risk Engine extensions are
one dependency chain and are therefore planned together:

```text
Current Thesis + verified facts
→ versioned Trade Plan
→ deterministic sizing range
→ expanded risk checks
→ monitorable conditions and invalidations
```

The combined scope covers technical/volume/fundamental/filing/announcement/macro/
sentiment/Thesis-invalidating monitors; entry/scale/exit/expiry plan conditions;
risk-budget/ATR/volatility-target sizing; and theme, drawdown, liquidity, event,
A-share T+1/limit/suspension, stale-data, and duplicate-instrument checks. Pending
broker orders are not yet incorporated into Risk v2.

All outputs remain proposals or calculations. They do not create positions, orders,
fills, or confirmation authority.

The implementation reuses existing public tools: Trade Plans use the research-state
Candidate lifecycle, sizing is returned by
`portfolio_risk_get(request={"operation":"check","trade_plan_id":...})`, and plan
conditions compile only through explicit `monitor_manage` `create` / `update` calls.
Trade Plan identities and versions are append-only and linked Monitors preserve the exact
plan version. A plan update never silently rewrites an existing Monitor.
The plan-level `instrument_id` identifies the execution/position instrument used by
sizing and portfolio risk. Each monitorable condition independently identifies the
instrument whose fact drives that condition. A linked Monitor may therefore observe
USOIL (`cfd:OTC:LIGHT_CMD_USD`) for a UCO execution plan. The relationship is only a
declared decision reference: the engine never substitutes the CFD price, return,
currency, or multiplier for UCO and does not assume one-to-one tracking.

A-share sizing rounds down to 100-share lots; US equity/ETF sizing supports four-decimal
fractional quantities. Plan, policy, cash, stop-distance, freshness, optional liquidity,
ATR, and volatility limits are disclosed separately. Missing required facts suppress the
recommended range rather than producing a zero-sized recommendation or implicit pass.

Monitoring v2 resolves price, volume, technical, fundamental, company event, macro,
sentiment, Thesis-state, and portfolio-risk facts deterministically. Provider failures,
unsupported fields, and stale observations transition to `NOT_EVALUATED`; repeated
unchanged states remain event-free. A linked Monitor cannot outlive a finite Trade Plan.

## Trade Retro — transaction-versus-plan discipline

> Status: implemented on 2026-08-09 without adding a public MCP tool. Migrations
> `0037_trade_retro` and `0038_trade_retro_reviews` add immutable plan snapshots,
> runs, append-only human review revisions, and export receipts.

Trade Retro replaces the former broad “performance attribution completion” roadmap
with a narrower, auditable product loop. It does not calculate benchmark attribution
or pretend that later plans describe earlier intent:

1. `prepare` captures current Trade Plans and confirmed Decision Records for ACTIVE
   Research Subjects before the requested period;
2. `run` reads durable normalized broker transactions and coverage receipts, accepts
   only an eligible pre-period snapshot, and persists deterministic discipline findings;
3. optional Bailian `qwen3.8-max` narration receives only those bounded facts and must
   answer in Chinese; model failure leaves the deterministic report intact;
4. `review` appends an explicitly confirmed human-review revision with optimistic
   version checking. The latest revision can accept/dispute/resolve individual
   Findings and record correction notes/action items without rewriting the Run;
5. `export` atomically replaces only Trading Partner's marker block in the configured
   Obsidian weekly note, preserving handwritten content and including the latest review;
6. `portfolio_analyze/retro_history` and the Console Trade Retro page read immutable
   Run and review history without contacting a Provider.

The first algorithm reports incomplete activity coverage, missing, ambiguous, or
inactive pre-trade plans, missing invalidation, unmatched buy/sell Decision Records,
within-period round trips, and same-day sell/re-entry. It cannot infer a fill, mutate a
Research Subject/Thesis/Trade Plan, change a position, or execute an order.

`RESEARCH-STATE-004` is also closed: ACTIVE/PAUSED Monitors require an ACTIVE
Research Subject, and linked ACTIVE/PAUSED Monitors block Research Subject or live
Trade Plan retirement until the caller explicitly archives them. No lifecycle state
is cascaded implicitly.

## Phase 3 public-surface rule

The roadmap names capabilities, not a promise to register one MCP tool per noun.
Phase 3 must preserve a compact public surface by consolidating related operations
behind closed enums and existing domain coordinators. Existing `monitor_*` and
`portfolio_risk_get` (`check`) tools are extended rather than duplicated; overlapping
weekly review capabilities should build on
`research_workflow_run(operation="portfolio_review")` instead of adding aliases.
