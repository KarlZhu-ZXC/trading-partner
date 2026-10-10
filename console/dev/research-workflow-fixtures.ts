// Synthetic research only. This module never reads owner data or configuration.

export const RESEARCH_SAMPLE = "case_00000000-0000-7000-8000-000000000501";
export const DECISION_SAMPLE = "decision_00000000-0000-7000-8000-000000000502";
export const THESIS_SAMPLE = "thesis_00000000-0000-7000-8000-000000000503";
export const REVISION_SAMPLE = "rev_00000000-0000-7000-8000-000000000504";
export const ASSUMPTION_SAMPLE = "rev_00000000-0000-7000-8000-000000000505";
export const PLAN_SAMPLE = "trade_plan_00000000-0000-7000-8000-000000000506";
export const NOTE_SAMPLE = "external_note_00000000-0000-7000-8000-000000000507";
export const NOTE_REVISION_SAMPLE = "external_note_revision_00000000-0000-7000-8000-000000000508";
export const AS_OF = "2026-09-20T08:00:00Z";

type SyntheticSubject = {
  subject_id: string;
  decision_id: string;
  thesis_id: string;
  revision_id: string;
  reviewed_revision_id: string;
  assumption_id: string;
  plan_id: string;
  note_id: string;
  note_revision_id: string;
  question_id: string;
  title: string;
  summary: string;
  instrument_id: string;
};

const SUBJECT_DEFINITIONS: SyntheticSubject[] = [
  {
    subject_id: RESEARCH_SAMPLE,
    decision_id: DECISION_SAMPLE,
    thesis_id: THESIS_SAMPLE,
    revision_id: REVISION_SAMPLE,
    reviewed_revision_id: "rev_00000000-0000-7000-8000-000000000510",
    assumption_id: ASSUMPTION_SAMPLE,
    plan_id: PLAN_SAMPLE,
    note_id: NOTE_SAMPLE,
    note_revision_id: NOTE_REVISION_SAMPLE,
    question_id: "question_00000000-0000-7000-8000-000000000509",
    title: "Apple · Long-term investment view",
    summary: "Track durable business performance, changing expectations, and the evidence behind each decision.",
    instrument_id: "equity:US:AAPL",
  },
  {
    subject_id: "sample_subject_MSFT",
    decision_id: "sample_decision_MSFT",
    thesis_id: "sample_thesis_MSFT",
    revision_id: "sample_revision_MSFT",
    reviewed_revision_id: "sample_reviewed_revision_MSFT",
    assumption_id: "sample_assumption_MSFT",
    plan_id: "sample_plan_MSFT",
    note_id: "sample_note_MSFT",
    note_revision_id: "sample_revision_note_MSFT",
    question_id: "sample_question_MSFT",
    title: "Microsoft · Cloud investment cycle",
    summary: "Track cloud demand, capital intensity, and the evidence behind the reviewed view.",
    instrument_id: "equity:US:MSFT",
  },
  {
    subject_id: "sample_subject_NVDA",
    decision_id: "sample_decision_NVDA",
    thesis_id: "sample_thesis_NVDA",
    revision_id: "sample_revision_NVDA",
    reviewed_revision_id: "sample_reviewed_revision_NVDA",
    assumption_id: "sample_assumption_NVDA",
    plan_id: "sample_plan_NVDA",
    note_id: "sample_note_NVDA",
    note_revision_id: "sample_revision_note_NVDA",
    question_id: "sample_question_NVDA",
    title: "NVIDIA · Capacity and demand",
    summary: "Track capacity, demand durability, and the evidence behind the reviewed view.",
    instrument_id: "equity:US:NVDA",
  },
  {
    subject_id: "sample_subject_GOOG",
    decision_id: "sample_decision_GOOG",
    thesis_id: "sample_thesis_GOOG",
    revision_id: "sample_revision_GOOG",
    reviewed_revision_id: "sample_reviewed_revision_GOOG",
    assumption_id: "sample_assumption_GOOG",
    plan_id: "sample_plan_GOOG",
    note_id: "sample_note_GOOG",
    note_revision_id: "sample_revision_note_GOOG",
    question_id: "sample_question_GOOG",
    title: "Alphabet · Durable cash generation",
    summary: "Track durable cash generation and the evidence behind the reviewed view.",
    instrument_id: "equity:US:GOOG",
  },
];

