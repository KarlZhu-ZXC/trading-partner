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

const SUBJECT_STATUSES = ["draft", "active", "archived"];
const SUBJECT_TYPES = ["company", "theme", "macro", "catalyst", "portfolio_concern"];
const THESIS_ROLES = ["primary", "sub", "competitor", "bear"];
const THESIS_STATUSES = ["draft", "active", "strengthened", "weakened", "invalidated", "archived"];
const LIVE_THESIS_STATUSES = new Set(["active", "strengthened", "weakened"]);
const CONFIDENCE_BANDS = ["low", "medium", "high"];
const RATINGS = ["avoid", "watch", "speculative_buy", "buy", "sell", "hold"];
const INVALIDATION_SEVERITIES = ["soft", "hard"];
const SELECTION_STATUSES = new Set(["watching", "shortlisted", "selected", "rejected"]);

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
  return first ? `${text(first.code, "RESEARCH_STATE_READ_FAILED")} · ${text(first.message, "Unable to read Thesis")}` : "Research state read failed; no error details returned.";
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
    throw new Error(`${text(first?.code, "WRITE_FAILED")} · ${text(first?.message, "Local write failed")}`);
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
    <Card className="research-editor-card" kicker={editing ? "SUBJECT METADATA · AUDITED UPDATE" : "RESEARCH SUBJECT"} title={editing ? "Edit Research Subject metadata" : "Create Research Subject"}>
      <p className="card-note">Research Subject metadata writes leave an auditable confirmation record; they do not modify Thesis revisions or positions.</p>
      <div className="research-form-grid">
        <Field label="Research Subject type"><select value={draft.subjectType} disabled={editing} onChange={(event) => onChange({ ...draft, subjectType: event.target.value })}>{SUBJECT_TYPES.map((value) => <option key={value} value={value}>{value}</option>)}</select></Field>
        <Field label={draft.subjectType === "company" || draft.subjectType === "catalyst" ? "Primary Instrument ID (required)" : "Primary Instrument ID (optional)"}><input value={draft.instrument} disabled={editing} onChange={(event) => onChange({ ...draft, instrument: event.target.value })} placeholder={draft.subjectType === "theme" ? "Leave blank during theme selection" : "equity:US:NVDA"} /><small>{draft.subjectType === "theme" ? "For example, keep this blank for “innovative drug ETF selection”; manage candidate ETFs in the pool below." : "This field defines the research object's identity and cannot be changed after creation."}</small></Field>
        <Field label="Title" className="research-field-wide"><input value={draft.title} onChange={(event) => onChange({ ...draft, title: event.target.value })} placeholder="e.g. NVDA AI infrastructure tracking" /></Field>
        <Field label="Summary" className="research-field-wide"><textarea value={draft.summary} onChange={(event) => onChange({ ...draft, summary: event.target.value })} rows={5} placeholder="Record the long-term question, scope, and boundaries for this Research Subject." /></Field>
        <Field label="Topic tags"><input value={draft.tags} onChange={(event) => onChange({ ...draft, tags: event.target.value })} placeholder="ai, valuation, catalyst" /></Field>
        <Field label="Linked Research Subject IDs"><textarea value={draft.linkedSubjectIds} onChange={(event) => onChange({ ...draft, linkedSubjectIds: event.target.value })} rows={2} placeholder="One case_<uuid7> per line" /></Field>
      </div>
      <div className="research-form-actions"><ActionButton onClick={onSave} busy={busy}>{editing ? "Save Research Subject" : "Create Research Subject"}</ActionButton><button className="close-button" type="button" onClick={onCancel}>Cancel</button></div>
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
  parentThesisId: string;
  rivalThesisIds: string[];
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
  parentThesisId: "",
  rivalThesisIds: [],
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
    parentThesisId: text(thesis?.parent_thesis_id, ""),
    rivalThesisIds: stringList(thesis?.rival_thesis_ids),
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
  availableTheses,
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
  availableTheses: Dict[];
  statusExplicit: boolean;
  subjectStatus: string;
  busy: boolean;
  onChange: (next: ThesisDraft) => void;
  onStatusExplicitChange: (next: boolean) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  const relationshipTargets = availableTheses.filter((item) => text(item.thesis_id) !== thesisId);
  const primaryTargets = relationshipTargets.filter((item) => text(item.role) === "primary");
  function updateAssumption(index: number, key: keyof AssumptionDraft, value: string) {
    const assumptions = draft.assumptions.map((item, itemIndex) => itemIndex === index ? { ...item, [key]: value } : item);
    onChange({ ...draft, assumptions });
  }
  function updateInvalidation(index: number, key: keyof InvalidationDraft, value: string) {
    const invalidations = draft.invalidations.map((item, itemIndex) => itemIndex === index ? { ...item, [key]: value } : item);
    onChange({ ...draft, invalidations });
  }
  return (
    <section className="research-thesis-editor" aria-label={thesisId ? "Edit Thesis revision" : "Create Thesis"}>
      <div className="research-thesis-editor-heading"><div><p className="card-kicker">{thesisId ? "THESIS REVISION · APPEND ONLY" : "THESIS · CANDIDATE"}</p><h3>{thesisId ? "Edit Thesis · Propose New Revision" : "Create New Thesis"}</h3></div><Badge value={subjectStatus.toUpperCase()} /></div>
      <p className="card-note">Historical revisions are never overwritten. Saving creates a pending candidate that must be explicitly Confirmed or Rejected.</p>
      <div className="research-form-grid">
        <Field label="Title" className="research-field-wide"><input value={draft.title} onChange={(event) => onChange({ ...draft, title: event.target.value })} /></Field>
        <Field label="Thesis Role"><select value={draft.thesisRole} onChange={(event) => onChange({ ...draft, thesisRole: event.target.value, parentThesisId: event.target.value === "sub" ? draft.parentThesisId : "" })}>{THESIS_ROLES.map((value) => <option key={value} value={value}>{value}</option>)}</select></Field>
        {draft.thesisRole === "sub" && <Field label="Parent PRIMARY Thesis"><select value={draft.parentThesisId} onChange={(event) => onChange({ ...draft, parentThesisId: event.target.value, rivalThesisIds: draft.rivalThesisIds.filter((id) => id !== event.target.value) })}><option value="">Select a parent Thesis</option>{primaryTargets.map((item) => <option value={text(item.thesis_id)} key={text(item.thesis_id)}>{text(item.title, "Unnamed PRIMARY")} · {text(item.status)}</option>)}</select></Field>}
        <Field label="Rival Theses" className="research-field-wide"><div className="research-thesis-relation-options">{relationshipTargets.length === 0 ? <span className="muted">No other Theses available.</span> : relationshipTargets.map((item) => { const id = text(item.thesis_id); const disabled = id === draft.parentThesisId; return <label key={id}><input type="checkbox" checked={draft.rivalThesisIds.includes(id)} disabled={disabled} onChange={(event) => onChange({ ...draft, rivalThesisIds: event.target.checked ? [...draft.rivalThesisIds, id] : draft.rivalThesisIds.filter((value) => value !== id) })} /><span>{text(item.title, "Unnamed Thesis")} · {text(item.role).toUpperCase()} · {text(item.status)}</span></label>; })}</div><small>Use this to declare competing explanations or contrary judgments; a parent Thesis cannot also be marked as a rival.</small></Field>
        <Field label="Candidate status"><div className="research-status-control">{thesisId && <label className="research-status-toggle"><input type="checkbox" checked={statusExplicit} onChange={(event) => onStatusExplicitChange(event.target.checked)} /><span>Also update status</span></label>}<select value={draft.thesisStatus} disabled={Boolean(thesisId) && !statusExplicit} onChange={(event) => onChange({ ...draft, thesisStatus: event.target.value })}>{THESIS_STATUSES.map((value) => <option key={value} value={value}>{value}</option>)}</select></div></Field>
        <Field label="Confidence"><select value={draft.confidenceBand} onChange={(event) => onChange({ ...draft, confidenceBand: event.target.value })}>{CONFIDENCE_BANDS.map((value) => <option key={value} value={value}>{value}</option>)}</select></Field>
        <Field label="Rating"><select value={draft.rating} onChange={(event) => onChange({ ...draft, rating: event.target.value })}>{RATINGS.map((value) => <option key={value} value={value}>{value}</option>)}</select></Field>
        <Field label="Replacement revision no"><input inputMode="numeric" value={draft.replacesRevisionNo} onChange={(event) => onChange({ ...draft, replacesRevisionNo: event.target.value.replace(/[^0-9]/g, "") })} placeholder="Current revision no" /></Field>
        <Field label="Statement" className="research-field-wide"><textarea value={draft.statement} onChange={(event) => onChange({ ...draft, statement: event.target.value })} rows={5} /></Field>
        <Field label="Rationale" className="research-field-wide"><textarea value={draft.rationale} onChange={(event) => onChange({ ...draft, rationale: event.target.value })} rows={5} /></Field>
        <Field label="Invalidation check note" className="research-field-wide"><textarea value={draft.invalidationCheckNote} onChange={(event) => onChange({ ...draft, invalidationCheckNote: event.target.value })} rows={4} /></Field>
      </div>
      {subjectStatus === "draft" && ["active", "strengthened", "weakened"].includes(draft.thesisStatus) && (!thesisId || statusExplicit) && <div className="research-state-warning" role="status">A Draft Research Subject cannot confirm a live Thesis. Submit and confirm a Research Subject activation candidate first, or keep Thesis status at DRAFT.</div>}
      <div className="research-array-editor">
        <div className="research-array-heading"><div><p className="card-kicker">ASSUMPTIONS</p><h3>Assumptions</h3></div><button className="close-button" type="button" onClick={() => onChange({ ...draft, assumptions: [...draft.assumptions, { statement: "", basis: "", falsifiability: "" }] })}>Add assumption</button></div>
        {draft.assumptions.length === 0 ? <p className="muted">No assumptions yet; add at least one if this judgment should be challenged over time.</p> : draft.assumptions.map((item, index) => <div className="research-array-row" key={`assumption-${index}`}><Field label="Statement"><textarea rows={3} value={item.statement} onChange={(event) => updateAssumption(index, "statement", event.target.value)} /></Field><Field label="Basis"><textarea rows={3} value={item.basis} onChange={(event) => updateAssumption(index, "basis", event.target.value)} /></Field><Field label="Falsifiability"><textarea rows={3} value={item.falsifiability} onChange={(event) => updateAssumption(index, "falsifiability", event.target.value)} /></Field><button className="close-button" type="button" onClick={() => onChange({ ...draft, assumptions: draft.assumptions.filter((_, itemIndex) => itemIndex !== index) })}>Remove</button></div>)}
      </div>
      <div className="research-array-editor">
        <div className="research-array-heading"><div><p className="card-kicker">INVALIDATIONS</p><h3>Invalidation conditions</h3></div><button className="close-button" type="button" onClick={() => onChange({ ...draft, invalidations: [...draft.invalidations, { description: "", observable: "", severity: "soft" }] })}>Add condition</button></div>
        {draft.invalidations.length === 0 ? <p className="muted">No invalidation conditions yet.</p> : draft.invalidations.map((item, index) => <div className="research-array-row" key={`invalidation-${index}`}><Field label="Description"><textarea rows={3} value={item.description} onChange={(event) => updateInvalidation(index, "description", event.target.value)} /></Field><Field label="Observable"><textarea rows={3} value={item.observable} onChange={(event) => updateInvalidation(index, "observable", event.target.value)} /></Field><Field label="Severity"><select value={item.severity} onChange={(event) => updateInvalidation(index, "severity", event.target.value)}>{INVALIDATION_SEVERITIES.map((value) => <option key={value} value={value}>{value}</option>)}</select></Field><button className="close-button" type="button" onClick={() => onChange({ ...draft, invalidations: draft.invalidations.filter((_, itemIndex) => itemIndex !== index) })}>Remove</button></div>)}
      </div>
      <div className="research-form-actions"><ActionButton onClick={onSave} busy={busy}>Propose candidate</ActionButton><button className="close-button" type="button" onClick={onCancel}>Cancel</button></div>
    </section>
  );
}

