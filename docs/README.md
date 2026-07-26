# Trading Partner — Documentation Index

Root-level markdown is limited to `README.md` (product overview) and `AGENTS.md`
(agent operating rules). Current phase specifications are consolidated. A small
`plans/` area holds review-stage proposals only; accepted conclusions are folded
back into the phase specification instead of accumulating permanent design notes.

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
├── plans/
│   └── phase3a-formal-futures-cross-asset-plan.md
├── operations/
│   ├── known-issues.md
│   └── phase3a-live-smoke.md
├── releases/
│   └── unreleased.md
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

## Implementation plans and acceptance records

| Document | Purpose |
|---|---|
| [plans/phase3a-formal-futures-cross-asset-plan.md](plans/phase3a-formal-futures-cross-asset-plan.md) | Approved free-provider design and Phase 3A implementation/acceptance record |
| [plans/phase3b-peer-comparison-plan.md](plans/phase3b-peer-comparison-plan.md) | Proposed caller-specified A-share/US peer-comparison fact-package design for review |
| [plans/phase3d-judgment-plan-controls.md](plans/phase3d-judgment-plan-controls.md) | Phase 3D Trade Plan, sizing, Risk v2, and Monitoring v2 implementation/acceptance record |
| [plans/cross-cutting-architecture-hardening-plan.md](plans/cross-cutting-architecture-hardening-plan.md) | Implemented Automation/idempotency hardening and MCP/bootstrap/provider modularization; authenticated host binding remains external |
| [plans/mcp-surface-reduction-plan.md](plans/mcp-surface-reduction-plan.md) | Implemented compact v2 reduction from 52 legacy tools to the sole 28-tool runtime surface, with discriminated schemas and permission separation; the compatibility profile was removed |

## Operations

| Document | Purpose |
|---|---|
| [operations/known-issues.md](operations/known-issues.md) | Ordered cross-phase defect queue for Moomoo, Reddit, and future verified operational gaps |
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
Formal futures definitions and EOD statistics are explicitly synchronized through
`uv run trading-partner-futures-sync`; it is read-only with respect to broker state.
