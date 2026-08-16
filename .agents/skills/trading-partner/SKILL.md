---
name: trading-partner
description: "Use Trading Partner MCP for verified investment facts, research, portfolio/risk, monitoring, workflows, confirmation-gated Schwab orders, and the closed SGOV scheduler exception. Public surface: mcp_vnext_shadow."
---

# Trading Partner

Use the live MCP schema and repository `AGENTS.md` as the detailed source of truth.
Keep calls and explanations bounded; never copy the product specification into chat.

## Call contract

- Use only the 27 `mcp_vnext_shadow` tools.
- Grouped tools take one required `request` object. Put `operation` and all variant
  fields inside `request`; never mix fields from another operation.
- Trust `data` only when `ok=true`. Preserve `as_of`, `fetched_at`, `freshness`,
  `degraded`, `sources`, `warnings`, and typed `errors`.
- Never invent a quote, balance, fill, event, or missing field.

## Tool routing

- System/identity: `system_health`, `instrument_resolve`.
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
- Research Subject metadata defines durable scope; Thesis holds judgment; Trade Plan
  holds conditional execution intent. Do not merge these concepts.
- Candidate decisions use Propose → explicit Confirm/Reject/Withdraw. Never choose
  the outcome autonomously.
- To attach an Instrument to a Research Subject, propose one `watchlist_item` create
  and ask for the explicit decision. Confirmation attaches it directly; do not add
  Shortlist, Select, or another status-transition step. Legacy Instrument Selection
  statuses may appear in durable reads but are not a user task to complete.
- When the user explicitly decides in the current chat, relay exactly
  `reviewed_by="user"`, `submitted_via="codex_chat"`, and a bounded
  `authorization_note`. Ambiguous target/action requires clarification.
- Journal, decision, watchlist, policy, Monitor, review, and agenda writes retain
  their idempotency/version/actor gates.

## Accounts, monitoring, and orders

- `account_get` and ordinary portfolio/risk reads are durable-only. Call
  `external_state_sync` only when the user explicitly asks to refresh upstream.
- Preserve native currencies and stale/missing coverage; never infer FX, NAV, cash,
  or transaction completeness.
- Deterministic Monitor rules remain valid when optional LLM judgment fails.
  Treat unavailable facts as `NOT_EVALUATED`; never turn them into a pass.
- Monitor judgment is read-only. It cannot mutate research, holdings, or orders.
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