function MonitorLinks({ monitorData, subjectId, loading }: { monitorData: Dict | null; subjectId: string; loading: boolean }) {
  const dashboard = envelopeData<Dict>(monitorData?.dashboard);
  const monitorItems = listOf<MonitorAggregate>(dashboard, "items");
  const linked = monitorItems.map((item) => item.monitor ?? item).filter((monitor) => text(monitor.subject_id, "") === subjectId);
  return (
    <Card className="research-related-card" kicker="MONITOR LINKS" title="Linked Monitor" action={<Link className="text-link" href="/monitors">Open Monitor workspace</Link>}>
      {loading ? <Empty>Reading Monitor links…</Empty> : linked.length === 0 ? <Empty>This Research Subject has no linked Monitors. Bind one by exact Research Subject ID in the Monitor workspace.</Empty> : <div className="research-monitor-links">{linked.map((monitor) => <Link href={`/monitors#monitor-${text(monitor.monitor_id)}`} className="research-monitor-link" key={text(monitor.monitor_id)}><div><strong>{text(monitor.name, "Unnamed Monitor")}</strong><small>{shortId(monitor.primary_instrument_id)} · {text(monitor.cadence)}</small></div><Badge value={text(monitor.status, "UNKNOWN")} /></Link>)}</div>}
    </Card>
  );
}

