# QuantConnect Free manual validation guide

> Status: implemented as the complete current Phase 3C scope. It prepares and
> imports validation artifacts; it does not run a local engine or call the paid
> QuantConnect API. The prepare → web backtest → import path has been exercised
> with user-exported results; each new strategy still requires the same manual run.

## Product decision

Trading Partner uses QuantConnect Free as a user-operated remote runner until the
value of historical validation justifies paid automation. Codex remains the
conversation and code-authoring layer. Trading Partner owns the immutable local
code/manifest/result hashes and exposes uncertainty instead of claiming that the
remote run is fully attested.

```text
Codex writes LEAN Python
  -> research_workflow_run.historical_validation_prepare
  -> user copies main.py into QuantConnect Free and clicks Backtest
  -> user downloads Overview / Download Results JSON
  -> research_workflow_run.historical_validation_import
  -> Codex reviews imported metrics, explicit limitations, and the linked investment judgment
```

No QuantConnect credential or environment variable is required. All local
artifacts are written with owner-only permissions under the gitignored
`data/artifacts/historical_validation/<validation_id>/` directory.

## MCP operations

The public inventory is the 30-tool MCP vNext Shadow surface. Two variants extend the
existing `research_workflow_run` tool.

### `historical_validation_prepare`

Required inputs include one idempotency key, strategy name/hypothesis, US symbols,
date range, complete LEAN `strategy_code`, and an explicit price-normalization
mode. The operation:

- parses the Python source without executing it;
- requires exactly one top-level `QCAlgorithm` subclass with `initialize`;
- records symbols, period, resolution, normalization, cash, benchmark and
  parameters in `manifest.json`;
- hashes and writes `main.py`, `manifest.json`, and `RUNBOOK.md` atomically;
- returns the exact local paths and the manual web steps.

The manifest is an experiment declaration, not proof that the web run used those
settings. Strategy code may place simulated backtest orders inside QuantConnect,
but Trading Partner itself has no order or broker execution effect.

### `historical_validation_import`

The caller supplies a prepared `validation_id` and the local path of the JSON from
QuantConnect's **Overview -> Download Results** action. The operation:

- accepts an object-root JSON file up to 64 MiB;
- copies the exact export into the prepared artifact directory;
- records its SHA-256 and a bounded normalized summary;
- extracts available Net Profit, CAGR, Sharpe/PSR, drawdown, fees, turnover,
  capacity and order metrics without inventing missing values;
- treats the export's formal `statistics` as authoritative when a same-named
  `runtimeStatistics` display value differs, while retaining the latter under a
  `Runtime ...` label for audit;
- derives total return, CAGR and maximum drawdown from a usable exported
  QuantConnect Benchmark curve, explicitly labelled as curve metrics rather than
  an official total-return index;
- checks for statistics, Strategy Equity, Benchmark series and order-count
  availability, and compares the exported run dates with the prepared manifest;
- leaves remote code matching and point-in-time dataset version as
  `NOT_EVALUATED`.

One prepared package accepts one immutable result. A materially different result
requires a new prepare request and idempotency key so parameters and outcomes do
not become detached. The exact export remains immutable; a newer deterministic
summary schema may be regenerated from that same hashed export.

## First-run checklist

1. Sign in at [QuantConnect Terminal](https://www.quantconnect.com/terminal) and
   create a Python project named `Trading Partner Validation`.
2. Start with
   [`quantconnect-free-hourly-template.py`](../examples/quantconnect-free-hourly-template.py)
   or ask Codex to produce a complete LEAN strategy.
3. Prepare the package through the MCP before copying code to the web editor.
4. In the Code tab compare symbols, dates, resolution, normalization, cash,
   benchmark, fee/slippage model and parameters with `manifest.json`.
5. Build and run one backtest. Do not use optimizer sweeps for the first smoke.
6. Download the result JSON from Overview and import it through the MCP.
7. Review the normalized metrics alongside buy-and-hold, transaction costs,
   sample size and the two `NOT_EVALUATED` reproducibility checks.

The first acceptance run is SPY, hourly, 2008-07-01 through 2026-07-29,
split-adjusted signal data, with a simple 20/100-hour moving-average rule. Passing
this smoke proves the bridge, not that the strategy has an edge.

## Future optional work outside the current Phase 3 scope

- paid QuantConnect API/MCP submission and polling;
- platform-neutral Strategy Registry and experiment comparison;
- dataset/version registry, DuckDB/Parquet and local engines;
- walk-forward/OOS, parameter experiments and event studies;
- comprehensive look-ahead, survivorship, data-snooping and cost-sensitivity
  automation.

The prepared manifest and imported summary are intentionally versioned so these
later capabilities can reuse them if the product value eventually justifies the
additional storage, data-quality, and execution complexity. None is a Phase 3 exit
gate.
