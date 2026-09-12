import assert from "node:assert/strict";
import test from "node:test";
import { observationResearchSeed } from "../app/lib/observation-research-draft.ts";

function draft() {
  return {
    source: {
      note_revision_id: "external_note_revision_fixture_7", note_id: "external_note_fixture",
      note_version: 7, title: "Add at 210 / exit below 200", instrument_id: "equity:US:TEST",
      observed_at: "2026-09-08T00:00:00Z", analysis_kind: "ESCALATED_REVIEW",
      analysis_id: "review_draft_fixture", provider: "fixture", model: "fixture-model",
      created_at: "2026-09-08T00:01:00Z",
    },
    payload: {
      material_change_summary: "A new pullback condition was recorded.",
      viewpoints: [
        { speaker_kind: "USER", speaker_label: "USER", summary: "Hold while the business thesis remains valid.", direction: "UP", holding_horizon: "LONG_TERM", structure: "Pullback under review.", source_block_ordinals: [1] },
        { speaker_kind: "NAMED_PERSON", speaker_label: "Analyst", summary: "Sell the entire position at 170.", direction: "DOWN", holding_horizon: "SWING", structure: "Bearish reference.", source_block_ordinals: [2] },
      ],
      user_scenarios: ["UPSIDE", "SIDEWAYS", "PULLBACK", "INVALIDATION"].map((scenario) => ({
        scenario, action: "REVIEW", condition: "Watch 210 support.", confirmation: "Wait for a closing-price confirmation.", loss_boundary: "Review below 200.",
      })),
      catalysts: ["Next earnings"], key_levels: ["210 support", "Analyst: 170 exit"],
      missing_evidence: ["Latest cash-flow evidence"], contradictions: ["Different holding horizons"],
    },
    warnings: [],
  };
}

test("Observation seed carries USER judgment and level references with exact provenance", () => {
  const source = draft();
  const seed = observationResearchSeed(source);
  assert.equal(seed.subject.title, "TEST Research");
  assert.doesNotMatch(seed.subject.summary, /210|200|170/);
  assert.equal(seed.thesis.statement, source.payload.viewpoints[0].summary);
  assert.doesNotMatch(seed.thesis.statement, /170|Sell the entire/);
  assert.match(seed.thesis.rationale, /external_note_revision_fixture_7/);
  assert.match(seed.thesis.rationale, /review_draft_fixture/);
  assert.match(seed.plan.notes, /210 support/);
  assert.match(seed.thesis.invalidationCheckNote, /below 200/);
  assert.equal(seed.otherViewpoints[0].speaker, "Analyst");
  assert.equal(seed.plan.conditions.length, 4);
  assert.ok(seed.plan.conditions.every((item) => item.mode === "MANUAL" && item.threshold === ""));
  assert.ok(seed.plan.conditions.every((item) => !item.description.includes("170")));
  assert.equal(seed.plan.stop, undefined);
  assert.equal(seed.plan.referencePrice, undefined);
});

test("Unparsed observations never manufacture a judgment", () => {
  const value = draft();
  value.payload = null;
  const seed = observationResearchSeed(value);
  assert.equal(seed.ready, false);
  assert.equal(seed.thesis.statement, "");
  assert.deepEqual(seed.plan.conditions, []);
});

test("Named viewpoints do not become the owner's statement or conditions", () => {
  const value = draft();
  value.payload.viewpoints = value.payload.viewpoints.filter((item) => item.speaker_kind !== "USER");
  const seed = observationResearchSeed(value);
  assert.equal(seed.thesis.statement, "");
  assert.equal(seed.plan.conditions.length, 0);
  assert.ok(seed.warnings.some((item) => item.includes("No USER viewpoint")));
});

test("Oversized drafts keep a full reference and disclose editor truncation", () => {
  const value = draft();
  value.payload.viewpoints[0].summary = "x".repeat(9000);
  const seed = observationResearchSeed(value);
  assert.equal(seed.thesis.statement.length, 8000);
  assert.ok(seed.fullReviewText.includes("x".repeat(9000)));
  assert.ok(seed.warnings.some((item) => item.includes("editor limit")));
  assert.ok(seed.plan.notes.length <= 8000);
});
