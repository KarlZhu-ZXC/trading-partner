import assert from "node:assert/strict";
import test from "node:test";
import { decodeRetroDraft, retroReviewRequest } from "../app/lib/retro-review-draft.ts";
const runId = "retro_synthetic";
const finding = "finding_" + "a".repeat(64);
const draft = { version: 2, attempt: "console-retro-review-synthetic", status: "ACCEPTED", note: "Owner draft", actions: "Review sources", findings: { [finding]: { status: "ACCEPTED", note: "", followup: "QUESTION", question_id: "question_synthetic", question_version: 3 } }, pending: true };
const raw = (d = draft, overrides = {}) => JSON.stringify({ schema: 1, runId, savedAt: 1000, draft: d, ...overrides });
test("restored pending drafts reproduce only the exact gated review request", () => {
  const restored = decodeRetroDraft(raw(), runId, 2000);
  assert.deepEqual(retroReviewRequest(runId, restored), retroReviewRequest(runId, draft));
  const request = retroReviewRequest(runId, restored);
  assert.equal(request.tool_name, "research_workflow_run");
  assert.equal(request.arguments.request.action, "review");
  assert.equal(request.arguments.request.expected_version, 2);
  assert.equal(request.arguments.request.idempotency_key, draft.attempt);
});
test("wrong scope, expiry, malformed drafts and oversized storage are rejected", () => {
  assert.equal(decodeRetroDraft(raw(), "retro_other", 2000), null);
  assert.equal(decodeRetroDraft(raw(), runId, 86401001), null);
  assert.equal(decodeRetroDraft(raw(), runId, 999), null);
  assert.equal(decodeRetroDraft(raw({ ...draft, version: -1 }), runId, 2000), null);
  assert.equal(decodeRetroDraft(raw({ ...draft, findings: { invalid: { status: "ACCEPTED", note: "" } } }), runId, 2000), null);
  assert.equal(decodeRetroDraft("{"), null);
  assert.equal(decodeRetroDraft("a".repeat(256001), runId, 2000), null);
});

test("quota failure clears stale cache instead of restoring superseded submitted terms", async () => {
  const { saveRetroDraft, loadRetroDraft } = await import("../app/lib/retro-review-draft.ts");
  const entries = new Map();
  let fail = false;
  globalThis.sessionStorage = {
    get length() { return entries.size; },
    key(index) { return [...entries.keys()][index] ?? null; },
    getItem(key) { return entries.get(key) ?? null; },
    removeItem(key) { entries.delete(key); },
    setItem(key, value) { if (fail) throw new Error("quota"); entries.set(key, value); },
  };
  try {
    assert.equal(saveRetroDraft(runId, draft), true);
    assert.deepEqual(loadRetroDraft(runId), draft);
    fail = true;
    assert.equal(saveRetroDraft(runId, { ...draft, note: "New terms" }), false);
    assert.equal(loadRetroDraft(runId), null);
  } finally { delete globalThis.sessionStorage; }
});
