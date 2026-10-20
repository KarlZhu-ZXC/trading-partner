# Architecture and performance verification

Trading Partner uses a modular monolith. Domain rules remain framework-free;
application services depend on ports; infrastructure implements those ports;
interfaces adapt validated operations to transports. `bootstrap.py` remains the
public facade; bounded builders under `composition_root/` own service construction.
The research builder shares the UoW factory and deterministic collaborators across
Subject, Thesis, memory, history, context and challenge services.

## Review queries and materialization

`ReviewWorkspaceQueryService` owns the Overview review projection. Its existing
application collaborators read only durable sources. `ResearchAttentionReader`
returns pending counts with one aggregate SELECT, without hydrating every Subject's
full state or inheriting the former 200-Subject / 500-candidate display limits.
Counts include persisted PROPOSED candidates on non-archived Subjects. Expiry
remains an explicit state transition, never a side effect of reading.

Overview, ReviewItem lists and scoped Journal GETs never apply ReviewItem batches.
Live projections remain visible without manufacturing durable rows. Exact active
source keys suppress duplicates, including manually resolved items; inactive or
previously unseen sources remain visible. Responses distinguish collection time,
latest materialized item observation time, unavailable sources and partial pages.
Latest item observation time is not a successful global-refresh receipt.

**Update Review Queue** on Overview calls `POST /api/review-items/refresh` with
`confirmation: "refresh_review_items"` and the current Console session token. It
collects and applies local deterministic projections, preserving failed-source and
truncated-source closure boundaries. It cannot refresh a provider, call a model,
confirm judgment or place an order. Ordinary Refresh only reloads page data.

The explicit command uses private stable pagination for all eight source groups
and compares two scans, with a maximum of 5,000 rows per source and an overflow
probe. Each source discloses its count, pages, bounds, scan window and safe warning
codes. Degraded/truncated pages, a gap in pagination, a failed overflow probe or a
changed fingerprint prevent that source from closing absent reminders. This is
per-source consistency, not a single database snapshot across all sources.

After applying the batch once, the command records safe scan metadata in the
existing audit table as `REVIEW_ITEMS_REFRESHED`. Overview displays the latest
durable receipt independently of current bounded page coverage. Saving the receipt
can fail after successful reconciliation; that response explicitly reports PARTIAL
and does not replay the batch. A later GET never creates a replacement receipt.
No database migration or extra diagnostic business-state store is introduced.

## Shared capability contract

`interfaces/capabilities/` owns the canonical operation graph, Pydantic argument
models, policies, exact schemas and invocation. Console and Agent consume those
contracts directly. The MCP adapter owns tool listing, protocol annotations,
protocol errors and transport compaction. The Console catalog retains complete
schemas; only the explicit Capability Workbench applies the compact result view.
The public MCP entrypoints remain compatible and bind the same validated graph.
Legacy capability-adapter locations under `interfaces/mcp/tools/` remain reusable
implementation modules; chart content retains its existing wire contract.

## Agent execution ownership

`AgentModelExecution` handles streaming response assembly and completion fallback
through explicit event and cancellation callbacks, without repositories or pending
actions. The runtime retains routing/retry decisions, conversation locks, final
assistant append and terminal turn transition. Partial visible output keeps its
retry barrier; Research budget accounting and cancellation remain shared.

## Reproducible measurement

Run from a development checkout:

```sh
uv run python scripts/benchmark_review_queries.py --output /tmp/tp-review-benchmark.json
```

The runner creates and removes its own SQLite databases. It never loads owner
configuration, contacts providers/models or reads private notes/accounts. Fixtures
contain 10, 100 and 200 active theme Subjects, each with one valid proposed candidate
and no Theses. Before timing, the runner requires equal results from the former
subject-list + full-state query path and the new aggregate path on the same database.

The JSON contains environment, warmups, elapsed samples, p50, nearest-rank p95,
SELECT counts and a separately instrumented Python allocation peak. This measures
the pending-research summary, not page rendering, HTTP/provider latency, resident
memory or cold-disk performance. The baseline retains its 200-Subject cap for fair
comparison; integration tests separately cover larger populations and counts.

