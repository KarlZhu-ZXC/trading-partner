"use client";

import { useEffect, useState } from "react";
import { ConsoleShell } from "../components/console-shell";
import { ActionButton, Badge, Card, DataBoundary, Empty, RefreshButton, formatDate } from "../components/ui";
import { listOf, postApi, useApi } from "../lib/api";

type Dict = Record<string, unknown>;
type FindingEdit = { status: string; note: string };

function asDict(value: unknown): Dict {
  return value && typeof value === "object" ? value as Dict : {};
}

function text(value: unknown, fallback = "—"): string {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

function initialFindingEdits(review: Dict): Record<string, FindingEdit> {
  return Object.fromEntries(listOf<Dict>(review, "finding_reviews").map((item) => [
    text(item.finding_key, ""),
    { status: text(item.status, ""), note: text(item.note, "") },
  ]).filter(([key]) => key));
}

function RetroRunCard({
  run,
  busy,
  onExport,
  onReviewed,
}: {
  run: Dict;
  busy: string | null;
  onExport: (runId: string, reviewVersion: number) => Promise<void>;
  onReviewed: () => void;
}) {
  const runId = text(run.run_id, "");
  const findings = listOf<Dict>(run, "findings");
  const latestReview = asDict(run.latest_review);
  const reviewHistory = listOf<Dict>(run, "review_history");
  const reviewVersion = Number(latestReview.version ?? 0);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState(text(latestReview.status, "OPEN"));
  const [note, setNote] = useState(text(latestReview.note_markdown, ""));
  const [actions, setActions] = useState(listOf<string>(latestReview, "action_items").join("\n"));
  const [findingEdits, setFindingEdits] = useState<Record<string, FindingEdit>>(() => initialFindingEdits(latestReview));

  useEffect(() => {
    setStatus(text(latestReview.status, "OPEN"));
    setNote(text(latestReview.note_markdown, ""));
    setActions(listOf<string>(latestReview, "action_items").join("\n"));
    setFindingEdits(initialFindingEdits(latestReview));
  }, [reviewVersion]);

  function setFinding(findingKey: string, patch: Partial<FindingEdit>) {
    setFindingEdits((current) => ({
      ...current,
      [findingKey]: { ...(current[findingKey] ?? { status: "", note: "" }), ...patch },
    }));
  }

  async function saveReview() {
    const disputedWithoutNote = findings.some((finding) => {
      const edit = findingEdits[text(finding.finding_key, "")];
      return edit?.status === "DISPUTED" && !edit.note.trim();
    });
    if (disputedWithoutNote) {
      setError("A disputed finding requires a note.");
      return;
    }
    if (!window.confirm(`Append Trade Retro review revision v${reviewVersion + 1}? The original run remains immutable.`)) return;
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
          authorization_note: "User saved this Trade Retro review in the local Console.",
          idempotency_key: `console-retro-review-${runId}-${reviewVersion + 1}-${Date.now()}`,
        } },
        confirmation: "research_workflow_run",
      });
      const envelope = asDict(response.result);
      if (envelope.ok !== true) {
        const firstError = listOf<Dict>(envelope, "errors")[0];
        throw new Error(text(firstError?.message, "Trade Retro review was rejected"));
      }
      setEditing(false);
      onReviewed();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to save Trade Retro review");
    } finally {
      setSaving(false);
    }
  }

  return <article className="retro-run-card">
    <header>
      <div>
        <strong>{text(run.period_start).slice(0, 10)} → {text(run.period_end).slice(0, 10)}</strong>
        <small className="mono">{runId}</small>
      </div>
      <div className="retro-badges"><Badge value={text(run.status)} />{reviewVersion > 0 ? <Badge value={text(latestReview.status)} /> : <Badge value="UNREVIEWED" />}</div>
    </header>
    <p className="retro-summary-lead">{text(run.summary_markdown, "").split("\n").find((line) => line.trim())}</p>
    <small>{findings.length} finding(s) · generated {formatDate(run.generated_at)} · review v{reviewVersion} · execution_effect=false</small>
    <div className="page-actions">
      <ActionButton onClick={() => setEditing((value) => !value)}>{editing ? "Close editor" : reviewVersion ? "Edit review" : "Review"}</ActionButton>
      <ActionButton busy={busy === `export${runId}`} onClick={() => { void onExport(runId, reviewVersion); }}>Export to Obsidian</ActionButton>
    </div>

    <details className="retro-details">
      <summary>Full immutable run · findings · transaction references</summary>
      <pre>{text(run.summary_markdown, "")}</pre>
      {listOf<string>(run, "warning_codes").length > 0 && <p className="retro-code-list"><strong>Warnings</strong>{listOf<string>(run, "warning_codes").map((item) => <code key={item}>{item}</code>)}</p>}
      {findings.length === 0 ? <Empty>No deterministic findings.</Empty> : <div className="retro-findings">{findings.map((finding) => <article key={text(finding.finding_key)}>
        <header><strong>{text(finding.code)} · {text(finding.title)}</strong><Badge value={text(finding.severity)} /></header>
        <p>{text(finding.detail)}</p>
        <small>{text(finding.instrument_id)} · {listOf<string>(finding, "transaction_ids").length} transaction reference(s)</small>
      </article>)}</div>}
      {listOf<string>(run, "transaction_ids").length > 0 && <p className="retro-code-list"><strong>Transaction IDs</strong>{listOf<string>(run, "transaction_ids").map((item) => <code key={item}>{item}</code>)}</p>}
    </details>

    {editing && <section className="retro-editor">
      <div className="retro-editor-heading"><div><span>APPEND-ONLY HUMAN REVIEW</span><strong>Review revision v{reviewVersion + 1}</strong></div><small>The generated run is never overwritten.</small></div>
      <div className="retro-editor-grid">
        <label><span>Review status</span><select value={status} onChange={(event) => setStatus(event.target.value)}>{["OPEN", "ACCEPTED", "DISPUTED", "RESOLVED"].map((item) => <option key={item}>{item}</option>)}</select></label>
        <label className="retro-wide"><span>Correction / review note</span><textarea rows={6} value={note} onChange={(event) => setNote(event.target.value)} placeholder="Record what you accept, dispute, or want to correct. This does not rewrite the generated summary." /></label>
        <label className="retro-wide"><span>Action items · one per line</span><textarea rows={4} value={actions} onChange={(event) => setActions(event.target.value)} placeholder="Review the next entry against the confirmed Trade Plan" /></label>
      </div>
      {findings.length > 0 && <div className="retro-finding-editor"><h3>Finding dispositions</h3>{findings.map((finding) => {
        const findingKey = text(finding.finding_key, "");
        const edit = findingEdits[findingKey] ?? { status: "", note: "" };
        return <div key={findingKey} className="retro-finding-edit-row">
          <div><strong>{text(finding.code)}</strong><small>{text(finding.title)}</small></div>
          <select value={edit.status} onChange={(event) => setFinding(findingKey, { status: event.target.value })}><option value="">UNREVIEWED</option>{["ACCEPTED", "DISPUTED", "RESOLVED"].map((item) => <option key={item}>{item}</option>)}</select>
          <input value={edit.note} onChange={(event) => setFinding(findingKey, { note: event.target.value })} placeholder={edit.status === "DISPUTED" ? "Reason required" : "Optional note"} />
        </div>;
      })}</div>}
      {error && <div className="inline-error">{error}</div>}
      <div className="retro-editor-actions"><ActionButton busy={saving} onClick={() => { void saveReview(); }}>Confirm append revision</ActionButton><small>Uses expected_version={reviewVersion}; stale tabs are rejected.</small></div>
    </section>}

    {reviewHistory.length > 0 && <details className="retro-review-history"><summary>Review history · {reviewHistory.length} immutable revision(s)</summary><div>{reviewHistory.map((review) => <article key={text(review.review_id)}><header><strong>v{text(review.version)} · {text(review.status)}</strong><small>{formatDate(review.created_at)} · {text(review.reviewed_by)}</small></header>{text(review.note_markdown, "") && <p>{text(review.note_markdown, "")}</p>}{listOf<string>(review, "action_items").length > 0 && <ul>{listOf<string>(review, "action_items").map((item) => <li key={item}>{item}</li>)}</ul>}</article>)}</div></details>}
  </article>;
}

