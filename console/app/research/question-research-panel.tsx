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
  Input,
  Select,
  Textarea,
  TextLink,
  formatDate,
} from "../components/ui";
import { getJson } from "../lib/api";
import { offerQuestionResearch, type PreparedQuestionResearch } from "../lib/question-research";
import { asObject, textDash as text } from "../lib/coerce";
import { applyResearchQuestionTemplate, getResearchQuestionTemplate, RESEARCH_QUESTION_TEMPLATES } from "../lib/research-question-templates";
import type { EvidenceSourceRef } from "./evidence-impact-panel";

type Dict = Record<string, unknown>;

type EvidenceSource = {
  source: EvidenceSourceRef;
  title: string;
  fact_at: string | null;
  recorded_at: string | null;
  origin: string;
  warning_codes: string[];
};

type Decision = { decision_id: string; decided_at: string; title: string };

type EvidenceLinks = {
  sources: EvidenceSource[];
  targets?: Array<{ thesis_revision_id: string; assumption_id: string; statement: string }>;
  sources_has_more: boolean;
  decisions: Decision[];
  as_of: string;
  coverage: { sources: string };
  question_history: QuestionHistory[];
};

type QuestionHistory = {
  candidate_id: string;
  question_id: string;
  reviewed_at: string | null;
  reviewed_by: string | null;
  action: string;
  payload: Dict;
};

type Question = {
  question_id: string;
  subject_id?: string;
  text: string;
  status: string;
  version: number;
  evidence_requirements: string[];
  due_at: string | null;
  progress_note: string | null;
  source_refs: EvidenceSourceRef[];
  decision_ids: string[];
  answer_summary?: string | null;
  answered_at?: string | null;
  closed_reason?: string | null;
  asked_at?: string | null;
};

type QuestionMode = "create" | "update_research" | "answer" | "link_decision" | "close";

type QuestionDraft = {
  mode: QuestionMode;
  questionId: string | null;
  expectedVersion: number | null;
  templateId: string;
  text: string;
  evidenceRequirements: string;
  dueAt: string;
  progressNote: string;
  answerSummary: string;
  closedReason: string;
  sourceKeys: string[];
  decisionIds: string[];
  criteriaMet: boolean;
  attemptKey: string;
  submittedFingerprint?: string;
};