function ThesisSummary({ thesis, revision, assumptions, invalidations }: { thesis: Dict; revision?: Dict; assumptions: Dict[]; invalidations: Dict[] }) {
  return (
    <article className="research-thesis-summary">
      <header><div><strong>{text(thesis.title, "Unnamed Thesis")}</strong><small className="mono">{text(thesis.thesis_id)}</small></div><div className="research-thesis-badges"><Badge value={text(thesis.status, "UNKNOWN").toUpperCase()} /><span className="research-role">{text(thesis.role).toUpperCase()}</span></div></header>
      <div className="research-revision-grid"><div className="research-statement"><span>Latest revision · statement</span><p>{text(revision?.statement, "No latest revision statement.")}</p></div><div><span>Rating</span><strong>{text(revision?.rating).toUpperCase()}</strong></div><div><span>Confidence</span><strong>{text(revision?.confidence_band).toUpperCase()}</strong></div><div><span>Current revision</span><strong>v{text(thesis.current_revision_no)}</strong><small>latest v{text(revision?.revision_no)}</small></div><div><span>Status</span><strong>{text(thesis.status).toUpperCase()}</strong></div><div><span>Role</span><strong>{text(thesis.role).toUpperCase()}</strong></div></div>
      {revision && <div className="research-thesis-detail-grid"><div><span>Rationale</span><p>{text(revision.rationale)}</p></div><div><span>Invalidation check</span><p>{text(revision.invalidation_check_note)}</p></div></div>}
      {(text(thesis.parent_thesis_id, "") || stringList(thesis.rival_thesis_ids).length > 0) && <div className="research-thesis-relations">{text(thesis.parent_thesis_id, "") && <span>Parent Thesis · <code>{shortId(thesis.parent_thesis_id)}</code></span>}{stringList(thesis.rival_thesis_ids).length > 0 && <span>Rivals · {stringList(thesis.rival_thesis_ids).map((id) => <code key={id}>{shortId(id)}</code>)}</span>}</div>}
      <div className="research-inline-columns"><div><span>Assumptions · {assumptions.length}</span>{assumptions.length === 0 ? <small className="muted">None</small> : <ul>{assumptions.map((item) => <li key={text(item.assumption_id)}>{text(item.statement)}</li>)}</ul>}</div><div><span>Invalidation conditions · {invalidations.length}</span>{invalidations.length === 0 ? <small className="muted">None</small> : <ul>{invalidations.map((item) => <li key={text(item.invalidation_id)}>{text(item.description)}</li>)}</ul>}</div></div>
    </article>
  );
}

