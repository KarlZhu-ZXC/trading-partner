"use client";

import { asObject as asDict, textStringified as text } from "../lib/coerce";

import { Button, Input, Select, Textarea } from "../components/ui/controls";

import { useEffect, useRef, useState } from "react";
import { ActionButton, Badge, Disclosure, Empty, ErrorNote, FormField, Table, formatDate, shortId } from "../components/ui";
import { getJson, listOf, postApi, useApi } from "../lib/api";

import { loadRetroDraft, saveRetroDraft, clearRetroDraft, retroReviewRequest, type RetroDraft } from "../lib/retro-review-draft";

type Dict = Record<string, unknown>;
import { FindingFollowup, RetroCoverage, SavedFollowup, type FollowupEdit } from "./retro-workflow";
import { FindingChoiceComparison } from "./retro-comparison";
type FindingEdit = FollowupEdit;

function editsFrom(review: Dict): Record<string, FindingEdit> {
  return Object.fromEntries(listOf<Dict>(review, "finding_reviews").map((item) => [
    text(item.finding_key, ""),
    { status: text(item.status, ""), note: text(item.note, ""), followup: text(item.followup, ""), question_id: item.question_id as string | null, question_version: item.question_version as number | null },
  ]).filter(([key]) => key));
}

function ReviewCard({ run, onUpdated }: { run: Dict; onUpdated: () => void }) {
  const runId = text(run.run_id, "");
  const findings = listOf<Dict>(run, "findings");
  const latestReview = asDict(run.latest_review);
  const reviewVersion = Number(latestReview.version ?? 0);
  const [editing, setEditing] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const [storageOk, setStorageOk] = useState(true);
  const restored = useRef(false);
  const [saving, setSaving] = useState(false);
  const [inspect, setInspect] = useState(false);
  const [draftVersion, setDraftVersion] = useState(reviewVersion);
  const [attempt, setAttempt] = useState("");
  const [pendingRequest, setPendingRequest] = useState<Dict | null>(null);
  const [recovering, setRecovering] = useState(false);
  const [receiptMessage, setReceiptMessage] = useState<string | null>(null);
  const workflow = useApi<{ data: Dict }>(`/api/retro/${encodeURIComponent(runId)}/workflow`, { enabled: inspect || editing });
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState(text(latestReview.status, "OPEN"));
  const [note, setNote] = useState(text(latestReview.note_markdown, ""));
  const [actions, setActions] = useState(listOf<string>(latestReview, "action_items").join("\n"));
  const [findingEdits, setFindingEdits] = useState<Record<string, FindingEdit>>(() => editsFrom(latestReview));
  const reviewedCount = findings.filter((finding) => findingEdits[text(finding.finding_key, "")]?.status).length;

  function draftSnapshot(pending = pendingRequest !== null): RetroDraft {
    return { version: draftVersion, attempt, status, note, actions, findings: findingEdits, pending };
  }
  useEffect(() => {
    if (restored.current) return;
    restored.current = true;
    const cached = loadRetroDraft(runId);
    if (cached) {
      setDraftVersion(cached.version); setAttempt(cached.attempt); setStatus(cached.status);
      setNote(cached.note); setActions(cached.actions); setFindingEdits(cached.findings);
      setEditing(true);
      if (cached.pending) {
        const request = retroReviewRequest(runId, cached);
        setPendingRequest(request); void checkReceipt(request);
      } else setReceiptMessage("Restored your local draft. Review it before saving.");
    }
    setHydrated(true);
  }, [runId]);
  useEffect(() => {
    if (!hydrated) return;
    if (editing || pendingRequest) setStorageOk(saveRetroDraft(runId, draftSnapshot()));
    else clearRetroDraft(runId);
  }, [hydrated, editing, pendingRequest, draftVersion, attempt, status, note, actions, findingEdits, runId]);
  useEffect(() => {
    if (!hydrated || editing) return;
    setStatus(text(latestReview.status, "OPEN"));
    setNote(text(latestReview.note_markdown, ""));
    setActions(listOf<string>(latestReview, "action_items").join("\n"));
    setFindingEdits(editsFrom(latestReview));
  }, [reviewVersion, editing, hydrated]);

  function setFinding(findingKey: string, patch: Partial<FindingEdit>) {
    setFindingEdits((current) => ({
      ...current,
      [findingKey]: { ...(current[findingKey] ?? { status: "", note: "" }), ...patch },
    }));
  }

  async function save() {
    if (saving || pendingRequest) return;
    if (reviewVersion !== draftVersion) { setError("A newer review is saved. Compare it below; your draft is preserved."); return; }
    if (Object.values(findingEdits).some(edit => edit.followup && (!edit.status || (edit.followup === "QUESTION" && !edit.question_id) || (edit.followup === "NO_FOLLOWUP" && !edit.note.trim())))) {
      setError("Choose a finding disposition and complete its follow-up reference or rationale."); return;
    }
    const disputedWithoutNote = findings.some((finding) => {
      const edit = findingEdits[text(finding.finding_key, "")];
      return edit?.status === "DISPUTED" && !edit.note.trim();
    });
    if (disputedWithoutNote) {
      setError("A disputed finding requires a note.");
      return;
    }
    setReceiptMessage(null);
    const submitted = draftSnapshot(true);
    const request = retroReviewRequest(runId, submitted);
    setStorageOk(saveRetroDraft(runId, submitted));
    setPendingRequest(request);
    await sendReview(request);
  }

  async function checkReceipt(request: Dict) {
    setRecovering(true);
    try {
      const original = asDict(asDict(request.arguments).request);
      const response = await getJson(`/api/retro/${encodeURIComponent(runId)}/review-receipt?idempotency_key=${encodeURIComponent(text(original.idempotency_key))}`);
      const receipt = asDict(asDict(response).data);
      if (receipt.run_id === runId && Number(receipt.version) === Number(original.expected_version) + 1) {
        clearRetroDraft(runId); setPendingRequest(null); setError(null); setEditing(false);
        setReceiptMessage(`Review v${receipt.version} was saved. Recovered the original receipt without resubmitting.`);
        onUpdated();
      } else {
        setError("Save not confirmed yet. Check again or retry the original review with its same request key.");
      }
    } catch {
      setError("Receipt read unavailable. Your original review is retained; no new submission was made.");
    } finally { setRecovering(false); }
  }

  async function sendReview(request: Dict) {
    setSaving(true); setError(null);
    try {
      const response = await postApi<Dict>("/api/tools/invoke", request);
      const envelope = asDict(response.result);
      if (envelope.ok === false) {
        setPendingRequest(null);
        setError(text(listOf<Dict>(envelope, "errors")[0]?.message, "Review revision was rejected."));
        onUpdated();
        return;
      }
      if (envelope.ok !== true) throw new Error("Unknown response");
      clearRetroDraft(runId); setPendingRequest(null); setEditing(false);
      setReceiptMessage("Review saved. Reloading the saved records."); onUpdated();
    } catch {
      setError("Save response unavailable. Checking the original receipt…");
      await checkReceipt(request);
    } finally { setSaving(false); }
  }

  return <article className="journal-review-run" id={`review-${runId}`}>
    <header><div><strong>{text(run.period_start).slice(0, 10)} → {text(run.period_end).slice(0, 10)}</strong><small>{formatDate(run.generated_at)} · {shortId(runId)}</small></div><div className="page-actions"><Badge value={text(run.status)} /><Badge value={reviewVersion ? text(latestReview.status) : "UNREVIEWED"} /></div></header>
    <p>{findings.length} deterministic finding{findings.length === 1 ? "" : "s"} · generated facts remain immutable.</p>
    {findings.length ? <Disclosure className="journal-review-finding-disclosure" title={`Inspect ${findings.length} Findings`} variant="compact"><div className="journal-review-findings">{findings.map((finding) => <div key={text(finding.finding_key)}><span><strong>{text(finding.title, text(finding.code))}</strong><small>{text(finding.detail)}</small></span><Badge value={text(finding.severity)} /></div>)}</div></Disclosure> : <span className="card-note">No deterministic findings.</span>}
    <div className="page-actions"><ActionButton disabled={!hydrated || saving || recovering || pendingRequest !== null} onClick={() => { setReceiptMessage(null); setError(null); if (!editing) { setDraftVersion(reviewVersion); setAttempt(`console-retro-review-${crypto.randomUUID()}`); } setEditing(value => !value); }}>{editing ? "Discard Draft" : reviewVersion ? "Revise Review" : "Review Findings"}</ActionButton></div>
    <Button onClick={() => { setInspect(true); workflow.refresh(); }}>Inspect Coverage and Reload Follow-ups</Button>
    {(inspect || editing) && (workflow.loading ? <p>Reading durable coverage…</p> : workflow.error ? <ErrorNote>Coverage and follow-up choices unavailable; original findings remain saved.</ErrorNote> : workflow.data?.data ? <RetroCoverage data={workflow.data.data} /> : null)}
    {reviewVersion > 0 && <Disclosure title="Saved Follow-up Choices"><ul>{listOf<Dict>(latestReview, "finding_reviews").map(row => <SavedFollowup key={text(row.finding_key)} row={row} data={workflow.data?.data} />)}</ul></Disclosure>}
    {!storageOk && <ErrorNote>Browser storage is unavailable or the draft is too large. Keep this page open to preserve your edits.</ErrorNote>}
    {receiptMessage && <p role="status">{receiptMessage}</p>}
    {pendingRequest && <section aria-label="Review Save Recovery"><ErrorNote>{error}</ErrorNote><p>Your submitted review remains unchanged. Checking a receipt never resubmits.</p><Button disabled={saving || recovering} onClick={() => { void checkReceipt(pendingRequest); }}>Check Saved Review</Button><Button disabled={saving || recovering} onClick={() => { void sendReview(pendingRequest); }}>Retry Original Review</Button></section>}
    {editing && !pendingRequest ? <section className="journal-review-editor">
      {reviewVersion !== draftVersion && <section aria-label="Review Version Conflict"><h4>A Newer Review Is Saved</h4><p>Your draft started from v{draftVersion}; the saved review is v{reviewVersion}. Compare both before continuing. No changes are merged automatically.</p><Table><thead><tr><th>Field</th><th>Saved v{reviewVersion}</th><th>Your Draft</th></tr></thead><tbody>{[["Status", text(latestReview.status), status], ["Note", text(latestReview.note_markdown, ""), note], ["Actions", listOf<string>(latestReview, "action_items").join("\n"), actions]].map(([label, saved, own]) => <tr key={label}><th>{label}</th><td>{saved || "—"}</td><td>{own || "—"}</td></tr>)}</tbody></Table><FindingChoiceComparison saved={listOf<Dict>(latestReview, "finding_reviews")} draft={findingEdits} findings={findings} /><Button onClick={() => { setDraftVersion(reviewVersion); setAttempt(`console-retro-review-${crypto.randomUUID()}`); setError(null); }}>Keep My Draft Against v{reviewVersion}</Button><p>This only updates the draft basis. Append Review Revision remains a separate action.</p></section>}

      <div className="journal-review-progress"><strong>{reviewedCount} / {findings.length} findings dispositioned</strong><progress max={Math.max(findings.length, 1)} value={reviewedCount} /><div className="page-actions"><Button type="button" onClick={() => setFindingEdits(current => Object.fromEntries(findings.map(finding => { const id = text(finding.finding_key, ""); return [id, { ...current[id], status: "ACCEPTED", note: current[id]?.note ?? "" }]; }))) }>Accept All</Button><Button type="button" onClick={() => setFindingEdits({})}>Clear Dispositions</Button></div></div>
      <div className="portfolio-form-grid"><FormField label="Review Status" required><Select required value={status} onChange={(event) => setStatus(event.target.value)}>{["OPEN", "ACCEPTED", "DISPUTED", "RESOLVED"].map((item) => <option key={item}>{item}</option>)}</Select></FormField><FormField label="Review Note"><Textarea rows={4} value={note} onChange={(event) => setNote(event.target.value)} /></FormField><FormField label="Action Items"><Textarea rows={4} value={actions} onChange={(event) => setActions(event.target.value)} placeholder="Choose 1–3 concrete process improvements, one per line" /></FormField></div>
      {findings.length ? <div className="journal-finding-editor">{findings.map((finding, index) => { const key = text(finding.finding_key, ""); const edit = findingEdits[key] ?? { status: "", note: "" }; return <div key={key}><span><strong>{index + 1} · {text(finding.code)}</strong><small>{text(finding.title)}</small><small>{text(finding.detail)}</small><code>{shortId(key)}</code></span><Select aria-label={`${text(finding.code)} disposition`} value={edit.status} onChange={(event) => setFinding(key, { status: event.target.value })}><option value="">Unreviewed</option><option value="ACCEPTED">Accepted</option><option value="DISPUTED">Disputed</option><option value="RESOLVED">Resolved</option></Select><label><span>{(edit.status === "DISPUTED" || edit.followup === "NO_FOLLOWUP") ? <b className="required-mark" aria-hidden="true">*</b> : null}Finding Note</span><Input aria-label={`${text(finding.code)} note`} required={edit.status === "DISPUTED" || edit.followup === "NO_FOLLOWUP"} value={edit.note} onChange={(event) => setFinding(key, { note: event.target.value })} placeholder={edit.status === "DISPUTED" ? "Required correction note" : "Optional note"} /></label><FindingFollowup runId={runId} findingKey={key} data={listOf<Dict>(workflow.data?.data, "findings").find(f => f.finding_key === key)} edit={edit} onChange={patch => setFinding(key, patch)} /></div>; })}</div> : null}
      <ErrorNote>{error}</ErrorNote><div className="page-actions journal-review-submit"><ActionButton busy={saving} disabled={reviewVersion !== draftVersion} onClick={() => { void save(); }}>Append Review Revision</ActionButton><small>Generated findings remain immutable.</small></div>
    </section> : null}
  </article>;
}

export function RetroReviewList({ runs, onUpdated }: { runs: Dict[]; onUpdated: () => void }) {
  if (runs.length === 0) return <Empty>No immutable period review has been generated yet.</Empty>;
  return <div className="journal-review-list">{runs.map((run) => <ReviewCard key={text(run.run_id)} run={run} onUpdated={onUpdated} />)}</div>;
}
