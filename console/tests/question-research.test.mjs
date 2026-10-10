import assert from "node:assert/strict";
import test from "node:test";
import { researchOutcome, validQuestionContext } from "../app/lib/question-research.ts";

test("research outcomes do not equate a finished run with a saved answer", () => {
  assert.equal(researchOutcome("COMPLETED", 1, []).title, "Answer Ready for Review");
  assert.equal(researchOutcome("COMPLETED", 0, []).title, "More Evidence Needed");
  for (const stop of ["TIME_BUDGET", "MODEL_BUDGET", "TOOL_BUDGET", "ROUND_LIMIT"]) assert.equal(researchOutcome(stop, 3, []).title, "Research Incomplete");
  assert.equal(researchOutcome("COMPLETED", 0, ["MODEL_PROGRESS_UNAVAILABLE"]).title, "Research Outcome Unavailable");
  assert.equal(researchOutcome("FAILED", 0, []).title, "Research Failed");
  assert.equal(researchOutcome("CANCELLED", 0, []).title, "Research Cancelled");
});

test("question binding rejects missing provenance and arbitrary source kinds", () => {
  const context = { subject_id: "case_fixture", question_id: "question_fixture", question_version: 1, question_fingerprint: "a".repeat(64), evidence_cutoff: "2026-10-02T12:00:00Z", source_refs: [], source_coverage: "COMPLETE" };
  assert.equal(validQuestionContext(context), true);
  assert.equal(validQuestionContext({ ...context, question_version: 0 }), false);
  assert.equal(validQuestionContext({ ...context, source_refs: [{ kind: "WEB", source_id: "url" }] }), false);
  assert.equal(validQuestionContext({ ...context, evidence_cutoff: "not a date" }), false);
  assert.equal(validQuestionContext({ ...context, source_refs: [null] }), false);
  assert.equal(validQuestionContext({ ...context, subject_id: "" }), false);
  assert.equal(validQuestionContext({ ...context, thesis_revision_id: "revision_only" }), false);
});
