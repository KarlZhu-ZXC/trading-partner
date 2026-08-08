# Contributing to Trading Partner

Thanks for helping improve Trading Partner. Contributions are welcome when they
preserve the project's local-first, evidence-first, and non-executing boundary.

## Before you start

- Search existing issues and discussions before opening a duplicate.
- Use an issue for a new Provider, public schema change, database migration, or
  cross-cutting feature before writing code.
- Never commit API keys, broker tokens, account identifiers, portfolio exports,
  personal research data, or captured Provider payloads.
- Keep `InvestmentCase`, `case_id`, and related names only where the documented
  compatibility boundary requires them. User-facing language is Research Subject
  in English and 标的、研究标的、研究档案 in Chinese.
- Do not add order placement or imply that derived facts are forecasts.

## Development setup

Requirements are Python 3.13, [uv](https://docs.astral.sh/uv/), and Node.js 22.13+
for Console changes.

```bash
git clone https://github.com/KarlZhu-ZXC/trading-partner.git
cd trading-partner
uv sync
cp .env.example .env
uv run alembic upgrade head
```

The default configuration should remain useful without private broker credentials.
Tests must use deterministic fakes or redacted, repository-safe fixtures.

## Making a change

1. Keep the change focused and preserve the domain/application/infrastructure/interface
   boundaries described in [`AGENTS.md`](../AGENTS.md).
2. Add the smallest useful regression test. Avoid duplicating Provider Router,
   Vendor Chain, or schema matrices when one representative contract proves the rule.
3. Update active documentation when behavior, configuration, public MCP schema, or
   an operational workflow changes.
4. Run the checks that match the change.

Backend baseline:

```bash
uv run ruff check .
uv run mypy src
uv run pytest
```

Console baseline:

```bash
cd console
npm ci
npm run lint
npm test
```

Packaging and security checks used by CI are listed in the README's Development
section.

## Pull requests

A pull request should explain:

- the user problem and implemented boundary;
- what changed and what deliberately did not change;
- data provenance, freshness, fallback, and failure semantics when applicable;
- database and compatibility impact;
- the exact validation commands run.

Keep secrets and personal data out of screenshots, logs, test output, and commit
history. By contributing, you agree that your contribution is licensed under the
repository's Apache License 2.0.
