"use client";

import { useEffect, useMemo, useState } from "react";
import { RefreshCw, Sparkles } from "lucide-react";
import { ErrorNote, Paginator, Badge, Card, DataBoundary, Empty, PageActionMenu } from "../components/ui";
import { ConsoleShell } from "../components/console-shell";
import { listOf, postApi, useApi } from "../lib/api";
import { textDash as text } from "../lib/coerce";

type Dict = Record<string, unknown>;

type SubjectAggregate = {
  subject?: Dict;
  state?: Dict;
} & Dict;

const ALL_SUBJECTS = "all";
const TARGET_DIMENSION_OUTCOMES = ["NOT_EVALUATED", "EVALUATED", "PARTIAL", "PASS", "FAIL"];

function asDict(value: unknown): Dict {
  return value && typeof value === "object" ? (value as Dict) : {};
}

function asList(value: unknown): Dict[] {
  if (Array.isArray(value)) return value.filter((item): item is Dict => item !== null && typeof item === "object");
  return [];
}

function asFactObject(value: unknown): Dict | null {
  if (value === null || value === undefined) return null;
  if (Array.isArray(value)) {
    if (value.length < 2) return null;
    const [factKey, factValue] = value;
    return { key: text(factKey), value: factValue };
  }
  if (typeof value === "object") return value as Dict;
  return null;
}

function asFacts(value: unknown): Dict[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item): Dict | null => asFactObject(item))
    .filter((item): item is Dict => item !== null);
}

function unwrapEnvelope(value: unknown): Dict {
  const source = asDict(value);
  const nested = asDict(source.data);
  if (Object.keys(nested).length > 0) return nested;
  const nestedResult = asDict(source.result);
  return Object.keys(nestedResult).length > 0 ? nestedResult : source;
}

function id(value: unknown): string {
  return text(value).trim();
}

function upper(value: unknown): string {
  return String(value ?? "").trim().toUpperCase();
}

function short(value: string, max = 18): string {
  return value.length <= max ? value : `${value.slice(0, max)}…`;
}

function parseDate(value: unknown): number {
  const raw = text(value, "");
  if (!raw || raw === "—") return 0;
  const date = new Date(raw);
  return Number.isNaN(date.getTime()) ? 0 : date.getTime();
}

function hasObjectKey(value: unknown, key: string): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && key in asDict(value);
}

function extractList(value: unknown, key: "subjects" | "scorecards"): Dict[] {
  const source = asDict(value);
  const nestedValue = asDict(source[key]);
  const nestedData = asDict(nestedValue.data);
  const nestedResult = asDict(nestedValue.result);
  return [
    ...asList(source[key]),
    ...asList((asDict(source.data))[key]),
    ...asList((asDict(source.result))[key]),
    ...(key === "scorecards" ? asList(nestedData.runs) : []),
    ...(key === "scorecards" ? asList(nestedResult.runs) : []),
  ];
}

function normalizeSubject(item: SubjectAggregate): Dict {
  if (hasObjectKey(item, "subject") && item.subject !== null && typeof item.subject === "object") {
    return { ...(asDict(item.subject) as Dict), state: item.state };
  }
  return { ...item };
}

function uniqueById(items: Dict[]): Dict[] {
  const byId = new Map<string, Dict>();
  for (const item of items) {
    const itemId = id(item.thesis_id);
    if (!itemId || byId.has(itemId)) continue;
    byId.set(itemId, item);
  }
  return Array.from(byId.values());
}

function collectThesesFromSubject(subject: Dict): Dict[] {
  const state = unwrapEnvelope(subject.state);
  const thesisRows = [...asList(state.theses), ...asList(state.latest_revisions)];
  const thesisById = new Map<string, Dict>();
  for (const item of thesisRows) {
    const thesisId = id(item.thesis_id);
    if (!thesisId) continue;
    const existing = thesisById.get(thesisId);
    if (existing) continue;
    thesisById.set(thesisId, {
      thesis_id: thesisId,
      thesis_title: text(item.thesis_title, text(item.title)),
      thesis_status: text(item.thesis_status, text(item.status)),
    });
  }
  return Array.from(thesisById.values());
}

