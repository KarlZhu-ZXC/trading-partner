# Trading Partner current product specification

This document describes the product that exists now. It replaces the former
Phase 1–4 design ledgers, which mixed implementation history, acceptance notes,
and current contracts. Version-specific changes remain in `releases/`; exact MCP
schemas and operating procedures remain in their dedicated guides.

## Product loop

Trading Partner is a local-first investment judgment companion. Moomoo is the
owner's main note-authoring and analysis surface. Trading Partner keeps the durable
chain that connects an observation to reviewed judgment and later evidence:

```text
Observation → Research Subject → Thesis / Trade Plan → Decision / NO_ACTION
            → Broker activity → Trade Cycle → Performance / Behavior Review
```

Provider facts, portfolio snapshots, technical analysis, monitoring, and the Agent
support this loop. They do not replace user judgment or independently authorize an
order.

## Core records

- An `Instrument` is an objective security or market identity.
- A Research Subject is the durable research file and stable scope. Compatibility
  APIs may still call it an `InvestmentCase` or use a `case_` identifier.
- A Thesis is a versioned, falsifiable judgment. One Subject may have one live
  PRIMARY Thesis plus related SUB, COMPETITOR, and BEAR threads.
- A Trade Plan records versioned execution intent and conditions for one Instrument.
- An Observation is immutable source material. External-note revisions retain exact
  source identity, chronology, attribution, and content mode.
- A Decision, including `NO_ACTION`, records reviewed intent. It may reference exact
  Thesis, Plan, scenario, portfolio snapshot, and Observation revisions.
- Broker orders, transactions, positions, and account snapshots remain distinct
  facts. Similar Instrument and time values do not prove an exact link.
- A Trade Cycle groups durable long-only activity deterministically. Performance,
  behavior summaries, Trade Retro, and Judgment Scorecard retain their inputs and
  limitations rather than producing an opaque score.

Research and policy mutations use version, actor, idempotency, and explicit review
gates. Candidate Propose → Confirm/Reject/Withdraw remains mandatory. A research
confirmation never authorizes an order.

## Product surfaces

The Console provides Research, Journal, Portfolio, Monitoring, Operations, Data
Quality, an optional Copilot rail, and a KLineChart-based interactive technical
workspace. The chart supports six candle/bar styles, common overlay and pane
indicators, zoom/crosshair interaction, SMC overlays, drawing tools, and PNG export.
Console reads complete local results and does not implicitly refresh a broker.

The public `mcp_vnext_shadow` v12 surface has nine entry tools over 24 business
capabilities. `capability_discover` reveals operations and exact schemas on demand;
read and write gateways preserve the original capability validation. Identity,
judgment confirmation, sync, broker-order, and chart operations keep dedicated
entry tools. The tool count may change through an explicit compatibility migration.

The optional built-in Copilot uses a smaller private tool set for capability search,
reads, proposals, action preparation, and web search. Writes become exact Pending
Actions and require the same-channel user to confirm an unexpired, single-use token.
Agent conversation memory is continuity context, not a current market or account
fact.

## Today and weekly review

Overview defaults to Today Review: durable note reviews, Monitor transitions,
Agenda and due Decisions are grouped by exact Instrument. Multiple Research
Subjects keep separate links and judgments. Unmapped sources and broker/Agent
pending actions stay explicit rather than inheriting another Subject's decision.
Deferred and previously reviewed reasons are folded by default; new evidence
reopens attention. This is a presentation grouping, not resolution of source records.
Processing failures stay visible, including a failed latest note interpretation.
Operational failures retain visible action links outside the research groups.

Sources fail independently and bounded histories remain partial. Repeated Monitor
polls do not manufacture new transitions; recovered current state suppresses stale
error events. No page read reconciles, acknowledges, refreshes a Provider, runs a
model or sends a notification through these projections.

