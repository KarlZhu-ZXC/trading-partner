# Trading Partner — Documentation Index

Root-level markdown is limited to `README.md` (product overview) and `AGENTS.md`
(agent operating rules). Current phase specifications are consolidated. The
`plans/` area holds bounded design and acceptance records; current behavior is
always folded back into the phase specifications and capability guide.

## Layout

```text
docs/
├── README.md                 # this index
├── examples/
│   ├── manual-holdings.v1.csv
│   ├── manual-watchlist.v1.csv
│   └── quantconnect-free-hourly-template.py
├── guide/
│   └── mcp-capability-boundary.md
├── phases/
│   ├── phase1.md
│   ├── phase2.md
│   └── phase3.md
├── plans/
│   ├── phase3a-formal-futures-cross-asset-plan.md
│   ├── phase3c-quantconnect-free-bridge.md
│   └── performance-attribution-and-console-plan.md
├── operations/
│   ├── known-issues.md
│   ├── local-console-and-maintenance.md
│   └── phase3a-live-smoke.md
├── releases/
│   └── unreleased.md
├── roadmap/
│   └── global-roadmap-cn-us.md
```

## Roadmap

| Document | Purpose |
|---|---|
| [roadmap/global-roadmap-cn-us.md](roadmap/global-roadmap-cn-us.md) | Global product principles and phase sequencing (A-share + US core, KR/cross-asset extensions) |

## User guide

| Document | Purpose |
|---|---|
| [guide/mcp-capability-boundary.md](guide/mcp-capability-boundary.md) | Complete Phase 1 MCP setup, capability, trust, write, provider, and out-of-scope boundaries |

## Current phase specifications

| Document | Purpose |
|---|---|
| [phases/phase1.md](phases/phase1.md) | Consolidated Phase 1 product, architecture, capability, acceptance, and operational boundary |
| [phases/phase2.md](phases/phase2.md) | Phase 2 Watchlist Hub, Risk, Monitoring, and cross-market Technical Engine v2 |
| [phases/phase3.md](phases/phase3.md) | Phase 3 cross-asset facts, QuantConnect Free manual validation bridge, and judgment-to-plan controls |

## Implementation plans and acceptance records

| Document | Purpose |
|---|---|
| [plans/phase3a-formal-futures-cross-asset-plan.md](plans/phase3a-formal-futures-cross-asset-plan.md) | Approved free-provider design and Phase 3A implementation/acceptance record |
| [plans/phase3c-quantconnect-free-bridge.md](plans/phase3c-quantconnect-free-bridge.md) | Implemented zero-cost LEAN package/result-import bridge for user-operated QuantConnect Free backtests |
| [plans/performance-attribution-and-console-plan.md](plans/performance-attribution-and-console-plan.md) | Staged real-performance attribution plan, QMT/FX deferral, and implemented local-console boundary |
| [plans/mcp-surface-reduction-plan.md](plans/mcp-surface-reduction-plan.md) | Implemented compact v2 reduction from 52 legacy tools to the sole 28-tool runtime surface, with discriminated schemas and permission separation; the compatibility profile was removed |

## Operations

| Document | Purpose |
|---|---|
| [operations/known-issues.md](operations/known-issues.md) | Active verified defects, deferred external boundaries, and accepted operational constraints |
| [operations/local-console-and-maintenance.md](operations/local-console-and-maintenance.md) | Loopback-only console, SQLite backup, cache retention, and maintenance commands |
| [operations/moomoo-opend-macos.md](operations/moomoo-opend-macos.md) | Secure macOS command-line OpenD layout, launchd lifecycle, readiness, and upgrade procedure |
| [operations/phase3a-live-smoke.md](operations/phase3a-live-smoke.md) | Free CME/DCE/Dukascopy live-smoke and typed-degradation runbook |

## Release notes

| Document | Purpose |
|---|---|
| [releases/unreleased.md](releases/unreleased.md) | Changes queued for the next release |
| [releases/v0.2.0.md](releases/v0.2.0.md) | Phase 3 facts, compact 28, and judgment controls |

## Source layout

```text
src/
├── bootstrap.py          # composition root only
├── application/          # ports, DTOs, services
├── domain/               # pure domain
├── infrastructure/       # composition, config, persistence/orm, providers, system
└── interfaces/           # MCP adapters
```

Python imports are top-level (`application.*`, `domain.*`, `infrastructure.*`,
`interfaces.*`, `bootstrap`). Console entries are `uv run trading-partner-mcp`
for the MCP server, `uv run trading-partner-watchlist-sync` for an explicit
Watchlist-only refresh, and `uv run trading-partner-post-market-sync` for the
due-checked US post-market account plus Watchlist job. Explicit-cadence
`trading-partner-monitor-run` remains available for diagnostics. Normal INTERVAL
and A-share/US post-market Monitoring uses the unified `trading-partner-monitor-run
due`; on macOS one token-free hourly launchd wake is managed by
`trading-partner-monitor-scheduler`.
Optional phone delivery uses a durable Telegram Outbox operated by
`trading-partner-monitor-notifications`; it sends transition alerts plus one
consolidated summary for every evaluated A-share/US post-market group and does not
invoke Codex or an LLM.
The loopback-only, LLM-free operational frontend is started with
`uv run trading-partner-console` plus `npm run dev` under `console/`.
Database status, owner-only backup, and dry-run-by-default cache retention are
managed by `uv run trading-partner-maintenance`.
Formal futures definitions and EOD statistics are explicitly synchronized through
`uv run trading-partner-futures-sync`; it is read-only with respect to broker state.