The deterministic regression budget is one SELECT regardless of Subject count. The per-Subject baseline needs at least `1 + 5 × Subjects` SELECTs; expanding
full-state records (including impact links) or adding Theses can require more. The
runner records actual counts for the checked-out version. Timing varies with machine load and storage. CI should
check result equality, query budgets and read-only behavior instead of imposing a
machine-specific millisecond threshold. Generated receipts stay outside Git.

## Research workflow quality and request latency

### Review completion and authority

One user journey can contain several independent business operations. The host
must show each confirmed result and any unfinished step, without describing the
journey as one cross-service transaction. Opening a page, preparing references,
expanding a source or reading a receipt never grants authority.

| Stage | Durable authority | Completion and recovery |
|---|---|---|
| Source preparation | Existing immutable source revision and read context | Source/model gaps stay visible; no confirmed change |
| Independent or Quick Decision | Exact user confirmation, version/fingerprint, idempotency key | Recorded Decision; source reviews keep separate status |
| Observation disposition | Exact source-review transition and linked Decision | Only that revision is adopted/deferred/NO_ACTION |
| Research mutation | Candidate reviewer/version/idempotency gates | That Candidate's change; no implied Plan/Monitor transition |
| Queue materialization | Explicit local refresh command | Per-source coverage and partial progress; no Provider/model work |
| Broker operation | Exact unexpired preview and its own submit/cancel authorization | Known result or UNKNOWN; no automatic replay |

Quick Review persists the reference manifest as a linked Journal NOTE tagged
`review_materials_v1`, in the Decision's existing research UoW. It contains IDs,
versions, preparation time and coverage rather than note text, account amounts or
model output. It records prepared context, not a claim that every source was read.
Several consulted account snapshots retain separate references. Historical material
receipts that do not exist remain unavailable, not reconstructed from current data.

The recovery read returns the original saved manifest and Decision. The browser
checks an unresolved saved request before preparing another context, retains the
original key, and submits only on explicit retry. A receipt read failure cannot
invalidate or repeat a successful write. Bounded pending counts describe preparation
time, not the current queue. Source-specific disposition and later steps remain
independent, visible operations.

Focused acceptance covers source identity/attribution, model-unavailable manual
reading, SUMMARY_ONLY exclusion, stale versions, multi-account references, material
rollback, response loss, restart, unresolved-request recovery and partial-source
queue closure. Job tests separately inject heartbeat/finish failure and cancellation.
These scenarios validate workflow behavior; they do not measure investment quality.

```sh
uv run python scripts/evaluate_research_workflow.py --output /tmp/research-workflow.json --repeats 15
uv run python scripts/evaluate_research_workflow.py --output /tmp/research-workflow-next.json --repeats 15 --baseline /tmp/research-workflow.json
```

This runner uses migrated disposable SQLite databases and synthetic Research
Subjects with 1, 10 and 100 source records. It measures the actual Console
evidence-links ASGI route through the application service and repositories to JSON,
including SQL SELECT and mutation counts. Setup, TCP/network transport, browser
rendering, real providers and model latency are outside this timing boundary.
Returned facts and stable response content are checked before interpreting latency;
warmup count, raw samples, p50/p95, environment and source fingerprints accompany
the receipt. Compare only compatible fixture/environment/boundary receipts; a
changed response is not a like-for-like performance improvement.

The same receipt runs the existing scripted Agent runtime and field-evidence
catalogs. Citation false acceptance, valid-field false rejection and surfaced
missing-source cases carry explicit denominators. Action-gate assertion failures
are distinct from observed unauthorized execution signals. These deterministic
cases measure host enforcement, not real-model research accuracy, forecast skill
or investment performance. No owner configuration, real notes, accounts, broker
refresh or model call is used. A failed quality gate or incompatible baseline
returns a nonzero exit code. Timing ratios are observations rather than portable
machine-independent service-level guarantees.

## Verification scope

Focused contracts cover complete counts, archived/resolved exclusions, partial
source visibility, duplicate suppression, passive HTTP reads, exact refresh
confirmation, streaming fallback/cancellation and composition identity. The browser
test verifies load/reload produces no write and an explicit queue update produces
one command. Broader backend checks follow the root agent guide; packaging,
security and coverage remain CI/release gates.


## Workflow acceptance scope

