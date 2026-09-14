"use client";

import { useEffect, useRef, useState } from "react";
import { Button, Card, Disclosure, ErrorNote, FieldLabel, Input, LinkButton, Select, Textarea, formatDate } from "../components/ui";
import { ObservationRefreshStatus, startObservationRefresh } from "../components/observation-refresh-status";
import { readQuickReviewDraft, saveQuickReviewDraft, clearQuickReviewDraft, type QuickPending } from "../lib/quick-review-draft";
import { getJson, postApi } from "../lib/api";

type Dict = Record<string, unknown>;
type Snapshot = {
  subject_id: string; title: string; instrument_id: string | null; review_token: string; expires_at: string;
  baseline: { decision_id: string; title: string; rationale?: string; decided_at: string; recorded_at: string; theses: Array<{ thesis_id: string; revision_id: string; statement: string; assumptions?: Dict[]; invalidations?: Dict[] }>; plan: { plan_id: string; version: number } | null } | null;
  changes: Array<{ change_id: string; title: string; kind: string; old_value: unknown; new_value: unknown; occurred_at: string; relation_detail?: string; source_names?: string[] }>;
  coverage: Record<string, string>; warnings: string[]; positions: Array<{ account_ref: string; quantity: string; currency: string; as_of: string; snapshot_id: string }>;
  draft_baseline: null | { thesis_id: string; revision_id: string; revision_no: number; title: string; statement: string; rationale: string };
  latest_thinking: Array<{ note_id: string; note_revision_id: string; version: number; title: string; source_timestamp: string | null; observed_at: string; status: string; user_excerpt: string; user_summary: string; other_viewpoints: Array<{ speaker: string; summary: string }>; warnings: string[]; thinking_date: string | null; thinking_date_basis: string; previous_revision_id: string | null; comparison_basis: string; added_lines: string[]; removed_lines: string[]; comparison_truncated: boolean }>;
  pending_observation_reviews: number; can_maintain: boolean;
};
const DEFAULT_REASON = "Reviewed available changes; maintain the prior judgment with no action.";
function readable(value: unknown) { return value == null ? "Unavailable" : typeof value === "string" ? value : JSON.stringify(value, null, 2); }

