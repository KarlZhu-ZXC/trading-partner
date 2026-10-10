"use client";

import { useState } from "react";
import { Badge, Button, Card, DescriptionList, Disclosure, Empty, ErrorNote, FilterBar, FormField, Select, formatDate } from "../components/ui";
import { useApi } from "../lib/api";
import { asObject, textDash as text } from "../lib/coerce";
import type { SharedEvidenceLinksRead } from "./evidence-impact-panel";

type Dict = Record<string, unknown>;
const rows = (value: unknown): Dict[] => Array.isArray(value) ? value.map(asObject) : [];
function exactSource(value: unknown) {
  const source = asObject(value);
  return [source.kind, source.source_id, source.monitor_id, source.rule_code].filter(Boolean).join(" · ");
}

export function DecisionReplayPanel({ subjectId, linksRead }: { subjectId: string; linksRead: SharedEvidenceLinksRead }) {
  const [selected, setSelected] = useState("");
  const [requested, setRequested] = useState("");
  const choices = rows(asObject(asObject(linksRead.data).data).decisions);
  const request = useApi<{ data: Dict }>(`/api/research/${encodeURIComponent(subjectId)}/decisions/${encodeURIComponent(requested)}/replay`, { enabled: Boolean(requested) });
  const result = request.data?.data;
  // A previous response must never masquerade as a newly selected Decision.
  const replay = result?.subject_id === subjectId && result?.decision_id === requested && requested === selected ? result : null;
  const coverage = asObject(replay?.coverage);
  const incomplete = Object.values(coverage).some((status) => status !== "COMPLETE" && status !== "NOT_APPLICABLE");
  return <Card id="research-decision-replay" className="research-context-card" kicker="HISTORICAL CONTEXT" title="Decision Replay" description="Inspect records that were available by the Decision time. Later evidence and answers stay outside this view.">
    <FilterBar aria-label="Decision Replay Controls">
      <FormField label="Reviewed Decision" required><Select required value={selected} disabled={linksRead.loading} onChange={(event) => { setSelected(event.target.value); setRequested(""); }}>
        <option value="">Choose a Decision</option>
        {choices.map((decision) => <option key={text(decision.decision_id)} value={text(decision.decision_id)}>{text(decision.title)} · {formatDate(decision.decided_at)}</option>)}
      </Select></FormField>
      <Button disabled={!selected || request.loading && Boolean(requested)} onClick={() => { if (selected === requested) request.refresh(); else setRequested(selected); }}>{request.loading && requested ? "Loading Replay…" : "Load Replay"}</Button>
    </FilterBar>
    {linksRead.error && <ErrorNote>Decision choices could not be refreshed. {linksRead.error}</ErrorNote>}
    {asObject(asObject(asObject(linksRead.data).data).coverage).decisions === "PARTIAL" && <p role="status">Only the available Decision choices are listed.</p>}
    {!linksRead.loading && !linksRead.error && choices.length === 0 && <Empty>No reviewed Decisions are available for this Research Subject.</Empty>}
    {requested && request.error && <ErrorNote>{request.error}</ErrorNote>}
    {requested && result && !replay && !request.loading && <ErrorNote>The replay response did not match the selected Decision and Research Subject.</ErrorNote>}
    {requested && request.loading && <p role="status">Reading the selected historical scope…</p>}
    {replay && <>
      {request.error && <p role="status">Showing the previous replay result; its refresh failed.</p>}
      <DescriptionList columns={3} items={[{ label: "Decision", value: text(replay.title) }, { label: "Decision Cutoff", value: text(replay.cutoff) }, { label: "Recorded Later At", value: text(replay.recorded_at) }]} />
      <p>The cutoff uses the Decision event time, including for backdated records. The saved Decision rationale is shown as record context.</p>
      <Disclosure title="Decision Record"><p>{text(replay.rationale)}</p><small>{text(replay.decision_id)}</small></Disclosure>
      {incomplete && <p role="status">Historical coverage is incomplete. Missing history does not mean there was no evidence or no unresolved question.</p>}
      <section aria-label="Historical Assumptions"><h3>Thesis Revisions & Assumptions</h3>
        {rows(replay.revisions).map((revision) => <article key={text(revision.revision_id)} className="research-selection-item"><div>
          <strong>{text(revision.statement)}</strong><small>Revision {text(revision.revision_id)} · Confirmed {text(revision.confirmed_at)}</small>
          {rows(revision.assumptions).map((assumption) => <div key={text(assumption.assumption_id)}><p>{text(assumption.statement)}</p>{assumption.status == null ? <p>Historical status unavailable</p> : <Badge value={text(assumption.status)} />}<small>{text(assumption.assumption_id)}</small></div>)}
        </div></article>)}
        {rows(replay.revisions).length === 0 && <Empty>No pinned Thesis revisions are available at this cutoff. Check coverage below.</Empty>}
      </section>
      <section aria-label="Historical Sources"><h3>Sources Available Then</h3>
        {rows(replay.sources).map((source) => <Disclosure key={exactSource(source.source)} variant="compact" title={text(source.title)}><DescriptionList items={[{ label: "Exact Source", value: exactSource(source.source) }, { label: "Origin", value: text(source.origin) }, { label: "Fact Time", value: text(source.fact_at) }, { label: "Recorded At", value: text(source.recorded_at) }]} /></Disclosure>)}
        {rows(replay.sources).length === 0 && <Empty>No source records are available in this historical slice. Check coverage below.</Empty>}
      </section>
      <section aria-label="Historical Evidence Links"><h3>Reviewed Links Available Then</h3>
        {rows(replay.impacts).map((impact) => <article key={text(impact.impact_id)} className="research-selection-item"><div><p><Badge value={text(impact.stance).toUpperCase()} /> {text(impact.rationale)}</p><small>{exactSource(impact.source)} · Assumption {text(impact.assumption_id)} · v{text(impact.version)}</small>{impact.source_available === false && <p>Original source unavailable; the reviewed link is retained.</p>}</div></article>)}
        {rows(replay.impacts).length === 0 && <p>No available links in this slice.</p>}
      </section>
      <section aria-label="Questions Existing Then"><h3>Questions Existing Then</h3>
        <p>Question text and creation time are available. Historical answers, progress and status are not reconstructed from today’s mutable records.</p>
        {rows(replay.questions).map((question) => <article key={text(question.question_id)} className="research-selection-item"><div><p>{text(question.text)}</p><small>Asked {text(question.asked_at)} · {text(question.question_id)}</small><p>Historical state unavailable</p></div></article>)}
      </section>
      <Disclosure title="Replay Coverage & Exclusions">
        <DescriptionList items={Object.entries(coverage).map(([label, value]) => ({ label, value: text(value) }))} />
        <DescriptionList items={rows(replay.excluded_references).map((item) => ({label: text(item.reference_id), value: text(item.reason)}))} />
        {Array.isArray(replay.warnings) && replay.warnings.length > 0 && <p>{replay.warnings.map((warning) => text(warning)).join(" · ")}</p>}
      </Disclosure>
    </>}
  </Card>;
}
