# Accounts, monitoring, and orders

- `portfolio_get` and ordinary portfolio/risk reads are durable-only. Call
  `external_state_sync` only when the user explicitly asks to refresh upstream.
- `portfolio_get/trade_cycles` is a deterministic long-only projection over
  durable transactions, grouped by account + Instrument + native currency. Treat
  OPEN/CLOSED/UNRESOLVED, coverage, missing fee/price, oversell, and re-entry
  warnings as part of the result; it never refreshes a broker or creates an order.
  SGOV is `CASH_MANAGEMENT`; do not include it in active-trade win-rate claims.
- `portfolio_get/performance_series` returns native-currency TWR, MWR/XIRR,
  and drawdown only when durable equity/cash-flow boundaries support them.
- Instrument attribution separates Net Trading P/L, exact Dividend Income, and Total
  P/L. Never allocate a cash-only dividend by amount or holding proximity; ambiguous
  identity, unsupported corporate-action lots, or missing transferred basis fails closed.
- `portfolio_get/behavior_summary` exposes numerator, denominator, exclusions,
  and exact refs without an aggregate score. Do not infer exact Decision coverage
  from Instrument and time alone.
- `portfolio_get/unlinked_activity` reads unmatched Broker trades. Use
  `research_memory_append/activity_annotation` only after an explicit user choice
  to link the exact activity or mark it unplanned/cash-management/correction.
- Preview Cycle split/merge/relink with
  `portfolio_get/trade_cycle_override_preview` before an explicitly confirmed
  `research_memory_append/trade_cycle_override`. Both take the Cycle action in
  `override_operation`; `operation` selects the MCP operation. Use `journal_timeline`,
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