The Weekly Review tab uses the configured local Monday-to-Monday window, through
now. It combines exact confirmed Thesis revisions, including definition changes,
user Decisions (including NO_ACTION), current unresolved questions and due review
groups. Missing predecessors or definition comparisons remain unavailable. It does
not reconstruct historical question status, infer investment outcomes or compute a
win rate. The tab is a read-only review surface, not an automatic weekly message.

## Quick Review

Today Review links directly to a Quick Review card inside the selected Research Subject.
The card combines the pinned user Decision/Thesis/Plan, durable position snapshots,
source coverage and changes. It reads up to 1,000 changes in one projection; larger
histories disclose the limit and cannot use the maintain shortcut. Nothing is
refreshed or sent to a model merely by opening the card.

Latest Thinking reads the latest synced Moomoo revision for each matching note,
selects the latest dated USER blocks, and may show an existing successful
same-revision summary only when its source ordinals bind to those USER blocks.
Others' viewpoints stay separate. Pending/failed latest interpretations never fall
back to an older revision. SUMMARY_ONLY sources expose metadata only. Up to five
recent notes are shown, with missing dates and truncation disclosed. Thought dates
are distinct from sync/edit timestamps; comparisons show changes in the previous
synced USER section and do not claim that the formal Thesis changed. Date headings
use the configured local timezone; inferred years remain explicitly labelled.

Maintain records a new NO_ACTION with the exact prior references; defer records
RESEARCH_MORE with the user's gap and a future review time. One explicit submit is
authorization for that record. Expiring server context is checked again against
current evidence/formal versions; stale context requires refresh without discarding
the user's draft. Existing Decision idempotency supports recovery without duplicate
writes; reload does not resubmit. Source-specific Observation adoption remains a
separate explicit action, never an implied consequence of a general review.

Adjustments reuse the existing Thesis and Plan editors in the same Research page,
with a return shortcut and preserved Quick Review draft. Latest thinking first produces an editable before/after preview bound to the
current PRIMARY revision. It may prefill a Thesis only after the user accepts the
preview, without overwriting an open editor.
Refresh Notes uses the existing explicit capture/interpret/review workflow. When it
finishes, an active idle card refreshes while keeping the user's text; hidden,
submitting or recorded cards stay pinned. Existing proposal/confirmation and order
authorization boundaries remain unchanged.

Quick Review action, user rationale and follow-up date persist in bounded,
Subject-scoped session storage for 24 hours. An unresolved submission additionally
retains its original request/key; on reload only the durable receipt is read.
Recorded receipts clear recovery state. Retrying or abandoning an unrecorded attempt
requires an explicit action, and edits cannot silently create a different attempt.
No automatic POST, copied note payload or formal editor state is persisted by this
recovery mechanism. Storage failure remains visible and falls back to the current page.

## Copilot research process

Console calls the built-in assistant **Copilot**; existing Agent API paths, stored
identities and internal symbols remain compatible. Chat keeps the existing behavior.
Research and Counter-review are explicitly selected modes on that same runtime.

Research exposes fixed evidence questions, deterministic step progress and a bounded
read-only tool surface. Defaults are 180 seconds, eight top-level model calls and
24 tool calls; the server validates narrower or larger allowed choices. The ordinary
six-tool-round limit also applies. Fallback and optional critique calls count
against the model budget; Research uses its field checker directly without an
extra legacy prose-repair round. Research uses explicit web-search tools, not hidden native
search or an uncounted conversation-summary call. Provider-internal retries and actual
billing remain unknown; these controls are not a guaranteed token or dollar ceiling.

Model text is held until final evidence checking. Canonical FACT/CITATION blocks bind
to an exact current-turn request and field path, including available Instrument,
account, unit, time and basis context. Unverifiable numerical/factual blocks become
GAP entries; qualitative synthesis stays INFERENCE. Old conversation memory and web
text cannot establish a canonical price or position. The bounded field catalog may
omit unsupported/oversized evidence; omission is not proof of absence. Verified refs
survive receipt compaction for later source inspection. The host renders selected
facts with their context; the model need not copy the catalog. Explicit null values
can support missing-data explanations. Qualitative explanations may cite multiple
fields but remain unverified interpretation, not fact verification. Invalid optional
replacement answers preserve the primary.

