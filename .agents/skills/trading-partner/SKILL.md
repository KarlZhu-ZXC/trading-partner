---
name: trading-partner
description: "Use Trading Partner MCP for verified investment facts, research, portfolio/risk, monitoring, workflows, confirmation-gated Schwab orders, and the closed SGOV scheduler exception. Public surface: mcp_vnext_shadow."
---

# Trading Partner

Use the live MCP schema and repository `AGENTS.md` as the detailed source of truth.
Keep calls and explanations bounded; never copy the product specification into chat.
The current repository boundary is implemented Phase 1–4D, 31 public tools, and
migration head `0072_external_note_review_drafts`.

## Call contract

- Use only the currently published `mcp_vnext_shadow` tools; do not assume a frozen
  tool count. Prefer the intent-first View tools for note-to-judgment review.
- Grouped tools take one required `request` object. Put `operation` and all variant
  fields inside `request`; never mix fields from another operation.
- Trust `data` only when `ok=true`. Preserve `as_of`, `fetched_at`, `freshness`,
  `degraded`, `sources`, `warnings`, and typed `errors`.
- Never invent a quote, balance, fill, event, or missing field.
- When maintaining the local Console, keep MCP transport compaction separate from
  Console completeness: BFF page reads use the same validated capabilities without
  the 15 KiB projection. Never render `_truncated` as a domain row or action target.

## Tool routing

- System/identity: `system_health`, `instrument_resolve`. Daily recovery
  always follows `system_health` with `investment_case_read/attention`; the
  health `attention_summary` is materialized ReviewItems only and cannot skip
  the inbox.
- View intake: `view_inbox` lists structured pending note changes without private full
  bodies; `view_review_get` compares one exact revision with durable judgment and
  portfolio context; confirmation-gated `view_review_run` may append a configured
  non-authoritative deep-review draft; `current_view_get` restores the latest exact
  confirmed view.
- Research: `investment_case_read`, `investment_case_manage`,
  `research_judgment_get`, `research_judgment_propose`,
  `research_judgment_confirm`, `research_memory_get`, `research_memory_append`.
- Facts: `a_share_get_facts`, `market_data_get`, `technical_get_snapshot`,
  `technical_render_chart`, `us_company_get`, `us_context_get`.
- Portfolio/execution: `account_get`, `external_state_sync`, `portfolio_analyze`,
  `broker_order_manage`, `research_workflow_run`.
- Watchlist/risk/monitoring: `watchlist_get`, `watchlist_manage`,
  `portfolio_risk_get`, `risk_policy_update`, `monitor_read`, `monitor_manage`,
  `monitor_evaluate`.

## Canonical market-data boundary

Treat the MCP response as canonical, never the Provider payload:

`Provider payload → Provider adapter → domain model → application DTO → MCP envelope`

- Provider aliases such as `regularMarketPrice`, `prev_close_price`, `f60`, or
  `overnight_price` must not leak into client-facing field names.
- For quotes, use canonical `instrument_id`, `quote_at`, `session`,
  `display_price`, `price_basis`, `previous_close`, and `previous_close_basis`.
  Optional OHLC/volume/bid/ask fields remain null when the source cannot establish them.
- Provider differences belong in `sources`, delay/freshness, and warning/error codes;
  they must not change the meaning of canonical fields.
- `display_price` is the usable displayed observation. Read `price_basis` before
  describing it as last, midpoint, settlement, bid, or ask.
- Equity/ETF/index `previous_close_basis=previous_completed_regular_session_close`
  follows the actual returned `quote_at + session`. Call it
  前收（前一已完成常规交易时段收盘）, never 昨收 or the previous arbitrary candle.
- Futures `previous_close_basis=previous_completed_daily_bar_close`; do not call it
  a regular-session close or settlement.

For near-current US equity/ETF quotes, preserve pre/post/overnight session labels.
During Sunday–Thursday 20:00–04:00 America/New_York, a true overnight observation
requires Moomoo OpenD `market_state=OVERNIGHT` plus the exact instrument's dedicated
overnight field. Otherwise retain the latest-known fallback with
`OVERNIGHT_QUOTE_UNAVAILABLE`; never relabel it as overnight.