type Props = {
  subjectId: string;
  questions: Dict[];
  onWrite: (toolName: string, request: Dict, confirmation?: string) => Promise<Dict>;
  onRefresh: () => void;
  busy: boolean;
  linksRead?: import("./evidence-impact-panel").SharedEvidenceLinksRead;
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

function questionFrom(value: Dict, subjectId: string): Question {
  return {
    question_id: text(value.question_id, ""),
    subject_id: text(value.subject_id, subjectId),
    text: text(value.text, "Untitled research question"),
    status: text(value.status, "OPEN").toUpperCase(),
    version: typeof value.version === "number" ? value.version : 1,
    evidence_requirements: Array.isArray(value.evidence_requirements) ? value.evidence_requirements.filter((item): item is string => typeof item === "string") : [],
    due_at: typeof value.due_at === "string" ? value.due_at : null,
    progress_note: typeof value.progress_note === "string" ? value.progress_note : null,
    source_refs: Array.isArray(value.source_refs) ? value.source_refs.map(sourceRef).filter((item): item is EvidenceSourceRef => item !== null) : [],
    decision_ids: Array.isArray(value.decision_ids) ? value.decision_ids.filter((item): item is string => typeof item === "string") : [],
    answer_summary: typeof value.answer_summary === "string" ? value.answer_summary : null,
    answered_at: typeof value.answered_at === "string" ? value.answered_at : null,
    closed_reason: typeof value.closed_without_answer_reason === "string" ? value.closed_without_answer_reason : null,
    asked_at: typeof value.asked_at === "string" ? value.asked_at : null,
  };
}

function normalizeSnapshot(value: unknown): EvidenceLinks {
  const data = asObject(asObject(value).data);
  return {
    as_of: text(data.as_of, ""),
    sources: Array.isArray(data.sources) ? data.sources.map((item) => {
      const sourceItem = asObject(item);
      return {
        source: sourceRef(sourceItem.source) ?? { kind: "EVIDENCE", source_id: "" },
        title: text(sourceItem.title, "Untitled source"),
        fact_at: typeof sourceItem.fact_at === "string" ? sourceItem.fact_at : null,
        recorded_at: typeof sourceItem.recorded_at === "string" ? sourceItem.recorded_at : null,
        origin: text(sourceItem.origin, "Unknown origin"),
        warning_codes: Array.isArray(sourceItem.warning_codes) ? sourceItem.warning_codes.filter((code): code is string => typeof code === "string") : [],
      };
    }).filter((item) => item.source.source_id) : [],
    sources_has_more: data.sources_has_more === true,
    targets: Array.isArray(data.targets) ? data.targets as EvidenceLinks["targets"] : [],
    decisions: Array.isArray(data.decisions) ? data.decisions.map((item) => {
      const decision = asObject(item);
      return { decision_id: text(decision.decision_id, ""), decided_at: text(decision.decided_at, ""), title: text(decision.title, "Untitled Decision") };
    }).filter((item) => item.decision_id) : [],
    coverage: { sources: text(asObject(data.coverage).sources, "UNKNOWN") },
    question_history: Array.isArray(data.question_history) ? data.question_history.map((item) => {
      const history = asObject(item);
      return {
        candidate_id: text(history.candidate_id, ""),
        question_id: text(history.question_id, ""),
        reviewed_at: typeof history.reviewed_at === "string" ? history.reviewed_at : null,
        reviewed_by: typeof history.reviewed_by === "string" ? history.reviewed_by : null,
        action: text(history.action, "UPDATE"),
        payload: asObject(history.payload),
      };
    }).filter((item) => item.candidate_id && item.question_id) : [],
  };
}

function historicalSource(ref: EvidenceSourceRef): EvidenceSource {
  return { source: ref, title: "Historical source linked to this question", fact_at: null, recorded_at: null, origin: "Historical immutable reference", warning_codes: ["SOURCE_UNAVAILABLE"] };
}

function toDateInput(value: string | null): string {
  if (!value) return "";
  return value.slice(0, 10);
}

function toDueAt(value: string): string | null {
  return value.trim() ? `${value.trim()}T23:59:59Z` : null;
}

function emptyDraft(subjectId: string): QuestionDraft {
  return { mode: "create", questionId: null, expectedVersion: null, templateId: "", text: "", evidenceRequirements: "", dueAt: "", progressNote: "", answerSummary: "", closedReason: "", sourceKeys: [], decisionIds: [], criteriaMet: false, attemptKey: attemptKey("open-question", subjectId) };
}

function historyReferences(payload: Dict): string {
  const refs = Array.isArray(payload.source_refs) ? payload.source_refs.map(sourceRef).filter((item): item is EvidenceSourceRef => item !== null).map((item) => `${item.kind}:${item.source_id}`) : [];
  const decisions = Array.isArray(payload.decision_ids) ? payload.decision_ids.filter((item): item is string => typeof item === "string") : [];
  return [...(refs.length > 0 ? [`Sources · ${refs.join(" · ")}`] : []), ...(decisions.length > 0 ? [`Decisions · ${decisions.join(" · ")}`] : [])].join(" · ");
}

export function QuestionResearchPanel({ subjectId, questions: questionValues, onWrite, onRefresh, busy, linksRead }: Props) {
  const questions = useMemo(() => questionValues.map((item) => questionFrom(item, subjectId)).filter((item) => item.question_id), [questionValues, subjectId]);
  const [researchQuestion, setResearchQuestion] = useState<Question | null>(null);
  const [researchTarget, setResearchTarget] = useState("");
  const [preparing, setPreparing] = useState(false);
  const [preparationMessage, setPreparationMessage] = useState<string | null>(null);
  async function prepareResearch() {
    if (!researchQuestion) return;
    setPreparing(true); setPreparationMessage(null);
    const query = new URLSearchParams({ version: String(researchQuestion.version) });
    if (researchTarget) { const [revision, assumption] = researchTarget.split("|"); query.set("thesis_revision_id", revision); query.set("assumption_id", assumption); }
    try {
      const response = await getJson(`/api/research/${encodeURIComponent(subjectId)}/questions/${encodeURIComponent(researchQuestion.question_id)}/research-context?${query}`) as { data: PreparedQuestionResearch };
      if (response.data.context.subject_id !== subjectId || response.data.context.question_id !== researchQuestion.question_id) throw new Error("Question scope changed.");
      if (!await offerQuestionResearch(response.data)) throw new Error("Copilot has an existing draft or active turn. Finish or clear it before preparing this question.");
      setPreparationMessage("Question and cutoff prepared in Copilot. Review the prompt and budget, then press Send to start Research.");
      setResearchQuestion(null);
    } catch (error) { setPreparationMessage(error instanceof Error ? error.message : "Question could not be prepared."); }
    finally { setPreparing(false); }
  }
  const [snapshot, setSnapshot] = useState<EvidenceLinks | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [draft, setDraft] = useState<QuestionDraft | null>(null);
  const [retroSeed, setRetroSeed] = useState<{ runId: string; findingKey: string; title: string; detail: string } | null>(null);
  useEffect(() => {
    setRetroSeed(null);
    const params = new URLSearchParams(window.location.search);
    const runId = params.get("retro_run_id"); const findingKey = params.get("retro_finding_key");
    if (!runId || !findingKey) return;
    const controller = new AbortController();
    void getJson(`/api/retro/${encodeURIComponent(runId)}/workflow`, controller.signal).then(response => {
      const data = asObject(asObject(response).data);
      const findings = Array.isArray(data.findings) ? data.findings.map(asObject) : [];
      const finding = findings.find(f => f.finding_key === findingKey);
      const subjects = finding && Array.isArray(finding.subjects) ? finding.subjects.map(asObject) : [];
      if (!controller.signal.aborted && finding && subjects.some(s => s.subject_id === subjectId)) setRetroSeed({ runId, findingKey, title: text(finding.title), detail: text(finding.detail) });
    }).catch(() => { if (!controller.signal.aborted) setError("Retro source unavailable; no question draft was prepared."); });
    return () => controller.abort();
  }, [subjectId]);
  const [writeError, setWriteError] = useState<string | null>(null);
  const [writeSuccess, setWriteSuccess] = useState<string | null>(null);

  useEffect(() => {
    if (linksRead) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    void getJson(`/api/research/${encodeURIComponent(subjectId)}/evidence-links`, controller.signal)
      .then((response) => { if (!controller.signal.aborted) setSnapshot(normalizeSnapshot(response)); })
      .catch((cause) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Research source links could not be read."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [linksRead, subjectId, reload]);

  useEffect(() => {
    setDraft(null);
    setWriteError(null);
    setWriteSuccess(null);
  }, [subjectId]);

  const sourceOptions = useMemo(() => {
    const sharedSnapshot = linksRead?.data == null ? null : normalizeSnapshot(linksRead.data);
    const currentSnapshot = linksRead ? sharedSnapshot : snapshot;
    const values = [...(currentSnapshot?.sources ?? [])];
    const seen = new Set(values.map((item) => sourceKey(item.source)));
    for (const question of questions) for (const ref of question.source_refs) {
      const key = sourceKey(ref);
      if (!seen.has(key)) values.push(historicalSource(ref));
    }
    return values;
  }, [linksRead, questions, snapshot]);

  const sharedSnapshot = linksRead?.data == null ? null : normalizeSnapshot(linksRead.data);
  const currentSnapshot = linksRead ? sharedSnapshot : snapshot;
  const readLoading = linksRead?.loading ?? loading;
  const readError = linksRead?.error ?? error;

  function startCreate() {
    setWriteError(null);
    setWriteSuccess(null);
    setDraft(emptyDraft(subjectId));
  }

  function startExisting(mode: Exclude<QuestionMode, "create">, question: Question) {
    setWriteError(null);
    setWriteSuccess(null);
    setDraft({
      mode,
      questionId: question.question_id,
      expectedVersion: question.version,
      templateId: "",
      text: question.text,
      evidenceRequirements: question.evidence_requirements.join("\n"),
      dueAt: toDateInput(question.due_at),
      progressNote: question.progress_note ?? "",
      answerSummary: question.answer_summary ?? "",
      closedReason: "",
      sourceKeys: question.source_refs.map(sourceKey),
      decisionIds: [...question.decision_ids],
      criteriaMet: false,
      attemptKey: attemptKey(`open-question-${mode}`, subjectId),
    });
  }

  function applyTemplate(replaceDraft: boolean) {
    if (!draft || draft.mode !== "create") return;
    const template = getResearchQuestionTemplate(draft.templateId);
    if (!template) return;
    const hasAuthoredContent = Boolean(draft.text.trim() || draft.evidenceRequirements.trim());
    if (hasAuthoredContent && !replaceDraft) {
      setWriteError("This draft already has question content. Choose Replace Draft with Template to overwrite only the question and evidence requirements.");
      return;
    }
    const fields = applyResearchQuestionTemplate(template);
    setWriteError(null);
    setDraft({ ...draft, text: fields.text, evidenceRequirements: fields.evidenceRequirements });
  }

  function toggleSource(key: string) {
    if (!draft) return;
    setDraft({ ...draft, sourceKeys: draft.sourceKeys.includes(key) ? draft.sourceKeys.filter((item) => item !== key) : [...draft.sourceKeys, key] });
  }

  function toggleDecision(id: string) {
    if (!draft) return;
    setDraft({ ...draft, decisionIds: draft.decisionIds.includes(id) ? draft.decisionIds.filter((item) => item !== id) : [...draft.decisionIds, id] });
  }

  function selectedSources(): EvidenceSource[] {
    return sourceOptions.filter((source) => draft?.sourceKeys.includes(sourceKey(source.source)));
  }

  function selectedRefs(): EvidenceSourceRef[] {
    return selectedSources().map((source) => source.source).sort((a, b) => sourceKey(a).localeCompare(sourceKey(b)));
  }

  async function submitQuestion() {
    if (!draft || readLoading || readError) return;
    setWriteError(null);
    setWriteSuccess(null);
    const requirements = draft.evidenceRequirements.split(/\n/).map((item) => item.trim()).filter(Boolean);
    const common: Dict = { kind: "open_question", action: draft.mode };
    if (draft.mode === "create") {
      if (!draft.text.trim()) { setWriteError("Question text is required."); return; }
      common.text = draft.text.trim();
      common.evidence_requirements = requirements;
      common.due_at = toDueAt(draft.dueAt);
      common.progress_note = draft.progressNote.trim() || null;
      common.source_refs = selectedRefs();
    } else if (!draft.questionId || draft.expectedVersion === null) {
      setWriteError("This question version is no longer available. Refresh and try again.");
      return;
    } else if (draft.mode === "update_research") {
      common.question_id = draft.questionId;
      common.expected_version = draft.expectedVersion;
      common.evidence_requirements = requirements;
      common.due_at = toDueAt(draft.dueAt);
      common.progress_note = draft.progressNote.trim() || null;
      common.source_refs = selectedRefs();
    } else if (draft.mode === "answer") {
      if (!draft.answerSummary.trim()) { setWriteError("An answer summary is required."); return; }
      if (requirements.length > 0 && !draft.criteriaMet) { setWriteError("Confirm that the explicit answer criteria are met before proposing an answer."); return; }
      if (requirements.length > 0 && selectedRefs().length === 0) { setWriteError("At least one source is required to answer a tracked question."); return; }
      common.question_id = draft.questionId;
      common.expected_version = draft.expectedVersion;
      common.answer_summary = draft.answerSummary.trim();
      common.source_refs = selectedRefs();
      common.criteria_met = draft.criteriaMet;
    } else if (draft.mode === "link_decision") {
      const existingLinks = questions.find((question) => question.question_id === draft.questionId)?.decision_ids ?? [];
      if (!draft.decisionIds.some((id) => !existingLinks.includes(id))) { setWriteError("Select at least one additional Decision to link."); return; }
      common.question_id = draft.questionId;
      common.expected_version = draft.expectedVersion;
      common.decision_ids = draft.decisionIds;
    } else {
      if (!draft.closedReason.trim()) { setWriteError("A close reason is required."); return; }
      common.question_id = draft.questionId;
      common.expected_version = draft.expectedVersion;
      common.closed_reason = draft.closedReason.trim();
    }
    const rationale = draft.mode === "answer" ? draft.answerSummary.trim() : draft.mode === "close" ? draft.closedReason.trim() : draft.progressNote.trim() || draft.text.trim() || `Research question ${draft.questionId ?? "created"}`;
    const fingerprint = JSON.stringify(common);
    const submissionKey = draft.submittedFingerprint && draft.submittedFingerprint !== fingerprint ? attemptKey("question-research", subjectId) : draft.attemptKey;
    setDraft({ ...draft, attemptKey: submissionKey, submittedFingerprint: fingerprint });
    try {
      await onWrite("research_judgment_propose", {
        operation: "research_state",
        case_id: subjectId,
        payload: common,
        proposed_by: "user",
        proposed_by_rationale: rationale,
        idempotency_key: submissionKey,
      }, "research_judgment_propose");
      setDraft(null);
      setWriteSuccess("Candidate proposal created. Review it in Pending Candidates before confirmation.");
      onRefresh();
      if (linksRead) linksRead.refresh(); else setReload((value) => value + 1);
    } catch (cause) {
      setWriteError(cause instanceof Error ? cause.message : "Research question proposal failed.");
    }
  }

  const openCount = questions.filter((question) => question.status === "OPEN").length;
  return <Card id="research-section-questions" className="research-context-card" kicker="INVESTIGATION" title="Research Questions" description="Turn an unresolved question into explicit evidence requirements, progress, and a reviewed conclusion.">
    <div className="research-form-actions"><ActionButton onClick={startCreate} disabled={Boolean(draft)}>Add Research Question</ActionButton><Button type="button" disabled={readLoading} onClick={() => { if (linksRead) linksRead.refresh(); else setReload((value) => value + 1); }}>{readLoading ? "Refreshing…" : "Refresh Sources"}</Button></div>
    <DescriptionList columns={3} items={[{ label: "Open", value: openCount }, { label: "Answered", value: questions.filter((question) => question.status === "ANSWERED").length }, { label: "Source Timestamp", value: formatDate(currentSnapshot?.as_of) }]} />
    {readLoading && <p role="status">Loading durable source and Decision choices…</p>}
    {readError && <ErrorNote role="alert">Source and Decision choices could not be read. Question writes are blocked until this scope is available. {readError}</ErrorNote>}
    {currentSnapshot?.coverage.sources === "UNAVAILABLE" && <ErrorNote role="alert">Source coverage is unavailable. Source-dependent question proposals are disabled until it recovers.</ErrorNote>}
    {writeSuccess && <div className="inline-success" role="status">{writeSuccess}</div>}
    {writeError && <ErrorNote role="alert">{writeError}</ErrorNote>}
    {retroSeed && <section aria-label="Retro Finding Source"><p>{retroSeed.title} · {retroSeed.detail}</p><Button disabled={busy || draft !== null} onClick={() => setDraft({ ...emptyDraft(subjectId), text: `What evidence will resolve: ${retroSeed.title}?`, evidenceRequirements: "Identify the facts needed to verify the finding and the next review outcome.", progressNote: `Follow-up source: ${retroSeed.runId} / ${retroSeed.findingKey}. This is a review task, not evidence of prior adherence.` })}>Use Retro Finding in Question Draft</Button><p>Review the question and deadline, propose, then explicitly confirm. The Retro finding remains unchanged.</p></section>}
    {draft && <section className="continuity-editor" aria-label={`${draft.mode} research question`}>
      <div className="continuity-heading"><div><p className="card-kicker">CANDIDATE DRAFT</p><h3>{draft.mode === "create" ? "New Research Question" : draft.mode === "update_research" ? "Update Research Plan" : draft.mode === "answer" ? "Answer Research Question" : draft.mode === "link_decision" ? "Link Decisions" : "Close Research Question"}</h3></div><Badge value="PENDING REVIEW" /></div>
      {draft.mode === "create" && <div className="research-form-grid" aria-label="Research Question Templates"><FormField label="Question Template"><Select aria-label="Question Template" disabled={busy} value={draft.templateId} onChange={(event) => setDraft({ ...draft, templateId: event.target.value })}><option value="">Custom question</option>{RESEARCH_QUESTION_TEMPLATES.map((template) => <option value={template.id} key={template.id}>{template.title} · v{template.version}</option>)}</Select></FormField><div className="research-field"><span>Template action</span><Button type="button" disabled={busy || !draft.templateId} onClick={() => { void applyTemplate(Boolean(draft.text.trim() || draft.evidenceRequirements.trim())); }}>{draft.text.trim() || draft.evidenceRequirements.trim() ? "Replace Draft with Template" : "Apply Template"}</Button><p className="card-note">Fills the question and evidence requirements. Review and edit before proposing.</p></div></div>}
      {draft.mode === "create" ? <FormField label="Question" required><Textarea disabled={busy} required aria-required="true" rows={3} value={draft.text} onChange={(event) => setDraft({ ...draft, text: event.target.value })} placeholder="What must be known before this Thesis can change?" /></FormField> : <div className="research-field"><span>Question</span><p>{draft.text}</p></div>}
      {(draft.mode === "create" || draft.mode === "update_research") && <div className="research-form-grid"><FormField label="Evidence Requirements"><Textarea disabled={busy} rows={3} value={draft.evidenceRequirements} onChange={(event) => setDraft({ ...draft, evidenceRequirements: event.target.value })} placeholder="One requirement per line" /></FormField><FormField label="Deadline"><Input disabled={busy} type="date" value={draft.dueAt} onChange={(event) => setDraft({ ...draft, dueAt: event.target.value })} /></FormField><FormField label="Progress Note" className="research-field-wide"><Textarea disabled={busy} rows={3} value={draft.progressNote} onChange={(event) => setDraft({ ...draft, progressNote: event.target.value })} placeholder="What has been checked so far?" /></FormField></div>}
      {(draft.mode === "create" || draft.mode === "update_research" || draft.mode === "answer") && <fieldset className="research-field" aria-required={draft.mode === "answer" && draft.evidenceRequirements.split(/\n/).some((item) => item.trim()) || undefined}><legend><span>{draft.mode === "answer" && draft.evidenceRequirements.split(/\n/).some((item) => item.trim()) ? <b className="required-mark" aria-hidden="true">*</b> : null}Source References</span></legend>{currentSnapshot?.sources.length ? <div className="research-selection-list">{sourceOptions.map((source) => { const key = sourceKey(source.source); return <label className="research-selection-item" key={key}><span><Input disabled={busy} type="checkbox" checked={draft.sourceKeys.includes(key)} onChange={() => toggleSource(key)} /><strong>{source.title}</strong><small>{source.source.kind} · {source.source.source_id}{source.source.monitor_id ? ` · monitor ${source.source.monitor_id}` : ""}</small></span></label>; })}</div> : <p className="muted">No selectable sources are available.</p>}{currentSnapshot?.sources_has_more && <small className="muted">The source list is bounded; historical IDs on this question remain available.</small>}</fieldset>}
      {draft.mode === "answer" && <><FormField label="Answer Summary" required><Textarea disabled={busy} required aria-required="true" rows={4} value={draft.answerSummary} onChange={(event) => setDraft({ ...draft, answerSummary: event.target.value })} placeholder="State the conclusion and what the evidence means." /></FormField>{draft.evidenceRequirements.split(/\n/).some((item) => item.trim()) && <label className="research-field"><span><b className="required-mark" aria-hidden="true">*</b>Answer Criteria</span><span><Input disabled={busy} type="checkbox" required checked={draft.criteriaMet} onChange={(event) => setDraft({ ...draft, criteriaMet: event.target.checked })} /> I reviewed the stated evidence requirements and the selected sources meet them.</span></label>}</>}
      {draft.mode === "link_decision" && <fieldset className="research-field" aria-required="true"><legend><span><b className="required-mark" aria-hidden="true">*</b>Decisions</span></legend><p className="card-note">Existing links are retained. Select additional Decisions recorded after the answer.</p>{currentSnapshot?.decisions.length ? <div className="research-selection-list">{currentSnapshot.decisions.map((decision) => <label className="research-selection-item" key={decision.decision_id}><span><Input disabled={busy || (questions.find((question) => question.question_id === draft.questionId)?.decision_ids.includes(decision.decision_id) ?? false)} type="checkbox" checked={draft.decisionIds.includes(decision.decision_id)} onChange={() => toggleDecision(decision.decision_id)} /><strong>{decision.title}</strong><small>{formatDate(decision.decided_at)} · {decision.decision_id}</small></span></label>)}</div> : <p className="muted">No Decisions are available to link.</p>}</fieldset>}
      {draft.mode === "close" && <FormField label="Close Reason" required><Textarea disabled={busy} required aria-required="true" rows={3} value={draft.closedReason} onChange={(event) => setDraft({ ...draft, closedReason: event.target.value })} placeholder="Explain why this question is no longer actionable." /></FormField>}
      <FormActions className="research-form-actions"><ActionButton onClick={() => { void submitQuestion(); }} busy={busy} disabled={readLoading || Boolean(readError) || (currentSnapshot?.coverage.sources === "UNAVAILABLE" && (draft.mode === "answer" || draft.sourceKeys.length > 0))}>Propose Candidate</ActionButton><Button type="button" disabled={busy} onClick={() => setDraft(null)}>Cancel</Button></FormActions>
    </section>}
    {preparationMessage && <p role="status">{preparationMessage}</p>}
    {researchQuestion && <section aria-label="Prepare Question Research"><h3>{researchQuestion.text}</h3><p>Version {researchQuestion.version}. Preparing reads durable context only; Copilot Send starts the model.</p><FormField label="Research Assumption"><Select value={researchTarget} disabled={preparing} onChange={(event) => setResearchTarget(event.target.value)}><option value="">No specific assumption</option>{currentSnapshot?.targets?.map((target) => <option key={`${target.thesis_revision_id}|${target.assumption_id}`} value={`${target.thesis_revision_id}|${target.assumption_id}`}>{target.statement}</option>)}</Select></FormField><Button disabled={preparing} onClick={() => { void prepareResearch(); }}>Prepare in Copilot</Button><Button disabled={preparing} onClick={() => setResearchQuestion(null)}>Cancel Research Preparation</Button></section>}
    <div className="research-selection-subhead"><strong>Tracked Questions · {questions.length}</strong></div>
    {questions.length === 0 ? <Empty>No open research questions.</Empty> : <div className="research-selection-list">{questions.map((question) => <article id={`question-${question.question_id}`} className="research-selection-item" key={question.question_id}><div><strong>{question.text}</strong><small>v{question.version} · asked {formatDate(question.asked_at)}{question.due_at ? ` · due ${formatDate(question.due_at)}` : ""}</small>{question.evidence_requirements.length > 0 && <><small>Evidence requirements</small><ul>{question.evidence_requirements.map((requirement) => <li key={requirement}>{requirement}</li>)}</ul></>}{question.progress_note && <p><strong>Progress</strong> · {question.progress_note}</p>}{question.answer_summary && <p><strong>Answer</strong> · {question.answer_summary}</p>}{question.source_refs.length > 0 && <small>Sources · {question.source_refs.map((ref) => `${ref.kind}:${ref.source_id}`).join(" · ")}</small>}{question.decision_ids.length > 0 && <div><small>User-linked Decisions</small><ul>{question.decision_ids.map((id) => <li key={id}><TextLink href={`/decision-workbench?subject_id=${encodeURIComponent(subjectId)}`}>{currentSnapshot?.decisions.find((decision) => decision.decision_id === id)?.title ?? "Historical Decision"}</TextLink><small> · {id}</small></li>)}</ul></div>}{question.closed_reason && <p><strong>Closed</strong> · {question.closed_reason}</p>}</div><div className="research-selection-actions"><Badge value={question.status} />{question.status === "OPEN" && <><Button disabled={Boolean(draft) || busy || preparing} onClick={() => { setResearchQuestion(question); setResearchTarget(""); setPreparationMessage(null); }}>Research with Copilot</Button><Button type="button" disabled={Boolean(draft) || busy} onClick={() => startExisting("update_research", question)}>Update Plan</Button><Button type="button" disabled={Boolean(draft) || busy} onClick={() => startExisting("answer", question)}>Answer</Button></>}{question.status === "ANSWERED" && <Button type="button" disabled={Boolean(draft) || busy} onClick={() => startExisting("link_decision", question)}>Link Decision</Button>}{question.status === "OPEN" && <Button type="button" disabled={Boolean(draft) || busy} onClick={() => startExisting("close", question)}>Close</Button>}</div></article>)}</div>}
    {currentSnapshot?.question_history.length ? <Disclosure className="research-question-history" variant="compact" title={`Question History · ${currentSnapshot.question_history.length}`}><div className="research-timeline">{currentSnapshot.question_history.map((item) => <article key={item.candidate_id}><div><strong>{item.action.replaceAll("_", " ").toUpperCase()}</strong><time>{formatDate(item.reviewed_at)}</time></div><small>Question {item.question_id} · Candidate {item.candidate_id} · reviewed by {item.reviewed_by ?? "unknown"}</small><p>{typeof item.payload.answer_summary === "string" ? item.payload.answer_summary : typeof item.payload.progress_note === "string" ? item.payload.progress_note : typeof item.payload.closed_reason === "string" ? item.payload.closed_reason : "Confirmed research question update."}</p>{historyReferences(item.payload) && <small>{historyReferences(item.payload)}</small>}</article>)}</div></Disclosure> : null}
  </Card>;
}