Counter-review permits one additional tool-free critique, only within the remaining
budget. Failure preserves the primary answer and reports a gap. Completed read steps
and receipts survive budget stops; reconnect/reload only restores records and never
resubmits a prompt or confirms an action. A user-requested failed-turn retry restores
the original research-only mode and budgets. Missing model progress or usage after
failure is explicitly unavailable rather than zero.

The UI shows reported input/output tokens, usage completeness, elapsed time, call
counts, evidence-check counts and stop reasons. Price information for inference
billing is not configured, so cost stays unknown. Conversation usage profiles group
recorded samples by model/effort/mode/budget and report P50/P95 elapsed times; different
prompts make these descriptive samples, not model rankings or quality comparisons.
The deterministic evaluation command includes 40 additional synthetic evidence cases
alongside the existing runtime catalog. These gates validate implementation behavior,
not live model research quality, investment returns, or the correctness of every
qualitative inference.

## Charts, valuation assumptions and calibration

Research and Monitor can open an exact Instrument in `/charts`. Chart retrieval is
explicit; stale responses are ignored. Locked SMC structures expose occurrence and
confirmation times, snapshot status, algorithm version and source-bar range. A
historical cutoff filters confirmed structures/bars without reconstructing historical
invalidation status. Session-only drawings reset with Instrument, period, basis and
version. Matching ACTIVE Plan conditions remain separate. An explicit reviewed
session handoff may prefill a DRAFT Thesis or Plan notes, never execution thresholds.

Valuation is a Console-only, deterministic annual diluted-EPS/P/E scenario method for
positive-earnings US ordinary operating companies. SEC filing identity/time and USD
per-share basis are required; unsupported companies or missing data are gaps. User
normalization and P/E assumptions remain separate from facts and produce a nine-cell
sensitivity grid. No enterprise/aggregate equity value or current split adjustment is
inferred. Explicitly reviewed saves append immutable Journal versions with existing
idempotency/audit gates; they never confirm judgment. Source tokens expire after one
hour or API restart; calculation and save never refresh Providers.

Judgment Calibration pins a user Decision and its exact Thesis/Plan references,
separately showing factual outcomes, matched Plan observations, adherence records and
attribution limitations. It reuses Changes Since Review, Scorecard, Agenda and Retro;
it has no combined score or new return engine. NO_ACTION remains reviewable, future
review dates are disclosed, and absent attribution remains unknown. These reads do
not generate evaluations or write research state.

See [the workflow guide](guide/research-chart-valuation.md) for supported inputs and
continuous navigation/recovery rules.

## Research and observations

Research Subjects support company, theme, macro, catalyst, and portfolio-concern
work. A Subject may exist without a primary Instrument or confirmed Thesis. Live
Theses, active Plans, and Monitors enforce explicit lifecycle dependencies; the
system does not auto-activate or cascade related records to hide a conflict.

External observations share one provider-neutral revision contract. The Local
Observation Bridge accepts closed full-text JSON. Moomoo note capture is read-only
and never automates the desktop UI. Summary-only text is change-detection evidence;
it cannot reach model interpretation or Decision adoption.

Optional model interpretation produces drafts only. Attribution and date ordering
are computed deterministically first. A model cannot confirm a Thesis, Decision,
Plan, Monitor, position, or order. Contributor models require the owner's explicit
training opt-in.

## Changes since a reviewed Decision

Research shows a read-only Changes Since Review workspace beside the exact user
Decision baseline and its linked immutable Thesis revisions, assumptions,
invalidation descriptions, and Trade Plan version. The default baseline is the
latest user Decision by recorded time, not a model draft or a backdated decision time.
A pinned `none` baseline explicitly preserves the unreviewed state.

