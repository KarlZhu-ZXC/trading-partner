# TradingAgents reference notes (Phase 1A)

## What is referenced

- Multi-agent research role separation ideas (bull / bear / risk critique style workflows)
- Research report structuring patterns
- The concept of orchestrated analysis stages

## What is explicitly not used as runtime

- TradingAgents package is **not** a Python dependency
- No imports from `tradingagents` appear under `src/`
  (`application`, `domain`, `infrastructure`, `interfaces`, `bootstrap`)
- MiniMax / Grok are not used as runtime dependencies either
- Phase 1A does not implement research workflows; those arrive in later phases

## Boundary

Trading Partner owns its own domain models (`InvestmentCase`, `Thesis`, Tool Envelope,
providers). Upstream is inspiration and attribution only, not a library fork.
