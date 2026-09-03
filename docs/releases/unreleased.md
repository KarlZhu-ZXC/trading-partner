# Unreleased

- Began the Moomoo-first judgment-intake transformation. Added an append-only
  Observation review ledger with `PENDING`, `DEFERRED`, `ADOPTED`, and `NO_ACTION`
  outcomes, exact Note Revision and Decision provenance, optimistic versioning,
  idempotency, and same-Subject/revision validation. Eligible successful FULL
  interpretations now materialize durable `OBSERVATION_REVIEW_DUE` items; Console
  exposes review state and closes the review only after the exact Decision succeeds.
  Migration head is now `0072_external_note_review_drafts`.
  A shared deterministic `ViewReviewService` compares the draft with the latest
  confirmed Thesis, Trade Plan, Decision, durable Positions, and linked Monitors,
  publishes explicit coverage and allowed actions, and derives Current View from the
  exact adopted review plus Decision rather than another mutable truth table. Home
  now starts with View Inbox; Journal exposes Current Confirmed View, renames Notes
  to View Inbox, supports durable deferral, and shows the baseline comparison before
  confirmation. The explicit read-only `view_inbox`, `view_review_get`, and
  `current_view_get` tools plus confirmation-gated `view_review_run` move the public
  snapshot from 27 to 31 capabilities under
  `mcp-vnext-shadow-v4`; no prior capability was removed or renamed, and tool count is
  no longer a product invariant. Private full note bodies remain Console-only and
  model output cannot confirm a review outcome. Continuous first-pass structure keeps
  OpenCode Go `qwen3.8-flash` at `max`; deterministic escalation stores a separate
  append-only draft. The owner runtime explicitly authorizes
  `muse-spark-1.3-contributor` at `high`; generic installs fail closed unless the
  separate Contributor-training opt-in is present, and Qwen 3.8 Max remains the
  zero-training fallback. Known withdrawn-action and invented-condition failures are
  frozen as sanitized regression benchmarks. The completed transformation ledger was
  folded into `AGENTS.md`, Phase 4, current guides, and this release record, then
  removed as a parallel specification; Git history retains its full progress.

- Consolidated project documentation around one current source-of-truth hierarchy.
  `AGENTS.md`, the Trading Partner Skill, roadmap, capability guide, Phase 4 spec,
  operations guide, known issues, and documentation index now describe the implemented
  Phase 1–4D / 27-tool / migration-0070 boundary consistently. Removed completed plans,
  dated MCP smoke receipts, superseded Console design audits, and their screenshots;
  Git history and release notes retain historical evidence without leaving parallel
  current specifications or broken links.

- Hardened the repository and installed-runtime boundary without expanding product
  scope. Private Observation inbox files and real account-basis checkpoints are now
  Git-ignored; Wheels ship only an empty checkpoint example. Installed runtimes pin a
  stable `RUNTIME_ROOT`, and every mutable token, lock, attachment, backup, Observation,
  and reconciliation artifact derives from it instead of the package directory.
  Console note bodies load only when Journal Notes is opened, background analysis tasks
  consume unexpected failures into closed codes, and infrastructure no longer imports
  Telegram interface policy. Complexity ceilings now ratchet both Python and Console
  hotspots. LAN passwords require 16 characters, login throttling is client-scoped, and
  Console-authenticated fetches reject non-relative targets. CI runs security, backend,
  Console, and packaging/migration gates in parallel; Alembic checks are explicitly
  forward-only for irreversible data repairs. Dependency automation was added and pypdf
  was raised to a fixed release.

- Added strict owner-verified Broker position-basis checkpoints for transferred or
  imported holdings whose acquisition basis is absent from the activity API. FIFO
  and Trade Cycle projections now rebase only open lots, exclude a replaced zero-cash
  position import from trade counts, and retain exact source IDs/document hashes.
  Real checkpoint values now live only in the owner-controlled runtime secret directory;
  the repository and Wheel contain an empty example. Portfolio separates net trading
  P/L, dividend income, and total P/L without double-counting dividends. Timestamped
  market value supplies a deterministic unit-price fallback when a position snapshot
  omits `marketPrice`.