The synthetic workflow evaluator reports automated runtime facts, action gates,
recovery, field-binding gaps and read-only query measurements separately. The
versioned `evals/workflow-semantic-holdout.v1.json` reserves five synthetic cases for
independent review of period, scope/currency, causality, counterevidence and missing
information. Its checksum is pinned by the evaluator test and included in source
fingerprints. Do not revise the corpus to accommodate a failed answer; a deliberate
corpus change needs a new version and a new comparison baseline. It is evaluation
material, never a runtime prompt or a product training example.

`passed` in `evaluate_research_workflow.py` covers automated synthetic gates only;
`passed_scope` states that limit. `semantic_acceptance` remains explicitly unreviewed,
with per-dimension denominators, null critical-failure counts and no model-quality
pass. Human active-task time is also unknown. No private notes, detailed click
telemetry, live broker reads or real model calls are used by this runner.

The current synthetic checkpoint exercised 15 runtime cases (fact reads, action
gates, recovery and safety) and 40 field-binding cases, including 32 expected gaps.
No failing assertions or explicit unauthorized-execution signals were observed.
The 1/10/100-source reads preserved facts, executed zero SQL mutations and used 11
SELECTs per read. These are synthetic read-path observations, not a measured user
productivity gain, proof of investment quality, or full human semantic acceptance.
Console regression covers follow-up navigation, source/version failures, explicit
review saves and independent order-preview drafts; backend transport/authorization
checks retain the shared exact schemas and pending-action gates.

## Note-led default loading

The default workspace excludes the Copilot client bundle until explicitly opened.
After mounting, collapsing retains the rail's draft/turn recovery state. Prepared
Question Research uses a readiness handshake before its existing gated handoff.
Technical adapters preserve their ports while deferring numerical imports until use.
No optional dependency or shared model provider has been removed.

Synthetic same-machine checks of technical-adapter import (three fresh processes)
observed median 173 ms / 72 MB peak RSS before, 19 ms / 21 MB after. This is an
isolated import measurement, not whole-application cold startup or steady-state RSS.
Frontend resource measurement intercepts all API requests with synthetic responses;
no owner notes, account data or model calls participate. A fresh 1440px Chromium
comparison against the v0.9.0 release build observed 374,715 → 185,732 encoded script
bytes and 8 → 4 API reads (Copilot reads 3 → 0). These are single synthetic default-page
observations, not a latency SLA or a claim about all routes. User task-time acceptance
and a representative capability usage window remain separate, unfinished evidence.

Note account reads filter exact instruments and conservative option-symbol candidates
before hydration. Legacy timezone-offset rows are compared as aware datetimes.
Candidate ledger rows are streamed as Core mappings in batches of 128; a bounded newest-set heap
retains at most the requested limit (1,001 for the note context overflow probe).
Aware Python datetimes preserve legacy offsets and microsecond distinctions; a
lexicographic ISO-string or SQLite julianday ordering is not an equivalent replacement.
This reduces Python allocations, not the number of candidate rows scanned by the DB.

Reproduce the synthetic repository benchmark without owner settings or providers:

```sh
uv run python scripts/benchmark_note_context.py --rows 20000 --repeats 3 --output /tmp/note-ledger.json
```

The benchmark first requires equality of the entire returned ordered tuple against
the original materialized algorithm. Separate warm-read timing and tracemalloc
measurements over 20,000 synthetic rows observed median 210 → 155 ms and Python
peak allocation 49.9 → 1.3 MB on one machine. These are repository-read measurements,
not whole-process RSS, cold startup, DB scan complexity or a latency guarantee.
A 50,000-row follow-up kept result equality and observed 533 → 387 ms median,
126.6 → 1.3 MB Python peak allocation, with the same 1,001-row result bound.
Regression tests bound retained ORM rows and cover sub-millisecond order, mixed
UTC offsets, tied IDs across accounts, future exclusions and pagination. Cursor fingerprints
reject changed snapshots/backfills rather than silently shifting pages.

The follow-up Core stream creates no ORM instance per candidate while retaining
domain validation for every matching row, including corrupt future candidates.
It does not reduce the number of candidate rows scanned or weaken error semantics.


## Default MCP startup resources

