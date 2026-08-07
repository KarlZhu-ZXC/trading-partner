"use client";

import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import Link from "next/link";
import {
  ActionButton,
  Badge,
  Card,
  DataBoundary,
  Empty,
  RefreshButton,
  displayJson,
  formatDate,
  shortId,
} from "../components/ui";
import { ConsoleShell } from "../components/console-shell";
import { envelopeData, listOf, postApi, useApi } from "../lib/api";
import { ResearchContinuity } from "./research-continuity";

type Dict = Record<string, unknown>;
type SubjectAggregate = { subject?: Dict; state?: Dict };
type MonitorAggregate = { monitor?: Dict; [key: string]: unknown };

const SUBJECT_STATUSES = ["draft", "active", "strengthened", "weakened", "invalidated", "archived"];
const SUBJECT_TYPES = ["company", "theme", "macro", "catalyst", "portfolio_concern"];
// SUB requires a parent_thesis_id selector; keep this first editor bounded to
// roles that are valid without an additional relationship field.
const THESIS_ROLES = ["primary", "competitor", "bear"];
const THESIS_STATUSES = ["draft", "active", "strengthened", "weakened", "invalidated", "archived"];
const CONFIDENCE_BANDS = ["low", "medium", "high"];
const RATINGS = ["avoid", "watch", "speculative_buy", "buy", "sell", "hold"];
const INVALIDATION_SEVERITIES = ["soft", "hard"];

function text(value: unknown, fallback = "—"): string {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value !== "string") return fallback;
  return value.trim() || fallback;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function stateData(item: SubjectAggregate): Dict | null {
  return envelopeData<Dict>(item.state);
}

function stateError(state: Dict | undefined): string | null {
  if (!state || state.ok !== false) return null;
  const first = Array.isArray(state.errors) ? state.errors[0] as Dict | undefined : undefined;
  return first ? `${text(first.code, "RESEARCH_STATE_READ_FAILED")} · ${text(first.message, "无法读取 Thesis")}` : "Research state 读取失败，未返回错误详情。";
}

function latestRevisionByThesis(revisions: Dict[]): Map<string, Dict> {
  return new Map(revisions.map((revision) => [String(revision.thesis_id ?? ""), revision]));
}

function subjectSearchText(item: SubjectAggregate): string {
  const researchSubject = item.subject ?? {};
  const state = stateData(item) ?? {};
  const theses = listOf<Dict>(state, "theses");
  const revisions = listOf<Dict>(state, "latest_revisions");
  return [
    researchSubject.title,
    researchSubject.summary,
    researchSubject.primary_instrument_id,
    ...stringList(researchSubject.topic_tags),
    ...theses.flatMap((thesis) => [thesis.title, thesis.role, thesis.status]),
    ...revisions.flatMap((revision) => [revision.statement, revision.rating, revision.confidence_band]),
  ].map(String).join(" ").toLocaleLowerCase();
}

