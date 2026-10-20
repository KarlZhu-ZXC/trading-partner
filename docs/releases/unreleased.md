# Unreleased

- Identify Dukascopy Jetta WAF challenges accurately, including HTTP 202 empty
  HTML responses. Suppress repeat Jetta calls for five minutes, retain configured
  legacy fallback and validated history cache, and allow recovery after cooldown.

- Clarify note extraction prompts: preserve explicit trading intent even when risk
  details are missing, keep inline quoted speakers in summaries under unchanged
  block labels, and consolidate dated history by author without dropping material
  plans. Repair feedback now distinguishes missing tool calls, attribution mismatch,
  invalid JSON and schema violations instead of calling every failure invalid JSON.

- Interpret TP using execution context rather than assuming every mention is an
  unfilled plan. Authorized evaluations can supply separate owner clarifications
  and saved transaction context without changing immutable note text or passing
  grading rubrics to the model. Existing runtime callers do not fetch new context.

- Reject note interpretations after attribution validation fails, including after
  the bounded repair attempt; never retain a parsed but invalid candidate.
- Send Go GLM 5.3 Flash reasoning strength without its incompatible native thinking
  field. Add an evaluation-only direct connection control without changing runtime
  proxy settings; preserve failed attempts and private source reviews.

## Optional CLI fallback for note interpretation

- Add an opt-in official OpenCode CLI fallback for first-pass note interpretation.
  It requires OpenCode 1.18.0 or newer and remains disabled by default. Transient,
  quota/admission and exhausted output-repair failures may use the configured
  allowlisted model; authentication/configuration failures and cancellation do not.
- Preserve explicit private-content authorization and exclude training-enabled
  models. Pass input over stdin into owner-only temporary storage, remove that
  storage after completion/cancellation, and reject tool execution. Keep TLS
  verification and the configured proxy; diagnostics never include private text,
  credentials or raw upstream errors.
- Retain schema/author validation and bounded repair. Record the actual
  `opencode_cli` model and `provider_default` effort. Reserve 32,000 shared
  reasoning/output tokens for Cat Jam, DeepSeek, GLM and LongCat; reject truncated
  output with a closed `token_limit` diagnostic. Escalated review and formal
  judgment/order permissions remain unchanged.
- Configuration and supported models are documented in the
  [operator guide](../operations/local-console-and-maintenance.md).

## Personal conclusions and thinking history

- Save a personal conclusion beside a FULL note without a conversation or Research
  Subject. Reuse global Journal NOTE records, exact source links and user confirmation;
  formal investment judgments and order permissions remain separate.
- Recover uncertain saves by exact idempotency receipt. Retry the original request;
  browser session storage retains only its ID and equality fingerprint, never text.
- Browse versioned notes, personal conclusions and discussions together by exact
  current Instrument mapping. Read historical text explicitly and request saved
  transactions for the displayed period separately; timing does not imply execution.
- Disclose history bounds and source failures, and reject cursors after history changes.
  No database migration, model call, provider refresh or new public MCP operation.

## Faster note reading and archive browsing

- Journal note links load only the selected judgment and saved positions alongside
  Subject choices. Other Journal sections load when another tab is selected.
- Read discussion history only when expanded, without repeating account context;
  note content and holdings pagination skip discussion history. MCP defaults remain.
- Browse older saved notes and search latest titles or Instruments across the archive.
  Search is a read-only POST to keep private terms out of URLs; full bodies are loaded
  only for the displayed page, and search never searches body text.
- Avoid deep-copying immutable transaction values during context fingerprinting;
  retain corrupt-record validation, exact sorting, backfill detection and disclosures.
- Remove 75 duplicate source assertions covered by existing navigation/Journal browser
  checks. Keep confirmation, provenance, privacy and recovery contracts.

## Lighter local verification

- Console `npm test` runs unit/source checks without a build or server. Separate
  `test:render` and `test:e2e` commands reuse a build; CI retains the complete
  `test:full` pipeline. Static contracts no longer start Next.js.
- Consolidate duplicate note flows across viewports and remove incidental page-title,
  logo-count and fixed-layout assertions while retaining shared accessibility,
  authorization, exact-revision, privacy and recovery checks.
- Scope local verification and documentation to the change; reuse successful builds,
  avoid tests for copy-only edits, and keep routine contributions and PRs concise.

## A smaller note-led workspace

- Clarify that Moomoo notes support the core workflow independently; Codex
  discussion and saved conclusions are optional. Align Notes copy and acceptance
  criteria while retaining the existing formal-review gates.
- Keep owner changes visible regardless of discussion receipts, collapse the optional
  discussion section, and link FULL notes to their exact Journal revision. Standalone
  revision reads require no Subject; independent Decisions retain matching Subject,
  rationale and explicit confirmation checks. Missing revisions never use the latest
  note, and reading/reloading never starts a model or records a Decision.
- Keep Notes, Portfolio and System as daily navigation; retain all advanced routes,
  risk visibility, history and exact action gates.
- Load Copilot only when opened, preserving drafts after collapse and prepared
  Question Research handoff. Defer numerical technical-adapter imports until use.
