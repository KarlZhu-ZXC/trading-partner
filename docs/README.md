# Trading Partner documentation

This directory contains one current product specification, focused user/operator
guides, a future-only roadmap, machine-readable contracts, and release history.
Completed design ledgers and audits are removed after their lasting rules reach the
current specification, agent instructions, or an operator guide.

## Start here

| Document | Use it for |
|---|---|
| [../README.md](../README.md) | Product overview, installation, architecture, and common commands |
| [product.md](product.md) | Current product model, capability boundary, technical scope, and exclusions |
| [guide/quickstart-zh.md](guide/quickstart-zh.md) | 中文安装、MCP 接入、首次验证与安全边界 |
| [guide/mcp-host-setup.md](guide/mcp-host-setup.md) | Claude Desktop, Cursor, generic stdio, upgrade, and uninstall recipes |
| [guide/mcp-capability-boundary.md](guide/mcp-capability-boundary.md) | Progressive MCP discovery, exact call contracts, trust model and host usage |
| [guide/research-chart-valuation.md](guide/research-chart-valuation.md) | 图表到研究草稿、估值假设版本与判断校准 |
| [guide/console-design-system.md](guide/console-design-system.md) | Current Console hierarchy, shared controls, density, and interaction standard |
| [operations/local-console-and-maintenance.md](operations/local-console-and-maintenance.md) | Console, backup, maintenance, scheduler, and operational controls |
| [operations/known-issues.md](operations/known-issues.md) | Active defects and accepted operational constraints |
| [roadmap/global-roadmap-cn-us.md](roadmap/global-roadmap-cn-us.md) | Prioritized evolution proposal, open-source evidence, acceptance gates, and deferred integrations |

## User and operator guides

| Document | Scope |
|---|---|
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

- [Root agent guide](../AGENTS.md) owns cross-cutting invariants, architecture, and verification.
  Its task-specific details live in [product contracts](../.agents/references/product-contracts.md),
  [Agent runtime](../.agents/references/agent-runtime.md), and
  [Console rules](../.agents/references/console.md); load them only for affected work.
- `product.md` owns the concise current product boundary. Exact public schemas live
  in the capability guide and code.
- `guide/` and `operations/` own current user/operator instructions only.
- `roadmap/` owns genuinely deferred directions.
- `releases/` preserves version-specific historical truth.
- Completed plans, phase ledgers, dated local paths, screenshots, test counts, and
  smoke receipts are removed instead of becoming a second current specification.

Runtime research data, broker exports, generated validation artifacts, audit captures,
and private reports live under gitignored `data/` or `artifacts/`. They must not be
committed.