function idempotencyKey(prefix: string): string {
  return `console-${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function resultEnvelope(response: Dict): Dict {
  const result = response.result;
  return result && typeof result === "object" ? result as Dict : response;
}

function assertEnvelopeSuccess(response: Dict): Dict {
  const envelope = resultEnvelope(response);
  if (envelope.ok === false) {
    const first = Array.isArray(envelope.errors) ? envelope.errors[0] as Dict | undefined : undefined;
    throw new Error(`${text(first?.code, "WRITE_FAILED")} · ${text(first?.message, "本地写入失败")}`);
  }
  return envelope;
}

type SubjectDraft = {
  subjectType: string;
  title: string;
  summary: string;
  instrument: string;
  tags: string;
  linkedSubjectIds: string;
};

const EMPTY_SUBJECT_DRAFT: SubjectDraft = {
  subjectType: "company",
  title: "",
  summary: "",
  instrument: "",
  tags: "",
  linkedSubjectIds: "",
};

function subjectDraftFrom(researchSubject: Dict): SubjectDraft {
  return {
    subjectType: text(researchSubject.subject_type, "company"),
    title: text(researchSubject.title, ""),
    summary: text(researchSubject.summary, ""),
    instrument: text(researchSubject.primary_instrument_id, ""),
    tags: stringList(researchSubject.topic_tags).join(", "),
    linkedSubjectIds: stringList(researchSubject.linked_subject_ids).join(", "),
  };
}

function splitList(value: string): string[] {
  return value.split(/[\n,，]/).map((item) => item.trim()).filter(Boolean);
}

function Field({ label, children, className = "" }: { label: string; children: ReactNode; className?: string }) {
  return <label className={`research-field ${className}`}><span>{label}</span>{children}</label>;
}

function SubjectEditor({
  draft,
  editing,
  busy,
  onChange,
  onCancel,
  onSave,
}: {
  draft: SubjectDraft;
  editing: boolean;
  busy: boolean;
  onChange: (next: SubjectDraft) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  return (
    <Card className="research-editor-card" kicker={editing ? "SUBJECT METADATA · AUDITED UPDATE" : "RESEARCH SUBJECT"} title={editing ? "编辑研究档案元数据" : "新建研究标的"}>
      <p className="card-note">研究档案元数据写入会留下可审计的确认记录；它不会修改 Thesis revision，也不会操作仓位。</p>
      <div className="research-form-grid">
        <Field label="研究档案类型"><select value={draft.subjectType} disabled={editing} onChange={(event) => onChange({ ...draft, subjectType: event.target.value })}>{SUBJECT_TYPES.map((value) => <option key={value} value={value}>{value}</option>)}</select></Field>
        <Field label="主标的 Instrument ID"><input value={draft.instrument} disabled={editing} onChange={(event) => onChange({ ...draft, instrument: event.target.value })} placeholder="equity:US:NVDA" /></Field>
        <Field label="标题" className="research-field-wide"><input value={draft.title} onChange={(event) => onChange({ ...draft, title: event.target.value })} placeholder="例如：NVDA AI 基础设施跟踪" /></Field>
        <Field label="摘要" className="research-field-wide"><textarea value={draft.summary} onChange={(event) => onChange({ ...draft, summary: event.target.value })} rows={5} placeholder="记录这个研究标的要长期回答的问题、范围和边界。" /></Field>
        <Field label="主题标签"><input value={draft.tags} onChange={(event) => onChange({ ...draft, tags: event.target.value })} placeholder="ai, valuation, catalyst" /></Field>
        <Field label="关联研究档案 ID"><textarea value={draft.linkedSubjectIds} onChange={(event) => onChange({ ...draft, linkedSubjectIds: event.target.value })} rows={2} placeholder="每行一个 case_<uuid7>" /></Field>
      </div>
      <div className="research-form-actions"><ActionButton onClick={onSave} busy={busy}>{editing ? "保存研究档案" : "创建研究档案"}</ActionButton><button className="close-button" type="button" onClick={onCancel}>取消</button></div>
    </Card>
  );
}

type AssumptionDraft = { statement: string; basis: string; falsifiability: string };
type InvalidationDraft = { description: string; observable: string; severity: string };
type ThesisDraft = {
  title: string;
  statement: string;
  rationale: string;
  confidenceBand: string;
  rating: string;
  invalidationCheckNote: string;
  thesisRole: string;
  thesisStatus: string;
  replacesRevisionNo: string;
  assumptions: AssumptionDraft[];
  invalidations: InvalidationDraft[];
};

const EMPTY_THESIS_DRAFT: ThesisDraft = {
  title: "",
  statement: "",
  rationale: "",
  confidenceBand: "medium",
  rating: "watch",
  invalidationCheckNote: "",
  thesisRole: "primary",
  thesisStatus: "draft",
  replacesRevisionNo: "",
  assumptions: [],
  invalidations: [],
};

function thesisDraftFrom(thesis: Dict | undefined, revision: Dict | undefined, revisionAssumptions: Dict[] = [], revisionInvalidations: Dict[] = []): ThesisDraft {
  return {
    title: text(revision?.title, text(thesis?.title, "")),
    statement: text(revision?.statement, ""),
    rationale: text(revision?.rationale, ""),
    confidenceBand: text(revision?.confidence_band, "medium"),
    rating: text(revision?.rating, "watch"),
    invalidationCheckNote: text(revision?.invalidation_check_note, ""),
    thesisRole: text(revision?.thesis_role, text(thesis?.role, "primary")),
    thesisStatus: text(revision?.thesis_status, text(thesis?.status, "active")),
    replacesRevisionNo: revision?.revision_no == null ? "" : String(revision.revision_no),
    assumptions: revisionAssumptions.map((item) => ({
      statement: text(item.statement, ""),
      basis: text(item.basis, ""),
      falsifiability: text(item.falsifiability, ""),
    })),
    invalidations: revisionInvalidations.map((item) => ({
      description: text(item.description, ""),
      observable: text(item.observable, ""),
      severity: text(item.severity, "soft"),
    })),
  };
}

function ThesisEditor({
  draft,
  thesisId,
  statusExplicit,
  subjectStatus,
  busy,
  onChange,
  onStatusExplicitChange,
  onCancel,
  onSave,
}: {
  draft: ThesisDraft;
  thesisId: string | null;
  statusExplicit: boolean;
  subjectStatus: string;
  busy: boolean;
  onChange: (next: ThesisDraft) => void;
  onStatusExplicitChange: (next: boolean) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  function updateAssumption(index: number, key: keyof AssumptionDraft, value: string) {
    const assumptions = draft.assumptions.map((item, itemIndex) => itemIndex === index ? { ...item, [key]: value } : item);
    onChange({ ...draft, assumptions });
  }
  function updateInvalidation(index: number, key: keyof InvalidationDraft, value: string) {
    const invalidations = draft.invalidations.map((item, itemIndex) => itemIndex === index ? { ...item, [key]: value } : item);
    onChange({ ...draft, invalidations });
  }
  return (
    <section className="research-thesis-editor" aria-label={thesisId ? "编辑 Thesis revision" : "创建 Thesis"}>
      <div className="research-thesis-editor-heading"><div><p className="card-kicker">{thesisId ? "THESIS REVISION · APPEND ONLY" : "THESIS · CANDIDATE"}</p><h3>{thesisId ? "编辑 Thesis · 提出新 Revision" : "创建新 Thesis"}</h3></div><Badge value={subjectStatus.toUpperCase()} /></div>
      <p className="card-note">不会直接改写历史 revision。保存后只会生成 pending candidate，必须显式 Confirm 或 Reject。</p>
      <div className="research-form-grid">
        <Field label="标题" className="research-field-wide"><input value={draft.title} onChange={(event) => onChange({ ...draft, title: event.target.value })} /></Field>
        <Field label="Thesis Role"><select value={draft.thesisRole} onChange={(event) => onChange({ ...draft, thesisRole: event.target.value })}>{THESIS_ROLES.map((value) => <option key={value} value={value}>{value}</option>)}</select></Field>
        <Field label="候选状态"><div className="research-status-control">{thesisId && <label className="research-status-toggle"><input type="checkbox" checked={statusExplicit} onChange={(event) => onStatusExplicitChange(event.target.checked)} /><span>同时修改状态</span></label>}<select value={draft.thesisStatus} disabled={Boolean(thesisId) && !statusExplicit} onChange={(event) => onChange({ ...draft, thesisStatus: event.target.value })}>{THESIS_STATUSES.map((value) => <option key={value} value={value}>{value}</option>)}</select></div></Field>
        <Field label="Confidence"><select value={draft.confidenceBand} onChange={(event) => onChange({ ...draft, confidenceBand: event.target.value })}>{CONFIDENCE_BANDS.map((value) => <option key={value} value={value}>{value}</option>)}</select></Field>
        <Field label="Rating"><select value={draft.rating} onChange={(event) => onChange({ ...draft, rating: event.target.value })}>{RATINGS.map((value) => <option key={value} value={value}>{value}</option>)}</select></Field>
        <Field label="替换 revision no"><input inputMode="numeric" value={draft.replacesRevisionNo} onChange={(event) => onChange({ ...draft, replacesRevisionNo: event.target.value.replace(/[^0-9]/g, "") })} placeholder="当前 revision no" /></Field>
        <Field label="Statement" className="research-field-wide"><textarea value={draft.statement} onChange={(event) => onChange({ ...draft, statement: event.target.value })} rows={5} /></Field>
        <Field label="Rationale" className="research-field-wide"><textarea value={draft.rationale} onChange={(event) => onChange({ ...draft, rationale: event.target.value })} rows={5} /></Field>
        <Field label="Invalidation check note" className="research-field-wide"><textarea value={draft.invalidationCheckNote} onChange={(event) => onChange({ ...draft, invalidationCheckNote: event.target.value })} rows={4} /></Field>
      </div>
      {subjectStatus === "draft" && ["active", "strengthened", "weakened"].includes(draft.thesisStatus) && (!thesisId || statusExplicit) && <div className="research-state-warning" role="status">草稿研究档案不能确认 live Thesis。请先提交并确认研究档案激活候选，或把 Thesis 状态保留为 DRAFT。</div>}
      <div className="research-array-editor">
        <div className="research-array-heading"><div><p className="card-kicker">ASSUMPTIONS</p><h3>假设</h3></div><button className="close-button" type="button" onClick={() => onChange({ ...draft, assumptions: [...draft.assumptions, { statement: "", basis: "", falsifiability: "" }] })}>添加假设</button></div>
        {draft.assumptions.length === 0 ? <p className="muted">暂无假设；如需长期挑战判断，建议至少填写一条。</p> : draft.assumptions.map((item, index) => <div className="research-array-row" key={`assumption-${index}`}><Field label="Statement"><textarea rows={3} value={item.statement} onChange={(event) => updateAssumption(index, "statement", event.target.value)} /></Field><Field label="Basis"><textarea rows={3} value={item.basis} onChange={(event) => updateAssumption(index, "basis", event.target.value)} /></Field><Field label="Falsifiability"><textarea rows={3} value={item.falsifiability} onChange={(event) => updateAssumption(index, "falsifiability", event.target.value)} /></Field><button className="close-button" type="button" onClick={() => onChange({ ...draft, assumptions: draft.assumptions.filter((_, itemIndex) => itemIndex !== index) })}>移除</button></div>)}
      </div>
      <div className="research-array-editor">
        <div className="research-array-heading"><div><p className="card-kicker">INVALIDATIONS</p><h3>失效条件</h3></div><button className="close-button" type="button" onClick={() => onChange({ ...draft, invalidations: [...draft.invalidations, { description: "", observable: "", severity: "soft" }] })}>添加条件</button></div>
        {draft.invalidations.length === 0 ? <p className="muted">暂无失效条件。</p> : draft.invalidations.map((item, index) => <div className="research-array-row" key={`invalidation-${index}`}><Field label="Description"><textarea rows={3} value={item.description} onChange={(event) => updateInvalidation(index, "description", event.target.value)} /></Field><Field label="Observable"><textarea rows={3} value={item.observable} onChange={(event) => updateInvalidation(index, "observable", event.target.value)} /></Field><Field label="Severity"><select value={item.severity} onChange={(event) => updateInvalidation(index, "severity", event.target.value)}>{INVALIDATION_SEVERITIES.map((value) => <option key={value} value={value}>{value}</option>)}</select></Field><button className="close-button" type="button" onClick={() => onChange({ ...draft, invalidations: draft.invalidations.filter((_, itemIndex) => itemIndex !== index) })}>移除</button></div>)}
      </div>
      <div className="research-form-actions"><ActionButton onClick={onSave} busy={busy}>提出候选</ActionButton><button className="close-button" type="button" onClick={onCancel}>取消</button></div>
    </section>
  );
}

function MonitorLinks({ monitorData, subjectId, loading }: { monitorData: Dict | null; subjectId: string; loading: boolean }) {
  const dashboard = envelopeData<Dict>(monitorData?.dashboard);
  const monitorItems = listOf<MonitorAggregate>(dashboard, "items");
  const linked = monitorItems.map((item) => item.monitor ?? item).filter((monitor) => text(monitor.subject_id, "") === subjectId);
  return (
    <Card className="research-related-card" kicker="MONITOR LINKS" title="关联 Monitor" action={<Link className="text-link" href="/monitors">打开 Monitor 工作区</Link>}>
      {loading ? <Empty>正在读取 Monitor 关联…</Empty> : linked.length === 0 ? <Empty>当前研究档案没有关联 Monitor。你可以在 Monitor 工作区通过研究档案 ID 精确绑定。</Empty> : <div className="research-monitor-links">{linked.map((monitor) => <Link href={`/monitors#monitor-${text(monitor.monitor_id)}`} className="research-monitor-link" key={text(monitor.monitor_id)}><div><strong>{text(monitor.name, "未命名 Monitor")}</strong><small>{shortId(monitor.primary_instrument_id)} · {text(monitor.cadence)}</small></div><Badge value={text(monitor.status, "UNKNOWN")} /></Link>)}</div>}
    </Card>
  );
}

