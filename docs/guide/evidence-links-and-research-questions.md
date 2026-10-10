# Reviewed evidence links and research questions

These features extend the existing Research Subject, Candidate and Review Queue
workflows. They do not infer semantic truth, change Thesis conviction or execute
orders. The first release is user-selected, explicitly reviewed association.

## Link evidence to an exact assumption

In **Research → Evidence**, choose an existing durable source and a current Thesis
revision/assumption, select Supports, Contradicts or Uncertain, and explain why.
**Propose Candidate** only creates a proposal. Review the pinned source, target,
stance and rationale in **Pending Candidates**, then explicitly confirm, reject or
withdraw it using the existing review controls.

Supported sources are a Subject-linked Evidence record, a FULL Observation revision
matching the Subject's Instrument, or an evaluated Monitor observation identified
by exact run, monitor and rule. Monitor scope comes from that historical definition
version. Source timestamps and visibility are rechecked at proposal and confirmation.
Summary-only notes, other Subjects' artifacts, future facts and NOT_EVALUATED Monitor
observations cannot be used as supporting evidence. Note metadata does not imply
that all speakers in a note are the owner; attribution still requires review.

The target must remain the current exact Thesis revision and an active assumption.
A changed target requires a new review rather than silent retargeting. Confirmation
appends an immutable impact version; it does not revise a Thesis, EvidenceAssessment,
Decision, position or order. One active source/assumption pair is allowed. Revise and
Retract proposals pin the current impact version. Retraction records its reason,
retains the prior version and can proceed even if the historical source has become
unavailable. Existing historical links remain visible and never migrate to a newer
Thesis revision automatically. Review History shows bounded earlier versions.

Changes Since Review shows these user-reviewed links alongside exact matching
Observation revisions or Monitor run/rule observations. This is separate from the
existing automatic scope/Plan relation. Baseline matching requires both the pinned
Thesis revision and the exact assumption already confirmed by the Decision's
recorded time; later assumptions on the same revision are not earlier knowledge.

## Close a research question

For an OPEN question, **Research with Copilot** lets you select an optional exact
assumption and then **Prepare in Copilot**. Preparation reads local records only.
It pins the question version, requirements, evidence cutoff and up to eight durable
source choices for twenty minutes. An existing composer draft is preserved; clear
or finish it before accepting another question. The prepared prompt stays read-only
so editing cannot silently change its binding. Review the model and budget, then
press **Send** to run Research or Counter-review.

Research completion distinguishes a reviewable answer, missing evidence, budget
stop, failure and cancellation. None answers or closes the saved question. The
original version and cutoff survive conversation reload and explicit failed-turn
retry. A changed question or selected assumption requires a new preparation. Facts
without a usable time or later than the cutoff cannot pass the question's field
evidence check; ordinary Research retains its existing per-query time rules.

**Return to Research Question** opens the original scope. **Review Prepared Sources**
opens eligible durable records retained at preparation, not an automatically inferred
list of model endorsements. Pick **Use Source in Link Draft**, inspect the exact
target, stance and empty rationale, and propose through the normal Candidate flow.
An existing active source/assumption pair opens a revision draft rather than a
duplicate create. Missing sources and changed context remain visible and cannot
silently replace the original references. Provider/Web field citations never become
new Evidence records through this handoff. Question answers remain separately reviewed.

A Quick Review follow-up offers **Continue with Research Questions**, where you can
choose an existing question or explicitly draft a new one. It does not create a
second task automatically from similar text.

The Evidence Comparison view groups confirmed ACTIVE associations into Supports,
Contradicts and Uncertain. Select an exact revision/assumption to narrow the view;
the all-targets view retains each exact target identity. These lanes show reviewed
interpretations, not votes or an automatically calculated conviction. Retracted
associations remain in review history. Original source metadata, times, warnings
and stored safe web links are available with each association; unavailable source
records and partial coverage stay visible.

In **Research → Thesis → Research Questions**:

1. Add a question with optional evidence requirements and deadline.
2. Update its research plan, progress note and collected source references.
3. Propose an answer. Tracked questions with requirements need cited sources and an
   explicit acknowledgment that the criteria are met. This acknowledgment is a user
   judgment, not automated semantic verification.
4. Confirm the exact Candidate. The existing OpenQuestion becomes ANSWERED and
   retains its requirements, evidence and versioned update trail.
5. Optionally link later Decisions from the same Subject. Both the decision time and
   recorded time must not precede the answer; linking does not create or rewrite a
   Decision and does not establish causal attribution automatically.

