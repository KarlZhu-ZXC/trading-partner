"use client";

import { useEffect, useRef, useState } from "react";
import { Badge, Button, Card, DescriptionList, Empty, ErrorNote, LinkButton, SelectableRow, formatDate } from "../components/ui";
import { getJson } from "../lib/api";
import styles from "./research-changes.module.css";

type Change = {
  change_id: string; kind: string; title: string; occurred_at: string; recorded_at: string;
  instrument_id: string | null; source_id: string; source_version: string | number | null;
  old_value: unknown; new_value: unknown; relation: string; relation_detail: string;
  thesis_id: string | null; plan_id: string | null; plan_version: number | null;
  note_revision_id: string | null; monitor_id: string | null; event_id: string | null;
  agenda_item_id: string | null; warning_codes: string[];
  previous_occurred_at?: string | null; previous_source_id?: string | null; source_names?: string[];
};
type Changes = {
  subject_id: string; as_of: string;
  baseline: null | { decision_id: string; title: string; rationale?: string; external_note_revision_id?: string | null; recorded_at: string; decided_at: string;
    theses: { thesis_id: string; revision_id: string; statement: string; assumptions?: { assumption_id: string; statement: string }[]; invalidations?: { invalidation_id: string; description: string }[] }[];
    plan: { plan_id: string; version: number } | null };
  coverage: Record<string, string>; warning_codes: string[];
  total: number; offset: number; limit: number; has_more: boolean; items: Change[];
};

function updateLocation(baseline: string | null, change: string | null, offset?: number) {
  const url = new URL(window.location.href);
  if (baseline) url.searchParams.set("changes_baseline", baseline); else url.searchParams.delete("changes_baseline");
  if (change) url.searchParams.set("change_id", change); else url.searchParams.delete("change_id");
  if (offset !== undefined) {
    if (offset > 0) url.searchParams.set("changes_offset", String(offset)); else url.searchParams.delete("changes_offset");
  }
  window.history.replaceState(window.history.state, "", url);
}

