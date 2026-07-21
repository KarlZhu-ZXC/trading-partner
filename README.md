# Trading Partner

**A local-first investment judgment companion for A-shares and US markets.**

[![CI](https://github.com/KarlZhu-ZXC/trading-partner/actions/workflows/quality.yml/badge.svg)](https://github.com/KarlZhu-ZXC/trading-partner/actions/workflows/quality.yml)
[![Release](https://img.shields.io/github/v/release/KarlZhu-ZXC/trading-partner)](https://github.com/KarlZhu-ZXC/trading-partner/releases)
[![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Buy me a coffee](https://img.shields.io/badge/Buy_me_a_coffee-PayPal-0070BA?logo=paypal&logoColor=white)](https://paypal.me/xczhu)

<p align="center">
  <img src="docs/assets/readme/hero.png" alt="Trading Partner is a local-first investment judgment companion for A-shares and US markets" width="100%" />
</p>

Trading Partner is a durable research and portfolio context service exposed through
the [Model Context Protocol](https://modelcontextprotocol.io/). Connect it to Codex
or another MCP host and the conversation can retrieve your positions, Watchlist,
past research, Investment Cases, thesis revisions, challenge reviews, monitoring
events, and current market facts without rebuilding context from scratch.

The MCP supplies structured facts and durable state. Your AI host remains responsible
for interpretation, debate, and the final answer.

## <img src="docs/assets/readme/sections/why.svg" alt="" width="24" /> Why Trading Partner?

Most investment chats forget what you believed last month, why you bought something,
which evidence would invalidate the thesis, and whether a new claim contradicts an
older one. Trading Partner gives the conversation a persistent research memory and a
fact layer designed to challenge decisions over time—not merely summarize today's
market.

Every precise result carries provenance such as source, observation time, freshness,
basis, and typed warnings. Missing or stale data is disclosed instead of fabricated.

## <img src="docs/assets/readme/sections/capabilities.svg" alt="" width="24" /> What it can do

- Maintain Investment Cases, research state, thesis history, journals, decisions, and
  contrary-first Challenge Reviews.
- Resolve A-share and US instruments and retrieve provider-backed market,
  fundamental, filing, macro, news, sentiment, and prediction-market context.
- Read positions and transactions from Schwab, Moomoo OpenD, or strict manual CSV
  sources; account refresh is explicit and read-only.
- Persist Moomoo or CSV Watchlists with group and membership history.
- Analyze portfolio exposure, simulate additions, and run deterministic risk-policy
  checks without placing orders.
- Create durable price and portfolio-risk monitors with transition-only events.
- Produce shared A-share/US daily and weekly technical analysis, including indicators,
  market structure, support/resistance, candlestick patterns, and PNG charts.
- Retrieve free Yahoo COMEX/NYMEX continuous commodity-futures quotes and 1m–monthly
  bars with explicit non-spot and contract-roll warnings.
- Run repeatable deep-dive, catalyst, market-review, and portfolio-review workflows
  while keeping the AI host as the synthesizer.

## <img src="docs/assets/readme/sections/safety.svg" alt="" width="24" /> Safety boundary

Trading Partner is a research service, not a broker or autonomous trading agent.

It does **not** expose order placement, fills, paper trading, live execution, backtests,
or autonomous thesis confirmation. Technical outputs are derived facts—not forecasts,
strategies, or trade signals. Ordinary portfolio questions read durable snapshots;
broker refreshes happen only when explicitly requested.

## <img src="docs/assets/readme/sections/quickstart.svg" alt="" width="24" /> Quick start

Requirements:

- Python 3.13
- [uv](https://docs.astral.sh/uv/)

```bash
git clone https://github.com/KarlZhu-ZXC/trading-partner.git
cd trading-partner

uv sync
cp .env.example .env
uv run alembic upgrade head
```

Edit `.env` only for the providers and account sources you want to use. Safe defaults
work without broker credentials; unavailable optional providers return explicit
degradation metadata.

Start the local stdio MCP server:

```bash
uv run trading-partner-mcp
```

The repository includes a Codex project configuration in `.codex/config.toml`. For a
generic stdio MCP host, use the equivalent configuration:

```json
{
  "mcpServers": {
    "trading-partner": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/trading-partner",
        "run",
        "trading-partner-mcp"
      ]
    }
  }
}
```

After connecting, call `system_health` first. Then verify the specific market and
account providers you intend to use; a healthy MCP process does not imply that every
optional external provider is configured or reachable.

## <img src="docs/assets/readme/sections/operations.svg" alt="" width="24" /> Operational commands

```bash
# Exact Watchlist refresh
uv run trading-partner-watchlist-sync

# Due-checked US post-market account and Watchlist refresh
uv run trading-partner-post-market-sync

# Evaluate active monitors from cron, launchd, or Codex Automation
uv run trading-partner-monitor-run --cadence US_POST_MARKET
uv run trading-partner-monitor-run --cadence A_SHARE_POST_MARKET
```

These commands never execute an order.

## <img src="docs/assets/readme/sections/architecture.svg" alt="" width="24" /> Architecture

```text
src/
├── bootstrap.py          # composition root
├── application/          # ports, DTOs, use cases
├── domain/               # pure domain model
├── infrastructure/       # persistence, providers, configuration
└── interfaces/           # MCP adapters
```

```text
interfaces ───────→ application ───────→ domain
infrastructure ───→ application ports ─→ domain
```

The domain has no dependency on MCP, SQLAlchemy, Alembic, settings, or provider SDKs.
Provider payloads are normalized at the infrastructure boundary, and only
`src/bootstrap.py` composes the application.

## <img src="docs/assets/readme/sections/data.svg" alt="" width="24" /> Data and secrets

- Static secrets live in the project-root `.env`, which is gitignored.
- Rotating provider OAuth tokens live under `data/secrets/`, which is also gitignored.
- Durable research and account state stays in your configured local database.
- Credentials are redacted from logs, MCP envelopes, exceptions, and audit payloads.
- Each user should supply their own provider credentials and maintain their own data
  store; never share an `.env` or broker token directory.

## <img src="docs/assets/readme/sections/documentation.svg" alt="" width="24" /> Documentation

- [MCP capability and trust boundary](docs/guide/mcp-capability-boundary.md)
- [Documentation index](docs/README.md)
- [Known operational issues](docs/operations/known-issues.md)
- [Product roadmap](docs/roadmap/global-roadmap-cn-us.md)
- [Security policy](SECURITY.md)

## <img src="docs/assets/readme/sections/development.svg" alt="" width="24" /> Development

```bash
uv run ruff check .
uv run mypy src
uv run pytest
uv run python scripts/smoke_isolated_wheel.py
gitleaks git --redact --no-banner --log-opts=HEAD
```

Upstream projects under `references/` are design references only and are not runtime
dependencies. See each `UPSTREAM.md` for its pinned revision and attribution.

## <img src="docs/assets/readme/sections/support.svg" alt="" width="24" /> Support

If Trading Partner is useful to you, consider
[buying me a coffee through PayPal](https://paypal.me/xczhu).

[![Buy me a coffee](https://img.shields.io/badge/Buy_me_a_coffee-PayPal-0070BA?logo=paypal&logoColor=white)](https://paypal.me/xczhu)

## <img src="docs/assets/readme/sections/license.svg" alt="" width="24" /> License

Licensed under [Apache License 2.0](LICENSE). Third-party attribution is recorded in
[NOTICE](NOTICE).
