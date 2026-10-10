import assert from "node:assert/strict";
import test from "node:test";
import {
  applyResearchQuestionTemplate,
  getResearchQuestionTemplate,
  RESEARCH_QUESTION_TEMPLATES,
} from "../app/lib/research-question-templates.ts";

test("question templates are local, versioned, and cover the three review workflows", () => {
  assert.deepEqual(RESEARCH_QUESTION_TEMPLATES.map((template) => template.id), [
    "earnings_review",
    "competitive_position",
    "thesis_invalidation",
  ]);
  for (const template of RESEARCH_QUESTION_TEMPLATES) {
    assert.equal(template.version, 1);
    assert.ok(template.title);
    assert.ok(template.question);
    assert.ok(template.evidence_requirements.length >= 2);
  }
});

test("applying a template returns only question text and requirements", () => {
  const template = getResearchQuestionTemplate("earnings_review");
  assert.ok(template);
  assert.deepEqual(applyResearchQuestionTemplate(template), {
    text: template.question,
    evidenceRequirements: template.evidence_requirements.join("\n"),
  });
  assert.equal(getResearchQuestionTemplate("missing"), null);
});