// The preview route uses this explicit inventory. A URL containing another
// Subject ID is rejected instead of silently showing the first fixture.
export const SYNTHETIC_SUBJECTS = SUBJECT_DEFINITIONS.map((subject) => ({ ...subject }));
export const SYNTHETIC_SUBJECT_IDS = SYNTHETIC_SUBJECTS.map((subject) => subject.subject_id);

function subjectOrThrow(subjectId: string): SyntheticSubject {
  const subject = SUBJECT_DEFINITIONS.find((item) => item.subject_id === subjectId);
  if (!subject) throw new Error(`Unknown synthetic Research Subject: ${subjectId}`);
  return subject;
}

export function isSyntheticSubject(subjectId: string): boolean {
  return SUBJECT_DEFINITIONS.some((subject) => subject.subject_id === subjectId);
}

export function syntheticSubject(subjectId: string): SyntheticSubject | null {
  const subject = SUBJECT_DEFINITIONS.find((item) => item.subject_id === subjectId);
  return subject ? { ...subject } : null;
}

function thesis(definition: SyntheticSubject) {
  return {
    thesis_id: definition.thesis_id,
    revision_id: definition.revision_id,
    revision_no: 2,
    title: "Durable earnings growth",
    statement: "Durable demand supports long-term earnings growth while reinvestment remains disciplined.",
    assumptions: [{ assumption_id: definition.assumption_id, statement: "Demand and cash generation remain durable through the investment cycle." }],
    invalidations: [{ invalidation_id: `invalidation_${definition.subject_id}`, description: "Sustained demand deterioration or structurally weaker cash generation." }],
  };
}

// Immutable revision pinned by the reviewed Decision, independent of today's Thesis.
function reviewedThesis(definition: SyntheticSubject) {
  return {
    thesis_id: definition.thesis_id,
    revision_id: definition.reviewed_revision_id,
    revision_no: 1,
    statement: "Contracted demand supports long-term earnings growth while reinvestment remains disciplined.",
    assumptions: [{ assumption_id: definition.assumption_id, statement: "Contracted demand remains visible through the investment cycle." }],
    invalidations: [{ invalidation_id: `invalidation_${definition.subject_id}`, description: "Loss of contracted demand or structurally weaker cash generation." }],
  };
}

function baseline(definition: SyntheticSubject) {
  const pinnedThesis = reviewedThesis(definition);
  return {
    decision_id: definition.decision_id,
    title: "Maintain the reviewed long-term judgment",
    rationale: "The reviewed evidence still supports patience while the next operating update is checked.",
    external_note_revision_id: definition.note_revision_id,
    decided_at: "2026-09-19T08:00:00Z",
    recorded_at: "2026-09-19T08:01:00Z",
    theses: [{
      thesis_id: pinnedThesis.thesis_id,
      revision_id: pinnedThesis.revision_id,
      statement: pinnedThesis.statement,
      assumptions: pinnedThesis.assumptions,
      invalidations: pinnedThesis.invalidations,
    }],
    plan: { plan_id: definition.plan_id, version: 2 },
  };
}

