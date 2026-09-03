# Moomoo-first judgment intake transformation

Status: **ACTIVE**

Started: 2026-09-03

Owner: project maintainer with Codex implementation support

This is the sole active implementation plan for the current product transformation.
It is intentionally a progress ledger while work is underway. After the final release
gate passes, lasting contracts must be folded into `AGENTS.md`, `docs/phases/`, and
the current user/operator guides; this file must then be removed.

## Product decision

Moomoo is the owner's primary analysis and note-authoring surface. Trading Partner is
the sole durable convergence point for reviewing, challenging, and confirming those
views. Market data, Research, portfolio, risk, and monitoring remain supporting
capabilities; they are no longer the product's default starting point.

The canonical loop is:

```text
Moomoo note revision
  -> immutable Observation
  -> model-assisted, non-authoritative interpretation
  -> deterministic comparison with confirmed judgment and portfolio context
  -> one user review
  -> confirmed Decision / NO_ACTION
  -> optional Thesis, Trade Plan, or Monitor follow-up
```

The user reviews investment meaning, not internal module selection.

## Non-negotiable boundaries

- A note revision is source evidence, never a confirmed judgment.
- Model output is a draft. It cannot confirm, activate, trade, or silently resolve a
  review item.
- Every adopted result names the exact Observation revision and confirmer.
- A material review first creates a Decision, including an explicit `NO_ACTION`.
- Thesis changes require a Thesis revision proposal and confirmation. Trade Plan
  changes require their existing proposal/confirmation lifecycle. A Monitor may be
  prefilled but is not created or activated without explicit confirmation.
- Reads fail closed. Missing Position, Thesis, Plan, or Monitor context is visible and
  never converted into a fact or an automatic resolution.
- `SUMMARY_ONLY`, duplicate, and out-of-order observations cannot become judgments.
- Other speakers may inform the review but cannot be attributed to USER.
- No Moomoo UI automation, note write-back, or broader unattended execution is added.
- Public MCP tool count is an implementation choice, not a product invariant. Tools
  may be split, grouped, added, or removed when that makes the judgment loop easier to
  discover without weakening schemas, authority gates, or compatibility discipline.

## Delivery plan and progress

Legend: `[ ]` not started, `[~]` in progress, `[x]` completed, `[!]` blocked.

### T0 — Baseline, plan, and coordination

- [x] Record the product decision, boundaries, implementation sequence, acceptance
  gates, and rollback strategy in this document.
- [x] Remove the fixed 27-tool count from future-facing product invariants while
  retaining the current runtime count as an implemented snapshot.
- [~] Track the parallel model-evaluation task. Its result may change provider/model,
  prompt protocol, reasoning effort, timeout, fallback, and evaluation fixtures; it
  must not change the confirmation boundary or make model output authoritative.
- [x] Capture the pre-change focused test/build baseline: 96 focused Python tests,
  46 Console unit tests, and Console TypeScript typecheck passed before domain changes.

### T1 — Remove existing workflow dead ends

- [x] Make `Record Decision` explain Research Subject prerequisites instead of
  silently failing for DRAFT/non-tracking Subjects.
- [x] Keep a newly created DRAFT Subject visible after creation and make lifecycle
  state explicit in the default Research view.
- [x] Stop claiming that core judgment controls are present when Thesis/Plan context
  is absent.
- [x] Promote the primary Decision action out of overflow-only UI and explain disabled
  states in plain language.
- [ ] Replace user-facing raw IDs, rule codes, and seconds with searchable labels and
  human units while retaining exact identities in submitted payloads.
- [x] Distinguish an empty unconfigured account from a failed account read.
- [~] Add focused regression tests for every repaired dead end.

Gate: a new or existing Observation always has a visible status and a valid next step;
no primary path requires knowing an opaque ID.

### T2 — Durable Observation review ledger

- [x] Add one append-safe Observation review identity per exact FULL note revision.
- [x] Add explicit outcomes: `PENDING`, `DEFERRED`, `ADOPTED`, and `NO_ACTION`, with
  reviewer, authorization note, version, timestamps, and idempotency.
