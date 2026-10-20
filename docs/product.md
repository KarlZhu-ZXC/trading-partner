# Trading Partner current product specification

This document describes the product that exists now. It replaces the former
Phase 1–4 design ledgers, which mixed implementation history, acceptance notes,
and current contracts. Version-specific changes remain in `releases/`; exact MCP
schemas and operating procedures remain in their dedicated guides.

## Product loop

Trading Partner is a local-first, note-led investment companion. The owner writes
in Moomoo; those notes are sufficient for the core workflow. Discussion with Codex
or another host is optional. Research Subjects are background
archives, not a daily state-maintenance checklist.

```text
Moomoo note revision → attributed reading / changes + available saved portfolio context
                     → optionally discuss with Codex and explicitly save a Journal conclusion
                     → optionally review a formal Thesis / Trade Plan / Decision
```

The owner can use notes directly without a conversation, model interpretation or
saved discussion. Formal review can start from a note in Console without a prior
conversation; exact user confirmation remains required. A missing discussion is
not an incomplete task or an unreviewed-judgment determination.

Reading or retaining an ordinary discussion requires neither a Research Subject,
model interpretation nor a live Thesis. A recorded conversation is not a formal
Decision, Observation adoption, order or instruction to update every archive.
Formal judgment and execution changes retain exact scope and separate confirmation.

The default Console tab is Your Notes. It shows changed owner text independently
of saved discussion receipts and folds unchanged/quoted-only text by default;
Show All Notes on This Page retains those records. Older/Newer Notes browse the
saved archive beyond the initial page and the legacy MCP inbox's 500-note window.
Title/Instrument search is read-only and keeps search terms out of URLs and browser
storage. Discussions are an optional collapsed section. No automatic status writes
are made.
Risk/operational exceptions remain visible outside the collapsed background
workspace. Existing Today/Weekly and structured research workflows remain optional.

`view_get/notes` returns bounded synced metadata. `view_get/note` reads an exact
revision without an interpretation or Subject, with attributed, paginated text only
after explicit owner private-content authorization. Summary-only notes never supply
full text; secret and URL redaction happens before pagination. MCP text pages are
bounded separately from context metadata and retain accurate cursors after transport
compaction. Saved discussion previews
remain bounded, with exact Journal IDs for separately paginated reads. The built-in
Agent may read note metadata, but its raw-note-text path is blocked; external Codex
must follow the owner-directed host contract.

`external_state_sync/notes` is an explicit Moomoo-only sync with `analyze=false`:
it neither refreshes accounts nor calls a model. Reads never sync implicitly.
`research_memory_append/journal` can link `external_note_revision` without a Subject.
The source must be FULL and visible at write time; any optional Subject must match
the note Instrument. Existing actor, idempotency, indexing and audit gates apply.
No new table, migration, reminder scheduler or autonomous judgment is introduced.

## Note conversation context and default loading

The three daily Console entries are Notes, Portfolio and System. Advanced Workspace
keeps Journal, Research, Monitors, Capabilities, Charts, Agenda, Retro and Scorecards
reachable at their original URLs. Risk and unresolved operational actions remain visible.
Copilot defaults to closed for new preferences, is loaded on explicit opening, and
stays mounted after first use so collapsing does not discard a draft or active turn.
Existing explicit open preferences and `?agent=open` are respected. Question Research
waits for the rail listener before offering its exact prepared context; no auto-send.

The default Notes page reads durable note context, risk signals and unresolved
Agent/Broker actions. Background archive review scans and maintenance inventory
load only through their explicit views. Duplicate calendar, Monitor/run-history,
post-market and local-runtime home cards are replaced by links to their owning
Agenda, Monitors and System pages. Full Overview reads and explicit queue refresh
remain available; ordinary note discussion requires no queue or archive maintenance.

Note-list predecessor reads are batched for displayed rows. Missing and unreadable
previous content remain distinct; a failed comparison never labels a note as new.
Console browsing reads a lightweight latest-revision index and loads full revisions
and predecessors only for the displayed page. Search uses latest revision titles or
canonical Instrument IDs; it does not search private note bodies. The index remains
a linear metadata scan and pages may move when new notes are synced.
No additional cache or status maintenance is required.

The note reader offers a reference-only Codex request for the selected revision;
copying does not read private content or send a message. Console discussion history
loads only when Saved Discussions is expanded; note-text and account-page reads skip
that history, and history reads skip portfolio context. MCP read defaults are unchanged.
Saved discussion text is read only after an explicit selection, with paginated text tied to their original note
revision. Reloading does not reopen private discussion text or record another entry.
FULL notes also link directly to the exact revision in Journal, independent of the
latest inbox window and without requiring a Subject to read it. Journal's Notes scope
loads Subject choices, selected judgment and saved positions; switching to other tabs
loads the full Journal. Unloaded trade cycles are labeled, never reported as empty.
Missing or mismatched
revisions never fall back to the latest note. Record Independent Decision still
requires an exact matching active Subject, the owner's rationale and confirmation;
neither navigation nor reload invokes interpretation or adopts an Observation.