function latestThinking(definition: SyntheticSubject) {
  return [{
    note_id: definition.note_id,
    note_revision_id: definition.note_revision_id,
    version: 3,
    title: `${definition.title} · Latest Moomoo thinking`,
    source_timestamp: "2026-09-19T07:30:00Z",
    observed_at: AS_OF,
    status: "EXTRACTED",
    thinking_date: "2026-09-19",
    thinking_date_basis: "EXACT",
    previous_revision_id: `${definition.note_revision_id}_previous`,
    comparison_basis: "PREVIOUS_SYNCED_USER_SECTION",
    added_lines: ["Keep position sizing disciplined until operating evidence confirms the next step."],
    removed_lines: ["Assume near-term price strength proves the thesis."],
    comparison_truncated: false,
    user_excerpt: "Review operating evidence before changing the long-term view.",
    user_summary: "Review operating evidence before changing the long-term view.",
    other_viewpoints: [{ speaker: "Quoted Analyst", summary: "Demand remains constructive, but reinvestment needs monitoring." }],
    warnings: [],
  }];
}

function changes(definition: SyntheticSubject) {
  const observationId = `observation:${definition.note_revision_id}`;
  const monitorId = `monitor:${definition.subject_id}:margin-watch`;
  return [
    {
      change_id: observationId,
      kind: "OBSERVATION",
      title: `${definition.title} operating note updated`,
      occurred_at: "2026-09-20T07:30:00Z",
      recorded_at: AS_OF,
      instrument_id: definition.instrument_id,
      source_id: definition.note_id,
      source_version: "3",
      previous_occurred_at: "2026-09-12T07:30:00Z",
      previous_source_id: `${definition.note_revision_id}_previous`,
      source_names: ["Moomoo Private Notes"],
      old_value: "The long-term view remained unchanged while demand was monitored.",
      new_value: "Operating evidence still supports patience; reinvestment remains a watch item.",
      relation: "SUBJECT_SCOPE",
      relation_detail: "Same Instrument; no inferred Thesis link.",
      thesis_id: null,
      plan_id: null,
      plan_version: null,
      note_revision_id: definition.note_revision_id,
      monitor_id: null,
      event_id: null,
      agenda_item_id: null,
      warning_codes: [],
      reviewed_impacts: [],
    },
    {
      change_id: `${monitorId}:event-1`,
      kind: "MONITOR",
      title: "Reinvestment watch condition observed",
      occurred_at: "2026-09-20T06:00:00Z",
      recorded_at: AS_OF,
      instrument_id: definition.instrument_id,
      source_id: monitorId,
      source_version: "2",
      previous_occurred_at: "2026-09-13T06:00:00Z",
      previous_source_id: `${monitorId}:event-0`,
      source_names: ["Synthetic Monitor"],
      old_value: "Within the reviewed range.",
      new_value: "Within the reviewed range; continue to observe cash conversion.",
      relation: "EXACT_PLAN",
      relation_detail: "The immutable Monitor condition is attached to this exact Trade Plan version.",
      thesis_id: definition.thesis_id,
      plan_id: definition.plan_id,
      plan_version: 2,
      note_revision_id: null,
      monitor_id: monitorId,
      event_id: `${monitorId}:event-1`,
      agenda_item_id: null,
      warning_codes: [],
      reviewed_impacts: [],
    },
  ];
}

