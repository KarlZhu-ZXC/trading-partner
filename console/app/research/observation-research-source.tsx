"use client";

import type { ObservationResearchSeed } from "../lib/observation-research-draft";
import { Badge, Card, Disclosure, ErrorNote, formatDate, shortId } from "../components/ui";
import { Button } from "../components/ui/controls";

type ObservationResearchSourceProps = {
  seed: ObservationResearchSeed | null;
  subjectInstrumentId: string;
  thesisEditorOpen: boolean;
  planEditorOpen: boolean;
  actionsDisabled?: boolean;
  onUseThesis: () => void;
  onUsePlan: () => void;
};

function text(value: unknown, fallback = "—"): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

export function ObservationResearchSource({
  seed,
  subjectInstrumentId,
  thesisEditorOpen,
  planEditorOpen,
  actionsDisabled = false,
  onUseThesis,
  onUsePlan,
}: ObservationResearchSourceProps) {
  if (!seed) return null;
  const sourceInstrumentId = text(seed.source.instrument_id, "");
  const instrumentMatches = Boolean(sourceInstrumentId && subjectInstrumentId && sourceInstrumentId === subjectInstrumentId);
  const canImport = Boolean(seed.ready && instrumentMatches && !actionsDisabled);
  const fullReviewText = "fullReviewText" in seed && typeof seed.fullReviewText === "string" ? seed.fullReviewText : "";
  return (
    <Card className="research-observation-source" kicker="REVIEW SOURCE" title="Observation Review Carryover">
      <div className="research-observation-source-meta">
        <div><span>Source Note</span><strong>{text(seed.source.title, "Untitled Note")}</strong><small>{shortId(seed.source.note_revision_id)} · v{seed.source.note_version} · {formatDate(seed.source.observed_at)}</small></div>
        <div><span>Analysis</span><strong>{text(seed.source.analysis_kind, "Unavailable")}</strong><small>{text(seed.source.model, "Model unavailable")} · {text(seed.source.provider, "Provider unavailable")}</small></div>
        <Badge value={instrumentMatches ? "INSTRUMENT MATCH" : "INSTRUMENT CHECK REQUIRED"} tone={instrumentMatches ? "good" : "warn"} />
      </div>
      {!instrumentMatches ? <ErrorNote>Imported fields are disabled until the Research Subject primary Instrument exactly matches {sourceInstrumentId || "the source Instrument"}.</ErrorNote> : null}
      {!seed.ready ? <ErrorNote>Parsed review content is unavailable. Only source metadata is shown; write your own Thesis and Plan text.</ErrorNote> : null}
      {seed.ready ? <>
        <section className="research-observation-source-section">
          <header><strong>USER Thesis Preview</strong><small>Editable seed; no candidate is submitted automatically.</small></header>
          <p>{text(seed.thesis.statement, "No USER Thesis statement was parsed.")}</p>
        </section>
        {seed.keyLevels.length > 0 ? <section className="research-observation-source-section"><header><strong>Key Level References</strong><small>Verify speaker, date, and role before use.</small></header><ul>{seed.keyLevels.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></section> : null}
        {seed.otherViewpoints.length > 0 ? <section className="research-observation-source-section"><header><strong>Other Speaker References</strong><small>Context only; never copied into the USER Thesis statement.</small></header>{seed.otherViewpoints.map((item, index) => <p key={`${item.speaker}-${index}`}><strong>{item.speaker}</strong> · {item.summary}</p>)}</section> : null}
        {fullReviewText ? <Disclosure className="research-observation-source-details" title="Parsed Review Details" variant="compact"><pre>{fullReviewText}</pre></Disclosure> : null}
      </> : null}
      {seed.warnings.length > 0 ? <div className="research-observation-source-warnings"><strong>Source Warnings</strong>{seed.warnings.map((warning, index) => <p key={`${warning}-${index}`}>{warning}</p>)}</div> : null}
      <div className="research-observation-source-actions">
        <Button type="button" disabled={!canImport || thesisEditorOpen} onClick={onUseThesis}>{thesisEditorOpen ? "Thesis Editor Open" : "Use Review in Thesis"}</Button>
        <Button type="button" disabled={!canImport || planEditorOpen} onClick={onUsePlan}>{planEditorOpen ? "Plan Editor Open" : "Use Review in Plan Draft"}</Button>
      </div>
    </Card>
  );
}
