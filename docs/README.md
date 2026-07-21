# Trading Partner — Documentation Index

Root-level markdown is limited to `README.md` (product overview) and `AGENTS.md`
(agent operating rules). Current phase specifications are consolidated; detailed
implementation-stage notes are intentionally excluded from the public tree.

## Layout

```text
docs/
├── README.md                 # this index
├── examples/
│   ├── manual-holdings.v1.csv
│   └── manual-watchlist.v1.csv
├── guide/
│   └── mcp-capability-boundary.md
├── phases/
│   ├── phase1.md
│   ├── phase2.md
│   └── phase3.md
├── operations/
│   └── known-issues.md
├── roadmap/
│   └── global-roadmap-cn-us.md
```

## Roadmap

| Document | Purpose |
|---|---|
| [roadmap/global-roadmap-cn-us.md](roadmap/global-roadmap-cn-us.md) | Global product principles and phase sequencing (A-share + US) |

## User guide

| Document | Purpose |
|---|---|
| [guide/mcp-capability-boundary.md](guide/mcp-capability-boundary.md) | Complete Phase 1 MCP setup, capability, trust, write, provider, and out-of-scope boundaries |

## Current phase specifications

| Document | Purpose |
|---|---|
| [phases/phase1.md](phases/phase1.md) | Consolidated Phase 1 product, architecture, capability, acceptance, and operational boundary |
| [phases/phase2.md](phases/phase2.md) | Phase 2 Watchlist Hub, Risk, Monitoring, and cross-market Technical Engine v2 |
| [phases/phase3.md](phases/phase3.md) | Phase 3 commodity-futures foundation and remaining validation/cross-asset boundary |

## Operations

| Document | Purpose |
|---|---|
| [operations/known-issues.md](operations/known-issues.md) | Ordered cross-phase defect queue for Moomoo, Reddit, and future verified operational gaps |
| [operations/moomoo-opend-macos.md](operations/moomoo-opend-macos.md) | Secure macOS command-line OpenD layout, launchd lifecycle, readiness, and upgrade procedure |

## Source layout

```text
src/
├── bootstrap.py          # composition root only
├── application/          # ports, DTOs, services
├── domain/               # pure domain
├── infrastructure/       # config, persistence, providers, system
└── interfaces/           # MCP adapters
```

Python imports are top-level (`application.*`, `domain.*`, `infrastructure.*`,
`interfaces.*`, `bootstrap`). Console entries are `uv run trading-partner-mcp`
for the MCP server, `uv run trading-partner-watchlist-sync` for an explicit
Watchlist-only refresh, and `uv run trading-partner-post-market-sync` for the
due-checked US post-market account plus Watchlist job. Active Monitoring rules
are evaluated by an external scheduler through `uv run trading-partner-monitor-run`
with an explicit market cadence; the command is not a resident scheduler.