An unanswered OPEN question can instead be closed with a reason. Existing Decision
links are retained when additional links are added. Closed/answered questions remain
readable. All effective changes use Candidate confirmation, actor, expected-version
and idempotency gates. Legacy questions start at version 1 with empty optional
metadata; historical plain answers remain readable. After the first revision,
mutations require an explicit current question version.

Question History retains confirmed updates with candidate identity and reviewer.
The original legacy create Candidate cannot always be mapped back to a question,
so the view does not reconstruct that association from matching text or timestamps.
Proposals do not auto-confirm. Failed writes preserve drafts; unchanged retries keep
the same attempt key. Page/source refresh does not post a proposal or clear an editor.
Private draft text is not copied to URLs or browser storage.

New question drafts can start from a versioned Earnings Review, Competitive Position
or Thesis Invalidation template. Applying a template fills editable question text
and evidence requirements only. Replacing an already filled draft requires an
explicit replacement action. Templates never select evidence, satisfy answer
criteria, create a Candidate, confirm judgment or populate execution quantities.
The comparison lanes and question controls stack for mobile review.

## Replay a Decision's historical context

In Research → Evidence → Decision Replay, choose one reviewed Decision and select
**Load Replay**. The read-only response uses `decided_at` as its strict cutoff and
discloses `recorded_at` separately. A backdated Decision can therefore reference
records that were stored later; those records are excluded, not silently admitted
using the later recorded time. The saved Decision rationale is record context.

Only exact pinned Thesis revisions and their then-existing assumptions are shown.
A later retirement or impact retraction does not rewrite the earlier state.
Sources must have both their fact and stored times within the cutoff and an exact
scope. Historical Observations require an exact Decision or then-confirmed impact
reference; today's Instrument assignment is not proof of a historical association.
Missing sources, excluded references and bounded/truncated histories are disclosed.

Existing question text and creation time can establish which questions existed.
Legacy mutable questions do not contain a complete immutable state history, so
their old status, answer, progress and requirements are explicitly unavailable.
The replay does not reconstruct them from today's values or matching text.
No migration, provider refresh, model call or research write is triggered.

The Console read endpoint is
`GET /api/research/{subject_id}/decisions/{decision_id}/replay`. A different
Subject/Decision scope fails closed. MCP operation counts and review gates are
unchanged.

## Due reminders and read boundaries

Overdue OPEN questions appear in the existing read-only workflow projection. The
explicit **Update Review Queue** command materializes `OPEN_QUESTION_DUE` reminders.
An answered/closed/rescheduled question can resolve its prior reminder only after a
successful sufficiently complete refresh. Failed or truncated source scans do not
silently close unseen reminders. The reminder opens the Subject's question panel.

`GET /api/research/{subject_id}/evidence-links` returns scoped source choices, pinned
links/history, current assumption targets and Decision choices with coverage flags.
It never refreshes a provider, calls a model or writes state. Source choices and
history are bounded; missing sources do not delete confirmed links. MCP
`research_get/state` also returns latest bounded `evidence_impacts` plus the expanded
question metadata. The nine MCP entries and 106 operations remain unchanged.

Writes reuse `research_judgment_propose/research_state` and
`research_judgment_confirm/candidate`. The v14 payload adds `kind="evidence_impact"`
with `create|revise|retract`, and OpenQuestion adds `update_research|link_decision`
while retaining its existing actions. No general evidence-ingestion write or new
broker authority is exposed. Built-in Agent allowlists remain narrower than the
full Console/MCP registry.

## Runtime upgrade

Application migration head is `0073_research_evidence_links`. The migration preserves
existing question/candidate/review rows, adds safe defaults, stores append-only
impact versions and extends closed Candidate/ReviewItem vocabulary. Ordinary reads
never migrate a database. Enabling this on an existing runtime requires its explicit
operational upgrade, following the backup and maintenance procedure.

Downgrade is refused before any DDL when new feature data/candidates exist; use a
verified backup for rollback. Development verification uses disposable databases,
not the installed owner's notes or accounts.


## Scorecard evidence cohorts and Retro follow-up

Scorecard → Evidence Balance and Research → Judgment Calibration show assessment
and reviewed Impact facts separately. The assessment denominator is distinct
Evidence with a latest assessment for the locked Thesis revision. Impact stance
counts use active links; its source count deduplicates exact source references,
including Monitor run/rule identity. Overlap with assessment Evidence is disclosed,
not added. The cutoff is the immutable Scorecard run time. Missing source metadata,
bounded history and legacy runs without Impact facts remain explicit limitations.