- Include durable cross-account positions and related transactions in note metadata,
  with bounded stable pages, separate source failures and explicit historical gaps.
  Option-symbol hints remain unverified; multiplier/Greeks are never guessed.
- Detect broker cost/P&L disagreement without replacing source facts. Keep partial
  Moomoo authentication/sync failures visible rather than claiming a complete sync.
- MCP v16 adds optional context_cursor to view_get/note; nine public tools,
  24 capabilities and 109 operations remain. No database migration or runtime
  configuration change. Reconnect MCP clients after deploying this source version.
- No low-usage capability, stored record or installed scheduler is removed.

- Stream note-ledger candidates in bounded batches instead of materializing the full
  matching history; retain exact aware-time ordering and the overflow disclosure.
- Prefer saved Instrument Master option relationships and recorded multipliers over
  symbol hints; include registered adjusted contracts, exclude explicit conflicting
  relationships, and keep missing terms/Greeks unknown. No live lookup is implied.

- Build US/Korean trading schedules only when queried and import Yahoo SDKs only
  for explicit Yahoo operations; preserve calendar bounds and provider errors.
- Keep disabled tracing free of SDK/exporter initialization. Add reproducible
  isolated MCP startup measurement and dependency-blocked note/account read checks.

- Keep Advanced Workspace usable in a collapsed sidebar with an icon and preserved
  link names; keyboard expansion restores the desktop navigation width.
- Capability audits retain validated observed dates and distinguish calendar span
  from recorded days, rejecting zero-call invocation evidence and complete-coverage claims.

- Split TA-Lib and the tracing SDK/exporter into `technical` and `tracing` install
  extras. `chart`, `all` and source development retain their required dependencies.
  Minimal installs return explicit extra guidance; enabled tracing fails without its
  SDK rather than silently switching off. Add offline installed-wheel verification.

- Batch note latest-version queries and compare predecessors only for the displayed
  inbox rows, retaining ordering and discussion coverage. Stream ledger Core mappings
  with complete domain validation and the same aware-time newest-set ordering.
- Show bounded saved activity coverage receipts with exact source windows and gaps.
  Do not infer complete history; source failures/future receipts stay explicit and
  receipt changes invalidate old context cursors. Add a synthetic workload benchmark.

- Move Yahoo SDK and exchange calendars into `market-data`, retained by `all` and
  development installs. Missing dependencies return explicit extra guidance; absent
  calendars stop the SGOV path before account, quote, notification or order calls.
  The actual minimal wheel works without NumPy/Pandas or those optional libraries.

- Note-home reads now skip archive review scans, queue history/metrics and maintenance
  inventory until the background workspace is opened. Unresolved broker/Agent actions
  still come directly from durable sources; failed or bounded coverage stays visible.
- Remove duplicate home cards for calendar, Monitor/run history, post-market status and
  local maintenance; link to Agenda, Monitors and System, which retain their full views.
  The full Overview API and explicit review-queue refresh remain compatible.

- Replace manual note-ID handoff with a reference-only Copy Codex Request action,
  including a manual-copy fallback. No private source text or account values enter
  the clipboard, and copying never opens a chat or invokes a model.
- Read previously saved discussions in the existing note panel using the existing
  revision-bound, redacted text pages. Keep original source attribution, independent
  private-read selection, bounded-history disclosure and read-only retry. Reading
  one discussion does not return other discussions' body previews.

- Batch predecessor reads for displayed notes, reducing a 30-row inbox from 33 to
  four SELECTs. Preserve exact per-note version bounds, future-source exclusion,
  independent corrupt-row disclosure, complete response semantics and zero read
  writes. No new cache, migration, public operation or manual maintenance step.

- Harden gold/Monitor Telegram alerts: retain the last evaluated same-version
  rule state across outages, preserving recovery thresholds and avoiding duplicate
  triggers on data restoration. Show partial-outage/recovery status and retain
  distinct rules sharing a threshold. Recheck expiry per queued message and start
  delivery retry delays from the response time. No migration or new owner settings.

- Tighten both note interpretation prompts around source fidelity: leave unspecified
  scenarios unknown, preserve withdrawn/current and external/owner boundaries,
  and avoid invented ranges or actions. Authorized two-note comparisons additionally
  tighten direction, horizon, execution ambiguity and contradiction handling; model
  choices remain unchanged and private comparison drafts do not change judgments.
- Allow five minutes per note interpretation/review attempt, with matching defaults,
  provider settings and service limits; other workflow timeouts remain separate.
- Route newly discovered Go `grok-4.7` and `gpt-6-luna` through their documented
  Responses endpoint. Other request compatibility remains subject to live validation.

## Reusable note model evaluation

- Add opt-in, resumable Go model/effort comparisons with private owner-only results,
  versioned synthetic cases and source-grounded human review. Timeouts and invalid
  outputs count as incapability in the full task denominator; retries retain failures.
- Allow explicit reasoning effort in the note interpretation service while preserving
  the production default of max and accurately recording the requested effort.

- Note evaluation retries transient failures up to three times with bounded backoff,
  immutable per-attempt results, safe failure diagnostics and a resume-stable budget.