- Removed duplicated Journal Behavior summaries and corrected numeric numerator /
  denominator rendering. Win Rate, Payoff Ratio, coverage, and the remaining
  behavior metrics now show one verified result with their calculation, exclusions,
  sample status, and fail-closed handling for inconsistent payloads.
- Replaced Journal’s five-item `Contributors` excerpt with a complete, paginated
  Traded Instruments table. The table is built from durable Broker fills, supports
  autosuggest multi-filtering and sortable headers, and discloses fill, quantity,
  account, Cycle, known P/L coverage, and first/last trade facts.
- Canonicalized Moomoo SOXL account positions, orders, and transactions to the
  Instrument Master ETF identity (`etf:US:SOXL`). Existing Moomoo history is
  migrated from the former assumed-equity identity so Portfolio and Trade Cycle
  projections no longer split SOXL into two Instruments.
- Standardized Console content accordions and cross-page shortcuts with shared
  `Disclosure` and `QuickLink` primitives. Replaced the raw Trade Cycle override
  form with a guided Split / Merge / Relink editor using searchable Cycle choices,
  readable activity assignment, and mandatory impact preview before append.

- Replaced line-local Moomoo viewpoint attribution with a deterministic dated-section
  state machine. Each date starts as USER; an explicit person owns following unlabeled
  paragraphs until the next date, explicit speaker, or explicit USER label. Structural
  headings such as 整体观点、风险、结论 inherit the current speaker. Sanitized regression
  fixtures verify continuation ownership and reset the next dated section to USER; the
  same strict OpenCode Go interpretation succeeds. Model-output failures now retain
  closed tool/JSON/schema/attribution error categories without storing raw output.
  Note date order is now detected independently per revision as newest-first,
  oldest-first, mixed, or unknown and passed explicitly to the model. Summary-only proof recovery
  preserves prior editor boundaries while accepting a strictly proven newer prefix
  and/or older suffix; any middle insertion or rewrite remains fail-closed until FULL
  editor text is available.
  Observation CLIs now support explicit `--reanalyze-all` for every latest FULL
  revision. Forced reanalysis includes prior successes and failures, reports aggregate
  closed error codes, and never lets a failed new attempt overwrite a usable prior
  success. The 2026-08-30 full rerun attempted all 16 notes and initially retained
  14 successful interpretations under DeepSeek Flash.
  Private-note model calls now allow 120 seconds per attempt (up from 80, still at
  most one schema repair). The default interpretation model then moved to OpenCode Go
  `qwen3.8-flash` at `max`; targeted sanitized runs satisfied the same strict schema and
  restored the latest observed set to complete successful interpretation coverage.

- Extended the existing XNYS due-checked post-market automation instead of adding a
  second scheduler. After account/transaction, Daily Equity, and exact Watchlist work,
  it now runs one read-only `MOOMOO_NOTE` Observation sync with `analyze=false`.
  Observation step status, notes seen, revisions created, and FULL/summary counts are
  stored on the same durable per-session receipt. Authentication or source failures
  degrade only this step and remain retryable without rolling back completed portfolio
  or Watchlist facts. The migration chain later advanced to
  `0070_retire_unlinked_review_items`.

- Separated Trade Cycle lifecycle, classification, and data-quality presentation. OPEN is now
  active green, CLOSED remains clearly neutral, UNRESOLVED is red, and incomplete/unclassified
  facts are amber. Journal's header now names the aggregate as Data Quality with incomplete and
  unresolved counts, adds an inline status guide, and replaces the raw Portfolio text link with a
  consistent `Open Portfolio` quick action.

- Replaced Journal's long Account, Instrument, Research Subject, and Classification selectors with
  shared autosuggest multi-select filters. Typed text only searches durable options; it does not
  affect results until the user selects a suggestion. Selected values remain removable chips and
  multiple values compose without accepting custom identities. Trade Cycle page capacity is now
  derived only from the browser viewport (4/6/8/10 rows); fixed list rows and an independently
  scrolling detail pane prevent either side's content from changing the other's height.