Scoped Observation revisions, immutable Monitor observation changes/transitions,
and Agenda versions are compared with their predecessors. Old and new fact times,
source/version identities, and available provenance are disclosed. Full-note previews
are bounded USER excerpts; summary-only revisions expose metadata only. A matching
Instrument never proves a particular Thesis assumption changed. Only a validated
Monitor condition on an exact Plan/Thesis has an exact Plan relation.

Each source reports its own coverage; missing/legacy Monitor history is partial and
failed reads do not imply no changes or resolve reviews. Projection reads neither
contact Providers/models nor create notifications. Stable source IDs deduplicate
replays. Pagination follows the complete scoped projection, and a selected change
is relocated by identity if newer rows shift its page.

The baseline, selected change and page survive reload and Journal navigation using
only opaque identifiers and a numeric offset in the URL. Journal restores an exact
historical Observation independently of the latest inbox window. Opening or returning
from review never confirms anything; existing Decision and Observation-review gates
remain authoritative. Refresh keeps the baseline; Use Latest Review explicitly
advances it after a completed review. User drafts are not auto-submitted on reload.

## Market and company facts

- A-share: quotes, market structure, capital flow, limit-up and sentiment context,
  ETF options, statements, research reports, operating disclosures, and optional
  hog-cycle data.
- US: quotes and bars, market context, SEC-first statements and filings, insiders,
  company events and updates, news, macro, source-separated sentiment, and prediction
  market context.
- Korea: Instrument resolution plus Yahoo quote/bars, shared technical analysis,
  manual Watchlist, monitoring, and XKRX post-market dispatch. DART, KR brokerage,
  fundamentals, news, sentiment, breadth, and Position Sizing are unavailable.
- Cross-asset: selected Yahoo continuous metal futures, formal CME metals, DCE
  live-hog EOD facts, Dukascopy precious-metal spot feeds and rolling copper/light-oil
  CFDs. Every proxy retains its venue, units, adjustment, roll, and basis warnings.

Provider output keeps source, observation time, freshness, basis, typed warnings,
and typed failures. Missing data is never silently estimated. Ordinary durable reads
do not contact a Provider; explicit sync operations own upstream refresh.

## Technical analysis

`technical_get_snapshot` and `technical_render_chart` support valid equity, ETF,
index, futures, and OTC identities where daily bars exist. The engine derives `1d`
and ISO-week `1w` views from one sourced daily series.

The current `tp_technical_v3` output contains:

- EMA 10/20, SMA 50/200, RSI 14, MACD, ATR 14, Bollinger Bands;
- ADX/+DI/-DI, Stochastic, ROC 20, MFI, VWMA 20, OBV, and relative volume;
- deterministic trend, momentum, volatility, and volume states;
- nearby support/resistance from five-bar swing points clustered within 0.75 ATR;
- recent engulfing, hammer, shooting-star, and doji recognition.
- `tp_smc_v1` internal and swing HH/HL/LH/LL structure, close-confirmed BOS/CHoCH,
  Order Blocks, Fair Value Gaps, equal-high/equal-low liquidity, and
  premium/equilibrium/discount zones.

MCP/Agent chart artifacts render auditable PNG candlesticks, EMA20, SMA50, structure
levels, volume, and RSI14. Console can request source bars with the same validated
technical read and render an interactive KLineChart workspace. Server-derived SMC
overlays are locked; user drawings remain editable for the current chart session.
All outputs disclose source, bar time, price-adjustment basis, algorithm version,
and `historically_validated=false`.

