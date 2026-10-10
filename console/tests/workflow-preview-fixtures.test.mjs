import assert from "node:assert/strict";
import test from "node:test";

import {
  AS_OF,
  DECISION_SAMPLE,
  RESEARCH_SAMPLE,
  SYNTHETIC_SUBJECT_IDS,
  isSyntheticSubject,
  syntheticDecisionReplay,
  syntheticEmptyQuickReview,
  syntheticEmptyResearchChanges,
  syntheticQuickReview,
  syntheticResearchChanges,
  syntheticResearchLinks,
  syntheticResearchWorkspace,
} from "../dev/research-workflow-fixtures.ts";
import { syntheticDailyReview } from "../dev/daily-review-fixtures.ts";

// The canonical W01 sample must remain one exact Research Subject through every
// read-only preview surface. This catches accidental fixture drift before a UI
// preview can hide it behind a first-item fallback.
test("canonical Today to Quick Review to Evidence and Replay identity is stable", () => {
  const digest = syntheticDailyReview();
  const apple = digest.groups.find((group) => group.instrument_id === "equity:US:AAPL");
  assert.ok(apple);
  assert.equal(apple.subjects[0].subject_id, RESEARCH_SAMPLE);
  assert.equal(apple.reasons[0].subject_id, RESEARCH_SAMPLE);
  assert.match(apple.subjects[0].href, new RegExp(`subject_id=${RESEARCH_SAMPLE}`));
  assert.equal(apple.reasons[0].href, apple.subjects[0].href);

  const workspace = syntheticResearchWorkspace();
  const subject = workspace.subjects.find((item) => item.subject.subject_id === RESEARCH_SAMPLE);
  assert.ok(subject);
  assert.equal(subject.subject.primary_instrument_id, "equity:US:AAPL");

  const links = syntheticResearchLinks();
  assert.equal(links.subject_id, RESEARCH_SAMPLE);
  assert.equal(links.decisions[0].decision_id, DECISION_SAMPLE);
  assert.ok(links.targets.every((target) => target.thesis_revision_id));
  assert.ok(links.impacts.every((impact) => impact.subject_id === RESEARCH_SAMPLE));

  const quick = syntheticQuickReview();
  assert.equal(quick.subject_id, RESEARCH_SAMPLE);
  assert.equal(quick.baseline.decision_id, DECISION_SAMPLE);
  assert.equal(quick.baseline.plan.version, 2);
  assert.equal(quick.expires_at, "2026-12-31T23:59:59Z");
  assert.equal(quick.can_maintain, true);
  assert.equal(quick.current_formal_versions.theses[0][0], subject.state.data.theses[0].thesis_id);
  assert.ok(quick.positions.every((position) => position.snapshot_id));
  assert.equal(quick.latest_thinking[0].note_revision_id, quick.changes[0].note_revision_id);
  assert.equal(quick.pending_observation_reviews, 2);
  assert.equal(quick.review_materials.subject_id, RESEARCH_SAMPLE);
  assert.equal(quick.review_materials.baseline_decision_id, DECISION_SAMPLE);
  assert.equal(quick.review_materials.reference_count, quick.review_materials.references.length);
  assert.equal(quick.review_materials.references_complete, true);

  const changes = syntheticResearchChanges();
  assert.equal(changes.subject_id, RESEARCH_SAMPLE);
  assert.equal(changes.baseline.decision_id, DECISION_SAMPLE);
  assert.equal(changes.as_of, AS_OF);
  assert.ok(changes.items.every((item) => item.recorded_at === AS_OF));

  const replay = syntheticDecisionReplay();
  assert.equal(replay.subject_id, RESEARCH_SAMPLE);
  assert.equal(replay.decision_id, DECISION_SAMPLE);
  assert.ok(replay.sources.every((source) => source.source.source_id));
  assert.ok(replay.impacts.every((impact) => impact.subject_id === RESEARCH_SAMPLE));
});

test("only explicitly defined synthetic Subjects are accepted", () => {
  assert.equal(isSyntheticSubject(RESEARCH_SAMPLE), true);
  assert.equal(isSyntheticSubject("case_unknown_preview_subject"), false);
  assert.throws(() => syntheticQuickReview("case_unknown_preview_subject"), /Unknown synthetic Research Subject/);
  assert.throws(() => syntheticResearchLinks("case_unknown_preview_subject"), /Unknown synthetic Research Subject/);
  assert.throws(() => syntheticDecisionReplay(RESEARCH_SAMPLE, "decision_other_subject"), /Unknown synthetic Decision/);
});

