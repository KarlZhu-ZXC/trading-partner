# Refactor and Slimming Backlog

> Created: 2026-08-16
> Scope: structural slimming, duplication removal, and consolidation work for
> the post-v0.6.x feature pause. The 27-tool MCP surface, confirmation gates,
> append-only semantics, Provider error taxonomy, and canonical market-data
> fields stay frozen. Some items may change internal module layout, Console
> presentation, or documented configuration; those changes need their own
> compatibility check rather than being called contract-free.
>
> Reviewed and revised: 2026-08-16. This version corrects the composition-root
> direction, retains the actively used DeepSeek Provider, separates Provider
> guard risks, and adds measurable acceptance criteria.

## Part A — Deferred defect follow-ups

Three items were explicitly deferred during the 2026-08-16 defect review. They
are folded into the slimming work below and should be executed one by one:

| ID | Item | Folded into |
|---|---|---|
| D1 | Extract one shared Agent conversation/controller implementation; keep Chat and Agent Rail as thin shells instead of making either shell own the other | F9 |
| D2 | Classify native dialogs into validation, required input, and destructive confirmation; replace each class with the correct accessible component without weakening a gate | F6 |
| D3 | Determine whether Tailwind supplies anything beyond reset/preflight; remove it only after proving no generated utility is required, otherwise document the dependency | F7 |

## Non-negotiable refactor rules

- No item may change the public MCP tool count, grouped request shape, schema
  semantics, confirmation authorization, or append-only state transitions.
- Canonical Provider adapter fields, typed errors, retryability, freshness, and
  warning codes are behavior. Similar-looking helpers are not duplicates when
  those outputs differ.
- Application code must not import Infrastructure or Interfaces. Composition
  remains a top-level concern; moving wiring must not create an Application-layer
  service locator.
- A line-count reduction is not sufficient. Every item must reduce a named form
  of duplication or improve a named boundary while keeping focused behavior tests.
- Do not preserve dead identifiers solely because a source-text test asserts them.
  Replace brittle source assertions with rendered behavior or exported-helper tests.

## Part B — Backend slimming

### S1. Shared coordinator envelope/failure base (~300–500 lines)

There are ten `*_tool_coordinator.py` services (4,193 lines total:
`risk` 186, `monitor` 164, `us_context` 261, `us_research` 353, `us` 856,
`market` 611, `a_share` 558, `technical` 468, `portfolio` 212,
`account_transaction` 524). All repeat the same
`request_id → try → ToolEnvelope.success → except → _failure` skeleton:

- Verbatim `_failure`/`_exception` helpers (17–20 lines × 10), e.g.
  `risk_tool_coordinator.py:168-186`, `monitor_tool_coordinator.py:148-164`,
  `us_context_tool_coordinator.py:242-261`, `us_research_tool_coordinator.py:334-353`.
- `_FRESHNESS_ORDER` duplicated 4× (`us_context:34`, `us_research:38`,
  `market_tool_coordinator.py:80`, `peer_comparison_service.py:48`).
- `us_context` and `us_research` share ~103 lines of identical private helpers
  differing only in a warning string.

A shared module-level helper (same approach as the existing
`_research_support.py:130-175` `envelope_success/envelope_failure`) needs only
application/domain imports, so the layer boundary test stays green. Minimum
subset first: deduplicate `us_context` + `us_research` (~110 lines, lowest
risk), then generalize.

### S2. bootstrap.py is at its architectural ceiling (1159/1160)

`tests/test_architecture_boundaries.py:211` caps `bootstrap.py` at 1,160 lines
and it is at 1,159. `build_application` spans ~:289–1123 constructing ~90
services inline. `application/runtime.py` is **not** a sanctioned home for
construction: it intentionally contains application-only bundles and must not
import Infrastructure.

Split the top-level composition root into a small explicit package such as
`composition_root/` or `bootstrap_parts/`. Those modules may construct bounded
graphs (research, market data, monitoring, Agent, operations) and return typed
bundles to `bootstrap.py`; they must not become globally reachable service
locators. `bootstrap.py` remains the public import façade for
`ApplicationContainer`, `build_application`, and `build_default_application`.

