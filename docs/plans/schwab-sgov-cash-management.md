# Schwab SGOV Cash Management

Status: **Shadow Preview, SGOV-only automatic BUY, and confirmation-gated general orders implemented**

## Objective

Use Schwab account facts to keep excess USD cash in whole shares of SGOV while
preserving a per-account cash reserve. The automatic path remains auditable and
fails closed outside one exact SGOV BUY boundary.

## Source-of-truth contract

- Available cash is exactly Schwab `currentBalances.cashBalance`.
- `buyingPower`, `liquidationValue`, `totalCash`, position market value, and inferred
  balances never substitute for `cashBalance`.
- Active supported BUY orders reserve their unfilled limit notional. SELL orders do
  not reserve buy-side cash. An unavailable or unsupported order set blocks future
  execution instead of being treated as empty.
- SGOV sizing uses a current Schwab ask, whole shares, and native USD. A stale quote,
  wide/unknown spread, missing ask, non-zero/unknown margin, or missing account fact
  is explicit.

## Default Shadow policy

```text
hard cash floor           $3,000 per account
operational buffer          $200 per account
minimum order notional    $1,000
price reference           current Schwab ask
quantity                  floor(surplus / ask)
order type                LIMIT
duration                  DAY
session                   NORMAL
```

```text
open_buy_reserve = Σ[(quantity - filled_quantity) × limit_price]
reserved_cash    = hard_cash_floor + operational_buffer + open_buy_reserve
surplus_cash     = max(0, cashBalance - reserved_cash)
quantity         = floor(surplus_cash / ask)
estimated_cost   = quantity × ask
projected_cash   = cashBalance - open_buy_reserve - estimated_cost
```

`broker_order_manage(request={"operation":"cash_sweep_preview",...})` returns the
calculation, blocker codes, and exact hypothetical Schwab payload with
`shadow_only=true` and `execution_effect=false`. The public MCP preview and the
legacy `run` command never call the separate live-order service.

## Delivery stages

### S0 — Shadow Preview (implemented)

- Refresh Schwab balances, positions, and supported active one-leg open orders.
- Read the SGOV broker bid/ask through the project-owned OAuth client.
- Persist no quote and submit no order.
- Calculate every selected Schwab account independently and return totals.
- Expose one read-only MCP capability; Challenge Review moved into the existing
  Judgment groups so the vNext public inventory is smaller even after adding it.

### S0.5 — Scheduled Shadow plan (implemented)

- `trading-partner-sgov-plan run` is due-checked by the XNYS calendar. It runs at
  15:45 `America/New_York` on ordinary sessions (15 minutes before an official early
  close) to recommend a passive bid, then refreshes again at 15:55 (five minutes
  before an early close) for a completion check. Weekends and exchange holidays make
  no Provider request.
- Each due run explicitly refreshes Schwab only, then calculates every returned
  Schwab account from `cashBalance`, active BUY reserves, the $3,000 floor, and the
  $200 buffer. A stale quote or another blocker stays visible and no order is sent.
- One stable Outbox identity per market session and phase prevents duplicate account
  refreshes and duplicate Telegram cards. The first card recommends a current-bid DAY
  limit while sizing conservatively at ask. The completion card never reuses the old
  bid or assumes a one-cent spread: if the user is still unfilled and still wants same-
  day deployment, it recommends the newly refreshed current ask as the limit. The
  mobile notification uses vertical account blocks;
  `trading-partner-sgov-plan preview` prints the same facts as a terminal table without
  enqueueing a notification.
- `trading-partner-sgov-plan-scheduler install` creates token-free macOS launchd wakes
  at minutes 45 and 55 of each hour. Application-side New York due selection handles
  DST; non-due wakes do not contact a Provider.

### S1 — Confirmation-gated live order (implemented)

- `broker_order_manage` owns `preview`, `submit`, `status`, and `cancel` operations
  without increasing the 27-tool MCP surface. SGOV Shadow remains a separate operation.
- Preview re-reads Schwab `cashBalance`, margin, active BUY reserves, positions, and a
  contextual quote. The exact account/order payload is persisted for 30–300 seconds.
- Submit consumes that preview once and requires `confirmed_by=user`,
  `submitted_via=codex_chat`, an idempotency key, and the bounded current-chat
  authorization note. Schwab must return an order ID before status becomes `SUBMITTED`.
- A missing/5xx transport response becomes non-retryable `UNKNOWN`; the service never
  emits another POST automatically. Status refresh and explicitly confirmed cancel use
  the internal broker ID without exposing raw account hashes.
- US equity/ETF v1 supports LIMIT and STOP_LIMIT BUY/SELL plus MARKET, STOP,
  TRAILING_STOP, and TRAILING_STOP_LIMIT SELL. AM/PM/SEAMLESS are LIMIT-only;
  `SEAMLESS` ends at 20:00 ET and is not overnight trading.
- The live service blocks non-zero/unknown margin, insufficient `cashBalance` after
  existing BUY reserves, overselling, and unbounded BUY market/stop/trailing orders.

### S2 — Narrow unattended BUY sweep (implemented)

- Installing `trading-partner-sgov-plan-scheduler` is the durable authorization;
  uninstalling it is the kill switch. The job invokes the private `auto-run` command,
  while MCP and Agent order actions remain unchanged and confirmation-gated.
- 15:45 is preparation-only. At 15:55 ET (or five minutes before an official early
  close), the service refreshes Schwab again and may place at most one order per
  eligible account: instrument `etf:US:SGOV`, BUY, LIMIT at current ask, DAY, NORMAL.
- Missing/stale bid or ask, spread above $0.02, unknown/non-zero margin, unsupported
  open orders, less than $1,000 deployable notional, or failure to preserve the
  `$3,000 + $200` reserve blocks that account.
- Stable session/account preview and submit identities survive retries. A replayed
  `SUBMITTING` claim, or an `UNKNOWN` Provider outcome, never issues another POST and
  stays in the unresolved queue for reconciliation.
- The completion Telegram receipt survives past market close and distinguishes
  submitted, skipped, failed, and reconciliation-required accounts.
- Retryable account-refresh and quote/sizing reads use at most three bounded
  attempts. Unknown order submission outcomes are never retried. A blocked
  completion notification identifies the failed stage, attempt count, Provider,
  typed error, and safe HTTP status when one was returned.
- The exception cannot sell SGOV, cancel/replace an order, use margin, use AM/PM/
  SEAMLESS/overnight sessions, or submit any other instrument. Those actions still
  require exact current-chat confirmation through `broker_order_manage`.

## Explicit non-goals

- No use of margin or `buyingPower` to increase quantity.
- No fractional ETF shares.
- No market-on-close or market order fallback.
- No claim that SGOV is risk-free or that the Schwab API path supports 24-hour SGOV
  execution; the automatic order is restricted to the regular XNYS session.
- No automatic assumption about QII or the user's local tax filing. Tax attributes
  remain annual issuer/broker facts outside the order-sizing algorithm.
- No order, fill, or tax claim inferred from a Shadow Preview.
