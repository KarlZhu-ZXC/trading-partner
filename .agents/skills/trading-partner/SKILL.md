---
name: trading-partner
description: "Use Trading Partner MCP for investment views, portfolio context, and sourced market facts. Not for repository development."
---

# Trading Partner

Durable investment judgment and portfolio context, supported by sourced market facts.

## Discover, then call

1. If the capability is unknown, call `capability_discover()` for the compact catalog.
2. Discover an operation with `capability_discover(tool="portfolio_get", operation="positions")`.
   Omit `operation` to list its choices. Reuse known schemas.
3. Follow `call_tool`. For `capability_read`/`capability_write`, pass the original
   capability as `tool` and the full original inputs as `arguments`, for example:
   `capability_read(tool="portfolio_get", arguments={"request":{"operation":"positions"}})`.
   Writes also require `confirmation` equal to the original capability name and the
   user's explicit authorization. Dedicated tools take their original inputs directly.

Only nine entry tools are published. Business names in records or next-read hints
identify catalog capabilities; discover their route instead of calling an unpublished tool.

Discovery reads metadata only. It never refreshes accounts, calls a Provider,
creates a candidate, or authorizes an action. Console retains complete schemas and
full local results; MCP result compaction must never determine Console completeness.

## Complete the requested task

Use the requested facts or workflow and report the result with material coverage gaps.
A read request does not require a full health, inbox, portfolio, or research sweep.
Discover only missing schemas; reuse results while their freshness and versions remain
suitable. For an explicitly authorized exact write, prepare and carry it through the
existing gate without asking for the same decision again. Clarify an ambiguous target
or missing authorization; continue independent reads while waiting.

## Always preserve

- Trust `data` only when `ok=true`; retain sources, timestamps, freshness,
  degradation, warnings, missing data and native currencies. Never invent facts.
- Research Subject defines scope; Thesis holds judgment; Trade Plan holds intent.
  Candidate Confirm/Reject/Withdraw requires the user's exact decision. Relay an
  explicit chat decision with `reviewed_by="user"`, `submitted_via="mcp_chat"`
  and `authorization_note`; never select the outcome autonomously.
- Ordinary portfolio reads are durable-only. Refresh upstream only when requested.
- An order requires its own exact unexpired preview and explicit submit/cancel
  authorization. Research confirmation is not order authorization. Never retry an
  unknown submit outcome. The installed SGOV scheduler exception grants no MCP orders.
- Distinguish fact, derivation, interpretation, plan, submitted order and fill.
  Never expose credentials or raw Provider payloads. Never automate Moomoo's UI.
- Daily recovery: `system_health`, then `research_get/attention`.

## Read details only when relevant

- Quotes, technicals, sessions, or proxies: [market data](references/market-data.md).
- Research, Candidate decisions, or Observation attribution: [research](references/research.md).
- Accounts, performance, monitoring, or broker actions: [portfolio and orders](references/portfolio-orders.md).
- Configuring an MCP host: [host setup](references/host-setup.md).
- Presenting analysis: [output semantics](references/output.md).

For a concrete stock/ETF investment decision, also apply the owner's
`bossmo-trading-discipline` when available. If unavailable, retain the four-scenario
boundary: UPSIDE, SIDEWAYS, PULLBACK, INVALIDATION, each with an action or NO_ACTION.
Software work and generic facts do not trigger it.
Repository development rules belong to `AGENTS.md`, not routine MCP fact queries.