Metadata reads of `view_get/note` include `portfolio_context`: exact-Instrument
positions across saved accounts and symbol-related US option positions/transactions.
Saved Instrument Master relations take precedence over symbol hints. Recorded positive
multipliers retain their metadata version; they do not verify the complete deliverable.
Registered adjusted contracts may be included with unknown expiry/type/strike; explicit
conflicting underlying relations are excluded. A master lookup over its 500-row bound
withholds option associations rather than guessing across omitted metadata. Canonical
symbols are unverified fallback hints; Greeks are never invented. Other unmatched
option positions are disclosed. Broker cost/P&L mismatches remain warnings; no FX,
portfolio sizing or execution linkage is inferred.

Context pages contain at most six rows and use `context_cursor`. Position and ledger
failures degrade independently. A fingerprint invalidates continuation when the saved
context changes. Up to 1,000 matching transactions are readable; an overflow is explicit.
Historical ledger coverage remains UNVERIFIED, including empty results. Snapshot times,
age, native currencies and provider warnings stay attached. Account context reads have
no provider/model/write dependency. Private note pages remain separately authorized.
Transport compaction must not retain a continuation cursor after dropping financial rows;
when a page cannot fit, it reports an unavailable context and points to the full Console read.

Sync receipts with PARTIAL/authentication warnings appear as incomplete in Console;
configured credentials are not proof of current cloud access. Technical indicator/PNG
adapters defer numerical imports until first use. Installed extras and public operations
are preserved; this does not disable Monitor, broker, or scheduled-operation resources.

## Personal conclusions and thinking history

A FULL note offers **My Conclusion** directly in its reader. Explicit Save appends
an existing global Journal NOTE, authored and confirmed by the user, tagged
`personal_conclusion_v1` and linked to that exact immutable source revision. No
conversation, interpretation or Research Subject is required. It is a personal
record, not a Decision, Observation adoption, Thesis change or order. Source notes
remain authored in Moomoo; this UI does not edit the source.

Save recovery uses the existing Journal idempotency contract and a read-only
exact-key receipt. An unconfirmed request retains its original key; edits are locked
while its submitted draft is retained. Across reload, session storage retains only
the request ID and a SHA-256 equality fingerprint. Re-entered text must match that
fingerprint before retrying. A reload checks the receipt without saving again or
opening private text. Private title/body text is never persisted in browser storage.
A verified save offers a separate private read and an explicit new-conclusion action.

**Thinking History** loads on expansion, grouping Moomoo notes by their current
exact Instrument mapping; an unmapped note shows only its own versions. This does
not reconstruct historical Instrument mappings. Source revisions, personal
conclusions and saved discussions remain distinct, ordered by observed/recorded time
with exact source references. Metadata responses do not disclose private bodies. Historical
versions and record bodies require separate selection and retain source attribution,
redaction and pagination. Personal conclusions are not labeled as Codex discussions.

History is limited to the latest 1,000 source revisions and 1,000 linked Journal
records, in 20-entry pages. Bounds and independent Journal failures remain visible.
A changed history invalidates its continuation cursor instead of silently shifting
entries. An optional period-transaction read uses the displayed history page's exact
time window and existing durable account context rules, without current-position
reads or broker refresh. Fees, native currencies, option identity limits and source
coverage remain explicit. Period overlap is not evidence that a trade executed a
note or a plan; no relationship is automatically written.

## Core records

- An `Instrument` is an objective security or market identity.
- A Research Subject is the durable research file and stable scope. Compatibility
  APIs may still call it an `InvestmentCase` or use a `case_` identifier.
- A Thesis is a versioned, falsifiable judgment. One Subject may have one live
  PRIMARY Thesis plus related SUB, COMPETITOR, and BEAR threads.
- A Trade Plan records versioned execution intent and conditions for one Instrument.
- An Observation is immutable source material. External-note revisions retain exact
  source identity, chronology, attribution, and content mode.
- A Decision, including `NO_ACTION`, records reviewed intent. It may reference exact
  Thesis, Plan, scenario, portfolio snapshot, and Observation revisions.
- Broker orders, transactions, positions, and account snapshots remain distinct
  facts. Similar Instrument and time values do not prove an exact link.
- A Trade Cycle groups durable long-only activity deterministically. Performance,
  behavior summaries, Trade Retro, and Judgment Scorecard retain their inputs and
  limitations rather than producing an opaque score.

