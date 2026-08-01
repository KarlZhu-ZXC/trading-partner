# Unreleased

- Published `compact-v11` without adding tools. Dukascopy XAUUSD/XAGUSD INTERVAL
  Monitors now recognize the venue's New-York-aligned weekend closure and daily
  maintenance break before Provider access, expose `MARKET_CLOSED` plus the next
  observation window, and stop manufacturing recurring `NOT_EVALUATED` runs while
  the venue is closed. Separately formed weekend CFD prices are not substituted.
- Froze the Catalyst Agenda and Judgment Scorecard implementation plan: planned
  items remain separate from occurred Research Events, queries are durable-only,
  explicit free-provider refresh belongs to a deterministic CLI, source coverage
  and date certainty stay visible, and the public MCP inventory remains 28 tools.
- Persisted bounded, secret-safe Provider route receipts and surfaced their
  24-hour failure/fallback/cache aggregates in the existing durable-only Data
  Quality Center. The 30-day/5,000-row ledger stores only typed routing metadata;
  request fingerprints, Provider payloads, and exception text never enter it.
  Console overview shows recent fallback and failure totals without adding an MCP
  tool or configuration key.
- Added an owner-only, CLI-only Schwab Realized Gain/Loss inspection path for the
  A1 broker-statement reconciliation gate. The strict CSV parser maps recognized
  lot-detail headers by name, uses `Decimal`, preserves missing cost/date/P&L facts,
  rejects traversal/symlinks/duplicate lots, restricts artifacts to `0700/0600`,
  and exposes only file hashes plus redacted account summaries. A durable-only
  comparison command now reconciles one US/Eastern natural month at symbol and
  account level against FIFO after-fee attribution, emits typed residual causes,
  and writes an immutable owner-only JSON draft. It adds no MCP tool, never refreshes
  Schwab, and does not claim that real-statement reconciliation has been signed off.
- Started the bounded A-share model modularization while preserving the stable
  `domain.a_share.models` import façade. Industry-cycle and company-operating
  models now live in a dedicated module, shared invariant validators have one
  internal owner, and market, fundamentals, and research/disclosure models now form
  separate capability modules. Market context, capital/chips, limit-pool/sentiment/
  options, and calendar windows now complete the split. The former 1,837-line file
  is a sub-100-line compatibility façade with stable type/function re-exports;
  architecture tests prevent the monolith returning.
- Split the former 1,618-line A-share DTO file into closed input, shared validation,
  core fact, market/capital, signal/option, and bounded product modules. The original
  import path is now a sub-100-line compatibility façade; schema names, fields, and
  `extra=forbid` behavior remain unchanged and file-size regression caps cover every
  capability module.
- Split strict A-share snapshot Provider-result validation into a dedicated mixin.
  The orchestration service keeps its established private validation entry points
  through inheritance while shrinking from 1,480 to about 1,030 lines; architecture
  tests cap both files so routing and validation cannot silently collapse together.
- Added a durable-only Data Quality Center without increasing the 28-tool MCP
  surface. `system_health` now summarizes latest account snapshot age,
  valuation/price-time coverage, account-activity coverage receipts, and active
  Monitor blind spots, while preserving configuration-versus-live Provider check
  semantics and declaring the missing fallback-history ledger. The local Console
  renders the same evidence as mobile-safe cards; no quality check contacts a broker
  or market-data upstream. Operational health and evidence quality retain separate
  statuses, and a quality-ledger failure cannot hide or relabel base health.
  Monitor coverage is definition-version-aware: a successful run for an older
  version is not accepted as evidence that the current rules were evaluated.
- Reduced the sole cross-layer composition root from 1,049 to 961 lines without
  creating another root. Application-only service/context bundles now live in
  `application/runtime.py`; infrastructure resource ownership and deterministic
  overrides live in `infrastructure/composition/runtime.py`. Architecture tests
  tighten the bootstrap regression cap from 1,050 to 1,000 lines.
- Upgraded the official GitHub checkout, Node setup, and artifact-upload actions
  to their Node 24-backed v7 majors. The Console itself remains tested on Node 22;
  the change removes the deprecated Node 20 JavaScript-action runtime warning.
- Published `compact-v10` without adding MCP tools. The durable-only
  `portfolio_analyze/performance_summary` operation reconstructs native-currency
  FIFO lots or preserves a distinct broker-reported basis, separates realized and
  unrealized P/L, dividends, interest, fees, and external cash flow, and links each
  instrument result to activity IDs and an ending snapshot. Coverage, fee,
  corporate-action, lot-reconciliation, and timestamped-valuation gaps remain
  explicit `INCOMPLETE` results. The local Console exposes the same calculation
  with date/basis controls and drill-down; A1 still requires one broker-statement
  reconciliation before final acceptance.
