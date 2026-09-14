export type QuickDraft = { action: "maintain" | "defer"; rationale: string; due: string };
export type QuickRequest = { review_token: string; baseline_decision_id: string | null; action: "maintain" | "defer"; rationale: string; review_due_at: string | null; confirmed: true; idempotency_key: string };
export type QuickPending = { request: QuickRequest; expires_at: string };
type Stored = { version: 1; subject_id: string; saved_at: string; draft: QuickDraft; pending: QuickPending | null };
type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;
const prefix = "tp:quick-review:v1:";
const bounded = (value: unknown, max: number): value is string => typeof value === "string" && value.length <= max;
export function validQuickDraft(value: unknown): value is QuickDraft {
  const draft = value as QuickDraft | null;
  return Boolean(draft && ["maintain", "defer"].includes(draft.action) && bounded(draft.rationale, 8000) && bounded(draft.due, 40) && (!draft.due || Number.isFinite(Date.parse(draft.due))));
}
function validPending(value: unknown): value is QuickPending | null {
  if (value === null) return true;
  const pending = value as QuickPending | undefined; const r = pending?.request;
  return Boolean(r && Number.isFinite(Date.parse(pending!.expires_at)) && bounded(r.review_token, 128) && r.review_token.length > 0 && (r.baseline_decision_id === null || bounded(r.baseline_decision_id, 128)) && ["maintain", "defer"].includes(r.action) && bounded(r.rationale, 8000) && (r.review_due_at === null || Number.isFinite(Date.parse(r.review_due_at))) && r.confirmed === true && bounded(r.idempotency_key, 128) && r.idempotency_key.length > 0);
}
export function readQuickReviewDraft(storage: StorageLike, subjectId: string, now = Date.now()): { value: Stored | null; status: "restored" | "empty" | "invalid" | "unavailable" } {
  try {
    const raw = storage.getItem(prefix + subjectId);
    if (raw === null) return { value: null, status: "empty" };
    if (raw.length > 40000) return { value: null, status: "invalid" };
    const value = JSON.parse(raw) as Stored;
    const age = now - Date.parse(value.saved_at);
    if (value.version !== 1 || value.subject_id !== subjectId || !Number.isFinite(age) || age < -60000 || age > 86400000 || !validQuickDraft(value.draft) || !validPending(value.pending)) return { value: null, status: "invalid" };
    return { value, status: "restored" };
  } catch { return { value: null, status: "unavailable" }; }
}
export function saveQuickReviewDraft(storage: StorageLike, subjectId: string, draft: QuickDraft, pending: QuickPending | null, now = Date.now()): boolean {
  if (!validQuickDraft(draft) || !validPending(pending)) return false;
  // Explicit projection: never retain arbitrary note/model fields passed by a caller.
  const cleanDraft = { action: draft.action, rationale: draft.rationale, due: draft.due };
  const r = pending?.request;
  const cleanPending = r ? { expires_at: pending!.expires_at, request: { review_token: r.review_token, baseline_decision_id: r.baseline_decision_id, action: r.action, rationale: r.rationale, review_due_at: r.review_due_at, confirmed: true, idempotency_key: r.idempotency_key } } : null;
  try { storage.setItem(prefix + subjectId, JSON.stringify({ version: 1, subject_id: subjectId, saved_at: new Date(now).toISOString(), draft: cleanDraft, pending: cleanPending })); return true; } catch { return false; }
}
export function clearQuickReviewDraft(storage: StorageLike, subjectId: string): boolean {
  try { storage.removeItem(prefix + subjectId); return true; } catch { return false; }
}
