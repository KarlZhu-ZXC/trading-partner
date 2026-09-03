# Trading Partner documentation

This directory contains only current product documentation, active future plans,
and release history. Completed implementation plans are folded into the phase
specifications and capability guide instead of being kept as parallel sources of
truth.

## Start here

| Document | Use it for |
|---|---|
| [../README.md](../README.md) | Product overview, installation, architecture, and common commands |
| [guide/quickstart-zh.md](guide/quickstart-zh.md) | 中文安装、MCP 接入、首次验证与安全边界 |
| [guide/mcp-host-setup.md](guide/mcp-host-setup.md) | Claude Desktop, Cursor, generic stdio, upgrade, and uninstall recipes |
| [guide/mcp-capability-boundary.md](guide/mcp-capability-boundary.md) | Complete public MCP contract, trust model, provider boundaries, and host usage |
| [guide/console-layout.md](guide/console-layout.md) | Specialist Console page hierarchy, shared controls, density, and interaction layout standard |
| [operations/local-console-and-maintenance.md](operations/local-console-and-maintenance.md) | Local Console, backup, maintenance, scheduler, and operational controls |
| [operations/known-issues.md](operations/known-issues.md) | Active defects, accepted constraints, and resolved issue index |
| [roadmap/global-roadmap-cn-us.md](roadmap/global-roadmap-cn-us.md) | Current product direction and future sequencing |
| [plans/reliability-usability-and-closure-plan.md](plans/reliability-usability-and-closure-plan.md) | Active reliability, usability, and decision-loop implementation plan |
| [plans/mcp-host-decision-loop.md](plans/mcp-host-decision-loop.md) | External MCP host attention read, result compaction, and schema-repair plan |
| [plans/refactor-slimming-backlog.md](plans/refactor-slimming-backlog.md) | Deferred defect follow-ups plus the backend/frontend slimming and consolidation backlog |
| [plans/moomoo-living-notes-workflow.md](plans/moomoo-living-notes-workflow.md) | Implemented provider-neutral external Observation intake and Decision-review boundary |

## Current specifications

| Document | Scope |
|---|---|
| [phases/phase1.md](phases/phase1.md) | Research memory, market/company facts, accounts, workflows, and the compact MCP foundation |
| [phases/phase2.md](phases/phase2.md) | Watchlist Hub, Risk Engine, Monitoring Hub, notifications, and Technical Engine |
| [phases/phase3.md](phases/phase3.md) | Cross-asset facts, company operating data, QuantConnect bridge, and judgment-to-plan controls |
| [phases/phase4.md](phases/phase4.md) | Implemented Trading Journal, Trade Cycle, performance, and behavior-review product loop |

## User and operator guides

| Document | Scope |
|---|---|
| [guide/quantconnect-free-bridge.md](guide/quantconnect-free-bridge.md) | Prepare LEAN code, run it manually in QuantConnect Free, and import the result JSON |
| [guide/mcp-host-setup.md](guide/mcp-host-setup.md) | Connect the installed core runtime to Claude Desktop, Cursor, or a generic stdio host |
| [guide/quickstart-zh.md](guide/quickstart-zh.md) | 中文快速开始与第一个安全研究问题 |
| [operations/moomoo-opend-macos.md](operations/moomoo-opend-macos.md) | Command-line OpenD lifecycle on macOS |
| [operations/phase3a-live-smoke.md](operations/phase3a-live-smoke.md) | Cross-asset free-provider smoke checks and expected typed degradation |
| [operations/mcp-host-decision-loop-smoke-2026-08-17.md](operations/mcp-host-decision-loop-smoke-2026-08-17.md) | External MCP host decision-loop read-only smoke receipts |

## Design and implementation records

`plans/` retains a small number of reviewed product contracts when their rationale is
still useful after implementation. Runtime guides and phase specifications remain the
authority for current operation.

| Document | Status |
|---|---|
| [plans/catalyst-agenda-and-scorecard-plan.md](plans/catalyst-agenda-and-scorecard-plan.md) | Implemented C0–C3/S0–S1 contract and bounded TDD record |
| [plans/schwab-sgov-cash-management.md](plans/schwab-sgov-cash-management.md) | Implemented Shadow Preview, SGOV-only automatic BUY scheduler, and confirmation-gated general live orders |
| [plans/shared-agent-runtime.md](plans/shared-agent-runtime.md) | Implemented Shared Agent Runtime contract and maturity backlog record |
| [plans/mcp-host-decision-loop.md](plans/mcp-host-decision-loop.md) | Planned: give external MCP hosts the Console/Agent decision loop without a 28th tool |
| [plans/agent-17-improvements.md](plans/agent-17-improvements.md) | Implemented Agent Rail reliability and usability improvement list |
| [plans/moomoo-living-notes-workflow.md](plans/moomoo-living-notes-workflow.md) | Implemented external Observation source contract, immutable revisions, and private-note review flow |

## Design audits

`audits/` holds dated design-QA captures and review records. They explain why a
Console or workflow looks the way it does at a point in time; they are not
usage instructions and are superseded by the phase specifications above.

| Document | Scope |
|---|---|
| [audits/design-qa.md](audits/design-qa.md) | Cross-page Console design-QA findings |
| [audits/portfolio-collapse-audit.md](audits/portfolio-collapse-audit.md) | Portfolio page collapse/refactor audit |
| [audits/monitor-workbench-2026-08-16/design-qa.md](audits/monitor-workbench-2026-08-16/design-qa.md) | Monitor Workbench design-QA capture (2026-08-16, with screenshots) |
| [audits/layout-reuse-2026-08-16](audits/layout-reuse-2026-08-16) | Console layout-reuse review capture (2026-08-16) |

## Release history

| Document | Scope |
|---|---|
| [releases/unreleased.md](releases/unreleased.md) | Current unreleased changes |
| [releases/v0.5.1.md](releases/v0.5.1.md) | v0.5.1 portable MCP onboarding release |
| [releases/v0.6.0.md](releases/v0.6.0.md) | v0.6.0 agent runtime, decision operations, and execution controls release |
| [releases/v0.5.0.md](releases/v0.5.0.md) | v0.5.0 release record |
| [releases/v0.4.0.md](releases/v0.4.0.md) | v0.4.0 release record |
| [releases/v0.2.0.md](releases/v0.2.0.md) | v0.2.0 release record |

Historical release notes intentionally retain the terminology and public schema
versions that were true when those releases shipped. They are not current usage
instructions.

## Examples and upstream references

- `examples/manual-holdings.v1.csv` and `examples/manual-watchlist.v1.csv` are
  strict manual-source templates.
- `examples/quantconnect-free-hourly-template.py` is the starter LEAN strategy.
- [contracts/observation-source-v1.schema.json](contracts/observation-source-v1.schema.json)
  is the closed full-text Local Observation Bridge contract.
- [references/tradingview-internal-api-reference.md](references/tradingview-internal-api-reference.md)
  records the reviewed unofficial TradingView account-session endpoints, their
  security boundary, and a staged read-only integration direction.
- `references/` records pinned upstream projects used as design references only;
  they are not runtime dependencies.

Runtime research data, broker exports, generated validation artifacts, audit
captures, and private reports live under gitignored `data/` or `artifacts/`. They
are not product documentation and must not be committed.
