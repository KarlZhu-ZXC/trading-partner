# Trading Partner — Agent Guide

## Product intent

Trading Partner is a long-horizon investment judgment companion and the durable
system of record for the owner's reviewed investment views. Moomoo is the owner's
primary analysis and note-authoring surface; Trading Partner ingests immutable
Observation revisions, drafts a non-authoritative interpretation, compares it with
confirmed judgment and portfolio context, and records a Decision or `NO_ACTION` only
after explicit user review. Codex, Console, or another agent host may conduct that
review; market data and the remaining structured capabilities support the judgment
loop rather than define its starting point. The implemented Phase 1–4D boundary
covers A-share/US research,
Korea Exchange quote/technical monitoring,
accounts, Research Subjects, Watchlist Hub, Risk v2, Monitoring v2, versioned Trade
Plans, deterministic Position Sizing, and professional daily/weekly technical
analysis and narrowly confirmation-gated Schwab US stock/ETF order writes. The sole unattended order
exception is the dedicated installed SGOV cash-sweep scheduler detailed in the
linked product contracts —
this is **not** an automated backtest runner or general autonomous trading system.

Canonical product language is **Research Subject** in English and 标的、研究标的 or
研究档案 in Chinese, depending on whether the context emphasizes the object or its
durable file. `InvestmentCase`, `investment_case_*`, `case_id`, `case_type`,
`linked_case_ids`, and the opaque `case_` ID prefix are compatibility-boundary names,
not user-facing terminology. Equity means an actual stock Instrument only.

## Current source of truth

- Implemented scope is Phase 1–4D. The current runtime snapshot exposes the single
  9-tool `mcp_vnext_shadow` entry surface over 24 business capabilities, but the count is not a future product invariant:
  tools may be split, grouped, added, deprecated, or removed through an explicit
  compatibility migration when that improves intent discovery and workflow cohesion.
- Application version is read from `src/application/__init__.py`; the current database
  migration head is `0072_external_note_review_drafts`.
- This file and its linked `.agents/references/` files own agent-facing invariants. `docs/phases/` owns implemented product
  contracts, `docs/guide/` and `docs/operations/` own current instructions,
  `docs/roadmap/` owns genuinely deferred work, and `docs/releases/` owns historical
  version truth.
- Do not retain completed plans, dated smoke receipts, local-path screenshots, or
  superseded design audits as parallel specifications. Fold lasting rules into the
  appropriate current document, update `docs/README.md`, then delete the obsolete file.
- Historical release notes are intentionally immutable except for secret/privacy
  redaction or broken-link repair; do not rewrite old terminology as current behavior.

## Task execution and completion

Carry an implementation or fix through the requested behavior, relevant verification,
and correction of failures caused by the change. Make routine reversible choices
within scope without another approval. Preserve existing worktree edits. A follow-up
correction or status question steers the active task unless the user replaces it.
Stop when the requested outcome is verified, or report a concrete blocker and the
remaining work; do not stop merely because a first implementation exists.

Ask only when missing information changes the target, material behavior, or required
authorization. Continue independent authorized work while awaiting an answer. Explicit
user instructions take precedence over skill workflow preferences; they do not bypass
application validation, privacy boundaries, or the separate broker-order contract.
If an instruction blocks progress, identify its file and exact rule and explain the
specific action it blocks. Do not invent an approval gate from a recommendation.

Local edits and checks using disposable fixtures may proceed within the requested
scope. Verify unfamiliar commands before running them against data: a development
request alone does not authorize production migrations, private-content model calls,
broker refreshes, research confirmation, or orders. Existing exact authorization
should be relayed through its required gate without requesting it again.

Report the result, relevant validation, and remaining limitations concisely in the
user's language. Distinguish observed behavior from an untested expectation.

## Task-specific instructions

Read only the references relevant to the requested behavior, including its affected
cross-cutting contracts. These are maintained parts of this guide, not optional
alternatives to its boundaries. A documentation typo does not require a product audit.