In Journal → Reviews, choose Inspect Coverage and Reload Follow-ups. Counts cover
original Run references, not all broker history. Pre-period Plan matches and
compatible Decisions use only the stored pre-period snapshot. New Plan versions
inside the week and current activity links have different time bases; neither
repairs a missing old snapshot. Unknown denominators never produce a percentage.

Open Review Findings and choose a disposition for the specific finding. Follow-up
is an independent choice: a confirmed Question, no follow-up with a rationale, or
defer. Append Review Revision is the explicit save; selecting a dropdown or opening
a link does not write. A stale review/question version rejects the append. Saved
choices preserve the original Question version and expose current status only as
new context. Inspect Follow-up Evidence opens Research for human verification.

For a new investigation, select an eligible Research Subject and choose Prepare
Question from Finding. Research first displays the immutable source; Use Retro
Finding in Question Draft prefills editable text and provenance only. Set evidence
requirements and, if wanted, a deadline. Propose and explicitly confirm the
Question through Pending Candidates. Return to the Retro review, reload choices
and explicitly link that confirmed Question. The steps are independently durable;
creating the Question does not imply that the Retro link was saved.

Keep the weekly Action Items focused on 1–3 selected process improvements. On the
next review, inspect the linked question's evidence and recorded timing. A later
answer, a retrospective activity link or a profitable trade does not establish that
the previous process was followed. NO_ACTION remains a separate reviewed Decision.


### Returning to selected follow-ups

Journal → Reviews → Inspect Coverage and Reload Follow-ups now shows What Needs
Attention first. Open Question and Evidence takes you to the existing research
workflow. Changed versions, missing sources, due questions and answers awaiting
review have different next-step text. Detailed coverage counts and denominator
rules stay collapsed until needed; no additional task list or confirmation step is
introduced.

An ANSWERED status alone is not completion. The traceable-review count requires
source references visible at the answer time plus a later explicit RESOLVED finding
review linked to the exact Question version. Source existence cannot establish
semantic correctness. A later similar finding prompts comparison; a missing later
finding never means the issue was fixed. Missing review history or a changed
Question makes totals partial. Historical deadlines that cannot be reconstructed
remain unknown, even when a reason for no further work was explicitly recorded.


### When a review save response is lost

Console retains the submitted review unchanged in tab-local session storage and reads the
receipt by its original request key and Run. A matching saved revision restores the
success state without a second POST. If the receipt is not yet found or cannot be
read, Check Saved Review only reads again. Retry Original Review is an explicit,
idempotent retry of the same payload and key; it cannot submit edited terms. A known
rejection returns to the draft. This recovery applies to human Retro review records,
not broker orders, and the retained request is restored across reloads within the same tab, subject to
the draft limits below.


### Draft continuity and review conflicts

Retro review drafts survive navigation to Research, browser Back and reload in the
same tab. Only editable review fields and their original request metadata are
cached; source bodies, account facts and model output are excluded. Cache entries
expire after 24 hours, with at most ten Run drafts and 256,000 characters per entry.
A blocked/quota-limited browser cache displays a warning and clears stale cached
terms rather than presenting an older draft as current. Keep the page open when
that warning is shown. Discard Draft is explicit; successful save/recovery clears
the cache.

When another review version arrives, the editor retains your note, action items
and finding choices. Compare the saved version with Your Draft, then choose Keep
My Draft Against the displayed version to rebase the local draft. This neither
merges fields nor saves a review; Append Review Revision remains separate, with a
new request key and expected version. A further concurrent change is rejected by
the same existing gate.

Reloading an unresolved submission only looks up its exact receipt. It never
reposts automatically. A user-selected retry reconstructs the original frozen
review fields and key. A successful Question Candidate proposal is also distinct
from a confirmed Question or saved Retro link: returning to Journal preserves the
unfinished review rather than declaring the whole workflow complete.


### Priorities and historical comparison

What Needs Attention places actionable overdue items first (oldest deadline first),
then changed versions, missing evidence, reviewable answers and future/deferred or
completed items. This order does not change saved dispositions. Later similar
Findings expose an in-place historical review read with period, snapshot, matching
findings and the saved human note. Collapse it to continue at the original location;
a failed read keeps the current draft. Version-conflict comparisons show only
changed finding fields with readable labels; exact question references remain
available when titles cannot be resolved.
