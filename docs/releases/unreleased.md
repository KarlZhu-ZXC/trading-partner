# Unreleased

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