OTC `XAUUSD`/`XAGUSD` and rolling CFDs are broker observations, not licensed spot
benchmarks or exchange futures. A midpoint is not a trade. Weekend proxy sources
must retain their token/perpetual/CFD identity and basis warnings.

## Research and writes

- Say Research Subject / 标的 / 研究档案, not user-facing “InvestmentCase”.
- Also apply `bossmo-trading-discipline` only when the user is making or reviewing
  an actionable investment judgment for a specific stock or ETF—such as equity
  Thesis conviction, entry, add, hold, reduce, exit, or a concrete setup. Do not
  trigger it from software development, UI/schema/test/docs work, generic fact
  requests, domain field names, or non-equity assets. When activated, require
  `UPSIDE`, `SIDEWAYS`, `PULLBACK`, and `INVALIDATION`; another user-selected
  Strategy may remain primary.
- Research Subject metadata defines durable scope; Thesis holds judgment; Trade Plan
  holds conditional execution intent. Do not merge these concepts.
- Candidate decisions use Propose → explicit Confirm/Reject/Withdraw. Never choose
  the outcome autonomously.
- To attach an Instrument to a Research Subject, propose one `watchlist_item` create
  and ask for the explicit decision. Confirmation attaches it directly; do not add
  Shortlist, Select, or another status-transition step. Legacy Instrument Selection
  statuses may appear in durable reads but are not a user task to complete.
- When the user explicitly decides in the current chat, relay exactly
  `reviewed_by="user"`, `submitted_via="mcp_chat"` (`codex_chat` remains valid),
  and a bounded `authorization_note`. Ambiguous target/action requires
  clarification.
- Journal, decision, watchlist, policy, Monitor, review, and agenda writes retain
  their idempotency/version/actor gates.
- Structured Phase 4 Decisions may bind `strategy_code`, one of `UPSIDE` /
  `SIDEWAYS` / `PULLBACK` / `INVALIDATION`, an exact same-Subject Trade Plan
  version, and an aware review due time. These fields are intent metadata, not an
  order authorization. An elapsed due time uses the existing durable ReviewItem
  path; a later Decision closes it automatically only when it explicitly
  supersedes that exact Decision. Failed/bounded reads never prove closure.
- Moomoo living notes are Console-only observation revisions. Attribution is a
  deterministic dated-section state machine: each date starts as USER. A line-leading
  `@speaker` marker may introduce any bounded new speaker. Legacy bare prefixes recognize
  only boss墨、宝总、姜汁汽水; other bare `heading:` prefixes inherit the current speaker
  and never create a person. Mid-line @ mentions do not switch attribution. Date order is detected per revision as newest-first,
  oldest-first, mixed, or unknown; the model must not assume one global order.
  Summary fallback may recover only a proven prefix/suffix around the prior FULL
  editor body, never a middle rewrite.
  First-pass note interpretation uses OpenCode Go `qwen3.8-flash` at `max` with a
  120-second per-attempt timeout; do not route it to DeepSeek. Escalated review may
  use `muse-spark-1.3-contributor` at `high` only when
  `EXTERNAL_NOTE_CONTRIBUTOR_TRAINING_OPT_IN=true` records the owner's explicit
  acceptance that prompts and completions may train future models. Muse 1.3 and Grok
  4.6 use the Responses route. Do not default Muse review to `xhigh`: the sanitized
  comparison was slower and over-propagated an EXIT action. Both interpretations are
  drafts only, and `Review as Decision` still requires the
  user to review and save through the existing Decision contract. A saved Decision
  may carry the exact optional `external_note_revision_id`; never substitute a
  display string such as `note_id@vN`, and never treat the link as Thesis, Plan, or
  order confirmation.
- External observation sources share one adapter contract and immutable revision
  store. Summary-only source text cannot reach the model or Decision adoption.
  Moomoo, future TradingView capture, and the local full-text JSON bridge must reuse
  this path instead of creating source-specific research workflows.
- Never control the Moomoo desktop UI to retrieve notes. Optional authenticated
  note enrichment reads an owner-only Cookie file and uses serial, bounded internal
  web reads with a freshly randomized delay per request. Authentication/rate-limit/
  page-shape failure falls back to cache and must not expose credentials or promote
  a list summary to full text. The desktop CEF Cookie store is not an authentication
  source; never claim its locale Cookie is a reusable login session. Do not use
  Computer Use or UI automation against Moomoo. When the user explicitly authorizes
  it, read-only process/IPC/network analysis is allowed if it does not modify app,
  certificate/proxy, account, or trading state and never exposes recovered secrets.
  A Web-session Cookie may be accepted over stdin and must never be printed.