- Enriched Moomoo historical deals with bounded exact order-fee reads. Fee totals are allocated
  once across partial fills and incomplete fee responses fail open without dropping trades. Journal
  and Portfolio now show explicitly labelled Gross P/L with `Fees unavailable` when Net P/L cannot
  be established; aggregate behavior and Results continue to use only Net-complete Cycles.

- Migrated the Console Journal to an account-wide review workspace. Journal now opens on the
  complete durable portfolio scope, keeps Research Subject as an optional filter, adds compact
  Period/Account/Instrument/Quality controls, and places Trade Cycles in a master-detail review
  browser. The Reviews tab now owns immutable Trade Retro review revisions; the legacy `/retro`
  route redirects to Journal instead of maintaining a second editor.
- Reworked imported Moomoo living notes into the provider-neutral Journal Notes master-detail
  view. Full-text revisions show exact change summaries, USER scenario drafts, attribution,
  Position/Cycle context, and revision history. `SUMMARY_ONLY` content remains non-adoptable, and
  `Review as Decision` still requires explicit user review with an exact Observation Revision
  reference.

- Audited the live Console workflow and removed several high-friction states. Home now
  groups Review Queue work instead of showing contradictory zero-action/217-review
  messages, zero-sample durations remain unavailable, and Monitor posture summarizes
  quiet rules. Journal can browse all observations, jump to a matching Subject, groups
  repeated review sources, fixes numeric Plan versions, hides historical model output
  for summary-only notes, and opens a prefilled Research Subject draft for unmatched
  observations. Observation filtering and exact background Analyze/Retry avoid a full
  source refresh or repeated 500 KiB Journal polling. Monitor Overview surfaces exact triggered/unavailable rules;
  Research option labels and Portfolio side/type values are consistently cased; Operations
  table inventory is collapsed by default. Read pages boundedly recover through a real
  15–16 second local API restart without retrying business errors.
  Observation cards now load bounded revision history and line-level additions/removals on
  demand. Journal renders strategy_v1 as four scenario blocks with the full Thesis behind a
  disclosure, and Portfolio Exposure defaults to the eight highest-priority rows. At 390px
  and a 720px effective zoom width the audited Journal has no horizontal overflow; Card
  actions stack on mobile. Dialogs trap Tab/Shift+Tab and restore focus, analysis completion
  is announced through a polite live region, and shared small metadata colors now exceed
  4.5:1 contrast in both themes. The same audit now covers specialist pages: Agenda hides its
  rare Provider-sync form behind a disclosure and fixes the field layout; Trade Retro groups
  repeated immutable attempts by period; Scorecards removes the zero-result paginator and gives
  actionable empty-state guidance; and Capabilities collapses its 27 tools into 16 searchable
  categories while removing duplicate entry controls.

- Added a Moomoo-first living-note intake to the existing Journal rather than a new
  research module. Local private notes are imported as immutable revisions; dated-section
  state deterministically carries explicit speakers across continuation paragraphs. OpenCode Go
  `qwen3.8-flash` at `max` effort creates a strict, read-only four-scenario
  and revision-change draft in the background. `Review as Decision` only prefills the
  existing confirmation-gated Decision dialog. The current migration head is
  `0070_retire_unlinked_review_items`;
  the public MCP surface remains 27 tools. Summary-only list text is now blocked from
  model analysis and Decision adoption, and cache eviction no longer creates a false
  content revision when the prior full revision has the same visible summary.
  Observation ingestion is now provider-neutral: source capability metadata, multi-source
  aggregation, `/api/observations/sync`, and the closed full-text Local Observation Bridge
  let a later TradingView adapter reuse the same revisions, attribution, model draft, and
  Decision handoff without another product module.
  Stable source-revision keys replace content-hash uniqueness so adapter replay is
  idempotent, late stale observations fail closed, and a later genuine reversion to
  historical content remains a new immutable revision. The current migration head is
  `0070_retire_unlinked_review_items`.
  A bounded cross-process observation lock now coordinates Console, CLI, and capture
  adapters; contention returns typed retryable `OBSERVATION_SYNC_BUSY`.
  Moomoo full-text enrichment can now read the authenticated internal note-list and
  editor HTML without controlling the desktop UI when the user provides a separately
  authenticated Web-session Cookie over stdin. Investigation confirmed that the
  desktop CEF Cookie database contains only locale state; native-bridge authentication
  is not reusable as a standard Cookie. Computer Use/UI automation remains prohibited;
  explicitly authorized read-only process, IPC, and protocol diagnostics may be used
  without changing app, proxy/certificate, account, or trading state or exposing any
  recovered secret. The configured Cookie is stored only in an owner-only secret file;
  serial list/detail reads sample a new
  bounded random delay before every request and cap both stock and note counts.
  Authentication, rate-limit, transport, or page-shape failures expose only closed
  warning codes, fall back to cache, and never promote list summaries to full text.
  A user-authorized CEF NetLog acceptance run recovered the in-memory Web Cookie
  without UI automation, deleted the raw NetLog/TLS keys after extraction, and
  improved the current Moomoo set's FULL coverage. Proven list-tail
  promotion now preserves prior editor paragraph boundaries and appends only a
  strictly ordered, punctuation-separated tail as USER text. This prevents a named
  viewpoint from swallowing later user-dated updates. A sanitized fixture reproduced
  `NOTE_INTERPRETATION_INVALID_OUTPUT`; the corrected revision passed the same
  strict model schema.
  Historical summary-only identities were then backfilled through the same randomized
  authenticated editor path, bringing the durable Observation Inbox to complete FULL
  coverage for every identity Trading Partner has
  observed; Moomoo still exposes no global account-level note index that can prove no
  never-observed stock note exists.

