"use client";

import { useState } from "react";
import { ActionButton, Badge, DescriptionList, ErrorNote, LinkButton, formatDate } from "../components/ui";
import { getJson } from "../lib/api";

type Context = {
  note_revision_id: string;
  note_version: number;
  observed_at: string;
  subject_id: string | null;
  thesis: { revision_id: string; statement: string; title: string } | null;
  trade_plan: { plan_id: string; version: number; status: string } | null;
  latest_decision: { decision_id: string; title: string; rationale: string; decided_at: string } | null;
  review: { status: string; decision_id?: string | null };
  coverage: Record<string, string>;
  deep_review: { status: string; error_code?: string | null } | null;
};

/** Deliberately current context: historical note text is not a historical portfolio snapshot. */
export function ObservationContext({ revisionId }: { revisionId: string }) {
  const [value, setValue] = useState<Context | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function load() {
    setBusy(true);
    setError(null);
    try {
      const response = await getJson(`/api/observations/${encodeURIComponent(revisionId)}/review`) as { data: Context };
      setValue(response.data);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Review context unavailable"); }
    finally { setBusy(false); }
  }
  return <section className="notes-section" aria-label="Observation Review Context">
    <h4>Source &amp; Confirmed Context</h4>
    <p>Compare this exact observation with current durable judgment. This is not a reconstruction of what was known on the observation date.</p>
    <ActionButton busy={busy} onClick={() => void load()}>{value ? "Reload Context" : "Load Review Context"}</ActionButton>
    <ErrorNote>{error}</ErrorNote>
    {value ? <>
      <DescriptionList columns={2} items={[
        { label: "Observation Revision", value: value.note_revision_id, detail: `v${value.note_version} · observed ${formatDate(value.observed_at)}` },
        { label: "Review", value: value.review.status, detail: value.review.decision_id ?? "No adopted Decision linked" },
        { label: "Current Thesis Revision", value: value.thesis?.revision_id ?? "Unavailable", detail: value.thesis?.statement },
        { label: "Current Trade Plan", value: value.trade_plan ? `${value.trade_plan.plan_id} · v${value.trade_plan.version}` : "Unavailable" },
        { label: "Comparison Decision", value: value.latest_decision?.title ?? "Unavailable", detail: value.latest_decision ? `${value.latest_decision.decision_id} · ${formatDate(value.latest_decision.decided_at)} · ${value.latest_decision.rationale}` : undefined },
        { label: "Escalated Draft", value: value.deep_review?.status ?? "Not available", detail: value.deep_review?.error_code ?? undefined },
      ]} />
      <div className="page-actions">{Object.entries(value.coverage).map(([name, status]) => <span key={name}>{name} <Badge value={status} /></span>)}</div>
      <div className="page-actions">
        {value.subject_id ? <LinkButton href={`/research#subject-${encodeURIComponent(value.subject_id)}`}>Open Research</LinkButton> : null}
        <LinkButton href="/#data-quality">Open Data Quality Center</LinkButton>
        <LinkButton href="/portfolio#activity">Open Account Coverage</LinkButton>
      </div>
    </> : null}
  </section>;
}