export function QuickReview({ subjectId, enabled, onAdjustThesis, onAdjustPlan }: { subjectId: string; enabled: boolean; onAdjustThesis: (seed?: { statement: string; rationale: string; sourceRevisionId: string; expectedThesisId: string | null; expectedRevisionId: string | null }) => void; onAdjustPlan: () => void }) {
  const [pending, setPending] = useState<QuickPending | null>(null);
  const [recovery, setRecovery] = useState<"checking" | "unknown" | "not_found" | "error" | null>(null);
  const [restored, setRestored] = useState(false);
  const [storageWarning, setStorageWarning] = useState<string | null>(null);
  const [storageReady, setStorageReady] = useState(false);
  const storage = useRef<Storage | null>(null);
  const [thinkingDraft, setThinkingDraft] = useState<{ statement: string; rationale: string; sourceRevisionId: string; expectedThesisId: string | null; expectedRevisionId: string | null; before: string } | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [action, setAction] = useState<"maintain" | "defer">("maintain");
  const [rationale, setRationale] = useState(DEFAULT_REASON);
  const [due, setDue] = useState("");
  const [refreshingNotes, setRefreshingNotes] = useState(false);
  const [notesChanged, setNotesChanged] = useState(false);
  const [notesUpdated, setNotesUpdated] = useState(false);
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<{ decision_id: string; action: string; status: string; review_due_at: string | null } | null>(null);
  const [now, setNow] = useState(Date.now());
  const loaded = useRef(false);
  const [readFailed, setReadFailed] = useState(false);
  const generation = useRef(0);
  const intent = useRef<{ signature: string; key: string } | null>(null);
  async function load(afterNoteRefresh = false) {
    const sequence = ++generation.current;
    setLoading(true); setError(null); setReadFailed(false);
    try {
      const response = await getJson(`/api/research/${encodeURIComponent(subjectId)}/quick-review`) as { data: Snapshot };
      if (sequence !== generation.current) return;
      if (response.data?.subject_id !== subjectId || !response.data.review_token) throw new Error("Quick Review returned an incompatible subject.");
      setSnapshot(response.data); setThinkingDraft(null); setNow(Date.now()); setNotesChanged(false); setNotesUpdated(afterNoteRefresh);
    } catch (cause) { if (sequence === generation.current) { setReadFailed(true); setError(cause instanceof Error ? cause.message : "Review unavailable"); } }
    finally { if (sequence === generation.current) setLoading(false); }
  }
  function persist(attempt: QuickPending | null) {
    if (!storage.current || !saveQuickReviewDraft(storage.current, subjectId, { action, rationale, due }, attempt)) setStorageWarning("Browser storage is unavailable. Your draft and recovery key are kept only while this page stays open.");
  }
  function clearSaved() {
    if (storage.current && !clearQuickReviewDraft(storage.current, subjectId)) setStorageWarning("Unable to clear browser recovery storage. A reload will check the saved receipt without resubmitting.");
  }
  async function recover(attempt: QuickPending) {
    setRecovery("checking");
    try {
      const response = await getJson(`/api/research/${encodeURIComponent(subjectId)}/quick-review/submissions/${encodeURIComponent(attempt.request.idempotency_key)}`) as { data: { status: string; decision_id: string; action: string; review_due_at: string | null } };
      if (response.data.status === "RECORDED" && response.data.decision_id) { setResult(response.data); setPending(null); setRecovery(null); clearSaved(); }
      else if (response.data.status === "NOT_FOUND") setRecovery("not_found");
      else throw new Error("Saved submission status is unavailable.");
    } catch (cause) { setRecovery("error"); setError(cause instanceof Error ? cause.message : "Recovery status unavailable"); }
  }
  useEffect(() => {
    try { storage.current = window.sessionStorage; } catch { setStorageWarning("Browser storage is unavailable. Recovery is limited to this page session."); }
    if (storage.current) {
      const saved = readQuickReviewDraft(storage.current, subjectId);
      if (saved.value) { setAction(saved.value.draft.action); setRationale(saved.value.draft.rationale); setDue(saved.value.draft.due); setRestored(true); setPending(saved.value.pending); if (saved.value.pending) void recover(saved.value.pending); }
      else if (saved.status === "invalid") setStorageWarning("A saved draft was expired or invalid and was not restored.");
      else if (saved.status === "unavailable") setStorageWarning("Browser storage is unavailable. Recovery is limited to this page session.");
    }
    setStorageReady(true);
  }, [subjectId]);
  useEffect(() => {
    if (!storageReady || result) return;
    if (pending || action !== "maintain" || rationale !== DEFAULT_REASON || due) persist(pending);
    else clearSaved();
  }, [action, rationale, due, pending, storageReady, result]);
  useEffect(() => {
    if (enabled && !loaded.current) { loaded.current = true; void load(); }
  }, [enabled]);
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => { window.clearInterval(timer); generation.current += 1; }; }, []);
  useEffect(() => {
    const changed = () => {
      if (enabled && !sending && !result && !pending && !thinkingDraft) { void load(true); }
      else { setNotesChanged(true); setNotesUpdated(false); }
    };
    window.addEventListener("tp-observation-data-changed", changed);
    return () => window.removeEventListener("tp-observation-data-changed", changed);
  }, [enabled, sending, result, pending, thinkingDraft, subjectId]);
  const expired = !snapshot || !Number.isFinite(Date.parse(snapshot.expires_at)) || now >= Date.parse(snapshot.expires_at);
  const dueIso = due && Number.isFinite(Date.parse(due)) ? new Date(due).toISOString() : null;
  const invalid = !rationale.trim() || (action === "maintain" ? !snapshot?.baseline || !snapshot.can_maintain : !dueIso || Date.parse(dueIso) <= now);
  async function sendAttempt(attempt: QuickPending) {
    setSending(true); setError(null); setPending(attempt); persist(attempt);
    try {
      const response = await postApi<{ data: { decision_id: string; action: string; status: string; review_due_at: string | null } }>(`/api/research/${encodeURIComponent(subjectId)}/quick-review`, attempt.request);
      if (!response.data?.decision_id || response.data.status !== "RECORDED") throw new Error("The recorded outcome could not be verified. Check or retry this saved submission.");
      setResult(response.data); setPending(null); setRecovery(null); clearSaved();
    } catch (cause) { setRecovery("unknown"); setError(cause instanceof Error ? cause.message : "Submission outcome unavailable. Check or retry this saved submission."); }
    finally { setSending(false); }
  }
  async function submit() {
    if (!snapshot || pending || readFailed || loading || sending || expired || invalid || result) return;
    const body = { review_token: snapshot.review_token, baseline_decision_id: snapshot.baseline?.decision_id ?? null, action, rationale: rationale.trim(), review_due_at: action === "defer" ? dueIso : null, confirmed: true as const };
    const signature = JSON.stringify(body);
    if (intent.current?.signature !== signature) intent.current = { signature, key: crypto.randomUUID() };
    await sendAttempt({ request: { ...body, idempotency_key: intent.current.key }, expires_at: snapshot.expires_at });
  }
  return <Card kicker="REVIEWED JUDGMENT" title={snapshot?.title ?? "Quick Review"}>
    <p>Review your latest thinking and what changed. Maintain the prior judgment, adjust it, or schedule a follow-up.</p>
    <ErrorNote>{error}</ErrorNote>
    {storageWarning && <p role="status">{storageWarning}</p>}
    {restored && !result && <p>Your saved Quick Review draft was restored. Nothing has been submitted automatically.</p>}
    {pending && !result && <div><p>{recovery === "checking" ? "Checking the saved submission…" : recovery === "not_found" ? "No receipt was found for your saved submission. You may explicitly retry the original request." : "A submission outcome is unresolved. Check its receipt or retry the exact original request before changing this review."}</p>
      <p>Saved action: {pending.request.action === "maintain" ? "Maintain Prior Judgment" : "Follow-up"} · {pending.request.rationale}</p>
      <Disclosure title="Saved Review Details"><p>Prior Decision {pending.request.baseline_decision_id ?? "none"} · {pending.request.review_due_at ? `Follow-up ${formatDate(pending.request.review_due_at)}` : "No follow-up date"}</p></Disclosure>
      <Button disabled={sending || recovery === "checking"} onClick={() => { void recover(pending); }}>Check Submission</Button>
      <Button disabled={sending || recovery === "checking" || recovery === "error" || now >= Date.parse(pending.expires_at)} onClick={() => { void sendAttempt(pending); }}>Retry Same Review</Button>
      {now >= Date.parse(pending.expires_at) && <p>The original review expired. Check its receipt first; if none exists, explicitly refresh and review again.</p>}
      {recovery === "not_found" && <Button disabled={sending} onClick={() => { setPending(null); setRecovery(null); intent.current = null; persist(null); void load(); }}>Abandon Attempt and Refresh</Button>}
    </div>}
    {loading && <p>Loading review context…</p>}
    {!result && <Button disabled={loading || sending || Boolean(pending)} onClick={() => { void load(); }}>Refresh Review Context</Button>}
    {snapshot && <>
      <Disclosure title="Prior Reviewed Judgment" defaultOpen><p>{snapshot.baseline ? snapshot.baseline.title : "No prior reviewed judgment; record a follow-up or adjust the research first."}</p>{snapshot.baseline && <><p>{snapshot.baseline.rationale}</p><p>Reviewed {formatDate(snapshot.baseline.recorded_at)}</p>{snapshot.baseline.theses.map((thesis) => <div key={thesis.revision_id}><p>{thesis.statement}</p>{thesis.assumptions?.length ? <pre>{readable(thesis.assumptions)}</pre> : null}{thesis.invalidations?.length ? <pre>{readable(thesis.invalidations)}</pre> : null}</div>)}<p>{snapshot.baseline.plan ? `Reviewed Trade Plan · version ${snapshot.baseline.plan.version}` : "No Plan pinned to this review."}</p></>}</Disclosure>
      <Disclosure title="Latest Thinking" defaultOpen><p>From your latest synced Moomoo notes. Refresh notes when you want to fetch newer thinking.</p>
      {(snapshot.latest_thinking ?? []).length === 0 && <p>No matching synced thinking is available.</p>}
      {(snapshot.latest_thinking ?? []).map((thinking) => <div key={thinking.note_revision_id}><h3>{thinking.title}</h3><p>Thinking date: {thinking.thinking_date ? `${thinking.thinking_date}${thinking.thinking_date_basis === "INFERRED_YEAR" ? " (year inferred)" : thinking.thinking_date_basis === "UNKNOWN" ? " (date uncertain)" : ""}` : "Unknown"} · Synced {formatDate(thinking.observed_at)}</p><p>Source {formatDate(thinking.source_timestamp)} · {thinking.status === "EXTRACTED" ? "Thinking extracted" : thinking.status === "PENDING" ? "Interpretation pending" : thinking.status === "SUMMARY_ONLY" ? "Summary available" : "Interpretation unavailable"}</p><p>{thinking.user_summary || thinking.user_excerpt || "No attributed thinking is available for this note yet."}</p><Disclosure title="Changes from Previous Thinking">{thinking.comparison_basis === "PREVIOUS_SYNCED_USER_SECTION" ? <><p>Compared with the previous synced USER section.</p><p>Added</p><pre>{(thinking.added_lines ?? []).join("\n") || "No added lines"}</pre><p>Removed</p><pre>{(thinking.removed_lines ?? []).join("\n") || "No removed lines"}</pre>{thinking.comparison_truncated && <p>This comparison is shortened; review the original sources for completeness.</p>}<small>Previous revision {thinking.previous_revision_id}</small></> : <p>{thinking.comparison_basis === "NO_PREVIOUS_REVISION" ? "No previous synced revision is available." : "The previous USER section is unavailable or not comparable."}</p>}</Disclosure>{thinking.user_summary && thinking.user_excerpt && <Disclosure title="Original Excerpt"><blockquote>{thinking.user_excerpt}</blockquote></Disclosure>}{thinking.other_viewpoints.length > 0 && <Disclosure title="Quoted Others">{thinking.other_viewpoints.map((other, index) => <p key={index}>{other.speaker}: {other.summary}</p>)}</Disclosure>}{thinking.warnings.length > 0 && <p>Some source details need attention; inspect Source Details below.</p>}{(thinking.user_summary || thinking.user_excerpt) && <Button disabled={sending || Boolean(pending)} onClick={() => setThinkingDraft({ before: snapshot.draft_baseline?.statement ?? "No current PRIMARY Thesis.", expectedThesisId: snapshot.draft_baseline?.thesis_id ?? null, expectedRevisionId: snapshot.draft_baseline?.revision_id ?? null, statement: thinking.user_summary || thinking.user_excerpt, rationale: `Moomoo USER thinking for review. Instrument ${snapshot.instrument_id}; note ${thinking.note_id}; revision ${thinking.note_revision_id}; v${thinking.version}; source ${thinking.source_timestamp ?? "unknown"}; observed ${thinking.observed_at}; status ${thinking.status}.`, sourceRevisionId: thinking.note_revision_id })}>Review Thinking in Thesis</Button>}</div>)}
      {thinkingDraft && <Card kicker="EDITABLE PROPOSAL" title="Review Thesis Changes"><p>Current PRIMARY Statement</p><blockquote>{thinkingDraft.before}</blockquote><label><FieldLabel required>Proposed Statement</FieldLabel><Textarea required maxLength={8000} value={thinkingDraft.statement} onChange={(event) => setThinkingDraft({ ...thinkingDraft, statement: event.target.value })} /></label><p>Your edited statement will open the formal Thesis editor for review; nothing is confirmed here.</p><Button disabled={!thinkingDraft.statement.trim() || sending || Boolean(pending)} onClick={() => onAdjustThesis(thinkingDraft)}>Use Edited Draft</Button><Button onClick={() => setThinkingDraft(null)}>Discard Proposed Draft</Button></Card>}
      <Button disabled={refreshingNotes || sending || Boolean(pending)} onClick={async () => { setRefreshingNotes(true); try { await startObservationRefresh(); } catch (cause) { setError(cause instanceof Error ? cause.message : "Note refresh failed"); } finally { setRefreshingNotes(false); } }}>Refresh Notes</Button>
      {notesUpdated && <p>Notes updated; your draft was kept. Review the refreshed context before confirming.</p>}
      {notesChanged && <p>Note refresh completed. Use Refresh Review Context to inspect the new exact revisions; your current review remains pinned.</p>}
      {enabled && <ObservationRefreshStatus />}
      </Disclosure>
      {(snapshot.warnings.length > 0 || Object.values(snapshot.coverage).some((value) => ["UNAVAILABLE", "PARTIAL", "FAILED", "INCOMPLETE"].includes(value))) && <p>Some evidence is missing or unavailable. Review the changes with that gap in mind, or schedule a follow-up.</p>}
      {snapshot.pending_observation_reviews > 0 && <p>{snapshot.pending_observation_reviews} notes still need their own review. This decision will not adopt them. <LinkButton href="/decision-workbench#notes">Review Notes</LinkButton></p>}
      <Disclosure title="Source Details"><pre>{readable({ coverage: snapshot.coverage, warnings: snapshot.warnings, baseline: snapshot.baseline, notes: (snapshot.latest_thinking ?? []).map(({ user_excerpt, user_summary, other_viewpoints, ...source }) => source), positions: snapshot.positions })}</pre></Disclosure>
      <Disclosure title={`Changes to Review · ${snapshot.changes.length}`} defaultOpen>{snapshot.changes.length === 0 ? <p>No changes returned within the disclosed coverage.</p> : snapshot.changes.map((change) => <Disclosure key={change.change_id} title={change.title}><p>{formatDate(change.occurred_at)} · {change.source_names?.join(", ")}</p><p>{change.relation_detail}</p><p>Previous Value</p><pre>{readable(change.old_value)}</pre><p>New Value</p><pre>{readable(change.new_value)}</pre></Disclosure>)}</Disclosure>
      <Disclosure title="Durable Positions">{snapshot.positions.length === 0 ? <p>No positions returned; this does not assert complete broker coverage.</p> : snapshot.positions.map((position) => <p key={`${position.snapshot_id}:${position.account_ref}`}>{position.account_ref}: {position.quantity} · {position.currency} · {formatDate(position.as_of)}</p>)}</Disclosure>
      <div className="page-actions"><Button disabled={sending || Boolean(pending)} onClick={() => onAdjustThesis()}>Adjust Thesis</Button><Button disabled={sending || Boolean(pending)} onClick={onAdjustPlan}>Adjust Plan</Button>{snapshot.instrument_id && <LinkButton href={`/charts?instrument_id=${encodeURIComponent(snapshot.instrument_id)}&subject_id=${encodeURIComponent(subjectId)}`}>Inspect Chart</LinkButton>}<LinkButton href={`/research?subject_id=${encodeURIComponent(subjectId)}&section=valuation`}>Inspect Valuation</LinkButton><LinkButton href={`/research?subject_id=${encodeURIComponent(subjectId)}&section=calibration`}>Inspect Calibration</LinkButton></div>
    </>}
    {result ? <div aria-live="polite"><p>Recorded {result.action === "maintain" ? "NO_ACTION" : "Follow-up"}{result.review_due_at ? ` · Due ${formatDate(result.review_due_at)}` : ""}</p><Disclosure title="Decision Receipt"><p>{result.decision_id}</p></Disclosure><Button onClick={() => { setResult(null); intent.current = null; setAction("maintain"); setRationale(DEFAULT_REASON); setDue(""); void load(); }}>Review Next</Button></div> : <>
      {snapshot && !snapshot.can_maintain && <p>{!snapshot.baseline ? "No prior reviewed judgment is available to maintain. Adjust the research or schedule a follow-up." : "This review has more changes than can be confirmed together. Inspect the changes and adjust the research or schedule a follow-up."}</p>}
      {snapshot && expired && <p>Review context expired. Refresh to review the current evidence before submitting.</p>}
      <label><FieldLabel required>Review Action</FieldLabel><Select required disabled={sending || Boolean(pending)} value={action} onChange={(event) => { const next = event.target.value as "maintain" | "defer"; setAction(next); if (next === "defer" && rationale === DEFAULT_REASON) setRationale(""); }}><option value="maintain">Maintain Prior Judgment</option><option value="defer">Defer for Missing Evidence</option></Select></label>
      <label><FieldLabel required>{action === "defer" ? "Evidence Gap / Follow-up Reason" : "Review Rationale"}</FieldLabel><Textarea required maxLength={8000} disabled={sending || Boolean(pending)} value={rationale} onChange={(event) => setRationale(event.target.value)} /></label>
      {action === "defer" && <label><FieldLabel required>Follow-up Date (Local Time)</FieldLabel><Input required type="datetime-local" disabled={sending || Boolean(pending)} value={due} onChange={(event) => setDue(event.target.value)} /></label>}
      <p>{action === "maintain" ? "Confirm to maintain your prior judgment and record no action." : "Save your evidence gap and the date you will review it again."}</p>
      <Button disabled={Boolean(pending) || readFailed || loading || sending || expired || invalid || (!snapshot)} onClick={() => { void submit(); }}>{sending ? "Saving…" : action === "maintain" ? "Confirm No Action" : "Save Follow-up"}</Button>
    </>}
  </Card>;
}