- Every Monitor state-transition notification now ends with one read-only Chinese
  model analysis capped at 160 characters. Same-Monitor transitions in one run share
  one call; successful composite judgment summaries are reused. No-change runs make
  no analysis call. Analysis uses `max` effort; an 80-second timeout or invalid output appends an
  unavailable note without blocking or changing the deterministic event. Post-market
  digests collect analyses in a final mobile-readable section.

- Added persistent authenticated LAN mode to the Console supervisor. `console
  install --lan` generates/reuses an owner-only password file, stores only its path
  in launchd, keeps the API loopback-only, and makes normal Console restarts bring up
  the LAN Web automatically.

- Every configured Agent model now has default Web Search access through the private
  read-only `tp_web_search` tool backed by a server-owned Tavily Search sidecar.
  Answer models never receive the sidecar key, no additional Bailian model call is made,
  queries persist only as hashes, source
  URLs remain bounded, and web context cannot override canonical Trading Partner facts.

- Composite Monitor judgment now defaults independently to Bailian
  `deepseek-v4-flash-0731` over Chat Completions with JSON Object output and one
  bounded structure-only repair. Bailian `qwen3.8-max` is the Responses fallback;
  Console Agent model defaults are unchanged.

- Agent Provider failures now render as durable, dedicated Console notifications with
  the exact internal error code, safe HTTP status, Provider/model identity,
  retryability, bounded attempt count, and a fixed human explanation. Migration
  `0054_agent_turn_failure_metadata` restores the same notification after refresh while
  excluding URLs, payloads, headers, response bodies, exception text, and credentials.
  HTTP 400/422 now map to non-retryable `PROVIDER_REQUEST_REJECTED`; 401 and 403
  render distinct authentication versus model-access guidance. OpenCode Zen free
  chat models that reject reasoning no longer receive it, while Ox Alpha Free
  (`x-preview-f-free`) exposes its verified `low`/`high`/`max` effort levels.
  The notification is pinned above the conversation scroll region and has a local,
  persistent dismiss control that does not mutate the durable failed turn.

- Documented Phase 4 as the planned Trading Journal product loop: Decision/NO_ACTION,
  order lifecycle, broker activity, deterministic Trade Cycles, trustworthy native-currency
  performance, strategy-linked behavior review, and one new Console Journal workspace without
  expanding the 27-tool public MCP surface or execution authority.

- Started the Phase 4A reuse-first Console slice by turning the existing Decision Workbench into
  `Journal`. Six module cards now form four workflow components—Decide, Observe, Execute, and
  Review—using the existing Research, Monitor/Catalyst, durable Position/Transaction, Portfolio
  Performance, Trade Retro, and Scorecard services. Agenda, Scorecards, and Retro remain intact as
  specialist deep links but no longer occupy primary navigation. Journal can append a compact
  strategy_v1 Decision/NO_ACTION through the existing Decision Record contract without authorizing
  an order.