export default function TradeRetroPage() {
  const api = useApi<Dict>("/api/retro");
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const runs = listOf<Dict>(asDict(api.data?.data), "runs");
  const windows = asDict(api.data?.console_windows);
  const previousWindow = asDict(windows.previous);
  const nextWindow = asDict(windows.next);

  async function invoke(action: "prepare" | "run" | "export", runId?: string, reviewVersion = 0) {
    setBusy(action + (runId ?? ""));
    setMessage(null);
    try {
      const request: Dict = {
        operation: "trade_retro",
        action,
        idempotency_key: action === "export"
          ? `console-retro-export-${runId}-review-${reviewVersion}`
          : `console-retro-${action}-${text((action === "prepare" ? nextWindow : previousWindow).start, "missing").slice(0, 10)}`,
      };
      if (action === "prepare") Object.assign(request, nextWindow);
      if (action === "run") Object.assign(request, previousWindow, { use_llm: true });
      if (action === "export") request.run_id = runId;
      const result = await postApi<Dict>("/api/tools/invoke", {
        tool_name: "research_workflow_run",
        arguments: { request },
        confirmation: "research_workflow_run",
      });
      const envelope = asDict(result.result);
      setMessage(envelope.ok === true ? `${action} completed` : "Trade Retro returned a degraded result");
      api.refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Trade Retro failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <ConsoleShell active="retro" eyebrow="DURABLE DISCIPLINE REVIEW" title="Trade Retro">
      <DataBoundary loading={api.loading} error={api.error}>
        <div className="page-actions">
          <RefreshButton loading={api.loading} onClick={api.refresh} />
          <ActionButton disabled={!nextWindow.start} busy={busy === "prepare"} onClick={() => { void invoke("prepare"); }}>Prepare next week</ActionButton>
          <ActionButton disabled={!previousWindow.start} busy={busy === "run"} onClick={() => { void invoke("run"); }}>Run previous week</ActionButton>
        </div>
        {message ? <p className="card-note">{message}</p> : null}
        <Card kicker="SOURCE OF TRUTH" title="Transaction-versus-plan discipline">
          <p className="card-note">Runs and findings are immutable. Human corrections, dispositions, and action items are editable through append-only review revisions with optimistic version checks; none changes research state, positions, or orders.</p>
        </Card>
        <Card kicker="IMMUTABLE RUNS · EDITABLE REVIEWS" title="Retro history">
          {runs.length === 0 ? <Empty>No Trade Retro run has been persisted yet.</Empty> : <div className="retro-run-list">{runs.map((run) => <RetroRunCard key={text(run.run_id)} run={run} busy={busy} onExport={async (runId, version) => invoke("export", runId, version)} onReviewed={api.refresh} />)}</div>}
        </Card>
      </DataBoundary>
    </ConsoleShell>
  );
}