function readable(value: unknown): string {
  if (value === null || value === undefined) return "Not available";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

export function ResearchChanges({ subjectId }: { subjectId: string }) {
  const [data, setData] = useState<Changes | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selection, setSelection] = useState<string | null>(null);
  const [request, setRequest] = useState({ offset: 0, nonce: 0, latest: false });
  const owner = useRef(subjectId);
  const visibleSubject = useRef(subjectId);
  visibleSubject.current = subjectId;
  const baseline = useRef<string | null>(null);
  const initialized = useRef(false);
  const initialOffset = useRef(0);
  const generation = useRef(0);

  useEffect(() => {
    const changed = owner.current !== subjectId;
    owner.current = subjectId;
    if (changed) {
      baseline.current = null;
      initialOffset.current = 0;
      setSelection(null);
      updateLocation(null, null, 0);
      setData(null);
    } else if (!initialized.current) {
      const params = new URLSearchParams(window.location.search);
      baseline.current = params.get("changes_baseline");
      const value = Number(params.get("changes_offset") ?? "0");
      initialOffset.current = Number.isSafeInteger(value) && value >= 0 ? value : 0;
      setSelection(params.get("change_id"));
    }
    initialized.current = true;
    if (request.latest) baseline.current = null;
    const controller = new AbortController();
    const run = ++generation.current;
    const offset = changed ? 0 : request.nonce === 0 ? initialOffset.current : request.offset;
    const params = new URLSearchParams({ offset: String(offset), limit: "25" });
    if (baseline.current) params.set("baseline_decision_id", baseline.current);
    const requestedChange = new URLSearchParams(window.location.search).get("change_id");
    if (requestedChange && !changed && !request.latest) params.set("change_id", requestedChange);
    setLoading(true);
    setError(null);
    void getJson(`/api/research/${encodeURIComponent(subjectId)}/changes?${params}`, controller.signal)
      .then((response) => {
        if (run !== generation.current || controller.signal.aborted || visibleSubject.current !== subjectId) return;
        const next = (response as { data?: Changes }).data;
        if (!next || next.subject_id !== subjectId || !Array.isArray(next.items)) throw new Error("Changes could not be read for this Research Subject.");
        baseline.current = next.baseline?.decision_id ?? "none";
        setData(next);
        const currentSelection = new URLSearchParams(window.location.search).get("change_id");
        setSelection(currentSelection);
        updateLocation(baseline.current, currentSelection, next.offset);
      })
      .catch(() => { if (run === generation.current && !controller.signal.aborted && visibleSubject.current === subjectId) setError("Changes could not be read. Retry to restore coverage; this does not mean there are no changes."); })
      .finally(() => { if (run === generation.current && !controller.signal.aborted && visibleSubject.current === subjectId) setLoading(false); });
    return () => controller.abort();
  }, [subjectId, request]);

  const current = data?.subject_id === subjectId ? data : null;
  const selected = current?.items.find((item) => item.change_id === selection);
  const incomplete = current && Object.values(current.coverage).some((status) => !["COMPLETE", "NOT_APPLICABLE"].includes(status));
  const reviewParams = new URLSearchParams({ subject_id: subjectId });
  if (selected) reviewParams.set("change_id", selected.change_id);
  if (current) reviewParams.set("changes_baseline", current.baseline?.decision_id ?? "none");
  if (current && current.offset > 0) reviewParams.set("changes_offset", String(current.offset));
  if (selected?.note_revision_id) reviewParams.set("note_revision_id", selected.note_revision_id);
  const refresh = () => setRequest({ offset: current?.offset ?? 0, nonce: request.nonce + 1, latest: false });

  return <Card id="research-changes" kicker="REVIEW CONTEXT" title="Changes Since Review" action={<div className={styles.actions}><Button disabled={loading} onClick={refresh}>Refresh Changes</Button><Button disabled={loading} onClick={() => { updateLocation(null, null, 0); setSelection(null); setRequest({ offset: 0, nonce: request.nonce + 1, latest: true }); }}>Use Latest Review</Button></div>}>
    {loading && <p role="status">Loading changes…</p>}
    {error && <ErrorNote>{error}</ErrorNote>}
    {current && !loading && <>
      {error && <p role="status">Previous result shown from {formatDate(current.as_of)}. Coverage below belongs to that saved read and has not been refreshed.</p>}
      <DescriptionList columns={3} items={Object.entries(current.coverage).map(([source, status]) => ({ label: `${source.replaceAll("_", " ")} Coverage`, value: <Badge value={status} /> }))} />
      {incomplete && <p role="status">Coverage is incomplete. Available changes are shown below; unavailable sources may contain additional changes. Refresh Changes retries these reads.</p>}
      {current.warning_codes.length > 0 && <p role="status">{current.warning_codes.join(" · ")}</p>}
      <div className={styles.columns}>
        <section aria-label="Pinned Review" className={styles.baseline}>
          <h3>Pinned Review</h3>
          {current.baseline ? <><strong>{current.baseline.title}</strong>{current.baseline.rationale ? <p>{current.baseline.rationale}</p> : null}{current.baseline.external_note_revision_id ? <p>Reviewed Observation <code>{current.baseline.external_note_revision_id}</code></p> : null}<p>Decision time {formatDate(current.baseline.decided_at)} · Review recorded {formatDate(current.baseline.recorded_at)}</p><code>{current.baseline.decision_id}</code>{current.baseline.theses.map((thesis) => <article key={thesis.revision_id}><p>{thesis.statement}</p><small>Thesis {thesis.thesis_id} · revision {thesis.revision_id}</small>{(thesis.assumptions ?? []).length > 0 && <><h4>Assumptions</h4><ul>{thesis.assumptions?.map((item) => <li key={item.assumption_id}>{item.statement}</li>)}</ul></>}{(thesis.invalidations ?? []).length > 0 && <><h4>Invalidation Conditions</h4><ul>{thesis.invalidations?.map((item) => <li key={item.invalidation_id}>{item.description}</li>)}</ul></>}</article>)}{current.baseline.theses.length === 0 && <p>No exact Thesis revision was available at this review.</p>}{current.baseline.plan && <p>Trade Plan {current.baseline.plan.plan_id} · v{current.baseline.plan.version}</p>}</> : <Empty>No completed user review. Changes have no reviewed baseline yet.</Empty>}
        </section>
        <section aria-label="Changes" className={styles.list}>
          <h3>Recorded Changes · {current.total}</h3>
          {current.items.length === 0 ? <Empty>{incomplete ? "No changes could be shown from the available sources." : "No recorded changes in this review window."}</Empty> : current.items.map((item) => <SelectableRow key={item.change_id} selected={selection === item.change_id} aria-pressed={selection === item.change_id} className={styles.row} onClick={() => { setSelection(item.change_id); updateLocation(baseline.current, item.change_id); }}><strong>{item.title}</strong><span>{item.kind} · {formatDate(item.occurred_at)}</span><small>{item.relation.replaceAll("_", " ")}</small></SelectableRow>)}
          <div className={styles.actions}><Button disabled={current.offset === 0} onClick={() => { setSelection(null); updateLocation(baseline.current, null); setRequest({ offset: Math.max(0, current.offset - current.limit), nonce: request.nonce + 1, latest: false }); }}>Previous Changes</Button><span>{current.total === 0 ? "0" : `${current.offset + 1}–${current.offset + current.items.length}`} of {current.total}</span><Button disabled={!current.has_more} onClick={() => { setSelection(null); updateLocation(baseline.current, null); setRequest({ offset: current.offset + current.limit, nonce: request.nonce + 1, latest: false }); }}>Next Changes</Button></div>
        </section>
      </div>
      {selection && !selected && <p role="status">The selected change is outside this page or no longer available in this window. Browse the pages or select another change.</p>}
      {selected && <section className={styles.detail} aria-label="Selected Change">
        <h3>{selected.title}</h3><p>{selected.relation_detail}</p>
        <div className={styles.columns}><div><h4>Previous Value</h4><small>{selected.previous_occurred_at ? formatDate(selected.previous_occurred_at) : "Previous fact time unavailable"} · {selected.previous_source_id ?? "Previous source unavailable"}</small><pre>{readable(selected.old_value)}</pre></div><div><h4>New Value</h4><small>{formatDate(selected.occurred_at)} · {(selected.source_names ?? []).join(", ") || "Source provenance unavailable"}</small><pre>{readable(selected.new_value)}</pre></div></div>
        <DescriptionList columns={3} items={[{ label: "Source", value: `${selected.kind} · ${selected.source_id}${selected.source_version === null ? "" : ` · v${selected.source_version}`}` }, { label: "Occurred", value: formatDate(selected.occurred_at) }, { label: "Recorded", value: formatDate(selected.recorded_at) }, { label: "Instrument", value: selected.instrument_id ?? "Unlinked" }, { label: "Thesis", value: selected.thesis_id ?? "No exact Thesis link" }, { label: "Trade Plan", value: selected.plan_id ? `${selected.plan_id} · v${selected.plan_version}` : "No exact Plan link" }]} />
        {selected.warning_codes.length > 0 && <p role="status">{selected.warning_codes.join(" · ")}</p>}
        <div className={styles.actions}><LinkButton href={`/decision-workbench?${reviewParams}#${selected.note_revision_id ? "notes" : "overview"}`}>Review This Change</LinkButton>{selected.monitor_id && <LinkButton href={`/monitors#monitor-${encodeURIComponent(selected.monitor_id)}`}>View Monitor</LinkButton>}{selected.agenda_item_id && <LinkButton href={`/agenda?agenda_item_id=${encodeURIComponent(selected.agenda_item_id)}`}>View Agenda Item</LinkButton>}</div>
        <p>Review opens the existing Journal workflow. Selecting a change does not adopt a draft or confirm a judgment.</p>
      </section>}
      <p className="muted">Saved Monitor observations and transitions; no live Provider refresh.</p>
      <p className="muted">Durable records as of {formatDate(current.as_of)}. Refresh keeps this exact review baseline.</p>
    </>}
  </Card>;
}
