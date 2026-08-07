# Unreleased

- Completed the loopback-only local Console product closure: Overview now exposes
  one actionable Attention Queue; Research adds Trade Plan versioning, Timeline,
  Journal/Decision, Challenge Review, and workflow controls; Monitors add scoped
  Run/event/observation drill-down; Operations adds post-market and notification
  receipts, Provider route summaries, scheduler state, and next-due diagnostics;
  Capabilities adds a human-first Market & Technical Lens. All paths reuse the
  existing application services or compact-28 registry and add no order surface.
- Separated Trade Plan execution instruments from condition reference instruments.
  Position sizing and risk continue to use the plan instrument, while a bound
  Monitor may observe and display a condition instrument such as USOIL for a UCO
  plan. The monitor infers a single reference instrument when unambiguous and never
  treats the two instruments' prices or returns as interchangeable.
- Normalized all current Research Subject titles and summaries around their durable
  research object and scope. New Research Subject writes now reject bounded action-plan language
  with non-retryable `CASE_METADATA_POLICY_VIOLATION`; the compact MCP descriptions
  and Trading Partner Skill direct judgments to Thesis and conditional execution
  intent to Trade Plan. Research Subject type and primary Instrument can no longer be changed
  by a generic update candidate after creation.
- Enforced the Research Subject → Thesis → Trade Plan lifecycle: a Draft/non-tracking Research Subject
  can no longer receive a live Thesis or ACTIVE Trade Plan, and a tracking Research Subject
  cannot be retired while live child state remains. Conflicts are typed as
  non-retryable `RESEARCH_STATE_CONFLICT`; no hidden activation or cascade occurs.
- Fixed existing Thesis revisions so an explicit `thesis_status` is persisted under
  strict review while an omitted status preserves the current value. Closing a Research Subject
  now has a usable inside-out path: archive Plan, retire Thesis, archive Research Subject.
- Added durable-only Data Quality Center diagnostics for legacy Research Subject/Thesis/Trade
  Plan lifecycle conflicts.
- Generalized the Telegram notification outbox to `notification_outbox` with
  closed `MONITOR_EVENT`, `MONITOR_RUN`, `SYSTEM`, and explicitly authorized
  `MANUAL` sources. Migration `0030_generic_notification_outbox` preserves historical
  Monitor event/run rows and restores the legacy table on downgrade.
- Added the operational-only `trading-partner-notifications` CLI (`status`,
  `test`, `flush`, and stdin-backed `enqueue`); the Monitor CLI name remains an
  alias. Manual notifications are idempotent, expiring, HTML-escaped, and have
  no order effect. The public MCP surface remains exactly `compact_28`.
- Added `display_price` and `price_basis` to OTC quote results so Dukascopy
  XAUUSD/XAGUSD midpoint observations are directly displayable without
  misrepresenting them as traded `last` prices.
- Fixed Yahoo pre-market fallback when no same-day minute observation exists:
  a prior-day post-market price now keeps its own session, uses that day's
  completed regular close as `previous_close`, and discloses
  `INTRADAY_QUOTE_UNAVAILABLE` instead of slipping the baseline back two sessions.
