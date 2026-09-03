"use client";

import { useEffect, useState } from "react";
import { ActionButton, Badge, Disclosure, Empty, ErrorNote, FormField, formatDate, shortId } from "../components/ui";
import { listOf, postApi } from "../lib/api";

type Dict = Record<string, unknown>;
type FindingEdit = { status: string; note: string };

function asDict(value: unknown): Dict {
  return value && typeof value === "object" ? value as Dict : {};
}

function text(value: unknown, fallback = "—"): string {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

function editsFrom(review: Dict): Record<string, FindingEdit> {
  return Object.fromEntries(listOf<Dict>(review, "finding_reviews").map((item) => [
    text(item.finding_key, ""),
    { status: text(item.status, ""), note: text(item.note, "") },
  ]).filter(([key]) => key));
}

function ReviewCard({ run, onUpdated }: { run: Dict; onUpdated: () => void }) {
  const runId = text(run.run_id, "");
  const findings = listOf<Dict>(run, "findings");
  const latestReview = asDict(run.latest_review);
  const reviewVersion = Number(latestReview.version ?? 0);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState(text(latestReview.status, "OPEN"));
  const [note, setNote] = useState(text(latestReview.note_markdown, ""));
  const [actions, setActions] = useState(listOf<string>(latestReview, "action_items").join("\n"));
  const [findingEdits, setFindingEdits] = useState<Record<string, FindingEdit>>(() => editsFrom(latestReview));
  const reviewedCount = findings.filter((finding) => findingEdits[text(finding.finding_key, "")]?.status).length;

  useEffect(() => {
    setStatus(text(latestReview.status, "OPEN"));
    setNote(text(latestReview.note_markdown, ""));
    setActions(listOf<string>(latestReview, "action_items").join("\n"));
    setFindingEdits(editsFrom(latestReview));
  }, [reviewVersion]);

  function setFinding(findingKey: string, patch: Partial<FindingEdit>) {
    setFindingEdits((current) => ({
      ...current,
      [findingKey]: { ...(current[findingKey] ?? { status: "", note: "" }), ...patch },
    }));
  }

  async function save() {
    const disputedWithoutNote = findings.some((finding) => {
      const edit = findingEdits[text(finding.finding_key, "")];
      return edit?.status === "DISPUTED" && !edit.note.trim();
    });
    if (disputedWithoutNote) {
      setError("A disputed finding requires a note.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const findingReviews = findings.flatMap((finding) => {
        const findingKey = text(finding.finding_key, "");
        const edit = findingEdits[findingKey];
        return edit?.status ? [{ finding_key: findingKey, status: edit.status, note: edit.note.trim() || null }] : [];
      });
      const response = await postApi<Dict>("/api/tools/invoke", {
        tool_name: "research_workflow_run",
        arguments: { request: {
          operation: "trade_retro",
          action: "review",
          run_id: runId,
          expected_version: reviewVersion,
          review_status: status,
          note_markdown: note.trim(),
          action_items: actions.split("\n").map((item) => item.trim()).filter(Boolean),
          finding_reviews: findingReviews,
          confirmed_by: "user",
          authorization_note: "User saved this period review in Journal.",
          idempotency_key: `console-journal-review-${runId}-${reviewVersion + 1}-${crypto.randomUUID()}`,
        } },
        confirmation: "research_workflow_run",
        preserve_full_result: true,
      });
      const envelope = asDict(response.result);
      if (envelope.ok !== true) {
        const firstError = listOf<Dict>(envelope, "errors")[0];
        throw new Error(text(firstError?.message, "Review revision was rejected."));
      }
      setEditing(false);
      onUpdated();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to save review revision.");
    } finally {
      setSaving(false);
    }
  }

  return <article className="journal-review-run" id={`review-${runId}`}>
    <header><div><strong>{text(run.period_start).slice(0, 10)} → {text(run.period_end).slice(0, 10)}</strong><small>{formatDate(run.generated_at)} · {shortId(runId)}</small></div><div className="page-actions"><Badge value={text(run.status)} /><Badge value={reviewVersion ? text(latestReview.status) : "UNREVIEWED"} /></div></header>
    <p>{findings.length} deterministic finding{findings.length === 1 ? "" : "s"} · generated facts remain immutable.</p>
    {findings.length ? <Disclosure className="journal-review-finding-disclosure" title={`Inspect ${findings.length} Findings`} variant="compact"><div className="journal-review-findings">{findings.map((finding) => <div key={text(finding.finding_key)}><span><strong>{text(finding.title, text(finding.code))}</strong><small>{text(finding.detail)}</small></span><Badge value={text(finding.severity)} /></div>)}</div></Disclosure> : <span className="card-note">No deterministic findings.</span>}
    <div className="page-actions"><ActionButton onClick={() => setEditing((value) => !value)}>{editing ? "Close Review" : reviewVersion ? "Revise Review" : "Review Findings"}</ActionButton></div>
    {editing ? <section className="journal-review-editor">
      <div className="journal-review-progress"><strong>{reviewedCount} / {findings.length} findings dispositioned</strong><progress max={Math.max(findings.length, 1)} value={reviewedCount} /><div className="page-actions"><button type="button" onClick={() => setFindingEdits(Object.fromEntries(findings.map((finding) => [text(finding.finding_key, ""), { status: "ACCEPTED", note: "" }]))) }>Accept All</button><button type="button" onClick={() => setFindingEdits({})}>Clear Dispositions</button></div></div>
      <div className="portfolio-form-grid"><FormField label="Review Status" required><select required value={status} onChange={(event) => setStatus(event.target.value)}>{["OPEN", "ACCEPTED", "DISPUTED", "RESOLVED"].map((item) => <option key={item}>{item}</option>)}</select></FormField><FormField label="Review Note"><textarea rows={4} value={note} onChange={(event) => setNote(event.target.value)} /></FormField><FormField label="Action Items"><textarea rows={4} value={actions} onChange={(event) => setActions(event.target.value)} placeholder="One durable follow-up per line" /></FormField></div>
      {findings.length ? <div className="journal-finding-editor">{findings.map((finding, index) => { const key = text(finding.finding_key, ""); const edit = findingEdits[key] ?? { status: "", note: "" }; return <div key={key}><span><strong>{index + 1} · {text(finding.code)}</strong><small>{text(finding.title)}</small><small>{text(finding.detail)}</small><code>{shortId(key)}</code></span><select aria-label={`${text(finding.code)} disposition`} value={edit.status} onChange={(event) => setFinding(key, { status: event.target.value })}><option value="">Unreviewed</option><option value="ACCEPTED">Accepted</option><option value="DISPUTED">Disputed</option><option value="RESOLVED">Resolved</option></select><label><span>{edit.status === "DISPUTED" ? <b className="required-mark" aria-hidden="true">*</b> : null}Finding Note</span><input aria-label={`${text(finding.code)} note`} required={edit.status === "DISPUTED"} value={edit.note} onChange={(event) => setFinding(key, { note: event.target.value })} placeholder={edit.status === "DISPUTED" ? "Required correction note" : "Optional note"} /></label></div>; })}</div> : null}
      <ErrorNote>{error}</ErrorNote><div className="page-actions journal-review-submit"><ActionButton busy={saving} onClick={() => { void save(); }}>Append Review Revision</ActionButton><small>Generated findings remain immutable.</small></div>
    </section> : null}
  </article>;
}

export function RetroReviewList({ runs, onUpdated }: { runs: Dict[]; onUpdated: () => void }) {
  if (runs.length === 0) return <Empty>No immutable period review has been generated yet.</Empty>;
  return <div className="journal-review-list">{runs.map((run) => <ReviewCard key={text(run.run_id)} run={run} onUpdated={onUpdated} />)}</div>;
}
