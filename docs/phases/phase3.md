# Phase 3 — Historical Validation and Cross-Asset Expansion

Phase 3 grows Trading Partner beyond A-share/US equity research while preserving
the same provenance, read-only, and no-fabrication rules. Backtests, paper trading,
and order execution remain unimplemented unless explicitly marked otherwise below.

## Phase 3A — Commodity futures market facts

> Status: implemented on 2026-07-21.

The existing public tools now support six free Yahoo continuous futures proxies;
the public MCP inventory remains exactly 52 tools.

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
- `us_get_market(operation="quote")` returns a futures quote;
- `market_get_bars` supports `1m`, `5m`, `15m`, `30m`, `60m`, `1d`, `1wk`, and
  `1mo`; an omitted adjustment becomes `none` for futures;
- `technical_get_snapshot` and `technical_render_chart` use unadjusted daily
  futures bars and disclose the continuous-futures basis.

Every successful futures response carries `FUTURES_CONTRACT_NOT_SPOT` and
`CONTINUOUS_FUTURES_ROLL_RISK`. `GC=F` must not be called XAUUSD, `SI=F` must not
be called XAGUSD, and `HG=F` must not be called London/LME copper. Exact futures
support/resistance levels must not be reused as OTC spot levels without a separately
observed basis.

Yahoo is a best-effort personal-research source without an SLA. Intraday history is
limited by Yahoo/yfinance to approximately the latest 60 days. Contract rolls may
introduce basis changes or artificial discontinuities; Phase 3A does not construct
a back-adjusted research-grade continuous series.

## Still pending

- provider-backed XAUUSD/XAGUSD and copper spot quotes/bars with explicit bid, ask,
  mid, unit, session, and timestamp basis;
- simultaneous spot/future observations and a durable basis series;
- LME Cash and LME 3-month copper as distinct licensed instruments;
- contract-specific futures, expiry calendars, open interest, and controlled roll
  construction;
- historical stores, backtests, experiments, paper trading, and attribution.

These pending items must not be inferred from the availability of Yahoo futures.