function ThesisSummary({ thesis, revision, assumptions, invalidations }: { thesis: Dict; revision?: Dict; assumptions: Dict[]; invalidations: Dict[] }) {
  return (
    <article className="research-thesis-summary">
      <header><div><strong>{text(thesis.title, "未命名 Thesis")}</strong><small className="mono">{text(thesis.thesis_id)}</small></div><div className="research-thesis-badges"><Badge value={text(thesis.status, "UNKNOWN").toUpperCase()} /><span className="research-role">{text(thesis.role).toUpperCase()}</span></div></header>
      <div className="research-revision-grid"><div className="research-statement"><span>Latest revision · statement</span><p>{text(revision?.statement, "没有 latest revision statement。")}</p></div><div><span>Rating</span><strong>{text(revision?.rating).toUpperCase()}</strong></div><div><span>Confidence</span><strong>{text(revision?.confidence_band).toUpperCase()}</strong></div><div><span>Current revision</span><strong>v{text(thesis.current_revision_no)}</strong><small>latest v{text(revision?.revision_no)}</small></div><div><span>Status</span><strong>{text(thesis.status).toUpperCase()}</strong></div><div><span>Role</span><strong>{text(thesis.role).toUpperCase()}</strong></div></div>
      {revision && <div className="research-thesis-detail-grid"><div><span>Rationale</span><p>{text(revision.rationale)}</p></div><div><span>Invalidation check</span><p>{text(revision.invalidation_check_note)}</p></div></div>}
      <div className="research-inline-columns"><div><span>假设 · {assumptions.length}</span>{assumptions.length === 0 ? <small className="muted">无</small> : <ul>{assumptions.map((item) => <li key={text(item.assumption_id)}>{text(item.statement)}</li>)}</ul>}</div><div><span>失效条件 · {invalidations.length}</span>{invalidations.length === 0 ? <small className="muted">无</small> : <ul>{invalidations.map((item) => <li key={text(item.invalidation_id)}>{text(item.description)}</li>)}</ul>}</div></div>
    </article>
  );
}