- [~] Link an outcome to the exact confirmed Decision and any downstream proposal or
  Monitor created from it.
- [x] Materialize `OBSERVATION_REVIEW_DUE` through the existing ReviewItem/Attention
  path and retire it only from an authoritative review outcome.
- [x] Backfill existing Decisions that already name `external_note_revision_id` as
  adopted; create at most one pending item for the latest eligible FULL revision per
  note, without historical inbox flooding.
- [x] Add migration, repository, service, schema, idempotency, concurrency, and
  forward-only migration tests.

Gate: repeated sync/reanalysis creates no duplicate review; failed or bounded reads
never resolve one; existing adopted Decisions remain authoritative.

Current receipt: migration `0071_external_note_reviews`, append-only review revisions,
exact Decision/Subject/source validation, idempotent pending materialization, Console
read/transition endpoints, and global Attention projection are implemented. Downstream
proposal links and the complete integration suite remain open; exact concurrent
pending-materialization recovery is implemented.

### T3 — One deterministic view-review package

- [x] Add an application-level `ViewReviewService` that composes the Observation,
  latest successful interpretation, prior adopted Decision, current Thesis, active
  Trade Plan, linked Monitors, durable Position context, and coverage warnings.
- [x] Separate model claims from deterministic facts and label unavailable context.
- [x] Produce one stable DTO: source change, USER scenarios, external viewpoints,
  conflicts with current judgment, affected objects, missing evidence, coverage, and
  allowed next actions.
- [x] Derive Current View from confirmed records; do not create a second mutable truth
  store.
- [~] Add explicit handling for unmapped, ambiguous, multi-subject, reverted,
  external-speaker-only, deleted, and stale revisions.
- [x] Add golden fixtures and contract tests independent of the selected model.

Gate: the same durable inputs always produce the same comparison and action
eligibility; changing the model cannot bypass deterministic gates.

Current receipt: the closed package contains four USER scenarios, source/model
separation, latest confirmed Thesis/Plan/Decision, durable Position and Monitor
context, per-source coverage, deterministic structural flags, and allowed actions.
Console loads this package before review. A dedicated Current View read now derives
the latest exact confirmed Observation review and Decision without adding another
mutable truth table; the remaining ambiguous/multi-subject policies remain open.

### T4 — Moomoo-first Console workflow

- [x] Make `待复核观点` and `当前正式观点` the primary Home/Journal entry points.
- [~] Replace module-first controls with one `复核这次观点变化` flow showing source
  diff, interpretation, confirmed-baseline comparison, portfolio impact, coverage,
  and one review conclusion.
- [ ] Provide an inline Subject mapping/create-proposal path when a note cannot be
  matched; never guess among ambiguous candidates.
- [~] Let one explicit review record the Decision first and optionally request
  non-effective Thesis/Plan proposals. Monitor creation remains a separate explicit
  confirmation from a prefilled draft.
- [x] Make partial success recoverable: a committed Decision is never duplicated or
  rolled back because a downstream proposal failed.
- [ ] Preserve the current inbox behind a rollback flag until the new golden journeys
  pass.

Gate: the owner can move from a changed Moomoo note to a formal Decision without
choosing an internal module or entering an opaque identity.

### T5 — MCP and Agent redesign

- [~] Measure the current grouped surface against user intents; split overloaded tools
  and remove obsolete compatibility tools only with an explicit migration table.
- [x] Expose bounded structured `view inbox`, `review package`, and `current view`
  reads suitable for Codex without returning private full note bodies by default.
- [x] Route phrases such as “复核我刚更新的笔记” through the same application service
  used by Console; do not implement a parallel Agent-only workflow.
- [x] Make market-data calls optional evidence verification within a review rather
  than the default conversational starting point.
- [x] Keep exact user confirmation, idempotency, version, actor, and order boundaries
  on every write regardless of tool shape.
- [x] Publish a before/after capability migration and update host instructions.

Gate: Console and Codex return the same review semantics from the same source data;
tool discovery follows user intents rather than backend modules.