Research and policy mutations use version, actor, idempotency, and explicit review
gates. Candidate Propose → Confirm/Reject/Withdraw remains mandatory. A research
confirmation never authorizes an order.

## Product surfaces

The Console provides Research, Journal, Portfolio, Monitoring, Operations, Data
Quality, an optional Copilot rail, and a KLineChart-based interactive technical
workspace. The chart supports six candle/bar styles, common overlay and pane
indicators, zoom/crosshair interaction, SMC overlays, drawing tools, and PNG export.
Console reads complete local results and does not implicitly refresh a broker.

The public `mcp_vnext_shadow` v16 surface has nine entry tools over 24 business
capabilities. `capability_discover` reveals operations and exact schemas on demand;
read and write gateways preserve the original capability validation. Identity,
judgment confirmation, sync, broker-order, and chart operations keep dedicated
entry tools. The tool count may change through an explicit compatibility migration.

Optional discovery categories narrow the returned catalogue without hiding exact
operations or changing authorization. No-argument discovery still returns the full
catalogue. Console distinguishes public tools from business capabilities and shows
best-effort, owner-only usage aggregates from the shared registry. These aggregates
contain no business content and do not establish non-use when observations are absent.
See [capability usage and audit](guide/capability-usage-and-audit.md).

The optional built-in Copilot uses a smaller private tool set for capability search,
reads, proposals, action preparation, and web search. Writes become exact Pending
Actions and require the same-channel user to confirm an unexpired, single-use token.
Agent conversation memory is continuity context, not a current market or account
fact.

## Today and weekly review

The optional Changes and Risks tab contains Today Review: durable note reviews, Monitor transitions,
Agenda and due Decisions are grouped by exact Instrument. Multiple Research
Subjects keep separate links and judgments. Unmapped sources and broker/Agent
pending actions stay explicit rather than inheriting another Subject's decision.
Deferred and previously reviewed reasons are folded by default; new evidence
reopens attention. This is a presentation grouping, not resolution of source records.
Processing failures stay visible, including a failed latest note interpretation.
Operational failures retain visible action links outside the research groups.
Today Review supports local search and priority filters with visible counts,
source time and trigger summaries. Filters retain exact scope links and never hide
separate operational actions. Overview preserves loaded content on refresh failure;
the Today/Weekly selection survives browser reload.

Sources fail independently and bounded histories remain partial. Repeated Monitor
polls do not manufacture new transitions; recovered current state suppresses stale
error events. No page read reconciles, acknowledges, refreshes a Provider, runs a
model or sends a notification through these projections.

The Weekly Review tab uses the configured local Monday-to-Monday window, through
now. It combines exact confirmed Thesis revisions, including definition changes,
user Decisions (including NO_ACTION), current unresolved questions and due review
groups. Missing predecessors or definition comparisons remain unavailable. It does
not reconstruct historical question status, infer investment outcomes or compute a
win rate. The tab is a read-only review surface, not an automatic weekly message.

Overview and ReviewItem list reads, including scoped Journal reads, do not
materialize the Review Queue. Overview's explicit **Update Review Queue** action
collects existing durable sources and applies their deterministic projections.
It does not refresh providers, invoke models or confirm judgment. Live projections,
partial-source coverage and the latest materialized item observation time remain
visible. Pending research counts use one complete aggregate read rather than
per-Subject full-state queries. See [architecture and measurement](guide/architecture-and-performance.md).

## Quick Review

Journal Notes also offers **Record Independent Decision** for FULL sources with an
exact matching active Research Subject. It opens the existing confirmation dialog
with an empty user rationale even when interpretation is unavailable. No model is
called, and the Observation review retains its own status. New Decision source
references must be FULL and already observable at the Decision time.

Today Review links directly to a Quick Review card inside the selected Research Subject.
The card combines the pinned user Decision/Thesis/Plan, durable position snapshots,
source coverage and changes. It reads up to 1,000 changes in one projection; larger
histories disclose the limit and cannot use the maintain shortcut. Nothing is
refreshed or sent to a model merely by opening the card.

Latest Thinking reads the latest synced Moomoo revision for each matching note,
selects the latest dated USER blocks, and may show an existing successful
same-revision summary only when its source ordinals bind to those USER blocks.
Others' viewpoints stay separate. Pending/failed latest interpretations never fall
back to an older revision. SUMMARY_ONLY sources expose metadata only. Up to five
recent notes are shown, with missing dates and truncation disclosed. Thought dates
are distinct from sync/edit timestamps; comparisons show changes in the previous
synced USER section and do not claim that the formal Thesis changed. Date headings
use the configured local timezone; inferred years remain explicitly labelled.