| Task | Read when relevant |
|---|---|
| MCP, providers, research, Observations, accounts, risk, monitoring, orders, or runtime operations | [.agents/references/product-contracts.md](.agents/references/product-contracts.md); locate the relevant named section before reading detail |
| Built-in Agent conversation, tools, pending actions, model selection, or attachments | [.agents/references/agent-runtime.md](.agents/references/agent-runtime.md), plus affected capability contracts |
| Console UI, forms, controls, or frontend deployment | [.agents/references/console.md](.agents/references/console.md) and [design system](docs/guide/console-design-system.md) |
| Investment research or portfolio work through MCP | [.agents/skills/trading-partner/SKILL.md](.agents/skills/trading-partner/SKILL.md) |
| Installation, maintenance, or a product specification | [docs/README.md](docs/README.md); select the relevant guide or Phase |

## Boundaries that apply across tasks

- Research Subject defines stable scope; Thesis holds judgment; Trade Plan holds
  intent. Preserve exact user review, actor, version, and idempotency gates. Never
  autonomously select Candidate Confirm/Reject/Withdraw.
- An exact user decision in chat can authorize the corresponding research write;
  relay it with the required review metadata. It cannot authorize a broker order.
- Orders require their own exact unexpired preview and explicit submit/cancel
  authorization. Never retry an unknown submit outcome. The installed SGOV scheduler
  exception grants no general MCP/Agent order authority.
- Ordinary portfolio reads use durable snapshots. Staleness is disclosed, not an
  implicit instruction to refresh a broker. Preserve fact sources, times, currencies,
  basis, missing data, and warnings; do not invent facts or promote drafts to judgment.
- Private notes/account values never enter Git, fixtures, package data, or diagnostics.
  Never automate Moomoo's UI. Private-content model use retains explicit authorization
  and Contributor training opt-in where applicable.
- Apply BossMo/`strategy_v1` only to a concrete stock/ETF investment decision, covering
  UPSIDE, SIDEWAYS, PULLBACK, and INVALIDATION. Software work and generic facts do not
  trigger investment discipline.

## Architecture rules

1. Domain never imports MCP, SQLAlchemy, Alembic, Pydantic Settings, or providers.
2. Application never imports infrastructure or interfaces.
3. Infrastructure never imports interfaces. Interfaces only adapt protocols, validate
   inputs, and convert to DTOs.
4. Only `src/bootstrap.py` and the sanctioned `src/composition_root/` package
   connect application services to infrastructure. `bootstrap.py` stays the
   public façade (`ApplicationContainer`, `build_application`); bounded graph
   builders under `composition_root/` may import both layers and are enforced
   by the architecture boundary tests.
   `infrastructure/composition/` may build infrastructure-only bundles but must
   never import `application.services`.
   Application-only service/context bundles live in `application/runtime.py`;
   infrastructure resource ownership and deterministic composition overrides live
   in `infrastructure/composition/runtime.py`. Neither is a second composition root.
5. Provider raw payloads never cross the infrastructure boundary.
6. Precise numbers come from tool snapshots with source, time, freshness, and basis.
7. The sole FastMCP server directly composes compact capability adapters; do not
   reintroduce legacy tool registrars, handler-name lookup, or a second argument-model registry.
8. Discovered exact schemas and full Console schemas must preserve resolvable local
   `$ref` targets and closed operation variants. Lightweight MCP listing schemas are
   discovery hints only; the original strict runtime validators remain authoritative.

## Source layout

```text
src/
├── bootstrap.py          # composition-root façade
├── composition_root/     # bounded app+infra graph builders (with bootstrap.py)
├── application/
├── domain/
├── infrastructure/       # composition/, persistence/orm/, providers/, config/
└── interfaces/
```

Imports are top-level (`application.*`, `domain.*`, `infrastructure.*`,
`interfaces.*`, `bootstrap`). There is no `trading_partner` package layer.

Docs: `docs/README.md` indexes the roadmap, consolidated Phase specifications,
current guides/runbooks, contracts, and release history.

## Documentation placement

The root `README.md` is the project's simplified product manual, not a design
record. It stays limited to the product tour, capability summary, safety
boundary, quick start, operational command cheat sheet, and documentation
links. Do not grow it with design or implementation narrative.