export function syntheticResearchLinks(subjectId = RESEARCH_SAMPLE) {
  const definition = subjectOrThrow(subjectId);
  const sources = ["Quarterly filing", "Capacity update", "Customer concentration question"].map((title, index) => ({
    source: { kind: "EVIDENCE", source_id: `evidence_${definition.subject_id}_${index + 1}` },
    title,
    fact_at: `2026-09-${String(16 + index).padStart(2, "0")}T08:00:00Z`,
    recorded_at: `2026-09-${String(16 + index).padStart(2, "0")}T08:00:00Z`,
    origin: "EXTERNAL_FACT",
    source_name: "Synthetic filing",
    source_url: "https://example.test/filing",
    warning_codes: [],
  }));
  const currentThesis = thesis(definition);
  return {
    subject_id: definition.subject_id,
    as_of: AS_OF,
    sources,
    sources_has_more: false,
    targets: [{
      thesis_id: currentThesis.thesis_id,
      thesis_revision_id: currentThesis.revision_id,
      revision_no: currentThesis.revision_no,
      thesis_title: currentThesis.title,
      assumption_id: definition.assumption_id,
      statement: currentThesis.assumptions[0].statement,
      is_current: true,
    }],
    impacts: sources.map((source, index) => ({
      impact_id: `impact_${definition.subject_id}_${index + 1}`,
      version: 1,
      subject_id: definition.subject_id,
      thesis_id: currentThesis.thesis_id,
      thesis_revision_id: currentThesis.revision_id,
      assumption_id: definition.assumption_id,
      source: source.source,
      stance: ["supports", "contradicts", "uncertain"][index],
      rationale: [
        "Contract coverage supports the reviewed assumption.",
        "Capacity timing challenges the expected schedule.",
        "Concentration risk needs additional evidence.",
      ][index],
      state: "ACTIVE",
      confirmed_at: AS_OF,
      confirmed_by: "user",
      candidate_id: null,
      source_available: true,
    })),
    impact_history: [],
    impacts_has_more: false,
    question_history: [],
    decisions: [{ decision_id: definition.decision_id, title: baseline(definition).title, decided_at: baseline(definition).decided_at }],
    coverage: { sources: "COMPLETE", targets: "COMPLETE", impacts: "COMPLETE", impact_history: "COMPLETE", decisions: "COMPLETE", question_history: "COMPLETE" },
  };
}

export function syntheticResearchWorkspace(subjectId = RESEARCH_SAMPLE) {
  subjectOrThrow(subjectId);
  return {
    subjects: SUBJECT_DEFINITIONS.map((definition) => {
      const currentThesis = thesis(definition);
      return {
        subject: {
          subject_id: definition.subject_id,
          subject_type: "company",
          title: definition.title,
          summary: definition.summary,
          status: "active",
          primary_instrument_id: definition.instrument_id,
          topic_tags: ["long-horizon", "evidence-review"],
          linked_subject_ids: [],
          created_at: "2026-09-01T08:00:00Z",
          updated_at: AS_OF,
        },
        state: {
          ok: true,
          data: {
            theses: [{ thesis_id: currentThesis.thesis_id, title: currentThesis.title, status: "active", role: "primary", current_revision_no: currentThesis.revision_no }],
            latest_revisions: [{ thesis_id: currentThesis.thesis_id, revision_id: currentThesis.revision_id, revision_no: currentThesis.revision_no, statement: currentThesis.statement }],
            assumptions: currentThesis.assumptions.map((assumption) => ({ thesis_id: currentThesis.thesis_id, ...assumption, revision_no: currentThesis.revision_no })),
            invalidations: currentThesis.invalidations.map((invalidation) => ({ thesis_id: currentThesis.thesis_id, ...invalidation, revision_no: currentThesis.revision_no })),
            watchlist_items: [],
            pending_candidates: [],
            trade_plan_versions: [{ plan_id: definition.plan_id, version: 2, instrument_id: definition.instrument_id, status: "active" }],
            current_trade_plan: { plan_id: definition.plan_id, version: 2, instrument_id: definition.instrument_id, status: "active", conditions: [] },
            open_questions: [{
              question_id: definition.question_id,
              subject_id: definition.subject_id,
              text: "Does the next filing resolve the reinvestment and concentration question?",
              status: "OPEN",
              asked_at: "2026-09-18T08:00:00Z",
              version: 1,
              evidence_requirements: ["A dated primary filing with operating and concentration details"],
              source_refs: [],
              decision_ids: [],
              due_at: null,
              progress_note: null,
            }],
          },
        },
      };
    }),
  };
}