- Extended the existing Decision Record with nullable structured Phase 4A metadata instead of
  adding another decision module: Strategy code/version, one four-scenario context, exact same-
  Subject Trade Plan ID/version, and aware review due time. The fields participate in idempotency,
  persist through migration `0055`, remain backward-compatible with historical records, and have
  no execution effect. Journal also reuses the Research Timeline to show recent Decisions directly.
  Elapsed review due times now reuse the existing ReviewItem/Attention lifecycle: an exact later
  superseding Decision or explicit manual resolution closes the item, while failed or bounded reads
  never infer closure. Migration `0056` safely widens the existing ReviewItem source vocabulary.

- Added the Phase 4B Trade Cycle first slice by reusing durable Account Transactions and FIFO
  semantics. `portfolio_analyze/trade_cycles` reconstructs long-only OPEN/CLOSED/UNRESOLVED cycles,
  scale-ins, reductions, closures, and per-account/native-currency re-entry without persistence or
  Provider access. Missing fees/prices, oversells, orphan sells, incomplete coverage, and truncation
  stay explicit. Journal now renders the latest real Cycle instead of a placeholder. The public MCP
  remains 27 tools and compact input-schema bytes decreased from 26,135 to 26,116.

- Completed the next Journal loop slices without adding a public tool: append-only Unlinked
  Activity annotations and ReviewItems, deterministic native-currency TWR/MWR-XIRR/drawdown,
  and no-score behavior cohorts with explicit denominators and exclusions. The Journal can
  classify an exact unmatched trade in one save, Portfolio renders return coverage, and the
  post-market job originally materialized Unlinked Activity before the Watchlist refresh. The
  current Journal removes that Timeline section and retires Unlinked Activity as a Review Queue
  source while retaining the read-only projection and append-only annotations. Follow-up slices add durable Daily Equity/Journal activation, mandatory
  preview plus append-only Cycle split/merge/relink, period action recurrence, a unified
  Decision→Order→Activity timeline, and optional exact Decision/Plan links on order previews.
  Migration head is now `0070_retire_unlinked_review_items`; the 27-tool compact surface remains
  below its schema budget.

- Added fail-closed Schwab dividend identity enrichment and per-Instrument performance
  decomposition. Cash-only dividend rows now use a description symbol only when it uniquely
  matches an exact same-account equity/ETF candidate; existing NULL identities are enriched
  only when every other normalized fact matches. Performance exposes Net Trading P/L,
  Dividend Income, and Total P/L while unsupported corporate actions remain incomplete.

- Added a no-live-side-effect Phase 4 acceptance flow and Journal Playwright flow. The backend
  test uses one temporary database to prove Decision/Plan-linked fake order preview+submit,
  exact Fill annotation, CLOSED active Cycle, Daily Equity/TWR, behavior coverage, recurrence,
  and FULL_CHAIN Timeline output. This uncovered and fixed the persisted Behavior Review DTO
  hydration boundary (`from_attributes=True`).

- Separated local Console completeness from MCP transport size limits. The shared
  capability registry now exposes a validation-identical `invoke_uncompacted` path
  for loopback BFF reads, while public MCP and the explicit Capability Workbench
  retain 15 KiB compaction. Portfolio, Watchlist, Research, Monitors, Workbench,
  Agenda, Retro, Scorecards, Operations, and Overview no longer lose durable rows
  to `_truncated` markers.

- Monitor Library now reads the complete validated local dashboard instead of the
  MCP transport-compacted projection. XAUUSD, light-oil, and later Monitor
  definitions are no longer omitted after the first large dashboard items;
  truncation markers are filtered and surfaced as an explicit error if encountered.

- Research Pending Candidates now use complete, validated local state instead of
  MCP transport-compacted payloads. Each approval card shows proposal identity,
  audit metadata, scalar changes, narratives, Conditions/Assumptions/Invalidations,
  and the complete payload before Confirm/Reject/Withdraw; truncation markers are
  never rendered as Candidates, and decision errors stay on the owning card.