- Published `compact-v9` without adding MCP tools. `portfolio_analyze/coverage`
  exposes durable, machine-readable account-activity coverage before any P/L claim.
  A0 now preserves instrument-less cash activities, signed native-currency cash
  amounts, explicitly unavailable fees, source event types, and mapping versions;
  repeated syncs report inserted/duplicate counts instead of duplicating ledger
  rows. Schwab long history requests page through bounded 60-day windows, while
  Moomoo trade-only/category/fee gaps remain typed `INCOMPLETE` coverage.
- Published `compact-v8` without changing the 28-tool inventory or runtime input
  validation. The JSON Schema 2020-12 representation now shares closed-operation
  semantics at each discriminated request union, uses compact local definition
  references, and removes redundant constant/enum/nullable syntax. Aggregate input
  schema is reduced from 36,586 to 32,319 bytes, restoring 4,545 bytes of headroom
  for performance-attribution operations.
- Unified the Python distribution, health response, and Console API on product
  version `0.3.0`; Hatch now reads the version from `application.__version__`
  instead of maintaining a second value in `pyproject.toml`.
- Extended the Console Monitor builder to resolve Korea Exchange instruments and
  select `KR_POST_MARKET`; changing between A-share, US, and KR markets now keeps
  an already selected post-market cadence aligned with the market.
- Removed unused Sites/D1/R2 packaging scaffolding and starter icons from the
  loopback-only Console; local vinext builds retain only the runtime worker they use.
- Published `compact-v7` without adding MCP tools. Korea Exchange is now a formal
  `KR` market with local-first Yahoo discovery, canonical bare-code identities,
  quote/batch quote, minute-to-month bars, daily/weekly technical analysis, Manual
  CSV Watchlist support, price monitoring, and XKRX-aware `KR_POST_MARKET` dispatch.
  Yahoo `.KS`/`.KQ`/caret symbols remain provider aliases; Korean fundamentals,
  DART filings, sentiment, breadth, account sync, and Moomoo Watchlist writes are
  explicitly outside this slice.
- Fixed batch quote envelopes when every item succeeds but one or more item results
  are degraded: the batch now emits `BATCH_QUOTE_ITEMS_DEGRADED` instead of violating
  the envelope's degraded-with-warning invariant.
- Published `compact-v6` without increasing the 28-tool inventory. All supported
  instrument-scoped capabilities now share one local-first discovery gateway;
  US ETF workflows use ETF-appropriate news/sentiment rather than equity company
  facts; `market_data_get/quotes` adds bounded batch quotes; and `account_get`
  exposes durable-only positions and transactions.
- Corrected Watchlist and Monitor read semantics: omitted Moomoo item scope selects
  the durable `All` group, explicit Watchlist sync is exact/full, pagination reports
  `total_count`/`has_more`, Monitor-scoped runs exclude sibling observations, and
  Dashboard embeds a compact latest-run summary instead of repeating a full batch.
- Public workflow receipts and research-context hints now name only active compact
  tools plus their operations. Instrument resolve market/asset enum inputs are
  whitespace-tolerant and case-insensitive, while system health distinguishes live
  probes from configuration-only checks.
- Made `market_data_get` quote/bars local-first rather than local-only for typed
  US equity, ETF, index, and futures IDs. A Master miss now discovers and caches
  one validated candidate before fetching market data, while directory outages
  retain their typed Provider error instead of becoming `INVALID_INSTRUMENT`.
- Made every durable account-position column independently sortable in the
  Console and added an adjacent snapshot-price column. The column displays only
  persisted `market_price` paired with `market_price_at`; Schwab/Moomoo snapshots
  that provide market value without an auditable price timestamp explicitly show
  the price as unavailable instead of deriving one from value and quantity.
- Published `compact-v5` with a required human-readable description on every new
  or updated Monitor rule while preserving legacy Monitor versions whose stored
  JSON predates the field. The Console now displays that meaning beside the
  machine rule code, condition, severity, observation, and state, and distinguishes
  the Monitor's original creation time from its latest run time. Aggregate public
  input schema size is 35,958 bytes and remains below the 36 KiB acceptance bound.
- Unified MCP and Console HTTP capability execution behind one compact-28 Registry.
  Both transports now share handlers, Pydantic validation, minimized schemas, and
  explicit effect/confirmation policies; the Console no longer reaches into
  FastMCP's private tool manager, and cache-capable instrument resolution is no
  longer misclassified as a user-confirmed write.
- Added persistent light/dark console themes with light as the first-run default
  and an accessible selector at the lower-left of the navigation rail.
- Fixed the Monitor event stream to render the actual event type, severity,
  observed value, threshold, message, and latest resolution. The console now
  offers explicit, noted, idempotent acknowledge/resolve actions without
  weakening `monitor_manage` confirmation.
- Added instrument-aware recent Monitor Run labels on both overview and Monitor
  pages. Historical runs prefer their immutable observation instrument and only
  reuse the current Monitor name when the persisted versions match.
- Linked each overview Monitor title to its exact definition card on the Monitor
  page, including async-load scrolling and a visible target highlight.