export function syntheticQuickReview(subjectId = RESEARCH_SAMPLE) {
  const definition = subjectOrThrow(subjectId);
  const reviewedBaseline = baseline(definition);
  const currentThesis = thesis(definition);
  const currentChanges = changes(definition);
  const accountSnapshots = [
    { snapshot_id: `snapshot_${definition.subject_id}_ira`, as_of: AS_OF },
    { snapshot_id: `snapshot_${definition.subject_id}_brokerage`, as_of: AS_OF },
  ];
  const pendingReviewSources = [
    { review_id: `review_${definition.subject_id}_one`, note_revision_id: definition.note_revision_id, version: 1, status: "PENDING" },
    { review_id: `review_${definition.subject_id}_two`, note_revision_id: `${definition.note_revision_id}_previous`, version: 1, status: "DEFERRED" },
  ];
  const coverage = {
    OBSERVATION: "COMPLETE",
    MONITOR: "COMPLETE",
    AGENDA: "NOT_APPLICABLE",
    IMPACT_LINKS: "COMPLETE",
    THINKING: "COMPLETE",
    PORTFOLIO: "COMPLETE",
    OBSERVATION_REVIEWS: "COMPLETE",
  };
  const reviewMaterials = {
    schema_version: 1,
    subject_id: definition.subject_id,
    baseline_decision_id: definition.decision_id,
    prepared_at: AS_OF,
    references: [
      { kind: "THESIS", source_id: currentThesis.revision_id, version: null, as_of: null },
      { kind: "PLAN", source_id: definition.plan_id, version: "2", as_of: null },
      ...currentChanges.map((change) => ({ kind: "CHANGE", source_id: change.source_id, version: change.source_version, as_of: change.occurred_at })),
      ...latestThinking(definition).map((thinking) => ({ kind: "OBSERVATION_REVISION", source_id: thinking.note_revision_id, version: String(thinking.version), as_of: thinking.observed_at })),
      ...accountSnapshots.map((snapshot) => ({ kind: "ACCOUNT_SNAPSHOT", source_id: snapshot.snapshot_id, version: null, as_of: snapshot.as_of })),
      ...pendingReviewSources.map((review) => ({ kind: "OBSERVATION_REVIEW", source_id: review.review_id, version: String(review.version), as_of: null })),
    ],
    reference_count: 9,
    references_complete: true,
    coverage,
    warning_codes: [],
    pending_observation_reviews: pendingReviewSources.length,
  };
  return {
    subject_id: definition.subject_id,
    title: definition.title,
    instrument_id: definition.instrument_id,
    review_token: `synthetic-preview-review-token-${definition.subject_id}`,
    expires_at: "2026-12-31T23:59:59Z",
    baseline: reviewedBaseline,
    changes: currentChanges,
    coverage,
    warnings: [],
    positions: [
      { account_ref: "Schwab IRA", quantity: "10", currency: "USD", as_of: AS_OF, snapshot_id: accountSnapshots[0].snapshot_id },
      { account_ref: "Schwab Brokerage", quantity: "5", currency: "USD", as_of: AS_OF, snapshot_id: accountSnapshots[1].snapshot_id },
    ],
    account_snapshots: accountSnapshots,
    pending_review_sources: pendingReviewSources,
    latest_thinking: latestThinking(definition),
    pending_observation_reviews: pendingReviewSources.length,
    can_maintain: true,
    review_materials: reviewMaterials,
    current_formal_versions: {
      theses: [[currentThesis.thesis_id, currentThesis.revision_id, "active"]],
      plan: [definition.plan_id, 2, "active"],
    },
    draft_baseline: {
      thesis_id: currentThesis.thesis_id,
      revision_id: currentThesis.revision_id,
      revision_no: currentThesis.revision_no,
      title: currentThesis.title,
      statement: currentThesis.statement,
      rationale: "Keep the reviewed statement explicit while the next evidence is collected.",
    },
  };
}

