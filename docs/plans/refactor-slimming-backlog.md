# Refactor and Slimming Backlog

> Created: 2026-08-16
> Scope: structural slimming, duplication removal, and consolidation work for
> the post-v0.6.x feature pause. Nothing here changes a public contract: the
> 27-tool MCP surface, confirmation gates, and append-only semantics stay as
> they are. Each item records evidence, expected benefit, and the regression
> tests that must move with it.

## Part A — Deferred defect follow-ups

Three items were explicitly deferred during the 2026-08-16 defect review. They
are folded into the slimming work below and should be executed one by one:

| ID | Item | Folded into |
|---|---|---|
| D1 | Merge `console/app/chat/chat-workspace.tsx` into the Agent Rail implementation instead of maintaining two diverging copies | F9 |
| D2 | Replace the ~28 `window.confirm` / `alert` / `prompt` call sites with real form and confirmation components so required fields stop bypassing the red-asterisk contract | F6 |
| D3 | Remove the unused Tailwind dependency (`globals.css` imports it for preflight only; every rule is handwritten) or explicitly justify keeping the reset | F7 |

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
services inline. Infrastructure assembly already lives in
`infrastructure/composition/` (1,019 lines); the remaining application-service
wiring should move to an application-side bundle module
(`application/runtime.py` already exists as the sanctioned home). This
requires extending the composition-root whitelist in
`test_architecture_boundaries.py:152-190` — one deliberate test edit, not a
quiet bypass.

### S3–S5. Split the three largest services along their natural seams

| Service | Today | Seam | After |
|---|---|---|---|
| `thesis_revision_service.py` | 1,980 | Post-confirmation appliers (`_apply_confirmed_payload` … `_apply_subject_update`, :1150-1980, ~830 lines) → `thesis_revision_appliers.py` | ~1,150 + module |
| `agent_runtime_service.py` | 1,974 | Tool-call handling + receipts/usage (:1580-1974, ~400 lines) → own module; `_run_turn` (:903-1417, 515 lines) needs internal sectioning first | ~1,500 + module |
| `monitor_evaluation_service.py` | 1,620 | Notification rendering (`_NotificationPriceContext` :112-155, summary messages :1290-1620, ~400 lines) is independent of rule evaluation | ~1,250 + module |

These are physical moves only. The Propose→Confirm state machine
(`thesis_revision_service.py:394-1149`) and `_transition_event`
(`monitor_evaluation_service.py:708-1289`) carry frozen product semantics and
must not be restructured, only relocated.

### S6. Provider guard helpers duplicated across 24 adapters (~400–600 lines)

HTTP retry/rate limiting is already centralized (`providers/common/retry.py`
consumed by `router_engine.py`) — no adapter hand-rolls that. The duplication
is per-adapter guards: `_require_as_of` verbatim in `us/sec_edgar.py:664-673`
and `us/yahoo_finance.py:322-331`; `_raise_for_http_status` re-implemented per
vendor (`yahoo_finance.py:425`, `cross_asset/cme_public_client.py:227`, …).
Extract a `providers/common/adapter_guards.py` mixin with parameterized vendor
messages, following the existing `sec_common.py:132` and
`eastmoney/capital.py:56` mixin precedents. `account/schwab.py` is excluded
(schwab-py SDK transport, not HttpTransport).

### S7. Delete `continuous_series_service.py` (349 lines) — confirm first

The only reference outside the file itself is its own test
(`tests/unit/test_futures_services.py:12,162,176`); bootstrap and every CLI go
through `container.operations.futures_contracts` instead
(`interfaces/cli/futures_sync.py:21,43`). Confirm it is not a reserved Phase 3A
capability, then delete service + dead tests together.

### S8. Console `api.py` (1,839/24 routes) and `agent_api.py` (1,767/23 routes) helpers (~100–150 lines)

Both define their own `_RequestModel` base (`api.py:129` vs `agent_api.py:61`),
three isomorphic failure-envelope constructors (`api.py` `_sanitized_error:193`,
`_console_failure:504`, `_research_state_failure:1003`), and diagnostic
builders (`agent_api.py:257,602`). They already import from each other
(`api.py:31-32`), so a shared `interfaces/console/_shared.py` has no boundary
cost.

### S9. `interfaces/mcp/tools/compact.py` per-domain registration (~200–250 lines)

Lines 1611-2127 register ~87 `_spec(...)` variants at ~7 lines each. The schema
minimization machine (:558-1274) is the mechanism that keeps the 27-tool
surface small — do not touch it. The registration tail can become table-driven
so variant ownership per tool is auditable at a glance.

