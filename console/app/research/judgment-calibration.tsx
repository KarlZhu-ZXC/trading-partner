"use client";

import { useEffect, useState } from "react";
import { Badge, Button, Card, DescriptionList, Disclosure, Empty, ErrorNote, LinkButton, formatDate } from "../components/ui";
import { getJson } from "../lib/api";

type SourceRef = { kind: string; entity_id: string; version: number | null };
type Observation = { source_id: string; observed_at: string; occurred_at?: string | null; title: string; summary: string; status: string; source_refs: SourceRef[]; limitation_codes: string[] };
type ScorecardCard = { warning_codes: string[]; scorecard_id: string; thesis_revision_id: string; generated_at: string; dimension: { code: string; title: string; summary: string; status: string; result_code: string; facts: [string, string][]; source_refs: SourceRef[]; limitation_codes: string[] } };
type Calibration = {
  subject_id: string; as_of: string; review_status: string;
  baseline: null | { decision_id: string; decision_type: string; title: string; recorded_at: string; thesis_revision_ids: string[]; trade_plan_id: string | null; trade_plan_version: number | null; review_due_at: string | null };
  dimensions: { code: string; title: string; status: string; summary: string; cards: ScorecardCard[]; observations: Observation[]; limitation_codes: string[] }[];
  coverage: Record<string, string>; warning_codes: string[];
};

function provenance(refs: SourceRef[]) {
  return refs.map((ref) => `${ref.kind}: ${ref.entity_id}${ref.version == null ? "" : ` v${ref.version}`}`).join(" · ");
}

export function JudgmentCalibration({ subjectId, decisionId }: { subjectId: string; decisionId?: string }) {
  const [data, setData] = useState<Calibration | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setData(null); setLoading(true); setError(false);
    const query = new URLSearchParams();
    if (decisionId) query.set("decision_id", decisionId);
    void getJson(`/api/research/${encodeURIComponent(subjectId)}/calibration?${query}`, controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return;
        const next = (response as { data?: Calibration }).data;
        if (!next || next.subject_id !== subjectId || !Array.isArray(next.dimensions)) throw new Error("Invalid calibration");
        setData(next);
      })
      .catch(() => { if (!controller.signal.aborted) setError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [subjectId, decisionId, nonce]);
  const current = data?.subject_id === subjectId && (!decisionId || data.baseline?.decision_id === decisionId) ? data : null;
  return <Card kicker="REVIEWED JUDGMENT" title="Judgment Calibration" action={<Button variant="secondary" onClick={() => setNonce((value) => value + 1)} disabled={loading}>Refresh Calibration</Button>}>
    <p>Review four independent dimensions against exact reviewed Decision versions. Unknown outcomes, future reviews and no trades do not imply failure.</p>
    {loading && <p role="status">Reading saved calibration records…</p>}
    {error && <ErrorNote>Calibration records are unavailable. Retry to restore coverage; this does not mean there are no outcomes.</ErrorNote>}
    {current && <>
      {!current.baseline ? <Empty>No user-reviewed Decision yet. Return after a judgment review, including NO_ACTION.</Empty> : <DescriptionList columns={2} items={[
        { label: "Reviewed Decision", value: `${current.baseline.title} · ${current.baseline.decision_type}`, detail: current.baseline.decision_id },
        { label: "Review Window", value: <Badge value={current.review_status} />, detail: current.baseline.review_due_at ? formatDate(current.baseline.review_due_at) : "No review date set" },
        { label: "Exact Thesis Revisions", value: current.baseline.thesis_revision_ids.join(" · ") || "No Thesis revision referenced" },
        { label: "Exact Trade Plan", value: current.baseline.trade_plan_id ? `${current.baseline.trade_plan_id} v${current.baseline.trade_plan_version}` : "No Trade Plan referenced" },
      ]} />}
      <p>As of {formatDate(current.as_of)} · {Object.entries(current.coverage).map(([key, value]) => `${key}: ${value}`).join(" · ")}</p>
      {current.warning_codes.length > 0 && <p>{current.warning_codes.join(" · ")}</p>}
      {current.warning_codes.includes("NO_MATCHING_SCORECARD") && <LinkButton href={`/scorecards?subject_id=${encodeURIComponent(subjectId)}`}>Open Scorecards</LinkButton>}
      {current.dimensions.map((dimension) => <Disclosure key={dimension.code} title={dimension.title}>
        <Badge value={dimension.status} /><p>{dimension.summary}</p>
        {dimension.limitation_codes.length > 0 && <p>{dimension.limitation_codes.join(" · ")}</p>}
        {dimension.cards.map((card) => <article key={`${card.scorecard_id}-${card.dimension.code}`}>
          <h4>{card.dimension.title}</h4><Badge value={card.dimension.status} />
          <p>{card.dimension.summary}</p><p>{card.dimension.result_code}</p>
          <DescriptionList columns={2} items={card.dimension.facts.map(([label, value]) => ({ label, value }))} />
          <p>{formatDate(card.generated_at)} · {card.thesis_revision_id}</p>
          <p>{provenance(card.dimension.source_refs)}</p><p>{[...card.warning_codes, ...card.dimension.limitation_codes].join(" · ")}</p>
          <LinkButton href={`/scorecards?subject_id=${encodeURIComponent(subjectId)}#scorecard-${encodeURIComponent(card.scorecard_id)}`}>Open Source Scorecard</LinkButton>
        </article>)}
        {dimension.observations.map((observation, index) => <article key={`${observation.source_id}-${index}`}>
          <h4>{observation.title}</h4><Badge value={observation.status} /><p>{observation.summary}</p>
          <p>Recorded {formatDate(observation.observed_at)} · Occurred {observation.occurred_at ? formatDate(observation.occurred_at) : "Unknown"} · {provenance(observation.source_refs)}</p>
          <p>{observation.limitation_codes.join(" · ")}</p>
        </article>)}
      </Disclosure>)}
    </>}
  </Card>;
}
