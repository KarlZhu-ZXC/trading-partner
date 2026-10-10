"use client";
import { useState } from "react";
import { Badge, Button, Disclosure, Table, formatDate } from "../components/ui";
import { listOf, useApi } from "../lib/api";
import { asObject, textStringified as text } from "../lib/coerce";
import { findingChoiceChanges } from "../lib/retro-review-presentation";
type Dict = Record<string, unknown>;
export function FindingChoiceComparison({ saved, draft, findings }: { saved: Dict[]; draft: Record<string, Dict>; findings: Dict[] }) {
  const prior = new Map(saved.map(row => [text(row.finding_key), row]));
  const keys = [...new Set([...prior.keys(), ...Object.keys(draft)])];
  const changes = keys.map(key => ({ key, changes: findingChoiceChanges(prior.get(key) ?? {}, draft[key] ?? {}), title: text(findings.find(f => f.finding_key === key)?.title, key) })).filter(row => row.changes.length);
  return <Disclosure title="Compare Finding Choices">{changes.length === 0 ? <p>Finding choices are unchanged.</p> : changes.map(row => <section key={row.key}><h5>{row.title}</h5><Table><thead><tr><th>Changed Field</th><th>Saved Review</th><th>Your Draft</th></tr></thead><tbody>{row.changes.map(change => <tr key={change.field}><th>{change.field}</th><td>{change.saved}</td><td>{change.draft}</td></tr>)}</tbody></Table></section>)}</Disclosure>;
}
export function HistoricalReview({ runId, code, instrumentId }: { runId: string; code?: string; instrumentId?: string | null }) {
  const [opened, setOpened] = useState(false);
  const query = useApi<{ data: Dict }>(`/api/retro/${encodeURIComponent(runId)}/detail`, { enabled: opened });
  const run = query.data?.data;
  const scoped = run?.run_id === runId ? run : null;
  const matching = listOf<Dict>(scoped, "findings").filter(f => !code || (f.code === code && f.instrument_id === instrumentId));
  return <Disclosure title={`Inspect Historical Review · ${runId}`} onToggle={setOpened}>
    {query.loading ? <p>Reading the saved review…</p> : query.error || !scoped ? <><p>Historical review unavailable. Your current draft remains here.</p><Button onClick={query.refresh}>Retry Historical Read</Button></> : <section aria-label="Historical Review Comparison">
      <p>{formatDate(scoped.period_start)} → {formatDate(scoped.period_end)} · <Badge value={text(scoped.status)} /></p>
      <p>Generated {formatDate(scoped.generated_at)} · Pre-period snapshot: {text(scoped.plan_snapshot_id, "Unavailable")}</p>
      {matching.map(f => <div key={text(f.finding_key)}><strong>{text(f.title)}</strong><p>{text(f.detail)}</p><small>Plan: {text(f.plan_id, "Unlinked")} · Trade references: {listOf<string>(f, "transaction_ids").join(", ") || "Unavailable"}</small></div>)}
      {matching.length === 0 && <p>No matching finding is present in this saved result.</p>}
      <p>Human review: {text(asObject(scoped.latest_review).status, "Unreviewed")}</p>
      <p>{text(asObject(scoped.latest_review).note_markdown, "No saved review note.")}</p>
      <p>This is a read of historical facts. Close this section to continue at your original position; no disposition is changed.</p>
    </section>}
  </Disclosure>;
}