US/Korean exchange calendars build their existing schedules only on the first
session query. Yahoo SDK imports occur on an explicit Yahoo operation, with the
same rate-limit, missing-ticker and timeout mapping. Disabled tracing loads neither
the OpenTelemetry SDK nor its HTTP exporter; enabled tracing keeps the existing
process-wide provider and redaction rules. Yahoo/exchange calendars, TA-Lib and the
tracing SDK/exporter are capability extras; source development and `all` retain them.
Missing exchange calendars fail explicitly before scheduler account/quote/order work.

```sh
uv run python scripts/benchmark_mcp_startup.py --repeats 3 --output /tmp/mcp-startup.json
```

The runner starts three fresh processes with an allowlisted environment, no owner
configuration, an empty in-memory SQLite database, disabled optional resources and
network connections blocked. It measures application imports, test-mode composition
and FastMCP tool listing independently; it excludes host handshake, populated DB
queries, cold-disk performance and steady-state resident memory. Peak RSS is the
whole sample process high-water mark, with platform-specific units normalized to
bytes. All samples must retain the same public tool names.

A same-machine comparison against the inherited slimming checkpoint observed
median 3.360 → 1.889 seconds overall, 1.121 → 0.033 seconds for composition and
257.2 → 186.3 MB peak RSS (decimal units). All nine tool names matched. Regressions
also block the numerical/PDF/vector SDKs and the tracing SDK/exporter, then perform
synthetic migrated-database note and saved-account reads with zero business SQL
mutations and no network connection. This proves default resource independence,
not that market calculations work without their required installed packages.


For the actual stdio handshake, use the same runner with `--mode stdio`. Each
fresh sample starts the production MCP entrypoint with its own synthetic runtime,
blocks network connections, sends initialize and list_tools over stdio, then closes
the server. Timing excludes the client SDK import; peak RSS belongs to the server
child, not the client. Three same-machine samples against the inherited checkpoint
observed median 3.384 → 1.943 seconds and 257.2 → 187.6 MB server peak RSS, retaining
the same nine tool names. Empty test-mode SQLite still excludes populated production
DB validation and query costs; neither mode measures user task time or resident memory after business workloads.


Stdio mode also samples the owned server PID's RSS immediately after tool listing
with `ps` (KiB normalized to bytes). Restricted hosts report this field as null with
an unavailable basis instead of substituting the high-water mark. A separate
three-sample same-machine check observed 257.3 → 187.7 MB idle RSS and median
3.375 → 1.914 seconds handshake/listing time. This is an empty, idle MCP baseline,
not the memory of a configured process after market/technical/model operations.

## Installation compatibility profiles

| Profile | Available capability | Absent extra behavior |
|---|---|---|
| Base wheel | Notes, durable account/portfolio reads and discovery | Explicit absent Yahoo/calendar/technical operation returns `PROVIDER_NOT_CONFIGURED` with its extra guidance |
| `market-data` | Yahoo SDK and exact US/Korean exchange sessions | Required by the market/scheduler paths using these sources; missing calendars never become guessed hours |
| `technical` | TA-Lib calculations | Does not implicitly install PNG plotting or a tracing exporter |
| `chart` | TA-Lib plus PNG rendering | Includes both numerical and matplotlib requirements |
| `tracing` | Explicitly enabled OpenTelemetry SDK and HTTP exporter | Enabled tracing without this extra fails before publishing process-wide state; disabled tracing stays a no-op |
| `all` / source `uv sync` | Existing full capability set, including technical/tracing | Existing daily paths and scheduler dependencies remain available |

The wheel probe checks an actual isolated base installation outside the repository,
including disposable migration initialization, packaged configuration, health and all
nine MCP tools. It also asserts NumPy/Pandas, Yahoo SDK, exchange calendars, TA-Lib
and tracing SDK/exporter are physically absent
and blocks network connections during the application probe. `--offline` uses only
cached uv packages and has no network fallback:

```sh
uv run python scripts/smoke_isolated_wheel.py --offline
```

Dependency-blocked unit tests cover Yahoo, calendar, technical and chart errors, unrelated import bugs,
explicit tracing failure and retry after dependencies become available. No package
versions or hashes changed in this profile split; `uv lock --check --offline` verifies
root metadata consistency. Minimal installed users needing technical operations must
add `technical` or `chart` plus `market-data` for sources/calendar-backed bar reads;
market/scheduler users must retain `market-data` or `all`, and users with enabled tracing
must add `tracing` before
upgrading. No installed owner environment is synchronized or restarted by this work.