- Portfolio Exposure Instrument cells now show durable aggregate Quantity and
  quantity-weighted average Cost, order exposure groups by Weight descending, and
  default each Accounts & Holdings table to Market Value descending.

- Added separate first-class OpenCode Zen and OpenCode Go support for the shared Agent and
  composite Monitor judgment runtime. They share one account credential by default while
  retaining separate Base URLs, model directories, optional key overrides, and route receipts.
  Go now includes Responses routing for Grok 4.5/4.6 and Muse Spark 1.2/1.3
  Contributor. Private Observation review may select Muse Spark 1.3 only behind an
  explicit Contributor-training opt-in. Models route
  through Responses, Chat Completions, or Messages according to a closed protocol map.
  Neither Provider advertises native Web Search, and unknown future model IDs
  remain hidden, and subscription/entitlement failures stay typed and secret-safe.
  Composite Monitor requests now use one schema-bound function call on compatible Chat
  Completions routes and strict JSON Schema on Responses routes, fall back to JSON-object
  mode only after an explicit structure-parameter rejection, project only declared fields,
  and permit one locally revalidated structure-only repair.

- Completed the 2026-08 technical platform upgrade: secret-safe OpenTelemetry,
  Playwright browser gates, SQLite WAL diagnostics, durable Operational Job leases,
  generated Pydantic→TypeScript contracts, shared Agent-LLM resilience routing, typed
  Agent answer blocks, and optional local-only FTS5/vector hybrid Research Search. Public MCP
  remains 27 tools and all execution confirmation boundaries are unchanged.

- Hardened the shared Agent decision loop: Chinese daily-attention intents route to
  `investment_case_read/attention`; incomplete source windows are explicitly partial;
  expired Candidates are not presented as confirmable; Auto failover has a hard
  read-only tool surface; Console streams recover through bounded durable replay;
  model/reasoning choices persist per Provider; and legacy `/chat` opens the Agent Rail.
- Re-ran the current checkout through a real FastMCP stdio child process on 2026-08-20.
  The 27-tool host completed `system_health → attention → exact next_read` with no writes.

- Standardized specialist Console layouts on the Research workspace pattern while
  leaving the Overview home layout unchanged. Portfolio, Workbench, Monitors,
  Catalyst Agenda, Trade Retro, Scorecards, Operations, and Capabilities now collect
  infrequent page actions in the shared vertical Header menu; view filters remain in
  compact control bars, and all existing confirmation boundaries are preserved.
  Overview remains excluded. The legacy `/chat` route now redirects to the shared
  Agent Rail instead of maintaining a second conversation implementation.

- Recorded the 2026-08-17 MCP host decision-loop read-only smoke. Current
  source checkout passed; the already-connected Grok stdio process was still
  pre-PR3 and must be reloaded.
- MCP Registry invoke and FastMCP stdio share Agent result compaction. Canonical
  JSON stays within 15 KiB; final TextContent stays within 16 KiB. Envelope
  `ok/as_of/warnings/errors` survive truncation. Chart ImageContent is untouched.
- Closed-variant validation failures return `TOOL_INPUT_INVALID` with bounded
  field names and reason codes. Transport-level missing `request` stays a
  standard MCP error and is still redacted.
- External MCP hosts can read a durable-only Attention inbox through
  `investment_case_read/attention`. The query never reconciles ReviewItems.
  `system_health.attention_summary` reports materialized ReviewItem counts only
  and does not replace the inbox. Console workflow/operational attention rows
  reuse the same typed projectors.
- Current-chat authorization is host-neutral: write and order gates accept
  `submitted_via=mcp_chat` with the same user + `authorization_note` rules as
  `codex_chat`. Existing Codex callers keep working; the stored channel now
  identifies the host instead of forcing every stdio chat to impersonate Codex.
- Simplified Console decision flows: explicitly labelled action buttons no longer
  trigger a second duplicate browser confirmation for non-destructive candidate,
  agenda, policy, retro, scorecard, sync, or Monitor-edit operations. Destructive,
  externally executing, OAuth, Monitor-run, and order boundaries remain guarded.
