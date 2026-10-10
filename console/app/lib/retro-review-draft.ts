/** Tab-local owner-authored draft only. No source bodies, account facts or model output. */
export type FindingEdit = { status: string; note: string; followup?: string; question_id?: string | null; question_version?: number | null };
export type RetroDraft = { version: number; attempt: string; status: string; note: string; actions: string; findings: Record<string, FindingEdit>; pending: boolean };
const PREFIX = "trading-partner-retro-draft:";
const TTL = 24 * 60 * 60 * 1000;
const MAX_CHARS = 256_000;
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);

export function decodeRetroDraft(raw: string | null, runId: string, now = Date.now()): RetroDraft | null {
  if (!raw || raw.length > MAX_CHARS) return null;
  try {
    const saved = JSON.parse(raw);
    if (!record(saved) || saved.schema !== 1 || saved.runId !== runId || typeof saved.savedAt !== "number" || saved.savedAt > now || now - saved.savedAt > TTL) return null;
    const d = saved.draft;
    if (!record(d) || !Number.isInteger(d.version) || Number(d.version) < 0 || typeof d.attempt !== "string" || !d.attempt.startsWith("console-retro-review-") || d.attempt.length > 200 || !["OPEN", "ACCEPTED", "DISPUTED", "RESOLVED"].includes(String(d.status)) || typeof d.note !== "string" || typeof d.actions !== "string" || typeof d.pending !== "boolean" || !record(d.findings) || Object.keys(d.findings).length > 100) return null;
    for (const [key, f] of Object.entries(d.findings)) {
      if (!/^finding_[a-f0-9]{64}$/u.test(key) || !record(f) || typeof f.note !== "string" || !["", "ACCEPTED", "DISPUTED", "RESOLVED"].includes(String(f.status)) || ![undefined, "", "QUESTION", "NO_FOLLOWUP", "DEFER"].includes(f.followup as string | undefined)) return null;
      if (f.question_id != null && (typeof f.question_id !== "string" || f.question_id.length > 128)) return null;
      if (f.question_version != null && (!Number.isInteger(f.question_version) || Number(f.question_version) < 1)) return null;
    }
    return d as RetroDraft;
  } catch { return null; }
}
export function loadRetroDraft(runId: string): RetroDraft | null {
  try {
    const key = PREFIX + runId;
    const value = decodeRetroDraft(sessionStorage.getItem(key), runId);
    if (!value) sessionStorage.removeItem(key);
    return value;
  } catch { return null; }
}
export function saveRetroDraft(runId: string, draft: RetroDraft): boolean {
  try {
    const raw = JSON.stringify({ schema: 1, runId, savedAt: Date.now(), draft });
    if (raw.length > MAX_CHARS) { clearRetroDraft(runId); return false; }
    const active: string[] = [];
    for (let index = 0; index < sessionStorage.length; index++) {
      const key = sessionStorage.key(index);
      if (key?.startsWith(PREFIX)) active.push(key);
    }
    const retained = active.filter(key => {
      if (decodeRetroDraft(sessionStorage.getItem(key), key.slice(PREFIX.length))) return true;
      sessionStorage.removeItem(key); return false;
    });
    if (!retained.includes(PREFIX + runId) && retained.length >= 10) return false;
    sessionStorage.setItem(PREFIX + runId, raw);
    return true;
  } catch { clearRetroDraft(runId); return false; }
}
export function clearRetroDraft(runId: string): void {
  try { sessionStorage.removeItem(PREFIX + runId); } catch { /* private mode / quota */ }
}
export function retroReviewRequest(runId: string, d: RetroDraft): Record<string, unknown> {
  return { tool_name: "research_workflow_run", arguments: { request: {
    operation: "trade_retro", action: "review", run_id: runId, expected_version: d.version,
    review_status: d.status, note_markdown: d.note.trim(),
    action_items: d.actions.split("\n").map(s => s.trim()).filter(Boolean),
    finding_reviews: Object.entries(d.findings).filter(([, f]) => f.status).map(([key, f]) => ({
      finding_key: key, status: f.status, note: f.note.trim() || null, followup: f.followup || null,
      question_id: f.question_id || null, question_version: f.question_version || null,
    })), confirmed_by: "user", authorization_note: "User saved this period review in Journal.",
    idempotency_key: d.attempt,
  } }, confirmation: "research_workflow_run", preserve_full_result: true };
}