## Accounts, monitoring, and orders

- `account_get` and ordinary portfolio/risk reads are durable-only. Call
  `external_state_sync` only when the user explicitly asks to refresh upstream.
- `portfolio_analyze/trade_cycles` is a deterministic long-only projection over
  durable transactions, grouped by account + Instrument + native currency. Treat
  OPEN/CLOSED/UNRESOLVED, coverage, missing fee/price, oversell, and re-entry
  warnings as part of the result; it never refreshes a broker or creates an order.
  SGOV is `CASH_MANAGEMENT`; do not include it in active-trade win-rate claims.
- `portfolio_analyze/performance_series` returns native-currency TWR, MWR/XIRR,
  and drawdown only when durable equity/cash-flow boundaries support them.
- Instrument attribution separates Net Trading P/L, exact Dividend Income, and Total
  P/L. Never allocate a cash-only dividend by amount or holding proximity; ambiguous
  identity, unsupported corporate-action lots, or missing transferred basis fails closed.
- `portfolio_analyze/behavior_summary` exposes numerator, denominator, exclusions,
  and exact refs without an aggregate score. Do not infer exact Decision coverage
  from Instrument and time alone.
- `portfolio_analyze/unlinked_activity` reads unmatched Broker trades. Use
  `research_memory_append/activity_annotation` only after an explicit user choice
  to link the exact activity or mark it unplanned/cash-management/correction.
- Preview Cycle split/merge/relink with
  `portfolio_analyze/trade_cycle_override_preview` before an explicitly confirmed
  `research_memory_append/trade_cycle_override`. Use `journal_timeline`,
  `daily_equity`, and `behavior_review_history` for closed-loop reads.
- When an order preview follows an exact Decision/Plan, pass its case, Decision,
  and Plan version. These links improve the Journal chain but never authorize submit.
- Preserve native currencies and stale/missing coverage; never infer FX, NAV, cash,
  or transaction completeness.
- Moomoo historical deals use best-effort exact `order_fee_query` enrichment. When
  any fee remains unavailable, keep Net P/L null and `TRANSACTION_FEES_UNAVAILABLE`;
  a UI may show Gross P/L only when it labels it as gross and keeps the missing-fee
  warning adjacent.
- Deterministic Monitor rules remain valid when optional LLM judgment fails.
  Treat unavailable facts as `NOT_EVALUATED`; never turn them into a pass.
- Monitor judgment is read-only. It cannot mutate research, holdings, or orders.
- Monitor transition notifications may end with a model-analysis section capped at
  160 Chinese characters using `max` effort and an 80-second timeout. Treat it as
  optional interpretation only; if unavailable, the deterministic event remains
  valid and must still be delivered.
- A Trade Plan or research confirmation never authorizes an order.
- Live Schwab submit/cancel requires an exact unexpired preview and explicit
  current-chat authorization for that action. Unknown submit outcomes are not retried.
- The installed operational SGOV scheduler is the sole persistent exception: SGOV
  BUY LIMIT/DAY/NORMAL only, with reserve and quote guards. It does not authorize an
  MCP/Agent order, another symbol, sell, cancel, replace, or overnight session.

## Output discipline

- Distinguish Provider fact, deterministic derivation, model interpretation, plan,
  submitted order, and fill.
- Preserve proxy/basis/roll/delay/coverage warnings near the claim they qualify.
- Do not describe technical indicators as predictions or unverified backtests as proof.
- Do not expose secrets, raw Provider payloads, request URLs, headers, or exception text.

## Host setup

```toml
[mcp_servers.trading-partner]
command = "uv"
args = ["run", "trading-partner-mcp"]
```

Other unattended trading, options/complex orders, short selling, order replacement,
and autonomous confirmation remain out of scope.

Installed hosts must use the explicit `runtime.env` produced by
`trading-partner-init`. Mutable files belong below its `RUNTIME_ROOT`; private
Observation bodies and real account-basis checkpoints must never enter Git, package
data, examples, tests, documentation, or tool output.