function PendingCandidate({ candidate, subjectStatus, onConfirm, onReject, onWithdraw, busy }: { candidate: Dict; subjectStatus: string; onConfirm: (candidate: Dict, action: "confirm" | "reject" | "withdraw", reason?: string) => void; onReject: (candidate: Dict) => void; onWithdraw: (candidate: Dict) => void; busy: boolean }) {
  const [rejectionReason, setRejectionReason] = useState("");
  const payload = candidate.payload && typeof candidate.payload === "object" ? candidate.payload as Dict : {};
  const kind = text(payload.kind, text(candidate.kind, "thesis_revision"));
  const isSubjectStatus = kind === "subject_status_change" || kind === "case_status_change";
  const summary = isSubjectStatus
    ? `${subjectStatus.toUpperCase()} → ${text(payload.new_status, "UNKNOWN").toUpperCase()}`
    : text(payload.statement, text(payload.title, "候选 revision"));
  const confirmCopy = isSubjectStatus ? `确认应用研究档案状态变更：${summary}？` : "确认应用这个 Thesis candidate？历史 revision 不会被改写。";
  return <article className="research-candidate"><header><div><strong>{text(candidate.candidate_id)}</strong><small>{kind} · {text(candidate.proposed_by, "unknown")}</small></div><Badge value={text(candidate.status, "PROPOSED").toUpperCase()} /></header><p>{summary}</p><div className="research-candidate-actions"><ActionButton onClick={() => { if (window.confirm(confirmCopy)) onConfirm(candidate, "confirm"); }} busy={busy}>确认</ActionButton><input value={rejectionReason} onChange={(event) => setRejectionReason(event.target.value)} placeholder="拒绝原因（必填）" aria-label="候选拒绝原因" /><ActionButton tone="warning" onClick={() => { if (!rejectionReason.trim()) { window.alert("拒绝需要填写原因。"); return; } if (window.confirm("确认拒绝这个候选？")) onReject({ ...candidate, rejectionReason }); }} busy={busy}>拒绝</ActionButton><button className="close-button" type="button" disabled={busy} onClick={() => { if (window.confirm("确认撤回这个尚未处理的候选？")) onWithdraw(candidate); }}>撤回</button></div></article>;
}