- Replaced the Console's Watchlist Groups panel with a full durable-instrument
  table showing symbol, name, asset type, source, sync time, and research support.
  For Moomoo, the page BFF now uses durable group metadata to select the system
  `All` group instead of silently counting only the configured default `Favorites`
  group; other sources retain the documented default-group fallback.
- Improved the compact-28 workbench with schema-derived required-field templates,
  inline PNG rendering for `technical_render_chart`, and copy-result feedback.
  Portfolio cards now format values and summarize position market value/P&L per
  native currency without claiming NAV or performing implicit FX conversion.
- Increased critical console text and control sizes, added consistent keyboard
  focus rings and mobile-accessible navigation labels, and repaired the narrow
  Monitor card hierarchy.
- Reworked Telegram Monitor alerts for mobile screens: the symbol and observed
  price now lead the message, while complete rules render as vertical cards rather
  than a fixed-width pseudo-table.
- Added durable run-linked A-share/US post-market Telegram summaries. Each evaluated
  market-close group now sends one consolidated heartbeat even when no rule changes;
  interval monitoring remains transition-only.

- Added a loopback-only, LLM-free operational console with five views over system
  health, the exact compact-28 capability catalog, Monitor definitions/runs/events,
  durable accounts/watchlist, sync/OAuth/notifications, and storage. The console
  can run all 28 public MCP tools plus gated Monitor/sync/notification/backup/cache
  operations without weakening schema, actor, confirmation, or idempotency rules.
- Added a dedicated Monitor builder to the local console: resolve A-share/US
  instruments, configure cadence and expiry, compose price/risk/all nine v2 fact
  categories, and create or version Monitor definitions without hand-writing MCP
  JSON. Client validation mirrors the domain's instrument, comparator, threshold,
  freshness, and portfolio-risk constraints.
- Added explicit operational maintenance: owner-only online SQLite backups,
  inventory/retention status, and dry-run-by-default pruning limited to expired
  Provider and Reddit caches. Durable research, transactions, Monitor history, and
  validation artifacts remain keep-forever.
- Planned real performance attribution as coverage ledger → actual P/L → returns →
  contribution → decision adherence. QMT, A-share account sync, and FX aggregation
  are explicitly deferred for at least two months; initial attribution is US/per-
  currency only and must disclose incomplete broker history.
- Closed the current Phase 3 implementation scope around cross-asset facts,
  company/industry facts, the QuantConnect Free manual bridge, and plan controls.
  Historical storage, local/automated backtests, experiment orchestration, and
  market-rule simulation are future options rather than Phase 3 exit gates; one
  user-operated bridge smoke remains an operational closeout item.
- Corrected the Phase 3D boundary: Risk v2 checks duplicate instruments across
  accounts but does not yet consume broker open orders or claim duplicate-order
  prevention. Phase 4 planning now requires a trusted approval channel and staged
  SIMULATE-to-REAL rollout before any execution work.
- Added the Phase 3C-0 QuantConnect Free manual bridge without increasing the
  28-tool MCP inventory: prepare hashed LEAN packages and import user-downloaded
  result JSON with explicit remote-code and dataset-version limitations.
- Hardened QuantConnect result imports so formal statistics cannot be overwritten
  by runtime display fields, exported run dates are checked against the manifest,
  and usable Benchmark curves receive deterministic comparison metrics.
- Slimmed the runtime package by removing unused provider/codec compatibility
  façades and moving delivery-evaluation validators into test support while
  retaining the declarative eval catalogs.
- Removed the deprecated Polymarket-only proxy setting; CME, DCE, Dukascopy,
  Polymarket, and Telegram now consistently use `PROVIDER_PROXY_URL`.
- Published `compact-v4`: optional discriminator mappings and schema defaults are
  omitted from `tools/list` while server-side Pydantic validation/defaults remain
  unchanged, reducing aggregate input schema from 40,544 to 35,882 bytes.
- Consolidated completed implementation notes into current phase specifications,
  release notes, and a bounded active known-issues document.
- Replaced the flat `ApplicationContainer` service locator with five explicit
  capability bundles, extracted infrastructure-only persistence and Provider graph
  builders, and reduced `bootstrap.py` to a bounded cross-layer connector.
- Split the 2,327-line ORM declaration monolith into ten capability modules under a
  single metadata registry without changing tables, constraints, or migrations.
- Monitor 工作台将同一次运行共享的价格与价格事实时间提升为 Monitor 级“最近运行价格”摘要；价格规则卡不再重复展示相同价格，异构非价格事实及不一致的价格观测仍保留逐规则明细。
- Monitor 列表整合为一个可检索面板，支持按标的代码或完整 instrument ID 即时筛选；每个 Monitor 同时展示首次创建、当前版本最近编辑和最近运行时间，桌面规则区固定支持一行六个规则块并在平板/手机自适应降列。
- 操作中心的 Schwab OAuth 卡片新增受控的手动重授权流程：一次点击只启动一个前台 OAuth 会话并打开新标签页，页面轮询等待本地回调；失败/中断后必须确认关闭旧标签才能创建新的 OAuth state，前端与日志均不接触 Token、secret 或原始授权 URL。
