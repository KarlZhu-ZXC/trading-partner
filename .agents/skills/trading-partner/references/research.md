# Research and writes

- Say Research Subject / 标的 / 研究档案, not user-facing “InvestmentCase”.
- Also apply `bossmo-trading-discipline` only when the user is making or reviewing
  an actionable investment judgment for a specific stock or ETF—such as equity
  Thesis conviction, entry, add, hold, reduce, exit, or a concrete setup. Do not
  trigger it from software development, UI/schema/test/docs work, generic fact
  requests, domain field names, or non-equity assets. When activated, require
  `UPSIDE`, `SIDEWAYS`, `PULLBACK`, and `INVALIDATION`; another user-selected
  Strategy may remain primary.
- Research Subject metadata defines durable scope; Thesis holds judgment; Trade Plan
  holds conditional execution intent. Do not merge these concepts.
- Candidate decisions use Propose → explicit Confirm/Reject/Withdraw. Never choose
  the outcome autonomously.
- To attach an Instrument to a Research Subject, propose one `watchlist_item` create
  and obtain the explicit decision if it has not already been provided for that exact action. Confirmation attaches it directly; do not add
  Shortlist, Select, or another status-transition step. Legacy Instrument Selection
  statuses may appear in durable reads but are not a user task to complete.
- When the user explicitly decides in the current chat, relay exactly
  `reviewed_by="user"`, `submitted_via="mcp_chat"` (`codex_chat` remains valid),
  and a bounded `authorization_note`. Ambiguous target/action requires
  clarification.
- Journal, decision, watchlist, policy, Monitor, review, and agenda writes retain
  their idempotency/version/actor gates.
- Structured Phase 4 Decisions may bind `strategy_code`, one of `UPSIDE` /
  `SIDEWAYS` / `PULLBACK` / `INVALIDATION`, an exact same-Subject Trade Plan
  version, and an aware review due time. These fields are intent metadata, not an
  order authorization. An elapsed due time uses the existing durable ReviewItem
  path; a later Decision closes it automatically only when it explicitly
  supersedes that exact Decision. Failed/bounded reads never prove closure.
- Moomoo living notes are Console-only observation revisions. Attribution is a
  deterministic dated-section state machine: each date starts as USER. A line-leading
  `@speaker` marker may introduce any bounded new speaker. Legacy bare prefixes recognize
  only boss墨、宝总、姜汁汽水; other bare `heading:` prefixes inherit the current speaker
  and never create a person. Mid-line @ mentions do not switch attribution. Date order is detected per revision as newest-first,
  oldest-first, mixed, or unknown; the model must not assume one global order.
  Summary fallback may recover only a proven prefix/suffix around the prior FULL
  editor body, never a middle rewrite.
  First-pass and escalated review default to OpenCode Go `deepseek-flash` at
  `max`; the first pass uses a 120-second per-attempt timeout. Model selection is
  configuration-owned; do not infer the active model from historical comparisons.
  Optional `muse-spark-1.3-contributor` at `high` requires
  `EXTERNAL_NOTE_CONTRIBUTOR_TRAINING_OPT_IN=true` recording the owner's explicit
  acceptance of prompt/completion training. Muse 1.3 and Grok 4.6 use Responses.
  Both interpretations are
  drafts only, and `Review as Decision` still requires the
  user to review and save through the existing Decision contract. A saved Decision
  may carry the exact optional `external_note_revision_id`; never substitute a
  display string such as `note_id@vN`, and never treat the link as Thesis, Plan, or
  order confirmation.
- External observation sources share one adapter contract and immutable revision
  store. Summary-only source text cannot reach the model or Decision adoption.
  Moomoo, future TradingView capture, and the local full-text JSON bridge must reuse
  this path instead of creating source-specific research workflows.
- Never control the Moomoo desktop UI to retrieve notes. Optional authenticated
  note enrichment reads an owner-only Cookie file and uses serial, bounded internal
  web reads with a freshly randomized delay per request. Authentication/rate-limit/
  page-shape failure falls back to cache and must not expose credentials or promote
  a list summary to full text. The desktop CEF Cookie store is not an authentication
  source; never claim its locale Cookie is a reusable login session. Do not use
  Computer Use or UI automation against Moomoo. When the user explicitly authorizes
  it, read-only process/IPC/network analysis is allowed if it does not modify app,
  certificate/proxy, account, or trading state and never exposes recovered secrets.
  A Web-session Cookie may be accepted over stdin and must never be printed.