## Synthetic note workload and remaining task acceptance

```sh
uv run python scripts/benchmark_note_workload.py --notes 200 --transactions 20000 --repeats 3 --output /tmp/note-workload.json
```

This runner seeds two revisions per note, one saved empty account and the stated
number of same-Instrument transactions in a temporary current ORM schema. Fresh
application processes use an allowlisted environment and blocked networking.
Fixture writes are excluded from read timings and SQL counts; memory includes
imports and fixture preparation. Latest revisions and the displayed rows' predecessors
are read in batches. A nonempty inbox now uses four SELECTs at 1, 30 and 100 displayed
rows: identities, latest revisions, predecessors and linked discussions. The single-note
predecessor API remains unchanged for sync and review workflows. No cache or new
persisted state is introduced.

Predecessors are selected independently below each note's exact version, including
nonconsecutive versions. An absent predecessor and an unreadable persisted predecessor
remain distinct; a corrupt row does not hide healthy notes or fall back to an older
version. A failed batch retains unavailable-content status. Identity-first bounds,
aware-time order, future exclusions and discussion coverage remain unchanged.

A same-machine comparison using 100 synthetic notes, two revisions each, zero
transactions and 30 displayed rows measured 33 → 4 SELECTs, with median inbox time
10.2 → 7.5 ms across three fresh processes per variant. Both variants issued zero
business writes; focused regression also compares the complete returned payload.
These timings describe only the synthetic warm SQLite read, not user productivity.

On a separate 200-note / 20,000-transaction fixture, the Core stream changed note-read median
166.3 → 113.6 ms and continuation 164.3 → 110.1 ms. Account pages still contain at
most six rows and retain the 1,000-row history limit and explicit gap. These samples
precede the added coverage-receipt lookup. Business-loaded RSS stayed about 158 MB;
no memory decrease is claimed. The runner excludes UI interaction, model discussion,
save and real human task time, which still require actual experience acceptance.

The Console now uses a separate archive browse projection: a lightweight metadata
index (latest title, Instrument, aware observation time and revision ID) followed by
one page of full revisions and batched predecessors. It has no 500-note archive cap;
MCP's compatibility inbox and its bounds are unchanged. Sorting/filtering the index
is linear in saved identities, while body hydration is bounded by the page size.
Search terms use a read-only POST body and are not persisted in URLs/browser storage.
Discussion history is deferred until expanded, with no portfolio read for that request.

A later three-process comparison with 200 notes / 20,000 transactions measured
note reads at 114.8 → 107.4 ms and continuation at 112.1 → 104.2 ms after removing
deep copies of immutable transaction scalar values. A profiler identified candidate
hydration as the main remaining cost. Hydrating only selected rows was rejected
because it bypassed the existing corrupt-future-record validation; the full candidate
validation remains. No snapshot cache or weaker cursor fingerprint was introduced.
These are synthetic service timings, not a measured end-to-end UI speedup.

## Note-home read scope and duplicate-view retirement

`GET /api/overview?scope=notes` reads unresolved Agent/Broker sources directly,
plus existing durable health, Monitor dashboard/runs and notification status.
It does not scan Agenda, Scorecard, Retro, research candidates, due questions,
Observation/Decision reviews, materialized queue history/metrics, backup inventory
or post-market status. These remain available from the unchanged full Overview
read after explicitly opening background details or selecting a review tab.
Coverage names only the operational sources actually read; failed and exactly
bounded reads are unavailable/partial, never a claim that the archive is clear.
No GET reconciles queue state or retries an order.

The home page's duplicate Catalyst Pulse, Monitor posture/run history, Post-Market
Run and Local Runtime cards are removed. Agenda owns calendar details, Monitors
owns rule/run history, and System owns post-market and maintenance receipts; home
keeps links, risk exceptions, live pending-action recovery and optional review/data
quality details. Existing APIs, route URLs and stored history remain compatible;
no data migration or compatibility deadline is needed. Reverting this presentation
and scope selection restores the prior views without restoring data.

Focused synthetic checks assert skipped service calls, zero writes, visible
partial/failing sources and explicit access to the full workflow. They do not
measure human task time or imply a latency improvement.
