# Trading Partner product roadmap

## Product direction

Trading Partner is a local-first, long-horizon investment judgment companion.
Codex or another MCP host owns conversation and synthesis. Trading Partner owns
durable research state, provider-backed facts, portfolio context, deterministic
risk/monitoring controls, provenance, and typed degradation.

The product is designed to challenge an investment judgment over time—not to act
as a generic market chatbot, broker terminal, or autonomous trading agent.

## Invariants

1. **Research and execution remain separate.** The current repository has one narrow,
   confirmation-gated Schwab single-leg US stock/ETF order surface. Its sole
   unattended exception is the installed SGOV BUY LIMIT/DAY/NORMAL cash-sweep
   scheduler; there is no replacement, options/complex orders, short selling, other
   unattended execution, or autonomous position mutation.
2. **Facts remain attributable.** Price, account, financial, and event facts carry
   source, time, basis, freshness, warnings, and typed errors.
3. **Missing data is not a value.** Unavailable or stale facts become degraded,
   incomplete, or `NOT_EVALUATED`; they are never silently replaced by an estimate.
4. **Durable state requires explicit authority.** Research Subject, Thesis, Trade
   Plan, risk-policy, Watchlist, journal, decision, and Monitor writes retain actor,
   confirmation, version, and idempotency gates.
5. **The MCP surface stays compact.** The sole public profile is `mcp_vnext_shadow` with
   27 grouped tools and closed operation unions.
6. **LLMs do interpretation, not data ownership.** Ordinary facts, rule evaluation,
   and Trade Retro findings are deterministic. Optional Monitor judgment and Trade
   Retro narration use a sandboxed server-side model only after deterministic
   feature construction and cannot mutate research state, portfolio state, or orders.

## Current product boundary

### Research and memory

- Research Subjects（研究标的/研究档案）for companies, themes, macro questions,
  catalysts, and portfolio concerns.
- Multiple Thesis threads per Subject: one live PRIMARY plus SUB, COMPETITOR, and
  BEAR relationships.
- Candidate Propose → explicit Confirm/Reject/Withdraw lifecycle.
- Versioned Trade Plans, assumptions, invalidations, open questions, evidence,
  reports, events, journals, decisions, Timeline, and Challenge Review.
- Theme-first Instrument Selection when the eventual ETF/equity is not yet known.

### Market and company facts

- A-share quotes, structure, capital flow, rankings/sentiment, ETF options,
  filings/reports, statements, operating disclosures, and optional hog-cycle data.
- US/KR quotes and bars, US company statements/filings/events/news, FRED macro,
  Reddit/Moomoo source-separated sentiment, and current Polymarket probabilities.
- Shared A-share/US/KR daily/weekly Technical Engine with charts.
- Continuous metal futures, formal CME metal contracts, DCE live-hog EOD facts,
  Dukascopy XAUUSD/XAGUSD and rolling copper/light-oil CFDs.
- Weekend-only PAXG/USDC and Hyperliquid CL/USDC references with explicit proxy
  basis, bounded retry, and durable secret-safe Provider diagnostics.

### Portfolio and operations

- Durable read-only Schwab, Moomoo OpenD, or manual-CSV account context.
- Explicit account, transaction, and Watchlist synchronization; ordinary reads do
  not contact brokers.
- Native-currency activity coverage, FIFO/broker-basis performance summaries,
  exposure analysis, hypothetical additions, and deterministic Risk Engine checks.
- Journal decision records, deterministic Trade Cycles, Daily Equity, native-currency
  TWR/MWR/drawdown, behavior cohorts, immutable reviews, and provider-neutral external
  Observation revisions with explicit Decision adoption.
- Immutable Trade Retro: capture a pre-period Trade Plan/Decision snapshot, compare
  it with durable broker transactions, persist deterministic discipline findings,
  optionally add a bounded Chinese narrative, append version-checked human review
  revisions with Finding dispositions and action items, and safely project the Run
  plus latest review into an owned Obsidian weekly-note block.
- Moomoo or manual-CSV Watchlist Hub with complete group/membership history.
- Versioned Monitoring Hub with price and deterministic fact rules, daily/weekly
  technical indicators, hysteresis, immutable Run observations, transition events,
  and optional Telegram Outbox delivery.
- One hourly local scheduler for due interval and A-share/US/KR post-market groups.
- Loopback-only Console for Research, Portfolio, Monitoring, Operations, Data
  Quality, and the compact capability workbench. Its disabled-by-default shared
  Agent Runtime uses five private capability tools and does not change the public
  MCP count; an explicit Monitor run may separately call the configured server-side
  model only for an enabled composite judgment policy.