- Added an ACTIVE Trade Plan → Monitor handoff that carries the exact plan/version,
  Research Subject, and Instrument and compiles monitorable conditions without
  copied IDs or duplicated rule entry.
- Added the private `tp_propose` Agent tool for non-effective Thesis, Trade Plan,
  and Instrument-selection candidates. It removes the redundant Pending Action
  wrapper while preserving the candidate's final Confirm/Reject/Withdraw gate;
  the public MCP surface remains 27 tools and order authorization is unchanged.
- Streamlined Review Queue closure: OPEN items may be resolved directly with the
  required note, while optional due-time editing appears only after acknowledge.

- Simplified Research Subject Instrument attachment to `Propose Instrument → explicit
  approval → Instruments`. Console and Agent guidance no longer expose or require
  Shortlist/Select as follow-up steps; legacy selection states remain readable for
  durable compatibility.

- Account Snapshot positions now expose a display-only `snapshot_price`. A broker
  position price remains primary; when it is absent, the value is derived as
  `abs(market_value) / quantity` and explicitly labelled
  `BROKER_VALUATION_ONLY`. The derivation never fills `market_price_at` and is not
  eligible for Monitor, risk-freshness, or order execution logic. Console also
  computes the same fallback for pre-upgrade API responses, treats missing
  broker-native price time as provenance rather than a Portfolio display error,
  and consistently title-cases table column headers.

- Hardened operational recovery: Monitor Provider outages now produce one compact
  interruption card and a distinct blue data-restored notice; retryable scheduled
  quote reads receive three bounded attempts. SGOV completion
  performs three bounded read-only retries and reports the exact blocked stage and
  safe Provider diagnostic. Yahoo adjusted daily bars tolerate only an unfinalized
  terminal adjusted-close row by omitting it with an explicit warning.

- Added the sole unattended execution exception: an installed SGOV-only Schwab
  cash-sweep scheduler. It prepares at 15:45 ET and at 15:55 may submit one current-
  ask BUY LIMIT/DAY/NORMAL order per eligible account after fresh quote, zero-margin,
  open-order reserve, and `$2,000 + $200` cash-floor checks. Stable order identities,
  no-retry `SUBMITTING`/`UNKNOWN` handling, unresolved-queue visibility, and durable
  Telegram outcomes prevent silent or duplicate execution. All other live actions
  remain current-chat confirmation-gated.
- Hardened the Shared Agent Runtime without changing the 27-tool public MCP surface:
  read/action capability discovery, bounded parallel reads, safe schema hints and
  operation-specific result compaction.
- Added durable Agent Turn lifecycle storage and Console refresh recovery through
  migration `0049_agent_turns`.
- Added confirmation-token reissue for durable `PRESENTED` Pending Actions using an
  identity/expiry/version CAS; raw tokens remain one-time and are never persisted.
- Upgraded the Console Agent Rail with navigation-only page context, safe structured
  answer rendering, durable turn/pending-action recovery, Telegram handoff and archive.
- Added a server-side, cached Provider model directory and a linked
  Provider → model → reasoning-effort selector in the Agent Rail. Credentials and endpoint
  URLs remain server-only; submitted model choices are catalog-validated by the runtime.
- Added a 14-case Agent behavior regression catalog. The default Bailian endpoint keeps
  bounded native Web Search/extraction enabled with usage receipts and source URLs;
  unsupported endpoints remain disabled. Agent broker orders remain disabled.
- Closed the 17-item Shared Agent maturity backlog: durable cancel/reconnect/retry and
  orphan recovery, local component supervision, auditable routing, evidence/quality
  guards, typed workbench context, private Review Queue actions through Pending Action,
  resizable/focus-mode Agent rail, durable presentation preferences, protocol-native
  token streaming, Auto read-only failover, and executable runtime behavior gates.
- Provider model selection now fetches each configured Provider's live model directory
  and reasoning-effort choices. Web Search remains enabled by default where supported;
  no opt-in toggle or autonomous broker-order path was added.
- Added bounded Console Agent PNG/JPEG attachments with paste support, private
  owner-checked storage, durable message metadata, context replay, and provider-neutral
  multimodal protocol translation. Telegram remains text-only.
