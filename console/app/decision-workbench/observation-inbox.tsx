"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  ActionButton,
  Badge,
  Card,
  Disclosure,
  ErrorNote,
  formatDate,
  shortId,
} from "../components/ui";
import { getJson, listOf } from "../lib/api";

type Dict = Record<string, unknown>;
type SubjectAggregate = { subject?: Dict; state?: Dict };
type Scope = "ALL" | "CURRENT";
type AttributionDisplayBlock = { speakerLabel: string; body: string };
type AttributionDisplaySection = { dateLabel: string | null; blocks: AttributionDisplayBlock[] };

const DATE_SECTION_LINE = /^(?:(?:20\d{2})[-/.])?\d{1,2}[-/.]\d{1,2}(?=$|\s|[:：])(?:\s*[:：]?\s*)?(.*)$/;

/**
 * The page can adopt this second callback argument without changing the note
 * payload shape. A null value is intentional: the note may not map to an
 * active Research Subject yet.
 */
export type NoteReviewDecisionHandler = (
  item: Dict,
  matchingSubjectId: string | null,
) => void | Promise<void>;
export type NoteDeferReviewHandler = (
  item: Dict,
  matchingSubjectId: string | null,
) => void | Promise<void>;

export type ObservationInboxProps = {
  items: Dict[];
  sources: Dict[];
  activeSubjects: SubjectAggregate[];
  selectedInstrumentIds: string[];
  busy: boolean;
  syncMessage: string | null;
  syncError: string | null;
  onRefresh: () => void;
  onSelectSubject: (subjectId: string) => void;
  onReviewDecision: NoteReviewDecisionHandler;
  onDeferReview: NoteDeferReviewHandler;
  analysisBusyId: string | null;
  reviewBusyId: string | null;
  onAnalyzeRevision: (revisionId: string) => void;
  /** Optional host-provided portfolio context for the selected note. */
  positionContext?: ReactNode | ((instrumentId: string) => ReactNode);
  /** Optional host-provided cycle context for the selected note. */
  cyclesContext?: ReactNode | ((instrumentId: string) => ReactNode);
};

const SCENARIOS = [
  "UPSIDE",
  "SIDEWAYS",
  "PULLBACK",
  "INVALIDATION",
] as const;

function asDict(value: unknown): Dict {
  return value && typeof value === "object" ? (value as Dict) : {};
}

function text(value: unknown, fallback = "—"): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function upper(value: unknown, fallback = "UNKNOWN"): string {
  return text(value, fallback).toUpperCase();
}

function canonicalSpeakerLabel(value: unknown, speakerKind?: unknown): string | null {
  const label = text(value, "").replace(/^@/, "").trim();
  if (["USER", "\u6211", "\u672c\u4eba", "\u81ea\u5df1"].includes(label.toUpperCase())) return "USER";
  const bossMo = "boss\u58a8";
  if (label.toLocaleLowerCase() === bossMo.toLocaleLowerCase()) return bossMo;
  if (["\u5b9d\u603b", "\u59dc\u6c41\u6c7d\u6c34"].includes(label)) return label;
  return upper(speakerKind, "") === "NAMED_PERSON" && label ? label : null;
}

