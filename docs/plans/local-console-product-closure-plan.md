# Local Console 产品闭环计划

> Status: active design and implementation plan
> Updated: 2026-08-04

## 1. Product role

Local Console is the local, LLM-free control room for Trading Partner. It is not a
second business implementation and not a database editor. Curated pages call the
same compact-28 Capability Registry used by MCP; Console-only operational actions
remain limited to scheduler, sync, OAuth, notifications, backup, and retention.

The Console must answer five questions without requiring the user to discover a
missing screen one feature at a time:

1. What investment judgments and research files exist, and what needs review?
2. What is owned or watched, and how fresh and complete is that state?
3. What is being monitored, what ran, and what changed?
4. What upstream or scheduled operation needs attention?
5. Which advanced MCP capability is available when no curated workflow exists?

## 2. Editing semantics

“Editable” never means mutating every durable row in place.

| Object | Console behavior | Persistence rule |
|---|---|---|
| Research Subject metadata | create, edit title/summary/tags/links, archive | confirmed and idempotent mutation with audit candidate |
| Thesis | create or revise through a proposal; confirm/reject explicitly | append-only revisions; no direct rewrite of confirmed history |
| Trade Plan and research state | propose, compare, confirm/reject | versioned Candidate → Confirm lifecycle |
| Monitor | create, revise cadence/rules/status, pause/archive | immutable definition versions |
| Risk Policy | propose and explicitly confirm a new version | append-only policy versions |
| Watchlist | sync, add, remove | durable membership lifecycle and mutation receipts |
| Journal and Decision | append with explicit user/external-agent confirmation | append-only research memory |
| Account snapshot, transaction, Monitor Run/Event, Provider receipt | inspect and filter only | immutable facts/audit history; never edited |

## 3. Information architecture and complete capability inventory

### 3.1 Overview — attention queue

- System health and Data Quality Center summary with issue drill-down.
- Pending research candidates, open Challenge Reviews, triggered/unevaluated
  Monitors, stale account state, failed syncs, OAuth age, notification backlog.
- Every summary links to the exact Research Subject, Monitor, account, run, or operation rather
  than instructing the user to invoke a raw tool.

### 3.2 Research Hub — judgment lifecycle

- Searchable/filterable Research Subject index and one selected Research Subject workspace.
- Research Subject create, metadata edit, archive, links, and related instrument.
- Current Thesis and full revision history; new/revise proposal forms; pending
  candidate diff plus confirm/reject/withdraw actions.
- Assumptions, invalidations, open questions, current/versioned Trade Plan.
- Research timeline, reports/evidence, journals, decisions, and Challenge Review.
- Related Monitor definitions/events and one-click navigation to Monitoring.
- Explicit workflow launchers for Deep Dive, Catalyst Review, Peer Comparison, and
  historical-validation prepare/import; outputs never auto-confirm judgment.
- Future Catalyst Agenda and Judgment Scorecard live here once their durable data
  contracts are implemented.

### 3.3 Portfolio Hub — durable account and watch state

- Accounts/positions with snapshot age, valuation coverage, native-currency basis,
  warnings, and explicit provider refresh.
- Transaction ledger, activity coverage receipts, FIFO/broker-basis performance,
  and traceable incomplete reasons.
- Exposure, Risk Policy/checks, Position Sizing, and hypothetical-addition analysis.
- Watchlist groups/items, active source, explicit full sync, add/remove, unsupported
  symbols, and Research Subject/Monitor links.
- No implicit broker refresh and no FX aggregation or order surface.

### 3.4 Monitoring Hub — definitions, runs, events

- Definition index and selected Monitor detail/editor rather than one unbounded page.
- Current observation and every rule meaning/state; cadence, due time, linked Research Subject
  and exact Trade Plan version.
- Immutable per-Monitor run history and full batch drill-down.
- Transition event acknowledgement/resolution and notification delivery status.
- Scheduler status, last/next due evaluation, market-closed skip reason, and manual
  `run due`; no duplicated post-market evaluation.
- Bulk filters and safe status changes; no bulk rule rewrite or silent confirmation.

### 3.5 Operations Hub — service reliability

- Account/transaction/watchlist/post-market sync receipts and explicit retry.
- Schwab OAuth token age and the single-tab renewal flow.
- Monitor/post-market scheduler installation and health.
- Telegram Outbox status, retry/dead-letter detail, test, and flush.
- Provider route/fallback/cache/admission receipts with typed errors and queue waits;
  never expose request payloads, exception text, or credentials.
- Database health, owner-only backup, backup verification, and dry-run-first cache
  retention.
- Configuration readiness by capability, showing only presence/absence and safe
  enum choices—not secret values.

### 3.6 Advanced — capability workbench

The searchable compact-28 schema workbench remains an advanced escape hatch. It
must not substitute for common workflows or duplicate application services.

## 4. Cross-cutting acceptance

- Desktop uses master-detail or tabs for long collections; mobile never depends on
  wide tables. No page has horizontal overflow at supported breakpoints.
- Selected entity and active tab are URL-addressable so links survive refresh.
- Reads are durable-only unless the user explicitly starts a Provider operation.
- Every write displays effect, confirmation requirement, and resulting audit ID.
- Partial failures stay local to the affected card/entity and do not blank the page.
- Lists paginate or virtualize before they become unbounded; aggregate endpoints do
  not perform an N+1 read for every entity on initial page load.
- Empty, loading, stale, degraded, failed, and permission-gated states are explicit.
- Curated Console actions and MCP use the same schemas, handlers, actor gates,
  idempotency, and typed errors.

## 5. Delivery order

### Console C0 — Research closure (completed 2026-08-04)

- Replace the all-expanded Research page with a Research Subject index and selected workspace.
- Add `investment_case_manage/update` without increasing the 28-tool surface.
- Add Research Subject create/edit/archive, Thesis propose/review, pending candidates, and the
  existing assumptions/invalidations/questions/Trade Plan context.
- Correct Research responsive overflow and add focused Console/MCP tests.

Acceptance: the public tool count remains 28 under `compact-v13`; the live Console
renders the master-detail workspace without horizontal overflow, exposes linked
Monitors, and preserves proposal/review separation for Thesis writes.

### Console C1 — Portfolio and Watchlist closure (completed 2026-08-04)

- Split Holdings, Activity, Performance, Risk, and Watchlist into stable tabs.
- Add explicit Watchlist sync/manage and durable risk/coverage drill-down.

Acceptance: one durable-only Portfolio aggregate supplies the initial workspace;
account, transaction, and Watchlist Provider access remains behind separate explicit
sync actions. Risk Policy changes and Watchlist mutations retain confirmation,
idempotency, and version gates. The public surface remains 28 tools under
`compact-v14`.

### Console C2 — Monitoring and operations closure

- Convert Monitor and Operations to indexed detail views.
- Add scheduler, Outbox, sync receipt, data-quality, and Provider-route drill-down.

### Console C3 — long-horizon review

- Add research timeline/journal/decision/Challenge Review ergonomics.
- Add Catalyst Agenda, Judgment Scorecard, and performance visualizations only after
  their underlying durable contracts are complete.

## 6. Explicit non-goals

- Direct SQL editing, deleting audit history, rewriting confirmed revisions.
- Automatic Thesis/Trade Plan confirmation, implicit Provider refresh, or orders.
- Mirroring all 28 tools as separate pages or adding a second Console business API.
- Adding charts or dashboard cards whose source, freshness, and calculation basis
  cannot be drilled into.