function ThesisRelationshipList({ theses, revisions, assumptions, invalidations, onEdit }: { theses: Dict[]; revisions: Map<string, Dict>; assumptions: Dict[]; invalidations: Dict[]; onEdit: (target?: Dict, createNew?: boolean) => void }) {
  const byId = new Map(theses.map((item) => [text(item.thesis_id), item]));
  const roots = theses.filter((item) => text(item.role) !== "sub" || !byId.has(text(item.parent_thesis_id, "")));
  const childrenByParent = new Map<string, Dict[]>();
  for (const item of theses.filter((candidate) => text(candidate.role) === "sub")) {
    const parentId = text(item.parent_thesis_id, "");
    childrenByParent.set(parentId, [...(childrenByParent.get(parentId) ?? []), item]);
  }
  const renderThesis = (thesis: Dict, depth: number): ReactNode => {
    const thesisId = text(thesis.thesis_id);
    return <div className={`research-thesis-node depth-${depth}`} key={thesisId}><ThesisSummary thesis={thesis} revision={revisions.get(thesisId)} assumptions={assumptions.filter((item) => String(item.thesis_id) === thesisId)} invalidations={invalidations.filter((item) => String(item.thesis_id) === thesisId)} /><div className="research-thesis-actions"><button className="close-button" type="button" onClick={() => onEdit(thesis)}>Edit Thesis · New Revision</button></div>{(childrenByParent.get(thesisId) ?? []).map((child) => renderThesis(child, depth + 1))}</div>;
  };
  return <div className="research-thesis-list">{roots.map((thesis) => renderThesis(thesis, 0))}</div>;
}