- Manual QuantConnect Free bridge: prepare hashed LEAN code, user runs it on the
  web, then import the downloaded result JSON.

The authoritative detail is the
[MCP capability boundary](../guide/mcp-capability-boundary.md), not this summary.

## Current maintenance roadmap

### R1 — Reliability and evidence quality

New capability breadth is frozen while the implemented Phase 1–4D product is
consolidated around stability, usability, data quality, and cross-feature closure.
Completed implementation plans are not retained as parallel specifications.

Continuous requirements remain:

- preserve exact Provider failure stages and retry/admission diagnostics;
- improve data coverage receipts before adding more derived conclusions;
- keep schedulers, notifications, migrations, backup, and Console state observable;
- reduce schema and test duplication when coverage is already represented elsewhere;
- keep public documentation synchronized with the 27-tool runtime contract.

### R2 — Preserve Catalyst and judgment calibration

The implemented durable future-event agenda separates scheduled/expected events from
already observed Research Events. It provides coverage and date certainty,
retains revisions when dates move, and never infers that “no returned event” means
“no catalyst.” Completed outcomes link to durable Event/Report/Evidence facts, and
Judgment Scorecard S1 calibrates one exact Thesis revision against those facts without
creating an opaque aggregate score.

The authoritative contracts now live in
[Phase 4](../phases/phase4.md) and the
[MCP capability boundary](../guide/mcp-capability-boundary.md).

### R3 — Historical validation only when value is proven

The current QuantConnect Free bridge remains manual. Paid API automation, local
historical databases, experiment orchestration, walk-forward testing, and bias
analysis are optional future investments—not assumed next steps. They should be
added only after repeated manual validations demonstrate real product value.

### R4 — Operate the completed Phase 4 Journal loop

Phase 4 不扩展市场广度，而是把现有 Research、Trade Plan、Broker activity、Portfolio
Performance、Trade Retro 和 Review Queue 组织成一条可追溯的个人操作与学习主线：

```text
Decision / NO_ACTION
  -> Order Intent / Order Result
  -> Broker Transaction / Fill
  -> Trade Cycle
  -> Performance + Behavior Review
  -> Next Decision Discipline
```

产品已把 `/decision-workbench` 兼容路由原地改造为顶层 Console `Journal`，并保留 Portfolio、
Trade Retro 和 Research 专业路由。Journal 不建立第二套交易事实；它引用 durable source、append-only
annotation、确定性 Trade Cycle、日频账户估值、可信收益率和行为 cohort。`strategy_v1` 的
`UPSIDE`、`SIDEWAYS`、`PULLBACK`、`INVALIDATION` 与实际动作进入同一决策快照，
`NO_ACTION` 也是正式记录。

4A Capture、4B Trade Cycle、4C Performance、4D Behavior Review 均已实现。后续工作只改善
覆盖、性能、可观测性和使用闭环；公共 MCP 保持 27 个 grouped tools，订单确认和 SGOV
唯一 unattended exception 不变。完整合同见 [Phase 4 specification](../phases/phase4.md)。

## Deferred integrations

- Additional brokers and A-share account execution feeds.
- Cross-currency consolidated performance until timestamped FX coverage is defined.
- DART fundamentals, KR news/sentiment/breadth, KR broker sync, and KR position sizing.
- Licensed LBMA/LME benchmarks, complete expired-futures history, and research-grade
  back-adjusted continuous futures.
- Stable future-event providers for A-share and KR markets.
- StockTwits runtime access; historical stored values remain readable only.

Deferred means unsupported, not “silently approximated through another Provider.”

## Explicit non-goals for the current repository

- Autonomous/unattended live orders, paper-trading engines, order replacement,
  options/complex orders, short selling, or Schwab API overnight execution.
- Autonomous Thesis confirmation, Trade Plan mutation, or fill inference.
- A runtime that scrapes arbitrary websites because a formal Provider failed.
- A general-purpose social network, news terminal, tax engine, or accounting system.
- A claim that technical indicators or LLM judgments are historically validated
  strategies.

If controlled execution is ever built, it belongs in a separately permissioned
execution service with independent credentials, previews, confirmation, limits,
kill switches, audit, and no implicit authority from this research MCP.

## Advancement gates

New roadmap work should start only when all relevant gates hold:

- product need is demonstrated by a real repeated workflow;
- a free or explicitly approved data source has a testable contract;
- identity, timestamp, basis, freshness, and fallback semantics are designed first;
- writes have a clear authority and idempotency model;
- the capability fits an existing grouped tool or justifies changing the compact
  public surface;
- focused tests cover the new invariant without recreating broad provider matrices;
- README, `AGENTS.md`, capability guide, relevant Phase spec, release note, roadmap,
  and Skill are updated in the same change.