function collectThesesFromScorecards(runs: Dict[], subjectId: string): Dict[] {
  const byId = new Map<string, Dict>();
  for (const run of runs) {
    if (text(run.subject_id, "") !== subjectId) continue;
    const thesisId = id(run.thesis_id);
    if (!thesisId) continue;
    if (byId.has(thesisId)) continue;
    byId.set(thesisId, {
      thesis_id: thesisId,
      thesis_title: text(run.thesis_title),
      thesis_status: "UNKNOWN",
    });
  }
  return Array.from(byId.values());
}

function collectDimensionOutcomeCodes(run: Dict): string[] {
  const result = new Set<string>();
  const status = upper(run.status);
  if (status) result.add(status);
  for (const dimension of asList(run.dimensions)) {
    const code = upper(dimension.result_code);
    if (code) result.add(code);
    const alternative = upper(dimension.status);
    if (alternative) result.add(alternative);
  }
  return Array.from(result);
}

function sortByGeneratedAt(a: Dict, b: Dict): number {
  return parseDate(b.generated_at) - parseDate(a.generated_at);
}

function idempotencyKey(prefix: string): string {
  return `console-${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function ScorecardCard({
  run,
}: {
  run: Dict;
}) {
  const dimensions = asList(run.dimensions);
  const warningCodes = listOf<string>(run, "warning_codes");
  return (
    <article id={`scorecard-${text(run.scorecard_id)}`} className="scorecard-card">
      <header className="scorecard-run-header">
        <div>
          <strong>{text(run.subject_title, short(id(run.subject_id), 30))}</strong>
          <small className="mono">{text(run.scorecard_id)}</small>
        </div>
        <Badge value={text(run.status)} />
      </header>
      <div className="scorecard-run-meta">
        <span>Generated <strong>{text(run.generated_at)}</strong></span>
        <span>Execution Effect: <strong>{text(run.execution_effect, "false")}</strong></span>
        <span>Algorithm <strong>{text(run.algorithm_version)}</strong></span>
        <span>Schema <strong>{text(run.schema_version)}</strong></span>
        <span>Thesis: <strong>{text(run.thesis_title, short(text(run.thesis_id), 24))}</strong></span>
        <span>Locked Revision: <strong>{text(run.thesis_revision_id)} (v{text(run.thesis_revision_no, "—")})</strong></span>
        <span>Input Fingerprint: <strong className="mono">{text(run.input_fingerprint)}</strong></span>
      </div>
      {!!warningCodes.length && (
        <p className="scorecard-run-note">
          <span>Warnings</span>
          {warningCodes.map((warning) => <code key={warning}>{warning}</code>)}
        </p>
      )}
      <details>
        <summary>Dimension Outcome Details ({dimensions.length})</summary>
        {dimensions.length === 0 ? (
          <p className="mono">No dimension records were returned.</p>
        ) : (
          <div className="scorecard-dimensions">
            {dimensions.map((dimension, index) => {
              const dimensionStatus = upper(dimension.status);
              const resultCode = upper(dimension.result_code);
              const dimensionCode = text(dimension.code, text(dimension.dimension_code, `Dimension ${index + 1}`));
              const sourceRefs = asList(dimension.source_refs);
              const limitationCodes = listOf<string>(dimension, "limitation_codes");
              const facts = asFacts(dimension.facts);
              return (
                <article className="scorecard-dimension" key={`${dimensionCode}-${index}`}>
                  <header>
                    <strong>{dimensionCode}</strong>
                    <span>
                      {dimensionStatus ? <Badge value={dimensionStatus} /> : null}
                      <Badge value={resultCode || "UNKNOWN"} />
                    </span>
                  </header>
                  <p>{text(dimension.title, text(dimension.summary, "No dimension summary"))}</p>
                  <dl>
                    <dt>Summary</dt>
                    <dd>{text(dimension.summary, "—")}</dd>
                    <dt>Facts</dt>
                    <dd>
                      {facts.length === 0 ? "None" : facts.map((fact, index) => (
                        <span key={`${text(fact.key)}-${index}`}>{text(fact.key)}: {text(fact.value)}</span>
                      ))}
                    </dd>
                    <dt>Source Refs</dt>
                    <dd>
                      {sourceRefs.length === 0 ? "None" : sourceRefs.map((ref, index) => (
                        <span key={`${text(ref.kind)}:${text(ref.entity_id)}:${index}`}>
                          {text(ref.kind)} · {text(ref.entity_id)}{text(ref.version) !== "—" ? ` · v${text(ref.version)}` : ""}
                        </span>
                      ))}
                    </dd>
                    <dt>Limitations</dt>
                    <dd>{limitationCodes.length === 0 ? "None" : limitationCodes.map((code) => <code key={code}>{code}</code>)}</dd>
                  </dl>
                </article>
              );
            })}
          </div>
        )}
      </details>
    </article>
  );
}

export default function JudgmentScorecardsPage() {
  const [subjectId, setSubjectId] = useState<string>(ALL_SUBJECTS);
  // Read the deep-link filter after mount so SSR and the first client render
  // agree; reading window during state initialization causes a hydration
  // mismatch when a subject_id query parameter is present.
  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get("subject_id");
    if (requested) setSubjectId(requested);
  }, []);
  const [thesisId, setThesisId] = useState<string>("");
  const [historyOffset, setHistoryOffset] = useState(0);
  const [outcomeFilter, setOutcomeFilter] = useState<string>("ALL");
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const scorecardQuery = new URLSearchParams({ limit: "50", offset: String(historyOffset) });
  if (subjectId !== ALL_SUBJECTS) scorecardQuery.set("subject_id", subjectId);
  if (thesisId) scorecardQuery.set("thesis_id", thesisId);
  const scorecardsApi = useApi<Dict>(`/api/scorecards?${scorecardQuery.toString()}`);

  const subjects = useMemo(() => {
    const raw = extractList(scorecardsApi.data, "subjects");
    return raw.map((item) => normalizeSubject(item as SubjectAggregate));
  }, [scorecardsApi.data]);

  const allScorecards = useMemo(() => extractList(scorecardsApi.data, "scorecards").sort(sortByGeneratedAt), [scorecardsApi.data]);
  const scorecardPage = unwrapEnvelope(asDict(scorecardsApi.data?.scorecards));
  const scorecardTotal = Number(scorecardPage.total ?? allScorecards.length);
  const scorecardHasMore = scorecardPage.has_more === true;

  const subjectById = useMemo(() => new Map(subjects.map((subject) => [id(subject.subject_id), subject])), [subjects]);
  const selectedSubject = subjectById.get(subjectId);

  const thesisOptions = useMemo(() => {
    if (!selectedSubject) return [];
    const fromSubject = collectThesesFromSubject(selectedSubject);
    const fromHistory = collectThesesFromScorecards(allScorecards, subjectId);
    const merged = uniqueById([...fromSubject, ...fromHistory]);
    return merged
      .sort((left, right) => text(left.thesis_title).localeCompare(text(right.thesis_title)))
      .map((item) => ({ id: text(item.thesis_id), title: text(item.thesis_title, text(item.thesis_id)), status: text(item.thesis_status, "UNKNOWN") }));
  }, [selectedSubject, subjectId, allScorecards]);

  useEffect(() => {
    if (subjects.length > 0 && subjectId !== ALL_SUBJECTS && !subjectById.has(subjectId)) {
      setSubjectId(ALL_SUBJECTS);
    }
  }, [subjectById, subjectId, subjects.length]);

  const outcomeSet = useMemo(() => {
    const codes = new Set(["ALL"]);
    for (const card of allScorecards) {
      for (const code of collectDimensionOutcomeCodes(card)) {
        if (code) codes.add(code);
      }
    }
    TARGET_DIMENSION_OUTCOMES.forEach((item) => codes.add(item));
    return Array.from(codes);
  }, [allScorecards]);

  const filtered = useMemo(() => {
    const base = subjectId === ALL_SUBJECTS
      ? allScorecards
      : allScorecards.filter((card) => id(card.subject_id) === subjectId);
    if (outcomeFilter === "ALL") return base;
    return base.filter((card) => collectDimensionOutcomeCodes(card).includes(outcomeFilter));
  }, [allScorecards, outcomeFilter, subjectId]);

  const canGenerate = subjectId !== ALL_SUBJECTS && thesisId !== "";

  useEffect(() => {
    if (subjectId === ALL_SUBJECTS) {
      setThesisId("");
      return;
    }
    if (thesisId && !thesisOptions.some((item) => item.id === thesisId)) {
      setThesisId("");
    }
  }, [thesisId, subjectId, thesisOptions]);

  async function generate() {
    if (!canGenerate) return;
    setBusy("generate");
    setError(null);
    setMessage(null);
    try {
      const response = await postApi<Dict>("/api/tools/invoke", {
        tool_name: "research_workflow_run",
        arguments: {
          request: {
            operation: "judgment_scorecard",
            case_id: subjectId,
            thesis_id: thesisId,
            idempotency_key: idempotencyKey("judgment-scorecard"),
          },
        },
        confirmation: "research_workflow_run",
      });
      const body = asDict(response);
      const result = asDict("ok" in asDict(body.result) ? body.result : body);
      if (result.ok === false) {
        const first = listOf<Dict>(result, "errors")[0];
        throw new Error(text(first?.message, "Failed to generate scorecard"));
      }
      setMessage("Scorecard generation request submitted. Refreshing list...");
      scorecardsApi.refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to generate scorecard");
    } finally {
      setBusy(null);
    }
  }

  return (
    <ConsoleShell active="scorecards" pageActions={<PageActionMenu ariaLabel="Scorecards Page Actions" items={[
      { id: "generate", label: busy === "generate" ? "Generating…" : "Generate Scorecard", description: "Create a read-only scorecard for the selected Thesis", icon: <Sparkles aria-hidden="true" />, disabled: busy === "generate" || !canGenerate, onSelect: () => { void generate(); } },
      { id: "refresh", label: scorecardsApi.loading ? "Refreshing…" : "Refresh", description: "Reload durable Judgment Scorecards", icon: <RefreshCw aria-hidden="true" className={scorecardsApi.loading ? "spin" : undefined} />, disabled: scorecardsApi.loading, onSelect: scorecardsApi.refresh },
    ]} />}>
      <DataBoundary loading={scorecardsApi.loading} error={scorecardsApi.error}>
        <ErrorNote>{error}</ErrorNote>
        {message ? <p className="card-note">{message}</p> : null}
        <Card kicker="SCORECARD RUNS · READ-ONLY EVIDENCE" title="Judgment Scorecards">
          <div className="workspace-controls scorecards-controls">
            <label>
              <span>Subject</span>
              <select value={subjectId} onChange={(event) => { setSubjectId(event.target.value); setThesisId(""); setHistoryOffset(0); }}>
                <option value={ALL_SUBJECTS}>All Subjects (Browse Only)</option>
                {subjects.map((subject) => (
                  <option key={id(subject.subject_id)} value={id(subject.subject_id)}>
                    {text(subject.title)} ({text(subject.status)}) · {short(id(subject.primary_instrument_id))}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Thesis</span>
              <select
                value={thesisId}
                disabled={subjectId === ALL_SUBJECTS || thesisOptions.length === 0}
                onChange={(event) => { setThesisId(event.target.value); setHistoryOffset(0); }}
              >
                <option value="">
                  {subjectId === ALL_SUBJECTS ? "Select a subject first" : thesisOptions.length === 0 ? "No available thesis" : "Select thesis"}
                </option>
                {thesisOptions.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.title} ({item.status})
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Dimension Outcome</span>
              <select value={outcomeFilter} onChange={(event) => setOutcomeFilter(event.target.value)}>
                {outcomeSet.length > 0 && [...outcomeSet].sort().map((item) => <option key={item} value={item}>{item}</option>)}
              </select>
            </label>
          </div>
          <p className="card-note">
            Latest scorecards are shown first. Use filters for subject and outcome; the Generate button is disabled for browsing mode or when a thesis is not chosen.
          </p>
          {filtered.length === 0
            ? <Empty>No matching Judgment Scorecards.</Empty>
            : (
              <div className="scorecard-run-grid">
                {filtered.map((run) => (
                  <ScorecardCard
                    key={text(run.scorecard_id, `${text(run.subject_id)}-${text(run.generated_at)}`)}
                    run={run}
                  />
                ))}
              </div>
            )}
          <Paginator
            step={50}
            offset={historyOffset}
            hasMore={scorecardHasMore}
            onOffsetChange={setHistoryOffset}
            summary={<small>{scorecardTotal} Total · Showing {allScorecards.length === 0 ? 0 : historyOffset + 1}–{Math.min(historyOffset + allScorecards.length, scorecardTotal)}</small>}
          />
        </Card>
      </DataBoundary>
    </ConsoleShell>
  );
}
