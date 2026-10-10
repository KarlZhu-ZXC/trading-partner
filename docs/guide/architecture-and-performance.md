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
