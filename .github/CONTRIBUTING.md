# Contributing to Trading Partner

Thanks for helping improve Trading Partner. Contributions are welcome when they
preserve the project's local-first, evidence-first, and non-executing boundary.

## Before you start

- Search existing issues and discussions before opening a duplicate.
- Discuss the scope of a new Provider, public schema, migration or cross-cutting
  feature before implementation. An existing task or conversation is sufficient;
  a separate issue or design document is optional.
- Never commit API keys, broker tokens, account identifiers, portfolio exports,
  personal research data, or captured Provider payloads.
- Keep `InvestmentCase`, `case_id`, and related names only where the documented
  compatibility boundary requires them. User-facing language is Research Subject
  in English and 标的、研究标的、研究档案 in Chinese.
- Preserve the existing order-authorization boundary; derived facts are not forecasts.

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
2. Reuse existing regression coverage. Add a test for new behavior or an uncovered
   bug; copy-only changes need no new test. Avoid duplicate business flows and
   assertions that merely restate implementation details.
3. Update only the documentation whose behavior, contract or instructions changed.
4. Run the checks that match the change. During development, prefer the exact test
   node or module and lint only changed files. Do not repeatedly run the full suite.

Typical backend inner loop:

```bash
uv run ruff check src/path/to/changed.py tests/path/to/test_changed.py
uv run pytest tests/path/to/test_changed.py -q
```

Broad backend checks, coverage, isolated-wheel smoke, dependency audits, SBOM and
secret scans normally run in CI. Expand local verification only for a concrete
cross-cutting risk; migrations use disposable databases. The selection rules live
in [AGENTS.md](../AGENTS.md#verification).

Console quick checks require installed dependencies but no app build or server:

```bash
cd console
npm test
# Or select one affected module:
node --test tests/review-due-date.test.mjs
```

For changed UI interactions, build once into an isolated directory, then run the
affected browser spec. Reuse that build for test-only edits:

```bash
CONSOLE_BUILD_DIR=.next-integration npm run build
CONSOLE_BUILD_DIR=.next-integration npm run test:e2e -- e2e/note-desk.spec.ts
```

`npm run test:render` runs the server-rendered route checks against an existing
build. `npm run test:full` builds and runs all three layers; CI uses this command.
Run lint on changed files; a successful build already includes TypeScript checking.

## Pull requests

A pull request should explain:

- the problem and resulting behavior;
- relevant validation and material limitations;
- data, authorization, migration or compatibility impact, when applicable.

Keep secrets and personal data out of screenshots, logs, test output, and commit
history. By contributing, you agree that your contribution is licensed under the
repository's Apache License 2.0.
