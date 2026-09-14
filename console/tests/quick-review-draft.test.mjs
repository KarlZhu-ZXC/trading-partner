import assert from "node:assert/strict";
import test from "node:test";
import { readQuickReviewDraft, saveQuickReviewDraft, clearQuickReviewDraft } from "../app/lib/quick-review-draft.ts";
const at = Date.parse("2026-09-15T10:00:00Z");
function store() { const data = new Map(); return { data, getItem: (k) => data.get(k) ?? null, setItem: (k,v) => data.set(k,v), removeItem: (k) => data.delete(k) }; }
const draft = { action: "defer", rationale: "Need new evidence", due: "2026-09-16T10:00" };
const pending = { expires_at: "2026-09-15T10:15:00Z", request: { review_token: "read-context", baseline_decision_id: "decision_fixture", action: "defer", rationale: draft.rationale, review_due_at: "2026-09-16T10:00:00Z", confirmed: true, idempotency_key: "exact-key" } };
test("Quick draft and original pending key restore only within their exact subject", () => {
  const storage = store(); assert.equal(saveQuickReviewDraft(storage, "case_a", draft, pending, at), true);
  const restored = readQuickReviewDraft(storage, "case_a", at + 1000);
  assert.equal(restored.status, "restored"); assert.deepEqual(restored.value.draft, draft); assert.deepEqual(restored.value.pending, pending);
  assert.equal(readQuickReviewDraft(storage, "case_b", at).status, "empty");
  clearQuickReviewDraft(storage, "case_a"); assert.equal(readQuickReviewDraft(storage, "case_a", at).status, "empty");
});
test("Persistence projects user fields only and excludes note/provider payloads", () => {
  const storage = store(); saveQuickReviewDraft(storage, "case_a", { ...draft, note_body: "do not store me" }, { ...pending, provider_payload: "private source" }, at);
  const raw = [...storage.data.values()][0]; assert.doesNotMatch(raw, /do not store me|private source|provider_payload|note_body/);
});
test("Expired, malformed and overlong saved drafts are rejected, unavailable storage degrades safely", () => {
  const storage = store(); saveQuickReviewDraft(storage, "case_a", draft, null, at);
  assert.equal(readQuickReviewDraft(storage, "case_a", at + 86400001).status, "invalid");
  assert.equal(saveQuickReviewDraft(storage, "case_a", { ...draft, rationale: "a".repeat(8001) }, null, at), false);
  storage.setItem("tp:quick-review:v1:case_a", "not json"); assert.equal(readQuickReviewDraft(storage, "case_a", at).value, null);
  const unavailable = { getItem() { throw Error("blocked"); }, setItem() { throw Error("blocked"); }, removeItem() { throw Error("blocked"); } };
  assert.equal(readQuickReviewDraft(unavailable, "case_a", at).status, "unavailable"); assert.equal(saveQuickReviewDraft(unavailable, "case_a", draft, null, at), false);
});
test("Expired source token retains recoverable original request within draft lifetime", () => {
  const storage = store(); saveQuickReviewDraft(storage, "case_a", draft, pending, at);
  assert.deepEqual(readQuickReviewDraft(storage, "case_a", at + 3600000).value.pending.request, pending.request);
});
