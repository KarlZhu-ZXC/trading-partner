# Unreleased

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
  post-market job now synchronizes transactions and materializes Unlinked Activity before the
  Watchlist refresh. Follow-up slices add durable Daily Equity/Journal activation, mandatory
  preview plus append-only Cycle split/merge/relink, period action recurrence, a unified
  Decision→Order→Activity timeline, and optional exact Decision/Plan links on order previews.
  Migration head is `0063_agent_image_attachments`; the 27-tool compact surface remains
  below its schema budget.

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
  Go now includes `muse-spark-1.2-contributor`; models route
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
