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

type Dict = Record<string, unknown>;
type CaseAggregate = { case?: Dict; state?: Dict };
type MonitorAggregate = { monitor?: Dict; [key: string]: unknown };

const CASE_STATUSES = ["draft", "active", "strengthened", "weakened", "invalidated", "archived"];
const CASE_TYPES = ["company", "theme", "macro", "catalyst", "portfolio_concern"];
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

function stateData(item: CaseAggregate): Dict | null {
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

function caseSearchText(item: CaseAggregate): string {
  const researchCase = item.case ?? {};
  const state = stateData(item) ?? {};
  const theses = listOf<Dict>(state, "theses");
  const revisions = listOf<Dict>(state, "latest_revisions");
  return [
    researchCase.title,
    researchCase.summary,
    researchCase.primary_instrument_id,
    ...stringList(researchCase.topic_tags),
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

type CaseDraft = {
  caseType: string;
  title: string;
  summary: string;
  instrument: string;
  tags: string;
  linkedCaseIds: string;
};

const EMPTY_CASE_DRAFT: CaseDraft = {
  caseType: "company",
  title: "",
  summary: "",
  instrument: "",
  tags: "",
  linkedCaseIds: "",
};

function caseDraftFrom(researchCase: Dict): CaseDraft {
  return {
    caseType: text(researchCase.case_type, "company"),
    title: text(researchCase.title, ""),
    summary: text(researchCase.summary, ""),
    instrument: text(researchCase.primary_instrument_id, ""),
    tags: stringList(researchCase.topic_tags).join(", "),
    linkedCaseIds: stringList(researchCase.linked_case_ids).join(", "),
  };
}

function splitList(value: string): string[] {
  return value.split(/[\n,，]/).map((item) => item.trim()).filter(Boolean);
}

function Field({ label, children, className = "" }: { label: string; children: ReactNode; className?: string }) {
  return <label className={`research-field ${className}`}><span>{label}</span>{children}</label>;
}

function CaseEditor({
  draft,
  editing,
  busy,
  onChange,
  onCancel,
  onSave,
}: {
  draft: CaseDraft;
  editing: boolean;
  busy: boolean;
  onChange: (next: CaseDraft) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  return (
    <Card className="research-editor-card" kicker={editing ? "CASE METADATA · AUDITED UPDATE" : "CASE FILE"} title={editing ? "编辑 Case 元数据" : "新建 Investment Case"}>
      <p className="card-note">Case 元数据写入会留下可审计的确认记录；它不会修改 Thesis revision，也不会操作仓位。</p>
      <div className="research-form-grid">
        <Field label="Case 类型"><select value={draft.caseType} disabled={editing} onChange={(event) => onChange({ ...draft, caseType: event.target.value })}>{CASE_TYPES.map((value) => <option key={value} value={value}>{value}</option>)}</select></Field>
        <Field label="主标的 Instrument ID"><input value={draft.instrument} disabled={editing} onChange={(event) => onChange({ ...draft, instrument: event.target.value })} placeholder="equity:US:NVDA" /></Field>
        <Field label="标题" className="research-field-wide"><input value={draft.title} onChange={(event) => onChange({ ...draft, title: event.target.value })} placeholder="例如：NVDA AI 基础设施跟踪" /></Field>
        <Field label="摘要" className="research-field-wide"><textarea value={draft.summary} onChange={(event) => onChange({ ...draft, summary: event.target.value })} rows={5} placeholder="记录这个 Case 要长期回答的问题、范围和边界。" /></Field>
        <Field label="主题标签"><input value={draft.tags} onChange={(event) => onChange({ ...draft, tags: event.target.value })} placeholder="ai, valuation, catalyst" /></Field>
        <Field label="关联 Case ID"><textarea value={draft.linkedCaseIds} onChange={(event) => onChange({ ...draft, linkedCaseIds: event.target.value })} rows={2} placeholder="每行一个 case_<uuid7>" /></Field>
      </div>
      <div className="research-form-actions"><ActionButton onClick={onSave} busy={busy}>{editing ? "保存 Case" : "创建 Case"}</ActionButton><button className="close-button" type="button" onClick={onCancel}>取消</button></div>
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
  thesisStatus: "active",
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
  busy,
  onChange,
  onCancel,
  onSave,
}: {
  draft: ThesisDraft;
  thesisId: string | null;
  busy: boolean;
  onChange: (next: ThesisDraft) => void;
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
    <Card className="research-editor-card" kicker={thesisId ? "THESIS REVISION · APPEND ONLY" : "THESIS · CANDIDATE"} title={thesisId ? "提出 Thesis revision" : "创建新 Thesis"}>
      <p className="card-note">不会直接改写历史 revision。保存后只会生成 pending candidate，必须显式 Confirm 或 Reject。</p>
      <div className="research-form-grid">
        <Field label="标题" className="research-field-wide"><input value={draft.title} onChange={(event) => onChange({ ...draft, title: event.target.value })} /></Field>
        <Field label="Thesis Role"><select value={draft.thesisRole} onChange={(event) => onChange({ ...draft, thesisRole: event.target.value })}>{THESIS_ROLES.map((value) => <option key={value} value={value}>{value}</option>)}</select></Field>
        <Field label="候选状态"><select value={draft.thesisStatus} onChange={(event) => onChange({ ...draft, thesisStatus: event.target.value })}>{THESIS_STATUSES.map((value) => <option key={value} value={value}>{value}</option>)}</select></Field>
        <Field label="Confidence"><select value={draft.confidenceBand} onChange={(event) => onChange({ ...draft, confidenceBand: event.target.value })}>{CONFIDENCE_BANDS.map((value) => <option key={value} value={value}>{value}</option>)}</select></Field>
        <Field label="Rating"><select value={draft.rating} onChange={(event) => onChange({ ...draft, rating: event.target.value })}>{RATINGS.map((value) => <option key={value} value={value}>{value}</option>)}</select></Field>
        <Field label="替换 revision no"><input inputMode="numeric" value={draft.replacesRevisionNo} onChange={(event) => onChange({ ...draft, replacesRevisionNo: event.target.value.replace(/[^0-9]/g, "") })} placeholder="当前 revision no" /></Field>
        <Field label="Statement" className="research-field-wide"><textarea value={draft.statement} onChange={(event) => onChange({ ...draft, statement: event.target.value })} rows={5} /></Field>
        <Field label="Rationale" className="research-field-wide"><textarea value={draft.rationale} onChange={(event) => onChange({ ...draft, rationale: event.target.value })} rows={5} /></Field>
        <Field label="Invalidation check note" className="research-field-wide"><textarea value={draft.invalidationCheckNote} onChange={(event) => onChange({ ...draft, invalidationCheckNote: event.target.value })} rows={4} /></Field>
      </div>
      <div className="research-array-editor">
        <div className="research-array-heading"><div><p className="card-kicker">ASSUMPTIONS</p><h3>假设</h3></div><button className="close-button" type="button" onClick={() => onChange({ ...draft, assumptions: [...draft.assumptions, { statement: "", basis: "", falsifiability: "" }] })}>添加假设</button></div>
        {draft.assumptions.length === 0 ? <p className="muted">暂无假设；如需长期挑战判断，建议至少填写一条。</p> : draft.assumptions.map((item, index) => <div className="research-array-row" key={`assumption-${index}`}><Field label="Statement"><textarea rows={3} value={item.statement} onChange={(event) => updateAssumption(index, "statement", event.target.value)} /></Field><Field label="Basis"><textarea rows={3} value={item.basis} onChange={(event) => updateAssumption(index, "basis", event.target.value)} /></Field><Field label="Falsifiability"><textarea rows={3} value={item.falsifiability} onChange={(event) => updateAssumption(index, "falsifiability", event.target.value)} /></Field><button className="close-button" type="button" onClick={() => onChange({ ...draft, assumptions: draft.assumptions.filter((_, itemIndex) => itemIndex !== index) })}>移除</button></div>)}
      </div>
      <div className="research-array-editor">
        <div className="research-array-heading"><div><p className="card-kicker">INVALIDATIONS</p><h3>失效条件</h3></div><button className="close-button" type="button" onClick={() => onChange({ ...draft, invalidations: [...draft.invalidations, { description: "", observable: "", severity: "soft" }] })}>添加条件</button></div>
        {draft.invalidations.length === 0 ? <p className="muted">暂无失效条件。</p> : draft.invalidations.map((item, index) => <div className="research-array-row" key={`invalidation-${index}`}><Field label="Description"><textarea rows={3} value={item.description} onChange={(event) => updateInvalidation(index, "description", event.target.value)} /></Field><Field label="Observable"><textarea rows={3} value={item.observable} onChange={(event) => updateInvalidation(index, "observable", event.target.value)} /></Field><Field label="Severity"><select value={item.severity} onChange={(event) => updateInvalidation(index, "severity", event.target.value)}>{INVALIDATION_SEVERITIES.map((value) => <option key={value} value={value}>{value}</option>)}</select></Field><button className="close-button" type="button" onClick={() => onChange({ ...draft, invalidations: draft.invalidations.filter((_, itemIndex) => itemIndex !== index) })}>移除</button></div>)}
      </div>
      <div className="research-form-actions"><ActionButton onClick={onSave} busy={busy}>提出候选</ActionButton><button className="close-button" type="button" onClick={onCancel}>取消</button></div>
    </Card>
  );
}

function MonitorLinks({ monitorData, caseId, loading }: { monitorData: Dict | null; caseId: string; loading: boolean }) {
  const dashboard = envelopeData<Dict>(monitorData?.dashboard);
  const monitorItems = listOf<MonitorAggregate>(dashboard, "items");
  const linked = monitorItems.map((item) => item.monitor ?? item).filter((monitor) => text(monitor.case_id, "") === caseId);
  return (
    <Card className="research-related-card" kicker="MONITOR LINKS" title="关联 Monitor" action={<Link className="text-link" href="/monitors">打开 Monitor 工作区</Link>}>
      {loading ? <Empty>正在读取 Monitor 关联…</Empty> : linked.length === 0 ? <Empty>当前 Case 没有关联 Monitor。你可以在 Monitor 工作区通过精确 case_id 绑定。</Empty> : <div className="research-monitor-links">{linked.map((monitor) => <Link href={`/monitors#monitor-${text(monitor.monitor_id)}`} className="research-monitor-link" key={text(monitor.monitor_id)}><div><strong>{text(monitor.name, "未命名 Monitor")}</strong><small>{shortId(monitor.primary_instrument_id)} · {text(monitor.cadence)}</small></div><Badge value={text(monitor.status, "UNKNOWN")} /></Link>)}</div>}
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

function PendingCandidate({ candidate, onConfirm, onReject, busy }: { candidate: Dict; onConfirm: (candidate: Dict, action: "confirm" | "reject", reason?: string) => void; onReject: (candidate: Dict) => void; busy: boolean }) {
  const [rejectionReason, setRejectionReason] = useState("");
  const payload = candidate.payload && typeof candidate.payload === "object" ? candidate.payload as Dict : {};
  return <article className="research-candidate"><header><div><strong>{text(candidate.candidate_id)}</strong><small>{text(candidate.kind, "thesis_revision")} · {text(candidate.proposed_by, "unknown")}</small></div><Badge value={text(candidate.status, "PROPOSED").toUpperCase()} /></header><p>{text(payload.statement, text(payload.title, "候选 revision"))}</p><div className="research-candidate-actions"><ActionButton onClick={() => { if (window.confirm("确认应用这个 Thesis candidate？历史 revision 不会被改写。")) onConfirm(candidate, "confirm"); }} busy={busy}>确认</ActionButton><input value={rejectionReason} onChange={(event) => setRejectionReason(event.target.value)} placeholder="拒绝原因（必填）" aria-label="候选拒绝原因" /><ActionButton tone="warning" onClick={() => { if (!rejectionReason.trim()) { window.alert("拒绝需要填写原因。"); return; } if (window.confirm("确认拒绝这个 Thesis candidate？")) onReject({ ...candidate, rejectionReason }); }} busy={busy}>拒绝</ActionButton></div></article>;
}

function ResearchCaseDetail({
  item,
  monitorData,
  monitorLoading,
  onSelectCase,
  onRefresh,
  onWrite,
  busy,
}: {
  item: CaseAggregate;
  monitorData: Dict | null;
  monitorLoading: boolean;
  onSelectCase: (caseId: string) => void;
  onRefresh: () => void;
  onWrite: (toolName: string, request: Dict, confirmation?: string) => Promise<Dict>;
  busy: boolean;
}) {
  const researchCase = item.case ?? {};
  const state = stateData(item) ?? {};
  const failure = stateError(item.state);
  const theses = listOf<Dict>(state, "theses");
  const revisions = latestRevisionByThesis(listOf<Dict>(state, "latest_revisions"));
  const assumptions = listOf<Dict>(state, "assumptions");
  const invalidations = listOf<Dict>(state, "invalidations");
  const openQuestions = listOf<Dict>(state, "open_questions");
  const pendingCandidates = listOf<Dict>(state, "pending_candidates");
  const currentTradePlan = state.current_trade_plan && typeof state.current_trade_plan === "object" ? state.current_trade_plan as Dict : null;
  const [caseEditor, setCaseEditor] = useState(false);
  const [caseDraft, setCaseDraft] = useState(() => caseDraftFrom(researchCase));
  const [thesisEditor, setThesisEditor] = useState(false);
  const [thesisId, setThesisId] = useState<string | null>(null);
  const [thesisDraft, setThesisDraft] = useState<ThesisDraft>(EMPTY_THESIS_DRAFT);

  useEffect(() => {
    setCaseDraft(caseDraftFrom(researchCase));
    setCaseEditor(false);
    setThesisEditor(false);
    setThesisId(null);
  }, [researchCase.case_id, researchCase.updated_at]);


  async function saveCase() {
    const title = caseDraft.title.trim();
    const summary = caseDraft.summary.trim();
    if (!title || !summary) { window.alert("标题和摘要不能为空。"); return; }
    try {
      await onWrite("investment_case_manage", {
        operation: "update",
        case_id: text(researchCase.case_id),
        title,
        summary,
        topic_tags: splitList(caseDraft.tags),
        linked_case_ids: splitList(caseDraft.linkedCaseIds),
        reviewed_by: "user",
        idempotency_key: idempotencyKey("case-update"),
      }, "investment_case_manage");
      setCaseEditor(false);
      onRefresh();
    } catch { /* onWrite keeps the local error visible */ }
  }

  async function archiveCase() {
    if (!window.confirm("确认归档这个 Case？归档不会删除历史 Thesis、证据或 Monitor。")) return;
    const reason = window.prompt("请输入归档原因：", "研究范围结束或判断失效")?.trim();
    if (!reason) return;
    try {
      await onWrite("investment_case_manage", { operation: "archive", case_id: text(researchCase.case_id), archived_reason: reason, reviewed_by: "user", idempotency_key: idempotencyKey("case-archive") }, "investment_case_manage");
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
    if (!thesis && theses.some((item) => text(item.role) === "primary" && text(item.status) === "active")) {
      draft.thesisRole = "competitor";
    }
    setThesisDraft(draft);
    setThesisEditor(true);
  }

  async function saveThesis() {
    const invalidAssumption = thesisDraft.assumptions.find((item) => item.statement.trim() && (!item.basis.trim() || !item.falsifiability.trim()));
    const invalidInvalidation = thesisDraft.invalidations.find((item) => item.description.trim() && !item.observable.trim());
    if (invalidAssumption) { window.alert("每条假设都需要填写 Basis 和 Falsifiability。"); return; }
    if (invalidInvalidation) { window.alert("每条失效条件都需要填写 Observable。"); return; }
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
      thesis_status: thesisDraft.thesisStatus,
      replaces_revision_no: thesisDraft.replacesRevisionNo ? Number(thesisDraft.replacesRevisionNo) : null,
    };
    if (!payload.title || !payload.statement || !payload.rationale || !payload.invalidation_check_note) { window.alert("Thesis title、statement、rationale、invalidation check note 都不能为空。"); return; }
    try {
      await onWrite("research_judgment_propose", { operation: "thesis_revision", case_id: text(researchCase.case_id), thesis_id: thesisId, payload, proposed_by: "user", proposed_by_rationale: "由本地 Research 工作区提出 Thesis revision", idempotency_key: idempotencyKey("thesis-propose") }, "research_judgment_propose");
      setThesisEditor(false);
      onRefresh();
    } catch { /* onWrite keeps the local error visible */ }
  }

  async function decideCandidate(candidate: Dict, action: "confirm" | "reject", reason?: string) {
    const request: Dict = { candidate_id: text(candidate.candidate_id), action, reviewed_by: "user", submitted_via: "direct" };
    if (action === "reject") request.rejection_reason = reason;
    else request.review_note = "通过本地 Research 工作区确认";
    try {
      await onWrite("research_judgment_confirm", request, "research_judgment_confirm");
      onRefresh();
    } catch { /* onWrite keeps the local error visible */ }
  }

  const tags = stringList(researchCase.topic_tags);
  return (
    <div className="research-detail-stack">
      <Card className="research-case-detail" kicker={text(researchCase.case_type, "CASE")} title={text(researchCase.title, "未命名 Case")} action={<div className="research-detail-actions"><Badge value={text(researchCase.status, "UNKNOWN").toUpperCase()} /><button className="close-button" type="button" onClick={() => setCaseEditor((value) => !value)}>{caseEditor ? "关闭编辑" : "编辑 Case"}</button>{String(researchCase.status).toLowerCase() !== "archived" && <button className="close-button warning-text" type="button" onClick={archiveCase}>归档</button>}</div>}>
        <div className="research-case-meta"><div><span>标的</span><strong>{shortId(researchCase.primary_instrument_id)}</strong><small>{text(researchCase.primary_instrument_id)}</small></div><div><span>状态</span><strong>{text(researchCase.status)}</strong></div><div><span>创建</span><strong>{formatDate(researchCase.created_at)}</strong></div><div><span>更新</span><strong>{formatDate(researchCase.updated_at)}</strong></div></div>
        <p className="research-summary">{text(researchCase.summary, "没有 Case 摘要。")}</p>
        <div className="research-tags" aria-label="Case 标签">{tags.length === 0 ? <span className="muted">无标签</span> : tags.map((tag) => <span key={tag}>{tag}</span>)}</div>
        {stringList(researchCase.linked_case_ids).length > 0 && <div className="research-linked-cases"><span>关联 Case</span>{stringList(researchCase.linked_case_ids).map((caseId) => <button type="button" key={caseId} onClick={() => onSelectCase(caseId)}>{shortId(caseId)}</button>)}</div>}
        {failure && <div className="research-state-error" role="status"><strong>局部读取失败</strong><span>{failure}</span><small>Case 元数据仍可操作；修复状态读取后再编辑 Thesis。</small></div>}
      </Card>
      {caseEditor && <CaseEditor draft={caseDraft} editing busy={busy} onChange={setCaseDraft} onCancel={() => setCaseEditor(false)} onSave={() => { void saveCase(); }} />}
      <Card className="research-theses-card" kicker="CURRENT JUDGMENT" title="Thesis" action={<ActionButton onClick={() => startThesisEditor(undefined, true)}>新建 Thesis</ActionButton>}>
        {theses.length === 0 ? <div className="research-no-thesis"><p>此 Case 暂无 Thesis。可以直接创建新的 Thesis candidate。</p><ActionButton onClick={() => startThesisEditor(undefined, true)}>创建 Thesis</ActionButton></div> : <div className="research-thesis-list">{theses.map((thesis) => <div key={text(thesis.thesis_id)}><ThesisSummary thesis={thesis} revision={revisions.get(String(thesis.thesis_id))} assumptions={assumptions.filter((item) => String(item.thesis_id) === String(thesis.thesis_id))} invalidations={invalidations.filter((item) => String(item.thesis_id) === String(thesis.thesis_id))} /><div className="research-thesis-actions"><button className="close-button" type="button" onClick={() => startThesisEditor(thesis)}>提出此 Thesis 的新 revision</button></div></div>)}</div>}
      </Card>
      {thesisEditor && <ThesisEditor draft={thesisDraft} thesisId={thesisId} busy={busy} onChange={setThesisDraft} onCancel={() => setThesisEditor(false)} onSave={() => { void saveThesis(); }} />}
      {pendingCandidates.length > 0 && <Card className="research-candidates-card" kicker="REVIEW QUEUE" title="待审候选" action={<Badge value={`${pendingCandidates.length} PROPOSED`} />}>{pendingCandidates.map((candidate) => <PendingCandidate key={text(candidate.candidate_id)} candidate={candidate} busy={busy} onConfirm={(item, action) => { void decideCandidate(item, action); }} onReject={(item) => { void decideCandidate(item, "reject", text(item.rejectionReason)); }} />)}</Card>}
      <Card className="research-context-card" kicker="RESEARCH CONTEXT" title="判断上下文"><div className="research-context-grid"><div><span>Open questions · {openQuestions.length}</span>{openQuestions.length === 0 ? <p className="muted">暂无未决问题。</p> : <ul>{openQuestions.map((question) => <li key={text(question.question_id)}>{text(question.text)}</li>)}</ul>}</div><div><span>Current Trade Plan</span>{currentTradePlan ? <pre className="research-json">{displayJson(currentTradePlan)}</pre> : <p className="muted">暂无已确认 Trade Plan。</p>}</div></div></Card>
      <MonitorLinks monitorData={monitorData} loading={monitorLoading} caseId={text(researchCase.case_id)} />
      <details className="research-raw"><summary>查看本 Case durable state 原文</summary><pre>{displayJson(state)}</pre></details>
      <div id={`research-detail-${text(researchCase.case_id)}`} className="sr-only">{text(researchCase.case_id)}</div>
    </div>
  );
}

export default function ResearchPage() {
  const result = useApi<Dict>("/api/research");
  const monitorResult = useApi<Dict>("/api/monitors?run_limit=1&event_limit=1");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("ALL");
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);
  const [caseEditor, setCaseEditor] = useState(false);
  const [caseDraft, setCaseDraft] = useState<CaseDraft>(EMPTY_CASE_DRAFT);
  const [writing, setWriting] = useState(false);
  const [writeError, setWriteError] = useState<string | null>(null);
  const [writeSuccess, setWriteSuccess] = useState<string | null>(null);
  const items = listOf<CaseAggregate>(result.data, "cases");
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const filtered = useMemo(() => items.filter((item) => { const researchCase = item.case ?? {}; const matchesStatus = status === "ALL" || text(researchCase.status).toUpperCase() === status; return matchesStatus && (!normalizedQuery || caseSearchText(item).includes(normalizedQuery)); }), [items, normalizedQuery, status]);

  useEffect(() => {
    if (filtered.length === 0) { setSelectedCaseId(null); return; }
    if (!selectedCaseId || !filtered.some((item) => String(item.case?.case_id) === selectedCaseId)) setSelectedCaseId(String(filtered[0].case?.case_id));
  }, [filtered, selectedCaseId]);

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

  async function createCase() {
    const title = caseDraft.title.trim();
    const summary = caseDraft.summary.trim();
    if (!title || !summary) { window.alert("标题和摘要不能为空。"); return; }
    try {
      const response = await write("investment_case_manage", { operation: "create", case_type: caseDraft.caseType, title, summary, primary_instrument_id: caseDraft.instrument.trim() || null, topic_tags: splitList(caseDraft.tags), linked_case_ids: splitList(caseDraft.linkedCaseIds), confirmed_by: "user", idempotency_key: idempotencyKey("case-create") }, "investment_case_manage");
      const created = envelopeData<Dict>(response);
      const createdId = created?.case_id;
      if (typeof createdId === "string") setSelectedCaseId(createdId);
      setCaseEditor(false);
      setCaseDraft(EMPTY_CASE_DRAFT);
      result.refresh();
    } catch { /* write() keeps the local error visible */ }
  }

  const selected = filtered.find((item) => String(item.case?.case_id) === selectedCaseId) ?? null;
  return (
    <ConsoleShell active="research" eyebrow="Durable judgment memory" title="Research 工作区">
      <DataBoundary loading={result.loading} error={result.error}>
        <div className="research-page">
          <div className="toolbar research-toolbar"><p>Case 元数据可在这里编辑；Thesis 只能通过 append-only candidate 提出，再由你显式确认或拒绝。这个工作区不刷新 Provider、不确认判断，也不操作仓位。</p><div className="toolbar-actions"><ActionButton onClick={() => { setCaseDraft(EMPTY_CASE_DRAFT); setCaseEditor((value) => !value); }}>{caseEditor ? "关闭新建" : "新建 Case"}</ActionButton><RefreshButton onClick={result.refresh} loading={result.loading} /></div></div>
          {writeError && <div className="inline-error" role="alert">{writeError}</div>}
          {writeSuccess && <div className="inline-success" role="status">{writeSuccess}</div>}
          {caseEditor && <CaseEditor draft={caseDraft} editing={false} busy={writing} onChange={setCaseDraft} onCancel={() => setCaseEditor(false)} onSave={() => { void createCase(); }} />}
          <div className="research-master-detail">
            <aside className="research-index"><Card className="research-index-card" kicker="CASE INDEX" title="所有 Case" action={<span className="muted">{filtered.length} / {items.length}</span>}><div className="research-filters"><Field label="文本筛选"><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="标题、标的、标签、Thesis" aria-label="筛选 Research Case" /></Field><Field label="Case status"><select value={status} onChange={(event) => setStatus(event.target.value)} aria-label="按 Case 状态筛选"><option value="ALL">全部（含 archived）</option>{CASE_STATUSES.map((value) => <option key={value} value={value.toUpperCase()}>{value}</option>)}</select></Field></div>{items.length === 0 ? <Empty>没有持久化 Investment Case。</Empty> : filtered.length === 0 ? <Empty>没有匹配当前筛选条件的 Case。</Empty> : <div className="research-case-index-list">{filtered.map((item) => { const researchCase = item.case ?? {}; const caseId = String(researchCase.case_id ?? ""); const state = stateData(item) ?? {}; const thesisCount = listOf<Dict>(state, "theses").length; return <button type="button" id={`research-case-${caseId}`} className={`research-index-item ${selectedCaseId === caseId ? "selected" : ""}`} onClick={() => setSelectedCaseId(caseId)} key={caseId}><span className="research-index-status"><Badge value={text(researchCase.status, "UNKNOWN").toUpperCase()} /></span><strong>{text(researchCase.title, "未命名 Case")}</strong><small>{shortId(researchCase.primary_instrument_id)} · {thesisCount} Thesis</small><time>{formatDate(researchCase.updated_at)}</time></button>; })}</div>}</Card></aside>
            <main className="research-detail" aria-live="polite">{selected ? <ResearchCaseDetail item={selected} monitorData={monitorResult.data} monitorLoading={monitorResult.loading} onSelectCase={setSelectedCaseId} onRefresh={result.refresh} onWrite={write} busy={writing} /> : <Empty>从左侧选择一个 Case。</Empty>}</main>
          </div>
          {monitorResult.error && <div className="inline-error">关联 Monitor 读取失败：{monitorResult.error}。Research Case 仍可正常查看和编辑。</div>}
        </div>
      </DataBoundary>
    </ConsoleShell>
  );
}