`tp_smc_v1` is an independent deterministic implementation based on the feature
vocabulary published for [LuxAlgo Smart Money Concepts on
TradingView](https://www.tradingview.com/script/CnB3fSph-Smart-Money-Concepts-SMC-LuxAlgo/).
The LuxAlgo-compatible profile uses its published defaults: internal length 5,
swing length 50, EQH/EQL confirmation length 3 with a 0.1 × ATR(200) threshold,
ATR(200) volatile-bar filtering for Order Blocks, and High/Low mitigation. A pivot
is not available until its right-hand confirmation bars close; BOS/CHoCH requires a
later close crossover/crossunder. Every swing and zone exposes occurrence and
confirmation time. FVGs use the published three-bar body-direction and cumulative
auto-threshold conditions.

When a timeframe lacks enough history for ATR(200), structure and FVG calculation
continue, while the output sets `atr_200_ready=false` and reports that volatile-bar
Order Block filtering and EQH/EQL are incomplete.

Ten-symbol acceptance against an independently transcribed reference evaluator over
three years of identical adjusted Yahoo daily OHLC passed all 50 category comparisons for structure,
Order Blocks, FVGs, EQH/EQL, and value zones. This establishes core calculation
compatibility, not TradingView pixel parity or cross-vendor bar equality. Visibility
toggles, styling, alerts, Strong/Weak High/Low, and multi-timeframe previous levels
are not modeled. The engine does not claim institutional order knowledge or a trade
signal.

## Portfolio, risk, monitoring, and review

Account sync supports configured Schwab, Moomoo OpenD, or manual CSV sources. Durable
portfolio reads preserve native currency, snapshot time, coverage, open orders, and
quality warnings. Exact fees remain required for Net P/L; unavailable fees do not
discard transactions or become estimates.

Risk and Position Sizing are deterministic. They evaluate the exact portfolio,
policy, Plan, price, and Instrument inputs they receive. They do not reserve capital,
change a position, or create an order.

Monitoring evaluates versioned price, technical, portfolio, and fact rules. Missing
inputs become `NOT_EVALUATED`. Optional model commentary cannot change deterministic
rule results. Due, post-market, and notification jobs retain idempotency, leases, and
durable receipts.

Journal connects reviewed Decisions to durable broker activity without copying the
underlying facts. It exposes Trade Cycles, Daily Equity, TWR, MWR/XIRR, drawdown,
behavior cohorts, review items, and exact Timeline links only when the required data
coverage exists.

## Orders and unattended work

Schwab supports narrowly confirmation-gated US stock/ETF preview, submit, status,
and cancel operations. Submit or cancel requires an exact unexpired preview and
explicit authorization for that action in the current conversation. An unknown
submit outcome is never retried automatically.

The installed SGOV BUY LIMIT/DAY/NORMAL cash-sweep scheduler is the sole unattended
order exception. Its persistent authorization, reserve controls, price guards,
operational lock, and receipts grant no authority for another symbol, sell, replace,
cancel, overnight session, or general Agent/MCP execution.

## Runtime and data boundary

Installed runtimes use one owner-controlled `RUNTIME_ROOT`. Mutable databases,
secrets, locks, attachments, observations, backups, and operational artifacts stay
below it. Private notes, credentials, account values, and real reconciliation inputs
never enter Git, package data, examples, tests, URLs, telemetry, or error messages.

The Console data API remains loopback-only. Optional trusted-LAN mode exposes only
the authenticated web process and is not public hosting. SQLite production uses WAL,
bounded busy handling, and explicit maintenance. Ordinary startup verifies the exact
migration head but never migrates as a side effect of a read.

## Deliberate exclusions

- local or automated backtesting and parameter optimization;
- autonomous Thesis, Plan, Candidate, position, or order decisions;
- general unattended execution beyond the installed SGOV exception;
- order replacement, options/complex orders, short selling, or overnight Schwab
  orders;
- fabricated cross-currency returns, missing fees, corporate-action lots, or cost
  basis;
- arbitrary scraping or relabelling a fallback/proxy as the requested market fact.

## Detailed references

- [MCP capability and trust boundary](guide/mcp-capability-boundary.md)
- [Console design system](guide/console-design-system.md)
- [Local Console and maintenance](operations/local-console-and-maintenance.md)
- [Known operational constraints](operations/known-issues.md)
- [Product roadmap](roadmap/global-roadmap-cn-us.md)
- [Current unreleased changes](releases/unreleased.md)