export function syntheticEmptyQuickReview(subjectId = RESEARCH_SAMPLE) {
  const empty = syntheticQuickReview(subjectId);
  return {
    ...empty,
    baseline: null,
    changes: [],
    positions: [],
    account_snapshots: [],
    pending_review_sources: [],
    latest_thinking: [],
    pending_observation_reviews: 0,
    can_maintain: false,
    current_formal_versions: { theses: [], plan: null },
    draft_baseline: null,
    review_materials: {
      schema_version: 1,
      subject_id: empty.subject_id,
      baseline_decision_id: null,
      prepared_at: empty.review_materials.prepared_at,
      references: [],
      reference_count: 0,
      references_complete: true,
      coverage: empty.coverage,
      warning_codes: [],
      pending_observation_reviews: 0,
    },
    coverage: { OBSERVATION: "COMPLETE", MONITOR: "COMPLETE", AGENDA: "NOT_APPLICABLE", IMPACT_LINKS: "COMPLETE", THINKING: "COMPLETE", PORTFOLIO: "COMPLETE", OBSERVATION_REVIEWS: "COMPLETE" },
  };
}

export function syntheticResearchChanges(subjectId = RESEARCH_SAMPLE, baselineDecisionId?: string | null) {
  const definition = subjectOrThrow(subjectId);
  const selectedBaseline = baselineDecisionId === "none" ? null : baseline(definition);
  const items = selectedBaseline ? changes(definition) : [];
  return {
    subject_id: definition.subject_id,
    as_of: AS_OF,
    baseline: selectedBaseline,
    coverage: { OBSERVATION: "COMPLETE", MONITOR: "COMPLETE", AGENDA: "NOT_APPLICABLE", IMPACT_LINKS: "COMPLETE" },
    warning_codes: selectedBaseline ? [] : ["NO_REVIEWED_BASELINE"],
    total: items.length,
    offset: 0,
    limit: 25,
    has_more: false,
    items,
  };
}

export function syntheticEmptyResearchChanges(subjectId = RESEARCH_SAMPLE) {
  return syntheticResearchChanges(subjectId, "none");
}

export function syntheticDecisionReplay(subjectId = RESEARCH_SAMPLE, decisionId?: string) {
  const definition = subjectOrThrow(subjectId);
  if (decisionId !== undefined && decisionId !== definition.decision_id) throw new Error("Unknown synthetic Decision for Research Subject");
  const links = syntheticResearchLinks(subjectId);
  const pinnedThesis = reviewedThesis(definition);
  const reviewedBaseline = baseline(definition);
  return {
    subject_id: definition.subject_id,
    decision_id: definition.decision_id,
    title: reviewedBaseline.title,
    rationale: reviewedBaseline.rationale,
    cutoff: reviewedBaseline.decided_at,
    recorded_at: reviewedBaseline.recorded_at,
    generated_at: "2026-09-23T08:00:00Z",
    revisions: [{
      revision_id: pinnedThesis.revision_id,
      thesis_id: pinnedThesis.thesis_id,
      revision_no: pinnedThesis.revision_no,
      statement: pinnedThesis.statement,
      confirmed_at: reviewedBaseline.decided_at,
      assumptions: pinnedThesis.assumptions.map((assumption) => ({ assumption_id: assumption.assumption_id, statement: assumption.statement, status: "active" })),
    }],
    sources: links.sources,
    impacts: links.impacts.filter((impact) => impact.confirmed_at <= reviewedBaseline.decided_at),
    questions: [{ question_id: definition.question_id, text: "Does the next filing resolve the reinvestment and concentration question?", asked_at: "2026-09-18T08:00:00Z", history_availability: "UNAVAILABLE" }],
    coverage: { revisions: "COMPLETE", sources: "COMPLETE", impacts: "COMPLETE", questions: "PARTIAL" },
    warnings: ["QUESTION_STATE_HISTORY_UNAVAILABLE"],
    excluded_references: links.impacts
      .filter((impact) => impact.confirmed_at > reviewedBaseline.decided_at)
      .map((impact) => ({ reference_id: impact.impact_id, reason: "IMPACT_CONFIRMED_AFTER_CUTOFF" })),
  };
}