function ResearchSubjectDetail({
  item,
  monitorData,
  monitorLoading,
  onSelectSubject,
  onRefresh,
  onWrite,
  busy,
}: {
  item: SubjectAggregate;
  monitorData: Dict | null;
  monitorLoading: boolean;
  onSelectSubject: (subjectId: string) => void;
  onRefresh: () => void;
  onWrite: (toolName: string, request: Dict, confirmation?: string) => Promise<Dict>;
  busy: boolean;
}) {
  const researchSubject = item.subject ?? {};
  const state = stateData(item) ?? {};
  const failure = stateError(item.state);
  const theses = listOf<Dict>(state, "theses");
  const revisions = latestRevisionByThesis(listOf<Dict>(state, "latest_revisions"));
  const assumptions = listOf<Dict>(state, "assumptions");
  const invalidations = listOf<Dict>(state, "invalidations");
  const openQuestions = listOf<Dict>(state, "open_questions");
  const pendingCandidates = listOf<Dict>(state, "pending_candidates");
  const [subjectEditor, setSubjectEditor] = useState(false);
  const [subjectDraft, setSubjectDraft] = useState(() => subjectDraftFrom(researchSubject));
  const [thesisEditor, setThesisEditor] = useState(false);
  const [thesisId, setThesisId] = useState<string | null>(null);
  const [thesisDraft, setThesisDraft] = useState<ThesisDraft>(EMPTY_THESIS_DRAFT);
  const [thesisStatusExplicit, setThesisStatusExplicit] = useState(false);

  useEffect(() => {
    setSubjectDraft(subjectDraftFrom(researchSubject));
    setSubjectEditor(false);
    setThesisEditor(false);
    setThesisId(null);
    setThesisStatusExplicit(false);
  }, [researchSubject.subject_id, researchSubject.updated_at]);


  async function saveSubject() {
    const title = subjectDraft.title.trim();
    const summary = subjectDraft.summary.trim();
    if (!title || !summary) { window.alert("标题和摘要不能为空。"); return; }
    try {
      await onWrite("investment_case_manage", {
        operation: "update",
        case_id: text(researchSubject.subject_id),
        title,
        summary,
        topic_tags: splitList(subjectDraft.tags),
        linked_case_ids: splitList(subjectDraft.linkedSubjectIds),
        reviewed_by: "user",
        idempotency_key: idempotencyKey("subject-update"),
      }, "investment_case_manage");
      setSubjectEditor(false);
      onRefresh();
    } catch { /* onWrite keeps the local error visible */ }
  }

  async function archiveSubject() {
    if (!window.confirm("确认归档这个研究档案？归档不会删除历史 Thesis、证据或 Monitor。")) return;
    const reason = window.prompt("请输入归档原因：", "研究范围结束或判断失效")?.trim();
    if (!reason) return;
    try {
      await onWrite("investment_case_manage", { operation: "archive", case_id: text(researchSubject.subject_id), archived_reason: reason, reviewed_by: "user", idempotency_key: idempotencyKey("subject-archive") }, "investment_case_manage");
      onRefresh();
    } catch { /* onWrite keeps the local error visible */ }
  }

  async function restoreSubject() {
    if (!window.confirm("确认将这个已归档研究档案恢复为 Draft？历史 Thesis、Trade Plan 与审计记录都会保留。")) return;
    try {
      const proposed = await onWrite("research_judgment_propose", {
        operation: "research_state",
        case_id: text(researchSubject.subject_id),
        payload: { kind: "case_status_change", action: "update", new_status: "draft" },
        confirmation_mode: "strict_review",
        proposed_by: "user",
        proposed_by_rationale: "用户通过本地 Research 工作区请求恢复已归档研究档案为 Draft",
        idempotency_key: idempotencyKey("subject-restore-propose"),
      }, "research_judgment_propose");
      const candidate = envelopeData<Dict>(proposed);
      const candidateId = candidate?.candidate_id;
      if (typeof candidateId !== "string" || !candidateId) throw new Error("恢复候选未返回 candidate_id。");
      await onWrite("research_judgment_confirm", {
        candidate_id: candidateId,
        action: "confirm",
        reviewed_by: "user",
        submitted_via: "direct",
        review_note: "用户通过本地 Research 工作区撤销归档并恢复为 Draft",
      }, "research_judgment_confirm");
      onRefresh();
    } catch { /* onWrite keeps the local error visible */ }
  }

  async function activateSubject() {
    if (!window.confirm("提交将此研究档案从 Draft 转为 Active 的候选？提交后仍需在待审候选中显式确认。")) return;
    try {
      await onWrite("research_judgment_propose", {
        operation: "research_state",
        case_id: text(researchSubject.subject_id),
        payload: { kind: "case_status_change", action: "update", new_status: "active" },
        confirmation_mode: "strict_review",
        proposed_by: "user",
        proposed_by_rationale: "用户通过本地 Research 工作区请求将草稿研究档案转为 Active 跟踪",
        idempotency_key: idempotencyKey("subject-activate-propose"),
      }, "research_judgment_propose");
      onRefresh();
    } catch { /* onWrite keeps the local error visible */ }
  }

  function startThesisEditor(target?: Dict, createNew = false) {
    const thesis = createNew ? undefined : target ?? theses[0];
    const revision = thesis ? revisions.get(String(thesis.thesis_id)) : undefined;
    setThesisId(thesis ? String(thesis.thesis_id) : null);
    const thesisKey = thesis ? String(thesis.thesis_id) : null;
    const revisionNo = revision?.revision_no == null ? null : String(revision.revision_no);
    const revisionAssumptions = assumptions.filter((item) => String(item.thesis_id) === thesisKey && (revisionNo === null || String(item.revision_no) === revisionNo));
    const revisionInvalidations = invalidations.filter((item) => String(item.thesis_id) === thesisKey && (revisionNo === null || String(item.revision_no) === revisionNo));
    const draft = thesisDraftFrom(thesis, revision, revisionAssumptions, revisionInvalidations);
    if (!thesis) draft.thesisStatus = String(researchSubject.status).toLowerCase() === "draft" ? "draft" : "active";
    if (!thesis && theses.some((item) => text(item.role) === "primary" && text(item.status) === "active")) {
      draft.thesisRole = "competitor";
    }
    setThesisDraft(draft);
    setThesisStatusExplicit(!thesis);
    setThesisEditor(true);
  }

  async function saveThesis() {
    const invalidAssumption = thesisDraft.assumptions.find((item) => item.statement.trim() && (!item.basis.trim() || !item.falsifiability.trim()));
    const invalidInvalidation = thesisDraft.invalidations.find((item) => item.description.trim() && !item.observable.trim());
    if (invalidAssumption) { window.alert("每条假设都需要填写 Basis 和 Falsifiability。"); return; }
    if (invalidInvalidation) { window.alert("每条失效条件都需要填写 Observable。"); return; }
    const subjectStatus = String(researchSubject.status).toLowerCase();
    const changesToLiveStatus = ["active", "strengthened", "weakened"].includes(thesisDraft.thesisStatus) && (!thesisId || thesisStatusExplicit);
    if (subjectStatus === "draft" && changesToLiveStatus) { window.alert("草稿研究档案不能确认 live Thesis。请先激活研究档案，或选择 DRAFT 状态。"); return; }
    const payload: Dict = {
      kind: "thesis_revision",
      title: thesisDraft.title.trim(),
      statement: thesisDraft.statement.trim(),
      rationale: thesisDraft.rationale.trim(),
      confidence_band: thesisDraft.confidenceBand,
      rating: thesisDraft.rating,
      invalidation_check_note: thesisDraft.invalidationCheckNote.trim(),
      assumptions: thesisDraft.assumptions.filter((item) => item.statement.trim()).map((item) => ({ statement: item.statement.trim(), basis: item.basis.trim(), falsifiability: item.falsifiability.trim() })),
      invalidations: thesisDraft.invalidations.filter((item) => item.description.trim()).map((item) => ({ description: item.description.trim(), observable: item.observable.trim(), severity: item.severity })),
      thesis_role: thesisDraft.thesisRole,
      replaces_revision_no: thesisDraft.replacesRevisionNo ? Number(thesisDraft.replacesRevisionNo) : null,
    };
    if (!thesisId || thesisStatusExplicit) payload.thesis_status = thesisDraft.thesisStatus;
    if (!payload.title || !payload.statement || !payload.rationale || !payload.invalidation_check_note) { window.alert("Thesis title、statement、rationale、invalidation check note 都不能为空。"); return; }
    try {
      await onWrite("research_judgment_propose", { operation: "thesis_revision", case_id: text(researchSubject.subject_id), thesis_id: thesisId, payload, proposed_by: "user", proposed_by_rationale: "由本地 Research 工作区提出 Thesis revision", idempotency_key: idempotencyKey("thesis-propose") }, "research_judgment_propose");
      setThesisEditor(false);
      onRefresh();
    } catch { /* onWrite keeps the local error visible */ }
  }

  async function decideCandidate(candidate: Dict, action: "confirm" | "reject" | "withdraw", reason?: string) {
    const request: Dict = { candidate_id: text(candidate.candidate_id), action, reviewed_by: "user", submitted_via: "direct" };
    if (action === "reject") request.rejection_reason = reason;
    else request.review_note = action === "withdraw" ? "用户通过本地 Research 工作区撤回候选" : "通过本地 Research 工作区确认";
    try {
      await onWrite("research_judgment_confirm", request, "research_judgment_confirm");
      onRefresh();
    } catch { /* onWrite keeps the local error visible */ }
  }

  const tags = stringList(researchSubject.topic_tags);
  return (
    <div className="research-detail-stack">
      <Card className="research-subject-detail" kicker={text(researchSubject.subject_type, "RESEARCH SUBJECT")} title={text(researchSubject.title, "未命名研究标的")} action={<div className="research-detail-actions"><Badge value={text(researchSubject.status, "UNKNOWN").toUpperCase()} /><button className="close-button" type="button" onClick={() => setSubjectEditor((value) => !value)}>{subjectEditor ? "关闭编辑" : "编辑研究档案"}</button>{String(researchSubject.status).toLowerCase() === "draft" && <button className="close-button restore-text" type="button" disabled={busy} onClick={() => { void activateSubject(); }}>开始跟踪</button>}{String(researchSubject.status).toLowerCase() === "archived" ? <button className="close-button restore-text" type="button" disabled={busy} onClick={() => { void restoreSubject(); }}>恢复为 Draft</button> : <button className="close-button warning-text" type="button" disabled={busy} onClick={archiveSubject}>归档</button>}</div>}>
        <div className="research-subject-meta"><div><span>标的</span><strong>{shortId(researchSubject.primary_instrument_id)}</strong><small>{text(researchSubject.primary_instrument_id)}</small></div><div><span>状态</span><strong>{text(researchSubject.status)}</strong></div><div><span>创建</span><strong>{formatDate(researchSubject.created_at)}</strong></div><div><span>更新</span><strong>{formatDate(researchSubject.updated_at)}</strong></div></div>
        <p className="research-summary">{text(researchSubject.summary, "没有研究档案摘要。")}</p>
        <div className="research-tags" aria-label="研究档案标签">{tags.length === 0 ? <span className="muted">无标签</span> : tags.map((tag) => <span key={tag}>{tag}</span>)}</div>
        {stringList(researchSubject.linked_subject_ids).length > 0 && <div className="research-linked-subjects"><span>关联研究档案</span>{stringList(researchSubject.linked_subject_ids).map((subjectId) => <button type="button" key={subjectId} onClick={() => onSelectSubject(subjectId)}>{shortId(subjectId)}</button>)}</div>}
        {failure && <div className="research-state-error" role="status"><strong>局部读取失败</strong><span>{failure}</span><small>研究档案元数据仍可操作；修复状态读取后再编辑 Thesis。</small></div>}
      </Card>
      {subjectEditor && <SubjectEditor draft={subjectDraft} editing busy={busy} onChange={setSubjectDraft} onCancel={() => setSubjectEditor(false)} onSave={() => { void saveSubject(); }} />}
      <Card className="research-theses-card" kicker="CURRENT JUDGMENT" title="Thesis" action={!thesisEditor ? <ActionButton onClick={() => startThesisEditor(undefined, true)}>新建 Thesis</ActionButton> : <Badge value="EDITING" />}>
        {thesisEditor ? <ThesisEditor draft={thesisDraft} thesisId={thesisId} statusExplicit={thesisStatusExplicit} subjectStatus={String(researchSubject.status).toLowerCase()} busy={busy} onChange={setThesisDraft} onStatusExplicitChange={setThesisStatusExplicit} onCancel={() => setThesisEditor(false)} onSave={() => { void saveThesis(); }} /> : theses.length === 0 ? <div className="research-no-thesis"><p>此研究档案暂无 Thesis。可以直接创建新的 Thesis candidate；草稿研究档案默认创建 DRAFT Thesis。</p><ActionButton onClick={() => startThesisEditor(undefined, true)}>创建 Thesis</ActionButton></div> : <div className="research-thesis-list">{theses.map((thesis) => <div key={text(thesis.thesis_id)}><ThesisSummary thesis={thesis} revision={revisions.get(String(thesis.thesis_id))} assumptions={assumptions.filter((item) => String(item.thesis_id) === String(thesis.thesis_id))} invalidations={invalidations.filter((item) => String(item.thesis_id) === String(thesis.thesis_id))} /><div className="research-thesis-actions"><button className="close-button" type="button" onClick={() => startThesisEditor(thesis)}>编辑 Thesis · 新建 Revision</button></div></div>)}</div>}
      </Card>
      {pendingCandidates.length > 0 && <Card className="research-candidates-card" kicker="REVIEW QUEUE" title="待审候选" action={<Badge value={`${pendingCandidates.length} PROPOSED`} />}>{pendingCandidates.map((candidate) => <PendingCandidate key={text(candidate.candidate_id)} candidate={candidate} subjectStatus={String(researchSubject.status).toLowerCase()} busy={busy} onConfirm={(item, action) => { void decideCandidate(item, action); }} onReject={(item) => { void decideCandidate(item, "reject", text(item.rejectionReason)); }} onWithdraw={(item) => { void decideCandidate(item, "withdraw"); }} />)}</Card>}
      <ResearchContinuity subject={researchSubject} state={state} onWrite={onWrite} onRefresh={onRefresh} busy={busy} />
      <Card className="research-context-card" kicker="RESEARCH CONTEXT" title="判断上下文"><div className="research-context-grid research-context-single"><div><span>Open questions · {openQuestions.length}</span>{openQuestions.length === 0 ? <p className="muted">暂无未决问题。</p> : <ul>{openQuestions.map((question) => <li key={text(question.question_id)}>{text(question.text)}</li>)}</ul>}</div></div></Card>
      <MonitorLinks monitorData={monitorData} loading={monitorLoading} subjectId={text(researchSubject.subject_id)} />
      <details className="research-raw"><summary>查看本研究档案 durable state 原文</summary><pre>{displayJson(state)}</pre></details>
      <div id={`research-detail-${text(researchSubject.subject_id)}`} className="sr-only">{text(researchSubject.subject_id)}</div>
    </div>
  );
}