Current capability migration: `mcp-vnext-shadow-v2` exposed 27 tools. The
`mcp-vnext-shadow-v3` snapshot exposes 30, adding read-only `view_inbox`,
`view_review_get`, and `current_view_get`. No v2 capability was removed or renamed;
all three additions omit private full note bodies and share the Console application
services. Future counts remain unfrozen.

### T6 — Model decision integration and evaluation

- [ ] Import the parallel model-evaluation result and record the selected primary,
  fallback, prompt/schema version, reasoning effort, timeout, cost/privacy rationale,
  and rejected alternatives in the current Phase contract.
- [x] Keep the provider behind `AgentModelProvider`; no domain or Console code may
  depend on a vendor-specific response shape.
- [x] Add sanitized regression fixtures for attribution, four scenarios, materiality,
  contradictions, missing evidence, revisions, reversions, and malformed output.
- [x] Require deterministic schema validation and retain the prior successful
  interpretation when a new attempt fails.
- [x] Add observable, secret-safe failure categories and manual retry/reanalysis.

Gate: the selected and fallback models pass the same closed fixture suite, and model
failure leaves the review usable with explicit degradation.

### T7 — Automation, quality, documentation, and release

- [x] Extend existing post-market Observation sync to analyze eligible FULL revisions
  and enqueue only normalized USER-text changes; suppress duplicate, summary-only,
  external-only, and whitespace-only noise. A model-labelled `NO_MATERIAL_CHANGE`
  cannot suppress a real USER text change because the model is non-authoritative.
- [~] Add review freshness, pending age, sync/analysis coverage, duplicate prevention,
  and adoption provenance to Data Quality/Operations.
- [~] Run focused tests, full Python quality, Console tests/lint/typecheck/build,
  packaging, migrations, secret scan, and a real local smoke with private output kept
  outside Git.
- [~] Update `AGENTS.md`, Trading Partner Skill, Phase specification, README, Console
  and MCP guides, operations/known issues, and unreleased notes.
- [ ] Delete superseded workflow documentation and this active plan after its lasting
  decisions and final completion receipt have been folded into current truth.
- [ ] Commit, push `main`, and verify local HEAD equals `origin/main`.

Gate: the complete judgment loop is documented, tested, reversible, and usable from
both Console and Codex without weakening confirmation or privacy.

## Rollback and migration strategy

- Ship storage and read projections before changing navigation or write paths.
- Put new review materialization, Console entry, and Agent routing behind separate
  owner-controlled flags.
- Make migrations additive until the final compatibility release; do not drop old
  columns or operations while rollback is required.
- Keep every new write idempotent and independently retryable.
- If a later stage is disabled, immutable Observations and confirmed Decisions remain
  readable through the current Phase 4 paths.

## Success measures

- Every adopted Decision names one exact Observation revision.
- Duplicate review tasks and duplicate Decisions from retry are zero.
- Median module choices required for a note review is zero.
- Pending review age and interpretation coverage are visible.
- The owner can answer: “What is my current confirmed view, what changed, what did I
  decide, and which exact note revision caused it?”
- There is no autonomous judgment confirmation, Monitor activation, position mutation,
  or order authorization.

## Decision log

| Date | Decision | Reason |
|---|---|---|
| 2026-09-03 | Moomoo is the primary authoring edge; Trading Partner is the durable judgment system of record. | This matches the owner's real workflow and removes duplicate analysis entry. |
| 2026-09-03 | Formal adoption starts with Decision/NO_ACTION; other durable objects are consequences. | The user should review meaning once instead of selecting modules. |
| 2026-09-03 | MCP tool count is flexible. | Discoverability and coherent intent routing are more important than preserving an arbitrary count. |
| 2026-09-03 | Model choice is a replaceable implementation decision pending a parallel evaluation. | Product authority and deterministic gates must not depend on one model vendor. |
| 2026-09-03 | Do not switch the formal interpretation model from partial benchmark evidence. | LongCat 2.0 completed quickly but mis-propagated an explicit withdrawal; other directory-visible candidates have not yet completed the strict contract reliably. |
