# Trading Partner

**The local-first memory and monitoring layer for serious investment conversations.**

[![CI](https://github.com/KarlZhu-ZXC/trading-partner/actions/workflows/quality.yml/badge.svg)](https://github.com/KarlZhu-ZXC/trading-partner/actions/workflows/quality.yml)
[![Release](https://img.shields.io/github/v/release/KarlZhu-ZXC/trading-partner)](https://github.com/KarlZhu-ZXC/trading-partner/releases)
[![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Buy me a coffee](https://img.shields.io/badge/Buy_me_a_coffee-PayPal-0070BA?logo=paypal&logoColor=white)](https://paypal.me/xczhu)

<p align="center">
  <img src="docs/assets/readme/hero.png" alt="Trading Partner is a local-first investment judgment companion for A-shares, US and Korean markets" width="100%" />
</p>

Most AI investment chats can summarize today's market. They do not remember why you
bought, what would invalidate the idea, how the plan changed, or whether new evidence
contradicts last month's judgment.

Trading Partner gives Codex and other
[Model Context Protocol](https://modelcontextprotocol.io/) hosts that missing layer:
durable research memory, read-only portfolio context, provider-backed facts, and
transition-aware monitoring—without giving the model permission to trade.

> **Ask:** “Review my gold position. What changed since the original thesis, and
> what evidence would make the current plan invalid?”
>
> **Trading Partner restores:** the confirmed position, Research Subject, competing
> Theses, Trade Plan, latest market facts, Monitor observations, provenance, and data
> quality warnings. The AI host interprets and challenges them in the conversation.

<p align="center">
  <a href="#product-tour"><strong>See the product flow</strong></a>
  ·
  <a href="#quick-start"><strong>Quick start</strong></a>
  ·
  <a href="docs/guide/mcp-capability-boundary.md"><strong>Capability boundary</strong></a>
</p>

<p align="center">
  <sub>If this is the investment workflow you want AI hosts to support, a ⭐ helps more people find the project.</sub>
</p>

<a id="product-tour"></a>
## <img src="docs/assets/readme/sections/why.svg" alt="" width="24" /> 60-second product tour

```text
You ask in Codex
    ↓
Trading Partner restores your portfolio, Watchlist, research history, and active plan
    ↓
Provider-backed facts arrive with source, timestamp, freshness, and typed warnings
    ↓
Codex compares the new evidence with PRIMARY, SUB, COMPETITOR, and BEAR Theses
    ↓
Deterministic or composite Monitors persist every observation and notify only on change
    ↓
You explicitly confirm durable judgments and every live broker order
```

| Built for | What it changes |
|---|---|
| Long-horizon research | One durable file keeps the question, evidence, Thesis history, decisions, and open questions together. |
| Portfolio-aware conversations | The host can use persisted positions, transactions, Watchlists, performance, and risk without silently refreshing a broker. |
| Judgment monitoring | Price, technical, relative-strength, fundamental, macro, sentiment, and portfolio-risk conditions can be evaluated on a schedule. |
| Trustworthy answers | Facts retain provenance, observation time, freshness, fallback basis, and typed failures instead of hiding uncertainty. |

The durable research object is a **Research Subject**. It may represent a company,
ETF, theme, macro question, catalyst, or portfolio concern—not only an equity.

## <img src="docs/assets/readme/sections/capabilities.svg" alt="" width="24" /> Core capabilities

- **Research memory:** Research Subjects, multiple Thesis threads, journals,
  decisions, evidence, Catalyst Agenda, Judgment Scorecards, Challenge Reviews,
  and versioned Trade Plans.
- **Portfolio context:** Schwab, Moomoo OpenD, or strict CSV holdings; durable
  transactions, append-only Cycle overrides, deterministic Trade Cycles, Daily Equity,
  native-currency returns, behavior recurrence Reviews, Watchlists, performance attribution,
  exposure, and Risk Engine v2.
- **Market facts:** A-share, US, Korea Exchange, macro, company filings, sentiment,
  selected futures, and explicitly labelled OTC/cross-asset references.
- **Monitoring:** deterministic facts plus optional bounded composite judgment,
  immutable run diagnostics, and transition-aware Telegram delivery.
- **Technical analysis:** shared daily/weekly indicators, market structure,
  support/resistance, candlestick patterns, and auditable charts.
- **Safe workflows:** deep dives, catalyst and market reviews, peer comparisons,
  deterministic Trade Retro, a manual QuantConnect Free code/result bridge, and
  confirmation-gated US stock/ETF orders.

## <img src="docs/assets/readme/sections/safety.svg" alt="" width="24" /> Safety boundary

Trading Partner is a research-first service, not a general autonomous trading agent.

Its Schwab order boundary is deliberately narrow: explicitly confirmed, single-leg
US stock/ETF orders through one grouped `broker_order_manage` tool. There is no
generic broker request, order replacement, or options/complex-order builder. Every
live submit consumes a short-lived durable preview and exact user authorization;
an unknown Provider response is recorded and never retried automatically. The
QuantConnect Free bridge only prepares code and imports a result after you operate
the web UI yourself.

Technical outputs are derived facts—not forecasts, strategies, or trade signals.
Ordinary portfolio questions read durable snapshots; a broker is refreshed only
when explicitly requested.

<a id="quick-start"></a>
## <img src="docs/assets/readme/sections/quickstart.svg" alt="" width="24" /> Quick start

Requirements: [uv](https://docs.astral.sh/uv/)

Install the core MCP from the versioned project tag, then initialize its private
per-user runtime:

```bash
uv tool install --python 3.13 \
  "git+https://github.com/KarlZhu-ZXC/trading-partner.git@v0.6.0"
trading-partner-init
```

`trading-partner-init` creates an owner-only `runtime.env`, initializes or upgrades
the SQLite database idempotently, and prints the absolute MCP command and arguments
for your host. No API key or broker login is required to start; add optional
Provider credentials only to that generated file. Tested host recipes are in the
[MCP host setup guide](docs/guide/mcp-host-setup.md).

For a source checkout or Console development:

```bash
git clone https://github.com/KarlZhu-ZXC/trading-partner.git
cd trading-partner
uv sync
cp .env.example .env
uv run alembic upgrade head
uv run trading-partner-mcp
```

Optional runtime extras are scoped to `accounts-moomoo`, `accounts-schwab`, `chart`,
`company-pdf`, and `console`; `trading-partner[all]` installs all of them. Node.js
22.13 or newer is required only for the optional Console frontend.

After connecting, call `system_health` first. It separates live probes from
configuration-only checks and embeds a durable-only Data Quality Center, so a
healthy MCP process does not imply that every optional external provider is
configured or reachable.

## <img src="docs/assets/readme/sections/capabilities.svg" alt="" width="24" /> Local Console

The optional Console is a loopback-only control room over the same application
services and the 27-capability MCP registry, with an opt-in Shared Agent rail:

```bash
# Terminal 1
uv sync --extra console
uv run trading-partner-console

# Terminal 2
cd console
npm ci
npm run dev
```

Open `http://localhost:3000`. Pages cover Research Subjects and Theses, Trade Plans,
Trade Retro, the Catalyst Agenda, Judgment Scorecards, Monitors, holdings, activity,
performance, risk, operational receipts, and a capability workbench. Page loads are
durable-only and never contact Providers; writes and external actions keep the
underlying confirmation, expected-version, actor, and idempotency gates. The Console
has no live order control.

See the [local Console and maintenance guide](docs/operations/local-console-and-maintenance.md)
for page-by-page detail, LAN mode, and scheduler setup.

## <img src="docs/assets/readme/sections/operations.svg" alt="" width="24" /> Operational commands

```bash
# Exact Watchlist refresh
uv run trading-partner-watchlist-sync

# Due-checked US post-market account, transaction, Unlinked Activity, and Watchlist refresh
uv run trading-partner-post-market-sync

# Freeze Trade Plans and Decision Records before a week, audit it after
uv run trading-partner-retro prepare --start 2026-08-10 --end 2026-08-17 --idempotency-key retro-plan-2026-w33
uv run trading-partner-retro run --start 2026-08-10 --end 2026-08-17 --idempotency-key retro-run-2026-w33 --export-obsidian
uv run trading-partner-retro weekly --export-obsidian
uv run trading-partner-retro history

# Diagnose Schwab token age without opening a browser
uv run trading-partner-schwab-auth status

# Force one market-close cadence, or run all due Monitor groups
uv run trading-partner-monitor-run --cadence US_POST_MARKET
uv run trading-partner-monitor-run due

# Install the token-free unified hourly Monitor dispatcher
uv run trading-partner-monitor-scheduler install

# Optional Telegram delivery: inspect, test, or retry the durable outbox
uv run trading-partner-monitor-notifications status
uv run trading-partner-monitor-notifications test
uv run trading-partner-monitor-notifications flush

# Explicitly refresh futures definitions or catalyst calendars
uv run trading-partner-futures-sync --product CME:GC --trade-date 2026-07-24
uv run trading-partner-catalyst-sync sync --fred-release-id 10 --window-days 30 --notify --flush
uv run trading-partner-catalyst-sync status

# Inspect retention, back up, or prune expired caches
uv run trading-partner-maintenance status
uv run trading-partner-maintenance backup
uv run trading-partner-maintenance prune-cache --retention-days 30
```

These commands never execute an order. Broker-statement reconciliation and other
owner-only CLIs are documented in the
[maintenance guide](docs/operations/local-console-and-maintenance.md).

Telegram delivery is opt-in: create a bot with `@BotFather`, message it once, then
set `NOTIFICATIONS_ENABLED`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID` in the
gitignored `.env`. The optional Telegram Agent poller
(`uv run trading-partner-agent telegram ...`) is independent of Monitor delivery;
the maintenance guide records the delivery, retry, and LLM-boundary contracts.

## <img src="docs/assets/readme/sections/architecture.svg" alt="" width="24" /> Architecture

```text
src/
├── bootstrap.py          # composition root
├── application/          # ports, DTOs, use cases
├── domain/               # pure domain model
├── infrastructure/       # composition builders, persistence, providers, config
└── interfaces/           # MCP, Console, and CLI adapters
```

The domain has no dependency on MCP, SQLAlchemy, Alembic, settings, or provider
SDKs. Provider payloads are normalized at the infrastructure boundary, and only
`src/bootstrap.py` connects infrastructure ports to application services. Layer
responsibilities, provider routing policy, and the boundary tests that enforce
them are documented in [AGENTS.md](AGENTS.md).

## <img src="docs/assets/readme/sections/data.svg" alt="" width="24" /> Data and secrets

- Static secrets live in the project-root `.env`, which is gitignored.
- Rotating provider OAuth tokens live under `data/secrets/`, which is also gitignored.
- Installed tools derive every mutable token, lock, attachment, backup, and
  Observation path from the owner-only runtime directory created by
  `trading-partner-init`; package installation directories contain code and static
  defaults only.
- Owner-specific account-basis checkpoints belong in the runtime
  `data/secrets/account_basis_checkpoints.yaml`; the repository ships only an empty
  example and never packages account references, quantities, costs, or document hashes.
- Durable research and account state stays in your configured local database.
- Credentials are redacted from logs, MCP envelopes, exceptions, and audit payloads.
- Each user should supply their own provider credentials and maintain their own data
  store; never share an `.env` or broker token directory.

## <img src="docs/assets/readme/sections/documentation.svg" alt="" width="24" /> Documentation

- [MCP capability and trust boundary](docs/guide/mcp-capability-boundary.md)
- [MCP host setup: Claude Desktop, Cursor, and generic stdio](docs/guide/mcp-host-setup.md)
- [Local Console and maintenance](docs/operations/local-console-and-maintenance.md)
- [QuantConnect Free manual validation](docs/guide/quantconnect-free-bridge.md)
- [Documentation index](docs/README.md)
- [Known operational issues](docs/operations/known-issues.md)
- [Product roadmap](docs/roadmap/global-roadmap-cn-us.md)
- [Contributing guide](.github/CONTRIBUTING.md)
- [Security policy](SECURITY.md)

## <img src="docs/assets/readme/sections/development.svg" alt="" width="24" /> Development

```bash
uv run ruff check .
uv run mypy src
uv run pytest
uv run python scripts/smoke_isolated_wheel.py
uv run pip-audit
uv run cyclonedx-py environment .venv/bin/python --pyproject pyproject.toml -o /tmp/trading-partner.cdx.json
cd console && npm ci && npm audit && npm run lint && npm test
gitleaks git --redact --no-banner --log-opts=HEAD
```

Upstream projects under `references/` are design references only and are not runtime
dependencies. See each `UPSTREAM.md` for its pinned revision and attribution.

## <img src="docs/assets/readme/sections/support.svg" alt="" width="24" /> Support

Questions, bug reports, Provider proposals, and focused contributions are welcome
through [GitHub Issues](https://github.com/KarlZhu-ZXC/trading-partner/issues).
Please read the [contributing guide](.github/CONTRIBUTING.md) and keep credentials
and personal financial data out of public reports.

If Trading Partner is useful to you, consider
[buying me a coffee through PayPal](https://paypal.me/xczhu).

[![Buy me a coffee](https://img.shields.io/badge/Buy_me_a_coffee-PayPal-0070BA?logo=paypal&logoColor=white)](https://paypal.me/xczhu)

## <img src="docs/assets/readme/sections/license.svg" alt="" width="24" /> License

Licensed under [Apache License 2.0](LICENSE). Third-party attribution is recorded in
[NOTICE](NOTICE).