function PendingCandidate({ candidate, subjectStatus, onConfirm, onReject, onWithdraw, busy }: { candidate: Dict; subjectStatus: string; onConfirm: (candidate: Dict, action: "confirm" | "reject" | "withdraw", reason?: string) => void; onReject: (candidate: Dict) => void; onWithdraw: (candidate: Dict) => void; busy: boolean }) {
  const [rejectionReason, setRejectionReason] = useState("");
  const payload = candidate.payload && typeof candidate.payload === "object" ? candidate.payload as Dict : {};
  const kind = text(payload.kind, text(candidate.kind, "thesis_revision"));
  const isSubjectStatus = kind === "subject_status_change" || kind === "case_status_change";
  const isInstrumentCandidate = kind === "watchlist_item";
  const summary = isSubjectStatus
    ? `${subjectStatus.toUpperCase()} → ${text(payload.new_status, "UNKNOWN").toUpperCase()}`
    : isInstrumentCandidate
      ? payload.action === "create" ? `Add candidate Instrument: ${text(payload.display_name)} · ${text(payload.instrument_id)}` : `Candidate status → ${text(payload.new_status).toUpperCase()}${payload.selection_reason ? ` · ${text(payload.selection_reason)}` : ""}`
      : text(payload.statement, text(payload.title, "Candidate revision"));
  const confirmCopy = isSubjectStatus ? `Confirm this Research Subject status change: ${summary}?` : isInstrumentCandidate ? `Confirm this Instrument Selection change: ${summary}?` : "Confirm this Thesis candidate? Historical revisions will not be overwritten.";
  return <article className="research-candidate"><header><div><strong>{text(candidate.candidate_id)}</strong><small>{kind} · {text(candidate.proposed_by, "unknown")}</small></div><Badge value={text(candidate.status, "PROPOSED").toUpperCase()} /></header><p>{summary}</p><div className="research-candidate-actions"><ActionButton onClick={() => { if (window.confirm(confirmCopy)) onConfirm(candidate, "confirm"); }} busy={busy}>Confirm</ActionButton><input value={rejectionReason} onChange={(event) => setRejectionReason(event.target.value)} placeholder="Rejection reason (required)" aria-label="Candidate rejection reason" /><ActionButton tone="warning" onClick={() => { if (!rejectionReason.trim()) { window.alert("A rejection reason is required."); return; } if (window.confirm("Reject this candidate?")) onReject({ ...candidate, rejectionReason }); }} busy={busy}>Reject</ActionButton><button className="close-button" type="button" disabled={busy} onClick={() => { if (window.confirm("Withdraw this pending candidate?")) onWithdraw(candidate); }}>Withdraw</button></div></article>;
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
  const instrumentCandidates = listOf<Dict>(state, "watchlist_items").filter((candidate) => SELECTION_STATUSES.has(text(candidate.status, "")));
  const pendingCandidates = listOf<Dict>(state, "pending_candidates");
  const liveTheses = theses.filter((thesis) => ["active", "strengthened", "weakened"].includes(text(thesis.status, "").toLowerCase()));
  const continuitySignals = [
    ...(String(researchSubject.status).toLowerCase() === "active" && liveTheses.length === 0
      ? [{ key: "live-thesis", severity: "ACTION", title: "No live Thesis", detail: "Tracking is active, but no current falsifiable judgment is live." }]
      : []),
    ...(liveTheses.length > 0 && assumptions.length === 0
      ? [{ key: "assumptions", severity: "GAP", title: "Assumptions are not explicit", detail: "Record the premises that must remain true for the live Thesis." }]
      : []),
    ...(liveTheses.length > 0 && invalidations.length === 0
      ? [{ key: "invalidations", severity: "GAP", title: "Invalidation is not explicit", detail: "Add a falsifiable condition that would retire or weaken the judgment." }]
      : []),
    ...(openQuestions.length > 0
      ? [{ key: "questions", severity: "OPEN", title: `${openQuestions.length} open question${openQuestions.length === 1 ? "" : "s"}`, detail: "Resolve these with new evidence before increasing conviction." }]
      : []),
    ...(pendingCandidates.length > 0
      ? [{ key: "candidates", severity: "REVIEW", title: `${pendingCandidates.length} candidate${pendingCandidates.length === 1 ? "" : "s"} awaiting review`, detail: "Confirm, reject, or withdraw each exact proposal." }]
      : []),
  ];
  const [subjectEditor, setSubjectEditor] = useState(false);
  const [subjectDraft, setSubjectDraft] = useState(() => subjectDraftFrom(researchSubject));
  const [thesisEditor, setThesisEditor] = useState(false);
  const [thesisId, setThesisId] = useState<string | null>(null);
  const [thesisDraft, setThesisDraft] = useState<ThesisDraft>(EMPTY_THESIS_DRAFT);
  const [thesisStatusExplicit, setThesisStatusExplicit] = useState(false);
  const [candidateInstrumentId, setCandidateInstrumentId] = useState("");
  const [candidateDisplayName, setCandidateDisplayName] = useState("");
  const [candidateThesisHint, setCandidateThesisHint] = useState("");

  useEffect(() => {
    setSubjectDraft(subjectDraftFrom(researchSubject));
    setSubjectEditor(false);
    setThesisEditor(false);
    setThesisId(null);
    setThesisStatusExplicit(false);
    setCandidateInstrumentId("");
    setCandidateDisplayName("");
    setCandidateThesisHint("");
  }, [researchSubject.subject_id, researchSubject.updated_at]);


  async function saveSubject() {
    const title = subjectDraft.title.trim();
    const summary = subjectDraft.summary.trim();
    if (!title || !summary) { window.alert("Title and summary are required."); return; }
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
    if (!window.confirm("Archive this Research Subject? Archiving does not delete historical Theses, evidence, or Monitors.")) return;
    const reason = window.prompt("Enter an archive reason:", "Research scope ended or judgment invalidated")?.trim();
    if (!reason) return;
    try {
      await onWrite("investment_case_manage", { operation: "archive", case_id: text(researchSubject.subject_id), archived_reason: reason, reviewed_by: "user", idempotency_key: idempotencyKey("subject-archive") }, "investment_case_manage");
      onRefresh();
    } catch { /* onWrite keeps the local error visible */ }
  }

  async function restoreSubject() {
    if (!window.confirm("Restore this archived Research Subject to Draft? Historical Theses, Trade Plans, and audit records will be preserved.")) return;
    try {
      const proposed = await onWrite("research_judgment_propose", {
        operation: "research_state",
        case_id: text(researchSubject.subject_id),
        payload: { kind: "case_status_change", action: "update", new_status: "draft" },
        confirmation_mode: "strict_review",
        proposed_by: "user",
        proposed_by_rationale: "User requested restoring the archived Research Subject to Draft from the local Research workspace",
        idempotency_key: idempotencyKey("subject-restore-propose"),
      }, "research_judgment_propose");
      const candidate = envelopeData<Dict>(proposed);
      const candidateId = candidate?.candidate_id;
      if (typeof candidateId !== "string" || !candidateId) throw new Error("Restore candidate did not return candidate_id.");
      await onWrite("research_judgment_confirm", {
        operation: "candidate",
        candidate_id: candidateId,
        action: "confirm",
        reviewed_by: "user",
        submitted_via: "direct",
        review_note: "User withdrew the archive and restored the Research Subject to Draft from the local Research workspace",
      }, "research_judgment_confirm");
      onRefresh();
    } catch { /* onWrite keeps the local error visible */ }
  }

  async function activateSubject() {
    if (!window.confirm("Submit a candidate to move this Research Subject from Draft to Active? It still requires explicit confirmation in the review queue.")) return;
    try {
      await onWrite("research_judgment_propose", {
        operation: "research_state",
        case_id: text(researchSubject.subject_id),
        payload: { kind: "case_status_change", action: "update", new_status: "active" },
        confirmation_mode: "strict_review",
        proposed_by: "user",
        proposed_by_rationale: "User requested moving the Draft Research Subject to Active tracking from the local Research workspace",
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
    if (!thesis && theses.some((item) => text(item.role) === "primary" && LIVE_THESIS_STATUSES.has(text(item.status)))) {
      draft.thesisRole = "competitor";
    }
    setThesisDraft(draft);
    setThesisStatusExplicit(!thesis);
    setThesisEditor(true);
  }

  async function saveThesis() {
    const invalidAssumption = thesisDraft.assumptions.find((item) => item.statement.trim() && (!item.basis.trim() || !item.falsifiability.trim()));
    const invalidInvalidation = thesisDraft.invalidations.find((item) => item.description.trim() && !item.observable.trim());
    if (invalidAssumption) { window.alert("Every assumption needs Basis and Falsifiability."); return; }
    if (invalidInvalidation) { window.alert("Every invalidation condition needs an Observable."); return; }
    const subjectStatus = String(researchSubject.status).toLowerCase();
    const changesToLiveStatus = ["active", "strengthened", "weakened"].includes(thesisDraft.thesisStatus) && (!thesisId || thesisStatusExplicit);
    if (subjectStatus === "draft" && changesToLiveStatus) { window.alert("A Draft Research Subject cannot confirm a live Thesis. Activate the Research Subject first, or choose DRAFT status."); return; }
    if (thesisDraft.thesisRole === "sub" && !thesisDraft.parentThesisId) { window.alert("A SUB Thesis must select a parent PRIMARY Thesis."); return; }
    const effectiveLive = thesisStatusExplicit || !thesisId ? LIVE_THESIS_STATUSES.has(thesisDraft.thesisStatus) : LIVE_THESIS_STATUSES.has(text(theses.find((item) => text(item.thesis_id) === thesisId)?.status, ""));
    const otherLivePrimary = theses.some((item) => text(item.thesis_id) !== thesisId && text(item.role) === "primary" && LIVE_THESIS_STATUSES.has(text(item.status)));
    if (thesisDraft.thesisRole === "primary" && effectiveLive && otherLivePrimary) { window.alert("This Research Subject already has a live PRIMARY Thesis. Use SUB, COMPETITOR, or BEAR, or retire the existing PRIMARY first."); return; }
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
      parent_thesis_id: thesisDraft.thesisRole === "sub" ? thesisDraft.parentThesisId : null,
      rival_thesis_ids: thesisDraft.rivalThesisIds,
      replaces_revision_no: thesisDraft.replacesRevisionNo ? Number(thesisDraft.replacesRevisionNo) : null,
    };
    if (!thesisId || thesisStatusExplicit) payload.thesis_status = thesisDraft.thesisStatus;
    if (!payload.title || !payload.statement || !payload.rationale || !payload.invalidation_check_note) { window.alert("Thesis title, statement, rationale, and invalidation check note are required."); return; }
    try {
      await onWrite("research_judgment_propose", { operation: "thesis_revision", case_id: text(researchSubject.subject_id), thesis_id: thesisId, payload, proposed_by: "user", proposed_by_rationale: "Thesis revision proposed from the local Research workspace", idempotency_key: idempotencyKey("thesis-propose") }, "research_judgment_propose");
      setThesisEditor(false);
      onRefresh();
    } catch { /* onWrite keeps the local error visible */ }
  }

  async function decideCandidate(candidate: Dict, action: "confirm" | "reject" | "withdraw", reason?: string) {
    const request: Dict = { operation: "candidate", candidate_id: text(candidate.candidate_id), action, reviewed_by: "user", submitted_via: "direct" };
    if (action === "reject") request.rejection_reason = reason;
    else request.review_note = action === "withdraw" ? "User withdrew the candidate from the local Research workspace" : "Confirmed from the local Research workspace";
    try {
      await onWrite("research_judgment_confirm", request, "research_judgment_confirm");
      onRefresh();
    } catch { /* onWrite keeps the local error visible */ }
  }

  async function proposeInstrumentCandidate() {
    if (!candidateInstrumentId.trim() || !candidateDisplayName.trim() || !candidateThesisHint.trim()) {
      window.alert("Instrument ID, display name, and selection reason are required.");
      return;
    }
    try {
      await onWrite("research_judgment_propose", {
        operation: "research_state",
        case_id: text(researchSubject.subject_id),
        payload: {
          kind: "watchlist_item",
          action: "create",
          instrument_id: candidateInstrumentId.trim(),
          display_name: candidateDisplayName.trim(),
          thesis_hint: candidateThesisHint.trim(),
          triggers: [],
        },
        confirmation_mode: "strict_review",
        proposed_by: "user",
        proposed_by_rationale: "Instrument Selection candidate pool maintained from the local Research workspace",
        idempotency_key: idempotencyKey("instrument-candidate-create"),
      }, "research_judgment_propose");
      setCandidateInstrumentId("");
      setCandidateDisplayName("");
      setCandidateThesisHint("");
      onRefresh();
    } catch { /* onWrite keeps the local error visible */ }
  }

  async function proposeCandidateStatus(candidate: Dict, newStatus: string) {
    let selectionReason: string | null = null;
    if (newStatus === "selected" || newStatus === "rejected") {
      selectionReason = window.prompt(newStatus === "selected" ? "Enter the final selection reason:" : "Enter the rejection reason:")?.trim() || null;
      if (!selectionReason) return;
    }
    try {
      await onWrite("research_judgment_propose", {
        operation: "research_state",
        case_id: text(researchSubject.subject_id),
        payload: {
          kind: "watchlist_item",
          action: "update_status",
          item_id: text(candidate.item_id),
          new_status: newStatus,
          selection_reason: selectionReason,
        },
        confirmation_mode: "strict_review",
        proposed_by: "user",
        proposed_by_rationale: "Instrument Selection status updated from the local Research workspace",
        idempotency_key: idempotencyKey(`instrument-candidate-${newStatus}`),
      }, "research_judgment_propose");
      onRefresh();
    } catch { /* onWrite keeps the local error visible */ }
  }

  const tags = stringList(researchSubject.topic_tags);
  return (
    <div className="research-detail-stack">
      <Card className="research-subject-detail" kicker={text(researchSubject.subject_type, "RESEARCH SUBJECT")} title={text(researchSubject.title, "Unnamed Research Subject")} action={<div className="research-detail-actions"><Badge value={text(researchSubject.status, "UNKNOWN").toUpperCase()} /><button className="close-button" type="button" onClick={() => setSubjectEditor((value) => !value)}>{subjectEditor ? "Close editor" : "Edit Research Subject"}</button>{String(researchSubject.status).toLowerCase() === "draft" && <button className="close-button restore-text" type="button" disabled={busy} onClick={() => { void activateSubject(); }}>Start tracking</button>}{String(researchSubject.status).toLowerCase() === "archived" ? <button className="close-button restore-text" type="button" disabled={busy} onClick={() => { void restoreSubject(); }}>Restore to Draft</button> : <button className="close-button warning-text" type="button" disabled={busy} onClick={archiveSubject}>Archive</button>}</div>}>
        <div className="research-subject-meta"><div><span>Instrument</span><strong>{shortId(researchSubject.primary_instrument_id)}</strong><small>{text(researchSubject.primary_instrument_id)}</small></div><div><span>Status</span><strong>{text(researchSubject.status)}</strong></div><div><span>Created</span><strong>{formatDate(researchSubject.created_at)}</strong></div><div><span>Updated</span><strong>{formatDate(researchSubject.updated_at)}</strong></div></div>
        <p className="research-summary">{text(researchSubject.summary, "No Research Subject summary.")}</p>
        <div className="research-tags" aria-label="Research Subject tags">{tags.length === 0 ? <span className="muted">No tags</span> : tags.map((tag) => <span key={tag}>{tag}</span>)}</div>
        {stringList(researchSubject.linked_subject_ids).length > 0 && <div className="research-linked-subjects"><span>Linked Research Subjects</span>{stringList(researchSubject.linked_subject_ids).map((subjectId) => <button type="button" key={subjectId} onClick={() => onSelectSubject(subjectId)}>{shortId(subjectId)}</button>)}</div>}
        {failure && <div className="research-state-error" role="status"><strong>Partial read failed</strong><span>{failure}</span><small>Research Subject metadata remains editable; fix state loading before editing Thesis.</small></div>}
      </Card>
      {subjectEditor && <SubjectEditor draft={subjectDraft} editing busy={busy} onChange={setSubjectDraft} onCancel={() => setSubjectEditor(false)} onSave={() => { void saveSubject(); }} />}
      <Card className="research-continuity-check" kicker="CONTINUITY CHECK" title="Next Evidence & Review" action={<Badge value={continuitySignals.length ? `${continuitySignals.length} OPEN` : "READY"} />}>
        <p className="card-note">Deterministic completeness prompts from durable research state. They do not fetch market facts, change conviction, or confirm a candidate.</p>
        {continuitySignals.length === 0 ? <div className="attention-clear"><span aria-hidden="true">✓</span><div><strong>Core judgment controls are present</strong><small>Continue checking current facts and Catalyst outcomes separately.</small></div></div> : <div className="continuity-checklist">{continuitySignals.map((signal) => <article key={signal.key}><Badge value={signal.severity} /><div><strong>{signal.title}</strong><span>{signal.detail}</span></div></article>)}</div>}
      </Card>
      <Card className="research-selection-card" kicker="INSTRUMENT SELECTION" title="Candidate Instruments & Final Selection" action={<Badge value={`${instrumentCandidates.length} CANDIDATES`} />}>
        <p className="card-note">A theme Research Subject may omit a primary Instrument. Candidate-pool changes require proposal and confirmation; only `SELECTED` Instruments should seed a later Trade Plan, and selection does not rewrite Research Subject identity.</p>
        <div className="research-selection-create"><Field label="Instrument ID"><input value={candidateInstrumentId} onChange={(event) => setCandidateInstrumentId(event.target.value)} placeholder="etf:A_SHARE:159992" /></Field><Field label="Display name"><input value={candidateDisplayName} onChange={(event) => setCandidateDisplayName(event.target.value)} placeholder="Innovative drug ETF" /></Field><Field label="Reason for adding to candidate pool"><input value={candidateThesisHint} onChange={(event) => setCandidateThesisHint(event.target.value)} placeholder="Compare index coverage, liquidity, or fees" /></Field><ActionButton onClick={() => { void proposeInstrumentCandidate(); }} busy={busy}>Propose candidate</ActionButton></div>
        {instrumentCandidates.length === 0 ? <Empty>No candidate Instruments yet. For theme research, add 2–5 comparable ETFs.</Empty> : <div className="research-selection-list">{instrumentCandidates.map((candidate) => <article className={`research-selection-item status-${text(candidate.status)}`} key={text(candidate.item_id)}><div><strong>{text(candidate.display_name)}</strong><small>{text(candidate.instrument_id, `${text(candidate.market)}:${text(candidate.symbol)}`)}</small><p>{text(candidate.thesis_hint)}</p>{text(candidate.selection_reason) ? <p className="research-selection-reason">Decision reason: {text(candidate.selection_reason)}</p> : null}</div><div className="research-selection-actions"><Badge value={text(candidate.status).toUpperCase()} />{text(candidate.status) === "watching" && <button className="close-button" type="button" disabled={busy} onClick={() => { void proposeCandidateStatus(candidate, "shortlisted"); }}>Shortlist</button>}{text(candidate.status) !== "selected" && <button className="close-button restore-text" type="button" disabled={busy} onClick={() => { void proposeCandidateStatus(candidate, "selected"); }}>Select</button>}{text(candidate.status) !== "rejected" && <button className="close-button warning-text" type="button" disabled={busy} onClick={() => { void proposeCandidateStatus(candidate, "rejected"); }}>Reject</button>}</div></article>)}</div>}
      </Card>
      <Card className="research-theses-card" kicker="CURRENT JUDGMENT" title="Thesis" action={!thesisEditor ? <ActionButton onClick={() => startThesisEditor(undefined, true)}>Create Thesis</ActionButton> : <Badge value="EDITING" />}>
        {thesisEditor ? <ThesisEditor draft={thesisDraft} thesisId={thesisId} availableTheses={theses} statusExplicit={thesisStatusExplicit} subjectStatus={String(researchSubject.status).toLowerCase()} busy={busy} onChange={setThesisDraft} onStatusExplicitChange={setThesisStatusExplicit} onCancel={() => setThesisEditor(false)} onSave={() => { void saveThesis(); }} /> : theses.length === 0 ? <div className="research-no-thesis"><p>This Research Subject has no Thesis yet. Create a new Thesis candidate; Draft Research Subjects default to DRAFT Thesis.</p><ActionButton onClick={() => startThesisEditor(undefined, true)}>Create Thesis</ActionButton></div> : <ThesisRelationshipList theses={theses} revisions={revisions} assumptions={assumptions} invalidations={invalidations} onEdit={startThesisEditor} />}
      </Card>
      {pendingCandidates.length > 0 && <Card className="research-candidates-card" kicker="REVIEW QUEUE" title="Pending candidates" action={<Badge value={`${pendingCandidates.length} PROPOSED`} />}>{pendingCandidates.map((candidate) => <PendingCandidate key={text(candidate.candidate_id)} candidate={candidate} subjectStatus={String(researchSubject.status).toLowerCase()} busy={busy} onConfirm={(item, action) => { void decideCandidate(item, action); }} onReject={(item) => { void decideCandidate(item, "reject", text(item.rejectionReason)); }} onWithdraw={(item) => { void decideCandidate(item, "withdraw"); }} />)}</Card>}
      <ResearchContinuity subject={researchSubject} state={state} onWrite={onWrite} onRefresh={onRefresh} busy={busy} />
      <Card className="research-context-card" kicker="RESEARCH CONTEXT" title="Judgment context"><div className="research-context-grid research-context-single"><div><span>Open questions · {openQuestions.length}</span>{openQuestions.length === 0 ? <p className="muted">No open questions.</p> : <ul>{openQuestions.map((question) => <li key={text(question.question_id)}>{text(question.text)}</li>)}</ul>}</div></div></Card>
      <MonitorLinks monitorData={monitorData} loading={monitorLoading} subjectId={text(researchSubject.subject_id)} />
      <details className="research-raw"><summary>View this Research Subject's durable state</summary><pre>{displayJson(state)}</pre></details>
      <div id={`research-detail-${text(researchSubject.subject_id)}`} className="sr-only">{text(researchSubject.subject_id)}</div>
    </div>
  );
}

export default function ResearchPage() {
  const result = useApi<Dict>("/api/research");
  const monitorResult = useApi<Dict>("/api/monitors?run_limit=1&event_limit=1");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("ACTIVE");
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
      setWriteSuccess("Write succeeded; refreshing Research durable state.");
      return envelope;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Local write failed";
      setWriteError(message);
      throw cause;
    } finally {
      setWriting(false);
    }
  }

  async function createSubject() {
    const title = subjectDraft.title.trim();
    const summary = subjectDraft.summary.trim();
    if (!title || !summary) { window.alert("Title and summary are required."); return; }
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
    <ConsoleShell active="research" eyebrow="Durable judgment memory" title="Research workspace">
      <DataBoundary loading={result.loading} error={result.error}>
        <div className="research-page">
          <div className="toolbar research-toolbar"><p>Research Subject metadata can be edited here; Thesis changes are proposed as append-only candidates and require your explicit confirmation or rejection. This workspace does not refresh Providers, confirm judgments, or change positions.</p><div className="toolbar-actions"><ActionButton onClick={() => { setSubjectDraft(EMPTY_SUBJECT_DRAFT); setSubjectEditor((value) => !value); }}>{subjectEditor ? "Close create" : "Create Research Subject"}</ActionButton><RefreshButton onClick={result.refresh} loading={result.loading} /></div></div>
          {writeError && <div className="inline-error" role="alert">{writeError}</div>}
          {writeSuccess && <div className="inline-success" role="status">{writeSuccess}</div>}
          {subjectEditor && <SubjectEditor draft={subjectDraft} editing={false} busy={writing} onChange={setSubjectDraft} onCancel={() => setSubjectEditor(false)} onSave={() => { void createSubject(); }} />}
          <div className="research-master-detail">
            <aside className="research-index"><Card className="research-index-card" kicker="RESEARCH SUBJECTS" title="All Research Subjects" action={<span className="muted">{filtered.length} / {items.length}</span>}><div className="research-filters"><Field label="Text filter"><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Title, instrument, tags, Thesis" aria-label="Filter Research Subjects" /></Field><Field label="Research Subject status"><select value={status} onChange={(event) => setStatus(event.target.value)} aria-label="Filter by Research Subject status"><option value="ALL">All (including archived)</option>{SUBJECT_STATUSES.map((value) => <option key={value} value={value.toUpperCase()}>{value}</option>)}</select></Field></div>{items.length === 0 ? <Empty>No durable Research Subjects.</Empty> : filtered.length === 0 ? <Empty>No Research Subjects match the current filters.</Empty> : <div className="research-subject-index-list">{filtered.map((item) => { const researchSubject = item.subject ?? {}; const subjectId = String(researchSubject.subject_id ?? ""); const state = stateData(item) ?? {}; const thesisCount = listOf<Dict>(state, "theses").length; return <button type="button" id={`research-subject-${subjectId}`} className={`research-index-item ${selectedSubjectId === subjectId ? "selected" : ""}`} onClick={() => selectSubject(subjectId)} key={subjectId}><span className="research-index-status"><Badge value={text(researchSubject.status, "UNKNOWN").toUpperCase()} /></span><strong>{text(researchSubject.title, "Unnamed Research Subject")}</strong><small>{shortId(researchSubject.primary_instrument_id)} · {thesisCount} Thesis</small><time>{formatDate(researchSubject.updated_at)}</time></button>; })}</div>}</Card></aside>
            <main className="research-detail" aria-live="polite">{selected ? <ResearchSubjectDetail item={selected} monitorData={monitorResult.data} monitorLoading={monitorResult.loading} onSelectSubject={selectSubject} onRefresh={result.refresh} onWrite={write} busy={writing} /> : <Empty>Select a Research Subject from the left.</Empty>}</main>
          </div>
          {monitorResult.error && <div className="inline-error">Failed to read linked Monitors: {monitorResult.error}. The Research Subject remains available for viewing and editing.</div>}
        </div>
      </DataBoundary>
    </ConsoleShell>
  );
}