Maintain records a new NO_ACTION with the exact prior references; defer records
RESEARCH_MORE with the user's gap and a future review time. One explicit submit is
authorization for that record. Expiring server context is checked again against
current evidence/formal versions; stale context requires refresh without discarding
the user's draft. Existing Decision idempotency supports recovery without duplicate
writes; reload does not resubmit. Source-specific Observation adoption remains a
separate explicit action, never an implied consequence of a general review.

Each prepared card includes a reference-only material manifest with its baseline,
formal versions, source revisions, consulted account snapshots, preparation time
and coverage. Preparation does not mean the user read or adopted every source.
The explicit Decision and its linked material Journal NOTE commit in one research
transaction; a failed material write rolls both back. The reference list is bounded
at 500, with total count and partial coverage disclosed; a partial list cannot use
maintain but can accompany an explicit follow-up.

The completion receipt identifies the saved Decision, retained evidence gaps and
pending-note count at review time. It preserves separate source statuses and links
to their current review entry. Recovery reads the saved material record after a
restart; legacy/missing material receipts remain unavailable. A recovered receipt
does not display a freshly prepared card as if it were the original context.

Adjustments reuse the existing Thesis and Plan editors in the same Research page,
with a return shortcut and preserved Quick Review draft. Latest thinking first produces an editable before/after preview bound to the
current PRIMARY revision. It may prefill a Thesis only after the user accepts the
preview, without overwriting an open editor.
Refresh Notes uses the existing explicit capture/interpret/review workflow. When it
finishes, an active idle card refreshes while keeping the user's text; hidden,
submitting or recorded cards stay pinned. Existing proposal/confirmation and order
authorization boundaries remain unchanged.

Quick Review action, user rationale and follow-up date persist in bounded,
Subject-scoped session storage for 24 hours. An unresolved submission additionally
retains its original request/key; on reload only the durable receipt is read.
Recorded receipts clear recovery state. Retrying or abandoning an unrecorded attempt
requires an explicit action, and edits cannot silently create a different attempt.
No automatic POST, copied note payload or formal editor state is persisted by this
recovery mechanism. Storage failure remains visible and falls back to the current page.

## Copilot research process

Console calls the built-in assistant **Copilot**; existing Agent API paths, stored
identities and internal symbols remain compatible. Chat keeps the existing behavior.
Research and Counter-review are explicitly selected modes on that same runtime.

Research exposes fixed evidence questions, deterministic step progress and a bounded
read-only tool surface. Defaults are 180 seconds, eight top-level model calls and
24 tool calls; the server validates narrower or larger allowed choices. The ordinary
six-tool-round limit also applies. Fallback and optional critique calls count
against the model budget; Research uses its field checker directly without an
extra legacy prose-repair round. Research uses explicit web-search tools, not hidden native
search or an uncounted conversation-summary call. Provider-internal retries and actual
billing remain unknown; these controls are not a guaranteed token or dollar ceiling.

Model text is held until final evidence checking. Canonical FACT/CITATION blocks bind
to an exact current-turn request and field path, including available Instrument,
account, unit, time and basis context. Unverifiable numerical/factual blocks become
GAP entries; qualitative synthesis stays INFERENCE. Old conversation memory and web
text cannot establish a canonical price or position. The bounded field catalog may
omit unsupported/oversized evidence; omission is not proof of absence. Verified refs
survive receipt compaction for later source inspection. The host renders selected
facts with their context; the model need not copy the catalog. Explicit null values
can support missing-data explanations. Qualitative explanations may cite multiple
fields but remain unverified interpretation, not fact verification. Invalid optional
replacement answers preserve the primary.

Explanations may embed `@evidence(ref)` for a selected current-turn field. The host
inserts that field's full identity, time, currency and basis context instead of
requiring the model to copy a number or date. Missing, unselected or malformed
references, additional unbound numbers and execution claims remain blocked;
expanded text retains the answer size limits. The explanation remains INFERENCE
(or GAP/NEXT_STEP), never a verified fact. Console reports exact field matches
separately from interpretation and explicitly discloses that meaning is not checked.
Omitted reference arrays may be reconstructed from exact inline paths. Bounded
model root notes become checked INFERENCE blocks without opening the public schema;
coherent parenthesized lists are not treated as amounts. Evidence-invalid optional
counter-reviews preserve the primary answer and disclose the rejected critique.

Research answers present supported canonical fields with readable names and values,
keeping identity and material context visible while full source/path text can be
expanded. New messages use host-generated structured fields and ordered context;
Console checks that they reconstruct the exact stored answer before rendering them.
The optional projection is dropped first when the receipt size bound is reached.
Historical or compacted messages retain their text formatting fallback. Stored/copied
answers keep the exact original text, and an interpretation never becomes a verified
fact through formatting. Missing-value definitions can use either zero notation;
this does not permit unsupported financial amounts or execution assertions.

