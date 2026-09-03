# Trading Partner documentation

This directory contains current product documentation, the durable roadmap,
operator runbooks, machine-readable contracts, and release history. Completed plans,
dated smoke receipts, and superseded UI audits are removed after their lasting rules
are folded into `AGENTS.md`, a Phase specification, or an operator guide.

## Start here

| Document | Use it for |
|---|---|
| [../README.md](../README.md) | Product overview, installation, architecture, and common commands |
| [guide/quickstart-zh.md](guide/quickstart-zh.md) | 中文安装、MCP 接入、首次验证与安全边界 |
| [guide/mcp-host-setup.md](guide/mcp-host-setup.md) | Claude Desktop, Cursor, generic stdio, upgrade, and uninstall recipes |
| [guide/mcp-capability-boundary.md](guide/mcp-capability-boundary.md) | Complete public MCP contract, trust model, provider boundaries, and host usage |
| [guide/console-layout.md](guide/console-layout.md) | Current Console hierarchy, shared controls, density, and interaction standard |
| [operations/local-console-and-maintenance.md](operations/local-console-and-maintenance.md) | Console, backup, maintenance, scheduler, and operational controls |
| [operations/known-issues.md](operations/known-issues.md) | Active defects and accepted operational constraints |
| [roadmap/global-roadmap-cn-us.md](roadmap/global-roadmap-cn-us.md) | Current direction and genuinely deferred integrations |

## Implemented specifications

| Document | Scope |
|---|---|
| [phases/phase1.md](phases/phase1.md) | Research memory, market/company facts, accounts, workflows, and compact MCP foundation |
| [phases/phase2.md](phases/phase2.md) | Watchlist Hub, Risk Engine, Monitoring Hub, notifications, and Technical Engine |
| [phases/phase3.md](phases/phase3.md) | Cross-asset facts, company operating data, QuantConnect bridge, and plan controls |
| [phases/phase4.md](phases/phase4.md) | Journal, Observations, Trade Cycles, performance, and behavior review |

## User and operator guides

| Document | Scope |
|---|---|
| [guide/quantconnect-free-bridge.md](guide/quantconnect-free-bridge.md) | Prepare LEAN code, run it manually in QuantConnect Free, and import result JSON |
| [operations/moomoo-opend-macos.md](operations/moomoo-opend-macos.md) | Command-line OpenD lifecycle on macOS |
| [operations/phase3a-live-smoke.md](operations/phase3a-live-smoke.md) | Current cross-asset free-provider smoke runbook and typed degradation |
| [contracts/observation-source-v1.schema.json](contracts/observation-source-v1.schema.json) | Closed full-text Local Observation Bridge contract |

## Release history

| Document | Scope |
|---|---|
| [releases/unreleased.md](releases/unreleased.md) | Current unreleased changes |
| [releases/v0.6.0.md](releases/v0.6.0.md) | v0.6.0 agent runtime, decision operations, and execution controls |
| [releases/v0.5.1.md](releases/v0.5.1.md) | v0.5.1 portable MCP onboarding release |
| [releases/v0.5.0.md](releases/v0.5.0.md) | v0.5.0 Research Subject and monitoring release |
| [releases/v0.4.0.md](releases/v0.4.0.md) | v0.4.0 release record |
| [releases/v0.2.0.md](releases/v0.2.0.md) | v0.2.0 release record |

Historical release notes retain the terminology and schema versions that were true
when those releases shipped. They are not current usage instructions.

## Documentation lifecycle

- `AGENTS.md` owns agent-facing invariants, safety gates, architecture, and verification.
- `phases/` owns implemented contracts; it does not act as a progress diary.
- `guide/` and `operations/` own current user/operator instructions only.
- `roadmap/` owns genuinely deferred directions.
- `releases/` preserves version-specific historical truth.
- Completed plans, dated local paths, screenshots, test counts, and smoke receipts are
  removed instead of becoming a second current specification.

Runtime research data, broker exports, generated validation artifacts, audit captures,
and private reports live under gitignored `data/` or `artifacts/`. They must not be
committed.