### S10. CLI lifecycle boilerplate (~200–300 lines)

13 CLI modules call `build_default_application()`; 11 repeat the
lock-acquire / `finally: await container.aclose()` pattern; 24 argparse
constructions. A tiny `run_cli(async fn)` helper removes ~10-15 lines per
module. Keep `monitor_notifications.py` as the public alias entry point.

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
rendered-html tests read page sources directly; keep asserted identifiers in
place when moving functions.

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

### F5. Unify the two agenda-metric implementations

`app/page.tsx:25-43` (`agendaSummary`) and `app/agenda/page.tsx:204-351`
compute the same upcoming/overdue/coverage metrics with different rules (home
uses `limitation_codes` for overdue, agenda uses `window_end`). Extract
`lib/agenda-presentation.ts` and pick one rule set deliberately.

### F6. Confirmation components (deferred item D2)

~28 native dialog sites (research alerts ×10, monitors:430/450/478/518,
operations:45/67, home:116/120, decision-workbench:214/216, agent-rail:1091).
Required resolution notes and due dates collected via `window.prompt` bypass
the red-asterisk/`aria-required` contract that the UI-convention test can only
enforce on DOM forms.

### F7. Remove Tailwind (deferred item D3)

`globals.css:1` is the only Tailwind touch; removing the import changes the
preflight reset, so verify visually (or via a rendered-DOM snapshot) that
handwritten rules do not depend on it, then drop `tailwindcss` +
`@tailwindcss/postcss` from `package.json`.

### F8. Merge `agent-api.ts` transport with `lib/api.ts` (~60 lines)

Only the transport/error layer: move the richer `responseError`
(agent-api:428-443) into `api.ts`, share `getJson`/`sendJson`. Keep agent
defensive parsing and SSE consumption separate.

## Part D — Feature-level consolidation candidates (need a product decision)

| ID | Candidate | Evidence | Note |
|---|---|---|---|
| F9 | Chat page vs Agent Rail: two entry shells over one agent surface | chat-workspace 830 lines vs agent-rail 1,555 with diverged `StreamSnapshot` types and stale "read-only milestone" copy (chat:345) | Merge to one implementation (shared `agent-stream` lib + `useAgentConversation` hook, ~350–450 lines), keep both shells thin |
| F10 | DeepSeek LLM provider retention | selectable via `LLM_PROVIDER`, Bailian is the default | If unused in practice, retire to cut an adapter + tests; requires owner confirmation |
| F11 | Home "Attention Queue" vs Decision Workbench Review Queue | home renders attention notices while the workbench owns acknowledge/resolve | Layered by design; evaluate whether the home summary should just deep-link instead of re-rendering its own queue view |

## Explicitly out of scope (do not "fix")

- Merging public MCP tools or altering the compact schema minimization
  machine (`compact.py:558-1274`) — frozen 27-tool contract.
- Merging Reddit and Moomoo sentiment — deliberately source-separated.
- Weakening append-only / Propose→Confirm / expected-version gates — only
  physical relocation is allowed (S3–S5).
- Deleting StockTwits enum/DB values or legacy Instrument Selection fields —
  readable-only wire compatibility; migration cost exceeds benefit.
- Renaming the `trading-partner-monitor-notifications` CLI alias — public
  entry-point contract.
- Moving `account/schwab.py` onto HttpTransport — schwab-py SDK owns the
  protocol.

## Suggested execution order

1. **Low risk, immediate relief**: S6 (provider guards), F1 (coerce), F2 (dead
   CSS), S8 (console helpers), S10 (CLI helper).
2. **Shared foundations**: S1 (coordinator base), F3 (form components), F5,
   F8.
3. **Big splits** (one per session, full checkpoint after each): S3, S4, S5,
   S2 (bootstrap + architecture-test whitelist), F4 (EntityBrowser).
4. **Decision-gated**: S7 (dead service), F9 (agent surface merge = D1), F6
   (= D2), F7 (= D3), F10, F11.

## Verification protocol

- Every item that moves code referenced by `console/tests/rendered-html.test.mjs`
  (it reads page sources and asserts identifiers/class names) must update the
  affected assertions in the same change.
- S2 adjusts `tests/test_architecture_boundaries.py` deliberately; no other
  item may touch boundary tests except to add coverage.
- Follow the repo's progressive verification: exact test node per edit, one
  full `ruff`/`mypy`/`pytest` + console `npm test` checkpoint per batch.