Counter-review permits one additional tool-free critique, only within the remaining
budget. Failure preserves the primary answer and reports a gap. Completed read steps
and receipts survive budget stops; reconnect/reload only restores records and never
resubmits a prompt or confirms an action. A user-requested failed-turn retry restores
the original research-only mode and budgets. Missing model progress or usage after
failure is explicitly unavailable rather than zero.

The UI shows reported input/output tokens, usage completeness, elapsed time, call
counts, evidence-check counts and stop reasons. Price information for inference
billing is not configured, so cost stays unknown. Conversation usage profiles group
recorded samples by model/effort/mode/budget and report P50/P95 elapsed times; different
prompts make these descriptive samples, not model rankings or quality comparisons.
The deterministic evaluation command includes 40 additional synthetic evidence cases
alongside the existing runtime catalog. These gates validate implementation behavior,
not live model research quality, investment returns, or the correctness of every
qualitative inference.

An explicit source-checkout acceptance script can exercise the configured real
model against four closed synthetic scenarios in an in-memory Research runtime.
It measures field/answer retention, budgets and reported usage without private
data, market access or writes. Automatic passing requires subsequent human semantic
review and does not establish general model quality. See the
[operator guide](operations/local-console-and-maintenance.md#copilot-真实模型验收显式执行).

## Charts, valuation assumptions and calibration

Research and Monitor can open an exact Instrument in `/charts`. Chart retrieval is
explicit; stale responses are ignored. Locked SMC structures expose occurrence and
confirmation times, snapshot status, algorithm version and source-bar range. A
historical cutoff filters confirmed structures/bars without reconstructing historical
invalidation status. Session-only drawings reset with Instrument, period, basis and
version. Matching ACTIVE Plan conditions remain separate. An explicit reviewed
session handoff may prefill a DRAFT Thesis or Plan notes, never execution thresholds.

Valuation is a Console-only, deterministic annual diluted-EPS/P/E scenario method for
positive-earnings US ordinary operating companies. SEC filing identity/time and USD
per-share basis are required; unsupported companies or missing data are gaps. User
normalization and P/E assumptions remain separate from facts and produce a nine-cell
sensitivity grid. No enterprise/aggregate equity value or current split adjustment is
inferred. Explicitly reviewed saves append immutable Journal versions with existing
idempotency/audit gates; they never confirm judgment. Source tokens expire after one
hour or API restart; calculation and save never refresh Providers.

Judgment Calibration pins a user Decision and its exact Thesis/Plan references,
separately showing factual outcomes, matched Plan observations, adherence records and
attribution limitations. It reuses Changes Since Review, Scorecard, Agenda and Retro;
it has no combined score or new return engine. NO_ACTION remains reviewable, future
review dates are disclosed, and absent attribution remains unknown. These reads do
not generate evaluations or write research state.

See [the workflow guide](guide/research-chart-valuation.md) for supported inputs and
continuous navigation/recovery rules.

## Review evidence and follow-through

New Scorecards disclose two exact-revision cohorts at the run cutoff: latest
EvidenceAssessments per Evidence, and active reviewed Evidence Impact links. Link
counts, distinct sources, stance counts, overlap with assessed Evidence and missing
source metadata remain separate. They are never summed into an evidence count or
quality score. Bounded/inaccessible Impact history is unknown, not zero. Calibration
shows the same immutable facts; old runs without these fields remain unknown.

Journal period reviews expose an explicit durable-only coverage read. It resolves
the original Run's trade references, compares only an eligible pre-period snapshot,
and reports unique active Plan matches, compatible pre-period Decisions and absent
or ambiguous matches. Current activity links and Plan versions created during the
period are separate cohorts; later records never backfill prior discipline. Missing
or colliding references and bounded reads stay unavailable. This projection is
read-time context, not a rewrite of the immutable Run or complete broker history.

A finding review may explicitly defer, record a reason for no follow-up, or link one
confirmed Question at an exact version. Scope/version checks, review version,
authorization note and idempotency remain mandatory. A new Question is prepared as
an editable Research draft, then proposed and separately confirmed before linking.
The review cannot create/answer/close a Question. Later review exposes the linked
version alongside current Question status and its evidence entry; completion is not
inferred from status or a later annotation. Retro drafts retain editable fields and pending request
metadata in bounded tab-local session storage across return/reload. Version
conflicts show saved-versus-draft differences and require explicit local rebasing
before a separate save. Reload recovery reads receipts only; it never replays a
write. Storage failure is disclosed and stale cached terms are removed. Weekly action items reuse the existing
human-selected list, with 1–3 concrete process improvements suggested.
The same explicit Retro read now prioritizes follow-up next steps, keeping coverage
and denominator details collapsed. Its cohort is the findings explicitly linked to
Questions in the bounded review history. Due counts use the latest reviewed exact
Question version. Missing histories, changed versions or unrecoverable old deadlines
keep totals unknown. An answered Question counts as a traceable reviewed resolution
only when its sources remain visible at answer time and a subsequent human Finding
review explicitly marks that exact version RESOLVED. This is traceability, not a
semantic-quality or investment-outcome claim. Later similar Findings are shown as
review leads; their absence is never proof of improvement. NO_ACTION and actual
trades retain separate cohorts and are not added to this denominator.


## Research and observations

Research Subjects support company, theme, macro, catalyst, and portfolio-concern
work. A Subject may exist without a primary Instrument or confirmed Thesis. Live
Theses, active Plans, and Monitors enforce explicit lifecycle dependencies; the
system does not auto-activate or cascade related records to hide a conflict.

The Research Plan dependency panel compares the current confirmed version with its
predecessor, including condition content, and shows the bound Thesis revision at
Plan creation versus the latest revision. Confirmation-time ordering is disclosed;
it does not assert that a historical review occurred. Old Monitor bindings remain
unchanged. Keeping/defer choices are view-local; durable review and reminders use
existing explicit workflows. Monitor read failures leave the confirmed Plan intact.

Plan coverage and new Scorecards share one exact-version calculation: active,
unexpired rules must match the condition semantics; usable observations must belong
to that exact Monitor version, have no error and remain within the condition's fact
age at read time. MANUAL, UNCONFIGURED, CONFIGURED, OBSERVED and UNAVAILABLE remain
separate. OBSERVED includes triggered conditions and is not a pass or authorization.
Scorecard algorithm `judgment-scorecard-s1-v3` retains historical S0/S1-v1/v2 readability.

Execution readiness runs only on explicit selection, using existing durable-only
Sizing/Risk. A different returned Plan version is rejected. Missing facts remain
blockers. Portfolio refresh, order-preview drafting and Journal reconciliation have
separate navigation; none submits an order. UNKNOWN outcomes must be reconciled
without retry; partial fills and external/unattributed activities retain their
existing execution and attribution boundaries.


External observations share one provider-neutral revision contract. The Local
Observation Bridge accepts closed full-text JSON. Moomoo note capture is read-only
and never automates the desktop UI. Summary-only text is change-detection evidence;
it cannot reach model interpretation or Decision adoption.

Optional model interpretation produces drafts only. Attribution and date ordering
are computed deterministically first. A model cannot confirm a Thesis, Decision,
Plan, Monitor, position, or order. Contributor models require the owner's explicit
training opt-in.
An explicitly enabled official OpenCode CLI fallback can recover first-pass note
extraction after transient or exhausted structured-output failures. It uses isolated,
temporary sessions with automatically rejected tool permissions, preserves the same
schema/attribution gates, and records the actual model source. Configuration and
limits are in the [local operations guide](operations/local-console-and-maintenance.md).

## Evidence impact links and research questions

OPEN Research Questions can prepare a version-bound Copilot prompt with an optional
exact assumption, evidence cutoff and bounded durable source choices. Preparation
is read-only; explicit Send starts the existing budgeted Research runtime. An
existing composer draft is preserved. The question version and cutoff remain in
durable receipts, including budget stops and reload. Changed context requires a
new preparation, and unknown/later field times cannot be verified against the cutoff.

Completion explains whether the output needs review, further evidence, a narrower
run or failure recovery. It can return to the question or open prepared durable
sources in an editable evidence-link draft. Existing active pairs use revision
drafts; every proposal and confirmation retains its own gates. No temporary Web or
Provider field becomes Evidence, and Research never answers/closes the question.

Research → Evidence proposes a user interpretation linking one exact durable
Evidence/FULL Observation/evaluated Monitor observation to one current Thesis
revision and active assumption. Source scope/time and target/version are checked
again at Candidate confirmation. Supports/Contradicts/Uncertain describes the
reviewed association, not a factual verification or a change to Thesis conviction.
Confirmed links append immutable versions; revise/retract retain history, and a
missing source never removes an existing link. Duplicate active source/assumption
pairs and stale versions are rejected.

The Evidence Comparison workspace places Supports, Contradicts and Uncertain links
beside their exact targets and original source metadata. Local, versioned question
templates prefill editable drafts only and require explicit replacement of existing
text. Desktop lanes stack for mobile review; confirmation gates remain unchanged.

Decision Replay uses an exact Decision's `decided_at` cutoff, separately discloses
its recorded time and excludes later facts and assumptions. Only pinned revisions
and historically scoped sources participate. Old question text/creation can be
shown, but unavailable historical mutable state is never reconstructed from today's
answers. This is a passive Console read, not a historical trading simulator.

Research → Thesis extends existing OpenQuestions with evidence requirements, due
at, progress notes, cited sources and explicit links to later same-Subject Decisions.
Tracked answers require evidence and a user criteria acknowledgment. Effective
changes retain the Candidate lifecycle and optimistic versions. Nothing creates a
Decision automatically. Explicit Review Queue refresh materializes overdue OPEN
questions and preserves incomplete-source closure rules. Reads remain passive.

Changes Since Review attaches exact reviewed source links separately from automatic
scope relations. A link matches the reviewed baseline only when both its revision
and assumption were present by the Decision's recorded time. The detail guide is
[evidence links and research questions](guide/evidence-links-and-research-questions.md).

## Changes since a reviewed Decision

Research shows a read-only Changes Since Review workspace beside the exact user
Decision baseline and its linked immutable Thesis revisions, assumptions,
invalidation descriptions, and Trade Plan version. The default baseline is the
latest user Decision by recorded time, not a model draft or a backdated decision time.
A pinned `none` baseline explicitly preserves the unreviewed state.

Scoped Observation revisions, immutable Monitor observation changes/transitions,
and Agenda versions are compared with their predecessors. Old and new fact times,
source/version identities, and available provenance are disclosed. Full-note previews
are bounded USER excerpts; summary-only revisions expose metadata only. A matching
Instrument never proves a particular Thesis assumption changed. Only a validated
Monitor condition on an exact Plan/Thesis has an exact Plan relation.

Each source reports its own coverage; missing/legacy Monitor history is partial and
failed reads do not imply no changes or resolve reviews. Projection reads neither
contact Providers/models nor create notifications. Stable source IDs deduplicate
replays. Pagination follows the complete scoped projection, and a selected change
is relocated by identity if newer rows shift its page.

The baseline, selected change and page survive reload and Journal navigation using
only opaque identifiers and a numeric offset in the URL. Journal restores an exact
historical Observation independently of the latest inbox window. Opening or returning
from review never confirms anything; existing Decision and Observation-review gates
remain authoritative. Refresh keeps the baseline; Use Latest Review explicitly
advances it after a completed review. User drafts are not auto-submitted on reload.

## Market and company facts

- A-share: quotes, market structure, capital flow, limit-up and sentiment context,
  ETF options, statements, research reports, operating disclosures, and optional
  hog-cycle data.
- US: quotes and bars, market context, SEC-first statements and filings, insiders,
  company events and updates, news, macro, source-separated sentiment, and prediction
  market context.
- Korea: Instrument resolution plus Yahoo quote/bars, shared technical analysis,
  manual Watchlist, monitoring, and XKRX post-market dispatch. DART, KR brokerage,
  fundamentals, news, sentiment, breadth, and Position Sizing are unavailable.
  Further Korean-market expansion is excluded from future plans.
- Cross-asset: selected Yahoo continuous metal futures, formal CME metals, DCE
  live-hog EOD facts, Dukascopy precious-metal spot feeds and rolling copper/light-oil
  CFDs. Every proxy retains its venue, units, adjustment, roll, and basis warnings.

Provider output keeps source, observation time, freshness, basis, typed warnings,
and typed failures. Missing data is never silently estimated. Ordinary durable reads
do not contact a Provider; explicit sync operations own upstream refresh.

## Technical analysis

`technical_get_snapshot` and `technical_render_chart` support valid equity, ETF,
index, futures, and OTC identities where daily bars exist. The engine derives `1d`
and ISO-week `1w` views from one sourced daily series.

The current `tp_technical_v3` output contains:

- EMA 10/20, SMA 50/200, RSI 14, MACD, ATR 14, Bollinger Bands;
- ADX/+DI/-DI, Stochastic, ROC 20, MFI, VWMA 20, OBV, and relative volume;
- deterministic trend, momentum, volatility, and volume states;
- nearby support/resistance from five-bar swing points clustered within 0.75 ATR;
- recent engulfing, hammer, shooting-star, and doji recognition.
- `tp_smc_v1` internal and swing HH/HL/LH/LL structure, close-confirmed BOS/CHoCH,
  Order Blocks, Fair Value Gaps, equal-high/equal-low liquidity, and
  premium/equilibrium/discount zones.

MCP/Agent chart artifacts render auditable PNG candlesticks, EMA20, SMA50, structure
levels, volume, and RSI14. Console can request source bars with the same validated
technical read and render an interactive KLineChart workspace. Server-derived SMC
overlays are locked; user drawings remain editable for the current chart session.
All outputs disclose source, bar time, price-adjustment basis, algorithm version,
and `historically_validated=false`.

`tp_smc_v1` is an independent deterministic implementation based on the feature
vocabulary published for [LuxAlgo Smart Money Concepts on
TradingView](https://www.tradingview.com/script/CnB3fSph-Smart-Money-Concepts-SMC-LuxAlgo/).
The LuxAlgo-compatible profile uses its published defaults: internal length 5,
swing length 50, EQH/EQL confirmation length 3 with a 0.1 × ATR(200) threshold,
ATR(200) volatile-bar filtering for Order Blocks, and High/Low mitigation. A pivot
is not available until its right-hand confirmation bars close; BOS/CHoCH requires a
later close crossover/crossunder. Every swing and zone exposes occurrence and
confirmation time. FVGs use the published three-bar body-direction and cumulative
auto-threshold conditions.

When a timeframe lacks enough history for ATR(200), structure and FVG calculation
continue, while the output sets `atr_200_ready=false` and reports that volatile-bar
Order Block filtering and EQH/EQL are incomplete.

Ten-symbol acceptance against an independently transcribed reference evaluator over
three years of identical adjusted Yahoo daily OHLC passed all 50 category comparisons for structure,
Order Blocks, FVGs, EQH/EQL, and value zones. This establishes core calculation
compatibility, not TradingView pixel parity or cross-vendor bar equality. Visibility
toggles, styling, alerts, Strong/Weak High/Low, and multi-timeframe previous levels
are not modeled. The engine does not claim institutional order knowledge or a trade
signal.

## Portfolio, risk, monitoring, and review

Account sync supports configured Schwab, Moomoo OpenD, or manual CSV sources. Durable
portfolio reads preserve native currency, snapshot time, coverage, open orders, and
quality warnings. Exact fees remain required for Net P/L; unavailable fees do not
discard transactions or become estimates.

Risk and Position Sizing are deterministic. They evaluate the exact portfolio,
policy, Plan, price, and Instrument inputs they receive. They do not reserve capital,
change a position, or create an order.

Monitoring evaluates versioned price, technical, portfolio, and fact rules. Missing
inputs become `NOT_EVALUATED`. Optional model commentary cannot change deterministic
rule results. Due, post-market, and notification jobs retain idempotency, leases, and
durable receipts.

Journal connects reviewed Decisions to durable broker activity without copying the
underlying facts. It exposes Trade Cycles, Daily Equity, TWR, MWR/XIRR, drawdown,
behavior cohorts, review items, and exact Timeline links only when the required data
coverage exists.

## Orders and unattended work

Schwab supports narrowly confirmation-gated US stock/ETF preview, submit, status,
and cancel operations. Submit or cancel requires an exact unexpired preview and
explicit authorization for that action in the current conversation. An unknown
submit outcome is never retried automatically.

The installed SGOV BUY LIMIT/DAY/NORMAL cash-sweep scheduler is the sole unattended
order exception. Its persistent authorization, reserve controls, price guards,
operational lock, and receipts grant no authority for another symbol, sell, replace,
cancel, overnight session, or general Agent/MCP execution.

## Runtime and data boundary

Installed runtimes use one owner-controlled `RUNTIME_ROOT`. Mutable databases,
secrets, locks, attachments, observations, backups, and operational artifacts stay
below it. Private notes, credentials, account values, and real reconciliation inputs
never enter Git, package data, examples, tests, URLs, telemetry, or error messages.

The Console data API remains loopback-only. Optional trusted-LAN mode exposes only
the authenticated web process and is not public hosting. SQLite production uses WAL,
bounded busy handling, and explicit maintenance. Ordinary startup verifies the exact
migration head but never migrates as a side effect of a read.

## Deliberate exclusions

- local or automated backtesting and parameter optimization;
- autonomous Thesis, Plan, Candidate, position, or order decisions;
- general unattended execution beyond the installed SGOV exception;
- order replacement, options/complex orders, short selling, or overnight Schwab
  orders;
- fabricated cross-currency returns, missing fees, corporate-action lots, or cost
  basis;
- arbitrary scraping or relabelling a fallback/proxy as the requested market fact.

## Detailed references

- [MCP capability and trust boundary](guide/mcp-capability-boundary.md)
- [Console design system](guide/console-design-system.md)
- [Local Console and maintenance](operations/local-console-and-maintenance.md)
- [Known operational constraints](operations/known-issues.md)
- [Product roadmap](roadmap/global-roadmap-cn-us.md)
- [Current unreleased changes](releases/unreleased.md)