test("Decision Replay pins the reviewed revision instead of today's assumptions", () => {
  for (const subjectId of SYNTHETIC_SUBJECT_IDS) {
    const subject = syntheticResearchWorkspace(subjectId).subjects.find((item) => item.subject.subject_id === subjectId);
    const current = subject.state.data.latest_revisions[0];
    const currentAssumption = subject.state.data.assumptions[0];
    const quick = syntheticQuickReview(subjectId);
    const replay = syntheticDecisionReplay(subjectId);
    const historical = replay.revisions[0];

    assert.equal(historical.thesis_id, current.thesis_id);
    assert.notEqual(historical.revision_id, current.revision_id);
    assert.equal(historical.revision_no, 1);
    assert.equal(current.revision_no, 2);
    assert.equal(subject.state.data.theses[0].current_revision_no, current.revision_no);
    assert.equal(quick.draft_baseline.revision_no, current.revision_no);
    assert.equal(historical.statement, "Contracted demand supports long-term earnings growth while reinvestment remains disciplined.");
    assert.equal(historical.assumptions[0].statement, "Contracted demand remains visible through the investment cycle.");
    assert.notEqual(historical.statement, current.statement);
    assert.notEqual(historical.assumptions[0].statement, currentAssumption.statement);
    assert.equal(quick.baseline.theses[0].revision_id, historical.revision_id);
    assert.equal(quick.baseline.theses[0].statement, historical.statement);
    assert.equal(quick.baseline.theses[0].assumptions[0].statement, historical.assumptions[0].statement);
    assert.equal(historical.confirmed_at, replay.cutoff);

    // Later reads and local edits must not mutate the historical fixture snapshot.
    current.statement = "A later current view";
    currentAssumption.statement = "A later current assumption";
    quick.baseline.theses[0].assumptions[0].statement = "A caller's local edit";
    assert.deepEqual(syntheticDecisionReplay(subjectId), replay);
  }
});

test("Decision Replay excludes evidence links confirmed after its event-time cutoff", () => {
  for (const subjectId of SYNTHETIC_SUBJECT_IDS) {
    const replay = syntheticDecisionReplay(subjectId);
    const links = syntheticResearchLinks(subjectId);
    const laterImpacts = links.impacts.filter((impact) => impact.confirmed_at > replay.cutoff);
    assert.ok(laterImpacts.length > 0);
    assert.deepEqual(replay.impacts, []);
    assert.deepEqual(replay.excluded_references, laterImpacts.map((impact) => ({
      reference_id: impact.impact_id,
      reason: "IMPACT_CONFIRMED_AFTER_CUTOFF",
    })));
    assert.ok(replay.sources.every((source) => source.fact_at <= replay.cutoff && source.recorded_at <= replay.cutoff));
    assert.ok(replay.questions.every((question) => question.asked_at <= replay.cutoff && question.history_availability === "UNAVAILABLE"));
    assert.deepEqual(replay.warnings, ["QUESTION_STATE_HISTORY_UNAVAILABLE"]);
  }
});

test("empty fixtures preserve full card and changes shapes without claiming a maintainable review", () => {
  const quick = syntheticEmptyQuickReview();
  assert.equal(quick.subject_id, RESEARCH_SAMPLE);
  assert.equal(quick.baseline, null);
  assert.deepEqual(quick.changes, []);
  assert.deepEqual(quick.positions, []);
  assert.deepEqual(quick.latest_thinking, []);
  assert.equal(quick.pending_observation_reviews, 0);
  assert.equal(quick.can_maintain, false);
  assert.deepEqual(quick.current_formal_versions, { theses: [], plan: null });

  const changes = syntheticEmptyResearchChanges();
  assert.equal(changes.subject_id, RESEARCH_SAMPLE);
  assert.equal(changes.baseline, null);
  assert.equal(changes.total, 0);
  assert.deepEqual(changes.items, []);
  assert.deepEqual(changes.warning_codes, ["NO_REVIEWED_BASELINE"]);
});