function number(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function noteKey(item: Dict, index: number): string {
  const identity = asDict(item.identity);
  const revision = asDict(item.revision);
  return text(
    identity.note_id,
    text(revision.note_revision_id, `note-${index}`),
  );
}

function researchDraftHref(identity: Dict): string | null {
  const instrumentId = text(identity.primary_instrument_id, "");
  if (!instrumentId) return null;
  const params = new URLSearchParams({
    create: "observation",
    instrument_id: instrumentId,
    title: text(identity.title, shortId(instrumentId)),
  });
  return `/research?${params.toString()}`;
}

function researchHref(identity: Dict, matchingSubjectId: string | null): string {
  if (matchingSubjectId) {
    return `/research#subject-${encodeURIComponent(matchingSubjectId)}`;
  }
  return researchDraftHref(identity) ?? "/research";
}

function matchingSubjectsFor(
  identity: Dict,
  activeSubjects: SubjectAggregate[],
): SubjectAggregate[] {
  const instrumentId = text(identity.primary_instrument_id, "");
  if (!instrumentId) return [];
  return activeSubjects.filter(
    (candidate) =>
      text(candidate.subject?.primary_instrument_id, "") === instrumentId,
  );
}

function noteSummary(revision: Dict, payload: Dict): string {
  return text(
    payload.material_change_summary,
    text(revision.summary, "No material change summary available."),
  );
}

function scenarioFor(scenarios: Dict[], name: string): Dict | null {
  return (
    scenarios.find((scenario) => upper(scenario.scenario, "") === name) ?? null
  );
}

function renderContext(
  value: ReactNode | ((instrumentId: string) => ReactNode) | undefined,
  instrumentId: string,
): ReactNode {
  return typeof value === "function" ? value(instrumentId) : value;
}

function attributionSections(blocks: Dict[]): AttributionDisplaySection[] {
  const sections: AttributionDisplaySection[] = [];
  let currentDate: string | null = null;
  let currentSpeaker = "USER";
  for (const block of blocks) {
    let body = text(block.body, "");
    const storedDate = text(block.section_date, "");
    if (storedDate && storedDate !== currentDate) {
      currentDate = storedDate;
      currentSpeaker = "USER";
    }
    const dateMatch = body.match(DATE_SECTION_LINE);
    if (dateMatch) {
      const remainder = text(dateMatch[1], "");
      currentDate = body.slice(0, body.length - remainder.length).trim().replace(/[:：]$/, "").trim();
      body = remainder;
      currentSpeaker = "USER";
      if (!body) continue;
    }
    if (!body) continue;
    let section = sections.at(-1);
    if (!section || section.dateLabel !== currentDate) {
      section = { dateLabel: currentDate, blocks: [] };
      sections.push(section);
    }
    const explicitSpeaker = canonicalSpeakerLabel(block.speaker_label, block.speaker_kind);
    if (explicitSpeaker) currentSpeaker = explicitSpeaker;
    const speakerLabel = currentSpeaker;
    const previous = section.blocks.at(-1);
    if (previous?.speakerLabel === speakerLabel) {
      previous.body = `${previous.body}\n\n${body}`;
    } else {
      section.blocks.push({ speakerLabel, body });
    }
  }
  return sections;
}

function HistoryContent({
  noteId,
  history,
  historyBusy,
  historyError,
}: {
  noteId: string;
  history: Dict[];
  historyBusy: string | null;
  historyError?: string;
}) {
  if (historyBusy === noteId) {
    return <p className="card-note">Loading revision history…</p>;
  }
  if (historyError) return <ErrorNote>{historyError}</ErrorNote>;
  if (history.length === 0) {
    return <p className="card-note">No revision history is available.</p>;
  }
  return (
    <div className="observation-history-list">
      {history.map((entry, index) => {
        const historyRevision = asDict(entry.revision);
        const historyInterpretation = asDict(entry.interpretation);
        const added = listOf<string>(historyRevision, "added_lines");
        const removed = listOf<string>(historyRevision, "removed_lines");
        const version = number(historyRevision.version) || "—";
        return (
          <div
            className="observation-history-row"
            key={text(
              historyRevision.note_revision_id,
              `${noteId}-revision-${index}`,
            )}
          >
            <header>
              <strong>v{version}</strong>
              <div className="page-actions">
                <Badge value={text(historyRevision.coverage, "UNKNOWN")} />
                <Badge
                  value={text(
                    historyInterpretation.change_relation,
                    number(historyRevision.version) === 1
                      ? "INITIAL"
                      : "UNCLASSIFIED",
                  )}
                />
              </div>
            </header>
            <small>
              Observed {formatDate(historyRevision.observed_at)}
              {historyRevision.source_timestamp
                ? ` · Source ${formatDate(historyRevision.source_timestamp)}`
                : ""}
              {` · ${number(historyRevision.text_length)} characters`}
            </small>
            {historyInterpretation.material_change_summary ? (
              <p>{text(historyInterpretation.material_change_summary)}</p>
            ) : null}
            {added.map((line, lineIndex) => (
              <code className="diff-added" key={`add-${lineIndex}`}>
                + {line}
              </code>
            ))}
            {removed.map((line, lineIndex) => (
              <code className="diff-removed" key={`remove-${lineIndex}`}>
                − {line}
              </code>
            ))}
          </div>
        );
      })}
    </div>
  );
}

export function ObservationInbox({
  items,
  sources,
  activeSubjects,
  selectedInstrumentIds,
  busy,
  syncMessage,
  syncError,
  onRefresh,
  onSelectSubject,
  onReviewDecision,
  onDeferReview,
  analysisBusyId,
  reviewBusyId,
  onAnalyzeRevision,
  positionContext,
  cyclesContext,
}: ObservationInboxProps) {
  const [scope, setScope] = useState<Scope>("ALL");
  const [query, setQuery] = useState("");
  const [selectedNoteKey, setSelectedNoteKey] = useState<string | null>(null);
  const [historyByNote, setHistoryByNote] = useState<Record<string, Dict[]>>(
    {},
  );
  const [historyBusy, setHistoryBusy] = useState<string | null>(null);
  const [historyError, setHistoryError] = useState<Record<string, string>>({});
  const [subjectOverrides, setSubjectOverrides] = useState<Record<string, string>>({});
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visible = useMemo(
    () =>
      items.filter((item) => {
        const identity = asDict(item.identity);
        const revision = asDict(item.revision);
        const matchesScope =
          scope === "ALL" ||
          selectedInstrumentIds.length === 0 ||
          selectedInstrumentIds.includes(text(identity.primary_instrument_id, ""));
        if (!matchesScope) return false;
        if (!normalizedQuery) return true;
        return [
          identity.title,
          identity.primary_instrument_id,
          identity.source,
          revision.summary,
          revision.source_timestamp,
        ]
          .map((value) => text(value, "").toLocaleLowerCase())
          .some((value) => value.includes(normalizedQuery));
      }),
    [items, normalizedQuery, scope, selectedInstrumentIds],
  );

  useEffect(() => {
    if (visible.length === 0) {
      if (selectedNoteKey !== null) setSelectedNoteKey(null);
      return;
    }
    if (
      !visible.some((item, index) => noteKey(item, index) === selectedNoteKey)
    ) {
      setSelectedNoteKey(noteKey(visible[0], 0));
    }
  }, [selectedNoteKey, visible]);

  const selectedIndex = visible.findIndex(
    (item, index) => noteKey(item, index) === selectedNoteKey,
  );
  const selected = visible[selectedIndex >= 0 ? selectedIndex : 0] ?? null;

  async function loadHistory(noteId: string) {
    if (!noteId || historyByNote[noteId] || historyBusy === noteId) return;
    setHistoryBusy(noteId);
    setHistoryError((current) => ({ ...current, [noteId]: "" }));
    try {
      const response = asDict(
        await getJson(
          `/api/observations/${encodeURIComponent(noteId)}/history?limit=20`,
        ),
      );
      setHistoryByNote((current) => ({
        ...current,
        [noteId]: listOf<Dict>(asDict(response.data), "items"),
      }));
    } catch (cause) {
      setHistoryError((current) => ({
        ...current,
        [noteId]:
          cause instanceof Error ? cause.message : "Revision history failed.",
      }));
    } finally {
      setHistoryBusy(null);
    }
  }

  const selectedIdentity = asDict(selected?.identity);
  const selectedRevision = asDict(selected?.revision);
  const selectedInterpretation = asDict(selected?.interpretation);
  const selectedReview = asDict(selected?.review);
  const selectedCoverage = upper(selectedRevision.coverage, "UNKNOWN");
  const selectedInterpretationStatus = upper(
    selectedInterpretation.status,
    "PENDING",
  );
  const selectedHasFullText = selectedCoverage === "FULL";
  const selectedReady =
    selectedHasFullText && selectedInterpretationStatus === "SUCCEEDED";
  // Interpretation payload is intentionally ignored for summary-only notes.
  const selectedPayload = selectedReady
    ? asDict(selectedInterpretation.payload)
    : {};
  const selectedScenarios = listOf<Dict>(
    selectedPayload,
    "user_scenarios",
  );
  const selectedBlocks = selectedHasFullText
    ? listOf<Dict>(selectedRevision, "blocks")
    : [];
  const selectedSpeakerLabels = new Set(selectedBlocks.flatMap((block) => {
    const speakerLabel = canonicalSpeakerLabel(block.speaker_label, block.speaker_kind);
    return speakerLabel ? [speakerLabel] : [];
  }));
  const selectedViewpoints = listOf<Dict>(selectedPayload, "viewpoints").flatMap((viewpoint) => {
    const speakerLabel = canonicalSpeakerLabel(
      viewpoint.speaker_label,
      viewpoint.speaker_kind,
    );
    return speakerLabel && selectedSpeakerLabels.has(speakerLabel)
      ? [{ ...viewpoint, speaker_label: speakerLabel } as Dict]
      : [];
  });
  const selectedAttributionSections = attributionSections(selectedBlocks);
  const selectedNoteId = text(selectedIdentity.note_id, "");
  const selectedRevisionId = text(selectedRevision.note_revision_id, "");
  const selectedMatchingSubjects = selected
    ? matchingSubjectsFor(selectedIdentity, activeSubjects)
    : [];
  const selectedSubjectOverride = subjectOverrides[selectedNoteId] ?? "";
  const selectedMatchingSubjectId = selectedMatchingSubjects.some(
    (item) => text(item.subject?.subject_id, "") === selectedSubjectOverride,
  )
    ? selectedSubjectOverride
    : selectedMatchingSubjects.length === 1
      ? text(selectedMatchingSubjects[0].subject?.subject_id, "")
      : null;
  const selectedResearchHref = researchHref(
    selectedIdentity,
    selectedMatchingSubjectId,
  );
  const selectedInstrumentIdForContext = text(
    selectedIdentity.primary_instrument_id,
    "",
  );
  const renderedPositionContext = renderContext(
    positionContext,
    selectedInstrumentIdForContext,
  );
  const renderedCyclesContext = renderContext(
    cyclesContext,
    selectedInstrumentIdForContext,
  );

  return (
    <Card
      className="notes-card"
      title="Latest Thinking"
      action={
        <div className="page-actions">
          <input
            aria-label="Filter Notes"
            placeholder="Symbol or note"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <select
            aria-label="Note Scope"
            value={scope}
            onChange={(event) => setScope(event.target.value as Scope)}
          >
            <option value="ALL">All Notes</option>
            <option value="CURRENT">Current Instrument</option>
          </select>
          <Badge value={`${sources.length} SOURCES`} />
          <Badge value={`${items.length} TOTAL`} />
          <ActionButton busy={busy} onClick={onRefresh}>
            Refresh Sources
          </ActionButton>
        </div>
      }
    >
      <p className="card-note">
        Each source note is retained as immutable revisions. Unprefixed text is
        your view; explicitly named prefixes remain external. Only full-text
        model drafts can enter Decision review.
      </p>

      {visible.length === 0 ? (
        <p className="observation-empty-inline">
          No notes match this scope and filter. Clear the filter, switch scope,
          or refresh sources.
        </p>
      ) : (
        <div className="notes-master-detail">
          <aside className="notes-master" aria-label="Notes List">
            <div className="notes-master-heading">
              <div>
                <strong>Source Notes</strong>
              </div>
              <small>
                {visible.length} {visible.length === 1 ? "note" : "notes"}
              </small>
            </div>
            <div
              className="notes-list"
              role="listbox"
              aria-label="Filtered Notes"
              aria-busy={busy}
            >
              {visible.map((item, index) => {
                const identity = asDict(item.identity);
                const revision = asDict(item.revision);
                const interpretation = asDict(item.interpretation);
                const review = asDict(item.review);
                const key = noteKey(item, index);
                const isSelected = key === selectedNoteKey;
                const coverage = upper(revision.coverage, "UNKNOWN");
                const status = upper(interpretation.status, "PENDING");
                const summary = text(
                  revision.summary,
                  "No summary available.",
                );
                return (
                  <button
                    key={key}
                    className={`notes-cell${isSelected ? " selected" : ""}`}
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    onClick={() => setSelectedNoteKey(key)}
                  >
                    <span className="notes-cell-heading">
                      <strong>{text(identity.title, "Untitled Note")}</strong>
                      <time>{formatDate(revision.observed_at)}</time>
                    </span>
                    <small>
                      {text(identity.source, "EXTERNAL")} ·{" "}
                      {shortId(identity.primary_instrument_id)} · v
                      {number(revision.version) || 1}
                    </small>
                    <span className="notes-cell-summary">{summary}</span>
                    <span className="notes-cell-badges">
                      <Badge value={`TEXT · ${coverage}`} />
                      <Badge value={`ANALYSIS · ${status}`} tone={status === "FAILED" ? "bad" : status === "SUCCEEDED" ? "good" : "neutral"} />
                      {review.status ? <Badge value={`REVIEW · ${upper(review.status)}`} tone={["ADOPTED", "NO_ACTION"].includes(upper(review.status)) ? "good" : "warn"} /> : null}
                    </span>
                  </button>
                );
              })}
            </div>
            {visible.length > 0 ? (
              <small className="notes-list-footnote">
                Select a note to inspect its latest revision and decision-safe
                analysis.
              </small>
            ) : null}
          </aside>

          <section
            className="notes-detail"
            id="notes-detail"
            aria-label="Selected Note"
            aria-live="polite"
          >
            {selected ? (
              <>
                <header className="notes-detail-header">
                  <div>
                    <h3>{text(selectedIdentity.title, "Untitled Note")}</h3>
                    <p>
                      {text(selectedIdentity.source, "EXTERNAL")} ·{" "}
                      {shortId(selectedIdentity.primary_instrument_id)} · v
                      {number(selectedRevision.version) || 1} · Observed{" "}
                      {formatDate(selectedRevision.observed_at)}
                    </p>
                  </div>
                  <div className="page-actions">
                    <Badge value={`TEXT · ${selectedCoverage}`} />
                    <Badge value={`ANALYSIS · ${selectedInterpretationStatus}`} tone={selectedInterpretationStatus === "FAILED" ? "bad" : selectedInterpretationStatus === "SUCCEEDED" ? "good" : "neutral"} />
                    {selectedReview.status ? <Badge value={`REVIEW · ${upper(selectedReview.status)}`} tone={["ADOPTED", "NO_ACTION"].includes(upper(selectedReview.status)) ? "good" : "warn"} /> : null}
                  </div>
                </header>

                {!selectedInstrumentIdForContext ? <div className="inline-error">Canonical Instrument unresolved. Position and Trade Cycle absence cannot be verified, and Decision review remains unavailable until identity is attached.</div> : null}

                {selectedInterpretationStatus === "FAILED" ? <div className="notes-recovery"><strong>Analysis failed</strong><span>{text(selectedInterpretation.error_code, "The durable draft could not be produced.")}</span>{selectedRevisionId ? <ActionButton busy={analysisBusyId === selectedRevisionId} onClick={() => onAnalyzeRevision(selectedRevisionId)}>Retry Analysis</ActionButton> : null}</div> : null}

                <section className="notes-section">
                  <header className="notes-section-heading">
                    <div>
                      <span className="card-kicker">REVISION SUMMARY</span>
                      <h4>What Changed</h4>
                    </div>
                    {selectedReady ? (
                      <Badge
                        value={text(
                          selectedPayload.change_relation,
                          "MATERIAL CHANGE",
                        )}
                      />
                    ) : null}
                  </header>
                  <p className="notes-section-copy">
                    {noteSummary(selectedRevision, selectedPayload)}
                  </p>
                  {selectedRevision.source_timestamp ? (
                    <small className="notes-source-meta">
                      Source timestamp {formatDate(selectedRevision.source_timestamp)}
                    </small>
                  ) : null}
                </section>

                <section className="notes-section">
                  <header className="notes-section-heading">
                    <div>
                      <span className="card-kicker">SCENARIO MAP</span>
                      <h4>Current View</h4>
                    </div>
                    {selectedReady ? <Badge value="4 SCENARIOS" /> : null}
                  </header>
                  {selectedReady ? (
                    <div className="notes-scenario-grid">
                      {SCENARIOS.map((scenario) => {
                        const value = scenarioFor(selectedScenarios, scenario);
                        return (
                          <article className="notes-scenario-card" key={scenario}>
                            <header>
                              <strong>{scenario}</strong>
                              <Badge value={upper(value?.action, "REVIEW")} />
                            </header>
                            <p>{text(value?.condition, "No condition recorded.")}</p>
                          </article>
                        );
                      })}
                    </div>
                  ) : (
                    <p className="notes-safety">
                      {!selectedHasFullText
                        ? "SUMMARY_ONLY: full source text is unavailable. Analysis and Decision review remain disabled."
                        : selectedInterpretationStatus === "FAILED"
                          ? "Analysis failed for this full-text revision. Retry analysis to produce the four-scenario view."
                          : "Analysis is not ready for this full-text revision. Analyze the note to produce the four-scenario view."}
                    </p>
                  )}
                </section>

                <section className="notes-section notes-context">
                  <header className="notes-section-heading">
                    <div>
                      <span className="card-kicker">PORTFOLIO CONTEXT</span>
                      <h4>Position &amp; Cycles</h4>
                    </div>
                  </header>
                  {renderedPositionContext || renderedCyclesContext ? (
                    <div className="notes-context-grid">
                      {renderedPositionContext ? (
                        <div className="notes-context-slot">
                          <span>Position</span>
                          {renderedPositionContext}
                        </div>
                      ) : null}
                      {renderedCyclesContext ? (
                        <div className="notes-context-slot">
                          <span>Trade Cycles</span>
                          {renderedCyclesContext}
                        </div>
                      ) : null}
                    </div>
                  ) : (
                    <p className="card-note">
                      Position and cycle context is not provided in this view.
                    </p>
                  )}
                </section>

                <section className="notes-section">
                  <header className="notes-section-heading">
                    <div>
                      <span className="card-kicker">SOURCE RECORD</span>
                      <h4>Source &amp; Attribution</h4>
                    </div>
                    <Badge value={selectedCoverage} />
                  </header>
                  {selectedHasFullText ? (
                    selectedAttributionSections.length > 0 ? (
                      <div className="notes-attribution-sections">
                        {selectedAttributionSections.map((section, sectionIndex) => <section key={`${section.dateLabel ?? "undated"}-${sectionIndex}`}>
                          <header><span>Date</span><strong>{section.dateLabel ?? "Undated"}</strong></header>
                          <div className="journal-timeline-list">{section.blocks.map((block, blockIndex) => <article key={`${block.speakerLabel}-${blockIndex}`}><Badge value={block.speakerLabel} /><div><p>{block.body}</p></div></article>)}</div>
                        </section>)}
                      </div>
                    ) : (
                      <p className="card-note">
                        Full source text is not present in this revision.
                      </p>
                    )
                  ) : (
                    <p className="notes-safety">
                      SUMMARY_ONLY source text is intentionally withheld. The
                      stored summary remains visible for change detection only.
                    </p>
                  )}
                  {selectedReady && selectedViewpoints.length > 0 ? (
                    <div className="notes-viewpoints">
                      <span className="card-kicker">INTERPRETATION ATTRIBUTION</span>
                      {selectedViewpoints.map((viewpoint, index) => (
                        <article key={`${text(viewpoint.speaker_label)}-${index}`}>
                          <header>
                            <strong>{text(viewpoint.speaker_label, "USER")}</strong>
                            <Badge value={upper(viewpoint.direction)} />
                          </header>
                          <p>{text(viewpoint.summary)}</p>
                        </article>
                      ))}
                    </div>
                  ) : null}
                </section>

                <section className="notes-section notes-history">
                  <Disclosure
                    onToggle={(open) => {
                      if (open) void loadHistory(selectedNoteId);
                    }}
                    title="Revision History"
                    variant="compact"
                  >
                    <HistoryContent
                      noteId={selectedNoteId}
                      history={historyByNote[selectedNoteId] ?? []}
                      historyBusy={historyBusy}
                      historyError={historyError[selectedNoteId]}
                    />
                  </Disclosure>
                </section>

                <div className="portfolio-form-actions notes-actions">
                  {selectedMatchingSubjects.length > 1 ? <label className="research-field"><span><b className="required-mark" aria-hidden="true">*</b>Research Subject</span><select value={selectedMatchingSubjectId ?? ""} onChange={(event) => { const value = event.target.value; setSubjectOverrides((current) => ({ ...current, [selectedNoteId]: value })); if (value) onSelectSubject(value); }}><option value="">Choose the exact Research Subject</option>{selectedMatchingSubjects.map((item) => { const candidate = asDict(item.subject); const candidateId = text(candidate.subject_id, ""); return <option key={candidateId} value={candidateId}>{text(candidate.title, "Untitled Research Subject")} · {upper(candidate.status)}</option>; })}</select><small>More than one Research Subject uses this Instrument. Selection is required; the system will not guess.</small></label> : null}
                  <Link
                    className="action-button default"
                    href={selectedResearchHref}
                    onClick={() => {
                      if (selectedMatchingSubjectId) {
                        onSelectSubject(selectedMatchingSubjectId);
                      }
                    }}
                  >
                    Open Research
                  </Link>
                  {selectedReady && selectedMatchingSubjectId ? (
                    <ActionButton
                      busy={reviewBusyId === text(selectedRevision.note_revision_id, "")}
                      busyLabel="Preparing Deep Review…"
                      onClick={() => { void onReviewDecision(selected, selectedMatchingSubjectId); }}
                    >
                      Review View Change
                    </ActionButton>
                  ) : null}
                  {selectedReady && !["ADOPTED", "NO_ACTION"].includes(upper(selectedReview.status, "PENDING")) ? (
                    <button type="button" onClick={() => { void onDeferReview(selected, selectedMatchingSubjectId); }}>
                      Defer Review
                    </button>
                  ) : null}
                  {selectedHasFullText &&
                  !selectedReady &&
                  selectedRevisionId ? (
                    <ActionButton
                      busy={analysisBusyId === selectedRevisionId}
                      onClick={() => onAnalyzeRevision(selectedRevisionId)}
                    >
                      {selectedInterpretationStatus === "FAILED"
                        ? "Retry Analysis"
                        : "Analyze Note"}
                    </ActionButton>
                  ) : null}
                  {!selectedHasFullText ? (
                    <small>
                      Awaiting full source text; interpretation and adoption are
                      disabled.
                    </small>
                  ) : null}
                </div>
              </>
            ) : (
              <p className="observation-empty-inline">
                Select a note to inspect its latest thinking.
              </p>
            )}
          </section>
        </div>
      )}
      {syncMessage ? (
        <div className="inline-success" role="status" aria-live="polite">
          {syncMessage}
        </div>
      ) : null}
      <ErrorNote>{syncError}</ErrorNote>
    </Card>
  );
}
