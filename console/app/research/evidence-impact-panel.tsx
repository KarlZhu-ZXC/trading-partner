"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ActionButton,
  Badge,
  Button,
  Card,
  DescriptionList,
  Disclosure,
  Empty,
  ErrorNote,
  FormActions,
  FormField,
  Select,
  Textarea,
  formatDate,
} from "../components/ui";
import { getJson } from "../lib/api";
import { asObject, textDash as text } from "../lib/coerce";
import { ResearchResultSources, readResearchSourceHandoff, type ResearchSourceHandoff } from "./research-result-sources";
import { EvidenceComparison } from "./evidence-comparison";

type Dict = Record<string, unknown>;

export type EvidenceSourceRef = {
  kind: "EVIDENCE" | "OBSERVATION" | "MONITOR" | string;
  source_id: string;
  monitor_id?: string | null;
  rule_code?: string | null;
};

export type EvidenceSource = {
  source: EvidenceSourceRef;
  title: string;
  source_name?: string | null;
  source_url?: string | null;
  source_url_invalid?: boolean;
  fact_at: string | null;
  recorded_at: string | null;
  origin: string;
  warning_codes: string[];
};

export type EvidenceImpact = {
  impact_id: string;
  version: number;
  subject_id: string;
  thesis_id: string;
  thesis_revision_id: string;
  assumption_id: string;
  source: EvidenceSourceRef;
  stance: "supports" | "contradicts" | "uncertain" | string;
  rationale: string;
  state: "ACTIVE" | "RETRACTED" | string;
  confirmed_at: string | null;
  confirmed_by: string | null;
  candidate_id: string | null;
  source_available?: boolean;
};

export type EvidenceTarget = {
  thesis_id: string;
  thesis_revision_id: string;
  revision_no: number;
  thesis_title: string;
  assumption_id: string;
  statement: string;
  is_current: boolean;
};

type Decision = { decision_id: string; decided_at: string; title: string };

export type EvidenceLinks = {
  subject_id: string;
  as_of: string;
  sources: EvidenceSource[];
  sources_has_more: boolean;
  impacts: EvidenceImpact[];
  impact_history: { impact_id: string; version: number; stance: string; state: string; rationale: string; confirmed_at: string | null; confirmed_by: string; thesis_revision_id: string; assumption_id: string; source: EvidenceSourceRef }[];
  impacts_has_more: boolean;
  targets: EvidenceTarget[];
  decisions: Decision[];
  coverage: { sources: string; impacts: string; impact_history: string };
};

type ImpactMode = "create" | "revise" | "retract";

type ImpactDraft = {
  mode: ImpactMode;
  impactId: string | null;
  expectedVersion: number | null;
  targetKey: string;
  sourceKey: string;
  stance: "supports" | "contradicts" | "uncertain";
  rationale: string;
  attemptKey: string;
  submittedFingerprint?: string;
};

type Props = {
  subjectId: string;
  onWrite: (toolName: string, request: Dict, confirmation?: string) => Promise<Dict>;
  onRefresh: () => void;
  busy: boolean;
  linksRead?: SharedEvidenceLinksRead;
};

export type SharedEvidenceLinksRead = {
  data: unknown | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
};