Design and implementation content — architecture internals such as composition
bundles and ORM grouping, provider pacing/fallback rules, runtime semantics,
confirmation/idempotency contract detail, and capability contracts — belongs
in this guide or its task-specific references when it defines an agent-facing boundary, or in a dedicated page
under `docs/` (typically `docs/operations/` or `docs/guide/`) for
operator/user-facing detail. When the detail already exists here or under
`docs/`, link to it from the README instead of restating it. Every new `docs/`
page must be added to the `docs/README.md` index.
Completed implementation plans and dated audit/smoke artifacts are deleted after their
durable rules and evidence summary have moved to a Phase spec, guide, release note, or
this file. Do not create a new `docs/plans/` or `docs/audits/` archive merely to preserve
superseded prose; Git history already owns that record.

## Secrets and configuration

- Source checkouts may use project-root `.env`; installed hosts use the explicit
  owner-only `runtime.env` produced by `trading-partner-init`. Both are gitignored.
- Provider-managed rotating OAuth tokens may live only under the active
  `RUNTIME_ROOT/data/secrets/` (gitignored, owner-only). Only the provider SDK may
  create or update them; never copy tokens between applications.
- Never read, print, or paste real `.env`/`runtime.env` contents into chat, logs,
  tests, or commits.
- Use `.env.example` for key names and safe defaults.
- When adding an `AppSettings` environment key, update `.env.example` and add its
  safe default to the active local config without overwriting values. Secret keys
  must remain empty for the owner to fill.
- Redact API keys, tokens, and credentials in every output path.

## Coding conventions

- Python 3.13, `uv`, hatchling, `src/` layout.
- Codex owns architecture, naming, boundaries, and acceptance; delegate code
  implementation to grok Build when external implementation help is useful.
- The project-scoped `deterministic_coder` custom subagent (`gpt-5.6-luna`,
  `max`) is the preferred local executor for bounded deterministic code changes
  after Codex has fixed architecture, file/module ownership, public contracts,
  naming, and acceptance criteria. Assign it concrete files/responsibilities and
  remind it that other agents and user edits share the worktree. It must not make
  product or architecture decisions, broaden scope, commit, push, or perform
  external side effects unless that exact action is explicitly delegated. Codex
  reviews its diff and owns final verification and acceptance.
- Do not assign code implementation to Claude Code / MiniMax.
- Typed settings via `AppSettings`; project `.env` keys have no global prefix.
- Entity IDs: `<prefix>_<uuid7>` via `uuid6.uuid7()`.
- Instrument IDs: `<asset_type>:<market>:<symbol>`.
- Money and market values use `Decimal`, not binary floats.
- All datetimes are timezone-aware ISO 8601.

## Out of scope

```text
local/automated backtest engines, autonomous/unattended order execution except the
closed SGOV BUY cash-sweep scheduler
automated evidence ingestion, general-purpose/autonomous runtime LLM synthesis
outside enabled composite Monitor judgment and optional Trade Retro narration
order replacement, options/complex orders, short selling, Schwab API overnight orders
```

## Upstream

TradingAgents and a-stock-data are **reference only** (see `references/`). They are
not runtime dependencies. Do not add MiniMax or Grok as project runtime deps.

## Verification

Use progressive verification. Test effort must be proportional to the current
change; do not run every check after every edit.

- Inner loop: run only the exact affected test node/module and lint only changed
  Python files. Prefer commands such as
  `uv run pytest tests/unit/test_x.py::test_y -q` and
  `uv run ruff check path/to/changed.py tests/path/to/test_changed.py`.
- Feature checkpoint: run the directly affected test directories/modules. Do not
  add coverage, wheel builds, dependency audits, or the entire suite.
- Subagents/workers must not run repository-wide pytest, mypy, coverage, frontend
  builds, wheel smoke, or audits unless the parent explicitly delegates that one
  check. They report their focused commands and results to the main agent.
- The main agent owns broad verification when the affected contracts warrant it.
  Repeat a successful check only after relevant changes or new evidence that its
  result is insufficient; a changed shared dependency can invalidate earlier checks.
- Full coverage, isolated-wheel smoke, dependency audits, SBOM, and secret scans
  belong to CI/release verification unless the change directly affects that area
  or the user explicitly requests them.
- Documentation-only changes require formatting/diff checks, not pytest.

The final local backend checkpoint, when warranted, is:

```bash
uv run ruff check .
uv run mypy
uv run pytest -q
```

For migration changes, verify upgrades against a disposable database. Applying
`uv run alembic upgrade head` to the installed runtime requires authorization for
that operational change; ordinary reads never migrate. CI remains the authority
for the full coverage floor and packaging/security matrix.