The boundary test may be changed once to recognize that exact top-level
composition-root package. It must continue to reject Infrastructure imports
from `application/` and Application-service imports from
`infrastructure/composition/`. AGENTS.md architecture rule 4 ("Only
`src/bootstrap.py` connects application services to infrastructure") must be
rewritten in the same change to name the sanctioned composition-root package —
otherwise the guide and the code contradict each other.

### S3–S5. Split the three largest services along their natural seams

| Service | Today | Seam | After |
|---|---|---|---|
| `thesis_revision_service.py` | 1,980 | Post-confirmation appliers (`_apply_confirmed_payload` … `_apply_subject_update`, :1150-1980, ~830 lines) → `thesis_revision_appliers.py` | ~1,150 + module |
| `agent_runtime_service.py` | 1,974 | Tool-call handling + receipts/usage (:1580-1974, ~400 lines) → own module; `_run_turn` (:903-1417, 515 lines) needs internal sectioning first | ~1,500 + module |
| `monitor_evaluation_service.py` | 1,620 | Notification rendering (`_NotificationPriceContext` :112-155, summary messages :1290-1620, ~400 lines) is independent of rule evaluation | ~1,250 + module |

These are behavior-preserving extractions, not blind file moves. The Thesis
appliers currently depend on `_id_generator`, `_touch_subject`, and relationship
validation; extract a typed internal collaborator rather than passing a large
unstructured dependency bag. Agent extraction should begin with pure validation,
receipt, and usage helpers before moving tool dispatch. Monitor notification
rendering is already largely module-level and is the cleanest first split.

The Propose→Confirm state machine
(`thesis_revision_service.py:394-1149`) and `_transition_event`
(`monitor_evaluation_service.py:708-1289`) carry frozen product semantics and
must not be behaviorally restructured.

### S6. Provider guard helpers — extract only proven-identical behavior

HTTP retry/rate limiting is already centralized (`providers/common/retry.py`
consumed by `router_engine.py`) — no adapter hand-rolls that. The duplication
is per-adapter guards: `_require_as_of` verbatim in `us/sec_edgar.py:664-673`
and `us/yahoo_finance.py:322-331`; `_raise_for_http_status` re-implemented per
vendor (`yahoo_finance.py:425`, `cross_asset/cme_public_client.py:227`, …).
Split the work:

- **S6a (early batch)**: byte-equivalent, stateless guards such as
  `_require_as_of` only, as plain functions in
  `providers/common/adapter_guards.py`. These raise the same typed error with
  the same details for the same input, so the extraction cannot change Router
  fallback or Monitor retry behavior.
- **S6b (late, contract-tested)**: a status-helper extraction requires a
  per-Provider mapping table and contract tests proving identical outputs for
  authentication, rate limit, timeout, 4xx, and 5xx responses. Do not merge
  `_raise_for_http_status` before that evidence exists: vendor status mapping,
  typed error code, retryability, and safe diagnostic fields (CME marks
  401/403 `retryable=False`; Yahoo reports `status_class` on 429) affect
  Router fallback and Monitor retry behavior.

`account/schwab.py` remains excluded (schwab-py SDK transport, not
HttpTransport).

S6a is deliberately small (~20–60 net lines) but zero-behavior-risk and belongs
in the early low-risk batch; S6b stays gated behind its contract tests.

### S7. Retire the unreferenced continuous-series application service — decision gate

The only reference outside the file itself is its own test
(`tests/unit/test_futures_services.py:12,162,176`); bootstrap and every CLI go
through `container.operations.futures_contracts` instead
(`interfaces/cli/futures_sync.py:21,43`). Before deletion, check package exports,
entry points, docs, migration history, and runtime import tracing in addition to
static references. Delete only the unused application service and its service
tests. Keep `ContinuousSeriesDefinition`, repository rows/ports, and Provider
support while formal CME continuous identities remain part of the data model.

### S8. Console `api.py` (1,839/24 routes) and `agent_api.py` (1,767/23 routes) helpers (~100–150 lines)

Both define their own `_RequestModel` base (`api.py:129` vs `agent_api.py:61`),
three similar failure-envelope constructors (`api.py` `_sanitized_error:193`,
`_console_failure:504`, `_research_state_failure:1003`), and diagnostic
builders (`agent_api.py:257,602`). They already import from each other
(`api.py:31-32`), so a shared `interfaces/console/_shared.py` has no layer cost.
Extract `_RequestModel` and secret-safe primitive helpers first. Merge failure
constructors only after tests compare status code, envelope shape, retryability,
and redaction; similar structure does not imply identical endpoint semantics.

### S9. Split `compact.py` registration by domain; keep explicit `_spec` declarations

Lines 1611-2127 register ~87 `_spec(...)` variants at ~7 lines each. The schema
minimization machine (:558-1274) is the mechanism that keeps the 27-tool
surface small — do not touch it. Move registration groups into bounded
`compact_registration_*` modules while keeping explicit `_spec(...)`
declarations and the same deterministic order. Do not replace them with a
highly dynamic table or reflection layer: that would reduce visible lines while
making schema ownership, type hints, and diffs harder to audit. This is line-count
neutral in aggregate and should be done only if it materially reduces ownership
conflicts in `compact.py`.

### S10. CLI lifecycle boilerplate (~200–300 lines)

13 CLI modules call `build_default_application()`; 11 repeat the
lock-acquire / `finally: await container.aclose()` pattern; 24 argparse
constructions. Introduce a small interface-layer lifecycle helper for the truly
isomorphic subset. It must preserve lock scope, exit codes, cancellation, exception
translation, and exactly-once `aclose()`. Do not force CLIs with different lock or
signal semantics through one callback shape. Keep `monitor_notifications.py` as
the public alias entry point.

### S11–S12. Structural splits (line count neutral)

- `_research_memory_write_support.py` (1,260) is a shared module used by four
  services, but ~785 lines are reference validators
  (:474-1258). Split `_research_reference_validation.py`; do not merge
  `validate_event_related_entity` with `validate_journal_related_entity`
  (different wire ABI and historical-visibility rules).
- `domain/research/models.py` (1,394) holds validation guards (:1-554) plus 15
  entity dataclasses. ORM side already has 18 bounded modules; split domain
  side with a `models.py` re-export façade so imports do not churn.

## Part C — Frontend slimming

### F1. `lib/coerce.ts` — twelve files define the same coercion helpers (~120–150 lines)

`text()` exists 9× in three behavior families (dashboard `"—"` fallback:
research/portfolio/agenda/scorecards/research-continuity; agent strict `""`:
agent-rail/chat-workspace/agent-api; loose `String()`:
decision-workbench/retro). `asDict` 10×, string-list helpers 7×, envelope
unwrapping 7×. Migrate families A and B to shared exports; leave the two loose
variants in place (decision-workbench numbers would change display if unified).
`idempotencyKey` has ≥6 formats — verify backend tolerance before unifying.
Where `rendered-html.test.mjs` asserts a page-local helper name, replace that
assertion with rendered output, an exported-helper unit test, or an exact request
contract assertion. Do not keep duplicate helpers or compatibility aliases solely
for a source-text test.

### F2. Dead CSS + duplicate selectors (~115–135 lines)

Confirmed dead in `globals.css` (1,459 lines): the entire `.watchlist-*`
family (~:1108-1124), legacy `.monitor-header-tools`/`.monitor-search-box`/
`.monitor-status-filter`/`.monitor-list`, legacy research layout classes
(`.research-subject-list`, `.research-revision-grid`, …). Duplicate field CSS
(`.research-field` vs `.portfolio-field` vs scorecards controls) and the
research/monitor filter-index-arrow quartets collapse with F4. Watch the
dynamic class names (`.columns-*`, `.status-*`, `.depth-*`, `.slide-*`) —
never delete those.

### F3. Shared form components (~100–130 lines)

`RequiredMark`/`FieldLabel` exist in `ui.tsx` but adoption is low: pages
hand-write `<b className="required-mark">` (research:425/776, retro:142/152,
research-continuity:277), duplicate private `Field` components
(research:189-191 vs portfolio:326-328), and `{error && <div
className="inline-error">}` appears 22× across 12 files. Add `FormField`,
`ErrorNote`, `FormActions`; add the missing `Paginator` (agenda:1328-1332 vs
scorecards:437-441) and `MetricTile` (~10 hand-rolled copies) to `ui.tsx`.

### F4. `EntityBrowser` for the master-detail skeleton (~120 TSX + 45 CSS lines)

`research/page.tsx` and `monitors/page.tsx` duplicate the filter bar, filtered-
out notice, arrow paginator, responsive per-page sizing, hash deep-link
selection, and page-clamp effects (research:827-884 vs monitors:378-427), with
parallel CSS (`.monitor-filter-*` vs `.research-filter-*`, etc.). Extract
`components/entity-browser.tsx` with shared `.entity-*` styles.

### F5. Make Agenda summary semantics canonical at the backend

`app/page.tsx:25-43` (`agendaSummary`) and `app/agenda/page.tsx:204-351`
compute the same upcoming/overdue/coverage metrics with different rules (home
uses `limitation_codes` for overdue, agenda uses `window_end`). Do not choose a
new frontend rule. The Application Agenda service already owns point-in-time
overdue semantics and emits `AGENDA_OUTCOME_UNVERIFIED`; extend the Console DTO
with canonical bucket/count fields or a shared server projection. Both pages then
render those fields. A frontend `agenda-presentation.ts` may format labels only.

### F6. Replace native dialogs by semantic class (deferred item D2)

~29 native dialog sites at review time (research validation/archives, Monitor
execution/lifecycle/events,
operations:45/67, home:116/120, decision-workbench:214/216, agent-rail:1091).
Required resolution notes and due dates collected via `window.prompt` bypass
the red-asterisk/`aria-required` contract that the UI-convention test can only
enforce on DOM forms. Treat them separately:

1. validation `alert` → inline `ErrorNote` attached to the relevant form;
2. required/optional input `prompt` → accessible form or dialog with label,
   Required Mark when applicable, validation, Cancel, and explicit Submit;
3. destructive/external-effect `confirm` → reusable Confirmation Dialog that
   states the exact target and effect.

Do not remove confirmation for live orders, OAuth, Monitor due evaluation,
Monitor/Research Subject archive or lifecycle changes. Do not reintroduce the
duplicate non-destructive confirmations already removed from proposal and append
flows.

### F7. Audit and conditionally remove Tailwind (deferred item D3)

`globals.css:1` is the only Tailwind touch; removing the import changes the
preflight reset and may remove generated utilities. First inventory rendered
class names against handwritten selectors and compare production CSS. If no
Tailwind utility is required, add the minimal explicit reset relied on by the
Console, verify desktop/mobile screenshots and interactive controls, then drop
`tailwindcss` + `@tailwindcss/postcss` from `package.json`. Otherwise keep it and
record exactly which reset or utility behavior is still required.

### F8. Merge `agent-api.ts` transport with `lib/api.ts` (~60 lines)

Only the transport/error layer: move the richer `responseError`
(agent-api:428-443) into `api.ts`, share `getJson`/`sendJson`. Keep agent
defensive parsing and SSE consumption separate.

## Part D — Feature-level consolidation decisions

| ID | Candidate | Evidence | Note |
|---|---|---|---|
| F9 | Chat page vs Agent Rail: two entry shells over one agent surface | chat-workspace 830 lines vs agent-rail 1,555 with diverged `StreamSnapshot` types and stale "read-only milestone" copy (chat:345) | Extract shared `agent-stream` primitives and `useAgentConversation`; keep route and rail shells thin, with neither importing the other |
| F10 | DeepSeek LLM Provider | Actively configured as a selectable Provider and Monitor fallback; current tests cover `deepseek-v4-flash` and `deepseek-v4-flash-0731` | **KEEP. Remove this deletion candidate.** Consolidate only protocol-neutral OpenAI-compatible transport code; retain Provider identity, settings, reasoning limits, fallback receipts, and tests |
| F11 | Home "Attention Queue" vs Decision Workbench Review Queue | home renders attention notices while the workbench owns acknowledge/resolve | Layered by design; evaluate whether the home summary should just deep-link instead of re-rendering its own queue view |

## Explicitly out of scope (do not "fix")

- Merging public MCP tools or altering the compact schema minimization
  machine (`compact.py:558-1274`) — frozen 27-tool contract.
- Merging Reddit and Moomoo sentiment — deliberately source-separated.
- Weakening append-only / Propose→Confirm / expected-version gates. S3–S5 may
  introduce typed internal collaborators, but behavior and authorization remain frozen.
- Deleting StockTwits enum/DB values or legacy Instrument Selection fields —
  readable-only wire compatibility; migration cost exceeds benefit.
- Renaming the `trading-partner-monitor-notifications` CLI alias — public
  entry-point contract.
- Moving `account/schwab.py` onto HttpTransport — schwab-py SDK owns the
  protocol.
- Removing Bailian or DeepSeek Provider identities, fallback receipts, or
  configured reasoning limits as part of a generic transport cleanup.

## Suggested execution order

0. **Baseline and ownership map**: record current tracked LOC, duplicate counts,
   focused/full test counts and wall time, Console build time, MCP tool count and
   schema bytes. Freeze the exact files owned by each item.
1. **Low risk, immediate relief**: F2 (dead CSS), F1 (coerce families), F3
   (small form primitives), S6a (byte-identical stateless provider guards),
   and F8 (transport primitives only).
2. **Small backend foundations**: S1 first on `us_context` + `us_research`; S8
   `_RequestModel`/redaction primitives; S10 only for the demonstrably isomorphic
   CLI subset.
3. **Composition pressure first**: corrected S2, preserving a top-level-only
   composition root and the public `bootstrap.py` façade.
4. **Large behavior-preserving splits, one checkpoint each**: S5 Monitor
   notification rendering, S3 Thesis applier collaborator, S4 Agent pure helpers
   then tool dispatcher, S11, S12, and F4.
5. **Canonical behavior consolidation**: F5 backend Agenda projection; F6 by
   dialog class; F9 shared Agent controller; F11 product decision.
6. **Medium/high-risk optional cleanup**: S6b status-mapping extraction after
   its contract tests exist, F7 after visual/reset verification, S7 after
   explicit capability decision, and S9 only if ownership-conflict data
   justifies the split.

F10 is not an execution item; DeepSeek is retained.

## Verification protocol

### Per-batch measurement record

Full-suite runs follow the repository's progressive-verification policy:
focused tests per item, one broad checkpoint per batch — never a full run per
item. Each completed **batch** appends a receipt containing:

| Measure | Before | After | Required interpretation |
|---|---:|---:|---|
| Tracked LOC in owned files | exact | exact | Report net reduction and moved lines separately |
| Named duplicate blocks | exact count | exact count | State the detection method; zero is not assumed |
| Focused tests | count | count | Per item; report added, removed, and net test count |
| Full Python suite | count + wall time | count + wall time | Once per batch checkpoint, compared with the Phase-0 baseline |
| Console test/build | count + wall time | count + wall time | Once per batch when the batch touched frontend/shared API |
| Public MCP surface | tool count + schema bytes | same metrics | Must remain 27 tools; schema drift needs review |

Small items (for example F2 dead CSS) record only the first three rows. Do not
claim a speed improvement without before/after wall-clock measurements from the
same command and environment.

### Test routing

- S1: coordinator unit tests plus MCP envelope/error contracts.
- S2: architecture boundaries, bootstrap/container tests, CLI initialization,
  isolated wheel smoke, then full Python.
- S3: candidate proposal/confirmation, Research state conflicts, and Research MCP
  lifecycle integration.
- S4: Agent runtime, capability gateway, pending actions, behavior evaluation, and
  Console/Telegram Agent integration.
- S5: Monitor evaluation, transition, diagnostics, notification rendering, and
  Telegram contracts.
- S6: exact affected Provider contracts plus Router/fallback/retry tests; test every
  preserved typed error class before adding another vendor.
- S7: futures service/domain/repository/Provider tests, tool schema tests, and wheel
  smoke. Static “no imports” is necessary but not sufficient.
- S8: Console API and Agent API focused tests, redaction assertions, then Console
  production build.
- S9: MCP compact-surface/schema tests and an exact before/after schema inventory.
- S10: each migrated CLI's focused test, lock/close failure path, and wheel smoke.
- F1–F9/F11: Console rendered behavior, accessibility/UI-convention tests, and
  production build; F5 also runs Catalyst Agenda service/API tests.

When `rendered-html.test.mjs` reads source text, update it in the same item to assert
user-visible output, request contracts, or exported helper behavior. Do not keep a
page-local identifier just to satisfy the test.

Use progressive verification per coherent item, not per keystroke: focused tests
during implementation, then `ruff`, `mypy`, full `pytest`, and Console `npm test`
once before closing each batch. A large split gets its own full checkpoint and must
not be bundled with another large split.

## Batch receipts

### Batch 1 (2026-08-16) — F2, F1, F3, S6a, F8

Baseline → after (focused measurements; full-suite wall times come from CI
because the local full pytest run was declined in this session):

| Measure | Before | After |
|---|---:|---:|
| `src` LOC | 170,656 | 170,590 |
| `console/app` LOC | 11,302 | 11,341 |
| `globals.css` lines | 1,459 | 1,401 (95 dead selectors removed) |
| `text()` coercion definitions | 9 | 2 shared + 2 loose variants kept |
| agent `asRecord` definitions | 3 | 1 shared (+1 typed in agent-api) |
| `_require_as_of` byte-identical bodies | 14 | 1 shared |
| hand-rolled inline-error blocks | 22 | 19 via `ErrorNote` (5 composite-class sites remain) |
| hand-rolled paginators | 2 | 1 shared `Paginator` |
| Provider contract tests | 311 pass | 311 pass |
| Console `npm test` (build + 36 tests) | 36 pass | 36 pass, ~7s wall |
| `ruff` / `mypy src` | clean | clean (649 files) |
| Public MCP tools | 27 | 27 (unchanged, boundary test) |

Commits: b5df749 (F2), 06da3ef (F1), 45edd73 (F3), 08d15d2 (S6a), 39ba5ed (F8).
Not unified on purpose: the three divergent `stringList` behaviors, the loose
decision-workbench/retro coercers, agent-api's typed `asRecord`, and
`_raise_for_http_status` (S6b contract tests pending).

### Completion status (2026-08-16)

| Item | Result | Frozen-boundary check |
|---|---|---|
| S1 | Completed for the proven-equivalent coordinator envelopes; differing technical/endpoint payloads stay separate | Focused coordinator/error tests, Ruff, mypy |
| S2 | Completed with a bounded top-level `composition_root/`; an isolated-wheel failure found and fixed the missing package manifest entry | Architecture tests plus isolated installed-wheel smoke |
| S3 | Completed; confirmation appliers moved behind a typed collaborator | 42 focused Research lifecycle tests |
| S4 | Completed; Agent tool dispatch, validation, pending-action handling, receipts, and usage moved out of the turn orchestrator | 61 Agent tests plus 33 architecture tests |
| S5 | Completed; notification rendering is separate from Monitor evaluation and phone copy no longer repeats price/weekend/data-basis lines | 40 Monitor/notification tests |
| S6a | Completed | 311 Provider contract tests in Batch 1 |
| S6b | Intentionally not extracted: the required cross-vendor status mapping equivalence does not exist, so merging these guards would change typed errors/retryability | Safety gate retained; no unsafe abstraction added |
| S7 | Completed after package/export/entry-point/docs/runtime checks; only the unused application service and its direct service tests were removed | 21 futures tests plus installed-wheel smoke |
| S8 | Completed for the strict request/redaction primitives; endpoint-specific failure constructors remain separate where envelopes differ | Console API tests, Ruff, mypy |
| S9 | Completed because recent ownership-conflict evidence justified it; five explicit domain registration modules now own the moved `_spec` declarations | 27 tools; schema 25,615 bytes and wire 35,719 bytes unchanged; 86 MCP tests |
| S10 | Completed for the demonstrably isomorphic CLI subset, including the remaining futures sync CLI | CLI lifecycle tests plus installed-wheel smoke |
| S11 | Completed; reference validation moved without merging the distinct Event and Journal ABIs | 27 focused memory tests |
| S12 | Completed behind the stable `domain.research.models` re-export façade | 206 relevant and 88 compatibility tests |
| F1–F2, F8 | Completed in Batch 1 | See Batch 1 receipt |
| F3 | Completed with `FormField`, `ErrorNote`, `FormActions`, `Paginator`, and `MetricTile`; representative duplicate forms/metric grids now consume them | Console build, typecheck, lint, rendered conventions |
| F4 | Completed with one `EntityBrowser`; Research and Monitors retain independent detail content | 21 rendered tests; build/typecheck/lint |
| F5 | Completed; Agenda buckets are computed before pagination by the backend and both pages format the same projection | 20 focused Agenda/API/Console tests |
| F6 | Completed; 27 native dialogs reduced to zero without removing lifecycle, due-evaluation, OAuth, resolution-note, or archive gates | 37 Console tests and dialog convention coverage |
| F7 | Completed; no utility-class dependency existed, explicit preflight-equivalent reset added, both Tailwind packages and 18 transitive packages removed | Production build, 37 Console tests, typecheck/lint, zero npm audit findings |
| F9 | Completed with shared stream reducer and `useAgentConversation`; Chat and Agent Rail remain independent shells | Production build, 37 Console tests, typecheck/lint |
| F10 | KEEP decision confirmed; DeepSeek Provider identity and fallback behavior remain intact | No deletion performed |
| F11 | Completed; Home is summary/deep-link only and Decision Workbench remains the sole Review Queue action owner | Console rendered tests |

### Batch 2 (2026-08-16) — backend boundaries and large service splits

| Measure | Before | After / interpretation |
|---|---:|---:|
| Monitor evaluator main module | 1,620 lines | 808 lines; 898-line renderer owns phone presentation, evaluator semantics stay in place |
| Thesis revision main module | 1,980 lines | 1,115 lines; 863-line typed applier collaborator |
| Agent runtime main module | 1,974 lines | 1,495 lines; 182-line receipt and 465-line tool collaborators |
| Research memory write support | 1,260 lines | 450 lines plus 832-line reference-validation module; distinct validators preserved |
| Research domain façade | 1,394 lines | 60-line façade plus three bounded modules (521/402/523) |
| Full Python checkpoint | not rerun per item | 2,345 passed; final wall time recorded below |

Commits: e8e0b21/a14e3fc (S1), c91c4a7/166f998 (S2 and wheel closure),
920ac55 (S3), 3fa5a61 (S4), 6c51bf0 (S5), 6a9fa20 (S8),
5d89bee/5a25ee8 (S10), 7c8583e (S11), and 10eecdb (S12).

### Batch 3 (2026-08-16) — Console consolidation and canonical projections

| Measure | Before | After / interpretation |
|---|---:|---:|
| Native `window.alert/confirm/prompt` calls | 27 | 0; semantic dialogs/inline errors retain required gates |
| Research page | 949 lines | 917 lines after shared browser extraction |
| Monitors page | 631 lines | 611 lines after shared browser extraction |
| Shared Entity Browser | 0 | 216 lines; one filter/page/hash/responsive implementation |
| Home Review Queue actions | duplicated summary/actions | summary and deep-link only; actions remain in Decision Workbench |
| Agenda summary rules | 2 frontend variants | 1 backend-canonical projection |

Commits: 59b5c9a (F5), cc00c95 (F4/F6), d4541ed (F11), and
4df076d (F3 completion).

### Batch 4 (2026-08-16) — optional cleanups and final release checks

| Measure | Before | After |
|---|---:|---:|
| Tailwind direct packages | 2 | 0 |
| Removed Tailwind transitive packages | 0 | 18 |
| Native/owned CSS | Tailwind preflight + 1,402 owned lines | explicit reset + 1,408 owned lines |
| Chat shell | 821 lines | 583 lines (-238) |
| Agent Rail shell | 1,557 lines | 1,322 lines (-235) |
| Duplicate `StreamSnapshot` / `EMPTY_STREAM` / `handleStreamEvent` definitions | 2 each | 0 each; shared reducer/hook owns one implementation |
| Compact registration owner | one 2,127-line module | 1,618-line core plus five explicit registration modules |
| Public MCP surface | 27 tools / 25,615 schema bytes / 35,719 wire bytes | unchanged exactly |
| Console full test/build | 37 passed | 37 passed in 5.96s including production build |
| Console dependency audit | 3 transitive findings after dependency removal exposed stale `nanoid` override | 0 after bounded 3.3.18 override update |
| Python static checks | — | Ruff clean; mypy clean across 666 source files |
| Python dependency audit | — | 0 findings |
| Migration round trip | — | upgrade head → downgrade one → upgrade head passed; data-preservation test passed |
| Isolated wheel smoke | failed: `composition_root` absent from wheel | passed in 33.02s after manifest fix |

Commits: a9e96d9 (S7), d23b7bd (S9), 3b47d88/f29bf62 (F7 and dependency
audit), 6ad6afc (F9), and 166f998 (wheel closure).

The final Python suite was run once more after the packaging fix: 2,345 passed
in 46.55s pytest time / 48.50s wall time. No runtime-speed improvement is
claimed because the baseline did not use the same final command and environment.