export default function ResearchPage() {
  const result = useApi<Dict>("/api/research");
  const monitorResult = useApi<Dict>("/api/monitors?run_limit=1&event_limit=1");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("ALL");
  const [selectedSubjectId, setSelectedSubjectId] = useState<string | null>(null);
  const [subjectEditor, setSubjectEditor] = useState(false);
  const [subjectDraft, setSubjectDraft] = useState<SubjectDraft>(EMPTY_SUBJECT_DRAFT);
  const [writing, setWriting] = useState(false);
  const [writeError, setWriteError] = useState<string | null>(null);
  const [writeSuccess, setWriteSuccess] = useState<string | null>(null);
  const items = listOf<SubjectAggregate>(result.data, "subjects");
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const filtered = useMemo(() => items.filter((item) => { const researchSubject = item.subject ?? {}; const matchesStatus = status === "ALL" || text(researchSubject.status).toUpperCase() === status; return matchesStatus && (!normalizedQuery || subjectSearchText(item).includes(normalizedQuery)); }), [items, normalizedQuery, status]);

  useEffect(() => {
    if (filtered.length === 0) { setSelectedSubjectId(null); return; }
    const canonicalSubjectId = window.location.hash.match(/^#subject-(case_.+)$/)?.[1];
    const legacySubjectId = window.location.hash.match(/^#case-(case_.+)$/)?.[1];
    const hashSubjectId = canonicalSubjectId ?? legacySubjectId;
    if (hashSubjectId && filtered.some((item) => String(item.subject?.subject_id) === hashSubjectId)) {
      setSelectedSubjectId(hashSubjectId);
      if (legacySubjectId) window.history.replaceState(null, "", `#subject-${hashSubjectId}`);
      return;
    }
    if (!selectedSubjectId || !filtered.some((item) => String(item.subject?.subject_id) === selectedSubjectId)) setSelectedSubjectId(String(filtered[0].subject?.subject_id));
  }, [filtered, selectedSubjectId]);

  function selectSubject(subjectId: string) {
    setSelectedSubjectId(subjectId);
    window.history.replaceState(null, "", `#subject-${subjectId}`);
  }

  async function write(toolName: string, request: Dict, confirmation?: string): Promise<Dict> {
    setWriting(true);
    setWriteError(null);
    setWriteSuccess(null);
    try {
      const response = await postApi<Dict>("/api/tools/invoke", { tool_name: toolName, arguments: { request }, confirmation });
      const envelope = assertEnvelopeSuccess(response);
      setWriteSuccess("写入成功，正在刷新 Research durable state。");
      return envelope;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "本地写入失败";
      setWriteError(message);
      throw cause;
    } finally {
      setWriting(false);
    }
  }

  async function createSubject() {
    const title = subjectDraft.title.trim();
    const summary = subjectDraft.summary.trim();
    if (!title || !summary) { window.alert("标题和摘要不能为空。"); return; }
    try {
      const response = await write("investment_case_manage", { operation: "create", case_type: subjectDraft.subjectType, title, summary, primary_instrument_id: subjectDraft.instrument.trim() || null, topic_tags: splitList(subjectDraft.tags), linked_case_ids: splitList(subjectDraft.linkedSubjectIds), confirmed_by: "user", idempotency_key: idempotencyKey("subject-create") }, "investment_case_manage");
      const created = envelopeData<Dict>(response);
      const createdId = created?.case_id;
      if (typeof createdId === "string") setSelectedSubjectId(createdId);
      setSubjectEditor(false);
      setSubjectDraft(EMPTY_SUBJECT_DRAFT);
      result.refresh();
    } catch { /* write() keeps the local error visible */ }
  }

  const selected = filtered.find((item) => String(item.subject?.subject_id) === selectedSubjectId) ?? null;
  return (
    <ConsoleShell active="research" eyebrow="Durable judgment memory" title="Research 工作区">
      <DataBoundary loading={result.loading} error={result.error}>
        <div className="research-page">
          <div className="toolbar research-toolbar"><p>研究档案元数据可在这里编辑；Thesis 只能通过 append-only candidate 提出，再由你显式确认或拒绝。这个工作区不刷新 Provider、不确认判断，也不操作仓位。</p><div className="toolbar-actions"><ActionButton onClick={() => { setSubjectDraft(EMPTY_SUBJECT_DRAFT); setSubjectEditor((value) => !value); }}>{subjectEditor ? "关闭新建" : "新建研究标的"}</ActionButton><RefreshButton onClick={result.refresh} loading={result.loading} /></div></div>
          {writeError && <div className="inline-error" role="alert">{writeError}</div>}
          {writeSuccess && <div className="inline-success" role="status">{writeSuccess}</div>}
          {subjectEditor && <SubjectEditor draft={subjectDraft} editing={false} busy={writing} onChange={setSubjectDraft} onCancel={() => setSubjectEditor(false)} onSave={() => { void createSubject(); }} />}
          <div className="research-master-detail">
            <aside className="research-index"><Card className="research-index-card" kicker="RESEARCH SUBJECTS" title="所有研究标的" action={<span className="muted">{filtered.length} / {items.length}</span>}><div className="research-filters"><Field label="文本筛选"><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="标题、标的、标签、Thesis" aria-label="筛选研究标的" /></Field><Field label="研究档案状态"><select value={status} onChange={(event) => setStatus(event.target.value)} aria-label="按研究档案状态筛选"><option value="ALL">全部（含 archived）</option>{SUBJECT_STATUSES.map((value) => <option key={value} value={value.toUpperCase()}>{value}</option>)}</select></Field></div>{items.length === 0 ? <Empty>没有持久化研究档案。</Empty> : filtered.length === 0 ? <Empty>没有匹配当前筛选条件的研究标的。</Empty> : <div className="research-subject-index-list">{filtered.map((item) => { const researchSubject = item.subject ?? {}; const subjectId = String(researchSubject.subject_id ?? ""); const state = stateData(item) ?? {}; const thesisCount = listOf<Dict>(state, "theses").length; return <button type="button" id={`research-subject-${subjectId}`} className={`research-index-item ${selectedSubjectId === subjectId ? "selected" : ""}`} onClick={() => selectSubject(subjectId)} key={subjectId}><span className="research-index-status"><Badge value={text(researchSubject.status, "UNKNOWN").toUpperCase()} /></span><strong>{text(researchSubject.title, "未命名研究标的")}</strong><small>{shortId(researchSubject.primary_instrument_id)} · {thesisCount} Thesis</small><time>{formatDate(researchSubject.updated_at)}</time></button>; })}</div>}</Card></aside>
            <main className="research-detail" aria-live="polite">{selected ? <ResearchSubjectDetail item={selected} monitorData={monitorResult.data} monitorLoading={monitorResult.loading} onSelectSubject={selectSubject} onRefresh={result.refresh} onWrite={write} busy={writing} /> : <Empty>从左侧选择一个研究标的。</Empty>}</main>
          </div>
          {monitorResult.error && <div className="inline-error">关联 Monitor 读取失败：{monitorResult.error}。研究档案仍可正常查看和编辑。</div>}
        </div>
      </DataBoundary>
    </ConsoleShell>
  );
}