function attemptKey(prefix: string, subjectId: string): string {
  return `console-${prefix}-${subjectId}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function sourceKey(ref: EvidenceSourceRef): string {
  return [ref.kind, ref.source_id, ref.monitor_id ?? "", ref.rule_code ?? ""].join(":");
}

function sourceRef(value: unknown): EvidenceSourceRef | null {
  const item = asObject(value);
  const kind = text(item.kind, "");
  const sourceId = text(item.source_id, "");
  if (!kind || !sourceId) return null;
  return {
    kind,
    source_id: sourceId,
    ...(item.monitor_id ? { monitor_id: text(item.monitor_id) } : {}),
    ...(item.rule_code ? { rule_code: text(item.rule_code) } : {}),
  };
}

function emptyDraft(subjectId: string): ImpactDraft {
  return {
    mode: "create",
    impactId: null,
    expectedVersion: null,
    targetKey: "",
    sourceKey: "",
    stance: "uncertain",
    rationale: "",
    attemptKey: attemptKey("evidence-impact", subjectId),
  };
}

function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const parsed = new URL(value.trim());
    if ((parsed.protocol !== "http:" && parsed.protocol !== "https:") || parsed.username || parsed.password || !parsed.hostname) return null;
    return parsed.toString();
  } catch {
    return null;
  }
}

export function normalizeSnapshot(value: unknown, subjectId: string): EvidenceLinks {
  const root = asObject(value);
  const data = asObject(root.data);
  return {
    subject_id: text(data.subject_id, subjectId),
    as_of: text(data.as_of, ""),
    sources: Array.isArray(data.sources) ? data.sources.map((item) => {
      const sourceItem = asObject(item);
      const rawSourceUrl = typeof sourceItem.source_url === "string" ? sourceItem.source_url.trim() : "";
      return {
        source: sourceRef(sourceItem.source) ?? { kind: "EVIDENCE", source_id: "" },
        title: text(sourceItem.title, "Untitled source"),
        source_name: typeof sourceItem.source_name === "string" ? sourceItem.source_name : null,
        source_url: safeHttpUrl(rawSourceUrl),
        source_url_invalid: Boolean(rawSourceUrl && !safeHttpUrl(rawSourceUrl)),
        fact_at: typeof sourceItem.fact_at === "string" ? sourceItem.fact_at : null,
        recorded_at: typeof sourceItem.recorded_at === "string" ? sourceItem.recorded_at : null,
        origin: text(sourceItem.origin, "Unknown origin"),
        warning_codes: Array.isArray(sourceItem.warning_codes) ? sourceItem.warning_codes.filter((code): code is string => typeof code === "string") : [],
      };
    }).filter((item) => item.source.source_id) : [],
    sources_has_more: data.sources_has_more === true,
    impacts: Array.isArray(data.impacts) ? data.impacts.map((item) => {
      const impact = asObject(item);
      return {
        impact_id: text(impact.impact_id, ""),
        version: typeof impact.version === "number" ? impact.version : 1,
        subject_id: text(impact.subject_id, subjectId),
        thesis_id: text(impact.thesis_id, ""),
        thesis_revision_id: text(impact.thesis_revision_id, ""),
        assumption_id: text(impact.assumption_id, ""),
        source: sourceRef(impact.source) ?? { kind: "EVIDENCE", source_id: "" },
        stance: typeof impact.stance === "string" && impact.stance.trim() ? impact.stance : "UNKNOWN",
        rationale: text(impact.rationale, ""),
        state: typeof impact.state === "string" && impact.state.trim() ? impact.state : "UNKNOWN",
        confirmed_at: typeof impact.confirmed_at === "string" ? impact.confirmed_at : null,
        confirmed_by: typeof impact.confirmed_by === "string" ? impact.confirmed_by : null,
        candidate_id: typeof impact.candidate_id === "string" ? impact.candidate_id : null,
        source_available: typeof impact.source_available === "boolean" ? impact.source_available : undefined,
      };
    }).filter((item) => item.impact_id) : [],
    impact_history: Array.isArray(data.impact_history) ? data.impact_history.map((value) => {
      const row = asObject(value);
      return { impact_id: text(row.impact_id, ""), version: typeof row.version === "number" ? row.version : 1, stance: text(row.stance), state: text(row.state), rationale: text(row.rationale), confirmed_at: typeof row.confirmed_at === "string" ? row.confirmed_at : null, confirmed_by: text(row.confirmed_by), thesis_revision_id: text(row.thesis_revision_id), assumption_id: text(row.assumption_id), source: sourceRef(row.source) ?? { kind: "EVIDENCE", source_id: "" } };
    }) : [],
    impacts_has_more: data.impacts_has_more === true,
    targets: Array.isArray(data.targets) ? data.targets.map((item) => {
      const target = asObject(item);
      return {
        thesis_id: text(target.thesis_id, ""),
        thesis_revision_id: text(target.thesis_revision_id, ""),
        revision_no: typeof target.revision_no === "number" ? target.revision_no : 0,
        thesis_title: text(target.thesis_title, "Untitled Thesis"),
        assumption_id: text(target.assumption_id, ""),
        statement: text(target.statement, "Unnamed assumption"),
        is_current: target.is_current === true,
      };
    }).filter((item) => item.thesis_revision_id && item.assumption_id) : [],
    decisions: Array.isArray(data.decisions) ? data.decisions.map((item) => {
      const decision = asObject(item);
      return { decision_id: text(decision.decision_id, ""), decided_at: text(decision.decided_at, ""), title: text(decision.title, "Untitled Decision") };
    }).filter((item) => item.decision_id) : [],
    coverage: {
      sources: text(asObject(data.coverage).sources, "UNKNOWN"),
      impacts: text(asObject(data.coverage).impacts, "UNKNOWN"),
      impact_history: text(asObject(data.coverage).impact_history, "UNKNOWN"),
    },
  };
}

function sourceLabel(item: EvidenceSource): string {
  const ref = item.source;
  const suffix = ref.kind === "MONITOR" && ref.rule_code ? ` · ${ref.rule_code}` : "";
  return `${item.title} · ${ref.kind} · ${ref.source_id}${suffix}`;
}

function impactTargetKey(impact: EvidenceImpact): string {
  return `${impact.thesis_revision_id}:${impact.assumption_id}`;
}

function targetLabel(target: EvidenceTarget): string {
  const current = target.is_current ? "current" : "history";
  return `${target.thesis_title} · v${target.revision_no} · ${current} · ${target.statement}`;
}

function sourceOptionForImpact(impact: EvidenceImpact): EvidenceSource {
  return {
    source: impact.source,
    title: "Historical source linked to this impact",
    fact_at: null,
    recorded_at: null,
    origin: "Historical immutable reference",
    warning_codes: impact.source_available === false ? ["SOURCE_UNAVAILABLE"] : [],
  };
}

export function EvidenceImpactPanel({ subjectId, onWrite, onRefresh, busy, linksRead }: Props) {
  const [snapshot, setSnapshot] = useState<EvidenceLinks | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [draft, setDraft] = useState<ImpactDraft | null>(null);
  const [researchOrigin, setResearchOrigin] = useState<{ url: string; handoff: ResearchSourceHandoff; source: EvidenceSource } | null>(null);
  const [writeError, setWriteError] = useState<string | null>(null);
  const [writeSuccess, setWriteSuccess] = useState<string | null>(null);

  useEffect(() => {
    if (linksRead) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    void getJson(`/api/research/${encodeURIComponent(subjectId)}/evidence-links`, controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return;
        const next = normalizeSnapshot(response, subjectId);
        if (next.subject_id !== subjectId) throw new Error("Evidence links belong to a different Research Subject.");
        setSnapshot(next);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Evidence links could not be read.");
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [linksRead, subjectId, reload]);

  useEffect(() => {
    setDraft(null);
    setWriteError(null);
    setWriteSuccess(null);
  }, [subjectId]);

  const sharedSnapshot = linksRead?.data == null ? null : normalizeSnapshot(linksRead.data, subjectId);
  const currentSnapshot = linksRead ? sharedSnapshot : snapshot;
  const readLoading = linksRead?.loading ?? loading;
  const readError = linksRead?.error ?? error;
  const sourceCoverageUnavailable = currentSnapshot?.coverage.sources === "UNAVAILABLE";
  const sourceOptions = useMemo(() => {
    const values = [...(currentSnapshot?.sources ?? [])];
    const seen = new Set(values.map((item) => sourceKey(item.source)));
    for (const impact of currentSnapshot?.impacts ?? []) {
      const key = sourceKey(impact.source);
      if (!seen.has(key)) values.push(sourceOptionForImpact(impact));
    }
    if (researchOrigin && !seen.has(sourceKey(researchOrigin.source.source))) values.push(researchOrigin.source);
    return values;
  }, [currentSnapshot, researchOrigin]);

  function startCreate() {
    setResearchOrigin(null);
    setWriteError(null);
    setWriteSuccess(null);
    setDraft(emptyDraft(subjectId));
  }

  function startRevise(impact: EvidenceImpact) {
    setResearchOrigin(null);
    setWriteError(null);
    setWriteSuccess(null);
    setDraft({
      mode: "revise",
      impactId: impact.impact_id,
      expectedVersion: impact.version,
      targetKey: impactTargetKey(impact),
      sourceKey: sourceKey(impact.source),
      stance: impact.stance === "supports" || impact.stance === "contradicts" ? impact.stance : "uncertain",
      rationale: impact.rationale,
      attemptKey: attemptKey("evidence-impact-revise", subjectId),
    });
  }

  function startRetract(impact: EvidenceImpact) {
    setResearchOrigin(null);
    setWriteError(null);
    setWriteSuccess(null);
    setDraft({
      mode: "retract",
      impactId: impact.impact_id,
      expectedVersion: impact.version,
      targetKey: impactTargetKey(impact),
      sourceKey: sourceKey(impact.source),
      stance: impact.stance === "supports" || impact.stance === "contradicts" ? impact.stance : "uncertain",
      rationale: "",
      attemptKey: attemptKey("evidence-impact-retract", subjectId),
    });
  }

  function selectedTarget(): EvidenceTarget | null {
    return currentSnapshot?.targets.find((target) => `${target.thesis_revision_id}:${target.assumption_id}` === draft?.targetKey) ?? null;
  }

  function selectedSource(): EvidenceSource | null {
    return sourceOptions.find((source) => sourceKey(source.source) === draft?.sourceKey) ?? null;
  }

  async function submitImpact() {
    if (!draft || !currentSnapshot || readLoading || readError) return;
    setWriteError(null);
    setWriteSuccess(null);
    if (!draft.rationale.trim()) { setWriteError("A rationale is required for every evidence link proposal."); return; }
    if (sourceCoverageUnavailable && draft.mode !== "retract") { setWriteError("Source coverage is unavailable. New evidence-link proposals are blocked until it recovers."); return; }
    const target = selectedTarget();
    const source = selectedSource();
    if (draft.mode !== "retract" && !target) { setWriteError("Choose a readable Thesis revision and assumption target."); return; }
    if (draft.mode !== "retract" && !source) { setWriteError("Choose a readable source before proposing this link."); return; }
    if (!draft.impactId && draft.mode !== "create") { setWriteError("The selected impact is no longer available. Refresh and try again."); return; }
    const payload: Dict = draft.mode === "retract"
      ? { kind: "evidence_impact", action: "retract", impact_id: draft.impactId, expected_version: draft.expectedVersion, rationale: draft.rationale.trim() }
      : {
        kind: "evidence_impact",
        action: draft.mode,
        ...(draft.mode === "revise" ? { impact_id: draft.impactId, expected_version: draft.expectedVersion } : {}),
        thesis_revision_id: target?.thesis_revision_id,
        assumption_id: target?.assumption_id,
        source: source?.source,
        stance: draft.stance,
        rationale: draft.rationale.trim(),
      };
    if (researchOrigin) {
      try {
        const current = await readResearchSourceHandoff(researchOrigin.url, subjectId);
        if (current.context_status !== "CURRENT" || !current.sources.some((item) => sourceKey(item.source) === draft.sourceKey)) throw new Error("The prepared question or source changed. Prepare Research again.");
        if (current.context.thesis_revision_id && draft.targetKey !== `${current.context.thesis_revision_id}:${current.context.assumption_id}`) throw new Error("This result pins a different assumption. Cancel this draft to create an independent link.");
      } catch (cause) { setWriteError(cause instanceof Error ? cause.message : "Prepared source unavailable"); return; }
    }
    const fingerprint = JSON.stringify(payload);
    const submissionKey = draft.submittedFingerprint && draft.submittedFingerprint !== fingerprint ? attemptKey("evidence-impact", subjectId) : draft.attemptKey;
    setDraft({ ...draft, attemptKey: submissionKey, submittedFingerprint: fingerprint });
    try {
      await onWrite("research_judgment_propose", {
        operation: "research_state",
        case_id: subjectId,
        payload,
        proposed_by: "user",
        proposed_by_rationale: draft.rationale.trim(),
        idempotency_key: submissionKey,
      }, "research_judgment_propose");
      setDraft(null);
      setWriteSuccess("Candidate proposal created. Review it in Pending Candidates before confirmation.");
      onRefresh();
      if (linksRead) linksRead.refresh(); else setReload((value) => value + 1);
    } catch (cause) {
      setWriteError(cause instanceof Error ? cause.message : "Evidence link proposal failed.");
    }
  }

  const effectiveSnapshot = currentSnapshot;
  return <Card id="research-section-evidence-impact" className="research-evidence-card" kicker="EVIDENCE WORKFLOW" title="Assumption Links" description="Connect durable sources to the exact Thesis revision and assumption they support, contradict, or leave uncertain. Each change becomes a reviewable Candidate.">
    <div className="research-form-actions">
      <ActionButton onClick={startCreate} disabled={Boolean(draft) || sourceCoverageUnavailable}>Link Evidence to Thesis</ActionButton>
      <Button type="button" disabled={readLoading} onClick={() => { if (linksRead) linksRead.refresh(); else setReload((value) => value + 1); }}>{readLoading ? "Refreshing…" : "Refresh Evidence Links"}</Button>
    </div>
    {readLoading && <p role="status">Loading durable evidence links…</p>}
    {readError && <ErrorNote role="alert">Evidence links could not be read. Writes are blocked until this source scope is available. {readError}</ErrorNote>}
    {sourceCoverageUnavailable && <ErrorNote role="alert">Source coverage is unavailable. New evidence-link proposals remain disabled; saved historical links are still shown.</ErrorNote>}
    {effectiveSnapshot && <>
      <DescriptionList columns={3} items={[{ label: "Sources", value: <Badge value={effectiveSnapshot.coverage.sources} /> }, { label: "Impacts", value: <Badge value={effectiveSnapshot.coverage.impacts} /> }, { label: "Snapshot Time", value: formatDate(effectiveSnapshot.as_of) }]} />
      {effectiveSnapshot.sources_has_more && <p className="muted" role="status">Recent sources are bounded. Historical links retain their immutable source IDs.</p>}
      {writeSuccess && <div className="inline-success" role="status">{writeSuccess}</div>}
      {writeError && <ErrorNote role="alert">{writeError}</ErrorNote>}
      <ResearchResultSources subjectId={subjectId} disabled={Boolean(draft) || busy || readLoading} onUse={(source, handoff, url) => {
      if (draft || busy) return;
      setResearchOrigin({ source, handoff, url });
      const targetKey = handoff.context.thesis_revision_id ? `${handoff.context.thesis_revision_id}:${handoff.context.assumption_id}` : "";
      const existing = currentSnapshot?.impacts.find((impact) => impact.state === "ACTIVE" && impactTargetKey(impact) === targetKey && sourceKey(impact.source) === sourceKey(source.source));
      setDraft({ ...emptyDraft(subjectId), sourceKey: sourceKey(source.source), targetKey,
        ...(existing ? { mode: "revise" as const, impactId: existing.impact_id, expectedVersion: existing.version, stance: existing.stance === "supports" || existing.stance === "contradicts" ? existing.stance : "uncertain" as const } : {}) });
      setWriteError(null); setWriteSuccess(null);
    }} />
    <EvidenceComparison subjectId={subjectId} snapshot={effectiveSnapshot} linksRead={linksRead} />
      {draft && <section className="continuity-editor" aria-label={`${draft.mode === "create" ? "Create" : draft.mode === "revise" ? "Revise" : "Retract"} Evidence Impact`}>
        {researchOrigin && <p>Prepared question v{researchOrigin.handoff.context.question_version} · cutoff {formatDate(researchOrigin.handoff.context.evidence_cutoff)}. This draft retains that exact context.</p>}
      <div className="continuity-heading"><div><p className="card-kicker">CANDIDATE DRAFT</p><h3>{draft.mode === "create" ? "Link Evidence" : draft.mode === "revise" ? "Revise Evidence Link" : "Retract Evidence Link"}</h3></div><Badge value="PENDING REVIEW" /></div>
        {draft.mode === "retract" ? <DescriptionList columns={3} items={[{ label: "Impact", value: draft.impactId ?? "—" }, { label: "Expected Version", value: `v${draft.expectedVersion ?? 1}` }, { label: "Current Link", value: `${draft.stance} · ${draft.sourceKey}` }]} /> : <div className="research-form-grid">
          <FormField label="Thesis Revision and Assumption" required><Select disabled={busy} required aria-required="true" value={draft.targetKey} onChange={(event) => setDraft({ ...draft, targetKey: event.target.value })}><option value="">Choose the reviewed target</option>{effectiveSnapshot?.targets.map((target) => <option key={`${target.thesis_revision_id}:${target.assumption_id}`} value={`${target.thesis_revision_id}:${target.assumption_id}`}>{targetLabel(target)}</option>)}</Select></FormField>
          <FormField label="Evidence Source" required><Select disabled={busy} required aria-required="true" value={draft.sourceKey} onChange={(event) => setDraft({ ...draft, sourceKey: event.target.value })}><option value="">Choose a source</option>{sourceOptions.map((source) => <option key={sourceKey(source.source)} value={sourceKey(source.source)}>{sourceLabel(source)}</option>)}</Select></FormField>
          <FormField label="Stance" required><Select disabled={busy} required aria-required="true" value={draft.stance} onChange={(event) => setDraft({ ...draft, stance: event.target.value as ImpactDraft["stance"] })}><option value="uncertain">Uncertain</option><option value="supports">Supports</option><option value="contradicts">Contradicts</option></Select></FormField>
          {selectedTarget() && <div className="research-field"><span>Selected Target</span><p>{targetLabel(selectedTarget() as EvidenceTarget)}</p><small>Pinning this revision records your interpretation; it does not change the Thesis.</small></div>}
        </div>}
        <FormField label="Rationale" required><Textarea disabled={busy} required aria-required="true" rows={4} value={draft.rationale} onChange={(event) => setDraft({ ...draft, rationale: event.target.value })} placeholder="Explain why this source changes the interpretation of this exact Thesis assumption." /></FormField>
        {draft.mode !== "retract" && selectedSource() && <p className="muted">Source scope: {sourceLabel(selectedSource() as EvidenceSource)}. Recorded {formatDate((selectedSource() as EvidenceSource).recorded_at)}.</p>}
        <FormActions className="research-form-actions"><ActionButton onClick={() => { void submitImpact(); }} busy={busy} disabled={readLoading || Boolean(readError) || (sourceCoverageUnavailable && draft.mode !== "retract")}>Propose Candidate</ActionButton><Button type="button" disabled={busy} onClick={() => { setDraft(null); setResearchOrigin(null); }}>Cancel</Button></FormActions>
      </section>}
      <div className="research-selection-subhead"><strong>Saved Evidence Links · {effectiveSnapshot.impacts.filter((impact) => impact.state === "ACTIVE").length + effectiveSnapshot.impacts.filter((impact) => impact.state === "RETRACTED").length}</strong></div>
      {effectiveSnapshot.impacts.filter((impact) => impact.state === "ACTIVE").length === 0 && effectiveSnapshot.impacts.filter((impact) => impact.state === "RETRACTED").length === 0 ? <Empty>No evidence links have been confirmed for this Research Subject.</Empty> : <div className="research-selection-list">{[...effectiveSnapshot.impacts.filter((impact) => impact.state === "ACTIVE"), ...effectiveSnapshot.impacts.filter((impact) => impact.state === "RETRACTED")].map((impact) => {
        const target = effectiveSnapshot.targets.find((item) => impactTargetKey(impact) === `${item.thesis_revision_id}:${item.assumption_id}`);
        return <article className="research-selection-item" key={`${impact.impact_id}:${impact.version}`}><div><strong>{target?.thesis_title ?? "Historical Thesis revision"} · {impact.stance}</strong><small>{target ? `v${target.revision_no} · ${target.statement}` : `Thesis revision ${impact.thesis_revision_id} · assumption ${impact.assumption_id}`}</small><small>{impact.source.kind} · {impact.source.source_id}{impact.source.monitor_id ? ` · monitor ${impact.source.monitor_id}` : ""}{impact.source.rule_code ? ` · ${impact.source.rule_code}` : ""}</small><p>{impact.rationale}</p><small>v{impact.version} · confirmed {formatDate(impact.confirmed_at)}{impact.confirmed_by ? ` by ${impact.confirmed_by}` : ""}</small>{impact.source_available === false && <span className="muted" role="status">Historical source is currently unavailable; the immutable link is retained.</span>}{effectiveSnapshot.impact_history.some((row) => row.impact_id === impact.impact_id && row.version < impact.version) && <Disclosure title="Review History"><ul>{effectiveSnapshot.impact_history.filter((row) => row.impact_id === impact.impact_id).map((row) => <li key={row.version}><strong>v{row.version} · {row.state} · {row.stance.toUpperCase()}</strong><p>{row.rationale}</p><small>{row.source.kind} · {row.source.source_id}{row.source.monitor_id ? ` · ${row.source.monitor_id} / ${row.source.rule_code}` : ""} · Thesis revision {row.thesis_revision_id} · Assumption {row.assumption_id}</small><small>{formatDate(row.confirmed_at)} · {row.confirmed_by}</small></li>)}</ul></Disclosure>}</div><div className="research-selection-actions"><Badge value={impact.state} />{impact.state === "ACTIVE" && <><Button type="button" disabled={Boolean(draft) || busy} onClick={() => startRevise(impact)}>Revise</Button><Button type="button" disabled={Boolean(draft) || busy} onClick={() => startRetract(impact)}>Retract</Button></>}</div></article>;
      })}</div>}
      {["PARTIAL", "UNAVAILABLE"].includes(effectiveSnapshot.coverage.impact_history) && <p role="status">Review history is incomplete in this view; stored versions are retained.</p>}
      {effectiveSnapshot.impacts_has_more && <p className="muted" role="status">Historical evidence links continue beyond this bounded view.</p>}
    </>}
  </Card>;
}
